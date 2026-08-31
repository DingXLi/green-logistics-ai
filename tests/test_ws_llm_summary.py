"""Tests for WebSocket cycle_update LLM cost summary (iter #29)."""

import asyncio
import json


def test_broadcast_cycle_update_includes_llm_summary(monkeypatch):
    from web.backend.main import ws_broadcaster, _broadcast_cycle_update
    from web.backend import main as backend_main

    class FakeWS:
        def __init__(self):
            self.sent = []
        async def accept(self): pass
        async def send_text(self, msg): self.sent.append(json.loads(msg))

    class FakePersistence:
        def get_efficiency_metrics(self):
            return {"n_cycles": 1, "cost_per_ton_sek": 5.0, "co2_per_ton_kg": 2.0}

    class FakeLogistics:
        async def get_fleet_status(self):
            return {"total_vehicles": 1, "available": 1, "en_route": 0,
                    "loading": 0, "utilization_rate": 0.0,
                    "total_distance_km": 0.0, "avg_distance_to_depot_km": 0.0}

    class FakeTracker:
        def get_stats(self):
            return {"total_calls": 4, "total_errors": 1, "total_tokens": 1234,
                    "total_cost_usd": 0.0123, "error_rate_pct": 25.0}

    async def run_test():
        ws = FakeWS()
        await ws_broadcaster.connect(ws)
        original_coord = backend_main.coordinator
        original_tracker = backend_main.__dict__.get("_llm_tracker")
        try:
            backend_main.coordinator = type("Coord", (), {})()
            backend_main.coordinator.persistence = FakePersistence()
            backend_main.coordinator.logistics_agent = FakeLogistics()
            monkeypatch.setattr(
                "agents.llm_tracker.get_llm_tracker", lambda: FakeTracker()
            )
            await _broadcast_cycle_update({"cycle_id": "c1", "sim_day": 1})
            assert len(ws.sent) == 1
            llm = ws.sent[0]["data"]["llm"]
            assert llm == {"total_calls": 4, "total_errors": 1,
                           "total_tokens": 1234, "total_cost_usd": 0.0123,
                           "error_rate_pct": 25.0}
        finally:
            backend_main.coordinator = original_coord
            await ws_broadcaster.disconnect(ws)

    asyncio.run(run_test())


def test_broadcast_llm_summary_empty_when_tracker_error(monkeypatch):
    from web.backend.main import ws_broadcaster, _broadcast_cycle_update
    from web.backend import main as backend_main

    class FakeWS:
        def __init__(self): self.sent = []
        async def accept(self): pass
        async def send_text(self, msg): self.sent.append(json.loads(msg))

    def broken_tracker():
        raise RuntimeError("tracker unavailable")

    async def run_test():
        ws = FakeWS()
        await ws_broadcaster.connect(ws)
        original_coord = backend_main.coordinator
        try:
            backend_main.coordinator = None
            monkeypatch.setattr("agents.llm_tracker.get_llm_tracker", broken_tracker)
            await _broadcast_cycle_update({"cycle_id": "c1"})
            assert ws.sent[0]["data"]["llm"] == {}
        finally:
            backend_main.coordinator = original_coord
            await ws_broadcaster.disconnect(ws)

    asyncio.run(run_test())
