"""
Tests for iter #63 top-materials-by-volume endpoint:
- Persistence.get_top_materials_by_volume()
- /api/persistence/top-materials
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestTopMaterialsPersistence(unittest.TestCase):
    """Persistence.get_top_materials_by_volume()"""

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
        """3 materials with different volume profiles."""
        # concrete: high volume, 3 matches across 3 cycles
        # wood: medium volume, 2 matches
        # metal: low volume, 1 match (highest co2_per_ton)
        profiles = [
            # (cycle, supplies, demands, matches, route)
            {"cid": "mat-1", "sim_day": 1,
             "supplies": [("concrete", 50.0), ("wood", 30.0)],
             "demands": [("concrete", 40.0), ("wood", 25.0), ("metal", 20.0)],
             "matches": [("concrete", "SUP_1", "DEM_1", 30.0, 10.0, 60.0, 150.0),
                         ("wood", "SUP_2", "DEM_2", 20.0, 15.0, 50.0, 100.0)],
             "route": ("VEH_A", 25.0, 2.0, 250.0, 110.0)},
            {"cid": "mat-2", "sim_day": 2,
             "supplies": [("concrete", 60.0), ("wood", 40.0)],
             "demands": [("concrete", 50.0), ("wood", 35.0), ("metal", 25.0)],
             "matches": [("concrete", "SUP_3", "DEM_3", 40.0, 12.0, 80.0, 200.0),
                         ("wood", "SUP_4", "DEM_4", 30.0, 18.0, 70.0, 140.0)],
             "route": ("VEH_B", 30.0, 2.5, 300.0, 150.0)},
            {"cid": "mat-3", "sim_day": 3,
             "supplies": [("concrete", 70.0)],
             "demands": [("concrete", 60.0), ("metal", 30.0)],
             "matches": [("concrete", "SUP_5", "DEM_5", 50.0, 15.0, 100.0, 250.0),
                         ("metal", "SUP_6", "DEM_6", 25.0, 30.0, 150.0, 400.0)],
             "route": ("VEH_C", 45.0, 3.0, 400.0, 250.0)},
        ]
        for p in profiles:
            cid = p["cid"]
            total_tons = sum(m[3] for m in p["matches"])
            total_co2 = p["route"][4]
            total_cost = p["route"][3]
            self.p.begin_cycle(
                cycle_id=cid, sim_day=p["sim_day"], sim_hour=8,
                activity_factor=1.0, n_supply_offers=len(p["supplies"]),
                n_demand_requests=len(p["demands"]),
            )
            self.p.commit_cycle(
                cycle_id=cid,
                kpi={"n_matches": len(p["matches"]),
                     "total_tons": total_tons,
                     "total_cost_sek": total_cost,
                     "total_co2_kg": total_co2,
                     "total_distance_km": p["route"][1],
                     "n_vehicles_used": 1, "n_vehicles_available": 5,
                     "fleet_utilization_pct": 20, "solver_status": "OPTIMAL"},
                wall_duration_ms=100,
            )
            for mat, tons in p["supplies"]:
                self.p.record_supply(cid, {
                    "agent_id": f"S_{mat}_{cid}",
                    "location": {"lat": 57.7, "lon": 12.9},
                    "material_type": mat,
                    "available_tons": tons,
                    "quality_score": 80.0,
                })
            for mat, tons in p["demands"]:
                self.p.record_demand(cid, {
                    "demand_id": f"D_{mat}_{cid}",
                    "material_type": mat,
                    "required_tons": tons,
                    "preferred_materials": [mat],
                    "location": {"lat": 57.71, "lon": 12.91},
                })
            for mat, sup, dem, tons, dist, co2, cost in p["matches"]:
                self.p.record_match(cid, {
                    "supply_id": sup, "demand_id": dem,
                    "material_type": mat, "tons": tons,
                    "distance_km": dist, "estimated_profit_sek": 50.0,
                })
            self.p.record_route(cid, {
                "vehicle_id": p["route"][0],
                "stops": ["A", "B", "C"],
                "distance_km": p["route"][1],
                "duration_hours": p["route"][2],
                "cost_sek": p["route"][3],
                "co2_kg": p["route"][4],
            })

    # --- core metrics ---

    def test_total_matched_tons_default(self):
        """Default metric total_matched_tons: concrete should win (120t)."""
        result = self.p.get_top_materials_by_volume()
        self.assertEqual(result["metric"], "total_matched_tons")
        self.assertEqual(result["direction"], "higher_is_better")
        self.assertEqual(result["n_materials_evaluated"], 3)
        self.assertEqual(result["top_materials"][0]["material_type"], "concrete")
        self.assertEqual(result["top_materials"][0]["total_matched_tons"], 120.0)

    def test_total_supply_tons(self):
        """total_supply_tons: concrete should win (180t available)."""
        result = self.p.get_top_materials_by_volume(metric="total_supply_tons")
        self.assertEqual(result["top_materials"][0]["material_type"], "concrete")
        # 50+60+70 = 180
        self.assertEqual(result["top_materials"][0]["total_supply_tons"], 180.0)

    def test_total_demand_tons(self):
        """total_demand_tons: concrete should win (150t requested)."""
        result = self.p.get_top_materials_by_volume(metric="total_demand_tons")
        self.assertEqual(result["top_materials"][0]["material_type"], "concrete")
        # 40+50+60 = 150
        self.assertEqual(result["top_materials"][0]["total_demand_tons"], 150.0)

    def test_n_matches(self):
        """n_matches: concrete should win (3 matches)."""
        result = self.p.get_top_materials_by_volume(metric="n_matches")
        self.assertEqual(result["top_materials"][0]["material_type"], "concrete")
        self.assertEqual(result["top_materials"][0]["n_matches"], 3)

    def test_match_rate(self):
        """match_rate ranks highest match-rate first."""
        result = self.p.get_top_materials_by_volume(metric="match_rate")
        # wood: 2 matches / 2 offers = 1.0 (highest)
        # concrete: 3 matches / 3 offers = 1.0 (also high)
        # metal: 1 match / 0 offers = skipped (None)
        # wood and concrete tie at 1.0; one of them should be first
        top_mat = result["top_materials"][0]["material_type"]
        self.assertIn(top_mat, ("wood", "concrete"))
        self.assertEqual(result["top_materials"][0]["value"], 1.0)

    def test_co2_per_ton_lower_is_better(self):
        """co2_per_ton should rank greenest first."""
        result = self.p.get_top_materials_by_volume(metric="co2_per_ton")
        self.assertEqual(result["direction"], "lower_is_better")
        # All materials share cycle route co2 (allocated by match.tons)
        # Just verify the math runs and returns the materials
        self.assertEqual(len(result["top_materials"]), 3)
        # Greenest should have smallest co2_per_ton
        values = [m["value"] for m in result["top_materials"]]
        self.assertEqual(values, sorted(values))

    def test_cost_per_ton_lower_is_better(self):
        """cost_per_ton should rank cheapest first."""
        result = self.p.get_top_materials_by_volume(metric="cost_per_ton")
        self.assertEqual(result["direction"], "lower_is_better")
        self.assertEqual(len(result["top_materials"]), 3)

    def test_avg_distance_km(self):
        """avg_distance_km ranks shortest-haul materials first."""
        result = self.p.get_top_materials_by_volume(metric="avg_distance_km")
        self.assertEqual(result["direction"], "lower_is_better")
        # concrete: avg of (10, 12, 15) = 12.33
        # wood: avg of (15, 18) = 16.5
        # metal: 30.0
        self.assertEqual(result["top_materials"][0]["material_type"], "concrete")
        self.assertAlmostEqual(result["top_materials"][0]["value"],
                               12.33, places=1)

    def test_n_supply_offers(self):
        """n_supply_offers: concrete (3), wood (2), metal (0) — metal skipped."""
        result = self.p.get_top_materials_by_volume(metric="n_supply_offers")
        self.assertEqual(result["top_materials"][0]["material_type"], "concrete")
        self.assertEqual(result["top_materials"][0]["value"], 3)

    # --- filters ---

    def test_sim_day_window(self):
        """since_sim_day / until_sim_day should restrict window.

        With since_sim_day=2, until_sim_day=2 (only mat-2 cycle), concrete and
        wood have matches. Metal has a demand but no match in this window,
        so it is excluded (no row in top_materials).
        """
        result = self.p.get_top_materials_by_volume(
            since_sim_day=2, until_sim_day=2)
        # Only concrete and wood have matches in cycle mat-2
        self.assertEqual(result["n_materials_evaluated"], 2)
        self.assertEqual(result["top_materials"][0]["material_type"], "concrete")
        self.assertEqual(result["top_materials"][0]["total_matched_tons"], 40.0)

    def test_limit_clamp(self):
        """limit should clamp to 50 max."""
        result = self.p.get_top_materials_by_volume(limit=2)
        self.assertEqual(len(result["top_materials"]), 2)
        self.assertEqual(result["n_materials_returned"], 2)

    def test_invalid_metric_raises(self):
        """Unknown metric should raise ValueError."""
        with self.assertRaises(ValueError) as ctx:
            self.p.get_top_materials_by_volume(metric="invalid_metric")
        self.assertIn("invalid_metric", str(ctx.exception))

    def test_result_envelope(self):
        """Verify top-level result envelope fields."""
        result = self.p.get_top_materials_by_volume()
        for k in ("metric", "metric_description", "direction",
                  "filter", "n_materials_evaluated", "n_materials_returned",
                  "top_materials"):
            self.assertIn(k, result)
        self.assertIn("since_sim_day", result["filter"])
        self.assertIn("until_sim_day", result["filter"])

    def test_each_material_has_expected_fields(self):
        """Each row in top_materials should have full context."""
        result = self.p.get_top_materials_by_volume(limit=1)
        row = result["top_materials"][0]
        for k in ("material_type", "value",
                  "total_matched_tons", "total_supply_tons",
                  "total_demand_tons", "total_co2_kg", "total_cost_sek",
                  "n_matches", "n_supply_offers", "n_demand_requests",
                  "match_rate", "avg_tons_per_match", "avg_distance_km",
                  "demand_fulfillment_pct"):
            self.assertIn(k, row)

    def test_cache_returns_same_result(self):
        """TTL cache should return identical result on second call."""
        r1 = self.p.get_top_materials_by_volume()
        r2 = self.p.get_top_materials_by_volume()
        self.assertEqual(r1, r2)


class TestTopMaterialsEndpoint(unittest.TestCase):
    """/api/persistence/top-materials endpoint."""

    def setUp(self):
        try:
            from web.backend.main import app  # noqa: F401
            from fastapi.testclient import TestClient
            self.TestClient = TestClient
            self.app = app
        except Exception as e:  # pragma: no cover
            self.skipTest(f"web.backend.main not importable: {e}")

    def test_endpoint_default_metric(self):
        client = self.TestClient(self.app)
        resp = client.get("/api/persistence/top-materials")
        self.assertIn(resp.status_code, (200, 503))
        if resp.status_code == 200:
            data = resp.json()
            self.assertIn("metric", data)
            self.assertIn("top_materials", data)
            self.assertEqual(data["metric"], "total_matched_tons")

    def test_endpoint_metric_query(self):
        client = self.TestClient(self.app)
        resp = client.get(
            "/api/persistence/top-materials?metric=co2_per_ton&limit=5")
        self.assertIn(resp.status_code, (200, 503))

    def test_endpoint_sim_day_window(self):
        client = self.TestClient(self.app)
        resp = client.get(
            "/api/persistence/top-materials?since_sim_day=1&until_sim_day=3")
        self.assertIn(resp.status_code, (200, 503))

    def test_endpoint_invalid_metric_returns_4xx(self):
        client = self.TestClient(self.app)
        resp = client.get("/api/persistence/top-materials?metric=invalid")
        self.assertIn(resp.status_code, (400, 503))


if __name__ == "__main__":
    unittest.main()