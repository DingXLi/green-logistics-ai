"""
Tests for WebSocket broadcaster enhanced stats (iter #27).

_iter #27 改进_: ws_broadcaster.stats() 现在返回更详细的字段:
- total_broadcasts: 调用 broadcast() 的总次数
- total_sends: 总发送尝试 (per-client)
- total_send_failures: 失败发送
- success_rate_pct: 成功率
- last_broadcast_at: 上次 broadcast 的 ISO timestamp

新 endpoint: POST /api/ws/stats/reset (测试 / 调试用)。
"""

import asyncio
import json
import pytest
from datetime import datetime


# ============================================
# stats() unit tests
# ============================================

class TestBroadcasterStats:
    """ws_broadcaster.stats() 暴露 broadcast 统计。"""

    def test_initial_stats(self):
        """新 broadcaster → 所有统计从 0 / 100% / None 开始。"""
        from web.backend.main import ws_broadcaster
        # Reset to known state
        ws_broadcaster.reset_stats()
        s = ws_broadcaster.stats()
        assert s["total_broadcasts"] == 0
        assert s["total_sends"] == 0
        assert s["total_send_failures"] == 0
        assert s["success_rate_pct"] == 100.0  # No sends = 100% success
        assert s["last_broadcast_at"] is None
        assert s["connected_clients"] == 0

    def test_stats_after_one_broadcast(self):
        """1 次 broadcast → total_broadcasts=1, last_broadcast_at 设置。"""
        from web.backend.main import ws_broadcaster
        ws_broadcaster.reset_stats()

        class FakeWS:
            def __init__(self):
                self.sent = []
            async def accept(self): pass
            async def send_text(self, msg): self.sent.append(msg)

        async def run_test():
            ws = FakeWS()
            await ws_broadcaster.connect(ws)
            await ws_broadcaster.broadcast({"type": "test", "value": 42})
            await ws_broadcaster.disconnect(ws)

        asyncio.run(run_test())

        s = ws_broadcaster.stats()
        assert s["total_broadcasts"] == 1
        assert s["total_sends"] == 1  # 1 client × 1 broadcast
        assert s["total_send_failures"] == 0
        assert s["success_rate_pct"] == 100.0
        assert s["last_broadcast_at"] is not None
        # ISO format check
        datetime.fromisoformat(s["last_broadcast_at"])

    def test_stats_after_multiple_broadcasts(self):
        """多次 broadcast → total_broadcasts 累加。"""
        from web.backend.main import ws_broadcaster
        ws_broadcaster.reset_stats()

        class FakeWS:
            def __init__(self):
                self.sent = []
            async def accept(self): pass
            async def send_text(self, msg): self.sent.append(msg)

        async def run_test():
            ws1, ws2 = FakeWS(), FakeWS()
            await ws_broadcaster.connect(ws1)
            await ws_broadcaster.connect(ws2)
            for i in range(3):
                await ws_broadcaster.broadcast({"n": i})
            await ws_broadcaster.disconnect(ws1)
            await ws_broadcaster.disconnect(ws2)

        asyncio.run(run_test())

        s = ws_broadcaster.stats()
        assert s["total_broadcasts"] == 3
        assert s["total_sends"] == 6  # 2 clients × 3 broadcasts
        assert s["total_send_failures"] == 0

    def test_stats_with_send_failures(self):
        """失败的 send → total_send_failures 增加, success_rate_pct 降低。"""
        from web.backend.main import ws_broadcaster
        ws_broadcaster.reset_stats()

        class GoodWS:
            def __init__(self):
                self.sent = []
            async def accept(self): pass
            async def send_text(self, msg): self.sent.append(msg)

        class BadWS:
            async def accept(self): pass
            async def send_text(self, msg):
                raise RuntimeError("client gone")

        async def run_test():
            good, bad = GoodWS(), BadWS()
            await ws_broadcaster.connect(good)
            await ws_broadcaster.connect(bad)
            await ws_broadcaster.broadcast({"x": 1})
            await ws_broadcaster.disconnect(good)

        asyncio.run(run_test())

        s = ws_broadcaster.stats()
        assert s["total_sends"] == 2  # both clients tried
        assert s["total_send_failures"] == 1  # bad one failed
        assert s["success_rate_pct"] == 50.0  # 1 of 2 succeeded

    def test_reset_stats(self):
        """reset_stats() 清零所有 counter。"""
        from web.backend.main import ws_broadcaster

        async def run_test():
            class FakeWS:
                def __init__(self):
                    self.sent = []
                async def accept(self): pass
                async def send_text(self, msg): self.sent.append(msg)
            ws = FakeWS()
            await ws_broadcaster.connect(ws)
            await ws_broadcaster.broadcast({"x": 1})
            await ws_broadcaster.disconnect(ws)

        asyncio.run(run_test())
        # Verify stats are non-zero
        s_before = ws_broadcaster.stats()
        assert s_before["total_broadcasts"] >= 1
        # Reset
        ws_broadcaster.reset_stats()
        s_after = ws_broadcaster.stats()
        assert s_after["total_broadcasts"] == 0
        assert s_after["total_sends"] == 0
        assert s_after["total_send_failures"] == 0
        assert s_after["last_broadcast_at"] is None


# ============================================
# /api/ws/stats/reset endpoint tests
# ============================================

class TestWsStatsResetEndpoint:
    """POST /api/ws/stats/reset endpoint。"""

    def test_reset_endpoint_returns_200(self):
        from fastapi.testclient import TestClient
        from web.backend import main as backend_main
        from web.backend.main import ws_broadcaster

        # Make some broadcast activity
        async def run_activity():
            class FakeWS:
                def __init__(self):
                    self.sent = []
                async def accept(self): pass
                async def send_text(self, msg): self.sent.append(msg)
            ws = FakeWS()
            await ws_broadcaster.connect(ws)
            await ws_broadcaster.broadcast({"x": 1})
            await ws_broadcaster.disconnect(ws)
        asyncio.run(run_activity())

        with TestClient(backend_main.app) as client:
            resp = client.post("/api/ws/stats/reset")
            assert resp.status_code == 200
            data = resp.json()
            assert data["reset"] is True
            assert data["stats"]["total_broadcasts"] == 0
            assert data["stats"]["total_sends"] == 0

    def test_reset_endpoint_via_get(self):
        """GET 不会触发 reset (POST only)。"""
        from fastapi.testclient import TestClient
        from web.backend import main as backend_main
        with TestClient(backend_main.app) as client:
            resp = client.get("/api/ws/stats/reset")
            # FastAPI returns 405 Method Not Allowed
            assert resp.status_code == 405


# ============================================
# /api/ws/stats metadata consistency
# ============================================

class TestWsStatsEndpoint:
    """/api/ws/stats endpoint 暴露新统计字段。"""

    def test_stats_endpoint_has_new_fields(self):
        from fastapi.testclient import TestClient
        from web.backend import main as backend_main
        from web.backend.main import ws_broadcaster
        ws_broadcaster.reset_stats()
        with TestClient(backend_main.app) as client:
            resp = client.get("/api/ws/stats")
        assert resp.status_code == 200
        data = resp.json()
        # iter #27 fields
        for f in ["total_broadcasts", "total_sends", "total_send_failures",
                  "success_rate_pct", "last_broadcast_at"]:
            assert f in data, f"Missing field {f}"
        # iter #27 origin allowlist fields
        assert "origin_allowlist_active" in data
        assert "origin_allowlist_size" in data
        # legacy field
        assert "connected_clients" in data
