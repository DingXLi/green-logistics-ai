"""
Optimize ws_broadcast tests (iter #12) — /api/optimize ws_broadcast param.

测试覆盖:
- OptimizationRequest 接受 ws_broadcast 字段 (default True)
- /api/optimize POST 默认 ws_broadcast=True → 调用 _broadcast_cycle_update
- /api/optimize POST ws_broadcast=False → 不调用 _broadcast_cycle_update
- /api/optimize POST 返回缓存时 (cached_flag=True) → 不再重复 broadcast
- 503 when coordinator missing
"""
import os
import sys
import time
import unittest
from unittest.mock import patch, MagicMock, AsyncMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient


class TestOptimizationRequestWsBroadcast(unittest.TestCase):
    """Test OptimizationRequest schema 接受 ws_broadcast 字段"""

    def test_ws_broadcast_default_true(self):
        from web.backend.main import OptimizationRequest
        req = OptimizationRequest()
        self.assertTrue(req.ws_broadcast)

    def test_ws_broadcast_explicit_false(self):
        from web.backend.main import OptimizationRequest
        req = OptimizationRequest(ws_broadcast=False)
        self.assertFalse(req.ws_broadcast)

    def test_ws_broadcast_explicit_true(self):
        from web.backend.main import OptimizationRequest
        req = OptimizationRequest(ws_broadcast=True)
        self.assertTrue(req.ws_broadcast)

    def test_other_fields_still_work(self):
        """确保加 ws_broadcast 后没破坏其他字段"""
        from web.backend.main import OptimizationRequest
        req = OptimizationRequest(
            run_simulation=True,
            simulation_days=5,
            use_real_roads=False,
            region="Göteborg",
            ws_broadcast=False,
        )
        self.assertEqual(req.run_simulation, True)
        self.assertEqual(req.simulation_days, 5)
        self.assertEqual(req.use_real_roads, False)
        self.assertEqual(req.region, "Göteborg")
        self.assertEqual(req.ws_broadcast, False)


class TestOptimizeApiWsBroadcast(unittest.TestCase):
    """/api/optimize 路径的 ws_broadcast 行为"""

    def setUp(self):
        from web.backend import main as backend_main
        from agents.persistence import Persistence

        # setup fake persistence
        self.tmp_path = f"/tmp/test_optimize_{os.getpid()}.db"
        persistence = Persistence(db_path=self.tmp_path)
        # seed 1 cycle (warmup data)
        persistence.begin_cycle(
            cycle_id="warmup-1",
            sim_day=1,
            sim_hour=8,
            activity_factor=1.0,
            n_supply_offers=3,
            n_demand_requests=2,
        )
        persistence.commit_cycle(
            cycle_id="warmup-1",
            kpi={"n_matches": 2, "total_tons": 10, "total_cost_sek": 100,
                 "total_co2_kg": 50, "total_distance_km": 20,
                 "n_vehicles_used": 3, "n_vehicles_available": 10,
                 "fleet_utilization_pct": 30, "solver_status": "OPTIMAL"},
            wall_duration_ms=100,
        )

        fake_coord = MagicMock()
        fake_coord.persistence = persistence
        # mock run_optimization_cycle
        fake_coord.run_optimization_cycle = AsyncMock(return_value={
            "optimization_id": "opt-test-1",
            "timestamp": "2026-08-28T08:00:00",
            "matches": {"total_matches": 5, "total_tons": 25.0},
            "route_optimization": {"total_cost_sek": 200.0, "total_co2_kg": 100.0},
            "distance_source": "haversine",
        })
        backend_main.coordinator = fake_coord
        self.backend_main = backend_main
        self.client = TestClient(backend_main.app)
        # 清空 cache
        backend_main._optimize_cache.clear()

    def tearDown(self):
        try:
            os.unlink(self.tmp_path)
        except Exception:
            pass
        self.backend_main._optimize_cache.clear()

    @patch("web.backend.main._broadcast_cycle_update", new_callable=AsyncMock)
    def test_optimize_default_broadcasts(self, mock_broadcast):
        """默认 ws_broadcast=True → 调用 _broadcast_cycle_update"""
        resp = self.client.post("/api/optimize", json={})
        self.assertEqual(resp.status_code, 200)
        # 确认 broadcast 被调用
        mock_broadcast.assert_called_once()
        data = resp.json()
        self.assertEqual(data["status"], "success")

    @patch("web.backend.main._broadcast_cycle_update", new_callable=AsyncMock)
    def test_optimize_ws_broadcast_false_skips_broadcast(self, mock_broadcast):
        """ws_broadcast=False → 不调用 _broadcast_cycle_update"""
        resp = self.client.post("/api/optimize", json={"ws_broadcast": False})
        self.assertEqual(resp.status_code, 200)
        mock_broadcast.assert_not_called()
        data = resp.json()
        self.assertEqual(data["status"], "success")

    @patch("web.backend.main._broadcast_cycle_update", new_callable=AsyncMock)
    def test_optimize_ws_broadcast_true_explicit_broadcasts(self, mock_broadcast):
        """ws_broadcast=True 显式 → 调用"""
        resp = self.client.post("/api/optimize", json={"ws_broadcast": True})
        self.assertEqual(resp.status_code, 200)
        mock_broadcast.assert_called_once()

    @patch("web.backend.main._broadcast_cycle_update", new_callable=AsyncMock)
    def test_optimize_cached_response_skips_broadcast(self, mock_broadcast):
        """cached response → 不再 broadcast (避免重复推送)"""
        # 第一次调用: 跑 cycle + broadcast
        resp1 = self.client.post("/api/optimize", json={})
        self.assertEqual(resp1.status_code, 200)
        self.assertEqual(resp1.json()["status"], "success")
        mock_broadcast.assert_called_once()
        # 第二次调用: 走 cache, 不应再 broadcast
        resp2 = self.client.post("/api/optimize", json={})
        self.assertEqual(resp2.status_code, 200)
        self.assertEqual(resp2.json()["status"], "cached")
        # broadcast 只调用了一次 (第一次)
        self.assertEqual(mock_broadcast.call_count, 1)

    @patch("web.backend.main._broadcast_cycle_update", new_callable=AsyncMock)
    def test_optimize_cached_response_with_ws_broadcast_false(self, mock_broadcast):
        """cached + ws_broadcast=False → 不 broadcast (应与 cached 行为一致)"""
        # 第一次
        self.client.post("/api/optimize", json={})
        mock_broadcast.assert_called_once()
        # 第二次 with ws_broadcast=False
        self.client.post("/api/optimize", json={"ws_broadcast": False})
        # 仍只 broadcast 1 次
        self.assertEqual(mock_broadcast.call_count, 1)

    def test_optimize_503_no_coordinator(self):
        """coordinator=None → 503"""
        self.backend_main.coordinator = None
        resp = self.client.post("/api/optimize", json={})
        self.assertEqual(resp.status_code, 503)


if __name__ == "__main__":
    unittest.main()
