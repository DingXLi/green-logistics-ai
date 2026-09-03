"""
iter #42: tests for /api/persistence/cohort-retention-by-material endpoint.

Covers:
1. Persistence.get_cohort_retention_by_material() pure logic (no API):
   - empty DB returns []
   - single material with mixed one-time + repeating supplies
   - multiple materials sorted by total_supply_ids DESC
   - retention_rate_pct / one_time_pct computation
2. /api/persistence/cohort-retention-by-material HTTP endpoint:
   - 503 when no coordinator
   - 200 happy path with valid data
   - schema validation
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
    """Build a temporary Persistence instance."""
    from agents.persistence import Persistence

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_cohort_by_material.db")
        p = Persistence(db_path)
        yield p


def _insert_cycle(p, cycle_id: str, sim_day: int):
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


def _insert_supply(p, cycle_id: str, supply_id: str, material_type: str):
    with p._conn() as conn:
        conn.execute(
            """INSERT INTO supply_offers
               (cycle_id, supply_id, material_type, available_tons,
                moisture_percent, quality_score,
                base_seasonal_multiplier, seasonal_multiplier, perturbation_applied)
               VALUES (?, ?, ?, 10.0, 0.5, 0.8, 1.0, 1.0, 0)""",
            (cycle_id, supply_id, material_type),
        )


# ---------------------------------------------------------------------------
# Persistence.get_cohort_retention_by_material() unit tests
# ---------------------------------------------------------------------------


def test_get_cohort_retention_by_material_empty(persistence):
    result = persistence.get_cohort_retention_by_material()
    assert result == []


def test_get_cohort_retention_by_material_single_material_mixed(persistence):
    """One material with 3 supplies: SUP_A appears 3 times, SUP_B 2 times, SUP_C 1 time."""
    for i in range(3):
        _insert_cycle(persistence, f"OPT00{i+1}", i + 1)
        _insert_supply(persistence, f"OPT00{i+1}", "SUP_A", "concrete")
        _insert_supply(persistence, f"OPT00{i+1}", "SUP_B", "concrete")
        _insert_supply(persistence, f"OPT00{i+1}", "SUP_C", "concrete")

    result = persistence.get_cohort_retention_by_material()
    assert len(result) == 1
    mat = result[0]
    assert mat["material_type"] == "concrete"
    assert mat["total_supply_ids"] == 3
    # All 3 supplies appear in all 3 cycles → n_repeating=3, n_one_time=0
    assert mat["n_one_time"] == 0
    assert mat["n_repeating"] == 3
    assert mat["retention_rate_pct"] == 100.0
    assert mat["one_time_pct"] == 0.0


def test_get_cohort_retention_by_material_multiple_materials_sorted(persistence):
    """Two materials: 'concrete' has 5 supplies, 'wood' has 2 supplies. Sorted by n_supply_ids DESC."""
    for i in range(3):
        _insert_cycle(persistence, f"OPT00{i+1}", i + 1)
        # All supplies on each cycle
        _insert_supply(persistence, f"OPT00{i+1}", "SUP_A", "concrete")
        _insert_supply(persistence, f"OPT00{i+1}", "SUP_B", "concrete")
        _insert_supply(persistence, f"OPT00{i+1}", "SUP_C", "concrete")
        _insert_supply(persistence, f"OPT00{i+1}", "SUP_D", "concrete")
        _insert_supply(persistence, f"OPT00{i+1}", "SUP_E", "concrete")
        # wood: only first 2 cycles
        if i < 2:
            _insert_supply(persistence, f"OPT00{i+1}", "SUP_F", "wood")
        _insert_supply(persistence, f"OPT00{i+1}", "SUP_G", "wood")

    result = persistence.get_cohort_retention_by_material()
    assert len(result) == 2
    # Sorted by total_supply_ids DESC: concrete (5) > wood (2)
    assert result[0]["material_type"] == "concrete"
    assert result[1]["material_type"] == "wood"
    assert result[0]["total_supply_ids"] == 5
    # All 5 concrete supplies appear 3 times (all cycles) → n_repeating=5, n_one_time=0
    assert result[0]["n_repeating"] == 5
    assert result[0]["n_one_time"] == 0
    assert result[1]["total_supply_ids"] == 2
    # SUP_F appears 2 cycles (repeating), SUP_G appears 3 cycles (repeating)
    assert result[1]["n_repeating"] == 2
    assert result[1]["n_one_time"] == 0


def test_get_cohort_retention_by_material_all_one_time(persistence):
    """All supplies appear only once → retention_rate=0."""
    for i in range(3):
        _insert_cycle(persistence, f"OPT00{i+1}", i + 1)
        _insert_supply(persistence, f"OPT00{i+1}", f"SUP_{i+1}", "plastic")
    result = persistence.get_cohort_retention_by_material()
    assert len(result) == 1
    assert result[0]["n_one_time"] == 3
    assert result[0]["n_repeating"] == 0
    assert result[0]["retention_rate_pct"] == 0.0
    assert result[0]["one_time_pct"] == 100.0


def test_get_cohort_retention_by_material_all_repeating(persistence):
    """All supplies repeat → one_time=0."""
    for i in range(5):
        _insert_cycle(persistence, f"OPT00{i+1}", i + 1)
        _insert_supply(persistence, f"OPT00{i+1}", "SUP_A", "metal")
        _insert_supply(persistence, f"OPT00{i+1}", "SUP_B", "metal")
    result = persistence.get_cohort_retention_by_material()
    assert len(result) == 1
    assert result[0]["n_one_time"] == 0
    assert result[0]["n_repeating"] == 2
    assert result[0]["retention_rate_pct"] == 100.0
    assert result[0]["one_time_pct"] == 0.0


def test_get_cohort_retention_by_material_null_material_excluded(persistence):
    """Supplies with NULL material_type should be excluded."""
    for i in range(2):
        _insert_cycle(persistence, f"OPT00{i+1}", i + 1)
        _insert_supply(persistence, f"OPT00{i+1}", "SUP_A", "wood")
        # Insert with NULL material_type directly
        with persistence._conn() as conn:
            conn.execute(
                """INSERT INTO supply_offers
                   (cycle_id, supply_id, material_type, available_tons,
                    moisture_percent, quality_score,
                    base_seasonal_multiplier, seasonal_multiplier, perturbation_applied)
                   VALUES (?, ?, NULL, 10.0, 0.5, 0.8, 1.0, 1.0, 0)""",
                (f"OPT00{i+1}", f"SUP_NULL_{i+1}"),
            )
    result = persistence.get_cohort_retention_by_material()
    assert len(result) == 1
    assert result[0]["material_type"] == "wood"
    assert result[0]["total_supply_ids"] == 1


# ---------------------------------------------------------------------------
# HTTP endpoint tests
# ---------------------------------------------------------------------------


def test_cohort_by_material_endpoint_no_coordinator(monkeypatch):
    from web.backend import main as backend_main

    monkeypatch.setattr(backend_main, "coordinator", None)
    from fastapi.testclient import TestClient
    client = TestClient(backend_main.app)
    resp = client.get("/api/persistence/cohort-retention-by-material")
    assert resp.status_code == 503


def test_cohort_by_material_endpoint_happy_path(monkeypatch):
    from web.backend import main as backend_main

    fake_data = [
        {
            "material_type": "concrete",
            "total_supply_ids": 10,
            "n_one_time": 3,
            "n_repeating": 7,
            "retention_rate_pct": 70.0,
            "one_time_pct": 30.0,
            "total_supply_offers": 25,
            "total_cycles_with_supply": 30,
        },
        {
            "material_type": "wood",
            "total_supply_ids": 5,
            "n_one_time": 4,
            "n_repeating": 1,
            "retention_rate_pct": 20.0,
            "one_time_pct": 80.0,
            "total_supply_offers": 6,
            "total_cycles_with_supply": 30,
        },
    ]

    class _FakePersistence:
        def get_cohort_retention_by_material(self):
            return fake_data

    class _FakeCoord:
        persistence = _FakePersistence()

    monkeypatch.setattr(backend_main, "coordinator", _FakeCoord())
    from fastapi.testclient import TestClient
    client = TestClient(backend_main.app)
    resp = client.get("/api/persistence/cohort-retention-by-material")
    assert resp.status_code == 200
    data = resp.json()
    assert data["n_materials"] == 2
    assert len(data["by_material"]) == 2

    # Schema check
    required = {"material_type", "total_supply_ids", "n_one_time", "n_repeating",
                "retention_rate_pct", "one_time_pct",
                "total_supply_offers", "total_cycles_with_supply"}
    for m in data["by_material"]:
        assert required.issubset(m.keys())


def test_cohort_by_material_endpoint_empty_db(monkeypatch):
    from web.backend import main as backend_main

    class _FakePersistence:
        def get_cohort_retention_by_material(self):
            return []

    class _FakeCoord:
        persistence = _FakePersistence()

    monkeypatch.setattr(backend_main, "coordinator", _FakeCoord())
    from fastapi.testclient import TestClient
    client = TestClient(backend_main.app)
    resp = client.get("/api/persistence/cohort-retention-by-material")
    assert resp.status_code == 200
    data = resp.json()
    assert data["n_materials"] == 0
    assert data["by_material"] == []
