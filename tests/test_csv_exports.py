"""
Tests for CSV export endpoints + persistence methods (iter #17)

Covers:
- Persistence.export_supplies_csv() / export_matches_csv() / export_routes_csv()
- API endpoints /api/persistence/export/{supplies,matches,routes}.csv
- CSV format validation (header + rows)
- Limit parameter handling
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

import pytest

from agents.persistence import Persistence


@pytest.fixture
def persistence(tmp_path) -> Persistence:
    """Fresh DB per test."""
    db_path = tmp_path / "test_csv.db"
    return Persistence(str(db_path))


def _record_basic_cycle(p: Persistence, cycle_id: str, day: int = 1,
                        supply_tons: dict = None, matches: list = None,
                        routes: list = None) -> None:
    """Helper: record a minimal cycle with supplies + matches + routes.

    Args:
        supply_tons: {supply_id: (material_type, available_tons, quality_score)}
        matches: [(supply_id, demand_id, material_type, tons, distance_km)]
        routes: [(vehicle_id, distance_km, duration_hours, cost_sek, co2_kg, stops_json)]
    """
    supply_tons = supply_tons or {}
    matches = matches or []
    routes = routes or []
    p.begin_cycle(
        cycle_id, sim_day=day, sim_hour=10, activity_factor=1.0,
        n_supply_offers=len(supply_tons), n_demand_requests=2,
    )
    for sid, (mat, tons, qual) in supply_tons.items():
        p.record_supply(cycle_id, {
            "supply_id": sid,
            "location": {"lat": 57.7, "lon": 14.1},
            "material_type": mat,
            "available_tons": tons,
            "moisture_percent": 20.0,
            "quality_score": qual,
        })
    for supply_id, demand_id, mat, tons, dist in matches:
        p.record_match(cycle_id, {
            "supply_id": supply_id,
            "demand_id": demand_id,
            "material_type": mat,
            "tons": tons,
            "distance_km": dist,
            "estimated_profit_sek": 100.0,
        })
    for vehicle_id, dist, dur, cost, co2, stops in routes:
        p.record_route(cycle_id, {
            "vehicle_id": vehicle_id,
            "stops": stops,
            "distance_km": dist,
            "duration_hours": dur,
            "cost_sek": cost,
            "co2_kg": co2,
        })
    p.commit_cycle(cycle_id, kpi={
        "n_supply_offers": len(supply_tons),
        "n_demand_requests": 2,
        "n_matches": len(matches),
        "n_vehicles_used": len(routes),
        "total_tons": sum(m[3] for m in matches),
        "total_cost_sek": sum(r[3] for r in routes),
        "total_co2_kg": sum(r[4] for r in routes),
        "total_distance_km": sum(r[1] for r in routes),
        "n_vehicles_available": 10,
        "fleet_utilization_pct": 30.0,
        "solver_status": "feasible",
    }, wall_duration_ms=100)


def _parse_csv(csv_str: str):
    """Parse CSV string → (header, rows)."""
    reader = csv.reader(io.StringIO(csv_str))
    header = next(reader)
    rows = list(reader)
    return header, rows


class TestExportSuppliesCsv:
    """Tests for Persistence.export_supplies_csv()."""

    def test_empty_db_returns_header_only(self, persistence):
        """Empty DB → CSV with just header, no data rows."""
        csv_str = persistence.export_supplies_csv()
        header, rows = _parse_csv(csv_str)
        assert "cycle_id" in header
        assert "supply_id" in header
        assert "material_type" in header
        assert "available_tons" in header
        assert "location_lat" in header
        assert "location_lon" in header
        assert "moisture_percent" in header
        assert "quality_score" in header
        assert "sim_day" in header
        assert "sim_hour" in header
        assert len(rows) == 0

    def test_single_cycle_one_supply(self, persistence):
        """1 cycle, 1 supply → 1 data row."""
        _record_basic_cycle(
            persistence, "OPT0001", day=1,
            supply_tons={"SUP001": ("wood", 10.0, 80.0)},
        )
        csv_str = persistence.export_supplies_csv()
        header, rows = _parse_csv(csv_str)
        assert len(rows) == 1
        assert rows[0][header.index("cycle_id")] == "OPT0001"
        assert rows[0][header.index("supply_id")] == "SUP001"
        assert rows[0][header.index("material_type")] == "wood"
        assert float(rows[0][header.index("available_tons")]) == 10.0
        assert float(rows[0][header.index("quality_score")]) == 80.0
        assert int(rows[0][header.index("sim_day")]) == 1

    def test_multiple_cycles_sorted_by_sim_day_desc(self, persistence):
        """Multiple cycles → rows sorted by sim_day DESC (newest first)."""
        _record_basic_cycle(
            persistence, "OPT0001", day=1,
            supply_tons={"SUP001": ("wood", 5.0, 80.0)},
        )
        _record_basic_cycle(
            persistence, "OPT0002", day=2,
            supply_tons={"SUP002": ("metal", 15.0, 90.0)},
        )
        csv_str = persistence.export_supplies_csv()
        _, rows = _parse_csv(csv_str)
        assert len(rows) == 2
        # OPT0002 (day 2) should be first
        assert rows[0][0] == "OPT0002"  # cycle_id column
        assert rows[1][0] == "OPT0001"

    def test_limit_caps_rows(self, persistence):
        """limit param caps number of returned rows."""
        # 5 different supply entries
        for i in range(5):
            _record_basic_cycle(
                persistence, f"OPT{i:04d}", day=i + 1,
                supply_tons={f"SUP{i:03d}": ("wood", 10.0, 80.0)},
            )
        csv_str = persistence.export_supplies_csv(limit=2)
        _, rows = _parse_csv(csv_str)
        assert len(rows) == 2


class TestExportMatchesCsv:
    """Tests for Persistence.export_matches_csv()."""

    def test_empty_db_returns_header_only(self, persistence):
        """Empty DB → CSV with header only."""
        csv_str = persistence.export_matches_csv()
        header, rows = _parse_csv(csv_str)
        assert "cycle_id" in header
        assert "supply_id" in header
        assert "demand_id" in header
        assert "material_type" in header
        assert "tons" in header
        assert "distance_km" in header
        assert "estimated_profit_sek" in header
        assert "sim_day" in header
        assert len(rows) == 0

    def test_single_match_row(self, persistence):
        """1 cycle, 1 match → 1 data row."""
        _record_basic_cycle(
            persistence, "OPT0001", day=1,
            matches=[("SUP001", "DEM001", "wood", 8.0, 25.0)],
        )
        csv_str = persistence.export_matches_csv()
        header, rows = _parse_csv(csv_str)
        assert len(rows) == 1
        assert rows[0][header.index("supply_id")] == "SUP001"
        assert rows[0][header.index("demand_id")] == "DEM001"
        assert rows[0][header.index("material_type")] == "wood"
        assert float(rows[0][header.index("tons")]) == 8.0
        assert float(rows[0][header.index("distance_km")]) == 25.0

    def test_multiple_matches_per_cycle(self, persistence):
        """2 matches in 1 cycle → 2 rows."""
        _record_basic_cycle(
            persistence, "OPT0001", day=1,
            matches=[
                ("SUP001", "DEM001", "wood", 8.0, 20.0),
                ("SUP002", "DEM002", "metal", 12.0, 30.0),
            ],
        )
        csv_str = persistence.export_matches_csv()
        _, rows = _parse_csv(csv_str)
        assert len(rows) == 2


class TestExportRoutesCsv:
    """Tests for Persistence.export_routes_csv()."""

    def test_empty_db_returns_header_only(self, persistence):
        """Empty DB → CSV with header only."""
        csv_str = persistence.export_routes_csv()
        header, rows = _parse_csv(csv_str)
        assert "cycle_id" in header
        assert "vehicle_id" in header
        assert "distance_km" in header
        assert "duration_hours" in header
        assert "cost_sek" in header
        assert "co2_kg" in header
        assert "stops_count" in header
        assert len(rows) == 0

    def test_single_route_row(self, persistence):
        """1 route → 1 row."""
        _record_basic_cycle(
            persistence, "OPT0001", day=1,
            routes=[("VEH001", 25.0, 1.0, 100.0, 5.0, ["DEPOT", "SUP001", "DEM001"])],
        )
        csv_str = persistence.export_routes_csv()
        header, rows = _parse_csv(csv_str)
        assert len(rows) == 1
        assert rows[0][header.index("vehicle_id")] == "VEH001"
        assert float(rows[0][header.index("distance_km")]) == 25.0
        assert int(rows[0][header.index("stops_count")]) == 3


class TestAPIExportEndpoints:
    """API endpoint tests via TestClient."""

    @pytest.fixture(autouse=True)
    def setup_fake_coordinator(self, tmp_path):
        from unittest.mock import MagicMock
        from web.backend import main as backend_main
        from fastapi.testclient import TestClient

        db_path = tmp_path / "api_csv_test.db"
        persistence = Persistence(str(db_path))
        _record_basic_cycle(
            persistence, "API-OPT0001", day=1,
            supply_tons={"SUP001": ("wood", 10.0, 80.0)},
            matches=[("SUP001", "DEM001", "wood", 8.0, 20.0)],
            routes=[("VEH001", 25.0, 1.0, 100.0, 5.0, ["DEPOT", "SUP001", "DEM001"])],
        )
        fake_coord = MagicMock()
        fake_coord.persistence = persistence
        backend_main.coordinator = fake_coord
        self.client = TestClient(backend_main.app)

    def test_export_supplies_endpoint(self):
        """GET /api/persistence/export/supplies.csv returns CSV."""
        response = self.client.get("/api/persistence/export/supplies.csv")
        assert response.status_code == 200
        assert "text/csv" in response.headers["content-type"]
        assert "attachment" in response.headers["content-disposition"]
        # Parse CSV body
        body = response.text
        assert "cycle_id" in body
        assert "SUP001" in body

    def test_export_matches_endpoint(self):
        """GET /api/persistence/export/matches.csv returns CSV."""
        response = self.client.get("/api/persistence/export/matches.csv")
        assert response.status_code == 200
        assert "text/csv" in response.headers["content-type"]
        body = response.text
        assert "supply_id" in body
        assert "demand_id" in body

    def test_export_routes_endpoint(self):
        """GET /api/persistence/export/routes.csv returns CSV."""
        response = self.client.get("/api/persistence/export/routes.csv")
        assert response.status_code == 200
        assert "text/csv" in response.headers["content-type"]
        body = response.text
        assert "vehicle_id" in body
        assert "stops_count" in body

    def test_export_supplies_with_limit(self):
        """GET with limit query param respects the limit."""
        response = self.client.get("/api/persistence/export/supplies.csv?limit=5")
        assert response.status_code == 200
        # Filename should reflect limit
        assert "5.csv" in response.headers["content-disposition"]

    def test_export_limit_clamped_to_max(self):
        """GET with limit > 50000 → clamped (not 400, but cap)."""
        # limit param > 50000 should be clamped to 50000 (not error)
        response = self.client.get("/api/persistence/export/supplies.csv?limit=99999")
        assert response.status_code == 200
        # Filename should show 50000
        assert "50000.csv" in response.headers["content-disposition"]

    def test_export_limit_at_least_1(self):
        """GET with limit=0 → clamped to 1 (no error)."""
        response = self.client.get("/api/persistence/export/supplies.csv?limit=0")
        assert response.status_code == 200
        # Filename should show 1
        assert "1.csv" in response.headers["content-disposition"]

    def test_export_returns_503_if_no_persistence(self):
        """All 3 export endpoints return 503 without coordinator."""
        from web.backend import main as backend_main
        old_coord = backend_main.coordinator
        backend_main.coordinator = None
        try:
            for path in [
                "/api/persistence/export/supplies.csv",
                "/api/persistence/export/matches.csv",
                "/api/persistence/export/routes.csv",
            ]:
                response = self.client.get(path)
                assert response.status_code == 503, f"{path} should return 503"
        finally:
            backend_main.coordinator = old_coord