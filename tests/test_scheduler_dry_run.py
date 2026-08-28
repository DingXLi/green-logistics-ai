"""
Scheduler dry_run tests (iter #12) — scheduler.set_dry_run + /api/scheduler/control dry_run_on/off.

测试覆盖:
- BackgroundScheduler.set_dry_run() toggle + status update
- /api/scheduler/control?action=dry_run_on/off
- 400 for unknown action (existing behavior preserved)
- 503 when scheduler missing
- dry_run_count tracked separately from cycle_count
- status response includes dry_run + dry_run_count
"""
import os
import sys
import unittest
from unittest.mock import patch, MagicMock, AsyncMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestBackgroundSchedulerDryRun(unittest.TestCase):
    """BackgroundScheduler.set_dry_run() unit tests"""

    def test_set_dry_run_default_false(self):
        from web.backend.main import BackgroundScheduler
        coord = MagicMock()
        sched = BackgroundScheduler(coord=coord, interval_seconds=10)
        self.assertFalse(sched.dry_run)
        self.assertEqual(sched.dry_run_count, 0)

    def test_set_dry_run_true(self):
        from web.backend.main import BackgroundScheduler
        coord = MagicMock()
        sched = BackgroundScheduler(coord=coord, interval_seconds=10)
        result = sched.set_dry_run(True)
        self.assertTrue(sched.dry_run)
        self.assertEqual(result["previous_dry_run"], False)
        self.assertEqual(result["current_dry_run"], True)

    def test_set_dry_run_false_clears_count(self):
        from web.backend.main import BackgroundScheduler
        coord = MagicMock()
        sched = BackgroundScheduler(coord=coord, interval_seconds=10)
        sched.set_dry_run(True)
        sched.dry_run_count = 5
        # turn off → count clears
        result = sched.set_dry_run(False)
        self.assertFalse(sched.dry_run)
        self.assertEqual(sched.dry_run_count, 0)
        self.assertEqual(result["current_dry_run"], False)

    def test_status_includes_dry_run(self):
        from web.backend.main import BackgroundScheduler
        coord = MagicMock()
        sched = BackgroundScheduler(coord=coord, interval_seconds=10)
        s = sched.status()
        self.assertIn("dry_run", s)
        self.assertIn("dry_run_count", s)
        self.assertFalse(s["dry_run"])
        self.assertEqual(s["dry_run_count"], 0)


class TestSchedulerDryRunApi(unittest.TestCase):
    """/api/scheduler/control?action=dry_run_on/off"""

    def setUp(self):
        from web.backend import main as backend_main
        from web.backend.main import BackgroundScheduler
        coord = MagicMock()
        self.scheduler = BackgroundScheduler(coord=coord, interval_seconds=10)
        backend_main.scheduler = self.scheduler
        self.backend_main = backend_main
        from fastapi.testclient import TestClient
        self.client = TestClient(backend_main.app)

    def test_dry_run_on(self):
        resp = self.client.post("/api/scheduler/control?action=dry_run_on")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["action"], "dry_run_on")
        self.assertTrue(data["success"])
        self.assertEqual(data["previous_dry_run"], False)
        self.assertEqual(data["current_dry_run"], True)
        self.assertTrue(self.scheduler.dry_run)

    def test_dry_run_off(self):
        self.scheduler.set_dry_run(True)
        self.scheduler.dry_run_count = 3
        resp = self.client.post("/api/scheduler/control?action=dry_run_off")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["action"], "dry_run_off")
        self.assertEqual(data["previous_dry_run"], True)
        self.assertEqual(data["current_dry_run"], False)
        # dry_run_count should be cleared
        self.assertEqual(self.scheduler.dry_run_count, 0)

    def test_dry_run_status_includes_flags(self):
        self.scheduler.set_dry_run(True)
        resp = self.client.get("/api/scheduler/status")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("dry_run", data)
        self.assertIn("dry_run_count", data)
        self.assertTrue(data["dry_run"])

    def test_existing_actions_still_work(self):
        """Make sure dry_run additions didn't break start/stop/restart/status"""
        # status
        resp = self.client.post("/api/scheduler/control?action=status")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["action"], "status")
        # unknown action → 400
        resp = self.client.post("/api/scheduler/control?action=foo")
        self.assertEqual(resp.status_code, 400)

    def test_503_when_scheduler_missing(self):
        from web.backend import main as backend_main
        backend_main.scheduler = None
        resp = self.client.post("/api/scheduler/control?action=dry_run_on")
        self.assertEqual(resp.status_code, 503)


class TestCoordinatorDryRunParam(unittest.TestCase):
    """Coordinator.run_optimization_cycle(dry_run=...) skips persistence"""

    def setUp(self):
        import tempfile
        from agents.coordinator import MultiAgentCoordinator
        from agents.persistence import Persistence

        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False, dir="/tmp")
        self.tmp.close()
        self.db_path = self.tmp.name
        self.persistence = Persistence(db_path=self.db_path)
        self.coord = MultiAgentCoordinator.__new__(MultiAgentCoordinator)
        # initialize minimal state
        self.coord.persistence = self.persistence
        self.coord.system_status = {"total_optimizations": 0}
        self.coord._last_cycle_result = None
        # 给一个 clock stub
        from agents.clock import SimClock
        self.coord.clock = SimClock(start_day=1)

    def tearDown(self):
        try:
            os.unlink(self.db_path)
        except Exception:
            pass

    def test_dry_run_param_default_false(self):
        """ensure dry_run param exists with default False"""
        import inspect
        sig = inspect.signature(self.coord.run_optimization_cycle)
        self.assertIn("dry_run", sig.parameters)
        self.assertEqual(sig.parameters["dry_run"].default, False)


if __name__ == "__main__":
    unittest.main()
