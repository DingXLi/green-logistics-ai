"""
iter #42: tests for forecast calibration endpoints and persistence methods.

Covers:
1. Persistence methods (pure logic):
   - record_forecast_predictions
   - backfill_forecast_actuals
   - get_forecast_calibration stats
2. /api/persistence/forecast-calibration HTTP endpoint
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
        db_path = os.path.join(tmpdir, "test_fcst_calib.db")
        p = Persistence(db_path)
        yield p


def _insert_cycle(p, cycle_id: str, sim_day: int,
                  cost_sek: float = 100.0, co2_kg: float = 50.0,
                  matches: int = 10, util_pct: float = 50.0):
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
# Persistence method tests
# ---------------------------------------------------------------------------


def test_record_forecast_predictions_basic(persistence):
    predictions = [
        {"sim_day": 5, "value": 100.0},
        {"sim_day": 6, "value": 105.0},
    ]
    n = persistence.record_forecast_predictions(
        metric="cost_sek", method="linear",
        predictions=predictions, created_at_sim_day=3,
    )
    assert n == 2
    assert persistence.count_forecast_predictions() == 2


def test_record_forecast_predictions_dedup(persistence):
    predictions = [{"sim_day": 5, "value": 100.0}]
    n1 = persistence.record_forecast_predictions(
        metric="cost_sek", method="linear",
        predictions=predictions, created_at_sim_day=3,
    )
    n2 = persistence.record_forecast_predictions(
        metric="cost_sek", method="linear",
        predictions=predictions, created_at_sim_day=3,
    )
    assert n1 == 1
    assert n2 == 0
    assert persistence.count_forecast_predictions() == 1


def test_record_forecast_predictions_different_method_not_dedup(persistence):
    predictions = [{"sim_day": 5, "value": 100.0}]
    n1 = persistence.record_forecast_predictions(
        metric="cost_sek", method="linear",
        predictions=predictions, created_at_sim_day=3,
    )
    n2 = persistence.record_forecast_predictions(
        metric="cost_sek", method="moving_average",
        predictions=predictions, created_at_sim_day=3,
    )
    assert n1 == 1
    assert n2 == 1
    assert persistence.count_forecast_predictions() == 2


def test_record_forecast_predictions_empty(persistence):
    n = persistence.record_forecast_predictions(
        metric="cost_sek", method="linear",
        predictions=[], created_at_sim_day=3,
    )
    assert n == 0


def test_backfill_forecast_actuals_basic(persistence):
    # Insert cycle at sim_day 5 with cost=100
    _insert_cycle(persistence, "OPT005", 5, cost_sek=100.0)
    # Predict 90 for sim_day 5
    persistence.record_forecast_predictions(
        metric="cost_sek", method="linear",
        predictions=[{"sim_day": 5, "value": 90.0}],
        created_at_sim_day=3,
    )
    # Predict 110 for sim_day 6 (no cycle yet, should stay NULL)
    persistence.record_forecast_predictions(
        metric="cost_sek", method="linear",
        predictions=[{"sim_day": 6, "value": 110.0}],
        created_at_sim_day=3,
    )
    n = persistence.backfill_forecast_actuals()
    assert n == 1
    stats = persistence.get_forecast_calibration(metric="cost_sek")
    assert stats["overall"]["n_evaluated"] == 1
    assert stats["overall"]["mae"] == 10.0  # |100 - 90|
    assert stats["overall"]["bias"] == 10.0  # actual - predicted = +10 (under-predicted)


def test_backfill_forecast_actuals_percentage(persistence):
    _insert_cycle(persistence, "OPT005", 5, cost_sek=200.0)
    persistence.record_forecast_predictions(
        metric="cost_sek", method="linear",
        predictions=[{"sim_day": 5, "value": 180.0}],
        created_at_sim_day=3,
    )
    persistence.backfill_forecast_actuals()
    stats = persistence.get_forecast_calibration()
    # abs_pct_error = |200 - 180| / 200 * 100 = 10%
    assert stats["overall"]["mape_pct"] == 10.0


def test_backfill_forecast_actuals_rmse(persistence):
    """RMSE test: errors = [+10, -10] → RMSE = sqrt((100+100)/2) = 10."""
    _insert_cycle(persistence, "OPT005", 5, cost_sek=100.0)
    _insert_cycle(persistence, "OPT006", 6, cost_sek=100.0)
    persistence.record_forecast_predictions(
        metric="cost_sek", method="linear",
        predictions=[{"sim_day": 5, "value": 90.0}, {"sim_day": 6, "value": 110.0}],
        created_at_sim_day=3,
    )
    persistence.backfill_forecast_actuals()
    stats = persistence.get_forecast_calibration()
    # errors: +10, -10 → mae=10, rmse=sqrt(200/2)=10, bias=0
    assert stats["overall"]["mae"] == 10.0
    assert stats["overall"]["rmse"] == 10.0
    assert stats["overall"]["bias"] == 0.0


def test_get_forecast_calibration_by_metric(persistence):
    _insert_cycle(persistence, "OPT005", 5, cost_sek=100.0, co2_kg=50.0)
    persistence.record_forecast_predictions(
        metric="cost_sek", method="linear",
        predictions=[{"sim_day": 5, "value": 90.0}],
        created_at_sim_day=3,
    )
    persistence.record_forecast_predictions(
        metric="co2_kg", method="linear",
        predictions=[{"sim_day": 5, "value": 40.0}],  # actual=50, error=+10
        created_at_sim_day=3,
    )
    persistence.backfill_forecast_actuals()
    stats = persistence.get_forecast_calibration()
    assert "cost_sek" in stats["by_metric"]
    assert "co2_kg" in stats["by_metric"]
    assert stats["by_metric"]["cost_sek"]["mae"] == 10.0
    assert stats["by_metric"]["co2_kg"]["mae"] == 10.0


def test_get_forecast_calibration_by_method(persistence):
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
    stats = persistence.get_forecast_calibration()
    assert "linear" in stats["by_method"]
    assert "moving_average" in stats["by_method"]
    # linear: actual=100, pred=90, err=+10
    assert stats["by_method"]["linear"]["mae"] == 10.0
    # moving_average: actual=100, pred=80, err=+20
    assert stats["by_method"]["moving_average"]["mae"] == 20.0


def test_get_forecast_calibration_empty(persistence):
    stats = persistence.get_forecast_calibration()
    assert stats["overall"]["n_evaluated"] == 0
    assert stats["overall"]["mae"] is None


def test_count_forecast_predictions_with_metric_filter(persistence):
    persistence.record_forecast_predictions(
        metric="cost_sek", method="linear",
        predictions=[{"sim_day": 5, "value": 100.0}],
        created_at_sim_day=3,
    )
    persistence.record_forecast_predictions(
        metric="co2_kg", method="linear",
        predictions=[{"sim_day": 5, "value": 50.0}],
        created_at_sim_day=3,
    )
    assert persistence.count_forecast_predictions() == 2
    assert persistence.count_forecast_predictions(metric="cost_sek") == 1
    assert persistence.count_forecast_predictions(metric="co2_kg") == 1


# ---------------------------------------------------------------------------
# HTTP endpoint tests
# ---------------------------------------------------------------------------


def test_calibration_endpoint_no_coordinator(monkeypatch):
    from web.backend import main as backend_main

    monkeypatch.setattr(backend_main, "coordinator", None)
    from fastapi.testclient import TestClient
    client = TestClient(backend_main.app)
    resp = client.get("/api/persistence/forecast-calibration")
    assert resp.status_code == 503


def test_calibration_endpoint_invalid_metric(monkeypatch):
    from web.backend import main as backend_main

    class _FakeCoord:
        persistence = object()

    monkeypatch.setattr(backend_main, "coordinator", _FakeCoord())
    from fastapi.testclient import TestClient
    client = TestClient(backend_main.app)
    resp = client.get("/api/persistence/forecast-calibration?metric=invalid")
    assert resp.status_code == 400


def test_calibration_endpoint_invalid_method(monkeypatch):
    from web.backend import main as backend_main

    class _FakeCoord:
        persistence = object()

    monkeypatch.setattr(backend_main, "coordinator", _FakeCoord())
    from fastapi.testclient import TestClient
    client = TestClient(backend_main.app)
    resp = client.get("/api/persistence/forecast-calibration?method=invalid")
    assert resp.status_code == 400


def test_calibration_endpoint_happy_path(monkeypatch):
    from web.backend import main as backend_main

    fake_stats = {
        "overall": {
            "n_evaluated": 5, "mae": 12.5, "rmse": 14.0, "mape_pct": 8.2,
            "bias": 3.0, "min_pct_err": 1.0, "max_pct_err": 15.0,
        },
        "by_metric": {
            "cost_sek": {"n_evaluated": 3, "mae": 10.0, "rmse": 11.0, "mape_pct": 7.0, "bias": 2.0, "min_pct_err": 1.0, "max_pct_err": 12.0},
        },
        "by_method": {
            "linear": {"n_evaluated": 3, "mae": 10.0, "rmse": 11.0, "mape_pct": 7.0, "bias": 2.0, "min_pct_err": 1.0, "max_pct_err": 12.0},
        },
        "by_metric_method": {},
    }

    class _FakePersistence:
        def backfill_forecast_actuals(self):
            return 1

        def get_forecast_calibration(self, metric=None, method=None):
            self.called_with = (metric, method)
            return fake_stats

        def count_forecast_predictions(self, metric=None):
            return 10

    class _FakeCoord:
        persistence = _FakePersistence()

    monkeypatch.setattr(backend_main, "coordinator", _FakeCoord())
    from fastapi.testclient import TestClient
    client = TestClient(backend_main.app)
    resp = client.get("/api/persistence/forecast-calibration?metric=cost_sek&method=linear")
    assert resp.status_code == 200
    data = resp.json()
    assert data["n_total_predictions"] == 10
    assert data["n_evaluated"] == 5
    assert data["n_pending"] == 5
    assert data["metric_filter"] == "cost_sek"
    assert data["method_filter"] == "linear"
    assert data["overall"]["mae"] == 12.5
    assert data["by_metric"]["cost_sek"]["mae"] == 10.0
