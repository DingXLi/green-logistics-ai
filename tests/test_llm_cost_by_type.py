"""
iter #48: tests for /api/persistence/llm-cost-by-type endpoint.

Covers:
1. Persistence.get_llm_cost_by_decision_type() — breakdown by decision_type
2. Sorted by n_total DESC
3. llm_rate_pct correctly computed
4. Window filter (since/until sim_day)
5. Endpoint exposes the new method
6. Endpoint validates input range
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
        db_path = os.path.join(tmpdir, "test_llm_by_type.db")
        p = Persistence(db_path)
        # Disable FK constraints for this test (llm_decisions has FK to cycles
        # but we only care about LLM aggregation, not the cycle itself)
        with p._conn() as conn:
            conn.execute("PRAGMA foreign_keys = OFF")
        yield p


def _insert_cycle(p, cycle_id, sim_day):
    """Insert a cycle row (idempotent: uses INSERT OR IGNORE)."""
    with p._conn() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO optimization_cycles
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


def _insert_decision(p, decision_type, target_id, sim_day, source="llm",
                     multiplier=1.0, confidence=0.8):
    # Insert a parent cycle first to satisfy FK
    _insert_cycle(p, f"c{sim_day}", sim_day)
    with p._conn() as conn:
        conn.execute(
            """INSERT INTO llm_decisions
               (cycle_id, sim_day, sim_hour, decision_type, target_id, target_type,
                multiplier, trend, confidence, reason, source, raw_json, wall_timestamp)
               VALUES (?, ?, 0, ?, ?, 'demand_point', ?, 'stable', ?, 'test', ?, '{}', '2026-09-04T00:00:00')""",
            (f"c{sim_day}", sim_day, decision_type, target_id, multiplier, confidence, source),
        )


# ============================================
# Persistence method tests
# ============================================


def test_empty_returns_empty_list(persistence):
    result = persistence.get_llm_cost_by_decision_type()
    assert result == []


def test_single_type_basic(persistence):
    _insert_decision(persistence, "demand_prediction", "DEM001", sim_day=1)
    _insert_decision(persistence, "demand_prediction", "DEM002", sim_day=2)
    _insert_decision(persistence, "demand_prediction", "DEM001", sim_day=3, source="fallback")
    result = persistence.get_llm_cost_by_decision_type()
    assert len(result) == 1
    row = result[0]
    assert row["decision_type"] == "demand_prediction"
    assert row["n_total"] == 3
    assert row["n_llm"] == 2
    assert row["n_fallback"] == 1
    assert row["llm_rate_pct"] == 66.67
    assert row["n_unique_targets"] == 2
    assert row["first_decision_sim_day"] == 1
    assert row["last_decision_sim_day"] == 3


def test_multiple_types_sorted_by_n_total(persistence):
    # 5 demand_prediction, 2 supply_prediction, 1 supply_collection
    for i in range(5):
        _insert_decision(persistence, "demand_prediction", f"DEM{i}", sim_day=1)
    for i in range(2):
        _insert_decision(persistence, "supply_prediction", f"SUP{i}", sim_day=1)
    _insert_decision(persistence, "supply_collection", "SUP0", sim_day=1, source="fallback")
    result = persistence.get_llm_cost_by_decision_type()
    assert len(result) == 3
    # Sorted by n_total DESC
    assert result[0]["decision_type"] == "demand_prediction"
    assert result[0]["n_total"] == 5
    assert result[1]["decision_type"] == "supply_prediction"
    assert result[1]["n_total"] == 2
    assert result[2]["decision_type"] == "supply_collection"
    assert result[2]["n_total"] == 1
    assert result[2]["n_llm"] == 0
    assert result[2]["llm_rate_pct"] == 0.0


def test_window_filter(persistence):
    _insert_decision(persistence, "demand_prediction", "DEM001", sim_day=5)
    _insert_decision(persistence, "demand_prediction", "DEM002", sim_day=15)
    _insert_decision(persistence, "demand_prediction", "DEM003", sim_day=25)
    # Window: 10-20
    result = persistence.get_llm_cost_by_decision_type(since_sim_day=10, until_sim_day=20)
    assert len(result) == 1
    assert result[0]["n_total"] == 1
    assert result[0]["first_decision_sim_day"] == 15
    # No window
    all_result = persistence.get_llm_cost_by_decision_type()
    assert all_result[0]["n_total"] == 3


def test_avg_multiplier_confidence(persistence):
    _insert_decision(persistence, "demand_prediction", "DEM1", sim_day=1,
                     multiplier=1.2, confidence=0.9)
    _insert_decision(persistence, "demand_prediction", "DEM2", sim_day=1,
                     multiplier=1.4, confidence=0.7)
    result = persistence.get_llm_cost_by_decision_type()
    assert result[0]["avg_multiplier"] == 1.3  # avg(1.2, 1.4) = 1.3
    assert result[0]["avg_confidence"] == 0.8  # avg(0.9, 0.7) = 0.8


# ============================================
# API endpoint tests
# ============================================


def test_endpoint_returns_200(monkeypatch):
    from web.backend import main as backend_main
    from fastapi.testclient import TestClient

    class _FakePersistence:
        def get_llm_cost_by_decision_type(self, since_sim_day=None, until_sim_day=None):
            return [
                {"decision_type": "demand_prediction", "n_total": 10, "n_llm": 8,
                 "n_fallback": 2, "llm_rate_pct": 80.0,
                 "avg_multiplier": 1.2, "avg_confidence": 0.85,
                 "n_unique_targets": 3, "first_decision_sim_day": 1,
                 "last_decision_sim_day": 30},
            ]

    class _FakeCoord:
        persistence = _FakePersistence()

    monkeypatch.setattr(backend_main, "coordinator", _FakeCoord())
    client = TestClient(backend_main.app)
    resp = client.get("/api/persistence/llm-cost-by-type")
    assert resp.status_code == 200
    data = resp.json()
    assert data["by_type"][0]["decision_type"] == "demand_prediction"
    assert data["by_type"][0]["n_total"] == 10


def test_endpoint_window_params(monkeypatch):
    from web.backend import main as backend_main
    from fastapi.testclient import TestClient

    class _FakePersistence:
        def get_llm_cost_by_decision_type(self, since_sim_day=None, until_sim_day=None):
            self.kwargs = (since_sim_day, until_sim_day)
            return []

    class _FakeCoord:
        persistence = _FakePersistence()

    monkeypatch.setattr(backend_main, "coordinator", _FakeCoord())
    client = TestClient(backend_main.app)
    resp = client.get("/api/persistence/llm-cost-by-type?since_sim_day=5&until_sim_day=20")
    assert resp.status_code == 200
    assert backend_main.coordinator.persistence.kwargs == (5, 20)


def test_endpoint_rejects_inverted_window(monkeypatch):
    from web.backend import main as backend_main
    from fastapi.testclient import TestClient

    class _FakePersistence:
        def get_llm_cost_by_decision_type(self, since_sim_day=None, until_sim_day=None):
            return []

    class _FakeCoord:
        persistence = _FakePersistence()

    monkeypatch.setattr(backend_main, "coordinator", _FakeCoord())
    client = TestClient(backend_main.app)
    resp = client.get("/api/persistence/llm-cost-by-type?since_sim_day=20&until_sim_day=10")
    assert resp.status_code == 400


def test_endpoint_returns_503_when_no_coordinator(monkeypatch):
    from web.backend import main as backend_main
    from fastapi.testclient import TestClient

    monkeypatch.setattr(backend_main, "coordinator", None)
    client = TestClient(backend_main.app)
    resp = client.get("/api/persistence/llm-cost-by-type")
    assert resp.status_code == 503
