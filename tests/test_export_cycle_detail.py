"""
iter #48: tests for /api/persistence/export/cycle-detail/{cycle_id}.csv endpoint.

Covers:
1. Persistence.export_cycle_detail_csv() — combined CSV with all sections
2. Returns None for non-existent cycle
3. Includes 5 sections: cycle_metadata / supply_offers / demand_requests / matches / routes
4. stops_count is derived from stops_json
5. Endpoint 200 with valid cycle
6. Endpoint 404 for missing cycle
7. Endpoint 503 when persistence not initialized
"""
import csv
import io
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def persistence():
    from agents.persistence import Persistence
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_cycle_detail.db")
        p = Persistence(db_path)
        yield p


def _insert_full_cycle(p, cycle_id="c1", sim_day=10):
    """Insert a cycle with all related tables populated."""
    with p._conn() as conn:
        conn.execute(
            """INSERT INTO optimization_cycles
               (cycle_id, sim_day, sim_hour, wall_timestamp,
                activity_factor, n_supply_offers, n_demand_requests, n_matches,
                total_tons, total_cost_sek, total_co2_kg, total_distance_km,
                n_vehicles_used, n_vehicles_available, fleet_utilization_pct,
                solver_status, wall_duration_ms, seasonal_factor_avg, seasonal_month)
               VALUES (?, ?, 0, '2026-09-04T00:00:00',
                       1.0, 2, 1, 1, 25.0, 200.0, 100.0, 40.0,
                       1, 2, 75.0, 'OPTIMAL', 100, 1.1, 1)""",
            (cycle_id, sim_day),
        )
        # supply_offers
        for sid, mat, tons in [("s1", "concrete", 15.0), ("s2", "metal_scrap", 10.0)]:
            conn.execute(
                """INSERT INTO supply_offers
                   (cycle_id, supply_id, material_type, available_tons,
                    moisture_percent, quality_score, location_lat, location_lon,
                    base_seasonal_multiplier, seasonal_multiplier, perturbation_applied)
                   VALUES (?, ?, ?, ?, 5.0, 0.8, 57.7, 14.2, 1.0, 1.1, 0)""",
                (cycle_id, sid, mat, tons),
            )
        # demand_requests
        conn.execute(
            """INSERT INTO demand_requests
               (cycle_id, demand_id, name, location_lat, location_lon,
                material_type, required_tons, priority, deadline)
               VALUES (?, 'd1', 'Borås Recycling', 57.7, 14.2, 'concrete', 25.0, 1, 5)""",
            (cycle_id,),
        )
        # matches
        conn.execute(
            """INSERT INTO matches
               (cycle_id, supply_id, demand_id, material_type, tons,
                distance_km, estimated_profit_sek)
               VALUES (?, 's1', 'd1', 'concrete', 15.0, 5.5, 250.0)""",
            (cycle_id,),
        )
        # routes (with stops_json)
        stops = [
            {"type": "depot", "lat": 57.7, "lon": 14.2},
            {"type": "pickup", "supply_id": "s1", "lat": 57.71, "lon": 14.21},
            {"type": "delivery", "demand_id": "d1", "lat": 57.72, "lon": 14.22},
        ]
        conn.execute(
            """INSERT INTO routes
               (cycle_id, vehicle_id, distance_km, duration_hours,
                cost_sek, co2_kg, stops_json)
               VALUES (?, 'v1', 5.5, 0.5, 200.0, 50.0, ?)""",
            (cycle_id, json.dumps(stops)),
        )


# ============================================
# Persistence method tests
# ============================================


def test_export_returns_none_for_missing_cycle(persistence):
    result = persistence.export_cycle_detail_csv("nonexistent")
    assert result is None


def test_export_includes_all_sections(persistence):
    _insert_full_cycle(persistence)
    csv_str = persistence.export_cycle_detail_csv("c1", include_metadata=False)
    # Parse sections
    sections = {}
    current = None
    for line in csv_str.split("\n"):
        if line.startswith("# section:"):
            current = line.replace("# section:", "").strip()
            sections[current] = []
        elif current and line.strip():
            sections[current].append(line)
    # 5 sections should be present
    assert set(sections.keys()) == {"cycle_metadata", "supply_offers", "demand_requests", "matches", "routes"}
    # cycle_metadata: 1 header + 1 data row
    assert len(sections["cycle_metadata"]) == 2
    # supply_offers: 1 header + 2 data rows
    assert len(sections["supply_offers"]) == 3
    # demand_requests: 1 header + 1 data row
    assert len(sections["demand_requests"]) == 2
    # matches: 1 header + 1 data row
    assert len(sections["matches"]) == 2
    # routes: 1 header + 1 data row
    assert len(sections["routes"]) == 2


def test_export_cycle_metadata_contains_kpis(persistence):
    _insert_full_cycle(persistence)
    csv_str = persistence.export_cycle_detail_csv("c1", include_metadata=False)
    lines = [l for l in csv_str.split("\n") if "cycle_metadata" not in l and l.strip()]
    # First non-comment line is the cycle_metadata header
    header = lines[0]
    assert "cycle_id" in header
    assert "sim_day" in header
    assert "total_cost_sek" in header
    assert "fleet_utilization_pct" in header
    # Data row
    data = lines[1]
    assert "c1" in data
    assert "200.0" in data  # cost


def test_export_stops_count_derived(persistence):
    _insert_full_cycle(persistence)
    csv_str = persistence.export_cycle_detail_csv("c1", include_metadata=False)
    lines = csv_str.split("\n")
    # Find routes section
    routes_idx = next(i for i, l in enumerate(lines) if l == "# section: routes")
    routes_section = lines[routes_idx+1:]
    # Header is routes_section[0], data row is routes_section[1]
    header = routes_section[0]
    data_row = routes_section[1]
    reader = csv.DictReader(io.StringIO(header + "\n" + data_row))
    rows = list(reader)
    assert len(rows) == 1
    assert int(rows[0]["stops_count"]) == 3  # 3 stops in stops_json


def test_export_with_metadata(persistence):
    _insert_full_cycle(persistence)
    csv_str = persistence.export_cycle_detail_csv("c1", include_metadata=True)
    assert csv_str.startswith("# Green Logistics AI CSV export")
    assert "# cycle_id: c1" in csv_str
    assert "# n_supplies: 2" in csv_str
    assert "# n_demands: 1" in csv_str
    assert "# n_matches: 1" in csv_str
    assert "# n_routes: 1" in csv_str


def test_export_empty_related_tables(persistence):
    """Cycle with no supplies/demands/matches/routes should still export."""
    with persistence._conn() as conn:
        conn.execute(
            """INSERT INTO optimization_cycles
               (cycle_id, sim_day, sim_hour, wall_timestamp,
                activity_factor, n_supply_offers, n_demand_requests, n_matches,
                total_tons, total_cost_sek, total_co2_kg, total_distance_km,
                n_vehicles_used, n_vehicles_available, fleet_utilization_pct,
                solver_status, wall_duration_ms)
               VALUES ('empty_c', 1, 0, '2026-09-04T00:00:00',
                       1.0, 0, 0, 0, 0.0, 0.0, 0.0, 0.0,
                       0, 0, 0.0, 'OPTIMAL', 100)""",
        )
    csv_str = persistence.export_cycle_detail_csv("empty_c", include_metadata=False)
    # All sections should have just headers (no data rows)
    lines = csv_str.split("\n")
    cycle_section = [l for l in lines if l.strip() and not l.startswith("# section:")][:1]
    # Just the header line
    assert cycle_section[0].startswith("cycle_id")


# ============================================
# API endpoint tests
# ============================================


def test_endpoint_returns_200_with_csv(monkeypatch):
    from web.backend import main as backend_main
    from fastapi.testclient import TestClient

    class _FakePersistence:
        def export_cycle_detail_csv(self, cycle_id, include_metadata=True):
            return "# Green Logistics AI CSV export\n# cycle_id: c1\n"

    class _FakeCoord:
        persistence = _FakePersistence()

    monkeypatch.setattr(backend_main, "coordinator", _FakeCoord())
    client = TestClient(backend_main.app)
    resp = client.get("/api/persistence/export/cycle-detail/c1.csv")
    assert resp.status_code == 200
    assert "text/csv" in resp.headers.get("content-type", "")
    assert "cycle_detail_c1.csv" in resp.headers.get("content-disposition", "")


def test_endpoint_returns_404_for_missing_cycle(monkeypatch):
    from web.backend import main as backend_main
    from fastapi.testclient import TestClient

    class _FakePersistence:
        def export_cycle_detail_csv(self, cycle_id, include_metadata=True):
            return None

    class _FakeCoord:
        persistence = _FakePersistence()

    monkeypatch.setattr(backend_main, "coordinator", _FakeCoord())
    client = TestClient(backend_main.app)
    resp = client.get("/api/persistence/export/cycle-detail/nonexistent.csv")
    assert resp.status_code == 404


def test_endpoint_returns_503_when_no_coordinator(monkeypatch):
    from web.backend import main as backend_main
    from fastapi.testclient import TestClient

    monkeypatch.setattr(backend_main, "coordinator", None)
    client = TestClient(backend_main.app)
    resp = client.get("/api/persistence/export/cycle-detail/c1.csv")
    assert resp.status_code == 503
