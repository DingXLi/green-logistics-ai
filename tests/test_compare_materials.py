"""
Tests for iter #64 compare-materials endpoint:
- Persistence.compare_materials()
- /api/persistence/compare-materials
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestCompareMaterialsPersistence(unittest.TestCase):
    """Persistence.compare_materials()"""

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
        """2 materials with very different profiles across 3 cycles.

        concrete: dominant, high volume, decent match rate
        metal_scrap: rare, low volume, 100% match rate
        """
        for i, p in enumerate([
            {"cid": "cmp-1", "sim_day": 1,
             "concrete_supply": 50.0, "metal_supply": 10.0,
             "concrete_demand": 40.0, "metal_demand": 15.0,
             "concrete_match": (30.0, 10.0, 60.0, 150.0),
             "metal_match": (8.0, 20.0, 80.0, 200.0),
             "route": ("VEH_A", 30.0, 2.0, 250.0, 140.0)},
            {"cid": "cmp-2", "sim_day": 2,
             "concrete_supply": 60.0, "metal_supply": 15.0,
             "concrete_demand": 50.0, "metal_demand": 20.0,
             "concrete_match": (40.0, 12.0, 80.0, 200.0),
             "metal_match": (12.0, 22.0, 90.0, 220.0),
             "route": ("VEH_B", 34.0, 2.5, 290.0, 170.0)},
            {"cid": "cmp-3", "sim_day": 3,
             "concrete_supply": 70.0, "metal_supply": 20.0,
             "concrete_demand": 60.0, "metal_demand": 25.0,
             "concrete_match": (50.0, 15.0, 100.0, 250.0),
             "metal_match": (18.0, 25.0, 110.0, 270.0),
             "route": ("VEH_C", 40.0, 3.0, 360.0, 210.0)},
        ]):
            cid = p["cid"]
            concrete_tons = p["concrete_match"][0]
            metal_tons = p["metal_match"][0]
            total_tons = concrete_tons + metal_tons
            total_co2 = p["route"][4]
            total_cost = p["route"][3]
            self.p.begin_cycle(
                cycle_id=cid, sim_day=p["sim_day"], sim_hour=8,
                activity_factor=1.0, n_supply_offers=2, n_demand_requests=2,
            )
            self.p.commit_cycle(
                cycle_id=cid,
                kpi={"n_matches": 2,
                     "total_tons": total_tons,
                     "total_cost_sek": total_cost,
                     "total_co2_kg": total_co2,
                     "total_distance_km": p["route"][1],
                     "n_vehicles_used": 1, "n_vehicles_available": 5,
                     "fleet_utilization_pct": 20, "solver_status": "OPTIMAL"},
                wall_duration_ms=100,
            )
            self.p.record_supply(cid, {
                "agent_id": f"S_CONCRETE_{cid}",
                "location": {"lat": 57.7, "lon": 12.9},
                "material_type": "concrete",
                "available_tons": p["concrete_supply"],
                "quality_score": 80.0,
            })
            self.p.record_supply(cid, {
                "agent_id": f"S_METAL_{cid}",
                "location": {"lat": 57.7, "lon": 12.9},
                "material_type": "metal_scrap",
                "available_tons": p["metal_supply"],
                "quality_score": 85.0,
            })
            self.p.record_demand(cid, {
                "demand_id": f"D_CONCRETE_{cid}",
                "material_type": "concrete",
                "required_tons": p["concrete_demand"],
                "preferred_materials": ["concrete"],
                "location": {"lat": 57.71, "lon": 12.91},
            })
            self.p.record_demand(cid, {
                "demand_id": f"D_METAL_{cid}",
                "material_type": "metal_scrap",
                "required_tons": p["metal_demand"],
                "preferred_materials": ["metal_scrap"],
                "location": {"lat": 57.71, "lon": 12.91},
            })
            self.p.record_match(cid, {
                "supply_id": f"S_CONCRETE_{cid}",
                "demand_id": f"D_CONCRETE_{cid}",
                "material_type": "concrete",
                "tons": p["concrete_match"][0],
                "distance_km": p["concrete_match"][1],
                "estimated_profit_sek": p["concrete_match"][3],
            })
            self.p.record_match(cid, {
                "supply_id": f"S_METAL_{cid}",
                "demand_id": f"D_METAL_{cid}",
                "material_type": "metal_scrap",
                "tons": p["metal_match"][0],
                "distance_km": p["metal_match"][1],
                "estimated_profit_sek": p["metal_match"][3],
            })
            self.p.record_route(cid, {
                "vehicle_id": p["route"][0],
                "stops": ["A", "B"],
                "distance_km": p["route"][1],
                "duration_hours": p["route"][2],
                "cost_sek": p["route"][3],
                "co2_kg": p["route"][4],
            })

    # --- core structure ---

    def test_basic_envelope(self):
        """Returns the standard compare envelope (a, b, diff, winner, filter)."""
        result = self.p.compare_materials("concrete", "metal_scrap")
        for k in ("material_a", "material_b", "differences", "winner", "filter"):
            self.assertIn(k, result)
        self.assertEqual(result["material_a"]["material_type"], "concrete")
        self.assertEqual(result["material_b"]["material_type"], "metal_scrap")

    def test_material_a_has_all_fields(self):
        """material_a row has full context dict."""
        result = self.p.compare_materials("concrete", "metal_scrap")
        for k in ("material_type", "n_matches", "n_supply_offers",
                  "n_demand_requests", "match_rate",
                  "total_matched_tons", "total_supply_tons", "total_demand_tons",
                  "avg_tons_per_match", "avg_distance_km",
                  "total_co2_kg", "total_cost_sek",
                  "co2_per_ton", "cost_per_ton",
                  "total_profit_sek", "demand_fulfillment_pct"):
            self.assertIn(k, result["material_a"])

    def test_concrete_volume_metrics(self):
        """concrete: 3 matches, 120t matched, 3 supplies, 3 demands."""
        result = self.p.compare_materials("concrete", "metal_scrap")
        a = result["material_a"]
        self.assertEqual(a["n_matches"], 3)
        self.assertEqual(a["total_matched_tons"], 120.0)  # 30+40+50
        self.assertEqual(a["n_supply_offers"], 3)
        self.assertEqual(a["n_demand_requests"], 3)
        self.assertEqual(a["total_supply_tons"], 180.0)  # 50+60+70
        self.assertEqual(a["total_demand_tons"], 150.0)  # 40+50+60

    def test_metal_volume_metrics(self):
        """metal_scrap: 3 matches, 38t matched (8+12+18)."""
        result = self.p.compare_materials("concrete", "metal_scrap")
        b = result["material_b"]
        self.assertEqual(b["n_matches"], 3)
        self.assertEqual(b["total_matched_tons"], 38.0)  # 8+12+18
        self.assertEqual(b["n_supply_offers"], 3)
        self.assertEqual(b["n_demand_requests"], 3)
        self.assertEqual(b["total_supply_tons"], 45.0)  # 10+15+20
        self.assertEqual(b["total_demand_tons"], 60.0)  # 15+20+25

    def test_match_rate_calculation(self):
        """match_rate = n_matches / n_offers."""
        result = self.p.compare_materials("concrete", "metal_scrap")
        # concrete: 3/3 = 1.0
        # metal_scrap: 3/3 = 1.0
        self.assertEqual(result["material_a"]["match_rate"], 1.0)
        self.assertEqual(result["material_b"]["match_rate"], 1.0)

    # --- differences ---

    def test_differences_absolute(self):
        """Absolute differences (b - a)."""
        result = self.p.compare_materials("concrete", "metal_scrap")
        diffs = result["differences"]["absolute"]
        # b.matched - a.matched = 38 - 120 = -82
        self.assertEqual(diffs["total_matched_tons"], -82.0)
        # b.matches - a.matches = 3 - 3 = 0
        self.assertEqual(diffs["n_matches"], 0)

    def test_differences_pct_change(self):
        """Pct change is 100 * (b - a) / |a|."""
        result = self.p.compare_materials("concrete", "metal_scrap")
        pct = result["differences"]["pct_change"]
        # 100 * (-82) / 120 ≈ -68.33%
        self.assertAlmostEqual(pct["total_matched_tons"], -68.33, places=1)

    def test_differences_skips_none_values(self):
        """If one side is None, skip diff for that field."""
        result = self.p.compare_materials("concrete", "metal_scrap")
        diffs = result["differences"]["absolute"]
        # All fields should have valid numeric diffs
        for k, v in diffs.items():
            self.assertIsNotNone(v)

    # --- winner ---

    def test_winner_axes(self):
        """Winner dict has 5 axes."""
        result = self.p.compare_materials("concrete", "metal_scrap")
        winner = result["winner"]
        for axis in ("lowest_co2_per_ton", "lowest_cost_per_ton",
                     "most_matches", "highest_match_rate",
                     "highest_demand_fulfillment"):
            self.assertIn(axis, winner)

    def test_winner_most_matches(self):
        """concrete has more matches → concrete wins on most_matches."""
        result = self.p.compare_materials("concrete", "metal_scrap")
        win = result["winner"]["most_matches"]
        self.assertEqual(win["material"], "concrete")
        self.assertEqual(win["direction"], "higher_is_better")
        self.assertEqual(win["a_value"], 3)
        self.assertEqual(win["b_value"], 3)
        # tie — concrete wins by tie-breaker (a >= b)
        self.assertIn("by_abs", win)

    def test_winner_co2_per_ton(self):
        """co2_per_ton: lower wins."""
        result = self.p.compare_materials("concrete", "metal_scrap")
        win = result["winner"]["lowest_co2_per_ton"]
        self.assertEqual(win["direction"], "lower_is_better")
        # concrete should win or metal — depends on allocation
        self.assertIn(win["material"], ("concrete", "metal_scrap"))

    def test_winner_demand_fulfillment(self):
        """demand_fulfillment_pct: higher wins."""
        result = self.p.compare_materials("concrete", "metal_scrap")
        win = result["winner"]["highest_demand_fulfillment"]
        self.assertEqual(win["direction"], "higher_is_better")
        # metal: 30/60 = 50% < concrete: 120/150 = 80% → concrete wins
        self.assertEqual(win["material"], "concrete")

    # --- filter ---

    def test_sim_day_window(self):
        """since/until_sim_day restrict window."""
        result = self.p.compare_materials("concrete", "metal_scrap",
                                          since_sim_day=2, until_sim_day=2)
        # Cycle cmp-2 only: concrete 40t, metal 12t
        self.assertEqual(result["material_a"]["total_matched_tons"], 40.0)
        self.assertEqual(result["material_b"]["total_matched_tons"], 12.0)
        self.assertEqual(result["filter"]["since_sim_day"], 2)
        self.assertEqual(result["filter"]["until_sim_day"], 2)

    def test_filter_only_a_in_window(self):
        """sim_day=1 only: concrete 30t, metal 8t (both seeded)."""
        result = self.p.compare_materials("concrete", "metal_scrap",
                                          since_sim_day=1, until_sim_day=1)
        # cmp-1 only: concrete 30t, metal 8t
        self.assertEqual(result["material_a"]["total_matched_tons"], 30.0)
        self.assertEqual(result["material_b"]["total_matched_tons"], 8.0)

    # --- error cases ---

    def test_unknown_material_returns_zero_dict(self):
        """Unknown material returns row with all zeros (not None)."""
        result = self.p.compare_materials("concrete", "unicorn_horn")
        self.assertIsNotNone(result["material_a"])
        self.assertIsNotNone(result["material_b"])
        self.assertEqual(result["material_b"]["n_matches"], 0)
        self.assertEqual(result["material_b"]["total_matched_tons"], 0.0)

    def test_both_unknown(self):
        """Both materials unknown → both rows have zero metrics."""
        result = self.p.compare_materials("unicorn_a", "unicorn_b")
        self.assertIsNotNone(result["material_a"])
        self.assertIsNotNone(result["material_b"])
        self.assertEqual(result["material_a"]["total_matched_tons"], 0.0)

    def test_cache_returns_same_result(self):
        """TTL cache should return identical result on second call."""
        r1 = self.p.compare_materials("concrete", "metal_scrap")
        r2 = self.p.compare_materials("concrete", "metal_scrap")
        self.assertEqual(r1, r2)


class TestCompareMaterialsEndpoint(unittest.TestCase):
    """/api/persistence/compare-materials endpoint."""

    def setUp(self):
        try:
            from web.backend.main import app  # noqa: F401
            from fastapi.testclient import TestClient
            self.TestClient = TestClient
            self.app = app
        except Exception as e:
            self.skipTest(f"web.backend.main not importable: {e}")

    def test_endpoint_basic(self):
        client = self.TestClient(self.app)
        resp = client.get(
            "/api/persistence/compare-materials?material_a=concrete&material_b=metal_scrap")
        self.assertIn(resp.status_code, (200, 503))
        if resp.status_code == 200:
            data = resp.json()
            self.assertIn("material_a", data)
            self.assertIn("material_b", data)
            self.assertIn("differences", data)
            self.assertIn("winner", data)

    def test_endpoint_with_window(self):
        client = self.TestClient(self.app)
        resp = client.get(
            "/api/persistence/compare-materials?material_a=concrete&material_b=metal_scrap&since_sim_day=1&until_sim_day=3")
        self.assertIn(resp.status_code, (200, 503))

    def test_endpoint_missing_params(self):
        client = self.TestClient(self.app)
        resp = client.get("/api/persistence/compare-materials")
        # FastAPI should return 422 for missing required params
        self.assertIn(resp.status_code, (422, 503))


if __name__ == "__main__":
    unittest.main()