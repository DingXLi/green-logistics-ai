"""
iter #43: tests for /api/persistence/forecast-calibration/trend endpoint.

Covers:
1. Persistence.get_forecast_calibration_trend() pure logic:
   - Empty returns []
   - Single day cumulative
   - Multi-day with growing sample
   - Filters (metric, method)
2. /api/persistence/forecast-calibration/trend HTTP endpoint
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
        db_path = os.path.join(tmpdir, "test_fcst_trend.db")
        p = Persistence(db_path)
        yield p


def _insert_cycle(p, cycle_id, sim_day, **kwargs):
    cost_sek = kwargs.get("cost_sek", 100.0)
    co2_kg = kwargs.get("co2_kg", 50.0)
    matches = kwargs.get("matches", 10)
    util_pct = kwargs.get("util_pct", 50.0)
    with p._conn() as conn:
        conn.execute(
            """INSERT INTO optimization_cycles
               (cycle_id, sim_day, sim_hour, wall_timestamp,
                activity_factor, n_supply_offers, n_demand_requests, n_matches,
                total_tons, total_cost_sek, total_co2_kg, total_distance_km,
                n_vehicles_used, n_vehicles_available, fleet_utilization_pct,
                solver_status, wall_duration_ms)
               VALUES (?, ?, 0, '2026-09-03T00:00:00',
                       1.0, 1, 1, ?,
                       5.0, ?, ?, 20.0,
                       1, 1, ?,
                       'OPTIMAL', 100)""",
            (cycle_id, sim_day, matches, cost_sek, co2_kg, util_pct),
        )


# ---------------------------------------------------------------------------
# Pure persistence tests
# ---------------------------------------------------------------------------


def test_trend_empty(persistence):
    result = persistence.get_forecast_calibration_trend()
    assert result == []


def test_trend_single_day(persistence):
    _insert_cycle(persistence, "OPT005", 5, cost_sek=100.0)
    persistence.record_forecast_predictions(
        metric="cost_sek", method="linear",
        predictions=[{"sim_day": 5, "value": 90.0}],
        created_at_sim_day=3,
    )
    persistence.backfill_forecast_actuals()
    trend = persistence.get_forecast_calibration_trend()
    assert len(trend) == 1
    assert trend[0]["bucket_sim_day"] == 5
    assert trend[0]["n_evaluated"] == 1
    assert trend[0]["cumulative_mae"] == 10.0
    assert trend[0]["cumulative_bias"] == 10.0


def test_trend_multi_day_cumulative(persistence):
    """3 predictions across 3 days, each with different error."""
    _insert_cycle(persistence, "OPT005", 5, cost_sek=100.0)
    _insert_cycle(persistence, "OPT006", 6, cost_sek=100.0)
    _insert_cycle(persistence, "OPT007", 7, cost_sek=100.0)
    persistence.record_forecast_predictions(
        metric="cost_sek", method="linear",
        predictions=[
            {"sim_day": 5, "value": 90.0},   # err=+10
            {"sim_day": 6, "value": 110.0},  # err=-10
            {"sim_day": 7, "value": 100.0},  # err=0
        ],
        created_at_sim_day=3,
    )
    persistence.backfill_forecast_actuals()
    trend = persistence.get_forecast_calibration_trend()
    assert len(trend) == 3
    # After day 5: only 1 prediction (err=+10), mae=10, bias=10
    assert trend[0]["n_evaluated"] == 1
    assert trend[0]["cumulative_mae"] == 10.0
    assert trend[0]["cumulative_bias"] == 10.0
    # After day 6: 2 predictions, errors [+10, -10], mae=10, bias=0
    assert trend[1]["n_evaluated"] == 2
    assert trend[1]["cumulative_mae"] == 10.0
    assert trend[1]["cumulative_bias"] == 0.0
    # After day 7: 3 predictions, errors [+10, -10, 0], mae=20/3, bias=0
    assert trend[2]["n_evaluated"] == 3
    assert trend[2]["cumulative_mae"] == round(20/3, 4)
    assert trend[2]["cumulative_bias"] == 0.0


def test_trend_multiple_predictions_same_day(persistence):
    """2 predictions on day 5, 1 on day 6."""
    _insert_cycle(persistence, "OPT005", 5, cost_sek=100.0)
    _insert_cycle(persistence, "OPT006", 6, cost_sek=100.0)
    persistence.record_forecast_predictions(
        metric="cost_sek", method="linear",
        predictions=[{"sim_day": 5, "value": 90.0}],
        created_at_sim_day=3,
    )
    persistence.record_forecast_predictions(
        metric="cost_sek", method="moving_average",
        predictions=[{"sim_day": 5, "value": 80.0}],
        created_at_sim_day=3,
    )
    persistence.record_forecast_predictions(
        metric="cost_sek", method="linear",
        predictions=[{"sim_day": 6, "value": 95.0}],
        created_at_sim_day=4,
    )
    persistence.backfill_forecast_actuals()
    trend = persistence.get_forecast_calibration_trend()
    # Day 5 has 2 predictions, day 6 has 1
    assert len(trend) == 2
    assert trend[0]["n_evaluated"] == 2
    assert trend[1]["n_evaluated"] == 3


def test_trend_filter_by_metric(persistence):
    _insert_cycle(persistence, "OPT005", 5, cost_sek=100.0, co2_kg=50.0)
    persistence.record_forecast_predictions(
        metric="cost_sek", method="linear",
        predictions=[{"sim_day": 5, "value": 90.0}],
        created_at_sim_day=3,
    )
    persistence.record_forecast_predictions(
        metric="co2_kg", method="linear",
        predictions=[{"sim_day": 5, "value": 40.0}],
        created_at_sim_day=3,
    )
    persistence.backfill_forecast_actuals()
    trend_cost = persistence.get_forecast_calibration_trend(metric="cost_sek")
    trend_co2 = persistence.get_forecast_calibration_trend(metric="co2_kg")
    # Each metric has 1 prediction on day 5
    assert len(trend_cost) == 1
    assert len(trend_co2) == 1
    assert trend_cost[0]["cumulative_mae"] == 10.0
    assert trend_co2[0]["cumulative_mae"] == 10.0


def test_trend_filter_by_method(persistence):
    _insert_cycle(persistence, "OPT005", 5, cost_sek=100.0)
    persistence.record_forecast_predictions(
        metric="cost_sek", method="linear",
        predictions=[{"sim_day": 5, "value": 90.0}],
        created_at_sim_day=3,
    )
    persistence.record_forecast_predictions(
        metric="cost_sek", method="moving_average",
        predictions=[{"sim_day": 5, "value": 80.0}],
        created_at_sim_day=3,
    )
    persistence.backfill_forecast_actuals()
    trend_linear = persistence.get_forecast_calibration_trend(method="linear")
    trend_ma = persistence.get_forecast_calibration_trend(method="moving_average")
    assert len(trend_linear) == 1
    assert len(trend_ma) == 1
    # linear: err=+10, mae=10
    assert trend_linear[0]["cumulative_mae"] == 10.0
    # moving_average: err=+20, mae=20
    assert trend_ma[0]["cumulative_mae"] == 20.0


def test_trend_sorted_ascending(persistence):
    """Trend should be sorted by bucket_sim_day ASC."""
    _insert_cycle(persistence, "OPT003", 3, cost_sek=100.0)
    _insert_cycle(persistence, "OPT007", 7, cost_sek=100.0)
    _insert_cycle(persistence, "OPT005", 5, cost_sek=100.0)
    persistence.record_forecast_predictions(
        metric="cost_sek", method="linear",
        predictions=[
            {"sim_day": 7, "value": 90.0},
            {"sim_day": 3, "value": 95.0},
            {"sim_day": 5, "value": 100.0},
        ],
        created_at_sim_day=2,
    )
    persistence.backfill_forecast_actuals()
    trend = persistence.get_forecast_calibration_trend()
    days = [t["bucket_sim_day"] for t in trend]
    assert days == [3, 5, 7]


# ---------------------------------------------------------------------------
# HTTP endpoint tests
# ---------------------------------------------------------------------------


def test_trend_endpoint_no_coordinator(monkeypatch):
    from web.backend import main as backend_main
    from fastapi.testclient import TestClient

    monkeypatch.setattr(backend_main, "coordinator", None)
    client = TestClient(backend_main.app)
    resp = client.get("/api/persistence/forecast-calibration/trend")
    assert resp.status_code == 503


def test_trend_endpoint_invalid_metric(monkeypatch):
    from web.backend import main as backend_main
    from fastapi.testclient import TestClient

    class _FakeCoord:
        persistence = object()
    monkeypatch.setattr(backend_main, "coordinator", _FakeCoord())
    client = TestClient(backend_main.app)
    resp = client.get("/api/persistence/forecast-calibration/trend?metric=invalid")
    assert resp.status_code == 400


def test_trend_endpoint_invalid_method(monkeypatch):
    from web.backend import main as backend_main
    from fastapi.testclient import TestClient

    class _FakeCoord:
        persistence = object()
    monkeypatch.setattr(backend_main, "coordinator", _FakeCoord())
    client = TestClient(backend_main.app)
    resp = client.get("/api/persistence/forecast-calibration/trend?method=invalid")
    assert resp.status_code == 400


def test_trend_endpoint_happy_path(monkeypatch):
    from web.backend import main as backend_main
    from fastapi.testclient import TestClient

    fake_trend = [
        {"bucket_sim_day": 5, "n_evaluated": 1, "cumulative_mae": 10.0, "cumulative_rmse": 10.0, "cumulative_mape_pct": 10.0, "cumulative_bias": 10.0},
        {"bucket_sim_day": 6, "n_evaluated": 2, "cumulative_mae": 10.0, "cumulative_rmse": 10.0, "cumulative_mape_pct": 6.7, "cumulative_bias": 0.0},
        {"bucket_sim_day": 7, "n_evaluated": 3, "cumulative_mae": 6.67, "cumulative_rmse": 8.16, "cumulative_mape_pct": 4.4, "cumulative_bias": 0.0},
    ]

    class _FakePersistence:
        def backfill_forecast_actuals(self):
            return 0

        def get_forecast_calibration_trend(self, metric=None, method=None):
            return fake_trend

    class _FakeCoord:
        persistence = _FakePersistence()

    monkeypatch.setattr(backend_main, "coordinator", _FakeCoord())
    client = TestClient(backend_main.app)
    resp = client.get("/api/persistence/forecast-calibration/trend?metric=cost_sek&method=linear")
    assert resp.status_code == 200
    data = resp.json()
    assert data["n_buckets"] == 3
    assert len(data["trend"]) == 3
    assert data["metric_filter"] == "cost_sek"
    assert data["method_filter"] == "linear"
    # Verify trend is sorted ASC
    days = [t["bucket_sim_day"] for t in data["trend"]]
    assert days == [5, 6, 7]
    # Cumulative growth
    ns = [t["n_evaluated"] for t in data["trend"]]
    assert ns == [1, 2, 3]
