"""
iter #48: tests for /api/persistence/llm-decision-targets endpoint.

Covers:
1. Persistence.get_llm_decision_targets() — per-target stats
2. Sorted by n_calls DESC
3. decision_type filter
4. limit respected
5. Endpoint exposes the new method
6. Endpoint validates limit
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
        db_path = os.path.join(tmpdir, "test_llm_targets.db")
        p = Persistence(db_path)
        with p._conn() as conn:
            conn.execute("PRAGMA foreign_keys = OFF")
        yield p


def _insert_cycle(p, cycle_id, sim_day):
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
                     target_type="demand_point", multiplier=1.0, confidence=0.8):
    _insert_cycle(p, f"c{sim_day}", sim_day)
    with p._conn() as conn:
        conn.execute(
            """INSERT INTO llm_decisions
               (cycle_id, sim_day, sim_hour, decision_type, target_id, target_type,
                multiplier, trend, confidence, reason, source, raw_json, wall_timestamp)
               VALUES (?, ?, 0, ?, ?, ?, ?, 'stable', ?, 'test', ?, '{}', '2026-09-04T00:00:00')""",
            (f"c{sim_day}", sim_day, decision_type, target_id, target_type,
             multiplier, confidence, source),
        )


# ============================================
# Persistence method tests
# ============================================


def test_empty_returns_empty(persistence):
    result = persistence.get_llm_decision_targets()
    assert result == []


def test_single_target(persistence):
    for d in [1, 2, 3]:
        _insert_decision(persistence, "demand_prediction", "DEM001", sim_day=d)
    result = persistence.get_llm_decision_targets()
    assert len(result) == 1
    assert result[0]["target_id"] == "DEM001"
    assert result[0]["n_calls"] == 3
    assert result[0]["n_real_llm"] == 3
    assert result[0]["n_fallback"] == 0
    assert result[0]["first_called_sim_day"] == 1
    assert result[0]["last_called_sim_day"] == 3


def test_multiple_targets_sorted_by_n_calls(persistence):
    # DEM001: 5 calls, DEM002: 3, DEM003: 1
    for d in [1, 2, 3, 4, 5]:
        _insert_decision(persistence, "demand_prediction", "DEM001", sim_day=d)
    for d in [1, 2, 3]:
        _insert_decision(persistence, "demand_prediction", "DEM002", sim_day=d)
    _insert_decision(persistence, "demand_prediction", "DEM003", sim_day=1)
    result = persistence.get_llm_decision_targets()
    assert len(result) == 3
    assert [r["target_id"] for r in result] == ["DEM001", "DEM002", "DEM003"]
    assert [r["n_calls"] for r in result] == [5, 3, 1]


def test_decision_type_filter(persistence):
    _insert_decision(persistence, "demand_prediction", "DEM001", sim_day=1)
    _insert_decision(persistence, "supply_prediction", "SUP001", sim_day=1)
    result = persistence.get_llm_decision_targets(decision_type="demand_prediction")
    assert len(result) == 1
    assert result[0]["target_id"] == "DEM001"
    assert result[0]["decision_type"] == "demand_prediction"


def test_limit_respected(persistence):
    for i in range(10):
        _insert_decision(persistence, "demand_prediction", f"DEM{i:03d}", sim_day=1)
    result = persistence.get_llm_decision_targets(limit=3)
    assert len(result) == 3


def test_avg_metrics(persistence):
    _insert_decision(persistence, "demand_prediction", "DEM001", sim_day=1,
                     multiplier=1.2, confidence=0.9)
    _insert_decision(persistence, "demand_prediction", "DEM001", sim_day=2,
                     multiplier=1.4, confidence=0.7)
    result = persistence.get_llm_decision_targets()
    assert result[0]["avg_multiplier"] == 1.3
    assert result[0]["avg_confidence"] == 0.8


def test_n_real_llm_vs_fallback(persistence):
    _insert_decision(persistence, "demand_prediction", "DEM001", sim_day=1, source="llm")
    _insert_decision(persistence, "demand_prediction", "DEM001", sim_day=2, source="llm")
    _insert_decision(persistence, "demand_prediction", "DEM001", sim_day=3, source="fallback")
    result = persistence.get_llm_decision_targets()
    assert result[0]["n_calls"] == 3
    assert result[0]["n_real_llm"] == 2
    assert result[0]["n_fallback"] == 1


# ============================================
# API endpoint tests
# ============================================


def test_endpoint_returns_200(monkeypatch):
    from web.backend import main as backend_main
    from fastapi.testclient import TestClient

    class _FakePersistence:
        def get_llm_decision_targets(self, decision_type=None, limit=50):
            self.kwargs = (decision_type, limit)
            return [
                {"target_id": "DEM001", "decision_type": "demand_prediction",
                 "target_type": "demand_point", "n_calls": 5,
                 "n_real_llm": 4, "n_fallback": 1,
                 "last_called_sim_day": 10, "first_called_sim_day": 1,
                 "avg_multiplier": 1.2, "avg_confidence": 0.85},
            ]

    class _FakeCoord:
        persistence = _FakePersistence()

    monkeypatch.setattr(backend_main, "coordinator", _FakeCoord())
    client = TestClient(backend_main.app)
    resp = client.get("/api/persistence/llm-decision-targets")
    assert resp.status_code == 200
    data = resp.json()
    assert data["n_targets"] == 1
    assert data["targets"][0]["target_id"] == "DEM001"
    assert backend_main.coordinator.persistence.kwargs == (None, 50)


def test_endpoint_with_decision_type_filter(monkeypatch):
    from web.backend import main as backend_main
    from fastapi.testclient import TestClient

    class _FakePersistence:
        def get_llm_decision_targets(self, decision_type=None, limit=50):
            self.kwargs = (decision_type, limit)
            return []

    class _FakeCoord:
        persistence = _FakePersistence()

    monkeypatch.setattr(backend_main, "coordinator", _FakeCoord())
    client = TestClient(backend_main.app)
    resp = client.get("/api/persistence/llm-decision-targets?decision_type=supply_prediction&limit=10")
    assert resp.status_code == 200
    assert backend_main.coordinator.persistence.kwargs == ("supply_prediction", 10)


def test_endpoint_clamps_limit(monkeypatch):
    """Limit > 500 should be clamped to 500."""
    from web.backend import main as backend_main
    from fastapi.testclient import TestClient

    class _FakePersistence:
        def get_llm_decision_targets(self, decision_type=None, limit=50):
            self.kwargs = (decision_type, limit)
            return []

    class _FakeCoord:
        persistence = _FakePersistence()

    monkeypatch.setattr(backend_main, "coordinator", _FakeCoord())
    client = TestClient(backend_main.app)
    resp = client.get("/api/persistence/llm-decision-targets?limit=9999")
    assert resp.status_code == 200
    assert backend_main.coordinator.persistence.kwargs == (None, 500)


def test_endpoint_returns_503_when_no_coordinator(monkeypatch):
    from web.backend import main as backend_main
    from fastapi.testclient import TestClient

    monkeypatch.setattr(backend_main, "coordinator", None)
    client = TestClient(backend_main.app)
    resp = client.get("/api/persistence/llm-decision-targets")
    assert resp.status_code == 503
