"""
iter #72: solver-duration-by-season-status tests.

Tests the persistence method ``get_solver_duration_by_season_status``
which slices cycles along BOTH axes (season × status), yielding a 4×4 =
16-cell matrix. Each cell reports n_cycles + p50/p95/mean/min/max/stddev
+ mean_cost + cost_per_ton + pct_of_total.

Also tests:
- per-season rollup (across statuses)
- per-status rollup (across seasons)
- global stats
- slowest_cell / fastest_cell / slowest_vs_fastest_pct
- top_5_slowest_cells (sorted by p50 desc)
- sim_day filter
- cost_per_ton edge case when tons == 0
- caching consistency
- cell ordering is stable
"""
from __future__ import annotations

import os
import tempfile

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _insert_cycle(p, cycle_id, sim_day, *, dur_ms, seasonal_month,
                   status="OPTIMAL", cost_sek=100.0, tons=5.0):
    """Insert one cycle row with all fields needed by 3D heatmap query."""
    with p._conn() as conn:
        conn.execute(
            """INSERT INTO optimization_cycles
               (cycle_id, sim_day, sim_hour, wall_timestamp,
                activity_factor, n_supply_offers, n_demand_requests, n_matches,
                total_tons, total_cost_sek, total_co2_kg, total_distance_km,
                n_vehicles_used, n_vehicles_available, fleet_utilization_pct,
                solver_status, wall_duration_ms, seasonal_month)
               VALUES (?, ?, 0, '2026-09-16T00:00:00',
                       1.0, 1, 1, 1, ?, ?, 50.0, 10.0,
                       1, 1, 50, ?, ?, ?)""",
            (cycle_id, sim_day, tons, cost_sek, status, dur_ms, seasonal_month),
        )


# ---------------------------------------------------------------------------
# Persistence tests
# ---------------------------------------------------------------------------


def test_empty_db_returns_consistent_shape(persistence):
    """Empty DB → 16 cells with null values + 0 cycles."""
    res = persistence.get_solver_duration_by_season_status()
    assert res["n_cycles_evaluated"] == 0
    assert res["n_cells_with_data"] == 0
    assert res["seasons"] == ["winter", "spring", "summer", "fall"]
    assert res["statuses"] == ["OPTIMAL", "FEASIBLE", "INFEASIBLE", "UNKNOWN"]
    assert len(res["cells"]) == 16
    for cell in res["cells"]:
        assert cell["n_cycles"] == 0
        assert cell["p50_ms"] is None
    assert res["slowest_cell"] is None
    assert res["fastest_cell"] is None
    assert res["slowest_vs_fastest_pct"] is None
    assert res["top_5_slowest_cells"] == []


def test_season_assignment_per_month(persistence):
    """Dec/Jan/Feb → winter, Mar/Apr/May → spring, etc."""
    _insert_cycle(persistence, 1, 1, dur_ms=100.0,
                   seasonal_month=12, status="OPTIMAL")  # winter
    _insert_cycle(persistence, 2, 2, dur_ms=200.0,
                   seasonal_month=3, status="OPTIMAL")   # spring
    _insert_cycle(persistence, 3, 3, dur_ms=300.0,
                   seasonal_month=7, status="OPTIMAL")   # summer
    _insert_cycle(persistence, 4, 4, dur_ms=400.0,
                   seasonal_month=10, status="OPTIMAL")  # fall
    res = persistence.get_solver_duration_by_season_status()
    assert res["n_cycles_evaluated"] == 4
    for season, expected_p50 in [
        ("winter", 100.0),
        ("spring", 200.0),
        ("summer", 300.0),
        ("fall",   400.0),
    ]:
        cell = next(
            c for c in res["cells"]
            if c["season"] == season and c["status"] == "OPTIMAL"
        )
        assert cell["n_cycles"] == 1
        assert cell["p50_ms"] == expected_p50


def test_case_insensitive_status(persistence):
    """Lowercase status normalized to uppercase."""
    _insert_cycle(persistence, 1, 1, dur_ms=100.0,
                   seasonal_month=1, status="optimal")
    _insert_cycle(persistence, 2, 2, dur_ms=200.0,
                   seasonal_month=2, status="Optimal")
    res = persistence.get_solver_duration_by_season_status()
    cell = next(
        c for c in res["cells"]
        if c["season"] == "winter" and c["status"] == "OPTIMAL"
    )
    assert cell["n_cycles"] == 2
    assert cell["p50_ms"] == 150.0


def test_null_status_becomes_unknown(persistence):
    """NULL solver_status → UNKNOWN bucket."""
    _insert_cycle(persistence, 1, 1, dur_ms=500.0,
                   seasonal_month=1, status=None)
    res = persistence.get_solver_duration_by_season_status()
    cell = next(
        c for c in res["cells"]
        if c["season"] == "winter" and c["status"] == "UNKNOWN"
    )
    assert cell["n_cycles"] == 1
    assert cell["p50_ms"] == 500.0


def test_16_cell_matrix_stable_order(persistence):
    """Cells ordered: seasons outer (winter→fall), statuses inner (OPTIMAL→UNKNOWN)."""
    statuses = ["OPTIMAL", "FEASIBLE", "INFEASIBLE", "UNKNOWN"]
    season_months = [12, 3, 7, 10]
    cid = 1
    for m in season_months:
        for st in statuses:
            _insert_cycle(persistence, cid, cid, dur_ms=float(cid * 100),
                          seasonal_month=m, status=st)
            cid += 1
    res = persistence.get_solver_duration_by_season_status()
    assert len(res["cells"]) == 16
    expected_order = [
        ("winter", "OPTIMAL"),
        ("winter", "FEASIBLE"),
        ("winter", "INFEASIBLE"),
        ("winter", "UNKNOWN"),
        ("spring", "OPTIMAL"),
        ("spring", "FEASIBLE"),
        ("spring", "INFEASIBLE"),
        ("spring", "UNKNOWN"),
        ("summer", "OPTIMAL"),
        ("summer", "FEASIBLE"),
        ("summer", "INFEASIBLE"),
        ("summer", "UNKNOWN"),
        ("fall", "OPTIMAL"),
        ("fall", "FEASIBLE"),
        ("fall", "INFEASIBLE"),
        ("fall", "UNKNOWN"),
    ]
    actual = [(c["season"], c["status"]) for c in res["cells"]]
    assert actual == expected_order


def test_pct_of_total_sums_to_100(persistence):
    """pct_of_total across all cells sums to ~100%."""
    _insert_cycle(persistence, 1, 1, dur_ms=100.0,
                   seasonal_month=12, status="OPTIMAL")
    _insert_cycle(persistence, 2, 2, dur_ms=200.0,
                   seasonal_month=3, status="FEASIBLE")
    _insert_cycle(persistence, 3, 3, dur_ms=300.0,
                   seasonal_month=7, status="INFEASIBLE")
    res = persistence.get_solver_duration_by_season_status()
    total_pct = sum(c["pct_of_total"] for c in res["cells"])
    assert abs(total_pct - 100.0) < 0.1


def test_season_rollup_matches_cells(persistence):
    """season_rollup[i].n_cycles == sum of cells in that season."""
    _insert_cycle(persistence, 1, 1, dur_ms=100.0,
                   seasonal_month=12, status="OPTIMAL")    # winter
    _insert_cycle(persistence, 2, 2, dur_ms=200.0,
                   seasonal_month=1, status="FEASIBLE")    # winter
    _insert_cycle(persistence, 3, 3, dur_ms=300.0,
                   seasonal_month=3, status="OPTIMAL")     # spring
    _insert_cycle(persistence, 4, 4, dur_ms=400.0,
                   seasonal_month=7, status="INFEASIBLE")  # summer
    res = persistence.get_solver_duration_by_season_status()
    winter = next(r for r in res["season_rollup"] if r["season"] == "winter")
    assert winter["n_cycles"] == 2
    spring = next(r for r in res["season_rollup"] if r["season"] == "spring")
    assert spring["n_cycles"] == 1
    summer = next(r for r in res["season_rollup"] if r["season"] == "summer")
    assert summer["n_cycles"] == 1
    fall = next(r for r in res["season_rollup"] if r["season"] == "fall")
    assert fall["n_cycles"] == 0
    assert fall["p50_ms"] is None


def test_status_rollup_matches_cells(persistence):
    """status_rollup[i].n_cycles == sum of cells for that status."""
    _insert_cycle(persistence, 1, 1, dur_ms=100.0,
                   seasonal_month=12, status="OPTIMAL")
    _insert_cycle(persistence, 2, 2, dur_ms=200.0,
                   seasonal_month=3, status="OPTIMAL")
    _insert_cycle(persistence, 3, 3, dur_ms=300.0,
                   seasonal_month=7, status="OPTIMAL")
    _insert_cycle(persistence, 4, 4, dur_ms=400.0,
                   seasonal_month=9, status="FEASIBLE")
    res = persistence.get_solver_duration_by_season_status()
    opt = next(r for r in res["status_rollup"] if r["status"] == "OPTIMAL")
    assert opt["n_cycles"] == 3
    feas = next(r for r in res["status_rollup"] if r["status"] == "FEASIBLE")
    assert feas["n_cycles"] == 1
    inf = next(r for r in res["status_rollup"] if r["status"] == "INFEASIBLE")
    assert inf["n_cycles"] == 0


def test_global_stats_match_total(persistence):
    """global_stats.n_cycles == n_cycles_evaluated when all populated."""
    durations = [100, 200, 300, 400, 500, 600, 700, 800,
                 900, 1000, 1100, 1200, 1300, 1400, 1500, 1600]
    statuses = ["OPTIMAL", "FEASIBLE", "INFEASIBLE", "UNKNOWN"]
    months = [12, 1, 2, 1, 3, 4, 5, 3, 6, 7, 8, 6, 9, 10, 11, 9]
    for i, (m, st) in enumerate(zip(months, statuses * 4)):
        _insert_cycle(persistence, i + 1, i + 1, dur_ms=float(durations[i]),
                      seasonal_month=m, status=st)
    res = persistence.get_solver_duration_by_season_status()
    assert res["n_cycles_evaluated"] == 16
    assert res["global_stats"]["n_cycles"] == 16
    assert res["global_stats"]["min_ms"] == 100.0
    assert res["global_stats"]["max_ms"] == 1600.0
    assert res["global_stats"]["mean_ms"] == 850.0


def test_slowest_and_fastest_cell(persistence):
    """slowest_cell / fastest_cell picked by p50 across populated cells."""
    _insert_cycle(persistence, 1, 1, dur_ms=100.0,
                   seasonal_month=12, status="OPTIMAL")     # winter OPT (fastest)
    _insert_cycle(persistence, 2, 2, dur_ms=500.0,
                   seasonal_month=3, status="FEASIBLE")    # spring FEAS
    _insert_cycle(persistence, 3, 3, dur_ms=900.0,
                   seasonal_month=7, status="INFEASIBLE")  # summer INFEAS (slowest)
    _insert_cycle(persistence, 4, 4, dur_ms=300.0,
                   seasonal_month=10, status="UNKNOWN")    # fall UNKNOWN
    res = persistence.get_solver_duration_by_season_status()
    assert res["slowest_cell"]["season"] == "summer"
    assert res["slowest_cell"]["status"] == "INFEASIBLE"
    assert res["slowest_cell"]["p50_ms"] == 900.0
    assert res["fastest_cell"]["season"] == "winter"
    assert res["fastest_cell"]["status"] == "OPTIMAL"
    assert res["fastest_cell"]["p50_ms"] == 100.0
    # (900 - 100) / 100 * 100 = 800%
    assert res["slowest_vs_fastest_pct"] == 800.0


def test_top_5_slowest_cells_sorted_desc(persistence):
    """top_5_slowest_cells sorted by p50 desc, capped at 5."""
    cycles = [
        (12, "OPTIMAL", 100.0),
        (1,  "FEASIBLE", 200.0),
        (3,  "INFEASIBLE", 300.0),
        (5,  "UNKNOWN", 400.0),
        (6,  "OPTIMAL", 500.0),
        (8,  "FEASIBLE", 600.0),
    ]
    for i, (m, st, w) in enumerate(cycles):
        _insert_cycle(persistence, i + 1, i + 1, dur_ms=w,
                      seasonal_month=m, status=st)
    res = persistence.get_solver_duration_by_season_status()
    top5 = res["top_5_slowest_cells"]
    assert len(top5) == 5
    p50s = [c["p50_ms"] for c in top5]
    assert p50s == sorted(p50s, reverse=True)
    assert p50s == [600.0, 500.0, 400.0, 300.0, 200.0]


def test_sim_day_filter(persistence):
    """since_sim_day / until_sim_day filter cycles correctly."""
    for day in range(1, 11):
        _insert_cycle(persistence, day, day, dur_ms=float(day * 100),
                      seasonal_month=1, status="OPTIMAL")
    res = persistence.get_solver_duration_by_season_status(
        since_sim_day=3, until_sim_day=7,
    )
    assert res["n_cycles_evaluated"] == 5
    assert res["since_sim_day"] == 3
    assert res["until_sim_day"] == 7
    cell = next(
        c for c in res["cells"]
        if c["season"] == "winter" and c["status"] == "OPTIMAL"
    )
    assert cell["n_cycles"] == 5
    # p50 of [300,400,500,600,700] = 500
    assert cell["p50_ms"] == 500.0


def test_cost_per_ton_when_tons_zero(persistence):
    """mean_cost_per_ton_sek == null when tons == 0."""
    _insert_cycle(persistence, 1, 1, dur_ms=100.0,
                   seasonal_month=12, status="OPTIMAL",
                   cost_sek=500.0, tons=0.0)
    res = persistence.get_solver_duration_by_season_status()
    cell = next(
        c for c in res["cells"]
        if c["season"] == "winter" and c["status"] == "OPTIMAL"
    )
    assert cell["mean_cost_sek"] == 500.0
    assert cell["mean_cost_per_ton_sek"] is None


def test_cost_per_ton_normal(persistence):
    """Normal case: mean_cost_per_ton = mean_cost / mean_tons."""
    _insert_cycle(persistence, 1, 1, dur_ms=100.0,
                   seasonal_month=12, status="OPTIMAL",
                   cost_sek=2000.0, tons=10.0)
    res = persistence.get_solver_duration_by_season_status()
    cell = next(
        c for c in res["cells"]
        if c["season"] == "winter" and c["status"] == "OPTIMAL"
    )
    assert cell["mean_cost_sek"] == 2000.0
    assert cell["mean_cost_per_ton_sek"] == 200.0


def test_caching_returns_same_shape(persistence):
    """Two calls return consistent shape (cache hit on second)."""
    _insert_cycle(persistence, 1, 1, dur_ms=100.0,
                   seasonal_month=12, status="OPTIMAL")
    r1 = persistence.get_solver_duration_by_season_status()
    r2 = persistence.get_solver_duration_by_season_status()
    assert r1["n_cycles_evaluated"] == r2["n_cycles_evaluated"]
    assert len(r1["cells"]) == len(r2["cells"])
    for c1, c2 in zip(r1["cells"], r2["cells"]):
        assert c1["season"] == c2["season"]
        assert c1["status"] == c2["status"]
        assert c1["n_cycles"] == c2["n_cycles"]


def test_single_cell_all_cycles(persistence):
    """All cycles in one cell → 1 populated, 15 null."""
    for i in range(10):
        _insert_cycle(persistence, i + 1, i + 1, dur_ms=float((i + 1) * 100),
                      seasonal_month=1, status="OPTIMAL")
    res = persistence.get_solver_duration_by_season_status()
    assert res["n_cycles_evaluated"] == 10
    assert res["n_cells_with_data"] == 1
    populated = [c for c in res["cells"] if c["n_cycles"] > 0]
    assert len(populated) == 1
    assert populated[0]["season"] == "winter"
    assert populated[0]["status"] == "OPTIMAL"
    assert populated[0]["n_cycles"] == 10
    # Single cell: slowest == fastest
    assert res["slowest_cell"] is not None
    assert res["fastest_cell"] is not None
    assert res["slowest_cell"] == res["fastest_cell"]
    assert res["slowest_vs_fastest_pct"] == 0.0


def test_p50_interpolation_in_cell(persistence):
    """p50 of [100,200,300,400,500] = 300 (middle)."""
    for i, w in enumerate([100, 200, 300, 400, 500]):
        _insert_cycle(persistence, i + 1, i + 1, dur_ms=float(w),
                      seasonal_month=1, status="OPTIMAL")
    res = persistence.get_solver_duration_by_season_status()
    cell = next(
        c for c in res["cells"]
        if c["season"] == "winter" and c["status"] == "OPTIMAL"
    )
    assert cell["p50_ms"] == 300.0


def test_min_max_in_cell(persistence):
    """min_ms / max_ms track cell extremes."""
    _insert_cycle(persistence, 1, 1, dur_ms=50.0,
                   seasonal_month=1, status="OPTIMAL")
    _insert_cycle(persistence, 2, 2, dur_ms=1000.0,
                   seasonal_month=1, status="OPTIMAL")
    res = persistence.get_solver_duration_by_season_status()
    cell = next(
        c for c in res["cells"]
        if c["season"] == "winter" and c["status"] == "OPTIMAL"
    )
    assert cell["min_ms"] == 50.0
    assert cell["max_ms"] == 1000.0


def test_slowest_vs_fastest_pct_when_fastest_zero(persistence):
    """Edge case: fastest p50 == 0 → slowest_vs_fastest_pct is null."""
    _insert_cycle(persistence, 1, 1, dur_ms=0.0,
                   seasonal_month=12, status="OPTIMAL")
    _insert_cycle(persistence, 2, 2, dur_ms=500.0,
                   seasonal_month=3, status="FEASIBLE")
    res = persistence.get_solver_duration_by_season_status()
    assert res["slowest_vs_fastest_pct"] is None


# ---------------------------------------------------------------------------
# Endpoint tests
# ---------------------------------------------------------------------------


def test_endpoint_basic(persistence, client):
    """Endpoint returns 200 + consistent shape."""
    _insert_cycle(persistence, 1, 1, dur_ms=100.0,
                   seasonal_month=12, status="OPTIMAL")
    resp = client.get("/api/persistence/solver-duration-by-season-status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["n_cycles_evaluated"] == 1
    assert body["n_cells_with_data"] == 1
    assert len(body["cells"]) == 16
    assert body["seasons"] == ["winter", "spring", "summer", "fall"]
    assert body["statuses"] == ["OPTIMAL", "FEASIBLE", "INFEASIBLE", "UNKNOWN"]
    assert body["slowest_cell"] is not None
    assert body["fastest_cell"] is not None
    assert "top_5_slowest_cells" in body


def test_endpoint_sim_day_filter(persistence, client):
    """Endpoint sim_day filter works."""
    for day in range(1, 6):
        _insert_cycle(persistence, day, day, dur_ms=float(day * 100),
                      seasonal_month=1, status="OPTIMAL")
    resp = client.get(
        "/api/persistence/solver-duration-by-season-status"
        "?since_sim_day=2&until_sim_day=4"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["n_cycles_evaluated"] == 3
    assert body["since_sim_day"] == 2
    assert body["until_sim_day"] == 4


def test_endpoint_invalid_sim_day_range(persistence, client):
    """since_sim_day > until_sim_day → 400."""
    resp = client.get(
        "/api/persistence/solver-duration-by-season-status"
        "?since_sim_day=10&until_sim_day=5"
    )
    assert resp.status_code == 400


def test_endpoint_empty_db(persistence, client):
    """Empty DB returns 200 with consistent shape."""
    resp = client.get("/api/persistence/solver-duration-by-season-status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["n_cycles_evaluated"] == 0
    assert body["n_cells_with_data"] == 0
    assert len(body["cells"]) == 16
    assert body["slowest_cell"] is None
    assert body["fastest_cell"] is None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def persistence():
    from agents.persistence import Persistence
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_solver_season_status.db")
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
