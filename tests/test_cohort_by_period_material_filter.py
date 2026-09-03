"""
iter #45: tests for cohort-retention-by-period material_type filter.

Covers:
1. Persistence.get_cohort_retention_by_period(material_type=...) filters correctly
2. Returns material_type_filter in response
3. /api/persistence/cohort-retention-by-period accepts material_type query param
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
        db_path = os.path.join(tmpdir, "test_period_mat.db")
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
               VALUES (?, ?, 0, '2026-09-03T00:00:00',
                       1.0, 1, 1, 1, 5.0, 100.0, 50.0, 20.0,
                       1, 1, 50.0, 'OPTIMAL', 100)""",
            (cycle_id, sim_day),
        )


def _insert_supply(p, cycle_id, supply_id, material_type):
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
# Persistence tests
# ---------------------------------------------------------------------------


def test_period_no_filter_includes_all_materials(persistence):
    """Without filter, all materials' supplies are included."""
    for i in range(3):
        _insert_cycle(persistence, f"OPT00{i+1}", i + 1)
        _insert_supply(persistence, f"OPT00{i+1}", "SUP_A", "concrete")
        _insert_supply(persistence, f"OPT00{i+1}", "SUP_B", "wood")
    result = persistence.get_cohort_retention_by_period()
    assert result["material_type_filter"] is None
    # All 6 supplies (3 concrete + 3 wood) should be counted
    total = sum(p["n_supply_ids"] for p in result["periods"])
    assert total == 6


def test_period_filter_by_material(persistence):
    """With material filter, only matching supplies are included."""
    for i in range(3):
        _insert_cycle(persistence, f"OPT00{i+1}", i + 1)
        _insert_supply(persistence, f"OPT00{i+1}", "SUP_A", "concrete")
        _insert_supply(persistence, f"OPT00{i+1}", "SUP_B", "wood")
    result = persistence.get_cohort_retention_by_period(material_type="concrete")
    assert result["material_type_filter"] == "concrete"
    # Only 3 concrete supplies should be counted
    total = sum(p["n_supply_ids"] for p in result["periods"])
    assert total == 3


def test_period_filter_nonexistent_material(persistence):
    """Filter for non-existent material returns 0 supplies."""
    for i in range(3):
        _insert_cycle(persistence, f"OPT00{i+1}", i + 1)
        _insert_supply(persistence, f"OPT00{i+1}", "SUP_A", "concrete")
    result = persistence.get_cohort_retention_by_period(material_type="unicornium")
    assert result["material_type_filter"] == "unicornium"
    total = sum(p["n_supply_ids"] for p in result["periods"])
    assert total == 0


def test_period_filter_changes_total_supply_ids(persistence):
    """Total supply_ids count reflects filter."""
    for i in range(3):
        _insert_cycle(persistence, f"OPT00{i+1}", i + 1)
        _insert_supply(persistence, f"OPT00{i+1}", "SUP_A", "concrete")
        _insert_supply(persistence, f"OPT00{i+1}", "SUP_B", "wood")
    full = persistence.get_cohort_retention_by_period()
    filtered = persistence.get_cohort_retention_by_period(material_type="concrete")
    assert filtered["total_supply_ids"] < full["total_supply_ids"]


def test_period_filter_null_material_excluded(persistence):
    """Supplies with NULL material_type are excluded under filter."""
    for i in range(2):
        _insert_cycle(persistence, f"OPT00{i+1}", i + 1)
        _insert_supply(persistence, f"OPT00{i+1}", "SUP_A", "concrete")
        with persistence._conn() as conn:
            conn.execute(
                """INSERT INTO supply_offers
                   (cycle_id, supply_id, material_type, available_tons,
                    moisture_percent, quality_score,
                    base_seasonal_multiplier, seasonal_multiplier, perturbation_applied)
                   VALUES (?, ?, NULL, 10.0, 0.5, 0.8, 1.0, 1.0, 0)""",
                (f"OPT00{i+1}", f"SUP_NULL_{i+1}"),
            )
    result = persistence.get_cohort_retention_by_period(material_type="concrete")
    total = sum(p["n_supply_ids"] for p in result["periods"])
    # Only 2 concrete supplies (NULL ones excluded)
    assert total == 2


# ---------------------------------------------------------------------------
# HTTP endpoint tests
# ---------------------------------------------------------------------------


def test_period_endpoint_with_material_filter(monkeypatch):
    from web.backend import main as backend_main
    from fastapi.testclient import TestClient

    fake_result = {
        "total_supply_ids": 5,
        "n_periods": 2,
        "period_unit": "quartile",
        "period_labels": ["P1", "P2"],
        "periods": [
            {"period_idx": 1, "period_label": "P1", "sim_day_range": {"min": 1, "max": 15}, "n_supply_ids": 2, "n_one_time": 0, "n_repeating": 2, "retention_rate_pct": 100.0, "one_time_pct": 0.0},
            {"period_idx": 2, "period_label": "P2", "sim_day_range": {"min": 16, "max": 30}, "n_supply_ids": 3, "n_one_time": 1, "n_repeating": 2, "retention_rate_pct": 66.7, "one_time_pct": 33.3},
        ],
        "trend": "declining",
        "material_type_filter": "concrete",
    }

    class _FakePersistence:
        def get_cohort_retention_by_period(self, n_periods=4, period_unit="quartile", material_type=None):
            self.called_with = (n_periods, period_unit, material_type)
            return fake_result

    class _FakeCoord:
        persistence = _FakePersistence()

    monkeypatch.setattr(backend_main, "coordinator", _FakeCoord())
    client = TestClient(backend_main.app)
    resp = client.get("/api/persistence/cohort-retention-by-period?material_type=concrete&n_periods=2")
    assert resp.status_code == 200
    data = resp.json()
    assert data["material_type_filter"] == "concrete"
    # Verify call
    assert backend_main.coordinator.persistence.called_with == (2, "quartile", "concrete")


def test_period_endpoint_without_filter(monkeypatch):
    from web.backend import main as backend_main
    from fastapi.testclient import TestClient

    fake_result = {
        "total_supply_ids": 10,
        "n_periods": 4,
        "period_unit": "quartile",
        "period_labels": [],
        "periods": [],
        "trend": "unknown",
        "material_type_filter": None,
    }

    class _FakePersistence:
        def get_cohort_retention_by_period(self, n_periods=4, period_unit="quartile", material_type=None):
            self.called_with = (n_periods, period_unit, material_type)
            return fake_result

    class _FakeCoord:
        persistence = _FakePersistence()

    monkeypatch.setattr(backend_main, "coordinator", _FakeCoord())
    client = TestClient(backend_main.app)
    resp = client.get("/api/persistence/cohort-retention-by-period")
    assert resp.status_code == 200
    data = resp.json()
    assert data["material_type_filter"] is None
    assert backend_main.coordinator.persistence.called_with == (4, "quartile", None)
