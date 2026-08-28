"""
Cycle history tests (iter #11) — /api/persistence/cycle-history + /cycle-detail/{id}.

测试覆盖:
- get_cycle_history() persistence method (limit, sim_day 过滤, has_matches_only, n_routes join)
- get_cycle_detail() persistence method (full data 返回, 不存在 → None)
- API endpoints (200/404/503)
"""
import os
import sys
import tempfile
import unittest
from typing import Any, Dict, List
from unittest.mock import patch, MagicMock

# 确保 import 路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient


class TestCycleHistoryPersistence(unittest.TestCase):
    """Persistence.get_cycle_history() 方法单元测试"""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(
            suffix=".db", delete=False, dir="/tmp"
        )
        self.tmp.close()
        self.db_path = self.tmp.name
        from agents.persistence import Persistence
        self.p = Persistence(db_path=self.db_path)

        # seed 5 个 cycles (不同 sim_day, match 数, route 数)
        self._seed_cycles()

    def tearDown(self):
        try:
            os.unlink(self.db_path)
        except Exception:
            pass

    def _record_cycle(self, cycle_id: str, sim_day: int, sim_hour: int,
                      activity: float, n_supply: int, n_demand: int,
                      n_matches: int, n_routes: int = 0,
                      total_tons: float = 10.0, total_cost: float = 100.0,
                      total_co2: float = 50.0, total_distance: float = 20.0,
                      n_vehicles_used: int = 5, n_vehicles_avail: int = 10,
                      util_pct: float = 50.0):
        """辅助: 记一个完整 cycle (cycle row + supplies + demands + matches + routes)"""
        self.p.begin_cycle(
            cycle_id=cycle_id,
            sim_day=sim_day,
            sim_hour=sim_hour,
            activity_factor=activity,
            n_supply_offers=n_supply,
            n_demand_requests=n_demand,
            seasonal_factor_avg=1.0,
            seasonal_month=((sim_day // 30) % 12 + 1),
        )
        self.p.commit_cycle(
            cycle_id=cycle_id,
            kpi={
                "n_matches": n_matches,
                "total_tons": total_tons,
                "total_cost_sek": total_cost,
                "total_co2_kg": total_co2,
                "total_distance_km": total_distance,
                "n_vehicles_used": n_vehicles_used,
                "n_vehicles_available": n_vehicles_avail,
                "fleet_utilization_pct": util_pct,
                "solver_status": "OPTIMAL",
            },
            wall_duration_ms=100,
        )
        # supply/demand offer rows
        for i in range(n_supply):
            self.p.record_supply(cycle_id, {
                "agent_id": f"SUP{i:03d}",
                "location": {"lat": 57.7 + i * 0.01, "lon": 12.9},
                "material_type": "concrete",
                "available_tons": 5.0,
                "moisture_percent": 20.0,
                "quality_score": 80.0,
            })
        for i in range(n_demand):
            self.p.record_demand(cycle_id, {
                "id": f"DEM{i:03d}",
                "name": f"Project {i}",
                "location": {"lat": 57.7, "lon": 12.9},
                "material_type": "concrete",
                "required_tons": 5.0,
                "priority": "high",
                "deadline": "2026-12-31",
            })
        for i in range(n_matches):
            self.p.record_match(cycle_id, {
                "supply_id": f"SUP{i:03d}",
                "demand_id": f"DEM{i:03d}",
                "material_type": "concrete",
                "tons": 5.0,
                "distance_km": 10.0,
                "estimated_profit_sek": 50.0,
            })
        for i in range(n_routes):
            self.p.record_route(cycle_id, {
                "vehicle_id": f"V{i:03d}",
                "stops": ["DEPOT", f"SUP{i:03d}", f"DEM{i:03d}"],
                "distance_km": 10.0,
                "duration_hours": 1.5,
                "cost_sek": 200.0,
                "co2_kg": 30.0,
            })

    def _seed_cycles(self):
        self._record_cycle("c1", sim_day=1, sim_hour=8, activity=1.0,
                           n_supply=3, n_demand=2, n_matches=2, n_routes=1)
        self._record_cycle("c2", sim_day=2, sim_hour=8, activity=1.0,
                           n_supply=3, n_demand=2, n_matches=0, n_routes=0)
        self._record_cycle("c3", sim_day=3, sim_hour=8, activity=0.5,
                           n_supply=4, n_demand=3, n_matches=3, n_routes=2)
        self._record_cycle("c4", sim_day=4, sim_hour=8, activity=1.2,
                           n_supply=2, n_demand=2, n_matches=1, n_routes=1)
        self._record_cycle("c5", sim_day=5, sim_hour=8, activity=1.0,
                           n_supply=3, n_demand=3, n_matches=2, n_routes=2)

    def test_cycle_history_returns_all_cycles(self):
        result = self.p.get_cycle_history(limit=10)
        self.assertEqual(len(result), 5)
        # 按 id DESC → c5 first
        self.assertEqual(result[0]["cycle_id"], "c5")
        self.assertEqual(result[-1]["cycle_id"], "c1")

    def test_cycle_history_includes_n_routes(self):
        result = self.p.get_cycle_history(limit=10)
        # c5 → 2 routes
        c5 = next(c for c in result if c["cycle_id"] == "c5")
        self.assertEqual(c5["n_routes"], 2)
        # c2 → 0 routes
        c2 = next(c for c in result if c["cycle_id"] == "c2")
        self.assertEqual(c2["n_routes"], 0)

    def test_cycle_history_includes_kpi(self):
        result = self.p.get_cycle_history(limit=10)
        c3 = next(c for c in result if c["cycle_id"] == "c3")
        self.assertEqual(c3["n_matches"], 3)
        self.assertEqual(c3["total_cost_sek"], 100.0)
        self.assertEqual(c3["total_co2_kg"], 50.0)
        self.assertEqual(c3["fleet_utilization_pct"], 50.0)
        self.assertEqual(c3["solver_status"], "OPTIMAL")

    def test_cycle_history_limit(self):
        result = self.p.get_cycle_history(limit=2)
        self.assertEqual(len(result), 2)

    def test_cycle_history_sim_day_filter(self):
        result = self.p.get_cycle_history(limit=10, sim_day_min=3, sim_day_max=4)
        self.assertEqual(len(result), 2)
        sim_days = sorted([c["sim_day"] for c in result])
        self.assertEqual(sim_days, [3, 4])

    def test_cycle_history_has_matches_only(self):
        result = self.p.get_cycle_history(limit=10, has_matches_only=True)
        # c2 has 0 matches → excluded
        self.assertEqual(len(result), 4)
        for c in result:
            self.assertGreater(c["n_matches"], 0)

    def test_export_cycles_csv_returns_header_and_rows(self):
        csv_str = self.p.export_cycles_csv(limit=10)
        lines = csv_str.strip().split("\n")
        # header + 5 cycles
        self.assertEqual(len(lines), 6)
        # 验证 header 列名 (strip \r from DictWriter)
        header_cols = [c.strip() for c in lines[0].split(",")]
        self.assertIn("cycle_id", header_cols)
        self.assertIn("n_matches", header_cols)
        self.assertIn("total_cost_sek", header_cols)
        self.assertIn("seasonal_month", header_cols)
        # 第一行数据应该是 c5 (newest first)
        first_row = [c.strip() for c in lines[1].split(",")]
        self.assertEqual(first_row[header_cols.index("cycle_id")], "c5")

    def test_export_cycles_csv_empty_db_returns_header_only(self):
        empty_path = tempfile.NamedTemporaryFile(suffix=".db", delete=False, dir="/tmp").name
        try:
            from agents.persistence import Persistence
            empty_p = Persistence(db_path=empty_path)
            csv_str = empty_p.export_cycles_csv(limit=10)
            lines = csv_str.strip().split("\n")
            self.assertEqual(len(lines), 1)
            self.assertIn("cycle_id", lines[0])
        finally:
            os.unlink(empty_path)

    def test_export_cycles_csv_limit(self):
        csv_str = self.p.export_cycles_csv(limit=2)
        lines = csv_str.strip().split("\n")
        # header + 2 cycles
        self.assertEqual(len(lines), 3)

    def test_cycle_history_empty_db(self):
        empty_path = tempfile.NamedTemporaryFile(suffix=".db", delete=False, dir="/tmp").name
        try:
            from agents.persistence import Persistence
            empty_p = Persistence(db_path=empty_path)
            result = empty_p.get_cycle_history(limit=10)
            self.assertEqual(result, [])
        finally:
            os.unlink(empty_path)

    def test_cycle_detail_returns_full_data(self):
        detail = self.p.get_cycle_detail("c3")
        self.assertIsNotNone(detail)
        self.assertEqual(detail["cycle"]["cycle_id"], "c3")
        self.assertEqual(detail["cycle"]["n_matches"], 3)
        # supply_offers: 4 (c3 has n_supply=4)
        self.assertEqual(len(detail["supply_offers"]), 4)
        # demand_requests: 3
        self.assertEqual(len(detail["demand_requests"]), 3)
        # matches: 3
        self.assertEqual(len(detail["matches"]), 3)
        # routes: 2 (with stops parsed)
        self.assertEqual(len(detail["routes"]), 2)
        self.assertEqual(detail["routes"][0]["vehicle_id"], "V000")
        self.assertEqual(detail["routes"][0]["stops"], ["DEPOT", "SUP000", "DEM000"])
        self.assertEqual(detail["routes"][0]["distance_km"], 10.0)
        self.assertEqual(detail["routes"][0]["cost_sek"], 200.0)

    def test_cycle_detail_returns_none_for_missing(self):
        detail = self.p.get_cycle_detail("nonexistent-id")
        self.assertIsNone(detail)

    def test_cycle_detail_zero_matches_cycle(self):
        # c2 has 0 matches/routes
        detail = self.p.get_cycle_detail("c2")
        self.assertIsNotNone(detail)
        self.assertEqual(detail["cycle"]["n_matches"], 0)
        self.assertEqual(len(detail["matches"]), 0)
        self.assertEqual(len(detail["routes"]), 0)


class TestCycleHistoryAPI(unittest.TestCase):
    """FastAPI endpoints for /api/persistence/cycle-history + cycle-detail/{id}"""

    def setUp(self):
        # import main 后替换 coordinator
        from web.backend import main as backend_main
        from agents.persistence import Persistence

        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False, dir="/tmp")
        self.tmp.close()
        self.db_path = self.tmp.name
        persistence = Persistence(db_path=self.db_path)
        # seed 一个 cycle
        persistence.begin_cycle(
            cycle_id="api-cycle-1",
            sim_day=1,
            sim_hour=8,
            activity_factor=1.0,
            n_supply_offers=2,
            n_demand_requests=1,
            seasonal_factor_avg=1.0,
            seasonal_month=1,
        )
        persistence.commit_cycle(
            cycle_id="api-cycle-1",
            kpi={
                "n_matches": 1,
                "total_tons": 5.0,
                "total_cost_sek": 50.0,
                "total_co2_kg": 25.0,
                "total_distance_km": 10.0,
                "n_vehicles_used": 2,
                "n_vehicles_available": 5,
                "fleet_utilization_pct": 40.0,
                "solver_status": "OPTIMAL",
            },
            wall_duration_ms=100,
        )

        fake_coord = MagicMock()
        fake_coord.persistence = persistence
        backend_main.coordinator = fake_coord
        self.backend_main = backend_main
        self.client = TestClient(backend_main.app)

    def tearDown(self):
        try:
            os.unlink(self.db_path)
        except Exception:
            pass

    def test_cycle_history_endpoint_200(self):
        resp = self.client.get("/api/persistence/cycle-history")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["cycle_id"], "api-cycle-1")
        self.assertEqual(data[0]["n_matches"], 1)

    def test_cycle_history_endpoint_with_limit(self):
        resp = self.client.get("/api/persistence/cycle-history?limit=10")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.json()), 1)

    def test_cycle_history_endpoint_sim_day_filter(self):
        resp = self.client.get("/api/persistence/cycle-history?sim_day_min=1&sim_day_max=1")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.json()), 1)

    def test_cycle_history_endpoint_has_matches_only(self):
        resp = self.client.get("/api/persistence/cycle-history?has_matches_only=true")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.json()), 1)

        resp_empty = self.client.get("/api/persistence/cycle-history?has_matches_only=false")
        self.assertEqual(resp_empty.status_code, 200)

    def test_cycle_detail_endpoint_200(self):
        resp = self.client.get("/api/persistence/cycle-detail/api-cycle-1")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["cycle"]["cycle_id"], "api-cycle-1")
        self.assertIn("supply_offers", data)
        self.assertIn("demand_requests", data)
        self.assertIn("matches", data)
        self.assertIn("routes", data)

    def test_cycle_detail_endpoint_404(self):
        resp = self.client.get("/api/persistence/cycle-detail/nonexistent")
        self.assertEqual(resp.status_code, 404)

    def test_cycle_history_endpoint_503_no_coordinator(self):
        from web.backend import main as backend_main
        backend_main.coordinator = None
        resp = self.client.get("/api/persistence/cycle-history")
        self.assertEqual(resp.status_code, 503)

    def test_export_cycles_csv_endpoint_200(self):
        resp = self.client.get("/api/persistence/export/cycles.csv")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/csv", resp.headers.get("content-type", ""))
        self.assertIn("attachment", resp.headers.get("content-disposition", ""))
        # body should have header + 1 cycle
        lines = resp.text.strip().split("\n")
        self.assertEqual(len(lines), 2)
        self.assertIn("cycle_id", lines[0])

    def test_export_cycles_csv_endpoint_with_limit(self):
        resp = self.client.get("/api/persistence/export/cycles.csv?limit=500")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("cycles_500", resp.headers.get("content-disposition", ""))

    def test_export_cycles_csv_endpoint_503(self):
        from web.backend import main as backend_main
        backend_main.coordinator = None
        resp = self.client.get("/api/persistence/export/cycles.csv")
        self.assertEqual(resp.status_code, 503)


if __name__ == "__main__":
    unittest.main()
