"""
iter #67: cycle-duration-by-problem-size tests.

Tests the persistence method ``get_cycle_duration_by_problem_size`` and the
``/api/persistence/cycle-duration-by-problem-size`` endpoint.

Buckets:
- small  : total_tons < 10
- medium : 10 <= total_tons < 50
- large  : 50 <= total_tons < 200
- xlarge : total_tons >= 200
"""
from __future__ import annotations

import os
import tempfile

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _insert_cycle(p, cycle_id, sim_day, *, tons, dur_ms, cost=100.0, co2=50.0,
                  distance=10.0, n_matches=1, status="OPTIMAL"):
    """Insert a single cycle row (matches optimization_cycles schema)."""
    with p._conn() as conn:
        conn.execute(
            """INSERT INTO optimization_cycles
               (cycle_id, sim_day, sim_hour, wall_timestamp,
                activity_factor, n_supply_offers, n_demand_requests, n_matches,
                total_tons, total_cost_sek, total_co2_kg, total_distance_km,
                n_vehicles_used, n_vehicles_available, fleet_utilization_pct,
                solver_status, wall_duration_ms)
               VALUES (?, ?, 0, '2026-09-11T00:00:00',
                       1.0, 1, 1, ?, ?, ?, ?,
                       ?, 1, 1, 50, ?, ?)""",
            (cycle_id, sim_day, n_matches, tons, cost, co2, distance, status,
             dur_ms),
        )


def _insert_supply(p, cycle_id, material_type):
    with p._conn() as conn:
        conn.execute(
            """INSERT INTO supply_offers
               (cycle_id, supply_id, material_type, available_tons,
                location_lat, location_lon)
               VALUES (?, ?, ?, 5.0, 0.0, 0.0)""",
            (cycle_id, cycle_id + "-s", material_type),
        )


# ---------------------------------------------------------------------------
# Persistence tests
# ---------------------------------------------------------------------------


def test_empty_db_returns_consistent_shape(persistence):
    """No cycles -> empty buckets but full envelope."""
    result = persistence.get_cycle_duration_by_problem_size()
    assert result["n_cycles"] == 0
    assert result["n_buckets"] == 4
    assert len(result["bucket_bounds"]) == 4
    for b in result["buckets"]:
        assert b["count"] == 0
        assert b["pct_of_total"] == 0.0
        assert b["duration_ms"]["mean"] is None
        assert b["solver_status_counts"]["OPTIMAL"] == 0
    assert result["scaling_signal"] == "unknown"


def test_bucket_bounds_are_correct(persistence):
    """Verify bucket bounds match the documented scheme."""
    result = persistence.get_cycle_duration_by_problem_size()
    bounds = result["bucket_bounds"]
    assert bounds[0] == {
        "label": "small", "min_tons": 0, "max_tons": 10, "open_upper_bound": False,
    }
    assert bounds[1] == {
        "label": "medium", "min_tons": 10, "max_tons": 50, "open_upper_bound": False,
    }
    assert bounds[2] == {
        "label": "large", "min_tons": 50, "max_tons": 200, "open_upper_bound": False,
    }
    assert bounds[3] == {
        "label": "xlarge", "min_tons": 200, "max_tons": None, "open_upper_bound": True,
    }


def test_small_bucket_only_short_cycles(persistence):
    """< 10 tons -> small bucket."""
    for i in range(3):
        _insert_cycle(persistence, f"S{i}", sim_day=i + 1, tons=5.0,
                      dur_ms=50 + i * 10)
        _insert_supply(persistence, f"S{i}", "wood")
    result = persistence.get_cycle_duration_by_problem_size()
    small = next(b for b in result["buckets"] if b["label"] == "small")
    medium = next(b for b in result["buckets"] if b["label"] == "medium")
    large = next(b for b in result["buckets"] if b["label"] == "large")
    xlarge = next(b for b in result["buckets"] if b["label"] == "xlarge")
    assert small["count"] == 3
    assert medium["count"] == 0
    assert large["count"] == 0
    assert xlarge["count"] == 0
    assert small["pct_of_total"] == 100.0
    assert small["duration_ms"]["mean"] == 60.0
    assert small["dominant_material"] == "wood"


def test_boundary_tons_assignment(persistence):
    """Exact boundary values belong to the higher bucket."""
    # 10.0 -> medium, 50.0 -> large, 200.0 -> xlarge, 9.99 -> small
    cases = [
        ("S1", 1, 9.99, "small"),
        ("S2", 2, 10.0, "medium"),
        ("S3", 3, 49.99, "medium"),
        ("S4", 4, 50.0, "large"),
        ("S5", 5, 199.99, "large"),
        ("S6", 6, 200.0, "xlarge"),
        ("S7", 7, 999.99, "xlarge"),
    ]
    for cid, sim_day, tons, expected in cases:
        _insert_cycle(persistence, cid, sim_day=sim_day, tons=tons, dur_ms=100)
    result = persistence.get_cycle_duration_by_problem_size()
    bucket_by_label = {b["label"]: b["count"] for b in result["buckets"]}
    assert bucket_by_label["small"] == 1
    assert bucket_by_label["medium"] == 2
    assert bucket_by_label["large"] == 2
    assert bucket_by_label["xlarge"] == 2
    # All 7 should be accounted for
    assert sum(bucket_by_label.values()) == 7


def test_pct_sums_to_100(persistence):
    """Bucket pcts must sum to 100% (modulo rounding)."""
    for i in range(10):
        tons = (i + 1) * 25.0  # mix across buckets
        _insert_cycle(persistence, f"C{i}", sim_day=i + 1, tons=tons,
                      dur_ms=100 + i)
    result = persistence.get_cycle_duration_by_problem_size()
    total_pct = sum(b["pct_of_total"] for b in result["buckets"])
    assert abs(total_pct - 100.0) < 0.01


def test_per_bucket_stats_calculations(persistence):
    """Verify mean/median/min/max/stddev for duration_ms."""
    durations = [100, 200, 300, 400]
    for i, d in enumerate(durations):
        _insert_cycle(persistence, f"D{i}", sim_day=i + 1, tons=5.0, dur_ms=d)
    result = persistence.get_cycle_duration_by_problem_size()
    small = next(b for b in result["buckets"] if b["label"] == "small")
    assert small["duration_ms"]["mean"] == 250.0
    assert small["duration_ms"]["min"] == 100.0
    assert small["duration_ms"]["max"] == 400.0
    # stddev: sqrt(sum((v-mean)^2)/n) = sqrt(((150)^2 + (50)^2 + (50)^2 + (150)^2)/4)
    # = sqrt((22500 + 2500 + 2500 + 22500)/4) = sqrt(50000/4) = sqrt(12500) ≈ 111.80
    assert 111.0 < small["duration_ms"]["stddev"] < 112.5


def test_cost_per_ton_calculated_correctly(persistence):
    """cost_per_ton_sek = total_cost_sek / total_tons per cycle, then stats."""
    # 3 cycles, all small (5 tons), costs 100, 200, 300 -> per_ton = 20, 40, 60
    for i, cost in enumerate([100, 200, 300]):
        _insert_cycle(persistence, f"X{i}", sim_day=i + 1, tons=5.0,
                      dur_ms=100, cost=cost)
    result = persistence.get_cycle_duration_by_problem_size()
    small = next(b for b in result["buckets"] if b["label"] == "small")
    assert small["cost_per_ton_sek"]["mean"] == 40.0
    assert small["cost_per_ton_sek"]["median"] == 40.0
    assert small["cost_per_ton_sek"]["min"] == 20.0
    assert small["cost_per_ton_sek"]["max"] == 60.0


def test_solver_status_counts(persistence):
    """Verify solver_status_counts aggregation."""
    _insert_cycle(persistence, "A1", 1, tons=5.0, dur_ms=100, status="OPTIMAL")
    _insert_cycle(persistence, "A2", 2, tons=5.0, dur_ms=100, status="OPTIMAL")
    _insert_cycle(persistence, "A3", 3, tons=5.0, dur_ms=100, status="FEASIBLE")
    _insert_cycle(persistence, "A4", 4, tons=5.0, dur_ms=100, status="INFEASIBLE")
    result = persistence.get_cycle_duration_by_problem_size()
    small = next(b for b in result["buckets"] if b["label"] == "small")
    counts = small["solver_status_counts"]
    assert counts["OPTIMAL"] == 2
    assert counts["FEASIBLE"] == 1
    assert counts["INFEASIBLE"] == 1
    assert counts["UNKNOWN"] == 0


def test_dominant_material(persistence):
    """Most common material_type wins; None if no supplies."""
    # cycle B1 (wood) — appears in 3 cycles
    for i in range(3):
        cid = f"B{i}"
        _insert_cycle(persistence, cid, sim_day=i + 1, tons=5.0, dur_ms=100)
        _insert_supply(persistence, cid, "wood")
    # B3 (metal) appears in 1 cycle
    _insert_cycle(persistence, "B4", sim_day=5, tons=5.0, dur_ms=100)
    _insert_supply(persistence, "B4", "metal")
    result = persistence.get_cycle_duration_by_problem_size()
    small = next(b for b in result["buckets"] if b["label"] == "small")
    assert small["dominant_material"] == "wood"
    assert small["count"] == 4


def test_dominant_material_none_when_no_supplies(persistence):
    """No supply_offers => dominant_material is None."""
    _insert_cycle(persistence, "Z1", 1, tons=5.0, dur_ms=100)
    result = persistence.get_cycle_duration_by_problem_size()
    small = next(b for b in result["buckets"] if b["label"] == "small")
    assert small["dominant_material"] is None


def test_since_until_sim_day_filter(persistence):
    """Time-window filter respected."""
    for i in range(1, 11):
        _insert_cycle(persistence, f"T{i}", sim_day=i, tons=5.0, dur_ms=100)
    all_result = persistence.get_cycle_duration_by_problem_size()
    assert all_result["n_cycles"] == 10
    windowed = persistence.get_cycle_duration_by_problem_size(
        since_sim_day=3, until_sim_day=6,
    )
    assert windowed["n_cycles"] == 4
    assert windowed["since_sim_day"] == 3
    assert windowed["until_sim_day"] == 6


def test_scaling_signal_unknown_when_no_data(persistence):
    """Empty DB -> scaling_signal = unknown."""
    result = persistence.get_cycle_duration_by_problem_size()
    assert result["scaling_signal"] == "unknown"


def test_scaling_signal_sublinear_when_duration_does_not_grow(persistence):
    """If large bucket barely slower than small -> sublinear (good)."""
    # 3 small cycles at 50ms, 3 large cycles at 60ms -> duration_ratio = 1.2
    # tonnage_ratio = 25 -> sublinear
    for i in range(3):
        _insert_cycle(persistence, f"P{i}", sim_day=i + 1, tons=5.0, dur_ms=50)
        _insert_cycle(persistence, f"Q{i}", sim_day=10 + i, tons=100.0, dur_ms=60)
    result = persistence.get_cycle_duration_by_problem_size()
    assert result["scaling_signal"] == "sublinear"


def test_scaling_signal_superlinear_when_duration_explodes(persistence):
    """If large bucket is 100x slower than small -> superlinear (bad)."""
    for i in range(3):
        _insert_cycle(persistence, f"R{i}", sim_day=i + 1, tons=5.0, dur_ms=10)
        _insert_cycle(persistence, f"S{i}", sim_day=10 + i, tons=100.0,
                      dur_ms=10000)
    result = persistence.get_cycle_duration_by_problem_size()
    assert result["scaling_signal"] == "superlinear"


def test_scaling_signal_linear_when_proportional(persistence):
    """If duration grows proportionally -> linear."""
    # small ~50ms, large ~1100ms -> duration_ratio = 22, tonnage_ratio = 25
    # 22 <= 25*1.2=30 → linear
    for i in range(3):
        _insert_cycle(persistence, f"L{i}", sim_day=i + 1, tons=5.0, dur_ms=50)
        _insert_cycle(persistence, f"M{i}", sim_day=10 + i, tons=100.0,
                      dur_ms=1100)
    result = persistence.get_cycle_duration_by_problem_size()
    assert result["scaling_signal"] == "linear"


def test_n_matches_stat_shape(persistence):
    """n_matches stat returns float stats (mean/median/min/max/stddev)."""
    for i in range(3):
        _insert_cycle(persistence, f"N{i}", sim_day=i + 1, tons=5.0, dur_ms=100,
                      n_matches=i + 1)
    result = persistence.get_cycle_duration_by_problem_size()
    small = next(b for b in result["buckets"] if b["label"] == "small")
    assert small["n_matches"]["mean"] == 2.0
    assert small["n_matches"]["max"] == 3.0


# ---------------------------------------------------------------------------
# Endpoint tests
# ---------------------------------------------------------------------------


def test_endpoint_returns_200(monkeypatch):
    """Endpoint returns 200 + envelope."""
    from web.backend import main as backend_main
    from fastapi.testclient import TestClient

    class _FakePersistence:
        def get_cycle_duration_by_problem_size(self, **kwargs):
            return {
                "n_cycles": 0,
                "n_buckets": 4,
                "since_sim_day": kwargs.get("since_sim_day"),
                "until_sim_day": kwargs.get("until_sim_day"),
                "bucket_bounds": [
                    {"label": "small", "min_tons": 0, "max_tons": 10, "open_upper_bound": False},
                    {"label": "medium", "min_tons": 10, "max_tons": 50, "open_upper_bound": False},
                    {"label": "large", "min_tons": 50, "max_tons": 200, "open_upper_bound": False},
                    {"label": "xlarge", "min_tons": 200, "max_tons": None, "open_upper_bound": True},
                ],
                "buckets": [],
                "scaling_signal": "unknown",
            }

    class _FakeCoord:
        persistence = _FakePersistence()

    monkeypatch.setattr(backend_main, "coordinator", _FakeCoord())
    client = TestClient(backend_main.app)
    resp = client.get("/api/persistence/cycle-duration-by-problem-size")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    for key in [
        "n_cycles", "n_buckets", "since_sim_day", "until_sim_day",
        "bucket_bounds", "buckets", "scaling_signal",
    ]:
        assert key in data, f"missing key: {key}"


def test_endpoint_with_query_params(monkeypatch):
    """Endpoint forwards since_sim_day and until_sim_day."""
    from web.backend import main as backend_main
    from fastapi.testclient import TestClient

    received_kwargs: dict = {}

    class _FakePersistence:
        def get_cycle_duration_by_problem_size(self, **kwargs):
            received_kwargs.update(kwargs)
            return {
                "n_cycles": 0,
                "n_buckets": 4,
                "since_sim_day": kwargs.get("since_sim_day"),
                "until_sim_day": kwargs.get("until_sim_day"),
                "bucket_bounds": [],
                "buckets": [],
                "scaling_signal": "unknown",
            }

    class _FakeCoord:
        persistence = _FakePersistence()

    monkeypatch.setattr(backend_main, "coordinator", _FakeCoord())
    client = TestClient(backend_main.app)
    resp = client.get(
        "/api/persistence/cycle-duration-by-problem-size"
        "?since_sim_day=5&until_sim_day=10"
    )
    assert resp.status_code == 200
    assert received_kwargs["since_sim_day"] == 5
    assert received_kwargs["until_sim_day"] == 10


def test_endpoint_400_when_inverted_window(monkeypatch):
    """since > until -> 400."""
    from web.backend import main as backend_main
    from fastapi.testclient import TestClient

    class _FakePersistence:
        def get_cycle_duration_by_problem_size(self, **kwargs):
            return {}

    class _FakeCoord:
        persistence = _FakePersistence()

    monkeypatch.setattr(backend_main, "coordinator", _FakeCoord())
    client = TestClient(backend_main.app)
    resp = client.get(
        "/api/persistence/cycle-duration-by-problem-size"
        "?since_sim_day=10&until_sim_day=5"
    )
    assert resp.status_code == 400


def test_endpoint_503_when_no_coordinator(monkeypatch):
    """No coordinator -> 503."""
    from web.backend import main as backend_main
    from fastapi.testclient import TestClient

    monkeypatch.setattr(backend_main, "coordinator", None)
    client = TestClient(backend_main.app)
    resp = client.get("/api/persistence/cycle-duration-by-problem-size")
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def persistence():
    from agents.persistence import Persistence
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_dur_bps.db")
        p = Persistence(db_path)
        yield p