"""
iter #49: tests for /api/persistence/material-supply-demand-balance endpoint.

Covers:
1. Persistence.get_material_supply_demand_balance() — supply vs demand breakdown
2. supply_demand_ratio + demand_fulfillment_pct correctly computed
3. excess_supply_tons + unmet_demand_tons
4. Window filter
5. Empty state
6. Sorted by unmet_demand_tons DESC
7. Endpoint exposes the new method
8. Endpoint validates input range
"""
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
        db_path = os.path.join(tmpdir, "test_balance.db")
        p = Persistence(db_path)
        yield p


def _insert_cycle(p, cycle_id, sim_day):
    with p._conn() as conn:
        conn.execute(
            """INSERT INTO optimization_cycles
               (cycle_id, sim_day, sim_hour, wall_timestamp,
                activity_factor, n_supply_offers, n_demand_requests, n_matches,
                total_tons, total_cost_sek, total_co2_kg, total_distance_km,
                n_vehicles_used, n_vehicles_available, fleet_utilization_pct,
                solver_status, wall_duration_ms)
               VALUES (?, ?, 0, '2026-09-04T00:00:00',
                       1.0, 1, 1, 1, 5.0, 100.0, 50.0, 20.0,
                       1, 1, 50.0, 'OPTIMAL', 100)""",
            (cycle_id, sim_day),
        )


def _insert_supply(p, cycle_id, supply_id, material_type, tons):
    with p._conn() as conn:
        conn.execute(
            """INSERT INTO supply_offers
               (cycle_id, supply_id, material_type, available_tons,
                moisture_percent, quality_score, location_lat, location_lon)
               VALUES (?, ?, ?, ?, 5.0, 0.8, 57.7, 14.2)""",
            (cycle_id, supply_id, material_type, tons),
        )


def _insert_demand(p, cycle_id, demand_id, material_type, required_tons):
    with p._conn() as conn:
        conn.execute(
            """INSERT INTO demand_requests
               (cycle_id, demand_id, location_lat, location_lon,
                material_type, required_tons, priority, deadline)
               VALUES (?, ?, 57.7, 14.2, ?, ?, 1, 5)""",
            (cycle_id, demand_id, material_type, required_tons),
        )


def _insert_match(p, cycle_id, supply_id, demand_id, material_type, tons):
    with p._conn() as conn:
        conn.execute(
            """INSERT INTO matches
               (cycle_id, supply_id, demand_id, material_type, tons,
                distance_km, estimated_profit_sek)
               VALUES (?, ?, ?, ?, ?, 5.0, 100.0)""",
            (cycle_id, supply_id, demand_id, material_type, tons),
        )


# ============================================
# Persistence method tests
# ============================================


def test_empty_returns_empty(persistence):
    result = persistence.get_material_supply_demand_balance()
    assert result == []


def test_single_material_basic(persistence):
    _insert_cycle(persistence, "c1", sim_day=10)
    _insert_supply(persistence, "c1", "s1", "concrete", 10.0)
    _insert_demand(persistence, "c1", "d1", "concrete", 8.0)
    _insert_match(persistence, "c1", "s1", "d1", "concrete", 6.0)
    result = persistence.get_material_supply_demand_balance()
    assert len(result) == 1
    row = result[0]
    assert row["material_type"] == "concrete"
    assert row["total_supply_tons"] == 10.0
    assert row["total_demand_tons"] == 8.0
    assert row["total_matched_tons"] == 6.0
    # supply_demand_ratio = 6/10 = 0.6
    assert row["supply_demand_ratio"] == 0.6
    # demand_fulfillment_pct = 6/8 * 100 = 75.0
    assert row["demand_fulfillment_pct"] == 75.0
    # excess_supply = 10 - 6 = 4
    assert row["excess_supply_tons"] == 4.0
    # unmet_demand = 8 - 6 = 2
    assert row["unmet_demand_tons"] == 2.0


def test_multiple_materials_sorted_by_unmet_demand(persistence):
    _insert_cycle(persistence, "c1", sim_day=10)
    # concrete: 100 supply, 50 demand, 30 matched → unmet 20
    _insert_supply(persistence, "c1", "s1", "concrete", 100.0)
    _insert_demand(persistence, "c1", "d1", "concrete", 50.0)
    _insert_match(persistence, "c1", "s1", "d1", "concrete", 30.0)
    # metal: 5 supply, 10 demand, 5 matched → unmet 5
    _insert_supply(persistence, "c1", "s2", "metal_scrap", 5.0)
    _insert_demand(persistence, "c1", "d2", "metal_scrap", 10.0)
    _insert_match(persistence, "c1", "s2", "d2", "metal_scrap", 5.0)
    # wood: 50 supply, 5 demand, 5 matched → unmet 0
    _insert_supply(persistence, "c1", "s3", "wood_waste", 50.0)
    _insert_demand(persistence, "c1", "d3", "wood_waste", 5.0)
    _insert_match(persistence, "c1", "s3", "d3", "wood_waste", 5.0)
    result = persistence.get_material_supply_demand_balance()
    assert len(result) == 3
    # Sorted by unmet_demand_tons DESC
    assert [r["material_type"] for r in result] == ["concrete", "metal_scrap", "wood_waste"]
    assert [r["unmet_demand_tons"] for r in result] == [20.0, 5.0, 0.0]


def test_material_with_supply_only(persistence):
    """Material that has supply but no demand or matches."""
    _insert_cycle(persistence, "c1", sim_day=10)
    _insert_supply(persistence, "c1", "s1", "concrete", 50.0)
    result = persistence.get_material_supply_demand_balance()
    assert len(result) == 1
    row = result[0]
    assert row["total_supply_tons"] == 50.0
    assert row["total_demand_tons"] == 0.0
    assert row["total_matched_tons"] == 0.0
    assert row["demand_fulfillment_pct"] is None  # no demand
    assert row["excess_supply_tons"] == 50.0
    assert row["unmet_demand_tons"] == 0.0  # no demand = no unmet


def test_material_with_demand_only(persistence):
    """Material that has demand but no supply or matches."""
    _insert_cycle(persistence, "c1", sim_day=10)
    _insert_demand(persistence, "c1", "d1", "concrete", 100.0)
    result = persistence.get_material_supply_demand_balance()
    assert len(result) == 1
    row = result[0]
    assert row["total_supply_tons"] == 0.0
    assert row["total_demand_tons"] == 100.0
    assert row["total_matched_tons"] == 0.0
    assert row["supply_demand_ratio"] is None
    assert row["demand_fulfillment_pct"] == 0.0
    assert row["unmet_demand_tons"] == 100.0


def test_window_filter(persistence):
    _insert_cycle(persistence, "c1", sim_day=5)
    _insert_supply(persistence, "c1", "s1", "concrete", 10.0)
    _insert_demand(persistence, "c1", "d1", "concrete", 5.0)
    _insert_cycle(persistence, "c2", sim_day=20)
    _insert_supply(persistence, "c2", "s2", "concrete", 30.0)
    _insert_demand(persistence, "c2", "d2", "concrete", 15.0)
    # Window 10-30: only c2 included
    result = persistence.get_material_supply_demand_balance(
        since_sim_day=10, until_sim_day=30,
    )
    assert len(result) == 1
    assert result[0]["total_supply_tons"] == 30.0
    assert result[0]["total_demand_tons"] == 15.0


def test_aggregates_across_cycles(persistence):
    """Multiple cycles with same material should sum."""
    for d in [1, 2, 3]:
        _insert_cycle(persistence, f"c{d}", sim_day=d)
        _insert_supply(persistence, f"c{d}", f"s{d}", "concrete", 10.0)
        _insert_demand(persistence, f"c{d}", f"d{d}", "concrete", 5.0)
    result = persistence.get_material_supply_demand_balance()
    assert result[0]["total_supply_tons"] == 30.0
    assert result[0]["total_demand_tons"] == 15.0
    assert result[0]["n_supply_offers"] == 3
    assert result[0]["n_demand_requests"] == 3


# ============================================
# API endpoint tests
# ============================================


def test_endpoint_returns_200(monkeypatch):
    from web.backend import main as backend_main
    from fastapi.testclient import TestClient

    class _FakePersistence:
        def get_material_supply_demand_balance(self, since_sim_day=None, until_sim_day=None):
            return [
                {"material_type": "concrete", "total_supply_tons": 100.0,
                 "total_demand_tons": 50.0, "total_matched_tons": 30.0,
                 "supply_demand_ratio": 0.3, "demand_fulfillment_pct": 60.0,
                 "excess_supply_tons": 70.0, "unmet_demand_tons": 20.0,
                 "n_supply_offers": 5, "n_demand_requests": 3, "n_matches": 2},
            ]

    class _FakeCoord:
        persistence = _FakePersistence()

    monkeypatch.setattr(backend_main, "coordinator", _FakeCoord())
    client = TestClient(backend_main.app)
    resp = client.get("/api/persistence/material-supply-demand-balance")
    assert resp.status_code == 200
    data = resp.json()
    assert data["by_material"][0]["material_type"] == "concrete"
    assert data["by_material"][0]["unmet_demand_tons"] == 20.0


def test_endpoint_window_params(monkeypatch):
    from web.backend import main as backend_main
    from fastapi.testclient import TestClient

    class _FakePersistence:
        def get_material_supply_demand_balance(self, since_sim_day=None, until_sim_day=None):
            self.kwargs = (since_sim_day, until_sim_day)
            return []

    class _FakeCoord:
        persistence = _FakePersistence()

    monkeypatch.setattr(backend_main, "coordinator", _FakeCoord())
    client = TestClient(backend_main.app)
    resp = client.get("/api/persistence/material-supply-demand-balance?since_sim_day=10&until_sim_day=20")
    assert resp.status_code == 200
    assert backend_main.coordinator.persistence.kwargs == (10, 20)


def test_endpoint_rejects_inverted_window(monkeypatch):
    from web.backend import main as backend_main
    from fastapi.testclient import TestClient

    class _FakeCoord:
        persistence = type("P", (), {"get_material_supply_demand_balance": lambda self, **kw: []})()

    monkeypatch.setattr(backend_main, "coordinator", _FakeCoord())
    client = TestClient(backend_main.app)
    resp = client.get("/api/persistence/material-supply-demand-balance?since_sim_day=20&until_sim_day=10")
    assert resp.status_code == 400


def test_endpoint_returns_503_when_no_coordinator(monkeypatch):
    from web.backend import main as backend_main
    from fastapi.testclient import TestClient

    monkeypatch.setattr(backend_main, "coordinator", None)
    client = TestClient(backend_main.app)
    resp = client.get("/api/persistence/material-supply-demand-balance")
    assert resp.status_code == 503
