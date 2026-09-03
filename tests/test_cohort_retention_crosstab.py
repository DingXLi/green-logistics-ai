"""
iter #44: tests for cohort retention cross-tab (period × material).

Covers:
1. Persistence.get_cohort_retention_crosstab() pure logic:
   - Empty DB
   - Single material
   - Multiple materials × multiple periods
   - Trend computation
   - Material filter
2. /api/persistence/cohort-retention-crosstab HTTP endpoint
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
        db_path = os.path.join(tmpdir, "test_crosstab.db")
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
# Pure persistence tests
# ---------------------------------------------------------------------------


def test_crosstab_empty_db(persistence):
    result = persistence.get_cohort_retention_crosstab()
    assert result["n_periods"] == 0
    assert result["materials"] == []
    assert result["matrix"] == []


def test_crosstab_single_material_single_period(persistence):
    """All cycles in one period, one material."""
    for i in range(3):
        _insert_cycle(persistence, f"OPT00{i+1}", i + 1)
        _insert_supply(persistence, f"OPT00{i+1}", "SUP_A", "concrete")
    result = persistence.get_cohort_retention_crosstab(n_periods=2)
    assert len(result["materials"]) == 1
    assert result["materials"][0] == "concrete"
    # At least one period should have data
    total_count = sum(sum(row) for row in result["cell_counts"])
    # 3 supplies across periods (each period covers part of the day range)
    assert total_count >= 1
    # The crosstab distributes supplies across periods; verify matrix has data
    assert len(result["matrix"]) >= 1


def test_crosstab_multiple_materials(persistence):
    for i in range(3):
        _insert_cycle(persistence, f"OPT00{i+1}", i + 1)
        _insert_supply(persistence, f"OPT00{i+1}", "SUP_A1", "concrete")
        _insert_supply(persistence, f"OPT00{i+1}", "SUP_B1", "wood")
    result = persistence.get_cohort_retention_crosstab()
    assert "concrete" in result["materials"]
    assert "wood" in result["materials"]
    assert len(result["materials"]) == 2
    # Matrix should have same number of cols as materials
    for row in result["matrix"]:
        assert len(row) == 2


def test_crosstab_filter_by_material(persistence):
    for i in range(3):
        _insert_cycle(persistence, f"OPT00{i+1}", i + 1)
        _insert_supply(persistence, f"OPT00{i+1}", "SUP_A1", "concrete")
        _insert_supply(persistence, f"OPT00{i+1}", "SUP_B1", "wood")
    result = persistence.get_cohort_retention_crosstab(material_type="concrete")
    assert result["material_filter"] == "concrete"
    assert result["materials"] == ["concrete"]


def test_crosstab_trend_per_material(persistence):
    """When retention improves over time, trend should be 'improving'."""
    for i in range(4):
        _insert_cycle(persistence, f"OPT00{i+1}", i + 1)
        _insert_supply(persistence, f"OPT00{i+1}", "SUP_A1", "concrete")
    # SUP_A1 appears in all 4 cycles - high retention in every period
    result = persistence.get_cohort_retention_crosstab(n_periods=2)
    assert "concrete" in result["trend_per_material"]
    # All retention rates = 100% (no change) → stable
    trend = result["trend_per_material"]["concrete"]
    assert trend in ("stable", "improving")


def test_crosstab_trend_declining(persistence):
    """Construct scenario with declining retention across periods."""
    # Period 1 (day 1-2): SUP_A repeats (2/2 = 100% retention)
    # Period 2 (day 3-4): SUP_B and SUP_C appear once each (0/2 = 0% retention)
    _insert_cycle(persistence, "OPT001", 1)
    _insert_cycle(persistence, "OPT002", 2)
    _insert_supply(persistence, "OPT001", "SUP_A", "concrete")
    _insert_supply(persistence, "OPT002", "SUP_A", "concrete")
    _insert_cycle(persistence, "OPT003", 3)
    _insert_cycle(persistence, "OPT004", 4)
    _insert_supply(persistence, "OPT003", "SUP_B", "concrete")
    _insert_supply(persistence, "OPT004", "SUP_C", "concrete")
    result = persistence.get_cohort_retention_crosstab(n_periods=2)
    if len(result["period_labels"]) >= 2:
        trend = result["trend_per_material"].get("concrete")
        # 100% → 0% = declining (drop > 5%)
        assert trend == "declining"


def test_crosstab_returns_cell_counts(persistence):
    """Each cell should have a count (sample size)."""
    for i in range(2):
        _insert_cycle(persistence, f"OPT00{i+1}", i + 1)
        _insert_supply(persistence, f"OPT00{i+1}", "SUP_A", "concrete")
    result = persistence.get_cohort_retention_crosstab(n_periods=2)
    assert len(result["cell_counts"]) == len(result["matrix"])
    for row in result["cell_counts"]:
        for count in row:
            assert count >= 0


def test_crosstab_period_labels(persistence):
    for i in range(5):
        _insert_cycle(persistence, f"OPT00{i+1}", i + 1)
    result = persistence.get_cohort_retention_crosstab(n_periods=3)
    # period_labels should be sorted by period_idx
    labels = result["period_labels"]
    if len(labels) > 1:
        for i in range(1, len(labels)):
            assert labels[i]["period_idx"] > labels[i-1]["period_idx"]
            assert labels[i]["sim_day_min"] >= labels[i-1]["sim_day_min"]


# ---------------------------------------------------------------------------
# HTTP endpoint tests
# ---------------------------------------------------------------------------


def test_crosstab_endpoint_no_coordinator(monkeypatch):
    from web.backend import main as backend_main
    from fastapi.testclient import TestClient

    monkeypatch.setattr(backend_main, "coordinator", None)
    client = TestClient(backend_main.app)
    resp = client.get("/api/persistence/cohort-retention-crosstab")
    assert resp.status_code == 503


def test_crosstab_endpoint_invalid_period_unit(monkeypatch):
    from web.backend import main as backend_main
    from fastapi.testclient import TestClient

    class _FakeCoord:
        persistence = object()
    monkeypatch.setattr(backend_main, "coordinator", _FakeCoord())
    client = TestClient(backend_main.app)
    resp = client.get("/api/persistence/cohort-retention-crosstab?period_unit=invalid")
    assert resp.status_code == 400


def test_crosstab_endpoint_invalid_n_periods(monkeypatch):
    from web.backend import main as backend_main
    from fastapi.testclient import TestClient

    class _FakeCoord:
        persistence = object()
    monkeypatch.setattr(backend_main, "coordinator", _FakeCoord())
    client = TestClient(backend_main.app)
    resp = client.get("/api/persistence/cohort-retention-crosstab?n_periods=0")
    assert resp.status_code == 400
    resp = client.get("/api/persistence/cohort-retention-crosstab?n_periods=999")
    assert resp.status_code == 400


def test_crosstab_endpoint_happy_path(monkeypatch):
    from web.backend import main as backend_main
    from fastapi.testclient import TestClient

    fake_result = {
        "n_periods": 2,
        "period_unit": "quartile",
        "period_labels": [
            {"period_idx": 1, "sim_day_min": 1, "sim_day_max": 15},
            {"period_idx": 2, "sim_day_min": 16, "sim_day_max": 30},
        ],
        "materials": ["concrete", "wood"],
        "matrix": [[100.0, 50.0], [80.0, 60.0]],
        "cell_counts": [[5, 4], [10, 8]],
        "material_filter": None,
        "trend_per_material": {"concrete": "declining", "wood": "improving"},
    }

    class _FakePersistence:
        def get_cohort_retention_crosstab(self, n_periods=4, period_unit="quartile", material_type=None):
            return fake_result

    class _FakeCoord:
        persistence = _FakePersistence()

    monkeypatch.setattr(backend_main, "coordinator", _FakeCoord())
    client = TestClient(backend_main.app)
    resp = client.get("/api/persistence/cohort-retention-crosstab?material_type=concrete")
    assert resp.status_code == 200
    data = resp.json()
    assert data["n_periods"] == 2
    assert data["materials"] == ["concrete", "wood"]
    assert len(data["matrix"]) == 2
    assert len(data["matrix"][0]) == 2
    assert data["trend_per_material"]["concrete"] == "declining"
    assert data["trend_per_material"]["wood"] == "improving"


def test_crosstab_endpoint_with_filter(monkeypatch):
    from web.backend import main as backend_main
    from fastapi.testclient import TestClient

    fake_result = {
        "n_periods": 2,
        "period_unit": "quartile",
        "period_labels": [
            {"period_idx": 1, "sim_day_min": 1, "sim_day_max": 15},
        ],
        "materials": ["concrete"],
        "matrix": [[100.0]],
        "cell_counts": [[5]],
        "material_filter": "concrete",
        "trend_per_material": {"concrete": "stable"},
    }

    class _FakePersistence:
        def get_cohort_retention_crosstab(self, n_periods=4, period_unit="quartile", material_type=None):
            self.called_with = (n_periods, period_unit, material_type)
            return fake_result

    class _FakeCoord:
        persistence = _FakePersistence()

    monkeypatch.setattr(backend_main, "coordinator", _FakeCoord())
    client = TestClient(backend_main.app)
    resp = client.get("/api/persistence/cohort-retention-crosstab?material_type=concrete&n_periods=4&period_unit=week")
    assert resp.status_code == 200
    # Verify filter was passed
    assert backend_main.coordinator.persistence.called_with == (4, "week", "concrete")
