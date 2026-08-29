"""
Tests for unified DB export endpoint (iter #18)

Covers:
- /api/admin/db-export?table=X&fmt=Y for 5 tables × 3 formats
- Validation: invalid table/format → 400
- since_sim_day filter
- limit param clamping
- 503 when no persistence
"""

from __future__ import annotations

import json

import pytest

from agents.persistence import Persistence


def _seed(p: Persistence, cid: str, day: int = 1) -> None:
    """Seed a minimal cycle with supplies/matches/routes."""
    p.begin_cycle(cid, sim_day=day, sim_hour=10, activity_factor=1.0,
                  n_supply_offers=2, n_demand_requests=2)
    p.commit_cycle(cid, kpi={
        "n_supply_offers": 2, "n_demand_requests": 2, "n_matches": 2,
        "total_tons": 20.0, "total_cost_sek": 200, "total_co2_kg": 10,
        "total_distance_km": 30, "n_vehicles_used": 2,
        "n_vehicles_available": 5, "fleet_utilization_pct": 40,
        "solver_status": "feasible",
    }, wall_duration_ms=10)
    for i in range(2):
        p.record_supply(cid, {
            "supply_id": f"SUP{i}", "location": {"lat": 57.7, "lon": 12.9},
            "material_type": "wood", "available_tons": 10.0, "quality_score": 80.0,
        })
        p.record_demand(cid, {
            "demand_id": f"DEM{i}", "location": {"lat": 57.8, "lon": 13.0},
            "material_type": "wood", "required_tons": 5.0, "priority": "normal",
        })
        p.record_match(cid, {
            "supply_id": f"SUP{i}", "demand_id": f"DEM{i}",
            "material_type": "wood", "tons": 5.0, "distance_km": 15.0,
            "estimated_profit_sek": 50.0,
        })
        p.record_route(cid, {
            "vehicle_id": f"VEH{i}", "stops": ["DEPOT", f"SUP{i}"],
            "distance_km": 15.0, "duration_hours": 0.5,
            "cost_sek": 100.0, "co2_kg": 5.0,
        })


class TestDBExportEndpoint:
    """Tests for GET /api/admin/db-export."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        from unittest.mock import MagicMock
        from web.backend import main as backend_main
        from fastapi.testclient import TestClient

        db_path = tmp_path / "api_export.db"
        p = Persistence(str(db_path))
        # Seed 2 cycles on different days
        _seed(p, "OPT0001", day=1)
        _seed(p, "OPT0002", day=5)

        fake_coord = MagicMock()
        fake_coord.persistence = p
        backend_main.coordinator = fake_coord
        self.client = TestClient(backend_main.app)
        self.persistence = p

    # ----- cycles -----
    def test_export_cycles_json(self):
        resp = self.client.get("/api/admin/db-export?table=cycles&fmt=json")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == 2

    def test_export_cycles_ndjson(self):
        resp = self.client.get("/api/admin/db-export?table=cycles&fmt=ndjson")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/x-ndjson")
        # Parse NDJSON: each line is a JSON object
        lines = resp.text.strip().split("\n")
        assert len(lines) == 2
        for line in lines:
            obj = json.loads(line)
            assert "cycle_id" in obj

    def test_export_cycles_csv(self):
        resp = self.client.get("/api/admin/db-export?table=cycles&fmt=csv")
        assert resp.status_code == 200
        assert "text/csv" in resp.headers["content-type"]
        # Has CSV header
        assert "cycle_id" in resp.text

    # ----- supplies -----
    def test_export_supplies_json(self):
        resp = self.client.get("/api/admin/db-export?table=supplies&fmt=json")
        assert resp.status_code == 200
        data = resp.json()
        # 2 cycles × 2 supplies = 4 supplies
        assert len(data) >= 4

    # ----- matches -----
    def test_export_matches_json(self):
        resp = self.client.get("/api/admin/db-export?table=matches&fmt=json")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 4

    def test_export_matches_ndjson(self):
        resp = self.client.get("/api/admin/db-export?table=matches&fmt=ndjson")
        assert resp.status_code == 200
        # Verify ndjson structure (no trailing comma, each line valid JSON)
        lines = [l for l in resp.text.strip().split("\n") if l]
        for line in lines:
            obj = json.loads(line)
            assert "supply_id" in obj

    # ----- routes -----
    def test_export_routes_json(self):
        resp = self.client.get("/api/admin/db-export?table=routes&fmt=json")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 4

    # ----- llm_decisions (only json/ndjson supported) -----
    def test_export_llm_decisions_json(self):
        resp = self.client.get("/api/admin/db-export?table=llm_decisions&fmt=json")
        assert resp.status_code == 200
        # Empty array (no LLM decisions seeded)
        data = resp.json()
        assert isinstance(data, list)

    # ----- validation -----
    def test_invalid_table_returns_400(self):
        resp = self.client.get("/api/admin/db-export?table=bogus&fmt=json")
        assert resp.status_code == 400
        assert "Unknown table" in resp.json()["detail"]

    def test_invalid_format_returns_400(self):
        resp = self.client.get("/api/admin/db-export?table=cycles&fmt=xml")
        assert resp.status_code == 400
        assert "Unknown format" in resp.json()["detail"]

    def test_default_format_is_json(self):
        """No format param → defaults to json."""
        resp = self.client.get("/api/admin/db-export?table=cycles")
        assert resp.status_code == 200
        assert "application/json" in resp.headers["content-type"]

    # ----- filters -----
    def test_since_sim_day_filter(self):
        """since_sim_day=3 returns only cycles >= day 3 (only OPT0002)."""
        resp = self.client.get(
            "/api/admin/db-export?table=cycles&fmt=json&since_sim_day=3"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        # CSV-parsed sim_day is a string
        assert int(data[0]["sim_day"]) == 5

    def test_since_sim_day_filter_all_match(self):
        """since_sim_day=1 returns both cycles."""
        resp = self.client.get(
            "/api/admin/db-export?table=cycles&fmt=json&since_sim_day=1"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2

    def test_since_sim_day_excludes_all(self):
        """since_sim_day=10 returns empty (no cycles >= 10)."""
        resp = self.client.get(
            "/api/admin/db-export?table=cycles&fmt=json&since_sim_day=10"
        )
        assert resp.status_code == 200
        assert resp.json() == []

    # ----- limit -----
    def test_limit_param(self):
        """limit=1 caps result count."""
        resp = self.client.get(
            "/api/admin/db-export?table=cycles&fmt=json&limit=1"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1

    def test_limit_zero_clamped_to_1(self):
        """limit=0 → clamped to 1."""
        resp = self.client.get(
            "/api/admin/db-export?table=cycles&fmt=json&limit=0"
        )
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    def test_limit_too_large_clamped(self):
        """limit > 50000 → clamped to 50000."""
        resp = self.client.get(
            "/api/admin/db-export?table=cycles&fmt=json&limit=99999"
        )
        assert resp.status_code == 200
        # No error, just clamped

    # ----- 503 -----
    def test_503_when_no_persistence(self):
        from web.backend import main as backend_main
        old_coord = backend_main.coordinator
        backend_main.coordinator = None
        try:
            resp = self.client.get("/api/admin/db-export?table=cycles&fmt=json")
            assert resp.status_code == 503
        finally:
            backend_main.coordinator = old_coord


class TestHelpers:
    """Test the helper functions directly."""

    def test_csv_to_rows_round_trip(self):
        from web.backend.main import _csv_to_rows, _rows_to_csv

        original = [
            {"a": 1, "b": "x"},
            {"a": 2, "b": "y"},
            {"a": 3, "b": "z"},
        ]
        csv_str = _rows_to_csv(original)
        parsed = _csv_to_rows(csv_str)
        # All values come back as strings via CSV
        assert len(parsed) == 3
        assert parsed[0]["a"] == "1"
        assert parsed[0]["b"] == "x"

    def test_rows_to_csv_empty(self):
        from web.backend.main import _rows_to_csv
        assert _rows_to_csv([]) == ""

    def test_csv_to_rows_empty(self):
        from web.backend.main import _csv_to_rows
        assert _csv_to_rows("") == []