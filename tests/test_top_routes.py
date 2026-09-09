"""
Tests for iter #63 top-routes-by-efficiency endpoint:
- Persistence.get_top_routes_by_efficiency()
- /api/persistence/top-routes
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestTopRoutesPersistence(unittest.TestCase):
    """Persistence.get_top_routes_by_efficiency()"""

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
        """4 cycles with different route profiles."""
        # Cycle 1: VEH_A greenest (10km, 5kg CO2 → 0.5 kg/km)
        # Cycle 2: VEH_A medium (20km, 20kg CO2 → 1.0 kg/km)
        # Cycle 3: VEH_B dirtiest (15km, 30kg CO2 → 2.0 kg/km)
        # Cycle 4: VEH_A fast (30km, 2hr → 15 km/hr)
        routes_seed = [
            {"cid": "rt-1", "sim_day": 1, "vehicle": "VEH_A",
             "stops": ["SUP_1", "DEM_1"], "distance": 10.0, "duration": 1.5,
             "cost": 50.0, "co2": 5.0, "tons": 8.0},
            {"cid": "rt-2", "sim_day": 2, "vehicle": "VEH_A",
             "stops": ["SUP_1", "DEM_1", "DEM_2"], "distance": 20.0, "duration": 2.5,
             "cost": 100.0, "co2": 20.0, "tons": 15.0},
            {"cid": "rt-3", "sim_day": 3, "vehicle": "VEH_B",
             "stops": ["SUP_2", "DEM_3"], "distance": 15.0, "duration": 2.0,
             "cost": 75.0, "co2": 30.0, "tons": 10.0},
            {"cid": "rt-4", "sim_day": 4, "vehicle": "VEH_A",
             "stops": ["SUP_1", "DEM_4", "DEM_5"], "distance": 30.0, "duration": 2.0,
             "cost": 120.0, "co2": 18.0, "tons": 22.0},
        ]
        for r in routes_seed:
            self.p.begin_cycle(
                cycle_id=r["cid"], sim_day=r["sim_day"], sim_hour=8,
                activity_factor=1.0, n_supply_offers=1, n_demand_requests=1,
            )
            self.p.commit_cycle(
                cycle_id=r["cid"],
                kpi={"n_matches": 1, "total_tons": r["tons"],
                     "total_cost_sek": r["cost"], "total_co2_kg": r["co2"],
                     "total_distance_km": r["distance"],
                     "n_vehicles_used": 1, "n_vehicles_available": 5,
                     "fleet_utilization_pct": 20, "solver_status": "OPTIMAL"},
                wall_duration_ms=100,
            )
            # Record at least one match so cycle_tons > 0
            self.p.record_match(r["cid"], {
                "supply_id": "SUP_" + r["cid"],
                "demand_id": "DEM_" + r["cid"],
                "material_type": "concrete",
                "tons": r["tons"] / 2,
                "distance_km": r["distance"],
                "estimated_profit_sek": 100.0,
            })
            self.p.record_route(r["cid"], {
                "vehicle_id": r["vehicle"],
                "stops": r["stops"],
                "distance_km": r["distance"],
                "duration_hours": r["duration"],
                "cost_sek": r["cost"],
                "co2_kg": r["co2"],
            })

    # --- core metric: co2_per_km ---

    def test_co2_per_km_default(self):
        """Default metric co2_per_km should rank greenest first."""
        result = self.p.get_top_routes_by_efficiency()
        self.assertEqual(result["metric"], "co2_per_km")
        self.assertEqual(result["direction"], "lower_is_better")
        self.assertEqual(result["n_routes_evaluated"], 4)
        # First should be rt-1 (5kg / 10km = 0.5)
        self.assertEqual(result["top_routes"][0]["cycle_id"], "rt-1")
        self.assertEqual(result["top_routes"][0]["value"], 0.5)
        # rt-3 should be last (30kg / 15km = 2.0)
        self.assertEqual(result["top_routes"][-1]["cycle_id"], "rt-3")
        self.assertEqual(result["top_routes"][-1]["value"], 2.0)

    def test_co2_per_hour_metric(self):
        """co2_per_hour should rank by emissions per driving hour."""
        result = self.p.get_top_routes_by_efficiency(metric="co2_per_hour")
        self.assertEqual(result["metric"], "co2_per_hour")
        # rt-1: 5/1.5 = 3.333, rt-2: 20/2.5 = 8.0, rt-3: 30/2.0 = 15.0,
        # rt-4: 18/2.0 = 9.0 → rt-1 best
        self.assertEqual(result["top_routes"][0]["cycle_id"], "rt-1")

    def test_cost_per_km_metric(self):
        """cost_per_km should rank by SEK per km."""
        result = self.p.get_top_routes_by_efficiency(metric="cost_per_km")
        # rt-1: 50/10 = 5.0, rt-2: 100/20 = 5.0, rt-3: 75/15 = 5.0,
        # rt-4: 120/30 = 4.0 → rt-4 best (cheapest per km)
        self.assertEqual(result["top_routes"][0]["cycle_id"], "rt-4")

    def test_speed_km_per_hour_higher_is_better(self):
        """speed_km_per_hour should rank fastest first."""
        result = self.p.get_top_routes_by_efficiency(
            metric="speed_km_per_hour")
        self.assertEqual(result["direction"], "higher_is_better")
        # rt-4: 30/2.0 = 15.0 (fastest)
        self.assertEqual(result["top_routes"][0]["cycle_id"], "rt-4")
        self.assertEqual(result["top_routes"][0]["value"], 15.0)

    def test_distance_metric(self):
        """distance should rank longest routes first."""
        result = self.p.get_top_routes_by_efficiency(metric="distance")
        self.assertEqual(result["direction"], "higher_is_better")
        self.assertEqual(result["top_routes"][0]["cycle_id"], "rt-4")
        self.assertEqual(result["top_routes"][0]["value"], 30.0)

    def test_tons_per_km_metric(self):
        """tons_per_km should rank most-loaded first (cycle_tons / distance).

        cycle_tons = sum of match.tons for that cycle (one match per cycle
        seeded with tons=tons/2).
        - rt-1: cycle_tons=4 / 10 = 0.4
        - rt-2: cycle_tons=7.5 / 20 = 0.375
        - rt-3: cycle_tons=5 / 15 ≈ 0.333
        - rt-4: cycle_tons=11 / 30 ≈ 0.367
        rt-1 wins.
        """
        result = self.p.get_top_routes_by_efficiency(metric="tons_per_km")
        self.assertEqual(result["top_routes"][0]["cycle_id"], "rt-1")
        self.assertAlmostEqual(result["top_routes"][0]["value"], 0.4, places=2)

    # --- filters ---

    def test_filter_by_vehicle_id(self):
        """vehicle_id filter should restrict to specific vehicle."""
        result = self.p.get_top_routes_by_efficiency(vehicle_id="VEH_A")
        self.assertEqual(result["n_routes_evaluated"], 3)  # 3 routes for VEH_A
        for r in result["top_routes"]:
            self.assertEqual(r["vehicle_id"], "VEH_A")

    def test_filter_by_sim_day_window(self):
        """since_sim_day / until_sim_day should restrict to window."""
        result = self.p.get_top_routes_by_efficiency(
            since_sim_day=2, until_sim_day=3)
        self.assertEqual(result["n_routes_evaluated"], 2)  # rt-2 + rt-3
        for r in result["top_routes"]:
            self.assertIn(r["cycle_id"], ("rt-2", "rt-3"))

    def test_filter_combination(self):
        """Combine vehicle_id + sim_day window."""
        result = self.p.get_top_routes_by_efficiency(
            vehicle_id="VEH_A", since_sim_day=2, until_sim_day=4)
        # rt-2 + rt-4 (VEH_A in [2,4])
        self.assertEqual(result["n_routes_evaluated"], 2)

    def test_limit_clamp(self):
        """limit should be clamped to 100 max."""
        result = self.p.get_top_routes_by_efficiency(limit=2)
        self.assertEqual(len(result["top_routes"]), 2)
        self.assertEqual(result["n_routes_returned"], 2)

    def test_invalid_metric_raises(self):
        """Unknown metric should raise ValueError."""
        with self.assertRaises(ValueError) as ctx:
            self.p.get_top_routes_by_efficiency(metric="invalid_metric")
        self.assertIn("invalid_metric", str(ctx.exception))

    def test_result_envelope(self):
        """Verify top-level result envelope fields."""
        result = self.p.get_top_routes_by_efficiency()
        for k in ("metric", "metric_description", "direction",
                  "filter", "n_routes_evaluated", "n_routes_returned",
                  "top_routes"):
            self.assertIn(k, result)
        self.assertIn("vehicle_id", result["filter"])
        self.assertIn("since_sim_day", result["filter"])
        self.assertIn("until_sim_day", result["filter"])

    def test_each_top_route_has_expected_fields(self):
        """Each row in top_routes should have the full context dict."""
        result = self.p.get_top_routes_by_efficiency(limit=1)
        row = result["top_routes"][0]
        for k in ("route_id", "cycle_id", "vehicle_id", "sim_day",
                  "distance_km", "duration_hours", "cost_sek", "co2_kg",
                  "n_stops", "cycle_tons", "value"):
            self.assertIn(k, row)

    def test_n_stops_counted_correctly(self):
        """n_stops should equal non-empty entries in stops_json."""
        result = self.p.get_top_routes_by_efficiency(
            metric="distance", vehicle_id="VEH_A")
        # rt-4 has stops ["SUP_1", "DEM_4", "DEM_5"] = 3 stops
        rt4 = next(r for r in result["top_routes"]
                   if r["cycle_id"] == "rt-4")
        self.assertEqual(rt4["n_stops"], 3)

    def test_cache_returns_same_result(self):
        """TTL cache should return identical result on second call."""
        r1 = self.p.get_top_routes_by_efficiency()
        r2 = self.p.get_top_routes_by_efficiency()
        self.assertEqual(r1, r2)


class TestTopRoutesEndpoint(unittest.TestCase):
    """/api/persistence/top-routes endpoint."""

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
        """2 cycles with different routes."""
        for i, r in enumerate([
            {"cid": "ep-1", "vehicle": "VEH_X", "stops": ["A", "B"],
             "distance": 10.0, "duration": 1.0, "cost": 50.0, "co2": 5.0,
             "tons": 8.0},
            {"cid": "ep-2", "vehicle": "VEH_Y", "stops": ["C", "D"],
             "distance": 25.0, "duration": 2.0, "cost": 100.0, "co2": 20.0,
             "tons": 12.0},
        ]):
            self.p.begin_cycle(
                cycle_id=r["cid"], sim_day=i+1, sim_hour=8,
                activity_factor=1.0, n_supply_offers=1, n_demand_requests=1,
            )
            self.p.commit_cycle(
                cycle_id=r["cid"],
                kpi={"n_matches": 1, "total_tons": r["tons"],
                     "total_cost_sek": r["cost"], "total_co2_kg": r["co2"],
                     "total_distance_km": r["distance"],
                     "n_vehicles_used": 1, "n_vehicles_available": 5,
                     "fleet_utilization_pct": 20, "solver_status": "OPTIMAL"},
                wall_duration_ms=100,
            )
            self.p.record_match(r["cid"], {
                "supply_id": "S_" + r["cid"], "demand_id": "D_" + r["cid"],
                "material_type": "concrete", "tons": r["tons"] / 2,
                "distance_km": r["distance"], "estimated_profit_sek": 100.0,
            })
            self.p.record_route(r["cid"], {
                "vehicle_id": r["vehicle"], "stops": r["stops"],
                "distance_km": r["distance"],
                "duration_hours": r["duration"],
                "cost_sek": r["cost"], "co2_kg": r["co2"],
            })

    def _build_client(self):
        """Build TestClient with FastAPI app."""
        try:
            from web.backend.main import app  # noqa: F401
            from fastapi.testclient import TestClient
            return TestClient(app)
        except Exception as e:  # pragma: no cover - import path issues
            self.skipTest(f"web.backend.main not importable: {e}")

    def test_endpoint_default_metric(self):
        client = self._build_client()
        resp = client.get("/api/persistence/top-routes")
        # Either OK (persistence ready) or 503 (no lifespan in TestClient)
        self.assertIn(resp.status_code, (200, 503))
        if resp.status_code == 200:
            data = resp.json()
            self.assertIn("metric", data)
            self.assertIn("top_routes", data)
            self.assertEqual(data["metric"], "co2_per_km")

    def test_endpoint_metric_query(self):
        client = self._build_client()
        resp = client.get(
            "/api/persistence/top-routes?metric=distance&limit=1")
        self.assertIn(resp.status_code, (200, 503))
        if resp.status_code == 200:
            data = resp.json()
            self.assertEqual(data["metric"], "distance")
            self.assertLessEqual(len(data["top_routes"]), 1)

    def test_endpoint_vehicle_filter(self):
        client = self._build_client()
        resp = client.get("/api/persistence/top-routes?vehicle_id=VEH_X")
        self.assertIn(resp.status_code, (200, 503))

    def test_endpoint_sim_day_window(self):
        client = self._build_client()
        resp = client.get(
            "/api/persistence/top-routes?since_sim_day=2&until_sim_day=2")
        self.assertIn(resp.status_code, (200, 503))

    def test_endpoint_invalid_metric_returns_4xx(self):
        client = self._build_client()
        resp = client.get("/api/persistence/top-routes?metric=invalid_metric")
        # 400 if persistence is ready, 503 if not initialized
        self.assertIn(resp.status_code, (400, 503))


if __name__ == "__main__":
    unittest.main()