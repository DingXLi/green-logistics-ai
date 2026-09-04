"""
iter #49: tests for /api/persistence/perturbation-history endpoint.

Covers:
1. Persistence.get_perturbation_history() — full list
2. include_inactive filter
3. since_sim_day filter
4. Sorted by start_sim_day DESC
5. duration_sim_days computed
6. Endpoint exposes the new method
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
        db_path = os.path.join(tmpdir, "test_pert_hist.db")
        p = Persistence(db_path)
        yield p


def _insert_perturbation(p, label, start, end, material="concrete",
                         multiplier=1.5, active=1):
    with p._conn() as conn:
        conn.execute(
            """INSERT INTO seasonal_perturbations
               (label, start_sim_day, end_sim_day, material_type, multiplier,
                active, created_at)
               VALUES (?, ?, ?, ?, ?, ?, '2026-09-04T00:00:00')""",
            (label, start, end, material, multiplier, active),
        )


# ============================================
# Persistence method tests
# ============================================


def test_empty_returns_empty(persistence):
    result = persistence.get_perturbation_history()
    assert result == []


def test_basic_listing(persistence):
    _insert_perturbation(persistence, "shock1", 5, 10)
    _insert_perturbation(persistence, "shock2", 15, 20)
    result = persistence.get_perturbation_history()
    assert len(result) == 2
    # Sorted by start_sim_day DESC
    assert result[0]["label"] == "shock2"
    assert result[1]["label"] == "shock1"
    # duration computed
    assert result[0]["duration_sim_days"] == 6  # 20 - 15 + 1
    assert result[1]["duration_sim_days"] == 6  # 10 - 5 + 1


def test_include_inactive_filter(persistence):
    _insert_perturbation(persistence, "active_shock", 5, 10, active=1)
    _insert_perturbation(persistence, "inactive_shock", 15, 20, active=0)
    result = persistence.get_perturbation_history(include_inactive=False)
    assert len(result) == 1
    assert result[0]["label"] == "active_shock"
    result_all = persistence.get_perturbation_history(include_inactive=True)
    assert len(result_all) == 2


def test_since_sim_day_filter(persistence):
    _insert_perturbation(persistence, "early", 1, 3)
    _insert_perturbation(persistence, "mid", 10, 12)
    _insert_perturbation(persistence, "late", 20, 25)
    result = persistence.get_perturbation_history(since_sim_day=10)
    assert len(result) == 2  # mid + late
    labels = [r["label"] for r in result]
    assert "late" in labels
    assert "mid" in labels
    assert "early" not in labels


def test_sorted_newest_first(persistence):
    _insert_perturbation(persistence, "old", 1, 5)
    _insert_perturbation(persistence, "newest", 30, 35)
    _insert_perturbation(persistence, "middle", 15, 20)
    result = persistence.get_perturbation_history()
    starts = [r["start_sim_day"] for r in result]
    assert starts == [30, 15, 1]


def test_duration_calculation(persistence):
    _insert_perturbation(persistence, "one_day", 5, 5)  # 1 day
    _insert_perturbation(persistence, "week", 5, 11)   # 7 days
    _insert_perturbation(persistence, "month", 0, 29)  # 30 days
    result = persistence.get_perturbation_history()
    by_label = {r["label"]: r["duration_sim_days"] for r in result}
    assert by_label["one_day"] == 1
    assert by_label["week"] == 7
    assert by_label["month"] == 30


def test_multiplier_field_preserved(persistence):
    _insert_perturbation(persistence, "boost", 5, 10, multiplier=2.5)
    _insert_perturbation(persistence, "suppress", 5, 10, multiplier=0.5)
    result = persistence.get_perturbation_history()
    by_label = {r["label"]: r["multiplier"] for r in result}
    assert by_label["boost"] == 2.5
    assert by_label["suppress"] == 0.5


# ============================================
# API endpoint tests
# ============================================


def test_endpoint_returns_200(monkeypatch):
    from web.backend import main as backend_main
    from fastapi.testclient import TestClient

    class _FakePersistence:
        def get_perturbation_history(self, include_inactive=True, since_sim_day=None):
            return [
                {"id": 1, "label": "shock1", "start_sim_day": 5, "end_sim_day": 10,
                 "material_type": "concrete", "multiplier": 1.5, "active": 1,
                 "created_at": "2026-09-04T00:00:00", "duration_sim_days": 6},
            ]

    class _FakeCoord:
        persistence = _FakePersistence()

    monkeypatch.setattr(backend_main, "coordinator", _FakeCoord())
    client = TestClient(backend_main.app)
    resp = client.get("/api/persistence/perturbation-history")
    assert resp.status_code == 200
    data = resp.json()
    assert data["n_total"] == 1
    assert data["n_active"] == 1
    assert data["perturbations"][0]["label"] == "shock1"


def test_endpoint_exclude_inactive(monkeypatch):
    from web.backend import main as backend_main
    from fastapi.testclient import TestClient

    class _FakePersistence:
        def get_perturbation_history(self, include_inactive=True, since_sim_day=None):
            self.kwargs = (include_inactive, since_sim_day)
            return []

    class _FakeCoord:
        persistence = _FakePersistence()

    monkeypatch.setattr(backend_main, "coordinator", _FakeCoord())
    client = TestClient(backend_main.app)
    resp = client.get("/api/persistence/perturbation-history?include_inactive=false")
    assert resp.status_code == 200
    assert backend_main.coordinator.persistence.kwargs == (False, None)


def test_endpoint_since_filter(monkeypatch):
    from web.backend import main as backend_main
    from fastapi.testclient import TestClient

    class _FakePersistence:
        def get_perturbation_history(self, include_inactive=True, since_sim_day=None):
            self.kwargs = (include_inactive, since_sim_day)
            return []

    class _FakeCoord:
        persistence = _FakePersistence()

    monkeypatch.setattr(backend_main, "coordinator", _FakeCoord())
    client = TestClient(backend_main.app)
    resp = client.get("/api/persistence/perturbation-history?since_sim_day=10")
    assert resp.status_code == 200
    assert backend_main.coordinator.persistence.kwargs == (True, 10)


def test_endpoint_rejects_negative_sim_day(monkeypatch):
    from web.backend import main as backend_main
    from fastapi.testclient import TestClient

    class _FakeCoord:
        persistence = type("P", (), {"get_perturbation_history": lambda self, **kw: []})()

    monkeypatch.setattr(backend_main, "coordinator", _FakeCoord())
    client = TestClient(backend_main.app)
    resp = client.get("/api/persistence/perturbation-history?since_sim_day=-5")
    assert resp.status_code == 400


def test_endpoint_returns_503_when_no_coordinator(monkeypatch):
    from web.backend import main as backend_main
    from fastapi.testclient import TestClient

    monkeypatch.setattr(backend_main, "coordinator", None)
    client = TestClient(backend_main.app)
    resp = client.get("/api/persistence/perturbation-history")
    assert resp.status_code == 503
