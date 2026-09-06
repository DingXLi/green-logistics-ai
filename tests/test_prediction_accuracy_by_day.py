"""
Tests for iter #58 prediction-accuracy-by-day endpoint:
- Persistence.get_prediction_accuracy_by_day()
- /api/persistence/prediction-accuracy-by-day
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestPredictionAccuracyByDay(unittest.TestCase):
    """Persistence.get_prediction_accuracy_by_day()"""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False, dir="/tmp")
        self.tmp.close()
        self.db_path = self.tmp.name
        from agents.persistence import Persistence
        self.p = Persistence(db_path=self.db_path)
        self._seed()

    def tearDown(self):
        try:
            os.unlink(self.db_path)
        except Exception:
            pass

    def _seed(self):
        """Seed 3 days of predictions across metrics and methods.

        Day 1 predictions (created_at_sim_day=1):
        - linear / cost_sek: forecast day 2 (lead 1), actual 100 vs forecast 90, error=10, ape=10%
        - linear / cost_sek: forecast day 8 (lead 7), actual 200 vs forecast 150, error=50, ape=25%
        - ma / co2_kg: forecast day 2 (lead 1), actual 50 vs forecast 55, error=-5, ape=10%
        Day 2 predictions (created_at_sim_day=2):
        - linear / cost_sek: forecast day 3 (lead 1), actual 110 vs forecast 100, error=10, ape≈9.09%
        - linear / co2_kg: forecast day 5 (lead 3), actual 60 vs forecast 50, error=10, ape≈16.67%
        - es / cost_sek: forecast day 4 (lead 2), actual 80 vs forecast 70, error=10, ape=12.5%
        Day 3 (created_at_sim_day=3):
        - linear / cost_sek: forecast day 4 (lead 1) — no actual yet (pending)
        """
        preds = [
            # day 1
            ("cost_sek", "linear", 1, 2, 90.0, 100.0, 10.0, 10.0),
            ("cost_sek", "linear", 1, 8, 150.0, 200.0, 50.0, 25.0),
            ("co2_kg", "moving_average", 1, 2, 55.0, 50.0, -5.0, 10.0),
            # day 2
            ("cost_sek", "linear", 2, 3, 100.0, 110.0, 10.0, 9.0909),
            ("co2_kg", "linear", 2, 5, 50.0, 60.0, 10.0, 16.6667),
            ("cost_sek", "exponential_smoothing", 2, 4, 70.0, 80.0, 10.0, 12.5),
            # day 3 — pending (no actual)
            ("cost_sek", "linear", 3, 4, 95.0, None, None, None),
        ]
        for metric, method, created, forecast, fval, aval, err, ape in preds:
            self.p.record_forecast_predictions(
                metric=metric,
                method=method,
                predictions=[{"sim_day": forecast, "value": fval}],
                created_at_sim_day=created,
            )
            if aval is not None:
                # Update the most recent row matching this metric/method/created/forecast
                self.p.backfill_forecast_actuals = lambda: None  # disable auto-call
                # Use the dedicated method instead
                from datetime import datetime
                now = datetime.now().isoformat()
                with self.p._conn() as conn:
                    conn.execute(
                        """UPDATE forecast_predictions
                           SET actual_value = ?, error = ?, abs_pct_error = ?
                           WHERE metric = ? AND method = ? AND forecast_sim_day = ?
                                 AND created_at_sim_day = ? AND actual_value IS NULL""",
                        (aval, err, ape, metric, method, forecast, created),
                    )

    def test_basic_structure(self):
        result = self.p.get_prediction_accuracy_by_day()
        self.assertIn("by_day", result)
        self.assertIn("overall", result)
        self.assertIn("lead_time_buckets", result)
        self.assertIn("day_range", result)

    def test_default_lead_time_buckets(self):
        result = self.p.get_prediction_accuracy_by_day()
        labels = [b["label"] for b in result["lead_time_buckets"]]
        self.assertEqual(labels, ["1-1d", "2-3d", "4-7d", "8-14d", "15-30d"])

    def test_by_day_count(self):
        result = self.p.get_prediction_accuracy_by_day()
        # We seeded 3 created_at_sim_day values (1, 2, 3)
        days = [r["created_at_sim_day"] for r in result["by_day"]]
        self.assertEqual(days, [1, 2, 3])

    def test_overall_counts(self):
        result = self.p.get_prediction_accuracy_by_day()
        overall = result["overall"]
        # 7 total predictions (6 evaluated + 1 pending)
        self.assertEqual(overall["n_predictions"], 7)
        self.assertEqual(overall["n_evaluated"], 6)
        self.assertEqual(overall["n_pending"], 1)

    def test_per_day_n_predictions(self):
        result = self.p.get_prediction_accuracy_by_day()
        by_day = {r["created_at_sim_day"]: r for r in result["by_day"]}
        # Day 1: 3 predictions, 3 evaluated
        self.assertEqual(by_day[1]["n_predictions"], 3)
        self.assertEqual(by_day[1]["n_evaluated"], 3)
        self.assertEqual(by_day[1]["n_pending"], 0)
        # Day 2: 3 predictions, 3 evaluated
        self.assertEqual(by_day[2]["n_predictions"], 3)
        self.assertEqual(by_day[2]["n_evaluated"], 3)
        # Day 3: 1 prediction, 0 evaluated, 1 pending
        self.assertEqual(by_day[3]["n_predictions"], 1)
        self.assertEqual(by_day[3]["n_evaluated"], 0)
        self.assertEqual(by_day[3]["n_pending"], 1)

    def test_per_day_overall_mape(self):
        result = self.p.get_prediction_accuracy_by_day()
        by_day = {r["created_at_sim_day"]: r for r in result["by_day"]}
        # Day 1: avg of (10, 25, 10) = 15%
        self.assertAlmostEqual(by_day[1]["overall_mape_pct"], 15.0, places=2)
        # Day 2: avg of (9.0909, 16.6667, 12.5) ≈ 12.752
        self.assertAlmostEqual(by_day[2]["overall_mape_pct"], 12.752, places=2)
        # Day 3: no evaluated predictions, mape is None
        self.assertIsNone(by_day[3]["overall_mape_pct"])

    def test_lead_time_breakdown(self):
        result = self.p.get_prediction_accuracy_by_day()
        by_day = {r["created_at_sim_day"]: r for r in result["by_day"]}
        day1 = by_day[1]
        # Day 1 has 2 cost_sek predictions: lead 1 (ape 10) + lead 7 (ape 25)
        # And 1 co2_kg: lead 1 (ape 10)
        # Bucket 1-1d: 2 predictions (both lead 1), mape = (10 + 10) / 2 = 10
        self.assertAlmostEqual(day1["by_lead_time"]["1-1d"]["mape_pct"], 10.0, places=2)
        self.assertEqual(day1["by_lead_time"]["1-1d"]["n_evaluated"], 2)
        # Bucket 4-7d: 1 prediction (lead 7), mape = 25
        self.assertAlmostEqual(day1["by_lead_time"]["4-7d"]["mape_pct"], 25.0, places=2)
        # Buckets 2-3d, 8-14d, 15-30d: no predictions
        self.assertEqual(day1["by_lead_time"]["2-3d"]["n_evaluated"], 0)
        self.assertEqual(day1["by_lead_time"]["8-14d"]["n_evaluated"], 0)
        self.assertEqual(day1["by_lead_time"]["15-30d"]["n_evaluated"], 0)

    def test_filter_by_metric(self):
        result = self.p.get_prediction_accuracy_by_day(metric="cost_sek")
        overall = result["overall"]
        # 5 cost_sek predictions (4 evaluated + 1 pending)
        self.assertEqual(overall["n_predictions"], 5)
        self.assertEqual(overall["n_evaluated"], 4)
        self.assertEqual(overall["n_pending"], 1)

    def test_filter_by_method(self):
        result = self.p.get_prediction_accuracy_by_day(method="exponential_smoothing")
        overall = result["overall"]
        # 1 es prediction
        self.assertEqual(overall["n_predictions"], 1)
        self.assertEqual(overall["n_evaluated"], 1)

    def test_day_range_filter(self):
        result = self.p.get_prediction_accuracy_by_day(
            since_created_day=2, until_created_day=2
        )
        # Only day 2
        self.assertEqual(len(result["by_day"]), 1)
        self.assertEqual(result["by_day"][0]["created_at_sim_day"], 2)

    def test_custom_lead_time_buckets(self):
        # Single big bucket covering all leads
        result = self.p.get_prediction_accuracy_by_day(
            lead_time_buckets=[(1, 30)]
        )
        labels = [b["label"] for b in result["lead_time_buckets"]]
        self.assertEqual(labels, ["1-30d"])
        by_day = {r["created_at_sim_day"]: r for r in result["by_day"]}
        # Day 1: all 3 should land in 1-30 bucket
        self.assertEqual(by_day[1]["by_lead_time"]["1-30d"]["n_evaluated"], 3)

    def test_invalid_bucket_raises(self):
        with self.assertRaises(ValueError):
            self.p.get_prediction_accuracy_by_day(
                lead_time_buckets=[(10, 5)]  # min > max
            )

    def test_empty_data(self):
        from agents.persistence import Persistence
        empty = Persistence(db_path=self.db_path + ".empty")
        result = empty.get_prediction_accuracy_by_day()
        self.assertEqual(result["by_day"], [])
        self.assertEqual(result["overall"]["n_predictions"], 0)
        self.assertEqual(result["overall"]["n_evaluated"], 0)
        try:
            os.unlink(self.db_path + ".empty")
        except Exception:
            pass

    def test_pending_predictions_tracked(self):
        result = self.p.get_prediction_accuracy_by_day()
        by_day = {r["created_at_sim_day"]: r for r in result["by_day"]}
        # Day 3 has 1 pending
        self.assertEqual(by_day[3]["n_pending"], 1)
        # MAE / MAPE / bias are None since no evaluated predictions
        self.assertIsNone(by_day[3]["overall_mape_pct"])
        self.assertIsNone(by_day[3]["overall_mae"])
        self.assertIsNone(by_day[3]["overall_bias"])

    def test_bias_negative_when_over_predicting(self):
        # Day 1 co2_kg: forecast 55 vs actual 50 → error -5 (over-predicted)
        # That prediction alone has bias = -5
        # In Day 1, 3 errors: [10, 50, -5] → bias = 55/3 ≈ 18.33
        result = self.p.get_prediction_accuracy_by_day()
        by_day = {r["created_at_sim_day"]: r for r in result["by_day"]}
        day1_bias = by_day[1]["overall_bias"]
        self.assertAlmostEqual(day1_bias, (10 + 50 + (-5)) / 3, places=4)


class TestPredictionAccuracyEndpoint(unittest.TestCase):
    """/api/persistence/prediction-accuracy-by-day endpoint behavior."""

    def setUp(self):
        try:
            from web.backend.main import app  # noqa: F401
            from fastapi.testclient import TestClient
            self.TestClient = TestClient
            self.app = app
        except Exception as e:  # pragma: no cover
            self.skipTest(f"web.backend.main not importable: {e}")

    def test_endpoint_invalid_metric_returns_400_or_503(self):
        client = self.TestClient(self.app)
        resp = client.get(
            "/api/persistence/prediction-accuracy-by-day?metric=invalid"
        )
        self.assertIn(resp.status_code, (400, 503))

    def test_endpoint_default(self):
        client = self.TestClient(self.app)
        resp = client.get("/api/persistence/prediction-accuracy-by-day")
        self.assertIn(resp.status_code, (200, 503))
        if resp.status_code == 200:
            data = resp.json()
            self.assertIn("by_day", data)
            self.assertIn("overall", data)
            self.assertIn("lead_time_buckets", data)

    def test_endpoint_invalid_buckets_format(self):
        client = self.TestClient(self.app)
        resp = client.get(
            "/api/persistence/prediction-accuracy-by-day?lead_time_buckets=abc"
        )
        self.assertIn(resp.status_code, (400, 503))

    def test_endpoint_valid_buckets_csv(self):
        client = self.TestClient(self.app)
        resp = client.get(
            "/api/persistence/prediction-accuracy-by-day?lead_time_buckets=1-1,2-7,8-30"
        )
        self.assertIn(resp.status_code, (200, 503))
        if resp.status_code == 200:
            data = resp.json()
            labels = [b["label"] for b in data["lead_time_buckets"]]
            self.assertEqual(labels, ["1-1d", "2-7d", "8-30d"])


if __name__ == "__main__":
    unittest.main()
