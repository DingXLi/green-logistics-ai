"""
Tests for WebSocket max-client guard + connection metrics (iter #32).

_iter #32 改进_: WebSocketBroadcaster 新增:
- max_clients: 全局并发连接数上限 (env GL_WS_MAX_CLIENTS, 默认 50)
- max_per_ip: 同一 IP 并发连接数上限 (env GL_WS_MAX_PER_IP, 默认 10)
- peak_clients: 历史最高并发
- total_connections_accepted / _rejected: 累计计数
- total_connection_seconds / avg_connection_seconds: 连接时长累计
- current_ip_distribution: 当前 IP → count

connect() 返回 bool: True=接受, False=拒绝 (caller 应 ws.close(code=1013))
"""

import asyncio
import os
import pytest
from unittest.mock import patch


# ============================================
# Test helpers
# ============================================

class FakeWS:
    """Minimal async WS stub that records accept/send/close calls."""

    def __init__(self):
        self.sent = []
        self.accepted = False
        self.closed_with = None  # tuple (code, reason) or None

    async def accept(self):
        self.accepted = True

    async def send_text(self, msg):
        self.sent.append(msg)

    async def close(self, code: int = 1000, reason: str = ""):
        self.closed_with = (code, reason)


def _reset_broadcaster(broadcaster):
    """No-op: 全局 conftest fixture 自动重置 ws_broadcaster singleton。

    保留这个 helper 是为了将来如果某个 test 想重置中间状态可以调用。
    现在所有 WS test 在 setup/teardown 时都会被 conftest 自动清空。
    """
    return None


# ============================================
# connect() basic acceptance tests
# ============================================

class TestConnectBasic:
    """connect() 默认行为不变 — 接受 client 直到上限。"""

    def test_connect_returns_true_when_under_limit(self):
        from web.backend.main import ws_broadcaster
        _reset_broadcaster(ws_broadcaster)
        ws = FakeWS()

        async def run():
            return await ws_broadcaster.connect(ws, client_ip="1.2.3.4")

        result = asyncio.run(run())
        assert result is True
        assert ws.accepted is True
        s = ws_broadcaster.stats()
        assert s["connected_clients"] == 1
        assert s["total_connections_accepted"] == 1
        assert s["total_connections_rejected"] == 0

    def test_connect_without_ip_still_works(self):
        """Backward compat: client_ip=None 也能正常 connect。"""
        from web.backend.main import ws_broadcaster
        _reset_broadcaster(ws_broadcaster)
        ws = FakeWS()

        async def run():
            return await ws_broadcaster.connect(ws)  # no client_ip kwarg

        result = asyncio.run(run())
        assert result is True
        s = ws_broadcaster.stats()
        assert s["current_ip_distribution"] == {"unknown": 1}


# ============================================
# Max-client guard
# ============================================

class TestMaxClientsGuard:
    """connect() 在并发达到 max_clients 时返回 False。"""

    def test_global_max_clients_enforced(self, monkeypatch):
        """GL_WS_MAX_CLIENTS=2 → 第三个 client 被拒。"""
        monkeypatch.setenv("GL_WS_MAX_CLIENTS", "2")
        # Force re-read of cached env
        from web.backend import main as backend_main
        import importlib
        importlib.reload(backend_main) if False else None  # not needed; helpers read env at call time

        from web.backend.main import ws_broadcaster
        _reset_broadcaster(ws_broadcaster)
        ws1, ws2, ws3 = FakeWS(), FakeWS(), FakeWS()

        async def run():
            r1 = await ws_broadcaster.connect(ws1, client_ip="10.0.0.1")
            r2 = await ws_broadcaster.connect(ws2, client_ip="10.0.0.2")
            r3 = await ws_broadcaster.connect(ws3, client_ip="10.0.0.3")
            return r1, r2, r3

        r1, r2, r3 = asyncio.run(run())
        assert r1 is True
        assert r2 is True
        assert r3 is False, "3rd client should be rejected at max=2"
        assert ws3.accepted is False, "rejected client must not be accepted"
        s = ws_broadcaster.stats()
        assert s["connected_clients"] == 2
        assert s["total_connections_accepted"] == 2
        assert s["total_connections_rejected"] == 1
        assert s["peak_clients"] == 2

    def test_global_max_zero_means_unlimited(self, monkeypatch):
        """GL_WS_MAX_CLIENTS=0 → 无限。"""
        monkeypatch.setenv("GL_WS_MAX_CLIENTS", "0")
        from web.backend.main import ws_broadcaster
        _reset_broadcaster(ws_broadcaster)
        ws_list = [FakeWS() for _ in range(15)]

        async def run():
            results = []
            for i, ws in enumerate(ws_list):
                results.append(await ws_broadcaster.connect(ws, client_ip=f"10.0.0.{i}"))
            return results

        results = asyncio.run(run())
        assert all(results)
        s = ws_broadcaster.stats()
        assert s["connected_clients"] == 15
        assert s["total_connections_rejected"] == 0

    def test_max_clients_default_is_50(self, monkeypatch):
        """Default max_clients 是 50 (with monkeypatch-deleted env var)。"""
        monkeypatch.delenv("GL_WS_MAX_CLIENTS", raising=False)
        from web.backend.main import _get_ws_max_clients
        assert _get_ws_max_clients() == 50


# ============================================
# Per-IP guard
# ============================================

class TestPerIPGuard:
    """connect() 在 per-IP 超限时返回 False。"""

    def test_per_ip_limit_enforced(self, monkeypatch):
        """GL_WS_MAX_PER_IP=2 → 同 IP 第 3 个被拒。"""
        monkeypatch.setenv("GL_WS_MAX_CLIENTS", "100")
        monkeypatch.setenv("GL_WS_MAX_PER_IP", "2")
        from web.backend.main import ws_broadcaster
        _reset_broadcaster(ws_broadcaster)
        ws1, ws2, ws3 = FakeWS(), FakeWS(), FakeWS()

        async def run():
            r1 = await ws_broadcaster.connect(ws1, client_ip="10.0.0.1")
            r2 = await ws_broadcaster.connect(ws2, client_ip="10.0.0.1")
            r3 = await ws_broadcaster.connect(ws3, client_ip="10.0.0.1")
            return r1, r2, r3

        r1, r2, r3 = asyncio.run(run())
        assert r1 is True
        assert r2 is True
        assert r3 is False
        s = ws_broadcaster.stats()
        assert s["current_ip_distribution"]["10.0.0.1"] == 2
        assert s["total_connections_rejected"] == 1

    def test_per_ip_different_ips_all_ok(self, monkeypatch):
        """不同 IP 不互相影响。"""
        monkeypatch.setenv("GL_WS_MAX_PER_IP", "1")
        from web.backend.main import ws_broadcaster
        _reset_broadcaster(ws_broadcaster)
        ws1, ws2, ws3 = FakeWS(), FakeWS(), FakeWS()

        async def run():
            r1 = await ws_broadcaster.connect(ws1, client_ip="1.1.1.1")
            r2 = await ws_broadcaster.connect(ws2, client_ip="2.2.2.2")
            r3 = await ws_broadcaster.connect(ws3, client_ip="3.3.3.3")
            return r1, r2, r3

        r1, r2, r3 = asyncio.run(run())
        assert all([r1, r2, r3])
        s = ws_broadcaster.stats()
        assert len(s["current_ip_distribution"]) == 3


# ============================================
# Disconnect + duration tracking
# ============================================

class TestDisconnectMetrics:
    """disconnect() 更新 peak/avg duration。"""

    def test_disconnect_tracks_duration(self):
        """disconnect 后 total_connection_seconds 累计, peak 不下降。"""
        from web.backend.main import ws_broadcaster
        _reset_broadcaster(ws_broadcaster)
        ws1, ws2 = FakeWS(), FakeWS()

        async def run():
            await ws_broadcaster.connect(ws1, client_ip="10.0.0.1")
            await ws_broadcaster.connect(ws2, client_ip="10.0.0.2")
            await asyncio.sleep(0.05)  # 50ms
            await ws_broadcaster.disconnect(ws1)
            await ws_broadcaster.disconnect(ws2)

        asyncio.run(run())
        s = ws_broadcaster.stats()
        # 2 connections × ≥50ms = ≥0.1s cumulative
        assert s["total_connection_seconds"] >= 0.05, \
            f"expected ≥0.05, got {s['total_connection_seconds']}"
        # peak stays at max even after disconnect
        assert s["peak_clients"] == 2
        # no current clients left
        assert s["connected_clients"] == 0
        # avg = total / accepted = total / 2
        assert s["avg_connection_seconds"] >= 0.025

    def test_disconnect_unknown_ws_is_noop(self):
        """disconnect 一个不在 _clients 的 ws 不报错。"""
        from web.backend.main import ws_broadcaster
        _reset_broadcaster(ws_broadcaster)
        ws_unknown = FakeWS()

        async def run():
            await ws_broadcaster.disconnect(ws_unknown)

        asyncio.run(run())  # 不抛异常
        s = ws_broadcaster.stats()
        assert s["connected_clients"] == 0


# ============================================
# Stats() new fields
# ============================================

class TestStatsNewFields:
    """stats() 包含 iter #32 新字段。"""

    def test_stats_has_max_fields(self):
        from web.backend.main import ws_broadcaster
        _reset_broadcaster(ws_broadcaster)
        s = ws_broadcaster.stats()
        for field in [
            "max_clients", "max_per_ip",
            "peak_clients",
            "total_connections_accepted", "total_connections_rejected",
            "total_connection_seconds", "avg_connection_seconds",
            "current_ip_distribution",
        ]:
            assert field in s, f"missing {field}"

    def test_stats_after_full_lifecycle(self):
        """完整生命周期后 stats 字段正确。"""
        from web.backend.main import ws_broadcaster
        _reset_broadcaster(ws_broadcaster)
        ws1, ws2 = FakeWS(), FakeWS()

        async def run():
            await ws_broadcaster.connect(ws1, client_ip="10.0.0.1")
            await ws_broadcaster.connect(ws2, client_ip="10.0.0.2")
            await asyncio.sleep(0.01)
            await ws_broadcaster.disconnect(ws1)
            # ws2 still connected

        asyncio.run(run())
        s = ws_broadcaster.stats()
        assert s["peak_clients"] == 2
        assert s["total_connections_accepted"] == 2
        assert s["total_connections_rejected"] == 0
        assert s["total_connection_seconds"] > 0
        assert s["avg_connection_seconds"] > 0
        assert s["current_ip_distribution"] == {"10.0.0.2": 1}


# ============================================
# /api/ws/stats endpoint integration
# ============================================

class TestWsStatsEndpoint:
    """/api/ws/stats 暴露新字段。"""

    def test_endpoint_includes_new_fields(self):
        from fastapi.testclient import TestClient
        from web.backend import main as backend_main
        with TestClient(backend_main.app) as client:
            resp = client.get("/api/ws/stats")
        assert resp.status_code == 200
        data = resp.json()
        for field in [
            "max_clients", "max_per_ip",
            "peak_clients",
            "total_connections_accepted", "total_connections_rejected",
            "total_connection_seconds", "avg_connection_seconds",
            "current_ip_distribution",
        ]:
            assert field in data, f"endpoint missing {field}"

    def test_reset_endpoint_clears_new_fields(self):
        """POST /api/ws/stats/reset 清零新字段。"""
        from fastapi.testclient import TestClient
        from web.backend import main as backend_main
        with TestClient(backend_main.app) as client:
            client.post("/api/ws/stats/reset")
            resp = client.get("/api/ws/stats")
        data = resp.json()
        assert data["total_connections_accepted"] == 0
        assert data["total_connections_rejected"] == 0
        assert data["peak_clients"] == 0
        assert data["total_connection_seconds"] == 0.0


# ============================================
# Env var parsing
# ============================================

class TestEnvVarParsing:
    """_get_ws_max_clients / _get_ws_max_per_ip 环境变量解析。"""

    def test_default_max_clients(self, monkeypatch):
        monkeypatch.delenv("GL_WS_MAX_CLIENTS", raising=False)
        from web.backend.main import _get_ws_max_clients
        assert _get_ws_max_clients() == 50

    def test_default_max_per_ip(self, monkeypatch):
        monkeypatch.delenv("GL_WS_MAX_PER_IP", raising=False)
        from web.backend.main import _get_ws_max_per_ip
        assert _get_ws_max_per_ip() == 10

    def test_invalid_max_clients_falls_back_to_50(self, monkeypatch):
        monkeypatch.setenv("GL_WS_MAX_CLIENTS", "not-a-number")
        from web.backend.main import _get_ws_max_clients
        assert _get_ws_max_clients() == 50

    def test_negative_max_clients_clamps_to_zero(self, monkeypatch):
        monkeypatch.setenv("GL_WS_MAX_CLIENTS", "-5")
        from web.backend.main import _get_ws_max_clients
        assert _get_ws_max_clients() == 0

    def test_custom_max_values(self, monkeypatch):
        monkeypatch.setenv("GL_WS_MAX_CLIENTS", "100")
        monkeypatch.setenv("GL_WS_MAX_PER_IP", "5")
        from web.backend.main import _get_ws_max_clients, _get_ws_max_per_ip
        assert _get_ws_max_clients() == 100
        assert _get_ws_max_per_ip() == 5


# ============================================
# /ws/cycle-updates endpoint capacity enforcement
# ============================================

class TestWsEndpointCapacity:
    """端到端验证 WS endpoint 在满载时主动 close(1013)。"""

    def test_endpoint_closes_with_1013_when_full(self, monkeypatch):
        monkeypatch.setenv("GL_WS_MAX_CLIENTS", "1")
        from fastapi.testclient import TestClient
        from web.backend import main as backend_main
        from web.backend.main import ws_broadcaster

        # clean state
        ws_broadcaster._clients.clear()
        ws_broadcaster._client_meta.clear()
        ws_broadcaster.reset_stats()

        with TestClient(backend_main.app) as client:
            with client.websocket_connect("/ws/cycle-updates") as ws1:
                # first client should be accepted (hello + keepalive loop)
                hello = ws1.receive_json()
                assert hello["type"] == "hello"

                # 2nd connection should fail with 1013 (try again later)
                with pytest.raises(Exception) as exc_info:
                    with client.websocket_connect("/ws/cycle-updates") as ws2:
                        ws2.receive_text()  # should not reach here
                # Starlette TestClient raises WebSocketDisconnect on close
                # code is accessible via exc_info.value.code
                err = exc_info.value
                assert getattr(err, "code", None) == 1013, \
                    f"expected close code 1013, got {getattr(err, 'code', err)}"

                # cleanup: close first
                ws1.close()

        s = ws_broadcaster.stats()
        assert s["total_connections_accepted"] == 1
        assert s["total_connections_rejected"] == 1
