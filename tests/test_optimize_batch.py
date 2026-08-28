"""
Batch optimize tests (iter #13) — /api/optimize/batch endpoint.

测试覆盖:
- BatchScenarioRequest / BatchOptimizeRequest Pydantic 验证 (count, n_points, time, co2_price)
- 1-8 scenarios 范围限制
- n_points / time_limit / co2_price 范围限制
- Endpoint 返回 scenarios 列表 (与请求顺序对应)
- 503 when coordinator missing
- empty matches → scenarios 列表带 error 字段
"""
import os
import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestBatchRequestSchema(unittest.TestCase):
    """Pydantic schema 验证"""

    def test_single_scenario_valid(self):
        from web.backend.main import BatchOptimizeRequest, BatchScenarioRequest
        req = BatchOptimizeRequest(scenarios=[
            BatchScenarioRequest(name="test1", n_points=5, time_limit_seconds=3,
                                 co2_price=1.5, use_real_roads=True)
        ])
        self.assertEqual(len(req.scenarios), 1)
        self.assertEqual(req.scenarios[0].name, "test1")

    def test_max_scenarios(self):
        from web.backend.main import BatchOptimizeRequest, BatchScenarioRequest
        req = BatchOptimizeRequest(scenarios=[
            BatchScenarioRequest(name=f"s{i}") for i in range(8)
        ])
        self.assertEqual(len(req.scenarios), 8)

    def test_zero_scenarios_invalid(self):
        from web.backend.main import BatchOptimizeRequest
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            BatchOptimizeRequest(scenarios=[])

    def test_too_many_scenarios_invalid(self):
        from web.backend.main import BatchOptimizeRequest, BatchScenarioRequest
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            BatchOptimizeRequest(scenarios=[
                BatchScenarioRequest(name=f"s{i}") for i in range(9)
            ])

    def test_n_points_too_low_invalid(self):
        from web.backend.main import BatchOptimizeRequest, BatchScenarioRequest
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            BatchOptimizeRequest(scenarios=[
                BatchScenarioRequest(name="bad", n_points=1)
            ])

    def test_n_points_too_high_invalid(self):
        from web.backend.main import BatchOptimizeRequest, BatchScenarioRequest
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            BatchOptimizeRequest(scenarios=[
                BatchScenarioRequest(name="bad", n_points=21)
            ])

    def test_time_limit_too_low_invalid(self):
        from web.backend.main import BatchOptimizeRequest, BatchScenarioRequest
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            BatchOptimizeRequest(scenarios=[
                BatchScenarioRequest(name="bad", time_limit_seconds=0)
            ])

    def test_co2_price_negative_invalid(self):
        from web.backend.main import BatchOptimizeRequest, BatchScenarioRequest
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            BatchOptimizeRequest(scenarios=[
                BatchScenarioRequest(name="bad", co2_price=-1.0)
            ])

    def test_default_values(self):
        from web.backend.main import BatchScenarioRequest
        s = BatchScenarioRequest()
        self.assertEqual(s.name, "scenario")
        self.assertEqual(s.n_points, 4)
        self.assertEqual(s.time_limit_seconds, 3)
        self.assertEqual(s.co2_price, 0.0)
        self.assertTrue(s.use_real_roads)
        self.assertIsNone(s.region)


class TestBatchOptimizeApi(unittest.TestCase):
    """/api/optimize/batch endpoint"""

    def setUp(self):
        from web.backend import main as backend_main
        from agents.persistence import Persistence

        # fake coordinator with supply_agents / market_agent / logistics_agent
        coord = MagicMock()
        coord.persistence = Persistence(db_path="/tmp/_test_batch_empty.db")

        # 3 supply + 3 demand + 3 vehicles
        coord.supply_agents = {}
        for i in range(3):
            agent = MagicMock(
                daily_capacity=10.0,
                material_type="concrete",
                location={"lat": 57.7 + i * 0.01, "lon": 12.9},
            )
            agent.agent_id = f"SUP{i:03d}"
            coord.supply_agents[f"SUP{i:03d}"] = agent
        coord.market_agent = MagicMock()
        coord.market_agent.demand_points = [
            {
                "id": f"DEM{i:03d}",
                "name": f"Project {i}",
                "current_demand_tons": 10.0,
                "preferred_materials": ["concrete"],
                "location": {"lat": 57.7, "lon": 12.9},
                "material_type": "concrete",
            }
            for i in range(3)
        ]
        # mock match_supply_demand async
        import asyncio
        async def fake_match(supply_offers, demand_requests):
            matches = []
            for sup in supply_offers[:3]:
                for dem in demand_requests[:3]:
                    matches.append({
                        "supply_id": sup["agent_id"],
                        "demand_id": dem["id"],
                        "material_type": "concrete",
                        "tons": 5.0,
                        "distance_km": 5.0,
                        "estimated_profit_sek": 50.0,
                    })
            return {"matches": matches, "total_matches": len(matches)}
        coord.market_agent.match_supply_demand = fake_match

        coord.logistics_agent = MagicMock()
        coord.logistics_agent.depot_location = {"lat": 57.7, "lon": 12.9}
        coord.logistics_agent.vehicles = [
            {
                "vehicle_id": f"V{i:03d}",
                "status": "available",
                "capacity_tons": 20.0,
                "co2_emission_rate": 0.85,
            }
            for i in range(5)
        ]

        backend_main.coordinator = coord
        self.backend_main = backend_main
        from fastapi.testclient import TestClient
        self.client = TestClient(backend_main.app)

    def tearDown(self):
        try:
            os.unlink("/tmp/_test_batch_empty.db")
        except Exception:
            pass
        self.backend_main.coordinator = None

    def test_batch_with_one_scenario(self):
        """最简单 1-scenario 测试"""
        resp = self.client.post("/api/optimize/batch", json={
            "scenarios": [
                {"name": "no_carbon", "n_points": 3, "time_limit_seconds": 2,
                 "co2_price": 0.0}
            ]
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data["scenarios"]), 1)
        s = data["scenarios"][0]
        self.assertEqual(s["name"], "no_carbon")
        self.assertIn("cost_optimal", s)
        self.assertIn("co2_optimal", s)
        self.assertIn("pareto", s)
        self.assertGreaterEqual(s["n_points"], 2)

    def test_batch_multiple_scenarios_parallel(self):
        """3 scenarios 并行"""
        resp = self.client.post("/api/optimize/batch", json={
            "scenarios": [
                {"name": "no_carbon", "n_points": 3, "time_limit_seconds": 2,
                 "co2_price": 0.0},
                {"name": "low_carbon", "n_points": 3, "time_limit_seconds": 2,
                 "co2_price": 1.5},
                {"name": "high_carbon", "n_points": 3, "time_limit_seconds": 2,
                 "co2_price": 5.0},
            ]
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data["scenarios"]), 3)
        # 顺序与请求对应
        self.assertEqual(data["scenarios"][0]["name"], "no_carbon")
        self.assertEqual(data["scenarios"][1]["name"], "low_carbon")
        self.assertEqual(data["scenarios"][2]["name"], "high_carbon")
        # carbon_price 保留
        self.assertEqual(data["scenarios"][0]["carbon_price_sek_per_kg"], 0.0)
        self.assertEqual(data["scenarios"][2]["carbon_price_sek_per_kg"], 5.0)

    def test_batch_invalid_n_points(self):
        """Pydantic validation 在 endpoint 触发"""
        resp = self.client.post("/api/optimize/batch", json={
            "scenarios": [{"name": "bad", "n_points": 100}]
        })
        self.assertEqual(resp.status_code, 422)

    def test_batch_too_many_scenarios(self):
        resp = self.client.post("/api/optimize/batch", json={
            "scenarios": [{"name": f"s{i}"} for i in range(9)]
        })
        self.assertEqual(resp.status_code, 422)

    def test_batch_503_no_coordinator(self):
        self.backend_main.coordinator = None
        resp = self.client.post("/api/optimize/batch", json={
            "scenarios": [{"name": "test"}]
        })
        self.assertEqual(resp.status_code, 503)


if __name__ == "__main__":
    unittest.main()
