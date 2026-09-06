"""
Tests for iter #55 top-suppliers-by-efficiency endpoint:
- Persistence.get_top_suppliers_by_efficiency()
- /api/persistence/top-suppliers
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestTopSuppliersPersistence(unittest.TestCase):
    """Persistence.get_top_suppliers_by_efficiency()"""

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
        """3 cycles with different supply/demand profiles."""
        # Cycle 1: SUP_A (10t), SUP_B (5t), 1 match SUP_A↔DEM_1
        # Cycle 2: SUP_A (12t), SUP_B (8t), 2 matches
        # Cycle 3: SUP_A (15t), SUP_C (20t), 1 match
        # SUP_A: most matches, most consistent
        # SUP_B: 2 matches but smaller
        # SUP_C: 1 match
        for i, cycle_data in enumerate([
            {"cid": "sup-1", "supplies": [("SUP_A", "concrete", 10.0),
                                            ("SUP_B", "concrete", 5.0)],
             "matches": [("SUP_A", 5.0, 10.0, 25.0, 50.0)]},
            {"cid": "sup-2", "supplies": [("SUP_A", "concrete", 12.0),
                                            ("SUP_B", "concrete", 8.0)],
             "matches": [("SUP_A", 8.0, 20.0, 50.0, 100.0),
                         ("SUP_B", 4.0, 15.0, 40.0, 80.0)]},
            {"cid": "sup-3", "supplies": [("SUP_A", "concrete", 15.0),
                                            ("SUP_C", "metal_scrap", 20.0)],
             "matches": [("SUP_C", 10.0, 30.0, 100.0, 200.0)]},
        ]):
            cid = cycle_data["cid"]
            self.p.begin_cycle(
                cycle_id=cid, sim_day=i+1, sim_hour=8, activity_factor=1.0,
                n_supply_offers=len(cycle_data["supplies"]),
                n_demand_requests=len(cycle_data["matches"]),
            )
            self.p.commit_cycle(
                cycle_id=cid,
                kpi={"n_matches": len(cycle_data["matches"]),
                     "total_tons": sum(m[1] for m in cycle_data["matches"]),
                     "total_cost_sek": 100, "total_co2_kg": 50,
                     "total_distance_km": 100, "n_vehicles_used": 1,
                     "n_vehicles_available": 5, "fleet_utilization_pct": 20,
                     "solver_status": "OPTIMAL"},
                wall_duration_ms=100,
            )
            for sid, mat, tons in cycle_data["supplies"]:
                self.p.record_supply(cid, {
                    "agent_id": sid,
                    "location": {"lat": 57.7, "lon": 12.9},
                    "material_type": mat,
                    "available_tons": tons,
                    "quality_score": 80.0,
                })
            for sup_id, tons, dist, co2, cost in cycle_data["matches"]:
                self.p.record_match(cid, {
                    "supply_id": sup_id,
                    "demand_id": f"D_{sup_id}_{i}",
                    "material_type": "concrete" if sup_id != "SUP_C" else "metal_scrap",
                    "tons": tons,
                    "distance_km": dist,
                    "estimated_profit_sek": 50.0,
                })
                self.p.record_route(cid, {
                    "vehicle_id": "VEH_1",
                    "stops": [],
                    "distance_km": dist,
                    "duration_hours": 1.0,
                    "cost_sek": cost,
                    "co2_kg": co2,
                })

    def test_valid_metric_required(self):
        with self.assertRaises(ValueError):
            self.p.get_top_suppliers_by_efficiency(metric="invalid_metric")

    def test_co2_per_ton_lower_better(self):
        result = self.p.get_top_suppliers_by_efficiency(metric="co2_per_ton", limit=5)
        self.assertEqual(result["metric"], "co2_per_ton")
        self.assertEqual(result["direction"], "lower_is_better")
        # Top supplier should have lowest co2/ton
        if result["top_suppliers"]:
            values = [s["value"] for s in result["top_suppliers"]]
            self.assertEqual(values, sorted(values))  # ascending

    def test_match_rate_higher_better(self):
        result = self.p.get_top_suppliers_by_efficiency(metric="match_rate", limit=5)
        self.assertEqual(result["metric"], "match_rate")
        self.assertEqual(result["direction"], "higher_is_better")
        # SUP_A had matches in cycle 1 AND cycle 2 (different cycles)
        # match_rate = n_matches / n_cycles_with_supply
        # SUP_A: 2 matches / 3 cycles = 0.667
        # SUP_B: 1 match / 2 cycles = 0.5
        # SUP_C: 1 match / 1 cycle = 1.0
        if result["top_suppliers"]:
            values = [s["value"] for s in result["top_suppliers"]]
            self.assertEqual(values, sorted(values, reverse=True))  # descending

    def test_top_supplier_basic_fields(self):
        result = self.p.get_top_suppliers_by_efficiency(metric="match_rate", limit=5)
        for s in result["top_suppliers"]:
            self.assertIn("supply_id", s)
            self.assertIn("material_type", s)
            self.assertIn("value", s)
            self.assertIn("n_matches", s)
            self.assertIn("total_matched_tons", s)
            self.assertIn("avg_distance_km", s)

    def test_filter_by_material(self):
        result = self.p.get_top_suppliers_by_efficiency(
            metric="match_rate", material_type="metal_scrap"
        )
        # Only SUP_C is metal_scrap
        for s in result["top_suppliers"]:
            self.assertEqual(s["material_type"], "metal_scrap")

    def test_limit_capped_at_100(self):
        result = self.p.get_top_suppliers_by_efficiency(limit=200)
        self.assertLessEqual(len(result["top_suppliers"]), 100)

    def test_skip_zero_match_for_ton_metrics(self):
        # co2_per_ton / cost_per_ton should skip suppliers with no matches
        # Add a supply with no matches
        self.p.begin_cycle(
            cycle_id="sup-4", sim_day=4, sim_hour=8, activity_factor=1.0,
            n_supply_offers=1, n_demand_requests=0,
        )
        self.p.commit_cycle(
            cycle_id="sup-4",
            kpi={"n_matches": 0, "total_tons": 0, "total_cost_sek": 0,
                 "total_co2_kg": 0, "total_distance_km": 0,
                 "n_vehicles_used": 0, "n_vehicles_available": 5,
                 "fleet_utilization_pct": 0, "solver_status": "OPTIMAL"},
            wall_duration_ms=100,
        )
        self.p.record_supply("sup-4", {
            "agent_id": "SUP_D",
            "location": {"lat": 57.7, "lon": 12.9},
            "material_type": "concrete",
            "available_tons": 100.0,
            "quality_score": 80.0,
        })
        result = self.p.get_top_suppliers_by_efficiency(metric="co2_per_ton", limit=100)
        # SUP_D should NOT appear (no matches → no co2/ton ratio)
        sids = [s["supply_id"] for s in result["top_suppliers"]]
        self.assertNotIn("SUP_D", sids)

    def test_empty_data(self):
        from agents.persistence import Persistence
        empty = Persistence(db_path=self.db_path + ".empty")
        result = empty.get_top_suppliers_by_efficiency(metric="match_rate")
        self.assertEqual(result["n_suppliers_evaluated"], 0)
        self.assertEqual(result["top_suppliers"], [])
        try:
            os.unlink(self.db_path + ".empty")
        except Exception:
            pass


class TestTopSuppliersEndpoint(unittest.TestCase):
    """/api/persistence/top-suppliers FastAPI endpoint."""

    def test_endpoint_returns_200(self):
        from fastapi.testclient import TestClient
        from web.backend.main import app

        client = TestClient(app)
        resp = client.get("/api/persistence/top-suppliers")
        self.assertIn(resp.status_code, (200, 503))
        if resp.status_code == 200:
            data = resp.json()
            self.assertIn("metric", data)
            self.assertIn("direction", data)
            self.assertIn("top_suppliers", data)
            self.assertIsInstance(data["top_suppliers"], list)

    def test_endpoint_with_metric(self):
        from fastapi.testclient import TestClient
        from web.backend.main import app

        client = TestClient(app)
        resp = client.get("/api/persistence/top-suppliers?metric=cost_per_ton&limit=5")
        self.assertIn(resp.status_code, (200, 503))
        if resp.status_code == 200:
            data = resp.json()
            self.assertEqual(data["metric"], "cost_per_ton")
            self.assertEqual(data["direction"], "lower_is_better")

    def test_endpoint_invalid_metric(self):
        # iter #55: invalid metric returns 400 (if persistence ready) or 503 (if not)
        from fastapi.testclient import TestClient
        from web.backend.main import app

        client = TestClient(app)
        resp = client.get("/api/persistence/top-suppliers?metric=bogus_metric")
        self.assertIn(resp.status_code, (400, 503))

    def test_endpoint_with_material_filter(self):
        from fastapi.testclient import TestClient
        from web.backend.main import app

        client = TestClient(app)
        resp = client.get("/api/persistence/top-suppliers?material_type=concrete&limit=10")
        self.assertIn(resp.status_code, (200, 503))


if __name__ == "__main__":
    unittest.main()

class TestTopVehiclesEndpoint(unittest.TestCase):
    """/api/persistence/top-vehicles FastAPI endpoint."""

    def test_endpoint_returns_200(self):
        from fastapi.testclient import TestClient
        from web.backend.main import app

        client = TestClient(app)
        resp = client.get("/api/persistence/top-vehicles")
        self.assertIn(resp.status_code, (200, 503))
        if resp.status_code == 200:
            data = resp.json()
            self.assertIn("metric", data)
            self.assertIn("direction", data)
            self.assertIn("top_vehicles", data)
            self.assertIsInstance(data["top_vehicles"], list)

    def test_endpoint_with_metric(self):
        from fastapi.testclient import TestClient
        from web.backend.main import app

        client = TestClient(app)
        resp = client.get("/api/persistence/top-vehicles?metric=co2_per_km&limit=5")
        self.assertIn(resp.status_code, (200, 503))
        if resp.status_code == 200:
            data = resp.json()
            self.assertEqual(data["metric"], "co2_per_km")
            self.assertEqual(data["direction"], "lower_is_better")

    def test_endpoint_invalid_metric(self):
        from fastapi.testclient import TestClient
        from web.backend.main import app

        client = TestClient(app)
        resp = client.get("/api/persistence/top-vehicles?metric=bogus_metric")
        self.assertIn(resp.status_code, (400, 503))

    def test_endpoint_utilization_metric(self):
        from fastapi.testclient import TestClient
        from web.backend.main import app

        client = TestClient(app)
        resp = client.get("/api/persistence/top-vehicles?metric=utilization")
        self.assertIn(resp.status_code, (200, 503))
        if resp.status_code == 200:
            data = resp.json()
            self.assertEqual(data["metric"], "utilization")
            self.assertEqual(data["direction"], "higher_is_better")
