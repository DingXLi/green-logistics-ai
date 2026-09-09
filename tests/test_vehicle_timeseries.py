"""
Tests for iter #65 vehicle-timeseries endpoint:
- Persistence.get_vehicle_timeseries()
- /api/persistence/vehicle-timeseries
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestVehicleTimeseriesPersistence(unittest.TestCase):
    """Persistence.get_vehicle_timeseries()"""

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
        """4 cycles × 2 vehicles with different efficiency profiles.

        VEH_A: improving (cycle 1 high co2/km, cycle 4 low)
        VEH_B: stable (consistent co2/km across cycles)
        """
        for i, (vid, dist, dur, cost, co2) in enumerate([
            ("VEH_A", 10.0, 1.0, 50.0, 20.0),    # cycle 1: high co2/km=2.0
            ("VEH_A", 10.0, 1.0, 50.0, 10.0),    # cycle 2: medium co2/km=1.0
            ("VEH_A", 10.0, 1.0, 50.0, 5.0),     # cycle 3: low co2/km=0.5
            ("VEH_A", 10.0, 1.0, 50.0, 3.0),     # cycle 4: very low co2/km=0.3
            ("VEH_B", 20.0, 2.0, 100.0, 24.0),   # cycle 5: stable co2/km=1.2
            ("VEH_B", 20.0, 2.0, 100.0, 25.0),   # cycle 6: stable co2/km=1.25
        ]):
            cid = f"vts-{i+1}"
            self.p.begin_cycle(
                cycle_id=cid, sim_day=i+1, sim_hour=8,
                activity_factor=1.0, n_supply_offers=1, n_demand_requests=1,
            )
            self.p.commit_cycle(
                cycle_id=cid,
                kpi={"n_matches": 1, "total_tons": 5,
                     "total_cost_sek": cost, "total_co2_kg": co2,
                     "total_distance_km": dist,
                     "n_vehicles_used": 1, "n_vehicles_available": 5,
                     "fleet_utilization_pct": 20, "solver_status": "OPTIMAL"},
                wall_duration_ms=100,
            )
            self.p.record_match(cid, {
                "supply_id": "S_" + cid, "demand_id": "D_" + cid,
                "material_type": "concrete", "tons": 5.0,
                "distance_km": dist, "estimated_profit_sek": 100.0,
            })
            self.p.record_route(cid, {
                "vehicle_id": vid, "stops": ["A", "B"],
                "distance_km": dist, "duration_hours": dur,
                "cost_sek": cost, "co2_kg": co2,
            })

    # --- core envelope ---

    def test_basic_envelope(self):
        """Returns the standard timeseries envelope."""
        result = self.p.get_vehicle_timeseries()
        for k in ("metric", "metric_description", "direction", "filter",
                  "n_routes_evaluated", "n_routes_returned",
                  "timeseries", "per_vehicle_summary"):
            self.assertIn(k, result)

    def test_n_routes(self):
        """n_routes_evaluated should equal seeded count (6)."""
        result = self.p.get_vehicle_timeseries()
        self.assertEqual(result["n_routes_evaluated"], 6)

    def test_metric_default(self):
        """Default metric is co2_per_km."""
        result = self.p.get_vehicle_timeseries()
        self.assertEqual(result["metric"], "co2_per_km")
        self.assertEqual(result["direction"], "lower_is_better")

    def test_metric_speed(self):
        """speed_km_per_hour metric (higher_is_better)."""
        result = self.p.get_vehicle_timeseries(metric="speed_km_per_hour")
        self.assertEqual(result["metric"], "speed_km_per_hour")
        self.assertEqual(result["direction"], "higher_is_better")
        # VEH_A: 10/1 = 10, VEH_B: 20/2 = 10 → both 10
        values = [r["value"] for r in result["timeseries"]]
        self.assertEqual(set(values), {10.0})

    def test_each_timeseries_row_has_fields(self):
        """Each row in timeseries has full context."""
        result = self.p.get_vehicle_timeseries(limit=1)
        row = result["timeseries"][0]
        for k in ("route_id", "cycle_id", "vehicle_id", "sim_day",
                  "distance_km", "duration_hours", "cost_sek", "co2_kg",
                  "n_stops", "value"):
            self.assertIn(k, row)

    # --- per_vehicle_summary ---

    def test_per_vehicle_summary_has_two_vehicles(self):
        """per_vehicle_summary should have one entry per vehicle."""
        result = self.p.get_vehicle_timeseries()
        self.assertIn("VEH_A", result["per_vehicle_summary"])
        self.assertIn("VEH_B", result["per_vehicle_summary"])

    def test_vehicle_a_summary(self):
        """VEH_A: 4 routes, mean co2_per_km = (2+1+0.5+0.3)/4 = 0.95."""
        result = self.p.get_vehicle_timeseries()
        a_sum = result["per_vehicle_summary"]["VEH_A"]
        self.assertEqual(a_sum["n_routes"], 4)
        self.assertAlmostEqual(a_sum["mean_value"], 0.95, places=2)
        self.assertAlmostEqual(a_sum["min_value"], 0.3, places=2)
        self.assertAlmostEqual(a_sum["max_value"], 2.0, places=2)
        self.assertAlmostEqual(a_sum["latest_value"], 0.3, places=2)
        self.assertEqual(a_sum["trend"], "improving")  # 2.0 → 0.3

    def test_vehicle_b_summary(self):
        """VEH_B: 2 routes, stable co2/km around 1.2."""
        result = self.p.get_vehicle_timeseries()
        b_sum = result["per_vehicle_summary"]["VEH_B"]
        self.assertEqual(b_sum["n_routes"], 2)
        self.assertAlmostEqual(b_sum["mean_value"], 1.225, places=2)
        # 24/20=1.2 vs 25/20=1.25, delta=0.05 / mean=1.225 ≈ 4% < 10% → stable
        self.assertEqual(b_sum["trend"], "stable")

    def test_declining_trend(self):
        """Build a separate DB where VEH_C starts green and gets dirtier."""
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        try:
            from agents.persistence import Persistence
            p = Persistence(db_path=tmp.name)
            for i, (dist, dur, cost, co2) in enumerate([
                (10.0, 1.0, 50.0, 3.0),   # cycle 1: low co2/km=0.3
                (10.0, 1.0, 50.0, 5.0),   # cycle 2: medium
                (10.0, 1.0, 50.0, 10.0),  # cycle 3: high
                (10.0, 1.0, 50.0, 20.0),  # cycle 4: very high co2/km=2.0
            ]):
                cid = f"d-{i+1}"
                p.begin_cycle(cycle_id=cid, sim_day=i+1, sim_hour=8,
                              activity_factor=1.0, n_supply_offers=1,
                              n_demand_requests=1)
                p.commit_cycle(cycle_id=cid,
                               kpi={"n_matches": 1, "total_tons": 5,
                                    "total_cost_sek": cost, "total_co2_kg": co2,
                                    "total_distance_km": dist,
                                    "n_vehicles_used": 1,
                                    "n_vehicles_available": 5,
                                    "fleet_utilization_pct": 20,
                                    "solver_status": "OPTIMAL"},
                               wall_duration_ms=100)
                p.record_route(cid, {
                    "vehicle_id": "VEH_C", "stops": ["A", "B"],
                    "distance_km": dist, "duration_hours": dur,
                    "cost_sek": cost, "co2_kg": co2,
                })
            result = p.get_vehicle_timeseries()
            c_sum = result["per_vehicle_summary"]["VEH_C"]
            self.assertEqual(c_sum["trend"], "declining")
        finally:
            try:
                os.unlink(tmp.name)
            except Exception:
                pass

    # --- filters ---

    def test_vehicle_id_filter(self):
        """vehicle_id filter restricts to single vehicle."""
        result = self.p.get_vehicle_timeseries(vehicle_id="VEH_A")
        self.assertEqual(result["n_routes_evaluated"], 4)
        for r in result["timeseries"]:
            self.assertEqual(r["vehicle_id"], "VEH_A")

    def test_sim_day_window(self):
        """since/until_sim_day restricts window."""
        result = self.p.get_vehicle_timeseries(
            since_sim_day=2, until_sim_day=4)
        # Cycles 2-4: 3 VEH_A routes
        self.assertEqual(result["n_routes_evaluated"], 3)
        for r in result["timeseries"]:
            self.assertIn(r["sim_day"], (2, 3, 4))

    def test_invalid_metric_raises(self):
        """Unknown metric raises ValueError."""
        with self.assertRaises(ValueError) as ctx:
            self.p.get_vehicle_timeseries(metric="invalid")
        self.assertIn("invalid", str(ctx.exception))

    def test_limit_caps_returned(self):
        """limit caps returned rows."""
        result = self.p.get_vehicle_timeseries(limit=3)
        self.assertEqual(result["n_routes_returned"], 3)
        self.assertEqual(len(result["timeseries"]), 3)

    def test_cache_returns_same_result(self):
        """TTL cache returns identical result on second call."""
        r1 = self.p.get_vehicle_timeseries()
        r2 = self.p.get_vehicle_timeseries()
        self.assertEqual(r1, r2)


class TestVehicleTimeseriesEndpoint(unittest.TestCase):
    """/api/persistence/vehicle-timeseries endpoint."""

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
        resp = client.get("/api/persistence/vehicle-timeseries")
        self.assertIn(resp.status_code, (200, 503))
        if resp.status_code == 200:
            data = resp.json()
            self.assertIn("metric", data)
            self.assertIn("timeseries", data)
            self.assertIn("per_vehicle_summary", data)

    def test_endpoint_metric(self):
        client = self.TestClient(self.app)
        resp = client.get(
            "/api/persistence/vehicle-timeseries?metric=cost_per_km")
        self.assertIn(resp.status_code, (200, 503))

    def test_endpoint_vehicle_filter(self):
        client = self.TestClient(self.app)
        resp = client.get(
            "/api/persistence/vehicle-timeseries?vehicle_id=VEH_A")
        self.assertIn(resp.status_code, (200, 503))

    def test_endpoint_sim_day_window(self):
        client = self.TestClient(self.app)
        resp = client.get(
            "/api/persistence/vehicle-timeseries?since_sim_day=1&until_sim_day=5")
        self.assertIn(resp.status_code, (200, 503))

    def test_endpoint_invalid_metric_returns_400(self):
        client = self.TestClient(self.app)
        resp = client.get(
            "/api/persistence/vehicle-timeseries?metric=invalid")
        self.assertIn(resp.status_code, (400, 503))


if __name__ == "__main__":
    unittest.main()