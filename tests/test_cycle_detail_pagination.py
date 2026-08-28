"""
Cycle detail pagination tests (iter #13) — /api/persistence/cycle-detail/{id} pagination.

测试覆盖:
- 默认无 pagination: 全返 (向后兼容)
- match_limit 限制 + has_more=True
- match_offset 跳过前 N 行
- route_limit / route_offset 同样
- pagination metadata 准确 (total/limit/offset/has_more)
- limit clamp 到 [1, 1000]
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestCycleDetailPaginationPersistence(unittest.TestCase):
    """Persistence.get_cycle_detail() pagination"""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False, dir="/tmp")
        self.tmp.close()
        self.db_path = self.tmp.name
        from agents.persistence import Persistence
        self.p = Persistence(db_path=self.db_path)
        # seed 1 cycle with 5 matches + 3 routes
        self._seed_cycle()

    def tearDown(self):
        try:
            os.unlink(self.db_path)
        except Exception:
            pass

    def _seed_cycle(self):
        cid = "pg-cycle-1"
        self.p.begin_cycle(
            cycle_id=cid, sim_day=1, sim_hour=8, activity_factor=1.0,
            n_supply_offers=5, n_demand_requests=5,
        )
        self.p.commit_cycle(
            cycle_id=cid,
            kpi={"n_matches": 5, "total_tons": 50, "total_cost_sek": 500,
                 "total_co2_kg": 250, "total_distance_km": 100,
                 "n_vehicles_used": 3, "n_vehicles_available": 10,
                 "fleet_utilization_pct": 30, "solver_status": "OPTIMAL"},
            wall_duration_ms=100,
        )
        for i in range(5):
            self.p.record_supply(cid, {
                "agent_id": f"SUP{i:03d}", "location": {"lat": 57.7, "lon": 12.9},
                "material_type": "concrete", "available_tons": 10,
            })
            self.p.record_demand(cid, {
                "id": f"DEM{i:03d}", "location": {"lat": 57.7, "lon": 12.9},
                "material_type": "concrete", "required_tons": 10,
            })
            self.p.record_match(cid, {
                "supply_id": f"SUP{i:03d}", "demand_id": f"DEM{i:03d}",
                "material_type": "concrete", "tons": 10,
                "distance_km": 5.0, "estimated_profit_sek": 50,
            })
        for i in range(3):
            self.p.record_route(cid, {
                "vehicle_id": f"V{i:03d}", "stops": ["DEPOT", f"SUP{i:03d}"],
                "distance_km": 10, "duration_hours": 1, "cost_sek": 100,
                "co2_kg": 20,
            })

    def test_no_pagination_returns_all(self):
        """默认 None = 不限"""
        detail = self.p.get_cycle_detail("pg-cycle-1")
        self.assertEqual(len(detail["matches"]), 5)
        self.assertEqual(len(detail["routes"]), 3)
        # pagination metadata
        self.assertEqual(detail["pagination"]["matches"]["total"], 5)
        self.assertIsNone(detail["pagination"]["matches"]["limit"])
        self.assertEqual(detail["pagination"]["matches"]["offset"], 0)
        self.assertFalse(detail["pagination"]["matches"]["has_more"])
        self.assertEqual(detail["pagination"]["routes"]["total"], 3)
        self.assertFalse(detail["pagination"]["routes"]["has_more"])

    def test_match_limit_2(self):
        detail = self.p.get_cycle_detail("pg-cycle-1", match_limit=2)
        self.assertEqual(len(detail["matches"]), 2)
        self.assertEqual(detail["pagination"]["matches"]["total"], 5)
        self.assertEqual(detail["pagination"]["matches"]["limit"], 2)
        self.assertEqual(detail["pagination"]["matches"]["offset"], 0)
        self.assertTrue(detail["pagination"]["matches"]["has_more"])
        # routes 不受影响
        self.assertEqual(len(detail["routes"]), 3)

    def test_match_offset_skip_first_3(self):
        detail = self.p.get_cycle_detail(
            "pg-cycle-1", match_limit=5, match_offset=3,
        )
        self.assertEqual(len(detail["matches"]), 2)  # 5 - 3 = 2
        self.assertEqual(detail["pagination"]["matches"]["total"], 5)
        self.assertEqual(detail["pagination"]["matches"]["offset"], 3)
        self.assertFalse(detail["pagination"]["matches"]["has_more"])

    def test_match_offset_exceeds_total_returns_empty(self):
        detail = self.p.get_cycle_detail(
            "pg-cycle-1", match_limit=5, match_offset=10,
        )
        self.assertEqual(len(detail["matches"]), 0)
        self.assertEqual(detail["pagination"]["matches"]["total"], 5)
        # has_more=False 因为 offset+len(0) < total 不成立
        # 5+0 = 5 不 < 5 → False
        self.assertFalse(detail["pagination"]["matches"]["has_more"])

    def test_route_limit_2(self):
        detail = self.p.get_cycle_detail("pg-cycle-1", route_limit=2)
        self.assertEqual(len(detail["routes"]), 2)
        self.assertEqual(detail["pagination"]["routes"]["total"], 3)
        self.assertEqual(detail["pagination"]["routes"]["limit"], 2)
        self.assertTrue(detail["pagination"]["routes"]["has_more"])

    def test_route_offset_skip_first(self):
        detail = self.p.get_cycle_detail(
            "pg-cycle-1", route_limit=10, route_offset=1,
        )
        self.assertEqual(len(detail["routes"]), 2)  # 3 - 1 = 2
        self.assertEqual(detail["pagination"]["routes"]["total"], 3)
        self.assertFalse(detail["pagination"]["routes"]["has_more"])

    def test_missing_cycle_returns_none(self):
        result = self.p.get_cycle_detail("nonexistent", match_limit=5)
        self.assertIsNone(result)


class TestCycleDetailPaginationApi(unittest.TestCase):
    """/api/persistence/cycle-detail/{cycle_id} pagination endpoints"""

    def setUp(self):
        from web.backend import main as backend_main
        from agents.persistence import Persistence

        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False, dir="/tmp")
        self.tmp.close()
        self.db_path = self.tmp.name
        persistence = Persistence(db_path=self.db_path)
        cid = "api-pg-cycle"
        persistence.begin_cycle(
            cycle_id=cid, sim_day=1, sim_hour=8, activity_factor=1.0,
            n_supply_offers=3, n_demand_requests=3,
        )
        persistence.commit_cycle(
            cycle_id=cid,
            kpi={"n_matches": 3, "total_tons": 30, "total_cost_sek": 300,
                 "total_co2_kg": 150, "total_distance_km": 60,
                 "n_vehicles_used": 2, "n_vehicles_available": 5,
                 "fleet_utilization_pct": 40, "solver_status": "OPTIMAL"},
            wall_duration_ms=100,
        )
        for i in range(3):
            persistence.record_match(cid, {
                "supply_id": f"SUP{i:03d}", "demand_id": f"DEM{i:03d}",
                "material_type": "concrete", "tons": 10,
                "distance_km": 5.0, "estimated_profit_sek": 50,
            })

        fake_coord = MagicMock() if False else None  # silence
        import unittest.mock as _mock
        fake_coord = _mock.MagicMock()
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

    def test_api_match_limit(self):
        resp = self.client.get(
            "/api/persistence/cycle-detail/api-pg-cycle?match_limit=2"
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data["matches"]), 2)
        self.assertEqual(data["pagination"]["matches"]["limit"], 2)
        self.assertTrue(data["pagination"]["matches"]["has_more"])

    def test_api_match_offset(self):
        resp = self.client.get(
            "/api/persistence/cycle-detail/api-pg-cycle?match_limit=2&match_offset=2"
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data["matches"]), 1)  # 3 - 2 = 1
        self.assertEqual(data["pagination"]["matches"]["offset"], 2)
        self.assertFalse(data["pagination"]["matches"]["has_more"])

    def test_api_no_pagination_backward_compat(self):
        """不传 query params = 全返 (向后兼容)"""
        resp = self.client.get("/api/persistence/cycle-detail/api-pg-cycle")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data["matches"]), 3)
        self.assertIsNone(data["pagination"]["matches"]["limit"])

    def test_api_limit_clamped_to_max(self):
        """limit > 1000 自动 clamp 到 1000"""
        resp = self.client.get(
            "/api/persistence/cycle-detail/api-pg-cycle?match_limit=99999"
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["pagination"]["matches"]["limit"], 1000)

    def test_api_limit_floor_1(self):
        """limit < 1 自动 clamp 到 1"""
        resp = self.client.get(
            "/api/persistence/cycle-detail/api-pg-cycle?match_limit=0"
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["pagination"]["matches"]["limit"], 1)


if __name__ == "__main__":
    unittest.main()
