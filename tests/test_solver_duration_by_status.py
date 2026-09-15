"""
iter #71: solver-duration-by-status tests.

Tests the persistence method ``get_solver_duration_by_status`` and the
``/api/persistence/solver-duration-by-status`` endpoint.

Groups cycles into 4 solver-status buckets (OPTIMAL / FEASIBLE /
INFEASIBLE / UNKNOWN) based on ``solver_status`` column and computes
per-status wall_duration_ms p50/p95/mean/min/max/stddev + cost stats +
mean n_matches + mean tons. Also computes rate percentages
(infeasible_rate_pct, feasible_rate_pct, optimal_rate_pct) and global
stats + slowest/fastest status comparison.
"""
from __future__ import annotations

import os
import tempfile

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _insert_cycle(p, cycle_id, sim_day, *, dur_ms, status="OPTIMAL",
                   cost_sek=100.0, tons=5.0, n_matches=1):
    """Insert a single cycle row with wall_duration_ms + status + cost/tons."""
    with p._conn() as conn:
        conn.execute(
            """INSERT INTO optimization_cycles
               (cycle_id, sim_day, sim_hour, wall_timestamp,
                activity_factor, n_supply_offers, n_demand_requests, n_matches,
                total_tons, total_cost_sek, total_co2_kg, total_distance_km,
                n_vehicles_used, n_vehicles_available, fleet_utilization_pct,
                solver_status, wall_duration_ms, seasonal_month)
               VALUES (?, ?, 0, '2026-09-15T00:00:00',
                       1.0, 1, 1, ?, ?, ?, 50.0, 10.0,
                       1, 1, 50, ?, ?, 1)""",
            (cycle_id, sim_day, n_matches, tons, cost_sek, status, dur_ms),
        )


# ---------------------------------------------------------------------------
# Persistence tests
# ---------------------------------------------------------------------------


def test_empty_db_returns_consistent_shape(persistence):
    """No cycles -> all 4 statuses empty + global null + slowest/fastest None."""
    result = persistence.get_solver_duration_by_status()
    assert result["n_cycles_evaluated"] == 0
    assert result["n_statuses_with_data"] == 0
    assert result["since_sim_day"] is None
    assert result["until_sim_day"] is None
    assert len(result["statuses"]) == 4
    assert result["global_stats"]["n_cycles"] == 0
    assert result["global_stats"]["p50_ms"] is None
    assert result["slowest_status"] is None
    assert result["fastest_status"] is None
    assert result["slowest_p50_ms"] is None
    assert result["fastest_p50_ms"] is None
    assert result["slowest_vs_fastest_pct"] is None
    assert result["infeasible_rate_pct"] == 0.0
    assert result["feasible_rate_pct"] == 0.0
    assert result["optimal_rate_pct"] == 0.0


def test_status_assignment_correctness(persistence):
    """solver_status -> bucket mapping is correct (case-insensitive)."""
    _insert_cycle(persistence, "O1", sim_day=1, dur_ms=100, status="OPTIMAL")
    _insert_cycle(persistence, "O2", sim_day=2, dur_ms=200, status="optimal")
    _insert_cycle(persistence, "F1", sim_day=3, dur_ms=300, status="FEASIBLE")
    _insert_cycle(persistence, "I1", sim_day=4, dur_ms=400, status="INFEASIBLE")
    _insert_cycle(persistence, "U1", sim_day=5, dur_ms=500, status=None)
    _insert_cycle(persistence, "X1", sim_day=6, dur_ms=600, status="UNKNOWN")

    result = persistence.get_solver_duration_by_status()
    by_status = {s["status"]: s for s in result["statuses"]}
    # Case-insensitive: both 'OPTIMAL' and 'optimal' land in OPTIMAL bucket
    assert by_status["OPTIMAL"]["n_cycles"] == 2
    assert by_status["FEASIBLE"]["n_cycles"] == 1
    assert by_status["INFEASIBLE"]["n_cycles"] == 1
    # UNKNOWN: both null status and explicit UNKNOWN land here
    assert by_status["UNKNOWN"]["n_cycles"] == 2


def test_pct_of_total_computes_correctly(persistence):
    """pct_of_total for each status = n_status / n_total * 100."""
    # 4 OPTIMAL + 2 FEASIBLE + 1 INFEASIBLE = 7 total
    for i in range(4):
        _insert_cycle(persistence, f"O{i}", sim_day=i + 1, dur_ms=100,
                       status="OPTIMAL")
    _insert_cycle(persistence, "F0", sim_day=5, dur_ms=200, status="FEASIBLE")
    _insert_cycle(persistence, "F1", sim_day=6, dur_ms=200, status="FEASIBLE")
    _insert_cycle(persistence, "I0", sim_day=7, dur_ms=200, status="INFEASIBLE")

    result = persistence.get_solver_duration_by_status()
    by_status = {s["status"]: s for s in result["statuses"]}
    assert by_status["OPTIMAL"]["n_cycles"] == 4
    assert by_status["OPTIMAL"]["pct_of_total"] == 57.14  # 4/7 = 57.142857...
    assert by_status["FEASIBLE"]["pct_of_total"] == 28.57
    assert by_status["INFEASIBLE"]["pct_of_total"] == 14.29
    assert by_status["UNKNOWN"]["pct_of_total"] == 0.0


def test_rate_pct_for_statuses(persistence):
    """optimal_rate_pct / feasible_rate_pct / infeasible_rate_pct are computed correctly."""
    _insert_cycle(persistence, "O1", sim_day=1, dur_ms=100, status="OPTIMAL")
    _insert_cycle(persistence, "O2", sim_day=2, dur_ms=100, status="OPTIMAL")
    _insert_cycle(persistence, "F1", sim_day=3, dur_ms=100, status="FEASIBLE")
    _insert_cycle(persistence, "I1", sim_day=4, dur_ms=100, status="INFEASIBLE")

    result = persistence.get_solver_duration_by_status()
    assert result["optimal_rate_pct"] == 50.0
    assert result["feasible_rate_pct"] == 25.0
    assert result["infeasible_rate_pct"] == 25.0


def test_p50_p95_for_each_status(persistence):
    """Per-status p50/p95 reflect that status's data only."""
    _insert_cycle(persistence, "O1", sim_day=1, dur_ms=100, status="OPTIMAL")
    _insert_cycle(persistence, "O2", sim_day=2, dur_ms=200, status="OPTIMAL")
    _insert_cycle(persistence, "O3", sim_day=3, dur_ms=300, status="OPTIMAL")
    _insert_cycle(persistence, "F1", sim_day=4, dur_ms=500, status="FEASIBLE")
    _insert_cycle(persistence, "F2", sim_day=5, dur_ms=1000, status="FEASIBLE")

    result = persistence.get_solver_duration_by_status()
    by_status = {s["status"]: s for s in result["statuses"]}
    # OPTIMAL p50 = 200 (middle of [100, 200, 300])
    assert by_status["OPTIMAL"]["p50_ms"] == 200.0
    # OPTIMAL p95 with linear interpolation: idx = 0.95 * 2 = 1.9
    # = 200 * 0.1 + 300 * 0.9 = 290.0
    assert by_status["OPTIMAL"]["p95_ms"] == 290.0
    assert by_status["OPTIMAL"]["mean_ms"] == 200.0
    # FEASIBLE p50 = 750 (middle of [500, 1000])
    assert by_status["FEASIBLE"]["p50_ms"] == 750.0
    # FEASIBLE p95 with linear interpolation: idx = 0.95 * 1 = 0.95
    # = 500 * 0.05 + 1000 * 0.95 = 975.0
    assert by_status["FEASIBLE"]["p95_ms"] == 975.0
    assert by_status["FEASIBLE"]["mean_ms"] == 750.0


def test_slowest_fastest_status_by_p50(persistence):
    """slowest_status / fastest_status picked by max/min p50 across non-empty buckets."""
    _insert_cycle(persistence, "O1", sim_day=1, dur_ms=100, status="OPTIMAL")
    _insert_cycle(persistence, "F1", sim_day=2, dur_ms=500, status="FEASIBLE")
    _insert_cycle(persistence, "I1", sim_day=3, dur_ms=900, status="INFEASIBLE")

    result = persistence.get_solver_duration_by_status()
    assert result["slowest_status"] == "INFEASIBLE"
    assert result["slowest_p50_ms"] == 900.0
    assert result["fastest_status"] == "OPTIMAL"
    assert result["fastest_p50_ms"] == 100.0
    # (900-100)/100 * 100 = 800%
    assert result["slowest_vs_fastest_pct"] == 800.0


def test_slowest_fastest_with_only_one_status(persistence):
    """Only one non-empty status -> slowest == fastest (tied)."""
    _insert_cycle(persistence, "O1", sim_day=1, dur_ms=200, status="OPTIMAL")

    result = persistence.get_solver_duration_by_status()
    assert result["slowest_status"] == "OPTIMAL"
    assert result["fastest_status"] == "OPTIMAL"
    # Tied: delta == 0
    assert result["slowest_vs_fastest_pct"] == 0.0


def test_sim_day_filter(persistence):
    """since_sim_day / until_sim_day restrict to cycles in window."""
    _insert_cycle(persistence, "O1", sim_day=1, dur_ms=100, status="OPTIMAL")
    _insert_cycle(persistence, "O2", sim_day=5, dur_ms=200, status="OPTIMAL")
    _insert_cycle(persistence, "O3", sim_day=20, dur_ms=300, status="OPTIMAL")
    _insert_cycle(persistence, "F1", sim_day=50, dur_ms=500, status="FEASIBLE")

    result = persistence.get_solver_duration_by_status(
        since_sim_day=3, until_sim_day=25,
    )
    # Only sim_day=5 and sim_day=20 are in window (2 OPTIMAL)
    assert result["n_cycles_evaluated"] == 2
    by_status = {s["status"]: s for s in result["statuses"]}
    assert by_status["OPTIMAL"]["n_cycles"] == 2
    assert by_status["FEASIBLE"]["n_cycles"] == 0


def test_sim_day_filter_no_match(persistence):
    """since_sim_day > until_sim_day returns empty without error."""
    _insert_cycle(persistence, "A", sim_day=1, dur_ms=100, status="OPTIMAL")
    result = persistence.get_solver_duration_by_status(
        since_sim_day=100, until_sim_day=10,
    )
    assert result["n_cycles_evaluated"] == 0


def test_cost_stats_per_status(persistence):
    """mean_cost_sek / mean_cost_per_ton_sek computed for each status bucket."""
    _insert_cycle(persistence, "O1", sim_day=1, dur_ms=100, status="OPTIMAL",
                   cost_sek=100.0, tons=10.0)
    _insert_cycle(persistence, "O2", sim_day=2, dur_ms=100, status="OPTIMAL",
                   cost_sek=200.0, tons=10.0)
    _insert_cycle(persistence, "F1", sim_day=3, dur_ms=200, status="FEASIBLE",
                   cost_sek=500.0, tons=10.0)

    result = persistence.get_solver_duration_by_status()
    by_status = {s["status"]: s for s in result["statuses"]}
    # OPTIMAL: mean cost = 150, mean tons = 10, cost/ton = 15
    assert by_status["OPTIMAL"]["mean_cost_sek"] == 150.0
    assert by_status["OPTIMAL"]["mean_tons"] == 10.0
    assert by_status["OPTIMAL"]["mean_cost_per_ton_sek"] == 15.0
    # FEASIBLE: cost = 500, tons = 10, cost/ton = 50
    assert by_status["FEASIBLE"]["mean_cost_sek"] == 500.0
    assert by_status["FEASIBLE"]["mean_cost_per_ton_sek"] == 50.0


def test_n_matches_mean(persistence):
    """mean_n_matches reflects average match count per status."""
    _insert_cycle(persistence, "O1", sim_day=1, dur_ms=100, status="OPTIMAL",
                   n_matches=5)
    _insert_cycle(persistence, "O2", sim_day=2, dur_ms=100, status="OPTIMAL",
                   n_matches=7)
    _insert_cycle(persistence, "O3", sim_day=3, dur_ms=100, status="OPTIMAL",
                   n_matches=9)
    _insert_cycle(persistence, "F1", sim_day=4, dur_ms=200, status="FEASIBLE",
                   n_matches=3)

    result = persistence.get_solver_duration_by_status()
    by_status = {s["status"]: s for s in result["statuses"]}
    # OPTIMAL mean = (5+7+9)/3 = 7.0
    assert by_status["OPTIMAL"]["mean_n_matches"] == 7.0
    assert by_status["FEASIBLE"]["mean_n_matches"] == 3.0


def test_caching_returns_same_dict(persistence):
    """Second call within cache TTL returns identical dict (cached)."""
    _insert_cycle(persistence, "A", sim_day=1, dur_ms=100, status="OPTIMAL")
    r1 = persistence.get_solver_duration_by_status()
    r2 = persistence.get_solver_duration_by_status()
    assert r1 is r2  # exact same object (cache hit)


def test_p50_with_single_value(persistence):
    """Edge case: 1 cycle in status -> p50 == that single value."""
    _insert_cycle(persistence, "A", sim_day=1, dur_ms=42.5, status="OPTIMAL")
    result = persistence.get_solver_duration_by_status()
    opt = next(s for s in result["statuses"] if s["status"] == "OPTIMAL")
    assert opt["n_cycles"] == 1
    assert opt["p50_ms"] == 42.5
    assert opt["p95_ms"] == 42.5
    assert opt["min_ms"] == 42.5
    assert opt["max_ms"] == 42.5
    assert opt["mean_ms"] == 42.5
    assert opt["stddev_ms"] == 0.0


def test_mean_cost_per_ton_when_tons_zero(persistence):
    """When mean_tons == 0, mean_cost_per_ton_sek should be None (avoid div-by-zero)."""
    _insert_cycle(persistence, "O1", sim_day=1, dur_ms=100, status="OPTIMAL",
                   cost_sek=100.0, tons=0.0)
    result = persistence.get_solver_duration_by_status()
    opt = next(s for s in result["statuses"] if s["status"] == "OPTIMAL")
    assert opt["mean_cost_sek"] == 100.0
    assert opt["mean_tons"] == 0.0
    assert opt["mean_cost_per_ton_sek"] is None


def test_global_stats_match_overall(persistence):
    """global_stats reflect all cycles regardless of status."""
    durations_by_status = {
        "OPTIMAL": [100, 200, 300],
        "FEASIBLE": [400, 500],
        "INFEASIBLE": [600],
    }
    sim_day = 1
    for status, durs in durations_by_status.items():
        for d in durs:
            _insert_cycle(persistence, f"{status}_{d}", sim_day=sim_day,
                           dur_ms=d, status=status)
            sim_day += 1
    result = persistence.get_solver_duration_by_status()
    # 6 cycles total, durations = [100, 200, 300, 400, 500, 600]
    # p50 = 350 (between 300 and 400), mean = 350
    assert result["global_stats"]["n_cycles"] == 6
    assert result["global_stats"]["mean_ms"] == 350.0
    assert result["global_stats"]["min_ms"] == 100.0
    assert result["global_stats"]["max_ms"] == 600.0


def test_consistent_order_statuses(persistence):
    """statuses array always has OPTIMAL first, then FEASIBLE, INFEASIBLE, UNKNOWN."""
    result = persistence.get_solver_duration_by_status()
    assert [s["status"] for s in result["statuses"]] == [
        "OPTIMAL", "FEASIBLE", "INFEASIBLE", "UNKNOWN",
    ]


# ---------------------------------------------------------------------------
# API endpoint tests
# ---------------------------------------------------------------------------


def test_endpoint_returns_full_envelope(client, persistence):
    """GET /api/persistence/solver-duration-by-status returns the envelope."""
    _insert_cycle(persistence, "O1", sim_day=1, dur_ms=100, status="OPTIMAL")
    _insert_cycle(persistence, "F1", sim_day=2, dur_ms=500, status="FEASIBLE")

    resp = client.get("/api/persistence/solver-duration-by-status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["n_cycles_evaluated"] == 2
    assert body["n_statuses_with_data"] == 2
    assert body["slowest_status"] == "FEASIBLE"
    assert body["fastest_status"] == "OPTIMAL"
    assert "global_stats" in body
    assert "statuses" in body
    assert len(body["statuses"]) == 4
    # Verify all envelope keys present
    for key in ("since_sim_day", "until_sim_day", "infeasible_rate_pct",
                "feasible_rate_pct", "optimal_rate_pct",
                "slowest_vs_fastest_pct"):
        assert key in body


def test_endpoint_with_sim_day_filter(client, persistence):
    """Endpoint respects since_sim_day / until_sim_day query params."""
    _insert_cycle(persistence, "O1", sim_day=1, dur_ms=100, status="OPTIMAL")
    _insert_cycle(persistence, "F1", sim_day=50, dur_ms=500, status="FEASIBLE")

    resp = client.get(
        "/api/persistence/solver-duration-by-status?since_sim_day=10&until_sim_day=100"
    )
    assert resp.status_code == 200
    body = resp.json()
    # Only FEASIBLE cycle (sim_day=50) in window
    assert body["n_cycles_evaluated"] == 1
    by_status = {s["status"]: s for s in body["statuses"]}
    assert by_status["OPTIMAL"]["n_cycles"] == 0
    assert by_status["FEASIBLE"]["n_cycles"] == 1


def test_endpoint_empty_db(client, persistence):
    """Empty DB -> all-zero envelope + null slowest/fastest."""
    resp = client.get("/api/persistence/solver-duration-by-status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["n_cycles_evaluated"] == 0
    assert body["n_statuses_with_data"] == 0
    assert body["slowest_status"] is None
    assert body["fastest_status"] is None
    assert body["slowest_vs_fastest_pct"] is None
    assert body["infeasible_rate_pct"] == 0.0
    assert body["feasible_rate_pct"] == 0.0
    assert body["optimal_rate_pct"] == 0.0
    for s in body["statuses"]:
        assert s["n_cycles"] == 0
        assert s["p50_ms"] is None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def persistence():
    from agents.persistence import Persistence
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_solver_status.db")
        p = Persistence(db_path)
        yield p


@pytest.fixture
def client(persistence, monkeypatch):
    """TestClient with coordinator.persistence monkey-patched to our temp DB."""
    from fastapi.testclient import TestClient
    import web.backend.main as backend_main

    class _FakeCoord:
        pass

    coord = _FakeCoord()
    coord.persistence = persistence
    monkeypatch.setattr(backend_main, "coordinator", coord)
    yield TestClient(backend_main.app)
