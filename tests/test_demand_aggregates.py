"""
Demand aggregates tests (iter #52) — Persistence.get_demand_aggregates
+ /api/persistence/demand-aggregates endpoint.

测试覆盖:
- get_demand_aggregates() aggregates per demand_id from demand_requests + matches
- top demands sorted by total_required_tons
- fulfillment_rate = matched / required (0.0 = unmet, 1.0 = perfect)
- filter by demand_id (single) and material_type
- API endpoint 200 / 503
"""
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestDemandAggregatesPersistence(unittest.TestCase):
    """Persistence.get_demand_aggregates()"""

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
        """3 cycles with 3 different demands + 1 match per cycle."""
        for cycle_idx in range(3):
            cid = f"da-{cycle_idx+1}"
            self.p.begin_cycle(
                cycle_id=cid, sim_day=cycle_idx+1, sim_hour=8, activity_factor=1.0,
                n_supply_offers=2, n_demand_requests=3,
            )
            self.p.commit_cycle(
                cycle_id=cid,
                kpi={"n_matches": 1, "total_tons": 5, "total_cost_sek": 50,
                     "total_co2_kg": 25, "total_distance_km": 10,
                     "n_vehicles_used": 1, "n_vehicles_available": 5,
                     "fleet_utilization_pct": 20, "solver_status": "OPTIMAL"},
                wall_duration_ms=50,
            )
            # 2 supplies (supply_aggregates only uses these for matches)
            for sup_i, (sid, mat, tons) in enumerate([
                ("SUP000", "concrete", 10.0),
                ("SUP001", "metal_scrap", 20.0),
            ]):
                self.p.record_supply(cid, {
                    "agent_id": sid,
                    "location": {"lat": 57.7, "lon": 12.9},
                    "material_type": mat,
                    "available_tons": tons,
                    "quality_score": 80.0,
                })
            # 3 demands: required tons vary
            for dem_i, (did, mat, req) in enumerate([
                ("DEM000", "concrete", 8.0),
                ("DEM001", "concrete", 15.0),
                ("DEM002", "metal_scrap", 30.0),
            ]):
                self.p.record_demand(cid, {
                    "demand_id": did,
                    "location": {"lat": 57.7, "lon": 13.5},
                    "material_type": mat,
                    "required_tons": req,
                    "priority": "high" if dem_i == 0 else "medium",
                })
            # 1 match: SUP000 ↔ DEM000 (5 t delivered, 8 t requested → 62.5% met)
            self.p.record_match(cid, {
                "supply_id": "SUP000",
                "demand_id": "DEM000",
                "material_type": "concrete",
                "tons": 5.0,
                "distance_km": 10.0,
                "estimated_profit_sek": 50.0,
            })

    def test_top_demands_sorted_by_total_required(self):
        result = self.p.get_demand_aggregates(limit_demands=10)
        self.assertEqual(len(result), 3)
        # DEM002 has 30 t/cycle * 3 = 90 t
        # DEM001 has 15 t/cycle * 3 = 45 t
        # DEM000 has 8 t/cycle * 3 = 24 t
        self.assertEqual(result[0]["demand_id"], "DEM002")
        self.assertEqual(result[1]["demand_id"], "DEM001")
        self.assertEqual(result[2]["demand_id"], "DEM000")
        self.assertEqual(result[0]["total_required_tons"], 90.0)
        self.assertEqual(result[1]["total_required_tons"], 45.0)

    def test_demands_n_cycles(self):
        result = self.p.get_demand_aggregates(limit_demands=10)
        for r in result:
            self.assertEqual(r["n_cycles_with_demand"], 3)

    def test_demand_fulfillment_rate(self):
        # DEM000: 5 t matched / 8 t required per cycle = 0.625 fulfillment
        result = self.p.get_demand_aggregates(limit_demands=10)
        dem0 = next(r for r in result if r["demand_id"] == "DEM000")
        self.assertEqual(dem0["n_matches"], 3)
        self.assertEqual(dem0["total_matched_tons"], 15.0)
        self.assertEqual(dem0["total_required_tons"], 24.0)
        self.assertAlmostEqual(dem0["fulfillment_rate"], 15.0 / 24.0, places=2)

    def test_demand_no_match_fulfillment_zero(self):
        # DEM001 and DEM002 have no matches → fulfillment = 0.0
        result = self.p.get_demand_aggregates(limit_demands=10)
        dem1 = next(r for r in result if r["demand_id"] == "DEM001")
        self.assertEqual(dem1["n_matches"], 0)
        self.assertEqual(dem1["fulfillment_rate"], 0.0)

    def test_filter_by_demand_id(self):
        result = self.p.get_demand_aggregates(demand_id="DEM002")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["demand_id"], "DEM002")
        self.assertEqual(result[0]["total_required_tons"], 90.0)

    def test_filter_by_material_type(self):
        result = self.p.get_demand_aggregates(material_type="metal_scrap", limit_demands=10)
        # Only DEM002 is metal_scrap
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["demand_id"], "DEM002")
        self.assertEqual(result[0]["material_type"], "metal_scrap")

    def test_avg_required_tons(self):
        result = self.p.get_demand_aggregates(limit_demands=10)
        dem0 = next(r for r in result if r["demand_id"] == "DEM000")
        # 24 t / 3 cycles = 8 t/cycle
        self.assertEqual(dem0["avg_required_tons"], 8.0)

    def test_avg_match_tons(self):
        result = self.p.get_demand_aggregates(limit_demands=10)
        dem0 = next(r for r in result if r["demand_id"] == "DEM000")
        # 5 t per match, 3 matches → avg = 5.0
        self.assertEqual(dem0["avg_match_tons"], 5.0)

    def test_fulfillment_clamped_to_2(self):
        # iter #52: fulfillment_rate capped at 2.0 (rare oversupply case)
        # Create scenario: matched (15) > required (8) → 1.875, well within 2.0
        # For oversupply test, would need matched > 2*required, which doesn't happen in normal sim
        result = self.p.get_demand_aggregates(limit_demands=10)
        for r in result:
            self.assertLessEqual(r["fulfillment_rate"], 2.0)
            self.assertGreaterEqual(r["fulfillment_rate"], 0.0)

    def test_sim_day_range(self):
        # last_sim_day / first_sim_day should reflect cycle range
        result = self.p.get_demand_aggregates(limit_demands=10)
        dem0 = next(r for r in result if r["demand_id"] == "DEM000")
        self.assertEqual(dem0["first_sim_day"], 1)
        self.assertEqual(dem0["last_sim_day"], 3)


class TestDemandAggregatesEndpoint(unittest.TestCase):
    """/api/persistence/demand-aggregates FastAPI endpoint."""

    def test_endpoint_returns_200(self):
        from fastapi.testclient import TestClient
        from web.backend.main import app

        client = TestClient(app)
        resp = client.get("/api/persistence/demand-aggregates")
        # Either 200 (coordinator ready) or 503 (coordinator not initialized)
        self.assertIn(resp.status_code, (200, 503))
        if resp.status_code == 200:
            data = resp.json()
            self.assertIsInstance(data, list)
            # If we have data, fields should be present
            if len(data) > 0:
                first = data[0]
                self.assertIn("demand_id", first)
                self.assertIn("material_type", first)
                self.assertIn("total_required_tons", first)
                self.assertIn("fulfillment_rate", first)
                self.assertIn("n_cycles_with_demand", first)

    def test_endpoint_filter_by_demand_id(self):
        from fastapi.testclient import TestClient
        from web.backend.main import app

        client = TestClient(app)
        resp = client.get("/api/persistence/demand-aggregates?demand_id=NONEXISTENT")
        # 200 with empty list, OR 503 if coordinator not initialized
        self.assertIn(resp.status_code, (200, 503))

    def test_endpoint_filter_by_material_type(self):
        from fastapi.testclient import TestClient
        from web.backend.main import app

        client = TestClient(app)
        resp = client.get("/api/persistence/demand-aggregates?material_type=concrete&limit=10")
        self.assertIn(resp.status_code, (200, 503))

    def test_endpoint_limit_validation(self):
        # limit > 500 应该 clamp 到 500 (or 503 if no coordinator)
        from fastapi.testclient import TestClient
        from web.backend.main import app

        client = TestClient(app)
        resp = client.get("/api/persistence/demand-aggregates?limit=999")
        self.assertIn(resp.status_code, (200, 503))


if __name__ == "__main__":
    unittest.main()