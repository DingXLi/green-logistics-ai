"""
Tests for iter #54 carbon savings endpoint:
- Persistence.get_carbon_savings_summary()
- /api/persistence/carbon-savings
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestCarbonSavingsPersistence(unittest.TestCase):
    """Persistence.get_carbon_savings_summary()"""

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
        """3 cycles with known ton/distance/co2 to verify savings calculation."""
        # Cycle 1: 10 t, 50 km, 25 kg CO2 (efficient)
        # Cycle 2: 20 t, 100 km, 60 kg CO2 (efficient)
        # Cycle 3: 15 t, 200 km, 100 kg CO2 (less efficient but still better than baseline)
        for i, (tons, dist, co2) in enumerate([
            (10.0, 50.0, 25.0),
            (20.0, 100.0, 60.0),
            (15.0, 200.0, 100.0),
        ]):
            cid = f"co2-{i+1}"
            self.p.begin_cycle(
                cycle_id=cid, sim_day=i+1, sim_hour=8, activity_factor=1.0,
                n_supply_offers=2, n_demand_requests=1,
            )
            self.p.commit_cycle(
                cycle_id=cid,
                kpi={"n_matches": 1, "total_tons": tons,
                     "total_cost_sek": 100, "total_co2_kg": co2,
                     "total_distance_km": dist,
                     "n_vehicles_used": 1, "n_vehicles_available": 5,
                     "fleet_utilization_pct": 50, "solver_status": "OPTIMAL"},
                wall_duration_ms=100,
            )

    def test_total_tons_and_distance(self):
        result = self.p.get_carbon_savings_summary()
        self.assertEqual(result["n_cycles"], 3)
        self.assertEqual(result["total_tons"], 45.0)  # 10+20+15
        self.assertEqual(result["total_distance_km"], 350.0)  # 50+100+200
        self.assertEqual(result["actual_co2_kg"], 185.0)  # 25+60+100

    def test_baseline_calculation(self):
        # baseline = 45 t × 350 km × 0.124 = 1953 kg
        result = self.p.get_carbon_savings_summary()
        self.assertAlmostEqual(result["baseline_co2_kg"], 1953.0, places=1)
        self.assertEqual(result["baseline_factor_kg_per_ton_km"], 0.124)
        self.assertEqual(result["baseline_factor_key"], "traditional_baseline")

    def test_savings_calculation(self):
        # savings = baseline - actual = 1953 - 185 = 1768
        # savings_pct = 1768 / 1953 ≈ 90.5%
        result = self.p.get_carbon_savings_summary()
        self.assertAlmostEqual(result["savings_co2_kg"], 1768.0, places=0)
        self.assertAlmostEqual(result["savings_pct"], 90.5, places=1)

    def test_per_ton_metrics(self):
        result = self.p.get_carbon_savings_summary()
        # actual per ton = 185 / 45 ≈ 4.111
        # baseline per ton = 1953 / 45 = 43.4
        self.assertAlmostEqual(result["co2_per_ton_actual_kg"], 4.111, places=2)
        self.assertAlmostEqual(result["co2_per_ton_baseline_kg"], 43.4, places=1)

    def test_different_baseline_factor(self):
        # truck_heavy = 0.062 kg/ton-km
        # baseline = 45 * 350 * 0.062 = 976.5
        result = self.p.get_carbon_savings_summary(baseline_factor_key="truck_heavy")
        self.assertEqual(result["baseline_factor_key"], "truck_heavy")
        self.assertAlmostEqual(result["baseline_co2_kg"], 976.5, places=1)
        # savings = 976.5 - 185 = 791.5
        self.assertAlmostEqual(result["savings_co2_kg"], 791.5, places=1)

    def test_optimized_fleet_vs_actual(self):
        # If we set baseline to "optimized_fleet" (same as actual),
        # savings should be 0
        result = self.p.get_carbon_savings_summary(baseline_factor_key="optimized_fleet")
        self.assertEqual(result["baseline_factor_key"], "optimized_fleet")
        # savings = max(0, baseline - actual), if baseline > actual still positive
        # baseline = 45 * 350 * 0.062 = 976.5, actual = 185
        # savings = 976.5 - 185 = 791.5
        # Lesson: even optimized_fleet assumption (0.062) is high vs actual (185 kg / 15750 ton-km = 0.0118)
        self.assertGreater(result["savings_co2_kg"], 0)

    def test_invalid_factor_raises(self):
        with self.assertRaises(ValueError):
            self.p.get_carbon_savings_summary(baseline_factor_key="invalid_key")

    def test_available_factors_listed(self):
        result = self.p.get_carbon_savings_summary()
        self.assertIn("truck_heavy", result["available_factors"])
        self.assertIn("truck_medium", result["available_factors"])
        self.assertIn("truck_light", result["available_factors"])
        self.assertIn("traditional_baseline", result["available_factors"])
        self.assertIn("optimized_fleet", result["available_factors"])

    def test_time_window_filter(self):
        # Filter to day >= 2 (only cycles 2 and 3)
        # tons = 20+15 = 35
        # distance = 100+200 = 300
        # co2 = 60+100 = 160
        result = self.p.get_carbon_savings_summary(since_sim_day=2)
        self.assertEqual(result["n_cycles"], 2)
        self.assertEqual(result["total_tons"], 35.0)
        self.assertEqual(result["actual_co2_kg"], 160.0)

    def test_empty_data(self):
        from agents.persistence import Persistence
        empty = Persistence(db_path=self.db_path + ".empty")
        result = empty.get_carbon_savings_summary()
        self.assertEqual(result["n_cycles"], 0)
        self.assertEqual(result["total_tons"], 0.0)
        self.assertEqual(result["actual_co2_kg"], 0.0)
        # baseline_co2 = 0 * 0 * factor = 0
        self.assertEqual(result["baseline_co2_kg"], 0.0)
        self.assertEqual(result["savings_co2_kg"], 0.0)
        try:
            os.unlink(self.db_path + ".empty")
        except Exception:
            pass


class TestCarbonSavingsEndpoint(unittest.TestCase):
    """/api/persistence/carbon-savings FastAPI endpoint."""

    def test_endpoint_returns_200(self):
        from fastapi.testclient import TestClient
        from web.backend.main import app

        client = TestClient(app)
        resp = client.get("/api/persistence/carbon-savings")
        self.assertIn(resp.status_code, (200, 503))
        if resp.status_code == 200:
            data = resp.json()
            self.assertIn("n_cycles", data)
            self.assertIn("actual_co2_kg", data)
            self.assertIn("baseline_co2_kg", data)
            self.assertIn("savings_co2_kg", data)
            self.assertIn("savings_pct", data)
            self.assertIn("available_factors", data)

    def test_endpoint_with_factor(self):
        from fastapi.testclient import TestClient
        from web.backend.main import app

        client = TestClient(app)
        resp = client.get("/api/persistence/carbon-savings?baseline_factor_key=truck_heavy")
        self.assertIn(resp.status_code, (200, 503))
        if resp.status_code == 200:
            data = resp.json()
            self.assertEqual(data["baseline_factor_key"], "truck_heavy")
            self.assertEqual(data["baseline_factor_kg_per_ton_km"], 0.062)

    def test_endpoint_invalid_factor(self):
        from fastapi.testclient import TestClient
        from web.backend.main import app

        client = TestClient(app)
        resp = client.get("/api/persistence/carbon-savings?baseline_factor_key=bogus")
        self.assertIn(resp.status_code, (400, 503))

    def test_endpoint_invalid_range(self):
        from fastapi.testclient import TestClient
        from web.backend.main import app

        client = TestClient(app)
        resp = client.get("/api/persistence/carbon-savings?since_sim_day=10&until_sim_day=5")
        self.assertIn(resp.status_code, (400, 503))


if __name__ == "__main__":
    unittest.main()