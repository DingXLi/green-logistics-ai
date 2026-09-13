"""
iter #69: solver-duration-by-season tests.

Tests the persistence method ``get_solver_duration_by_season`` and the
``/api/persistence/solver-duration-by-season`` endpoint.

Groups cycles into 4 seasons (winter/spring/summer/fall) based on
``seasonal_month`` and computes per-season wall_duration_ms p50/p95/mean
plus solver_status breakdown (OPTIMAL/FEASIBLE/INFEASIBLE/UNKNOWN), and
global stats + slowest/fastest season comparison.
"""
from __future__ import annotations

import os
import tempfile

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _insert_cycle(p, cycle_id, sim_day, *, dur_ms, status="OPTIMAL",
                   seasonal_month=1):
    """Insert a single cycle row with wall_duration_ms + seasonal_month."""
    with p._conn() as conn:
        conn.execute(
            """INSERT INTO optimization_cycles
               (cycle_id, sim_day, sim_hour, wall_timestamp,
                activity_factor, n_supply_offers, n_demand_requests, n_matches,
                total_tons, total_cost_sek, total_co2_kg, total_distance_km,
                n_vehicles_used, n_vehicles_available, fleet_utilization_pct,
                solver_status, wall_duration_ms, seasonal_month)
               VALUES (?, ?, 0, '2026-09-13T00:00:00',
                       1.0, 1, 1, 1, 5.0, 100.0, 50.0, 10.0,
                       1, 1, 50, ?, ?, ?)""",
            (cycle_id, sim_day, status, dur_ms, seasonal_month),
        )


# ---------------------------------------------------------------------------
# Persistence tests
# ---------------------------------------------------------------------------


def test_empty_db_returns_consistent_shape(persistence):
    """No cycles -> all 4 seasons empty + global null + slowest/fastest None."""
    result = persistence.get_solver_duration_by_season()
    assert result["n_cycles_evaluated"] == 0
    assert result["n_seasons_with_data"] == 0
    assert result["since_sim_day"] is None
    assert result["until_sim_day"] is None
    assert len(result["seasons"]) == 4
    assert result["global_stats"]["n_cycles"] == 0
    assert result["global_stats"]["p50_ms"] is None
    assert result["slowest_season"] is None
    assert result["fastest_season"] is None
    assert result["slowest_p50_ms"] is None
    assert result["fastest_p50_ms"] is None
    assert result["slowest_vs_fastest_pct"] is None


def test_season_assignment_correctness(persistence):
    """seasonal_month -> season mapping is correct."""
    # winter: 12, 1, 2
    _insert_cycle(persistence, "W1", sim_day=1, dur_ms=100, seasonal_month=12)
    _insert_cycle(persistence, "W2", sim_day=2, dur_ms=200, seasonal_month=1)
    _insert_cycle(persistence, "W3", sim_day=3, dur_ms=300, seasonal_month=2)
    # spring: 3, 4, 5
    _insert_cycle(persistence, "SP1", sim_day=4, dur_ms=400, seasonal_month=3)
    _insert_cycle(persistence, "SP2", sim_day=5, dur_ms=500, seasonal_month=4)
    # summer: 6, 7, 8
    _insert_cycle(persistence, "SU1", sim_day=6, dur_ms=600, seasonal_month=6)
    _insert_cycle(persistence, "SU2", sim_day=7, dur_ms=700, seasonal_month=8)
    # fall: 9, 10, 11
    _insert_cycle(persistence, "F1", sim_day=8, dur_ms=800, seasonal_month=9)
    _insert_cycle(persistence, "F2", sim_day=9, dur_ms=900, seasonal_month=11)

    result = persistence.get_solver_duration_by_season()
    seasons_by_name = {s["season"]: s for s in result["seasons"]}

    assert seasons_by_name["winter"]["n_cycles"] == 3
    assert seasons_by_name["spring"]["n_cycles"] == 2
    assert seasons_by_name["summer"]["n_cycles"] == 2
    assert seasons_by_name["fall"]["n_cycles"] == 2

    # winter months list = [12, 1, 2]
    assert seasons_by_name["winter"]["months"] == [12, 1, 2]
    assert seasons_by_name["spring"]["months"] == [3, 4, 5]
    assert seasons_by_name["summer"]["months"] == [6, 7, 8]
    assert seasons_by_name["fall"]["months"] == [9, 10, 11]


def test_p50_p95_min_max_mean_per_season(persistence):
    """Per-season stats: p50/p95/mean/min/max/stddev computed correctly."""
    # winter: 100, 200, 300 -> mean=200, min=100, max=300, p50=200, p95=290
    _insert_cycle(persistence, "W1", sim_day=1, dur_ms=100, seasonal_month=12)
    _insert_cycle(persistence, "W2", sim_day=2, dur_ms=200, seasonal_month=1)
    _insert_cycle(persistence, "W3", sim_day=3, dur_ms=300, seasonal_month=2)

    result = persistence.get_solver_duration_by_season()
    winter = next(s for s in result["seasons"] if s["season"] == "winter")
    assert winter["n_cycles"] == 3
    assert winter["mean_ms"] == 200.0
    assert winter["min_ms"] == 100.0
    assert winter["max_ms"] == 300.0
    assert winter["p50_ms"] == 200.0
    assert winter["p95_ms"] == 290.0
    # stddev = sqrt(((100-200)^2 + (200-200)^2 + (300-200)^2)/3)
    # = sqrt((10000 + 0 + 10000) / 3) = sqrt(6666.67) ≈ 81.65
    assert 81.0 < winter["stddev_ms"] < 82.0


def test_status_counts_per_season(persistence):
    """solver_status_counts reflects the OPTIMAL/FEASIBLE/INFEASIBLE mix."""
    _insert_cycle(persistence, "A", sim_day=1, dur_ms=100,
                   status="OPTIMAL", seasonal_month=12)
    _insert_cycle(persistence, "B", sim_day=2, dur_ms=200,
                   status="OPTIMAL", seasonal_month=1)
    _insert_cycle(persistence, "C", sim_day=3, dur_ms=300,
                   status="FEASIBLE", seasonal_month=2)
    _insert_cycle(persistence, "D", sim_day=4, dur_ms=400,
                   status="INFEASIBLE", seasonal_month=3)
    _insert_cycle(persistence, "E", sim_day=5, dur_ms=500,
                   status="RANDOM_GARBAGE", seasonal_month=4)

    result = persistence.get_solver_duration_by_season()
    seasons_by_name = {s["season"]: s for s in result["seasons"]}

    assert seasons_by_name["winter"]["solver_status_counts"] == {
        "OPTIMAL": 2, "FEASIBLE": 1, "INFEASIBLE": 0, "UNKNOWN": 0,
    }
    # spring: 1 INFEASIBLE + 1 UNKNOWN (random garbage)
    assert seasons_by_name["spring"]["solver_status_counts"] == {
        "OPTIMAL": 0, "FEASIBLE": 0, "INFEASIBLE": 1, "UNKNOWN": 1,
    }


def test_global_stats_aggregate_all_cycles(persistence):
    """global_stats is computed across ALL cycles in window (all seasons)."""
    _insert_cycle(persistence, "A", sim_day=1, dur_ms=100, seasonal_month=12)
    _insert_cycle(persistence, "B", sim_day=2, dur_ms=200, seasonal_month=6)
    _insert_cycle(persistence, "C", sim_day=3, dur_ms=300, seasonal_month=9)

    result = persistence.get_solver_duration_by_season()
    g = result["global_stats"]
    assert g["n_cycles"] == 3
    assert g["mean_ms"] == 200.0
    assert g["min_ms"] == 100.0
    assert g["max_ms"] == 300.0
    assert g["p50_ms"] == 200.0


def test_slowest_fastest_season_by_p50(persistence):
    """slowest/fastest picked by p50_ms (not mean, not max)."""
    # winter: [1000, 1000, 1000] p50=1000
    _insert_cycle(persistence, "W1", sim_day=1, dur_ms=1000, seasonal_month=12)
    _insert_cycle(persistence, "W2", sim_day=2, dur_ms=1000, seasonal_month=1)
    _insert_cycle(persistence, "W3", sim_day=3, dur_ms=1000, seasonal_month=2)
    # summer: [100, 100, 100] p50=100  (fastest)
    _insert_cycle(persistence, "SU1", sim_day=4, dur_ms=100, seasonal_month=6)
    _insert_cycle(persistence, "SU2", sim_day=5, dur_ms=100, seasonal_month=7)
    _insert_cycle(persistence, "SU3", sim_day=6, dur_ms=100, seasonal_month=8)
    # fall: [500, 500] p50=500
    _insert_cycle(persistence, "F1", sim_day=7, dur_ms=500, seasonal_month=9)
    _insert_cycle(persistence, "F2", sim_day=8, dur_ms=500, seasonal_month=10)

    result = persistence.get_solver_duration_by_season()
    assert result["slowest_season"] == "winter"
    assert result["fastest_season"] == "summer"
    assert result["slowest_p50_ms"] == 1000.0
    assert result["fastest_p50_ms"] == 100.0
    # (1000 - 100) / 100 * 100 = 900%
    assert result["slowest_vs_fastest_pct"] == 900.0


def test_single_season_only(persistence):
    """If only one season has data, slowest == fastest == that season."""
    _insert_cycle(persistence, "W1", sim_day=1, dur_ms=100, seasonal_month=12)
    _insert_cycle(persistence, "W2", sim_day=2, dur_ms=200, seasonal_month=1)

    result = persistence.get_solver_duration_by_season()
    assert result["n_seasons_with_data"] == 1
    assert result["slowest_season"] == "winter"
    assert result["fastest_season"] == "winter"
    assert result["slowest_vs_fastest_pct"] == 0.0  # same season


def test_skips_cycles_with_null_wall_duration(persistence):
    """Cycles with wall_duration_ms IS NULL are excluded entirely."""
    _insert_cycle(persistence, "A", sim_day=1, dur_ms=100, seasonal_month=12)
    # Insert a row with NULL wall_duration_ms directly
    with persistence._conn() as conn:
        conn.execute(
            """INSERT INTO optimization_cycles
               (cycle_id, sim_day, sim_hour, wall_timestamp,
                activity_factor, n_supply_offers, n_demand_requests, n_matches,
                total_tons, total_cost_sek, total_co2_kg, total_distance_km,
                n_vehicles_used, n_vehicles_available, fleet_utilization_pct,
                solver_status, wall_duration_ms, seasonal_month)
               VALUES (?, ?, 0, '2026-09-13T00:00:00',
                       1.0, 1, 1, 1, 5.0, 100.0, 50.0, 10.0,
                       1, 1, 50, 'OPTIMAL', NULL, ?)""",
            ("NULL_DUR", 99, 3),
        )

    result = persistence.get_solver_duration_by_season()
    assert result["n_cycles_evaluated"] == 1  # NULL row excluded
    winter = next(s for s in result["seasons"] if s["season"] == "winter")
    spring = next(s for s in result["seasons"] if s["season"] == "spring")
    assert winter["n_cycles"] == 1
    assert spring["n_cycles"] == 0


def test_sim_day_filter(persistence):
    """since_sim_day / until_sim_day window applied."""
    # sim_day 1-5
    for i in range(1, 6):
        _insert_cycle(persistence, f"OPT{i}", sim_day=i, dur_ms=100,
                       seasonal_month=12)  # all winter
    # sim_day 10-15
    for i in range(10, 16):
        _insert_cycle(persistence, f"LATE{i}", sim_day=i, dur_ms=200,
                       seasonal_month=6)  # all summer

    # Window only the late summer cycles
    result = persistence.get_solver_duration_by_season(
        since_sim_day=8, until_sim_day=20,
    )
    assert result["since_sim_day"] == 8
    assert result["until_sim_day"] == 20
    seasons_by_name = {s["season"]: s for s in result["seasons"]}
    assert seasons_by_name["winter"]["n_cycles"] == 0
    assert seasons_by_name["summer"]["n_cycles"] == 6


def test_sim_day_filter_invalid_no_crash(persistence):
    """since_sim_day > until_sim_day returns empty without error (filter just matches nothing)."""
    _insert_cycle(persistence, "A", sim_day=1, dur_ms=100, seasonal_month=12)
    result = persistence.get_solver_duration_by_season(
        since_sim_day=100, until_sim_day=10,
    )
    assert result["n_cycles_evaluated"] == 0


def test_caching_returns_same_dict(persistence):
    """Second call within cache TTL returns identical dict (cached)."""
    _insert_cycle(persistence, "A", sim_day=1, dur_ms=100, seasonal_month=12)
    r1 = persistence.get_solver_duration_by_season()
    r2 = persistence.get_solver_duration_by_season()
    assert r1 is r2  # exact same object (cache hit)


def test_p50_with_single_value(persistence):
    """Edge case: 1 cycle in season -> p50 == that single value."""
    _insert_cycle(persistence, "A", sim_day=1, dur_ms=42.5,
                   seasonal_month=12)
    result = persistence.get_solver_duration_by_season()
    winter = next(s for s in result["seasons"] if s["season"] == "winter")
    assert winter["n_cycles"] == 1
    assert winter["p50_ms"] == 42.5
    assert winter["p95_ms"] == 42.5  # single value => p95 == p50
    assert winter["min_ms"] == 42.5
    assert winter["max_ms"] == 42.5
    assert winter["mean_ms"] == 42.5
    assert winter["stddev_ms"] == 0.0


def test_all_four_seasons_even_split(persistence):
    """4 seasons × 2 cycles each -> 8 total, 2 per season, equal p50=200."""
    for i, m in enumerate([12, 1,    # winter
                            3, 4,    # spring
                            6, 7,    # summer
                            9, 10]): # fall
        _insert_cycle(persistence, f"C{i}", sim_day=i + 1, dur_ms=200,
                       seasonal_month=m)
    result = persistence.get_solver_duration_by_season()
    assert result["n_cycles_evaluated"] == 8
    assert result["n_seasons_with_data"] == 4
    for s in result["seasons"]:
        assert s["n_cycles"] == 2
        assert s["p50_ms"] == 200.0
    # All seasons tied -> slowest/fastest pick first by p50 (max/min)
    # Max returns first max (200) which is winter, min returns first min (200) which is winter
    # Either way both slowest/fastest == winter (since all == 200)
    assert result["slowest_season"] == "winter"
    assert result["fastest_season"] == "winter"


# ---------------------------------------------------------------------------
# API endpoint tests
# ---------------------------------------------------------------------------


def test_endpoint_returns_full_envelope(client, persistence):
    """GET /api/persistence/solver-duration-by-season returns the envelope."""
    _insert_cycle(persistence, "W1", sim_day=1, dur_ms=100,
                   status="OPTIMAL", seasonal_month=12)
    _insert_cycle(persistence, "SU1", sim_day=2, dur_ms=500,
                   status="FEASIBLE", seasonal_month=7)

    resp = client.get("/api/persistence/solver-duration-by-season")
    assert resp.status_code == 200
    body = resp.json()
    assert body["n_cycles_evaluated"] == 2
    assert body["n_seasons_with_data"] == 2
    assert body["slowest_season"] == "summer"
    assert body["fastest_season"] == "winter"
    assert "global_stats" in body
    assert "seasons" in body
    assert len(body["seasons"]) == 4


def test_endpoint_with_sim_day_filter(client, persistence):
    """Endpoint respects since_sim_day / until_sim_day query params."""
    _insert_cycle(persistence, "W1", sim_day=1, dur_ms=100, seasonal_month=12)
    _insert_cycle(persistence, "SU1", sim_day=50, dur_ms=200, seasonal_month=7)

    resp = client.get(
        "/api/persistence/solver-duration-by-season?since_sim_day=10&until_sim_day=100"
    )
    assert resp.status_code == 200
    body = resp.json()
    # Only the summer cycle (sim_day=50) falls in the window
    assert body["n_cycles_evaluated"] == 1
    seasons_by_name = {s["season"]: s for s in body["seasons"]}
    assert seasons_by_name["winter"]["n_cycles"] == 0
    assert seasons_by_name["summer"]["n_cycles"] == 1


def test_endpoint_empty_db(client, persistence):
    """Empty DB -> all-zero envelope + null slowest/fastest."""
    resp = client.get("/api/persistence/solver-duration-by-season")
    assert resp.status_code == 200
    body = resp.json()
    assert body["n_cycles_evaluated"] == 0
    assert body["n_seasons_with_data"] == 0
    assert body["slowest_season"] is None
    assert body["fastest_season"] is None
    assert body["slowest_vs_fastest_pct"] is None
    for s in body["seasons"]:
        assert s["n_cycles"] == 0
        assert s["p50_ms"] is None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def persistence():
    from agents.persistence import Persistence
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_solver_season.db")
        p = Persistence(db_path)
        yield p


@pytest.fixture
def client(persistence, monkeypatch):
    """TestClient with coordinator.persistence monkey-patched to our temp DB.

    Reuses the iter #68 pattern: stand up a fake coordinator whose
    ``persistence`` attribute is the temp Persistence instance, so the
    FastAPI endpoint reads/writes the same DB the test inserts into.
    """
    from fastapi.testclient import TestClient
    import web.backend.main as backend_main

    class _FakeCoord:
        pass

    coord = _FakeCoord()
    coord.persistence = persistence
    monkeypatch.setattr(backend_main, "coordinator", coord)
    # Note: no `with` so startup_event (which re-inits coordinator) doesn't run
    yield TestClient(backend_main.app)
