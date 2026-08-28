"""
Optimize /last tests (iter #12) — /api/optimize/last extended metrics.

测试覆盖:
- /api/optimize/last 返回新的 last_* 字段 (sim_day/hour/matches/seasonal/etc)
- cost_per_ton / co2_per_ton 计算正确
- efficiency 字段包含全期聚合
- 503 when coordinator missing
- 503 when persistence missing
"""
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient


class TestOptimizeLastExtended(unittest.TestCase):
    """/api/optimize/last 返回完整 metrics"""

    def setUp(self):
        from web.backend import main as backend_main
        from agents.persistence import Persistence

        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False, dir="/tmp")
        self.tmp.close()
        self.db_path = self.tmp.name
        persistence = Persistence(db_path=self.db_path)
        # seed 3 cycles with varied data
        for i in range(3):
            cid = f"last-cycle-{i+1}"
            persistence.begin_cycle(
                cycle_id=cid,
                sim_day=i + 1,
                sim_hour=8,
                activity_factor=1.0,
                n_supply_offers=4,
                n_demand_requests=3,
                seasonal_factor_avg=0.85 + i * 0.05,
                seasonal_month=i + 1,
            )
            persistence.commit_cycle(
                cycle_id=cid,
                kpi={
                    "n_matches": 3 - i,  # 3, 2, 1
                    "total_tons": 30.0 - i * 5,  # 30, 25, 20
                    "total_cost_sek": 200.0 - i * 30,  # 200, 170, 140
                    "total_co2_kg": 100.0 - i * 15,  # 100, 85, 70
                    "total_distance_km": 50.0,
                    "n_vehicles_used": 3 + i,
                    "n_vehicles_available": 10,
                    "fleet_utilization_pct": 30.0 + i * 10,
                    "solver_status": "OPTIMAL",
                },
                wall_duration_ms=100 + i * 50,
            )

        fake_coord = MagicMock()
        fake_coord.persistence = persistence
        fake_coord._last_cycle_result = {
            "distance_source": "haversine",
            "sim_day": 3,
        }
        backend_main.coordinator = fake_coord
        self.backend_main = backend_main
        self.client = TestClient(backend_main.app)

    def tearDown(self):
        try:
            os.unlink(self.db_path)
        except Exception:
            pass

    def test_optimize_last_basic_fields(self):
        resp = self.client.get("/api/optimize/last")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        # basic fields
        self.assertEqual(data["last_cycle_id"], "last-cycle-3")  # newest first
        self.assertEqual(data["total_cycles"], 3)
        self.assertEqual(data["distance_source"], "haversine")

    def test_optimize_last_extended_last_cycle_fields(self):
        """iter #12: 新的 last_* 字段"""
        resp = self.client.get("/api/optimize/last")
        data = resp.json()
        # last-cycle-3 has sim_day=3, n_matches=1
        self.assertEqual(data["last_sim_day"], 3)
        self.assertEqual(data["last_sim_hour"], 8)
        self.assertEqual(data["last_n_matches"], 1)
        # seasonal (last-cycle-3 has seasonal_factor_avg=0.95, month=3)
        self.assertEqual(round(data["last_seasonal_factor_avg"], 2), 0.95)
        self.assertEqual(data["last_seasonal_month"], 3)
        # solver_status
        self.assertEqual(data["last_solver_status"], "OPTIMAL")
        # fleet
        self.assertEqual(data["last_fleet_utilization_pct"], 50.0)
        # distance
        self.assertEqual(data["last_distance_km"], 50.0)

    def test_optimize_last_cost_per_ton_calc(self):
        """last_cost_per_ton_sek = last_cost / last_tons = 140 / 20 = 7.0"""
        resp = self.client.get("/api/optimize/last")
        data = resp.json()
        # last-cycle-3: cost=140, tons=20 → 7.0
        self.assertEqual(data["last_cost_per_ton_sek"], 7.0)
        # co2: 70 / 20 = 3.5
        self.assertEqual(data["last_co2_per_ton_kg"], 3.5)

    def test_optimize_last_efficiency_section(self):
        """efficiency 字段含全期聚合 (3 cycles)"""
        resp = self.client.get("/api/optimize/last")
        data = resp.json()
        self.assertIn("efficiency", data)
        self.assertIsNotNone(data["efficiency"])
        self.assertEqual(data["efficiency"]["n_cycles"], 3)
        # total_tons = 30+25+20 = 75
        self.assertEqual(round(data["efficiency"]["total_tons"], 2), 75.0)

    def test_optimize_last_age_seconds(self):
        """age_seconds 应是 positive 且 reasonable"""
        resp = self.client.get("/api/optimize/last")
        data = resp.json()
        self.assertIsNotNone(data["age_seconds"])
        self.assertGreaterEqual(data["age_seconds"], 0)  # can be 0 (just seeded)
        self.assertLess(data["age_seconds"], 60)  # 刚 seed 的, 不到 1 min

    def test_optimize_last_503_no_coordinator(self):
        self.backend_main.coordinator = None
        resp = self.client.get("/api/optimize/last")
        self.assertEqual(resp.status_code, 503)

    def test_optimize_last_503_no_persistence(self):
        fake_coord = MagicMock()
        fake_coord.persistence = None
        self.backend_main.coordinator = fake_coord
        resp = self.client.get("/api/optimize/last")
        self.assertEqual(resp.status_code, 503)


if __name__ == "__main__":
    unittest.main()
