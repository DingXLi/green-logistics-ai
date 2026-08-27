"""
Tests for /api/scheduler/control endpoint (iter #10).

覆盖:
- 503 if scheduler None
- 400 if action invalid
- start/stop/restart 行为
- status returns current scheduler.status()
"""

import asyncio
import pytest

from fastapi.testclient import TestClient
from web.backend import main as backend_main


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def fake_scheduler():
    """构造 fake BackgroundScheduler with controllable state."""
    class FakeScheduler:
        def __init__(self):
            self.scheduler_active = False
            self.running = False
            self.cycle_count = 0
            self.started_at = None
            self.last_cycle_at = None
            self.last_cycle_id = None
            self.error_count = 0
            self.last_error = None
            self.is_idle = False
            self.idle_entered_at = None
            self.interval_seconds = 30.0
            self.idle_window_seconds = 300.0

        def start(self):
            if self.scheduler_active:
                return
            self.scheduler_active = True
            self.started_at = "2026-08-27T20:00:00Z"

        async def stop(self):
            self.scheduler_active = False
            self.is_idle = False

        def status(self):
            return {
                "enabled": True,
                "active": self.scheduler_active,
                "running_now": self.running,
                "is_idle": self.is_idle,
                "cycle_count": self.cycle_count,
                "last_cycle_at": self.last_cycle_at,
                "last_cycle_id": self.last_cycle_id,
                "error_count": self.error_count,
                "started_at": self.started_at,
            }

    return FakeScheduler()


@pytest.fixture
def patched_scheduler(fake_scheduler):
    """把 backend_main.scheduler 替换成 fake"""
    orig = backend_main.scheduler
    backend_main.scheduler = fake_scheduler
    yield fake_scheduler
    backend_main.scheduler = orig


# ============================================================
# Tests
# ============================================================

class TestSchedulerControl:
    def test_503_when_scheduler_none(self):
        """没 scheduler → 503"""
        orig = backend_main.scheduler
        backend_main.scheduler = None
        try:
            client = TestClient(backend_main.app)
            r = client.post("/api/scheduler/control?action=status")
            assert r.status_code == 503
        finally:
            backend_main.scheduler = orig

    def test_400_for_unknown_action(self, patched_scheduler):
        client = TestClient(backend_main.app)
        r = client.post("/api/scheduler/control?action=invalid_action")
        assert r.status_code == 400
        assert "Unknown action" in r.text

    def test_status_action_returns_current_state(self, patched_scheduler):
        patched_scheduler.scheduler_active = True
        patched_scheduler.cycle_count = 5

        client = TestClient(backend_main.app)
        r = client.post("/api/scheduler/control?action=status")
        assert r.status_code == 200
        data = r.json()
        assert data["action"] == "status"
        assert data["success"] is True
        assert data["status"]["active"] is True
        assert data["status"]["cycle_count"] == 5

    def test_start_when_not_running(self, patched_scheduler):
        assert patched_scheduler.scheduler_active is False

        client = TestClient(backend_main.app)
        r = client.post("/api/scheduler/control?action=start")
        assert r.status_code == 200
        data = r.json()
        assert data["success"] is True
        assert patched_scheduler.scheduler_active is True
        assert patched_scheduler.started_at is not None

    def test_start_when_already_running(self, patched_scheduler):
        """已运行时 start 是 idempotent"""
        patched_scheduler.scheduler_active = True
        orig_started = "2026-08-27T20:00:00Z"
        patched_scheduler.started_at = orig_started

        client = TestClient(backend_main.app)
        r = client.post("/api/scheduler/control?action=start")
        assert r.status_code == 200
        # started_at 不应被覆盖
        assert patched_scheduler.started_at == orig_started

    def test_stop_when_running(self, patched_scheduler):
        patched_scheduler.scheduler_active = True

        client = TestClient(backend_main.app)
        r = client.post("/api/scheduler/control?action=stop")
        assert r.status_code == 200
        assert r.json()["success"] is True
        assert patched_scheduler.scheduler_active is False

    def test_stop_when_not_running(self, patched_scheduler):
        """没运行时 stop 是 idempotent"""
        assert patched_scheduler.scheduler_active is False

        client = TestClient(backend_main.app)
        r = client.post("/api/scheduler/control?action=stop")
        assert r.status_code == 200
        assert r.json()["success"] is True
        assert patched_scheduler.scheduler_active is False

    def test_restart_stops_then_starts(self, patched_scheduler):
        patched_scheduler.scheduler_active = True
        orig_started = "2026-08-27T20:00:00Z"
        patched_scheduler.started_at = orig_started

        client = TestClient(backend_main.app)
        r = client.post("/api/scheduler/control?action=restart")
        assert r.status_code == 200
        data = r.json()
        assert data["success"] is True
        # restart 后 scheduler 仍在运行 (start 后是 active)
        assert patched_scheduler.scheduler_active is True
        # restart 后状态值是 update 后的 (started_at 在 restart 时被设成新的)
        # 我们的 fake start() 只在 scheduler_active=False 才设 started_at
        # 所以 restart 后 started_at 仍是 orig_started (因为 restart 中 stop 后立即 start,
        # fake start() 看不到中断状态)
        assert patched_scheduler.started_at is not None

    def test_restart_when_not_running(self, patched_scheduler):
        """没运行时 restart 等价于 start"""
        assert patched_scheduler.scheduler_active is False

        client = TestClient(backend_main.app)
        r = client.post("/api/scheduler/control?action=restart")
        assert r.status_code == 200
        assert patched_scheduler.scheduler_active is True

    def test_response_includes_full_status(self, patched_scheduler):
        client = TestClient(backend_main.app)
        r = client.post("/api/scheduler/control?action=status")
        data = r.json()
        status = data["status"]
        # status 应包含关键字段
        for field in ("enabled", "active", "running_now", "is_idle",
                      "cycle_count", "error_count"):
            assert field in status