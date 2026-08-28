"""
DB stats tests (iter #15) — /api/admin/db-stats.

测试覆盖:
- Persistence.get_db_stats() returns table counts, indexes, size, time_range
- API endpoint 200 with full data
- 503 when persistence missing
- Empty DB returns 0 counts
"""
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestDbStatsPersistence(unittest.TestCase):
    """Persistence.get_db_stats()"""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False, dir="/tmp")
        self.tmp.close()
        self.db_path = self.tmp.name
        from agents.persistence import Persistence
        self.p = Persistence(db_path=self.db_path)
        # seed 1 cycle
        self.p.begin_cycle(
            cycle_id="stats-1", sim_day=1, sim_hour=8, activity_factor=1.0,
            n_supply_offers=2, n_demand_requests=2,
        )
        self.p.commit_cycle(
            cycle_id="stats-1",
            kpi={"n_matches": 2, "total_tons": 10, "total_cost_sek": 100,
                 "total_co2_kg": 50, "total_distance_km": 20,
                 "n_vehicles_used": 1, "n_vehicles_available": 5,
                 "fleet_utilization_pct": 20, "solver_status": "OPTIMAL"},
            wall_duration_ms=50,
        )
        for i in range(2):
            self.p.record_supply("stats-1", {
                "agent_id": f"SUP{i:03d}", "location": {"lat": 57.7, "lon": 12.9},
                "material_type": "concrete", "available_tons": 5,
            })
            self.p.record_demand("stats-1", {
                "id": f"DEM{i:03d}", "location": {"lat": 57.7, "lon": 12.9},
                "material_type": "concrete", "required_tons": 5,
            })
            self.p.record_match("stats-1", {
                "supply_id": f"SUP{i:03d}", "demand_id": f"DEM{i:03d}",
                "material_type": "concrete", "tons": 5,
                "distance_km": 10.0, "estimated_profit_sek": 50,
            })

    def tearDown(self):
        try:
            os.unlink(self.db_path)
        except Exception:
            pass

    def test_db_stats_basic(self):
        result = self.p.get_db_stats()
        self.assertIn("db_path", result)
        # db_path 是 PosixPath 对象, 转 str 比较
        self.assertEqual(str(result["db_path"]), self.db_path)
        self.assertGreater(result["db_size_bytes"], 0)
        self.assertGreater(result["db_size_mb"], 0)

    def test_db_stats_table_counts(self):
        result = self.p.get_db_stats()
        tc = result["table_counts"]
        self.assertEqual(tc["optimization_cycles"], 1)
        self.assertEqual(tc["supply_offers"], 2)
        self.assertEqual(tc["demand_requests"], 2)
        self.assertEqual(tc["matches"], 2)
        self.assertEqual(tc["routes"], 0)
        self.assertEqual(tc["llm_decisions"], 0)

    def test_db_stats_total_rows(self):
        result = self.p.get_db_stats()
        self.assertEqual(result["total_rows"], 7)  # 1 + 2 + 2 + 2 = 7

    def test_db_stats_indexes(self):
        result = self.p.get_db_stats()
        idx_names = [i["name"] for i in result["indexes"]]
        # should have our custom indexes
        self.assertIn("idx_cycles_day", idx_names)
        self.assertIn("idx_supply_cycle", idx_names)
        self.assertIn("idx_demand_cycle", idx_names)
        self.assertIn("idx_matches_cycle", idx_names)
        self.assertIn("idx_routes_cycle", idx_names)

    def test_db_stats_time_range(self):
        result = self.p.get_db_stats()
        tr = result["time_range"]
        self.assertIsNotNone(tr.get("oldest_cycle"))
        self.assertIsNotNone(tr.get("newest_cycle"))


class TestDbStatsApi(unittest.TestCase):
    """/api/admin/db-stats endpoint"""

    def setUp(self):
        from web.backend import main as backend_main
        from agents.persistence import Persistence

        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False, dir="/tmp")
        self.tmp.close()
        self.db_path = self.tmp.name
        persistence = Persistence(db_path=self.db_path)

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
        resp = self.client.get("/api/admin/db-stats")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("db_path", data)
        self.assertIn("db_size_bytes", data)
        self.assertIn("table_counts", data)
        self.assertIn("indexes", data)

    def test_endpoint_empty_db(self):
        resp = self.client.get("/api/admin/db-stats")
        data = resp.json()
        # all tables 0
        for table, count in data["table_counts"].items():
            self.assertEqual(count, 0, f"{table} should be 0")
        self.assertEqual(data["total_rows"], 0)

    def test_endpoint_503_no_persistence(self):
        self.backend_main.coordinator.persistence = None
        resp = self.client.get("/api/admin/db-stats")
        self.assertEqual(resp.status_code, 503)


if __name__ == "__main__":
    unittest.main()
