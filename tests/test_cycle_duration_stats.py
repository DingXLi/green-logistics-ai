"""
Tests for iter #53 endpoints:
- /api/persistence/cycle-duration-stats (solver wall time distribution)
- /api/persistence/match-distance-buckets (match distance histogram)
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestCycleDurationStatsPersistence(unittest.TestCase):
    """Persistence.get_cycle_duration_stats()"""

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

    def _seed(self, durations_ms=(50, 100, 200, 500, 1000, 5000, 10000)):
        """Seed N cycles with given solver wall times."""
        for i, dur in enumerate(durations_ms):
            cid = f"dur-{i+1}"
            self.p.begin_cycle(
                cycle_id=cid, sim_day=i+1, sim_hour=8, activity_factor=1.0,
                n_supply_offers=2, n_demand_requests=1,
            )
            self.p.commit_cycle(
                cycle_id=cid,
                kpi={"n_matches": 1, "total_tons": 5, "total_cost_sek": 50,
                     "total_co2_kg": 25, "total_distance_km": 10,
                     "n_vehicles_used": 1, "n_vehicles_available": 5,
                     "fleet_utilization_pct": 20, "solver_status": "OPTIMAL"},
                wall_duration_ms=dur,
            )

    def test_basic_stats_present(self):
        result = self.p.get_cycle_duration_stats()
        self.assertIn("n_cycles", result)
        self.assertIn("mean_ms", result)
        self.assertIn("median_ms", result)
        self.assertIn("min_ms", result)
        self.assertIn("max_ms", result)
        self.assertIn("stddev_ms", result)
        self.assertEqual(result["n_cycles"], 7)

    def test_min_max_values(self):
        result = self.p.get_cycle_duration_stats()
        self.assertEqual(result["min_ms"], 50.0)
        self.assertEqual(result["max_ms"], 10000.0)

    def test_mean_calculation(self):
        # (50+100+200+500+1000+5000+10000)/7 = 16850/7 ≈ 2407.14
        result = self.p.get_cycle_duration_stats()
        self.assertAlmostEqual(result["mean_ms"], 2407.14, places=1)

    def test_percentiles_present(self):
        result = self.p.get_cycle_duration_stats()
        for p in ("p25_ms", "p50_ms", "p75_ms", "p90_ms", "p95_ms", "p99_ms"):
            self.assertIn(p, result)
            self.assertIsNotNone(result[p])

    def test_slow_fast_counts(self):
        # seed: 50, 100, 200, 500, 1000, 5000, 10000
        # slow (>= 5000): 5000, 10000 = 2
        # fast (<= 100): 50, 100 = 2
        result = self.p.get_cycle_duration_stats()
        self.assertEqual(result["slow_cycles_count"], 2)
        self.assertEqual(result["fast_cycles_count"], 2)

    def test_total_solver_time(self):
        # sum = 16850 ms = 16.85 s
        result = self.p.get_cycle_duration_stats()
        self.assertEqual(result["total_solver_time_seconds"], 16.85)

    def test_empty_data_returns_zeros(self):
        # new DB with no cycles
        from agents.persistence import Persistence
        empty = Persistence(db_path=self.db_path + ".empty")
        result = empty.get_cycle_duration_stats()
        self.assertEqual(result["n_cycles"], 0)
        self.assertIsNone(result["mean_ms"])
        self.assertIsNone(result["median_ms"])
        self.assertEqual(result["slow_cycles_count"], 0)
        self.assertEqual(result["fast_cycles_count"], 0)
        self.assertEqual(result["total_solver_time_seconds"], 0.0)
        try:
            os.unlink(self.db_path + ".empty")
        except Exception:
            pass

    def test_time_window_filter(self):
        # Filter to sim_day >= 4 (durations: 500, 1000, 5000, 10000)
        result = self.p.get_cycle_duration_stats(since_sim_day=4)
        self.assertEqual(result["n_cycles"], 4)
        self.assertEqual(result["min_ms"], 500.0)
        self.assertEqual(result["max_ms"], 10000.0)


class TestMatchDistanceBucketsPersistence(unittest.TestCase):
    """Persistence.get_match_distance_buckets()"""

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
        """3 cycles with various distances."""
        # Cycle 1: 1 match @ 3km (bucket 0-5)
        # Cycle 2: 2 matches @ 15km, 80km (buckets 10-25, 50-100)
        # Cycle 3: 1 match @ 250km (bucket 200-500)
        for cycle_idx, (dists, cid_suffix) in enumerate([
            ([3.0], "dist-1"),
            ([15.0, 80.0], "dist-2"),
            ([250.0], "dist-3"),
        ]):
            cid = cid_suffix
            self.p.begin_cycle(
                cycle_id=cid, sim_day=cycle_idx+1, sim_hour=8, activity_factor=1.0,
                n_supply_offers=2, n_demand_requests=2,
            )
            self.p.commit_cycle(
                cycle_id=cid,
                kpi={"n_matches": len(dists), "total_tons": 5*len(dists),
                     "total_cost_sek": 50*len(dists), "total_co2_kg": 25*len(dists),
                     "total_distance_km": sum(dists),
                     "n_vehicles_used": len(dists), "n_vehicles_available": 5,
                     "fleet_utilization_pct": 40, "solver_status": "OPTIMAL"},
                wall_duration_ms=100,
            )
            for j, d in enumerate(dists):
                self.p.record_match(cid, {
                    "supply_id": f"S{j}",
                    "demand_id": f"D{j}",
                    "material_type": "concrete",
                    "tons": 5.0,
                    "distance_km": d,
                    "estimated_profit_sek": 50.0,
                })

    def test_buckets_present(self):
        result = self.p.get_match_distance_buckets()
        self.assertIn("buckets", result)
        self.assertEqual(len(result["buckets"]), 8)

    def test_bucket_labels(self):
        result = self.p.get_match_distance_buckets()
        labels = [b["label"] for b in result["buckets"]]
        self.assertEqual(labels, ["0-5", "5-10", "10-25", "25-50",
                                  "50-100", "100-200", "200-500", "500+"])

    def test_bucket_counts(self):
        # 4 matches total: 3km, 15km, 80km, 250km
        # 0-5: 1 (3km)
        # 10-25: 1 (15km)
        # 50-100: 1 (80km)
        # 200-500: 1 (250km)
        result = self.p.get_match_distance_buckets()
        counts = {b["label"]: b["count"] for b in result["buckets"]}
        self.assertEqual(counts["0-5"], 1)
        self.assertEqual(counts["10-25"], 1)
        self.assertEqual(counts["50-100"], 1)
        self.assertEqual(counts["200-500"], 1)
        self.assertEqual(counts["5-10"], 0)
        self.assertEqual(counts["25-50"], 0)

    def test_total_matches(self):
        result = self.p.get_match_distance_buckets()
        self.assertEqual(result["total_matches"], 4)
        # 3+15+80+250 = 348
        self.assertEqual(result["total_distance_km"], 348.0)
        self.assertEqual(result["avg_distance_km"], 87.0)

    def test_bucket_shares_sum_to_one(self):
        result = self.p.get_match_distance_buckets()
        shares = sum(b["share"] for b in result["buckets"])
        self.assertAlmostEqual(shares, 1.0, places=2)

    def test_500_plus_bucket(self):
        # Add a match at 600km (bucket 500+)
        self.p.begin_cycle(
            cycle_id="dist-4", sim_day=4, sim_hour=8, activity_factor=1.0,
            n_supply_offers=1, n_demand_requests=1,
        )
        self.p.commit_cycle(
            cycle_id="dist-4",
            kpi={"n_matches": 1, "total_tons": 5, "total_cost_sek": 50,
                 "total_co2_kg": 25, "total_distance_km": 600,
                 "n_vehicles_used": 1, "n_vehicles_available": 5,
                 "fleet_utilization_pct": 20, "solver_status": "OPTIMAL"},
            wall_duration_ms=100,
        )
        self.p.record_match("dist-4", {
            "supply_id": "S4", "demand_id": "D4",
            "material_type": "concrete", "tons": 5.0,
            "distance_km": 600.0, "estimated_profit_sek": 50.0,
        })
        result = self.p.get_match_distance_buckets()
        counts = {b["label"]: b["count"] for b in result["buckets"]}
        self.assertEqual(counts["500+"], 1)
        # upper_km for 500+ should be None (infinity marker)
        last = result["buckets"][-1]
        self.assertIsNone(last["upper_km"])

    def test_median_and_p95(self):
        result = self.p.get_match_distance_buckets()
        # Sorted distances: [3, 15, 80, 250]
        # median (p50) = (15+80)/2 = 47.5
        self.assertEqual(result["median_distance_km"], 47.5)
        self.assertIsNotNone(result["p95_distance_km"])


class TestCycleDurationStatsEndpoint(unittest.TestCase):
    """/api/persistence/cycle-duration-stats FastAPI endpoint."""

    def test_endpoint_returns_200(self):
        from fastapi.testclient import TestClient
        from web.backend.main import app

        client = TestClient(app)
        resp = client.get("/api/persistence/cycle-duration-stats")
        self.assertIn(resp.status_code, (200, 503))
        if resp.status_code == 200:
            data = resp.json()
            self.assertIn("n_cycles", data)
            self.assertIn("mean_ms", data)
            self.assertIn("slow_cycles_count", data)

    def test_endpoint_invalid_range(self):
        from fastapi.testclient import TestClient
        from web.backend.main import app

        client = TestClient(app)
        resp = client.get("/api/persistence/cycle-duration-stats?since_sim_day=10&until_sim_day=5")
        self.assertIn(resp.status_code, (400, 503))


class TestMatchDistanceBucketsEndpoint(unittest.TestCase):
    """/api/persistence/match-distance-buckets FastAPI endpoint."""

    def test_endpoint_returns_200(self):
        from fastapi.testclient import TestClient
        from web.backend.main import app

        client = TestClient(app)
        resp = client.get("/api/persistence/match-distance-buckets")
        self.assertIn(resp.status_code, (200, 503))
        if resp.status_code == 200:
            data = resp.json()
            self.assertIn("buckets", data)
            self.assertIn("total_matches", data)
            self.assertIsInstance(data["buckets"], list)
            self.assertEqual(len(data["buckets"]), 8)

    def test_endpoint_invalid_range(self):
        from fastapi.testclient import TestClient
        from web.backend.main import app

        client = TestClient(app)
        resp = client.get("/api/persistence/match-distance-buckets?since_sim_day=10&until_sim_day=5")
        self.assertIn(resp.status_code, (400, 503))


if __name__ == "__main__":
    unittest.main()