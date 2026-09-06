"""
Tests for iter #57 top-demands-by-fulfillment endpoint:
- Persistence.get_top_demands_by_fulfillment()
- /api/persistence/top-demands
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestTopDemandsPersistence(unittest.TestCase):
    """Persistence.get_top_demands_by_fulfillment()"""

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
        """Seed 4 cycles with 5 demands of varying fulfillment quality.

        Demand profile:
        - DEM_A: well-served (required 10t, matched 10t → fulfillment 1.0)
        - DEM_B: under-served (required 20t, matched 8t → fulfillment 0.4)
        - DEM_C: over-served (required 5t, matched 12t → fulfillment 2.4 → capped 2.0)
        - DEM_D: completely unmet (required 30t, matched 0t → fulfillment 0.0)
        - DEM_E: small + well-served (required 2t, matched 2t → fulfillment 1.0)
        """
        cycle_profiles = [
            # Cycle 1: DEM_A (10), DEM_B (20), DEM_C (5), DEM_D (30)
            {
                "cid": "dem-1",
                "demands": [
                    ("DEM_A", "concrete", 10.0),
                    ("DEM_B", "concrete", 20.0),
                    ("DEM_C", "metal_scrap", 5.0),
                    ("DEM_D", "concrete", 30.0),
                ],
                "matches": [
                    ("DEM_A", 10.0, 12.0, 60.0, 100.0),
                    ("DEM_B", 8.0, 25.0, 80.0, 150.0),
                    ("DEM_C", 7.0, 15.0, 100.0, 200.0),
                ],
            },
            # Cycle 2: DEM_A (10), DEM_B (20), DEM_C (5)
            {
                "cid": "dem-2",
                "demands": [
                    ("DEM_A", "concrete", 10.0),
                    ("DEM_B", "concrete", 20.0),
                    ("DEM_C", "metal_scrap", 5.0),
                ],
                "matches": [
                    ("DEM_A", 10.0, 12.0, 60.0, 100.0),
                    ("DEM_C", 5.0, 18.0, 110.0, 220.0),
                ],
            },
            # Cycle 3: DEM_E (2) — small + well-served
            {
                "cid": "dem-3",
                "demands": [("DEM_E", "metal_scrap", 2.0)],
                "matches": [("DEM_E", 2.0, 8.0, 30.0, 60.0)],
            },
            # Cycle 4: DEM_A only — DEM_A is fully met across all cycles
            {
                "cid": "dem-4",
                "demands": [("DEM_A", "concrete", 10.0)],
                "matches": [("DEM_A", 10.0, 12.0, 60.0, 100.0)],
            },
        ]

        for i, c in enumerate(cycle_profiles):
            self.p.begin_cycle(
                cycle_id=c["cid"], sim_day=i + 1, sim_hour=8, activity_factor=1.0,
                n_supply_offers=10, n_demand_requests=len(c["demands"]),
            )
            self.p.commit_cycle(
                cycle_id=c["cid"],
                kpi={"n_matches": len(c["matches"]),
                     "total_tons": sum(m[1] for m in c["matches"]),
                     "total_cost_sek": 100, "total_co2_kg": 50,
                     "total_distance_km": 100, "n_vehicles_used": 2,
                     "n_vehicles_available": 5, "fleet_utilization_pct": 40,
                     "solver_status": "OPTIMAL"},
                wall_duration_ms=100,
            )
            for did, mat, tons in c["demands"]:
                self.p.record_demand(c["cid"], {
                    "id": did,
                    "name": did,
                    "location": {"lat": 57.7, "lon": 12.9},
                    "material_type": mat,
                    "required_tons": tons,
                })
            for did, tons, dist, co2, cost in c["matches"]:
                self.p.record_match(c["cid"], {
                    "supply_id": f"SUP_{did}",
                    "demand_id": did,
                    "material_type": "concrete" if did != "DEM_C" and did != "DEM_E"
                                     else "metal_scrap",
                    "tons": tons,
                    "distance_km": dist,
                    "estimated_profit_sek": 50.0,
                })
                self.p.record_route(c["cid"], {
                    "vehicle_id": "VEH_1",
                    "stops": [],
                    "distance_km": dist,
                    "duration_hours": 1.0,
                    "cost_sek": cost,
                    "co2_kg": co2,
                })

    def test_valid_metric_required(self):
        with self.assertRaises(ValueError):
            self.p.get_top_demands_by_fulfillment(metric="invalid_metric")

    def test_fulfillment_rate_higher_better(self):
        result = self.p.get_top_demands_by_fulfillment(
            metric="fulfillment_rate", limit=10
        )
        self.assertEqual(result["metric"], "fulfillment_rate")
        self.assertEqual(result["direction"], "higher_is_better")
        # DEM_A: matched 30/required 40 = 0.75
        # DEM_E: matched 2/required 2 = 1.0
        # DEM_B: matched 8/required 40 = 0.2
        # DEM_C: matched 12/required 10 = 1.2 → top (capped at 2.0)
        # DEM_D: matched 0/required 30 = 0.0
        if result["top_demands"]:
            self.assertEqual(result["top_demands"][0]["demand_id"], "DEM_C")
            # Values should be descending
            values = [d["value"] for d in result["top_demands"]]
            self.assertEqual(values, sorted(values, reverse=True))

    def test_fulfillment_rate_caps_at_2(self):
        result = self.p.get_top_demands_by_fulfillment(
            metric="fulfillment_rate", limit=10
        )
        for d in result["top_demands"]:
            self.assertLessEqual(d["value"], 2.0)

    def test_unmet_demand_tons_lower_better(self):
        result = self.p.get_top_demands_by_fulfillment(
            metric="unmet_demand_tons", limit=10
        )
        self.assertEqual(result["direction"], "lower_is_better")
        # DEM_A: required 30, matched 30 → unmet 0 (top)
        # DEM_E: required 2, matched 2 → unmet 0
        # DEM_B: required 40, matched 8 → unmet 32
        # DEM_C: required 10, matched 12 → unmet 0
        # DEM_D: required 30, matched 0 → unmet 30
        if result["top_demands"]:
            # DEM_A, DEM_C, DEM_E all have unmet 0 (best)
            # DEM_D and DEM_B have positive unmet
            top_ids = {d["demand_id"] for d in result["top_demands"][:3]}
            self.assertIn("DEM_A", top_ids)
            # Bottom should be DEM_B with highest unmet
            self.assertEqual(result["top_demands"][-1]["demand_id"], "DEM_B")

    def test_total_matched_tons_higher_better(self):
        result = self.p.get_top_demands_by_fulfillment(
            metric="total_matched_tons", limit=10
        )
        self.assertEqual(result["direction"], "higher_is_better")
        # DEM_A: 30t matched (top)
        # DEM_C: 12t
        # DEM_B: 8t
        # DEM_E: 2t
        # DEM_D: 0t
        if result["top_demands"]:
            self.assertEqual(result["top_demands"][0]["demand_id"], "DEM_A")

    def test_match_rate_higher_better(self):
        result = self.p.get_top_demands_by_fulfillment(
            metric="match_rate", limit=10
        )
        self.assertEqual(result["direction"], "higher_is_better")
        # DEM_A: 3 matches / 3 cycles = 1.0
        # DEM_B: 1 match / 2 cycles = 0.5
        # DEM_C: 2 matches / 2 cycles = 1.0
        # DEM_D: 0/1 = 0
        # DEM_E: 1/1 = 1.0
        if result["top_demands"]:
            top_ids = [d["demand_id"] for d in result["top_demands"][:3]]
            self.assertIn("DEM_A", top_ids)

    def test_avg_match_distance_lower_better(self):
        result = self.p.get_top_demands_by_fulfillment(
            metric="avg_match_distance_km", limit=10
        )
        self.assertEqual(result["direction"], "lower_is_better")
        # DEM_D should NOT appear (no matches → no avg distance)
        ids = [d["demand_id"] for d in result["top_demands"]]
        self.assertNotIn("DEM_D", ids)

    def test_top_demand_basic_fields(self):
        result = self.p.get_top_demands_by_fulfillment(
            metric="fulfillment_rate", limit=5
        )
        for d in result["top_demands"]:
            self.assertIn("demand_id", d)
            self.assertIn("material_type", d)
            self.assertIn("value", d)
            self.assertIn("n_matches", d)
            self.assertIn("total_required_tons", d)
            self.assertIn("total_matched_tons", d)
            self.assertIn("fulfillment_rate", d)
            self.assertIn("avg_match_distance_km", d)

    def test_filter_by_material(self):
        result = self.p.get_top_demands_by_fulfillment(
            metric="fulfillment_rate", material_type="metal_scrap"
        )
        for d in result["top_demands"]:
            self.assertEqual(d["material_type"], "metal_scrap")
        ids = [d["demand_id"] for d in result["top_demands"]]
        self.assertIn("DEM_C", ids)
        self.assertIn("DEM_E", ids)

    def test_min_required_tons_filter(self):
        result = self.p.get_top_demands_by_fulfillment(
            metric="fulfillment_rate", min_required_tons=20.0, limit=10
        )
        # DEM_E (2t), DEM_C (10t) should be filtered out
        ids = [d["demand_id"] for d in result["top_demands"]]
        self.assertNotIn("DEM_E", ids)
        self.assertNotIn("DEM_C", ids)

    def test_limit_capped_at_100(self):
        result = self.p.get_top_demands_by_fulfillment(limit=200)
        self.assertLessEqual(len(result["top_demands"]), 100)

    def test_empty_data(self):
        from agents.persistence import Persistence
        empty = Persistence(db_path=self.db_path + ".empty")
        result = empty.get_top_demands_by_fulfillment(metric="fulfillment_rate")
        self.assertEqual(result["n_demands_evaluated"], 0)
        self.assertEqual(result["top_demands"], [])
        try:
            os.unlink(self.db_path + ".empty")
        except Exception:
            pass

    def test_n_demands_counts(self):
        result = self.p.get_top_demands_by_fulfillment(
            metric="fulfillment_rate", limit=10
        )
        # 5 demands total (DEM_A/B/C/D/E)
        self.assertEqual(result["n_demands_evaluated"], 5)
        self.assertEqual(result["n_demands_returned"], 5)


class TestTopDemandsEndpoint(unittest.TestCase):
    """/api/persistence/top-demands endpoint behavior."""

    def setUp(self):
        try:
            from web.backend.main import app  # noqa: F401
            from fastapi.testclient import TestClient
            self.TestClient = TestClient
            self.app = app
        except Exception as e:  # pragma: no cover
            self.skipTest(f"web.backend.main not importable: {e}")

    def test_endpoint_invalid_metric_returns_4xx(self):
        client = self.TestClient(self.app)
        resp = client.get("/api/persistence/top-demands?metric=invalid")
        self.assertIn(resp.status_code, (400, 503))

    def test_endpoint_default_metric(self):
        client = self.TestClient(self.app)
        resp = client.get("/api/persistence/top-demands")
        self.assertIn(resp.status_code, (200, 503))
        if resp.status_code == 200:
            data = resp.json()
            self.assertEqual(data["metric"], "fulfillment_rate")
            self.assertIn("top_demands", data)

    def test_endpoint_limit_param(self):
        client = self.TestClient(self.app)
        resp = client.get("/api/persistence/top-demands?limit=5")
        self.assertIn(resp.status_code, (200, 503))


if __name__ == "__main__":
    unittest.main()
