"""
Match distance stats tests (iter #15) — /api/persistence/match-distance-stats.

测试覆盖:
- get_match_distance_stats() persistence method
  - total / n_cycles
  - avg / min / max / median
  - distance_distribution 4 桶
- API endpoint
  - 200 with full data
  - 503 when persistence missing
  - empty db returns None stats
"""
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestMatchDistanceStatsPersistence(unittest.TestCase):
    """Persistence.get_match_distance_stats()"""

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
        """Seed 1 cycle with 5 matches at different distances"""
        self.p.begin_cycle(
            cycle_id="mds-1", sim_day=1, sim_hour=8, activity_factor=1.0,
            n_supply_offers=5, n_demand_requests=5,
        )
        self.p.commit_cycle(
            cycle_id="mds-1",
            kpi={"n_matches": 5, "total_tons": 25, "total_cost_sek": 250,
                 "total_co2_kg": 100, "total_distance_km": 50,
                 "n_vehicles_used": 2, "n_vehicles_available": 5,
                 "fleet_utilization_pct": 40, "solver_status": "OPTIMAL"},
            wall_duration_ms=50,
        )
        distances = [5.0, 25.0, 75.0, 150.0, 200.0]  # short / medium / long / very_long / very_long
        for i, d in enumerate(distances):
            self.p.record_match("mds-1", {
                "supply_id": f"SUP{i:03d}",
                "demand_id": f"DEM{i:03d}",
                "material_type": "concrete",
                "tons": 5.0,
                "distance_km": d,
                "estimated_profit_sek": 50.0,
            })

    def test_stats_basic(self):
        result = self.p.get_match_distance_stats()
        self.assertEqual(result["total_matches"], 5)
        self.assertEqual(result["n_cycles_with_matches"], 1)
        self.assertEqual(result["min_distance_km"], 5.0)
        self.assertEqual(result["max_distance_km"], 200.0)
        # avg = (5+25+75+150+200) / 5 = 91.0
        self.assertEqual(result["avg_distance_km"], 91.0)

    def test_stats_distribution(self):
        result = self.p.get_match_distance_stats()
        d = result["distance_distribution"]
        # 5: short <10 (1)
        # 25: medium 10-50 (1)
        # 75: long 50-100 (1)
        # 150, 200: very_long >=100 (2)
        self.assertEqual(d["short_<10km"], 1)
        self.assertEqual(d["medium_10-50km"], 1)
        self.assertEqual(d["long_50-100km"], 1)
        self.assertEqual(d["very_long_>=100km"], 2)

    def test_stats_median(self):
        result = self.p.get_match_distance_stats()
        # sorted: [5, 25, 75, 150, 200] → median = 75 (index 2)
        self.assertEqual(result["median_distance_km"], 75.0)

    def test_stats_empty_db(self):
        empty = tempfile.NamedTemporaryFile(suffix=".db", delete=False, dir="/tmp")
        empty.close()
        try:
            from agents.persistence import Persistence
            p = Persistence(db_path=empty.name)
            result = p.get_match_distance_stats()
            self.assertEqual(result["total_matches"], 0)
            self.assertIsNone(result["avg_distance_km"])
            self.assertIsNone(result["median_distance_km"])
            self.assertEqual(result["distance_distribution"]["short_<10km"], 0)
        finally:
            os.unlink(empty.name)


class TestMatchDistanceStatsApi(unittest.TestCase):
    """/api/persistence/match-distance-stats endpoint"""

    def setUp(self):
        from web.backend import main as backend_main
        from agents.persistence import Persistence

        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False, dir="/tmp")
        self.tmp.close()
        self.db_path = self.tmp.name
        persistence = Persistence(db_path=self.db_path)
        persistence.begin_cycle(
            cycle_id="api-mds-1", sim_day=1, sim_hour=8, activity_factor=1.0,
            n_supply_offers=3, n_demand_requests=3,
        )
        persistence.commit_cycle(
            cycle_id="api-mds-1",
            kpi={"n_matches": 3, "total_tons": 15, "total_cost_sek": 150,
                 "total_co2_kg": 75, "total_distance_km": 30,
                 "n_vehicles_used": 1, "n_vehicles_available": 5,
                 "fleet_utilization_pct": 20, "solver_status": "OPTIMAL"},
            wall_duration_ms=50,
        )
        for i, d in enumerate([3.0, 30.0, 80.0]):
            persistence.record_match("api-mds-1", {
                "supply_id": f"SUP{i:03d}",
                "demand_id": f"DEM{i:03d}",
                "material_type": "concrete",
                "tons": 5.0,
                "distance_km": d,
                "estimated_profit_sek": 50.0,
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
        resp = self.client.get("/api/persistence/match-distance-stats")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["total_matches"], 3)
        self.assertEqual(data["min_distance_km"], 3.0)
        self.assertEqual(data["max_distance_km"], 80.0)
        self.assertIn("distance_distribution", data)

    def test_endpoint_distribution(self):
        resp = self.client.get("/api/persistence/match-distance-stats")
        data = resp.json()
        d = data["distance_distribution"]
        # 3: short, 30: medium, 80: long
        self.assertEqual(d["short_<10km"], 1)
        self.assertEqual(d["medium_10-50km"], 1)
        self.assertEqual(d["long_50-100km"], 1)
        self.assertEqual(d["very_long_>=100km"], 0)

    def test_endpoint_503_no_persistence(self):
        self.backend_main.coordinator.persistence = None
        resp = self.client.get("/api/persistence/match-distance-stats")
        self.assertEqual(resp.status_code, 503)


if __name__ == "__main__":
    unittest.main()
