"""
Tests for WebSocket broadcaster and /ws/cycle-updates endpoint.
"""

import asyncio
import json
import pytest
from fastapi.testclient import TestClient


def test_websocket_broadcaster_basic_connect_broadcast_disconnect():
    """连接 → broadcast → 收到 → 断开"""
    from web.backend.main import ws_broadcaster

    # 创建一个模拟 WebSocket
    class FakeWS:
        def __init__(self):
            self.sent = []
            self.accepted = False
            self.closed = False

        async def accept(self):
            self.accepted = True

        async def send_text(self, msg):
            self.sent.append(msg)

        async def close(self):
            self.closed = True

    async def run_test():
        ws1, ws2 = FakeWS(), FakeWS()
        await ws_broadcaster.connect(ws1)
        await ws_broadcaster.connect(ws2)
        assert len(ws_broadcaster._clients) == 2
        assert ws1.accepted and ws2.accepted

        await ws_broadcaster.broadcast({"type": "test", "value": 42})
        assert len(ws1.sent) == 1 and len(ws2.sent) == 1
        msg = json.loads(ws1.sent[0])
        assert msg == {"type": "test", "value": 42}

        await ws_broadcaster.disconnect(ws1)
        await ws_broadcaster.disconnect(ws2)
        assert len(ws_broadcaster._clients) == 0

    asyncio.run(run_test())


def test_websocket_broadcaster_handles_dead_client():
    """一个 client send 失败不应该拖累其他 client"""
    from web.backend.main import ws_broadcaster

    class GoodWS:
        def __init__(self):
            self.sent = []

        async def accept(self):
            pass

        async def send_text(self, msg):
            self.sent.append(msg)

    class DeadWS:
        async def accept(self):
            pass

        async def send_text(self, msg):
            raise RuntimeError("connection lost")

    async def run_test():
        good = GoodWS()
        dead = DeadWS()
        await ws_broadcaster.connect(good)
        await ws_broadcaster.connect(dead)
        assert len(ws_broadcaster._clients) == 2

        # broadcast: dead 应该被清理, good 应该收到
        await ws_broadcaster.broadcast({"type": "hello"})
        assert len(good.sent) == 1
        # dead 被自动清理
        assert dead not in ws_broadcaster._clients
        # good 还在
        assert good in ws_broadcaster._clients
        await ws_broadcaster.disconnect(good)

    asyncio.run(run_test())


def test_websocket_endpoint_accepts_and_sends_hello():
    """FastAPI WS endpoint 应该 accept + 立刻发 hello"""
    from web.backend.main import app

    with TestClient(app) as client:
        with client.websocket_connect("/ws/cycle-updates") as ws:
            # server 应该立刻推 hello
            msg = ws.receive_text()
            data = json.loads(msg)
            assert data["type"] == "hello"
            assert "data" in data
            assert "scheduler_stats" in data["data"]
            # keepalive 会按 10s 间隔发 — 不测试等超时，只测 hello


def test_websocket_endpoint_handles_ping_pong():
    """客户端 ping → server 回 pong"""
    from web.backend.main import app

    with TestClient(app) as client:
        with client.websocket_connect("/ws/cycle-updates") as ws:
            # 接收 hello
            hello = ws.receive_text()
            json.loads(hello)  # 确认是合法 JSON

            # 发 ping
            ws.send_text("ping")
            # 收 pong
            response = ws.receive_text()
            assert response == "pong"


def test_websocket_endpoint_stats_increases_on_connect():
    """/api/ws/stats 应该返回当前连接数"""
    from web.backend.main import app, ws_broadcaster

    with TestClient(app) as client:
        before = client.get("/api/ws/stats").json()["connected_clients"]
        # 打开 1 个 WS
        with client.websocket_connect("/ws/cycle-updates") as ws:
            ws.receive_text()  # hello
            during = client.get("/api/ws/stats").json()["connected_clients"]
            assert during == before + 1
        # 退出 with 后 WS 断开
        # 等 0.5s 让 disconnect task 完成
        import time
        time.sleep(0.5)
        after = client.get("/api/ws/stats").json()["connected_clients"]
        assert after == before