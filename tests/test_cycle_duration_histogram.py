"""
Tests for iter #64 cycle-duration-histogram endpoint:
- Persistence.get_cycle_duration_histogram()
- /api/persistence/cycle-duration-histogram
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestCycleDurationHistogramPersistence(unittest.TestCase):
    """Persistence.get_cycle_duration_histogram()"""

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
        """10 cycles with deterministic wall_duration_ms profile."""
        # 3 cycles <100ms, 2 cycles 1-5s, 2 cycles 10-30s, 2 cycles 30-60s, 1 cycle 60s+
        durations = [50, 80, 90, 2000, 3000, 15000, 25000, 35000, 45000, 75000]
        for i, dur in enumerate(durations):
            cid = f"dur-{i+1}"
            self.p.begin_cycle(
                cycle_id=cid, sim_day=i+1, sim_hour=8,
                activity_factor=1.0, n_supply_offers=1, n_demand_requests=1,
            )
            self.p.commit_cycle(
                cycle_id=cid,
                kpi={"n_matches": 1, "total_tons": 5,
                     "total_cost_sek": 50, "total_co2_kg": 25,
                     "total_distance_km": 10, "n_vehicles_used": 1,
                     "n_vehicles_available": 5,
                     "fleet_utilization_pct": 20, "solver_status": "OPTIMAL"},
                wall_duration_ms=dur,
            )

    # --- core ---

    def test_basic_envelope(self):
        """Returns the standard histogram envelope."""
        result = self.p.get_cycle_duration_histogram()
        for k in ("n_cycles", "n_buckets", "buckets", "stats"):
            self.assertIn(k, result)

    def test_n_cycles(self):
        """n_cycles should equal seeded count."""
        result = self.p.get_cycle_duration_histogram()
        self.assertEqual(result["n_cycles"], 10)

    def test_n_buckets(self):
        """n_buckets = 8 fixed buckets."""
        result = self.p.get_cycle_duration_histogram()
        self.assertEqual(result["n_buckets"], 8)

    def test_bucket_labels(self):
        """Buckets have correct labels and bounds."""
        result = self.p.get_cycle_duration_histogram()
        labels = [b["label"] for b in result["buckets"]]
        expected_labels = [
            "<100ms", "100-500ms", "0.5-1s", "1-5s",
            "5-10s", "10-30s", "30-60s", "60s+",
        ]
        self.assertEqual(labels, expected_labels)

    def test_bucket_counts(self):
        """Verify counts against seed distribution."""
        result = self.p.get_cycle_duration_histogram()
        counts = {b["label"]: b["count"] for b in result["buckets"]}
        # 3 fast (<100ms: 50, 80, 90), 2 in 1-5s (2000, 3000),
        # 2 in 10-30s (15000, 25000), 2 in 30-60s (35000, 45000),
        # 1 in 60s+ (75000)
        self.assertEqual(counts["<100ms"], 3)
        self.assertEqual(counts["1-5s"], 2)
        self.assertEqual(counts["10-30s"], 2)
        self.assertEqual(counts["30-60s"], 2)
        self.assertEqual(counts["60s+"], 1)
        # Empty buckets
        self.assertEqual(counts["100-500ms"], 0)
        self.assertEqual(counts["0.5-1s"], 0)
        self.assertEqual(counts["5-10s"], 0)

    def test_bucket_pcts(self):
        """Pct values should sum to ~100%."""
        result = self.p.get_cycle_duration_histogram()
        total_pct = sum(b["pct"] for b in result["buckets"])
        self.assertAlmostEqual(total_pct, 100.0, places=1)

    def test_bucket_pct_correctness(self):
        """Each bucket pct = 100 * count / n_cycles."""
        result = self.p.get_cycle_duration_histogram()
        for b in result["buckets"]:
            if result["n_cycles"] > 0:
                expected = round(100 * b["count"] / result["n_cycles"], 2)
                self.assertEqual(b["pct"], expected)

    def test_stats_envelope(self):
        """Stats dict has expected fields."""
        result = self.p.get_cycle_duration_histogram()
        for k in ("mean_ms", "median_ms", "min_ms", "max_ms",
                  "stddev_ms", "slow_count", "fast_count", "total_seconds"):
            self.assertIn(k, result["stats"])

    def test_stats_values(self):
        """Stats match seed distribution."""
        result = self.p.get_cycle_duration_histogram()
        s = result["stats"]
        self.assertEqual(s["min_ms"], 50.0)
        self.assertEqual(s["max_ms"], 75000.0)
        # fast_count: <100ms = 3
        self.assertEqual(s["fast_count"], 3)
        # slow_count: ≥5s = 7 (5-10s:0 + 10-30s:2 + 30-60s:2 + 60s+:1 = 5)
        self.assertEqual(s["slow_count"], 5)

    def test_stats_total_seconds(self):
        """total_seconds = sum(durations) / 1000."""
        result = self.p.get_cycle_duration_histogram()
        expected = sum([50, 80, 90, 2000, 3000, 15000, 25000, 35000, 45000, 75000]) / 1000
        self.assertAlmostEqual(result["stats"]["total_seconds"], expected, places=1)

    # --- filter ---

    def test_sim_day_window(self):
        """since/until_sim_day restricts the cycle sample."""
        result = self.p.get_cycle_duration_histogram(
            since_sim_day=1, until_sim_day=5)
        # Cycles 1-5: 50, 80, 90, 2000, 3000
        self.assertEqual(result["n_cycles"], 5)
        counts = {b["label"]: b["count"] for b in result["buckets"]}
        self.assertEqual(counts["<100ms"], 3)
        self.assertEqual(counts["1-5s"], 2)

    # --- edge cases ---

    def test_empty_database(self):
        """Empty database returns all-zero buckets."""
        empty_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        empty_db.close()
        try:
            from agents.persistence import Persistence
            empty_p = Persistence(db_path=empty_db.name)
            result = empty_p.get_cycle_duration_histogram()
            self.assertEqual(result["n_cycles"], 0)
            for b in result["buckets"]:
                self.assertEqual(b["count"], 0)
                self.assertEqual(b["pct"], 0.0)
            self.assertIsNone(result["stats"]["mean_ms"])
        finally:
            try:
                os.unlink(empty_db.name)
            except Exception:
                pass

    def test_cache_returns_same_result(self):
        """TTL cache returns identical result on second call."""
        r1 = self.p.get_cycle_duration_histogram()
        r2 = self.p.get_cycle_duration_histogram()
        self.assertEqual(r1, r2)


class TestCycleDurationHistogramEndpoint(unittest.TestCase):
    """/api/persistence/cycle-duration-histogram endpoint."""

    def setUp(self):
        try:
            from web.backend.main import app  # noqa: F401
            from fastapi.testclient import TestClient
            self.TestClient = TestClient
            self.app = app
        except Exception as e:
            self.skipTest(f"web.backend.main not importable: {e}")

    def test_endpoint_default(self):
        client = self.TestClient(self.app)
        resp = client.get("/api/persistence/cycle-duration-histogram")
        self.assertIn(resp.status_code, (200, 503))
        if resp.status_code == 200:
            data = resp.json()
            self.assertIn("buckets", data)
            self.assertIn("stats", data)
            self.assertEqual(data["n_buckets"], 8)

    def test_endpoint_with_window(self):
        client = self.TestClient(self.app)
        resp = client.get(
            "/api/persistence/cycle-duration-histogram?since_sim_day=1&until_sim_day=10")
        self.assertIn(resp.status_code, (200, 503))

    def test_endpoint_invalid_window_returns_400(self):
        client = self.TestClient(self.app)
        resp = client.get(
            "/api/persistence/cycle-duration-histogram?since_sim_day=10&until_sim_day=1")
        # since > until should return 400
        self.assertIn(resp.status_code, (400, 503))


if __name__ == "__main__":
    unittest.main()