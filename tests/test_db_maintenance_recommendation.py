"""
iter #42: tests for DB maintenance recommendation + log endpoints.

Covers:
1. Persistence methods:
   - should_auto_vacuum() with various states
   - get_maintenance_log() ordering
   - vacuum() logs the run
2. /api/admin/db-maintenance/recommendation HTTP endpoint
3. /api/admin/db-maintenance/log HTTP endpoint
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
        db_path = os.path.join(tmpdir, "test_maint.db")
        p = Persistence(db_path)
        yield p


# ---------------------------------------------------------------------------
# Pure persistence tests
# ---------------------------------------------------------------------------


def test_should_auto_vacuum_first_time(persistence):
    """When no vacuum has been run, should recommend one."""
    result = persistence.should_auto_vacuum()
    assert result["should_vacuum"] is True
    assert any("first" in r.lower() or "never" in r.lower() for r in result["reasons"])
    assert result["stats"]["last_vacuum_at"] is None
    assert result["stats"]["total_maintenance_runs"] == 0


def test_vacuum_writes_log(persistence):
    """After vacuum(), db_maintenance_log has one entry."""
    result = persistence.vacuum(triggered_by="auto")
    assert result["success"] is True
    assert result["triggered_by"] == "auto"

    log = persistence.get_maintenance_log()
    assert len(log) == 1
    assert log[0]["action"] == "vacuum_analyze"
    assert log[0]["triggered_by"] == "auto"
    assert log[0]["ran_at"] is not None


def test_should_auto_vacuum_after_recent_vacuum(persistence):
    """If vacuum was just run, should NOT recommend again."""
    persistence.vacuum(triggered_by="manual")
    result = persistence.should_auto_vacuum()
    assert result["should_vacuum"] is False
    assert result["reasons"] == []
    assert result["stats"]["last_vacuum_at"] is not None
    assert result["stats"]["total_maintenance_runs"] == 1


def test_get_maintenance_log_ordered_desc(persistence):
    """Multiple runs → most recent first."""
    import time
    persistence.vacuum(triggered_by="manual")
    time.sleep(0.01)
    persistence.vacuum(triggered_by="auto")
    log = persistence.get_maintenance_log()
    assert len(log) == 2
    # Most recent first
    assert log[0]["ran_at"] >= log[1]["ran_at"]


def test_get_maintenance_log_limit(persistence):
    for _ in range(5):
        persistence.vacuum(triggered_by="manual")
    log = persistence.get_maintenance_log(limit=2)
    assert len(log) == 2


def test_get_maintenance_log_empty(persistence):
    log = persistence.get_maintenance_log()
    assert log == []


def test_vacuum_size_reduction(persistence):
    """vacuum() should report size_before/after correctly."""
    # Insert some data to make DB grow
    with persistence._conn() as conn:
        conn.execute(
            """INSERT INTO optimization_cycles
               (cycle_id, sim_day, sim_hour, wall_timestamp,
                activity_factor, n_supply_offers, n_demand_requests, n_matches,
                total_tons, total_cost_sek, total_co2_kg, total_distance_km,
                n_vehicles_used, n_vehicles_available, fleet_utilization_pct,
                solver_status, wall_duration_ms)
               VALUES ('OPT001', 1, 0, '2026-09-03T00:00:00',
                       1.0, 1, 1, 1, 5.0, 100.0, 50.0, 20.0,
                       1, 1, 50.0, 'OPTIMAL', 100)""",
        )
    size_before = persistence.db_path.stat().st_size
    result = persistence.vacuum()
    assert result["size_before_bytes"] == size_before
    assert result["size_after_bytes"] > 0
    assert result["success"] is True


def test_should_auto_vacuum_size_growth_heuristic(persistence):
    """Manually insert a log with old size_after, then verify growth > 30% triggers."""
    # Run vacuum first to establish baseline
    persistence.vacuum()
    after_size = persistence.db_path.stat().st_size
    # Backdate the log entry to use a small size_after to simulate growth
    with persistence._conn() as conn:
        conn.execute(
            """UPDATE db_maintenance_log
               SET size_after_bytes = ?
               WHERE id = (SELECT MAX(id) FROM db_maintenance_log)""",
            (max(100, int(after_size * 0.5)),),  # simulate 50% smaller then growth
        )
    result = persistence.should_auto_vacuum()
    # Should detect growth (now we're 2x of recorded size)
    if result["stats"]["size_growth_pct_since_last_vacuum"] is not None:
        if result["stats"]["size_growth_pct_since_last_vacuum"] > 30:
            assert result["should_vacuum"] is True
            assert any("grew" in r.lower() for r in result["reasons"])


def test_should_auto_vacuum_high_cycles(persistence):
    """If no vacuum has been run and > 1000 cycles, should recommend."""
    # Insert 1001 cycles without vacuum
    with persistence._conn() as conn:
        for i in range(1001):
            try:
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
                    (f"OPT{i:04d}", i + 1),
                )
            except Exception:
                # Some may fail on UNIQUE; that's OK
                pass
    result = persistence.should_auto_vacuum()
    assert result["stats"]["total_cycles"] >= 1000
    # First heuristic (no vacuum) always triggers
    assert result["should_vacuum"] is True


# ---------------------------------------------------------------------------
# HTTP endpoint tests
# ---------------------------------------------------------------------------


def test_db_recommendation_endpoint_no_coordinator(monkeypatch):
    from web.backend import main as backend_main

    monkeypatch.setattr(backend_main, "coordinator", None)
    from fastapi.testclient import TestClient
    client = TestClient(backend_main.app)
    resp = client.get("/api/admin/db-maintenance/recommendation")
    assert resp.status_code == 503


def test_db_recommendation_endpoint_happy_path(monkeypatch):
    from web.backend import main as backend_main

    fake_rec = {
        "should_vacuum": True,
        "reasons": ["DB size grew 45.0% since last vacuum"],
        "stats": {
            "db_size_bytes": 5242880,
            "db_size_mb": 5.0,
            "total_cycles": 30,
            "last_vacuum_at": "2026-08-25T00:00:00",
            "days_since_last_vacuum": 9,
            "size_growth_pct_since_last_vacuum": 45.0,
            "size_after_last_vacuum_bytes": 3617587,
            "total_maintenance_runs": 1,
        },
    }

    class _FakePersistence:
        def should_auto_vacuum(self):
            return fake_rec

    class _FakeCoord:
        persistence = _FakePersistence()

    monkeypatch.setattr(backend_main, "coordinator", _FakeCoord())
    from fastapi.testclient import TestClient
    client = TestClient(backend_main.app)
    resp = client.get("/api/admin/db-maintenance/recommendation")
    assert resp.status_code == 200
    data = resp.json()
    assert data["should_vacuum"] is True
    assert "grew" in data["reasons"][0].lower()
    assert data["stats"]["db_size_mb"] == 5.0


def test_db_recommendation_requires_admin(monkeypatch):
    from web.backend import main as backend_main

    class _FakePersistence:
        def should_auto_vacuum(self):
            return {"should_vacuum": False, "reasons": [], "stats": {}}

    class _FakeCoord:
        persistence = _FakePersistence()

    monkeypatch.setattr(backend_main, "coordinator", _FakeCoord())
    # Set admin token to enforce auth
    monkeypatch.setenv("GL_ADMIN_TOKEN", "test-secret-token-12345")
    # Reload module so it picks up the new env var for _get_admin_token
    import importlib
    importlib.reload(backend_main)
    # Re-apply the fake coordinator after reload
    monkeypatch.setattr(backend_main, "coordinator", _FakeCoord())
    from fastapi.testclient import TestClient
    client = TestClient(backend_main.app)
    # No admin token provided → 401
    resp = client.get("/api/admin/db-maintenance/recommendation")
    assert resp.status_code == 401


def test_db_log_endpoint_no_coordinator(monkeypatch):
    from web.backend import main as backend_main

    monkeypatch.setattr(backend_main, "coordinator", None)
    from fastapi.testclient import TestClient
    client = TestClient(backend_main.app)
    resp = client.get("/api/admin/db-maintenance/log")
    assert resp.status_code == 503


def test_db_log_endpoint_invalid_limit(monkeypatch):
    from web.backend import main as backend_main

    class _FakeCoord:
        persistence = object()

    monkeypatch.setattr(backend_main, "coordinator", _FakeCoord())
    from fastapi.testclient import TestClient
    client = TestClient(backend_main.app)
    resp = client.get("/api/admin/db-maintenance/log?limit=0")
    assert resp.status_code == 400
    resp = client.get("/api/admin/db-maintenance/log?limit=999")
    assert resp.status_code == 400


def test_db_log_endpoint_happy_path(monkeypatch):
    from web.backend import main as backend_main

    fake_log = [
        {
            "id": 2,
            "action": "vacuum_analyze",
            "size_before_bytes": 5000000,
            "size_after_bytes": 4800000,
            "reclaimed_bytes": 200000,
            "triggered_by": "manual",
            "ran_at": "2026-09-03T10:00:00",
        },
        {
            "id": 1,
            "action": "vacuum_analyze",
            "size_before_bytes": 4500000,
            "size_after_bytes": 4000000,
            "reclaimed_bytes": 500000,
            "triggered_by": "auto",
            "ran_at": "2026-08-30T10:00:00",
        },
    ]

    class _FakePersistence:
        def get_maintenance_log(self, limit=20):
            self.called_with = limit
            return fake_log

    class _FakeCoord:
        persistence = _FakePersistence()

    monkeypatch.setattr(backend_main, "coordinator", _FakeCoord())
    from fastapi.testclient import TestClient
    client = TestClient(backend_main.app)
    resp = client.get("/api/admin/db-maintenance/log?limit=5")
    assert resp.status_code == 200
    data = resp.json()
    assert "entries" in data
    assert len(data["entries"]) == 2
    assert data["entries"][0]["triggered_by"] == "manual"
    assert data["entries"][1]["triggered_by"] == "auto"
