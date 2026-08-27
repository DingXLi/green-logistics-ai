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

def test_broadcast_cycle_update_includes_efficiency_field():
    """iter #7: _broadcast_cycle_update 应附带 efficiency summary (cost/co2 per ton)。

    前端 Dashboard 顶部可以直接读 wsMessage.data.efficiency 显示运行 KPI,
    不需要额外 fetch /api/persistence/efficiency-metrics。
    """
    from web.backend.main import ws_broadcaster, _broadcast_cycle_update

    class FakeWebSocket:
        def __init__(self):
            self.sent = []
            self.accepted = False

        async def accept(self):
            self.accepted = True

        async def send_text(self, msg):
            import json as _json
            self.sent.append(_json.loads(msg))

    async def run_test():
        ws = FakeWebSocket()
        await ws_broadcaster.connect(ws)

        # 模拟 coordinator persistence (有 1 cycle)
        from web.backend import main as backend_main

        orig_coord = backend_main.coordinator
        try:
            class _FakePersistence:
                def get_efficiency_metrics(self):
                    return {
                        "n_cycles": 1,
                        "total_tons": 100.0,
                        "total_cost_sek": 500.0,
                        "total_co2_kg": 200.0,
                        "cost_per_ton_sek": 5.0,
                        "co2_per_ton_kg": 2.0,
                        "avg_fleet_util_pct": 70.0,
                        "match_rate_pct": 100.0,
                    }
            class FakeCoord:
                pass
            fake_coord = FakeCoord()
            fake_coord.persistence = _FakePersistence()
            backend_main.coordinator = fake_coord

            await _broadcast_cycle_update({
                "cycle_id": "test-1",
                "n_supply_offers": 5,
                "n_demand_requests": 5,
                "n_matches": 3,
                "total_tons": 100.0,
                "total_cost_sek": 500.0,
                "total_co2_kg": 200.0,
                "sim_day": 5,
            })

            assert len(ws.sent) == 1
            payload = ws.sent[0]
            assert payload["type"] == "cycle_update"
            data = payload["data"]
            assert "efficiency" in data
            eff = data["efficiency"]
            assert eff["n_cycles"] == 1
            assert eff["cost_per_ton_sek"] == 5.0
            assert eff["co2_per_ton_kg"] == 2.0
            assert eff["avg_fleet_util_pct"] == 70.0
            assert eff["match_rate_pct"] == 100.0
        finally:
            backend_main.coordinator = orig_coord
            await ws_broadcaster.disconnect(ws)

    asyncio.run(run_test())


def test_broadcast_cycle_update_efficiency_field_empty_when_no_persistence():
    """无 coordinator persistence → efficiency 字段为空 dict (不抛异常)。"""
    from web.backend.main import ws_broadcaster, _broadcast_cycle_update

    class FakeWebSocket:
        def __init__(self):
            self.sent = []
        async def accept(self):
            pass
        async def send_text(self, msg):
            import json as _json
            self.sent.append(_json.loads(msg))

    async def run_test():
        ws = FakeWebSocket()
        await ws_broadcaster.connect(ws)

        from web.backend import main as backend_main
        orig_coord = backend_main.coordinator
        try:
            backend_main.coordinator = None  # 没 coordinator

            await _broadcast_cycle_update({
                "cycle_id": "test-1",
                "n_matches": 0,
                "sim_day": 1,
            })

            assert len(ws.sent) == 1
            payload = ws.sent[0]
            assert payload["data"]["efficiency"] == {}
        finally:
            backend_main.coordinator = orig_coord
            await ws_broadcaster.disconnect(ws)

    asyncio.run(run_test())


def test_broadcast_cycle_update_includes_fleet_field():
    """iter #8: WS broadcast 附带 fleet metrics (n_vehicles, util, distance)。"""
    from web.backend.main import ws_broadcaster, _broadcast_cycle_update

    class FakeWebSocket:
        def __init__(self):
            self.sent = []
        async def accept(self):
            pass
        async def send_text(self, msg):
            import json as _json
            self.sent.append(_json.loads(msg))

    async def run_test():
        ws = FakeWebSocket()
        await ws_broadcaster.connect(ws)

        from web.backend import main as backend_main
        orig_coord = backend_main.coordinator
        try:
            # 构造 fake logistics_agent with get_fleet_status
            class _FakeFleetStatus:
                async def get_fleet_status(self):
                    return {
                        "total_vehicles": 30,
                        "available": 21,
                        "en_route": 7,
                        "loading": 2,
                        "utilization_rate": 30.0,
                        "total_distance_km": 145.5,
                        "avg_distance_to_depot_km": 4.2,
                    }
            class _FakeLogistics:
                pass
            fake_logistics = _FakeLogistics()
            fake_logistics.get_fleet_status = _FakeFleetStatus().get_fleet_status
            class _FakeCoord:
                pass
            fake_coord = _FakeCoord()
            fake_coord.logistics_agent = fake_logistics
            fake_coord.persistence = None  # 没 persistence
            backend_main.coordinator = fake_coord

            await _broadcast_cycle_update({
                "cycle_id": "test-1",
                "n_matches": 5,
                "sim_day": 10,
                "distance_source": "osm",
            })

            assert len(ws.sent) == 1
            payload = ws.sent[0]
            data = payload["data"]
            assert "fleet" in data
            fleet = data["fleet"]
            assert fleet["total_vehicles"] == 30
            assert fleet["utilization_rate"] == 30.0
            assert fleet["avg_distance_to_depot_km"] == 4.2
            # 同时 iter #8: distance_source 也应在 data 顶层
            assert data["distance_source"] == "osm"
        finally:
            backend_main.coordinator = orig_coord
            await ws_broadcaster.disconnect(ws)

    asyncio.run(run_test())
