"""
iter #68: solver-duration-trend tests.

Tests the persistence method ``get_solver_duration_trend`` and the
``/api/persistence/solver-duration-trend`` endpoint.

Groups cycles into chronological windows of N (default 5) cycles and
computes p50 / p95 / mean / min / max / stddev wall_duration_ms per window,
plus an overall trend (improving / declining / stable / unknown) by
comparing first-half vs second-half median p50.
"""
from __future__ import annotations

import os
import tempfile

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _insert_cycle(p, cycle_id, sim_day, *, dur_ms, status="OPTIMAL"):
    """Insert a single cycle row with the given wall_duration_ms."""
    with p._conn() as conn:
        conn.execute(
            """INSERT INTO optimization_cycles
               (cycle_id, sim_day, sim_hour, wall_timestamp,
                activity_factor, n_supply_offers, n_demand_requests, n_matches,
                total_tons, total_cost_sek, total_co2_kg, total_distance_km,
                n_vehicles_used, n_vehicles_available, fleet_utilization_pct,
                solver_status, wall_duration_ms)
               VALUES (?, ?, 0, '2026-09-12T00:00:00',
                       1.0, 1, 1, 1, 5.0, 100.0, 50.0, 10.0,
                       1, 1, 50, ?, ?)""",
            (cycle_id, sim_day, status, dur_ms),
        )


# ---------------------------------------------------------------------------
# Persistence tests
# ---------------------------------------------------------------------------


def test_empty_db_returns_consistent_shape(persistence):
    """No cycles -> empty windows + unknown trend."""
    result = persistence.get_solver_duration_trend()
    assert result["n_windows"] == 0
    assert result["n_cycles_evaluated"] == 0
    assert result["windows"] == []
    assert result["trend"] == "unknown"
    assert result["trend_delta_pct"] is None
    assert result["trend_confidence"] == 0.0
    assert result["first_window_p50_ms"] is None
    assert result["last_window_p50_ms"] is None
    assert result["window_size"] == 5
    assert result["since_sim_day"] is None
    assert result["until_sim_day"] is None


def test_window_size_clamped_to_min_2(persistence):
    """window_size=0/1 should be clamped to 2."""
    for i in range(4):
        _insert_cycle(persistence, f"C{i}", sim_day=i + 1, dur_ms=100 + i * 10)
    result = persistence.get_solver_duration_trend(window_size=1)
    assert result["window_size"] == 2  # clamped
    # 4 cycles / 2 per window = 2 windows
    assert result["n_windows"] == 2
    assert result["n_cycles_evaluated"] == 4


def test_window_size_clamped_to_max_30(persistence):
    """window_size=100 should be clamped to 30."""
    for i in range(3):
        _insert_cycle(persistence, f"C{i}", sim_day=i + 1, dur_ms=100)
    result = persistence.get_solver_duration_trend(window_size=100)
    assert result["window_size"] == 30
    # 3 cycles / 30 per window = 1 window (partial)
    assert result["n_windows"] == 1
    assert result["n_cycles_evaluated"] == 3


def test_chronological_window_order(persistence):
    """Cycles should be grouped in id-insert order."""
    # Insert 10 cycles with durations 1000, 200, ..., 100 (decreasing)
    for i in range(10):
        dur_ms = 1100 - i * 100
        _insert_cycle(persistence, f"OPT{i:04d}", sim_day=i + 1, dur_ms=dur_ms)
    result = persistence.get_solver_duration_trend(window_size=3)
    assert result["n_windows"] == 4  # 10/3 = 3 full + 1 partial
    assert result["n_cycles_evaluated"] == 10
    # First window: cycles OPT0000-OPT0002 (durations 1100, 1000, 900)
    w0 = result["windows"][0]
    assert w0["start_cycle_id"] == "OPT0000"
    assert w0["end_cycle_id"] == "OPT0002"
    assert w0["n_cycles"] == 3
    assert w0["p50_ms"] == 1000.0  # median of [1100, 1000, 900]
    assert w0["min_ms"] == 900.0
    assert w0["max_ms"] == 1100.0
    assert w0["mean_ms"] == 1000.0


def test_partial_last_window(persistence):
    """Last window may have fewer cycles than window_size."""
    for i in range(7):
        _insert_cycle(persistence, f"C{i}", sim_day=i + 1, dur_ms=100)
    result = persistence.get_solver_duration_trend(window_size=3)
    # 7/3 = 2 full + 1 partial (1 cycle)
    assert result["n_windows"] == 3
    assert result["windows"][2]["n_cycles"] == 1


def test_trend_improving(persistence):
    """p50 decreases over time → improving (solver got faster)."""
    # First half (5 cycles): durations 1000-1400
    # Second half (5 cycles): durations 100-500
    for i in range(5):
        _insert_cycle(persistence, f"S{i}", sim_day=i + 1, dur_ms=1000 + i * 100)
    for i in range(5):
        _insert_cycle(persistence, f"L{i}", sim_day=10 + i, dur_ms=100 + i * 100)
    result = persistence.get_solver_duration_trend(window_size=5)
    assert result["trend"] == "improving"
    assert result["trend_delta_pct"] is not None
    assert result["trend_delta_pct"] < -10.0  # significant decrease
    assert result["first_window_p50_ms"] == 1200.0
    assert result["last_window_p50_ms"] == 300.0


def test_trend_declining(persistence):
    """p50 increases over time → declining (solver got slower)."""
    for i in range(5):
        _insert_cycle(persistence, f"F{i}", sim_day=i + 1, dur_ms=100 + i * 10)
    for i in range(5):
        _insert_cycle(persistence, f"S{i}", sim_day=10 + i, dur_ms=1000 + i * 100)
    result = persistence.get_solver_duration_trend(window_size=5)
    assert result["trend"] == "declining"
    assert result["trend_delta_pct"] is not None
    assert result["trend_delta_pct"] > 10.0


def test_trend_stable(persistence):
    """p50 stays roughly constant → stable."""
    for i in range(10):
        # Tiny variation around 500ms
        dur = 500 + (i % 2) * 5  # 500, 505, 500, 505, ...
        _insert_cycle(persistence, f"C{i}", sim_day=i + 1, dur_ms=dur)
    result = persistence.get_solver_duration_trend(window_size=5)
    # delta_pct = ~1%, well within ±10%
    assert result["trend"] == "stable"


def test_trend_unknown_for_single_window(persistence):
    """Need ≥ 2 windows for trend."""
    for i in range(3):
        _insert_cycle(persistence, f"C{i}", sim_day=i + 1, dur_ms=100)
    result = persistence.get_solver_duration_trend(window_size=10)
    assert result["n_windows"] == 1
    assert result["trend"] == "unknown"


def test_p95_calculation(persistence):
    """Verify p95 calculation per window."""
    # 10 cycles with durations 100, 200, ..., 1000
    for i in range(10):
        _insert_cycle(persistence, f"P{i}", sim_day=i + 1, dur_ms=(i + 1) * 100)
    result = persistence.get_solver_duration_trend(window_size=10)
    assert result["n_windows"] == 1
    w0 = result["windows"][0]
    # p95 of [100, 200, ..., 1000] using linear interpolation:
    # idx = 0.95 * 9 = 8.55, lo=8 (val=900), hi=9 (val=1000)
    # 900 * 0.45 + 1000 * 0.55 = 405 + 550 = 955
    assert 950.0 <= w0["p95_ms"] <= 960.0


def test_sim_day_window_filter(persistence):
    """since/until_sim_day filters cycles before bucketing."""
    for i in range(10):
        _insert_cycle(persistence, f"C{i}", sim_day=i + 1, dur_ms=100 * (i + 1))
    # Only include cycles with sim_day 4-6
    result = persistence.get_solver_duration_trend(
        window_size=5, since_sim_day=4, until_sim_day=6,
    )
    assert result["n_cycles_evaluated"] == 3
    assert result["since_sim_day"] == 4
    assert result["until_sim_day"] == 6
    assert result["n_windows"] == 1
    w0 = result["windows"][0]
    assert w0["start_sim_day"] == 4
    assert w0["end_sim_day"] == 6


def test_null_wall_duration_excluded(persistence):
    """Cycles with wall_duration_ms IS NULL should be excluded."""
    # Insert 2 cycles with durations
    _insert_cycle(persistence, "C1", sim_day=1, dur_ms=100)
    _insert_cycle(persistence, "C2", sim_day=2, dur_ms=200)
    # Insert one cycle with NULL wall_duration_ms directly
    with persistence._conn() as conn:
        conn.execute(
            """INSERT INTO optimization_cycles
               (cycle_id, sim_day, sim_hour, wall_timestamp,
                activity_factor, n_supply_offers, n_demand_requests, n_matches,
                total_tons, total_cost_sek, total_co2_kg, total_distance_km,
                n_vehicles_used, n_vehicles_available, fleet_utilization_pct,
                solver_status, wall_duration_ms)
               VALUES (?, ?, 0, '2026-09-12T00:00:00',
                       1.0, 1, 1, 1, 5.0, 100.0, 50.0, 10.0,
                       1, 1, 50, 'OPTIMAL', NULL)""",
            ("NULL_C", 3),
        )
    result = persistence.get_solver_duration_trend()
    # NULL_C should be excluded
    assert result["n_cycles_evaluated"] == 2


def test_trend_confidence_scales_with_n_windows(persistence):
    """trend_confidence should grow as more windows are available."""
    # 25 cycles, window_size=5 → 5 windows, confidence should hit 1.0 (capped)
    for i in range(25):
        _insert_cycle(persistence, f"C{i}", sim_day=i + 1, dur_ms=100)
    result = persistence.get_solver_duration_trend(window_size=5)
    assert result["n_windows"] == 5
    assert result["trend_confidence"] == 1.0  # min(1.0, 5/5) = 1.0


# ---------------------------------------------------------------------------
# Endpoint tests
# ---------------------------------------------------------------------------


def test_endpoint_returns_200(monkeypatch):
    """Endpoint returns 200 + envelope."""
    from web.backend import main as backend_main
    from fastapi.testclient import TestClient

    class _FakePersistence:
        def get_solver_duration_trend(self, **kwargs):
            return {
                "n_windows": 0,
                "window_size": kwargs.get("window_size", 5),
                "n_cycles_evaluated": 0,
                "since_sim_day": kwargs.get("since_sim_day"),
                "until_sim_day": kwargs.get("until_sim_day"),
                "trend": "unknown",
                "trend_delta_pct": None,
                "trend_confidence": 0.0,
                "windows": [],
                "first_window_p50_ms": None,
                "last_window_p50_ms": None,
            }

    class _FakeCoord:
        persistence = _FakePersistence()

    monkeypatch.setattr(backend_main, "coordinator", _FakeCoord())
    client = TestClient(backend_main.app)
    resp = client.get("/api/persistence/solver-duration-trend")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    for key in [
        "n_windows", "window_size", "n_cycles_evaluated",
        "since_sim_day", "until_sim_day",
        "trend", "trend_delta_pct", "trend_confidence",
        "windows", "first_window_p50_ms", "last_window_p50_ms",
    ]:
        assert key in data, f"missing key: {key}"


def test_endpoint_with_query_params(monkeypatch):
    """Endpoint forwards window_size + since_sim_day + until_sim_day."""
    from web.backend import main as backend_main
    from fastapi.testclient import TestClient

    received_kwargs: dict = {}

    class _FakePersistence:
        def get_solver_duration_trend(self, **kwargs):
            received_kwargs.update(kwargs)
            return {
                "n_windows": 0,
                "window_size": kwargs.get("window_size", 5),
                "n_cycles_evaluated": 0,
                "since_sim_day": kwargs.get("since_sim_day"),
                "until_sim_day": kwargs.get("until_sim_day"),
                "trend": "unknown",
                "trend_delta_pct": None,
                "trend_confidence": 0.0,
                "windows": [],
                "first_window_p50_ms": None,
                "last_window_p50_ms": None,
            }

    class _FakeCoord:
        persistence = _FakePersistence()

    monkeypatch.setattr(backend_main, "coordinator", _FakeCoord())
    client = TestClient(backend_main.app)
    resp = client.get(
        "/api/persistence/solver-duration-trend"
        "?window_size=7&since_sim_day=3&until_sim_day=20"
    )
    assert resp.status_code == 200
    assert received_kwargs["window_size"] == 7
    assert received_kwargs["since_sim_day"] == 3
    assert received_kwargs["until_sim_day"] == 20


def test_endpoint_400_when_inverted_window(monkeypatch):
    """since > until -> 400."""
    from web.backend import main as backend_main
    from fastapi.testclient import TestClient

    class _FakeCoord:
        class _FakePersistence:
            def get_solver_duration_trend(self, **kwargs):
                return {}

        persistence = _FakePersistence()

    monkeypatch.setattr(backend_main, "coordinator", _FakeCoord())
    client = TestClient(backend_main.app)
    resp = client.get(
        "/api/persistence/solver-duration-trend?since_sim_day=10&until_sim_day=5"
    )
    assert resp.status_code == 400


def test_endpoint_503_when_no_persistence(monkeypatch):
    """No coordinator.persistence → 503."""
    from web.backend import main as backend_main
    from fastapi.testclient import TestClient

    class _FakeCoord:
        persistence = None

    monkeypatch.setattr(backend_main, "coordinator", _FakeCoord())
    client = TestClient(backend_main.app)
    resp = client.get("/api/persistence/solver-duration-trend")
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def persistence():
    from agents.persistence import Persistence
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_solver_trend.db")
        p = Persistence(db_path)
        yield p
