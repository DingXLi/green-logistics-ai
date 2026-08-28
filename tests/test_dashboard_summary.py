"""
Dashboard summary API tests (iter #11) — /api/dashboard-summary endpoint.

测试覆盖:
- 200 OK with all expected sections (health/summary/efficiency/fleet/last_cycle/scheduler)
- 503 / graceful degradation when coordinator is None
- When persistence is missing, returns nulls not 500
"""
import os
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient


class TestDashboardSummaryAPI(unittest.TestCase):
    """Dashboard summary aggregator endpoint tests"""

    def setUp(self):
        from web.backend import main as backend_main
        from agents.persistence import Persistence

        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False, dir="/tmp")
        self.tmp.close()
        self.db_path = self.tmp.name
        persistence = Persistence(db_path=self.db_path)

        # seed 2 cycles
        for i, (sim_day, matches) in enumerate([(1, 2), (2, 0)], start=1):
            persistence.begin_cycle(
                cycle_id=f"summary-cycle-{i}",
                sim_day=sim_day,
                sim_hour=8,
                activity_factor=1.0,
                n_supply_offers=3,
                n_demand_requests=2,
                seasonal_factor_avg=1.0,
                seasonal_month=sim_day,
            )
            persistence.commit_cycle(
                cycle_id=f"summary-cycle-{i}",
                kpi={
                    "n_matches": matches,
                    "total_tons": 10.0,
                    "total_cost_sek": 100.0,
                    "total_co2_kg": 50.0,
                    "total_distance_km": 20.0,
                    "n_vehicles_used": 3,
                    "n_vehicles_available": 10,
                    "fleet_utilization_pct": 30.0,
                    "solver_status": "OPTIMAL",
                },
                wall_duration_ms=100,
            )

        fake_coord = MagicMock()
        fake_coord.persistence = persistence
        fake_coord._last_cycle_result = {
            "sim_day": 2,
            "sim_hour": 8,
            "total_cost_sek": 100.0,
            "total_co2_kg": 50.0,
            "total_tons": 10.0,
            "n_matches": 0,
            "distance_source": "haversine",
            "fleet_utilization_pct": 30.0,
        }
        backend_main.coordinator = fake_coord
        self.backend_main = backend_main
        self.client = TestClient(backend_main.app)

    def tearDown(self):
        try:
            os.unlink(self.db_path)
        except Exception:
            pass

    def test_dashboard_summary_endpoint_200(self):
        resp = self.client.get("/api/dashboard-summary")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()

        # check top-level keys
        for key in ("timestamp", "health", "summary", "efficiency", "fleet",
                    "last_cycle", "scheduler"):
            self.assertIn(key, data)

        # health has features
        self.assertIsNotNone(data["health"])
        self.assertIn("features", data["health"])
        self.assertIn("websocket_enabled", data["health"]["features"])

        # summary from persistence
        self.assertIsNotNone(data["summary"])
        self.assertEqual(data["summary"]["n_cycles"], 2)

        # efficiency aggregate
        self.assertIsNotNone(data["efficiency"])
        self.assertEqual(data["efficiency"]["n_cycles"], 2)

        # last_cycle from coordinator cache
        self.assertIsNotNone(data["last_cycle"])
        self.assertEqual(data["last_cycle"]["sim_day"], 2)
        self.assertEqual(data["last_cycle"]["distance_source"], "haversine")

        # scheduler absent
        self.assertIsNotNone(data["scheduler"])
        self.assertIn("enabled", data["scheduler"])

    def test_dashboard_summary_with_no_coordinator(self):
        """When coordinator=None, all sections should be null/empty but still 200"""
        from web.backend import main as backend_main
        backend_main.coordinator = None
        resp = self.client.get("/api/dashboard-summary")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        # top-level present, but body sections are None
        self.assertIsNone(data["summary"])
        self.assertIsNone(data["efficiency"])
        self.assertIsNone(data["last_cycle"])
        self.assertIsNone(data["fleet"])
        # health still works
        self.assertIsNotNone(data["health"])

    def test_dashboard_summary_with_persistence_error(self):
        """When persistence raises, sections should have error dict, not 500"""
        from web.backend import main as backend_main
        broken_coord = MagicMock()
        broken_coord.persistence = MagicMock()
        broken_coord.persistence.get_summary.side_effect = RuntimeError("DB locked")
        broken_coord.persistence.get_efficiency_metrics.return_value = {"n_cycles": 0}
        broken_coord._last_cycle_result = None
        backend_main.coordinator = broken_coord
        resp = self.client.get("/api/dashboard-summary")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("error", data["summary"])
        # efficiency should still work (didn't raise)
        self.assertEqual(data["efficiency"]["n_cycles"], 0)

    def test_dashboard_summary_with_no_last_cycle(self):
        """last_cycle should be None when coordinator has no _last_cycle_result"""
        from web.backend import main as backend_main
        empty_coord = MagicMock()
        empty_coord.persistence = self.backend_main.coordinator.persistence
        empty_coord._last_cycle_result = None
        backend_main.coordinator = empty_coord
        resp = self.client.get("/api/dashboard-summary")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIsNone(data["last_cycle"])

    def test_dashboard_summary_health_environment_development(self):
        """Without HF env, environment should be 'development'"""
        # default dev env (no SPACE_ID)
        resp = self.client.get("/api/dashboard-summary")
        data = resp.json()
        self.assertEqual(data["health"]["environment"], "development")


if __name__ == "__main__":
    unittest.main()
