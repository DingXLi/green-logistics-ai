"""
iter #41: tests for /api/persistence/vehicle-stats endpoint + persistence method.

Covers:
1. Persistence.get_vehicle_stats() pure logic (no API):
   - empty DB returns []
   - single vehicle, multiple cycles aggregates correctly
   - vehicle_id filter
   - efficiency metrics (avg_cost_per_km, avg_co2_per_km)
   - sorted by total_distance_km DESC
2. /api/persistence/vehicle-stats HTTP endpoint:
   - 503 when no coordinator
   - 400 on invalid limit
   - 200 happy path
   - schema validation
"""
import os
import sys
import tempfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Persistence.get_vehicle_stats() unit tests
# ---------------------------------------------------------------------------


@pytest.fixture
def persistence():
    """Build a temporary Persistence instance."""
    from agents.persistence import Persistence

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_vehicle_stats.db")
        p = Persistence(db_path)
        yield p


def _insert_cycle(p, cycle_id: str, sim_day: int):
    """Helper to insert a row into optimization_cycles."""
    with p._conn() as conn:
        conn.execute(
            """INSERT INTO optimization_cycles
               (cycle_id, sim_day, sim_hour, wall_timestamp,
                activity_factor, n_supply_offers, n_demand_requests, n_matches,
                total_tons, total_cost_sek, total_co2_kg, total_distance_km,
                n_vehicles_used, n_vehicles_available, fleet_utilization_pct,
                solver_status, wall_duration_ms)
               VALUES (?, ?, 0, '2026-09-03T00:00:00',
                       1.0, 1, 1, 1,
                       5.0, 100.0, 50.0, 20.0,
                       1, 1, 100.0,
                       'OPTIMAL', 100)""",
            (cycle_id, sim_day),
        )


def _insert_route(p, cycle_id: str, vehicle_id: str, distance: float,
                  duration: float, cost: float, co2: float):
    with p._conn() as conn:
        conn.execute(
            """INSERT INTO routes
               (cycle_id, vehicle_id, stops_json, distance_km,
                duration_hours, cost_sek, co2_kg)
               VALUES (?, ?, '[]', ?, ?, ?, ?)""",
            (cycle_id, vehicle_id, distance, duration, cost, co2),
        )


def test_get_vehicle_stats_empty_returns_empty_list(persistence):
    result = persistence.get_vehicle_stats()
    assert result == []


def test_get_vehicle_stats_single_vehicle(persistence):
    _insert_cycle(persistence, "OPT001", 1)
    _insert_route(persistence, "OPT001", "V1", 50.0, 2.0, 100.0, 30.0)
    result = persistence.get_vehicle_stats()
    assert len(result) == 1
    v = result[0]
    assert v["vehicle_id"] == "V1"
    assert v["n_routes"] == 1
    assert v["total_distance_km"] == 50.0
    assert v["total_duration_hours"] == 2.0
    assert v["total_cost_sek"] == 100.0
    assert v["total_co2_kg"] == 30.0
    assert v["avg_distance_km"] == 50.0
    assert v["avg_cost_per_km_sek"] == 2.0
    assert v["avg_co2_per_km_kg"] == 0.6
    assert v["first_cycle_id"] == "OPT001"
    assert v["last_cycle_id"] == "OPT001"
    assert v["last_sim_day"] == 1


def test_get_vehicle_stats_multiple_cycles_same_vehicle(persistence):
    for i in range(3):
        _insert_cycle(persistence, f"OPT00{i+1}", i + 1)
        _insert_route(persistence, f"OPT00{i+1}", "V1",
                      distance=10.0 + i * 5, duration=1.0,
                      cost=20.0, co2=6.0)
    result = persistence.get_vehicle_stats()
    assert len(result) == 1
    v = result[0]
    assert v["n_routes"] == 3
    assert v["total_distance_km"] == 10.0 + 15.0 + 20.0  # 45.0
    assert v["total_cost_sek"] == 60.0
    assert v["total_co2_kg"] == 18.0
    assert v["avg_distance_km"] == 15.0  # 45/3
    # avg_cost_per_km = 60/45 = 1.333
    assert v["avg_cost_per_km_sek"] == round(60.0 / 45.0, 3)
    assert v["last_sim_day"] == 3  # most recent cycle


def test_get_vehicle_stats_multiple_vehicles_sorted_by_distance(persistence):
    _insert_cycle(persistence, "OPT001", 1)
    _insert_route(persistence, "OPT001", "V1", 100.0, 5.0, 200.0, 60.0)
    _insert_route(persistence, "OPT001", "V2", 50.0, 2.5, 100.0, 30.0)
    _insert_route(persistence, "OPT001", "V3", 200.0, 10.0, 400.0, 120.0)

    result = persistence.get_vehicle_stats()
    assert len(result) == 3
    # Sorted by total_distance_km DESC: V3 (200), V1 (100), V2 (50)
    assert [v["vehicle_id"] for v in result] == ["V3", "V1", "V2"]


def test_get_vehicle_stats_vehicle_id_filter(persistence):
    _insert_cycle(persistence, "OPT001", 1)
    _insert_route(persistence, "OPT001", "V1", 50.0, 2.0, 100.0, 30.0)
    _insert_route(persistence, "OPT001", "V2", 100.0, 4.0, 200.0, 60.0)

    result = persistence.get_vehicle_stats(vehicle_id="V1")
    assert len(result) == 1
    assert result[0]["vehicle_id"] == "V1"

    result = persistence.get_vehicle_stats(vehicle_id="V999")
    assert result == []  # nonexistent vehicle


def test_get_vehicle_stats_limit(persistence):
    _insert_cycle(persistence, "OPT001", 1)
    for i in range(5):
        _insert_route(persistence, "OPT001", f"V{i+1}",
                      distance=10.0 + i, duration=1.0, cost=20.0, co2=6.0)

    result = persistence.get_vehicle_stats(limit=3)
    assert len(result) == 3


def test_get_vehicle_stats_null_vehicle_id_excluded(persistence):
    """Routes with NULL vehicle_id should not appear in stats."""
    _insert_cycle(persistence, "OPT001", 1)
    _insert_route(persistence, "OPT001", "V1", 50.0, 2.0, 100.0, 30.0)
    # Insert route with NULL vehicle_id directly
    with persistence._conn() as conn:
        conn.execute(
            """INSERT INTO routes
               (cycle_id, vehicle_id, stops_json, distance_km,
                duration_hours, cost_sek, co2_kg)
               VALUES ('OPT001', NULL, '[]', 10.0, 0.5, 20.0, 6.0)""",
        )
    result = persistence.get_vehicle_stats()
    assert len(result) == 1
    assert result[0]["vehicle_id"] == "V1"


def test_get_vehicle_stats_zero_distance_handles_efficiency(persistence):
    """avg_cost_per_km and avg_co2_per_km should be None when total_distance=0."""
    _insert_cycle(persistence, "OPT001", 1)
    _insert_route(persistence, "OPT001", "V1", 0.0, 1.0, 10.0, 5.0)
    result = persistence.get_vehicle_stats()
    assert len(result) == 1
    assert result[0]["total_distance_km"] == 0.0
    assert result[0]["avg_cost_per_km_sek"] is None
    assert result[0]["avg_co2_per_km_kg"] is None


# ---------------------------------------------------------------------------
# HTTP endpoint tests
# ---------------------------------------------------------------------------


def test_vehicle_stats_endpoint_no_coordinator(monkeypatch):
    from web.backend import main as backend_main

    monkeypatch.setattr(backend_main, "coordinator", None)
    from fastapi.testclient import TestClient
    client = TestClient(backend_main.app)
    resp = client.get("/api/persistence/vehicle-stats")
    assert resp.status_code == 503


def test_vehicle_stats_endpoint_invalid_limit(monkeypatch):
    from web.backend import main as backend_main

    class _FakePersistence:
        def get_vehicle_stats(self, vehicle_id=None, limit=100):
            return []

    class _FakeCoord:
        persistence = _FakePersistence()

    monkeypatch.setattr(backend_main, "coordinator", _FakeCoord())
    from fastapi.testclient import TestClient
    client = TestClient(backend_main.app)
    resp = client.get("/api/persistence/vehicle-stats?limit=0")
    assert resp.status_code == 400
    resp = client.get("/api/persistence/vehicle-stats?limit=9999")
    assert resp.status_code == 400


def test_vehicle_stats_endpoint_happy_path(monkeypatch):
    from web.backend import main as backend_main

    fake_data = [
        {
            "vehicle_id": "V1",
            "n_routes": 5,
            "total_distance_km": 250.0,
            "total_duration_hours": 10.0,
            "total_cost_sek": 500.0,
            "total_co2_kg": 150.0,
            "avg_distance_km": 50.0,
            "avg_duration_hours": 2.0,
            "avg_cost_per_km_sek": 2.0,
            "avg_co2_per_km_kg": 0.6,
            "first_cycle_id": "OPT001",
            "last_cycle_id": "OPT005",
            "last_sim_day": 5,
        },
        {
            "vehicle_id": "V2",
            "n_routes": 3,
            "total_distance_km": 150.0,
            "total_duration_hours": 6.0,
            "total_cost_sek": 300.0,
            "total_co2_kg": 90.0,
            "avg_distance_km": 50.0,
            "avg_duration_hours": 2.0,
            "avg_cost_per_km_sek": 2.0,
            "avg_co2_per_km_kg": 0.6,
            "first_cycle_id": "OPT002",
            "last_cycle_id": "OPT005",
            "last_sim_day": 5,
        },
    ]

    class _FakePersistence:
        def get_vehicle_stats(self, vehicle_id=None, limit=100):
            return [v for v in fake_data if vehicle_id is None or v["vehicle_id"] == vehicle_id]

    class _FakeCoord:
        persistence = _FakePersistence()

    monkeypatch.setattr(backend_main, "coordinator", _FakeCoord())
    from fastapi.testclient import TestClient
    client = TestClient(backend_main.app)
    resp = client.get("/api/persistence/vehicle-stats")
    assert resp.status_code == 200
    data = resp.json()
    assert data["n_vehicles"] == 2
    assert len(data["vehicles"]) == 2
    # Schema check
    required_keys = {"vehicle_id", "n_routes", "total_distance_km",
                     "total_duration_hours", "total_cost_sek", "total_co2_kg",
                     "avg_distance_km", "avg_duration_hours",
                     "avg_cost_per_km_sek", "avg_co2_per_km_kg",
                     "first_cycle_id", "last_cycle_id", "last_sim_day"}
    for v in data["vehicles"]:
        assert required_keys.issubset(v.keys())


def test_vehicle_stats_endpoint_with_vehicle_filter(monkeypatch):
    from web.backend import main as backend_main

    class _FakePersistence:
        def __init__(self):
            self.called_with = None

        def get_vehicle_stats(self, vehicle_id=None, limit=100):
            self.called_with = (vehicle_id, limit)
            if vehicle_id == "V1":
                return [{
                    "vehicle_id": "V1",
                    "n_routes": 5,
                    "total_distance_km": 250.0,
                    "total_duration_hours": 10.0,
                    "total_cost_sek": 500.0,
                    "total_co2_kg": 150.0,
                    "avg_distance_km": 50.0,
                    "avg_duration_hours": 2.0,
                    "avg_cost_per_km_sek": 2.0,
                    "avg_co2_per_km_kg": 0.6,
                    "first_cycle_id": "OPT001",
                    "last_cycle_id": "OPT005",
                    "last_sim_day": 5,
                }]
            return []

    fake_p = _FakePersistence()
    class _FakeCoord:
        persistence = fake_p

    monkeypatch.setattr(backend_main, "coordinator", _FakeCoord())
    from fastapi.testclient import TestClient
    client = TestClient(backend_main.app)
    resp = client.get("/api/persistence/vehicle-stats?vehicle_id=V1")
    assert resp.status_code == 200
    assert fake_p.called_with == ("V1", 100)
    data = resp.json()
    assert data["n_vehicles"] == 1
    assert data["vehicles"][0]["vehicle_id"] == "V1"
