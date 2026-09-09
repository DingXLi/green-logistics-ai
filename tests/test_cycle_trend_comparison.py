"""
Tests for iter #65 cycle-trend-comparison endpoint:
- Persistence.cycle_trend_comparison()
- /api/persistence/cycle-trend-comparison
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestCycleTrendComparisonPersistence(unittest.TestCase):
    """Persistence.cycle_trend_comparison()"""

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
        """8 cycles with monotonically improving co2_per_ton."""
        # Cycle 1: 10t matched, 100kg CO2 → 10 kg/t (high)
        # Cycle 2: 10t matched, 90 kg CO2 → 9 kg/t
        # Cycle 3: 10t matched, 80 kg CO2 → 8 kg/t
        # Cycle 4: 10t matched, 70 kg CO2 → 7 kg/t
        # Cycle 5: 10t matched, 60 kg CO2 → 6 kg/t
        # Cycle 6: 10t matched, 50 kg CO2 → 5 kg/t
        # Cycle 7: 10t matched, 40 kg CO2 → 4 kg/t
        # Cycle 8: 10t matched, 30 kg CO2 → 3 kg/t (low)
        for i, co2 in enumerate([100, 90, 80, 70, 60, 50, 40, 30]):
            cid = f"trend-{i+1}"
            self.p.begin_cycle(
                cycle_id=cid, sim_day=i+1, sim_hour=8,
                activity_factor=1.0, n_supply_offers=2, n_demand_requests=1,
            )
            self.p.commit_cycle(
                cycle_id=cid,
                kpi={"n_matches": 1, "total_tons": 10,
                     "total_cost_sek": 100, "total_co2_kg": co2,
                     "total_distance_km": 50, "n_vehicles_used": 1,
                     "n_vehicles_available": 5,
                     "fleet_utilization_pct": 20, "solver_status": "OPTIMAL"},
                wall_duration_ms=100,
            )
            self.p.record_match(cid, {
                "supply_id": "S_" + cid, "demand_id": "D_" + cid,
                "material_type": "concrete", "tons": 10.0,
                "distance_km": 50.0, "estimated_profit_sek": 100.0,
            })

    # --- core ---

    def test_basic_envelope(self):
        """Returns standard trend envelope."""
        result = self.p.cycle_trend_comparison()
        for k in ("metric", "metric_description", "direction",
                  "window", "early", "late", "delta", "trend"):
            self.assertIn(k, result)

    def test_default_metric(self):
        """Default metric is co2_per_ton."""
        result = self.p.cycle_trend_comparison()
        self.assertEqual(result["metric"], "co2_per_ton")
        self.assertEqual(result["direction"], "lower_is_better")

    def test_default_windows(self):
        """Default early/late windows = 5 cycles each."""
        result = self.p.cycle_trend_comparison()
        self.assertEqual(result["early"]["n_cycles"], 5)
        self.assertEqual(result["late"]["n_cycles"], 5)

    def test_early_window_cycles(self):
        """Early window should be cycles 1-5 (co2 100→60)."""
        result = self.p.cycle_trend_comparison()
        early = result["early"]
        # Cycles 1-5: 100, 90, 80, 70, 60 → mean=80
        self.assertEqual(early["n_cycles"], 5)
        self.assertEqual(early["mean_value"], 8.0)
        self.assertIn("trend-1", early["cycle_ids"])
        self.assertIn("trend-5", early["cycle_ids"])

    def test_late_window_cycles(self):
        """Late window should be cycles 4-8 (co2 70→30)."""
        result = self.p.cycle_trend_comparison()
        late = result["late"]
        # Cycles 4-8: 70, 60, 50, 40, 30 → mean=50
        self.assertEqual(late["n_cycles"], 5)
        self.assertEqual(late["mean_value"], 5.0)
        self.assertIn("trend-4", late["cycle_ids"])
        self.assertIn("trend-8", late["cycle_ids"])

    def test_delta_and_pct_change(self):
        """Delta = late - early, pct_change = 100 * delta / early."""
        result = self.p.cycle_trend_comparison()
        # 5 - 8 = -3
        self.assertEqual(result["delta"]["absolute"], -3.0)
        # 100 * (-3) / 8 = -37.5%
        self.assertEqual(result["delta"]["pct_change"], -37.5)

    def test_trend_improving(self):
        """lower_is_better with negative delta > 5% → improving."""
        result = self.p.cycle_trend_comparison()
        self.assertEqual(result["trend"], "improving")

    # --- alternative metrics ---

    def test_metric_cost_per_ton(self):
        """Switch metric to cost_per_ton (constant in seed → stable)."""
        result = self.p.cycle_trend_comparison(metric="cost_per_ton")
        self.assertEqual(result["metric"], "cost_per_ton")
        # All cycles cost 100 SEK / 10t = 10 SEK/t → stable
        self.assertEqual(result["trend"], "stable")

    def test_metric_fleet_utilization(self):
        """Switch to fleet_utilization_pct (constant 20% → stable)."""
        result = self.p.cycle_trend_comparison(
            metric="fleet_utilization_pct")
        self.assertEqual(result["metric"], "fleet_utilization_pct")
        self.assertEqual(result["trend"], "stable")

    def test_metric_tons_per_cycle(self):
        """tons_per_cycle (constant 10 → stable)."""
        result = self.p.cycle_trend_comparison(metric="tons_per_cycle")
        self.assertEqual(result["trend"], "stable")

    def test_metric_match_rate_vs_offers(self):
        """match_rate_vs_offers: 1 match / 2 offers = 0.5 → stable."""
        result = self.p.cycle_trend_comparison(
            metric="match_rate_vs_offers")
        self.assertEqual(result["trend"], "stable")

    def test_metric_higher_is_better_improving(self):
        """higher_is_better metric with positive delta → improving."""
        # Build a separate DB where tons_per_cycle improves
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        try:
            from agents.persistence import Persistence
            p = Persistence(db_path=tmp.name)
            for i, tons in enumerate([5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 11.0, 12.0]):
                cid = f"hp-{i+1}"
                p.begin_cycle(cycle_id=cid, sim_day=i+1, sim_hour=8,
                              activity_factor=1.0, n_supply_offers=1,
                              n_demand_requests=1)
                p.commit_cycle(cycle_id=cid,
                               kpi={"n_matches": 1, "total_tons": tons,
                                    "total_cost_sek": 100, "total_co2_kg": 50,
                                    "total_distance_km": 50,
                                    "n_vehicles_used": 1,
                                    "n_vehicles_available": 5,
                                    "fleet_utilization_pct": 20,
                                    "solver_status": "OPTIMAL"},
                               wall_duration_ms=100)
                p.record_match(cid, {
                    "supply_id": "S_" + cid, "demand_id": "D_" + cid,
                    "material_type": "concrete", "tons": tons,
                    "distance_km": 50, "estimated_profit_sek": 100,
                })
            result = p.cycle_trend_comparison(metric="tons_per_cycle")
            # Early mean: (5+6+7+8+9)/5 = 7
            # Late mean: (8+9+10+11+12)/5 = 10
            # 10-7 = 3, 100*3/7 ≈ 42.86%
            self.assertEqual(result["trend"], "improving")
            self.assertAlmostEqual(result["delta"]["absolute"], 3.0, places=2)
        finally:
            try:
                os.unlink(tmp.name)
            except Exception:
                pass

    def test_metric_declining(self):
        """higher_is_better metric with negative delta → declining."""
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        try:
            from agents.persistence import Persistence
            p = Persistence(db_path=tmp.name)
            for i, tons in enumerate([15.0, 14.0, 13.0, 12.0, 11.0, 10.0, 9.0, 8.0]):
                cid = f"dn-{i+1}"
                p.begin_cycle(cycle_id=cid, sim_day=i+1, sim_hour=8,
                              activity_factor=1.0, n_supply_offers=1,
                              n_demand_requests=1)
                p.commit_cycle(cycle_id=cid,
                               kpi={"n_matches": 1, "total_tons": tons,
                                    "total_cost_sek": 100, "total_co2_kg": 50,
                                    "total_distance_km": 50,
                                    "n_vehicles_used": 1,
                                    "n_vehicles_available": 5,
                                    "fleet_utilization_pct": 20,
                                    "solver_status": "OPTIMAL"},
                               wall_duration_ms=100)
                p.record_match(cid, {
                    "supply_id": "S_" + cid, "demand_id": "D_" + cid,
                    "material_type": "concrete", "tons": tons,
                    "distance_km": 50, "estimated_profit_sek": 100,
                })
            result = p.cycle_trend_comparison(metric="tons_per_cycle")
            # Early mean: (15+14+13+12+11)/5 = 13
            # Late mean: (12+11+10+9+8)/5 = 10
            # 10-13 = -3, 100*-3/13 ≈ -23%
            self.assertEqual(result["trend"], "declining")
        finally:
            try:
                os.unlink(tmp.name)
            except Exception:
                pass

    # --- windows ---

    def test_custom_windows(self):
        """Custom early/late window sizes."""
        result = self.p.cycle_trend_comparison(early_window=3, late_window=2)
        self.assertEqual(result["early"]["n_cycles"], 3)
        self.assertEqual(result["late"]["n_cycles"], 2)
        # Early: cycles 1-3 (100, 90, 80) → mean=90 → mean_value=9
        self.assertEqual(result["early"]["mean_value"], 9.0)
        # Late: cycles 7-8 (40, 30) → mean=35 → mean_value=3.5
        self.assertEqual(result["late"]["mean_value"], 3.5)

    def test_sim_day_window(self):
        """since/until_sim_day restricts cycle window."""
        # Only consider cycles 2-6
        result = self.p.cycle_trend_comparison(
            since_sim_day=2, until_sim_day=6, early_window=2, late_window=2)
        # Cycles 2-6 (sim_day 2..6): 90, 80, 70, 60, 50
        # Early 2: 90, 80 → mean=85 → mean_value=8.5
        # Late 2: 60, 50 → mean=55 → mean_value=5.5
        self.assertEqual(result["early"]["mean_value"], 8.5)
        self.assertEqual(result["late"]["mean_value"], 5.5)

    # --- error cases ---

    def test_invalid_metric_raises(self):
        """Unknown metric raises ValueError."""
        with self.assertRaises(ValueError):
            self.p.cycle_trend_comparison(metric="invalid")

    def test_cache_returns_same_result(self):
        """TTL cache returns identical result on second call."""
        r1 = self.p.cycle_trend_comparison()
        r2 = self.p.cycle_trend_comparison()
        self.assertEqual(r1, r2)


class TestCycleTrendComparisonEndpoint(unittest.TestCase):
    """/api/persistence/cycle-trend-comparison endpoint."""

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
        resp = client.get("/api/persistence/cycle-trend-comparison")
        self.assertIn(resp.status_code, (200, 503))
        if resp.status_code == 200:
            data = resp.json()
            self.assertIn("early", data)
            self.assertIn("late", data)
            self.assertIn("delta", data)
            self.assertIn("trend", data)

    def test_endpoint_with_params(self):
        client = self.TestClient(self.app)
        resp = client.get(
            "/api/persistence/cycle-trend-comparison?early_window=3&late_window=3&metric=cost_per_ton")
        self.assertIn(resp.status_code, (200, 503))

    def test_endpoint_invalid_window_returns_400(self):
        client = self.TestClient(self.app)
        resp = client.get(
            "/api/persistence/cycle-trend-comparison?early_window=0")
        # 400 if persistence ready, 503 if not
        self.assertIn(resp.status_code, (400, 503))

    def test_endpoint_invalid_metric_returns_400(self):
        client = self.TestClient(self.app)
        resp = client.get(
            "/api/persistence/cycle-trend-comparison?metric=invalid")
        self.assertIn(resp.status_code, (400, 503))


if __name__ == "__main__":
    unittest.main()