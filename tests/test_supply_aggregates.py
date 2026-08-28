"""
Supply aggregates tests (iter #15) — /api/persistence/supply-aggregates.

测试覆盖:
- get_supply_aggregates() aggregates per supply_id from supply_offers + matches
- top supplies sorted by total_available_tons
- filter by supply_id (single) and material_type
- API endpoint 200 / 503
"""
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestSupplyAggregatesPersistence(unittest.TestCase):
    """Persistence.get_supply_aggregates()"""

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
        """3 cycles with 3 different supplies + 1 match"""
        for cycle_idx in range(3):
            cid = f"sa-{cycle_idx+1}"
            self.p.begin_cycle(
                cycle_id=cid, sim_day=cycle_idx+1, sim_hour=8, activity_factor=1.0,
                n_supply_offers=3, n_demand_requests=2,
            )
            self.p.commit_cycle(
                cycle_id=cid,
                kpi={"n_matches": 1, "total_tons": 5, "total_cost_sek": 50,
                     "total_co2_kg": 25, "total_distance_km": 10,
                     "n_vehicles_used": 1, "n_vehicles_available": 5,
                     "fleet_utilization_pct": 20, "solver_status": "OPTIMAL"},
                wall_duration_ms=50,
            )
            for sup_i, (sid, mat, tons, q) in enumerate([
                ("SUP000", "concrete", 10.0, 80.0),
                ("SUP001", "concrete", 20.0, 75.0),
                ("SUP002", "metal_scrap", 30.0, 90.0),
            ]):
                self.p.record_supply(cid, {
                    "agent_id": sid,
                    "location": {"lat": 57.7, "lon": 12.9},
                    "material_type": mat,
                    "available_tons": tons,
                    "quality_score": q,
                })
            # 1 match: SUP000 ↔ DEM000
            self.p.record_match(cid, {
                "supply_id": "SUP000",
                "demand_id": "DEM000",
                "material_type": "concrete",
                "tons": 5.0,
                "distance_km": 10.0,
                "estimated_profit_sek": 50.0,
            })

    def test_top_supplies_sorted_by_total(self):
        result = self.p.get_supply_aggregates(limit_supplies=10)
        # SUP002 has 30 t/cycle * 3 cycles = 90 t
        # SUP001 has 20 t/cycle * 3 = 60 t
        # SUP000 has 10 t/cycle * 3 = 30 t
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0]["supply_id"], "SUP002")
        self.assertEqual(result[1]["supply_id"], "SUP001")
        self.assertEqual(result[2]["supply_id"], "SUP000")
        self.assertEqual(result[0]["total_available_tons"], 90.0)
        self.assertEqual(result[1]["total_available_tons"], 60.0)

    def test_supplies_n_cycles(self):
        result = self.p.get_supply_aggregates(limit_supplies=10)
        for r in result:
            self.assertEqual(r["n_cycles_with_supply"], 3)

    def test_supplies_match_count(self):
        result = self.p.get_supply_aggregates(limit_supplies=10)
        # SUP000 has 1 match per cycle * 3 cycles = 3 matches
        sup0 = next(r for r in result if r["supply_id"] == "SUP000")
        self.assertEqual(sup0["n_matches"], 3)
        self.assertEqual(sup0["total_matched_tons"], 15.0)
        # SUP001/002 have no matches
        sup1 = next(r for r in result if r["supply_id"] == "SUP001")
        self.assertEqual(sup1["n_matches"], 0)
        self.assertEqual(sup1["total_matched_tons"], 0)

    def test_supplies_quality_avg(self):
        result = self.p.get_supply_aggregates(limit_supplies=10)
        sup0 = next(r for r in result if r["supply_id"] == "SUP000")
        self.assertEqual(sup0["avg_quality_score"], 80.0)
        sup2 = next(r for r in result if r["supply_id"] == "SUP002")
        self.assertEqual(sup2["avg_quality_score"], 90.0)

    def test_filter_by_supply_id(self):
        result = self.p.get_supply_aggregates(supply_id="SUP001")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["supply_id"], "SUP001")

    def test_filter_by_material_type(self):
        result = self.p.get_supply_aggregates(material_type="concrete", limit_supplies=10)
        # Only SUP000, SUP001 (concrete); SUP002 (metal_scrap) excluded
        ids = [r["supply_id"] for r in result]
        self.assertEqual(set(ids), {"SUP000", "SUP001"})

    def test_supply_aggregates_empty(self):
        empty = tempfile.NamedTemporaryFile(suffix=".db", delete=False, dir="/tmp")
        empty.close()
        try:
            from agents.persistence import Persistence
            p = Persistence(db_path=empty.name)
            result = p.get_supply_aggregates(limit_supplies=10)
            self.assertEqual(result, [])
        finally:
            os.unlink(empty.name)


class TestSupplyAggregatesApi(unittest.TestCase):
    """/api/persistence/supply-aggregates"""

    def setUp(self):
        from web.backend import main as backend_main
        from agents.persistence import Persistence

        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False, dir="/tmp")
        self.tmp.close()
        self.db_path = self.tmp.name
        persistence = Persistence(db_path=self.db_path)
        persistence.begin_cycle(
            cycle_id="api-sa-1", sim_day=1, sim_hour=8, activity_factor=1.0,
            n_supply_offers=2, n_demand_requests=1,
        )
        persistence.commit_cycle(
            cycle_id="api-sa-1",
            kpi={"n_matches": 1, "total_tons": 5, "total_cost_sek": 50,
                 "total_co2_kg": 25, "total_distance_km": 10,
                 "n_vehicles_used": 1, "n_vehicles_available": 5,
                 "fleet_utilization_pct": 20, "solver_status": "OPTIMAL"},
            wall_duration_ms=50,
        )
        persistence.record_supply("api-sa-1", {
            "agent_id": "SUP000", "location": {"lat": 57.7, "lon": 12.9},
            "material_type": "concrete", "available_tons": 15.0,
            "quality_score": 85.0,
        })
        persistence.record_supply("api-sa-1", {
            "agent_id": "SUP001", "location": {"lat": 57.7, "lon": 12.9},
            "material_type": "wood_waste", "available_tons": 25.0,
            "quality_score": 75.0,
        })
        persistence.record_match("api-sa-1", {
            "supply_id": "SUP000", "demand_id": "DEM000",
            "material_type": "concrete", "tons": 5.0,
            "distance_km": 10.0, "estimated_profit_sek": 50.0,
        })

        fake_coord = MagicMock()
        fake_coord.persistence = persistence
        backend_main.coordinator = fake_coord
        self.backend_main = backend_main
        from fastapi.testclient import TestClient
        self.client = TestClient(backend_main.app)

    def tearDown(self):
        try:
            os.unlink(self.db_path)
        except Exception:
            pass

    def test_endpoint_200(self):
        resp = self.client.get("/api/persistence/supply-aggregates")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data), 2)
        # sorted by total_available DESC: SUP001 (25) > SUP000 (15)
        self.assertEqual(data[0]["supply_id"], "SUP001")
        self.assertEqual(data[1]["supply_id"], "SUP000")

    def test_endpoint_filter_by_supply_id(self):
        resp = self.client.get(
            "/api/persistence/supply-aggregates?supply_id=SUP000"
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["supply_id"], "SUP000")
        self.assertEqual(data[0]["n_matches"], 1)
        self.assertEqual(data[0]["total_matched_tons"], 5.0)

    def test_endpoint_filter_by_material(self):
        resp = self.client.get(
            "/api/persistence/supply-aggregates?material_type=concrete"
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["supply_id"], "SUP000")

    def test_endpoint_503_no_persistence(self):
        self.backend_main.coordinator.persistence = None
        resp = self.client.get("/api/persistence/supply-aggregates")
        self.assertEqual(resp.status_code, 503)


if __name__ == "__main__":
    unittest.main()
