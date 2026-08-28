"""
Health deep endpoint tests (iter #14) — /api/health/deep.

测试覆盖:
- 200 OK with all expected check sections (database/websocket/osm/scheduler/llm/agents)
- status field reflects overall health (ok/degraded/down)
- 503 paths handled gracefully (no exceptions leak)
- LLM check degrades when GOOGLE_API_KEY missing
- Database down → overall_status = down
"""
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient


class TestHealthDeepEndpoint(unittest.TestCase):
    """/api/health/deep 多子系统检查"""

    def setUp(self):
        from web.backend import main as backend_main
        from agents.persistence import Persistence

        self.tmp_path = f"/tmp/test_health_deep_{os.getpid()}.db"
        persistence = Persistence(db_path=self.tmp_path)
        # seed 1 cycle
        persistence.begin_cycle(
            cycle_id="hd-1", sim_day=1, sim_hour=8, activity_factor=1.0,
            n_supply_offers=2, n_demand_requests=1,
        )
        persistence.commit_cycle(
            cycle_id="hd-1",
            kpi={"n_matches": 1, "total_tons": 5, "total_cost_sek": 50,
                 "total_co2_kg": 25, "total_distance_km": 10,
                 "n_vehicles_used": 1, "n_vehicles_available": 5,
                 "fleet_utilization_pct": 20, "solver_status": "OPTIMAL"},
            wall_duration_ms=50,
        )

        coord = MagicMock()
        coord.persistence = persistence
        coord.supply_agents = {"SUP000": MagicMock(), "SUP001": MagicMock()}
        coord.market_agent = MagicMock()
        coord.market_agent.demand_points = [{"id": "DEM000"}, {"id": "DEM001"}]
        coord.logistics_agent = MagicMock()
        coord.logistics_agent.vehicles = [
            {"vehicle_id": "V000", "status": "available"},
            {"vehicle_id": "V001", "status": "available"},
        ]

        backend_main.coordinator = coord
        self.backend_main = backend_main
        self.client = TestClient(backend_main.app)

    def tearDown(self):
        try:
            os.unlink(self.tmp_path)
        except Exception:
            pass

    def test_health_deep_returns_200(self):
        resp = self.client.get("/api/health/deep")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("status", data)
        self.assertIn("timestamp", data)
        self.assertIn("checks", data)

    def test_health_deep_includes_all_sections(self):
        resp = self.client.get("/api/health/deep")
        data = resp.json()
        checks = data["checks"]
        for section in ("database", "websocket", "osm", "scheduler",
                        "llm", "agents"):
            self.assertIn(section, checks, f"Missing {section} section")

    def test_health_deep_database_ok(self):
        resp = self.client.get("/api/health/deep")
        data = resp.json()
        db = data["checks"]["database"]
        self.assertEqual(db["status"], "ok")
        self.assertEqual(db["n_cycles"], 1)

    def test_health_deep_agents_counts(self):
        resp = self.client.get("/api/health/deep")
        data = resp.json()
        agents = data["checks"]["agents"]
        self.assertEqual(agents["n_supply"], 2)
        self.assertEqual(agents["n_demand"], 2)
        self.assertEqual(agents["n_vehicles"], 2)

    def test_health_deep_no_api_key(self):
        """GOOGLE_API_KEY 未设 → LLM degraded"""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GOOGLE_API_KEY", None)
            resp = self.client.get("/api/health/deep")
        data = resp.json()
        llm = data["checks"]["llm"]
        self.assertEqual(llm["status"], "degraded")
        self.assertFalse(llm["api_key_set"])

    def test_health_deep_with_api_key(self):
        """GOOGLE_API_KEY 设置 → LLM ok"""
        with patch.dict(os.environ, {"GOOGLE_API_KEY": "test-key"}):
            resp = self.client.get("/api/health/deep")
        data = resp.json()
        llm = data["checks"]["llm"]
        self.assertEqual(llm["status"], "ok")
        self.assertTrue(llm["api_key_set"])
        self.assertIn("model", llm)

    def test_health_deep_no_coordinator_graceful(self):
        """coordinator=None → agents.status=down, overall_status=down"""
        self.backend_main.coordinator = None
        resp = self.client.get("/api/health/deep")
        # 即使 coordinator=None 也不应抛 500
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["checks"]["agents"]["status"], "down")
        self.assertEqual(data["status"], "down")

    def test_health_deep_no_persistence_graceful(self):
        """persistence=None → database degraded, overall degraded"""
        self.backend_main.coordinator.persistence = None
        resp = self.client.get("/api/health/deep")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["checks"]["database"]["status"], "degraded")
        # overall 应该是 degraded (or down)
        self.assertIn(data["status"], ("degraded", "down"))

    def test_health_deep_database_error_graceful(self):
        """DB 抛异常 → database.status=down, 不抛 500"""
        # 用 MagicMock 包装, 让 get_summary 抛异常
        broken_db = MagicMock()
        broken_db.get_summary.side_effect = RuntimeError("DB locked")
        self.backend_main.coordinator.persistence = broken_db
        resp = self.client.get("/api/health/deep")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["checks"]["database"]["status"], "down")
        self.assertIn("error", data["checks"]["database"])
        self.assertEqual(data["status"], "down")

    def test_health_deep_scheduler_idle(self):
        """scheduler=None → scheduler.status=idle"""
        resp = self.client.get("/api/health/deep")
        data = resp.json()
        self.assertEqual(data["checks"]["scheduler"]["status"], "idle")

    def test_health_deep_overall_status_reflects_db(self):
        """DB down → overall=down, 但其他都 degraded 时 overall=degraded"""
        resp = self.client.get("/api/health/deep")
        data = resp.json()
        # 默认情况: DB ok, LLM 可能 degraded
        # overall 至少是 ok 或 degraded
        self.assertIn(data["status"], ("ok", "degraded"))


if __name__ == "__main__":
    unittest.main()
