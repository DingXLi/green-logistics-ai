"""
Tests for /api/persistence/export/{cycles,supplies,matches,routes}.{json,ndjson}
(iter #27 — consistency with /api/admin/db-export json+ndjson support).
"""

import json
import pytest


# ============================================
# Fixtures
# ============================================

@pytest.fixture
def client_with_cycles():
    """A TestClient with at least 1 cycle in DB."""
    from fastapi.testclient import TestClient
    from web.backend import main as backend_main
    from web.backend.main import coordinator as coord

    with TestClient(backend_main.app) as client:
        # Trigger a cycle if none exist
        if coord is not None and coord.persistence is not None:
            summary = coord.persistence.get_summary() or {}
            n_cycles = summary.get("n_cycles", 0)
            if n_cycles == 0:
                try:
                    client.post(
                        "/api/optimize",
                        json={"use_real_roads": False, "time_limit_seconds": 1},
                    )
                except Exception:
                    pass
        yield client


# ============================================
# JSON endpoints
# ============================================

class TestJsonEndpoints:
    """/api/persistence/export/*.json 应该返回 JSON array。"""

    def test_cycles_json_returns_array(self, client_with_cycles):
        resp = client_with_cycles.get("/api/persistence/export/cycles.json?limit=10")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        if len(data) > 0:
            assert "cycle_id" in data[0]
            assert "sim_day" in data[0]

    def test_cycles_json_attachment_header(self, client_with_cycles):
        resp = client_with_cycles.get("/api/persistence/export/cycles.json?limit=5")
        assert "attachment" in resp.headers["content-disposition"]
        assert ".json" in resp.headers["content-disposition"]

    def test_supplies_json_returns_array(self, client_with_cycles):
        resp = client_with_cycles.get("/api/persistence/export/supplies.json?limit=10")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        if len(data) > 0:
            assert "supply_id" in data[0]

    def test_matches_json_returns_array(self, client_with_cycles):
        resp = client_with_cycles.get("/api/persistence/export/matches.json?limit=10")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        if len(data) > 0:
            assert "supply_id" in data[0]
            assert "demand_id" in data[0]

    def test_routes_json_returns_array(self, client_with_cycles):
        resp = client_with_cycles.get("/api/persistence/export/routes.json?limit=10")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        if len(data) > 0:
            assert "vehicle_id" in data[0]

    def test_json_limit_clamped_to_min(self, client_with_cycles):
        resp = client_with_cycles.get("/api/persistence/export/cycles.json?limit=0")
        assert resp.status_code == 200
        # limit=0 → clamped to 1 (filename)
        assert "1" in resp.headers["content-disposition"]


# ============================================
# NDJSON endpoints
# ============================================

class TestNdjsonEndpoints:
    """/api/persistence/export/*.ndjson 应该返回 line-delimited JSON。"""

    def test_cycles_ndjson_returns_ndjson(self, client_with_cycles):
        resp = client_with_cycles.get("/api/persistence/export/cycles.ndjson?limit=10")
        assert resp.status_code == 200
        assert "application/x-ndjson" in resp.headers["content-type"]
        assert "attachment" in resp.headers["content-disposition"]

    def test_cycles_ndjson_each_line_is_valid_json(self, client_with_cycles):
        resp = client_with_cycles.get("/api/persistence/export/cycles.ndjson?limit=10")
        if resp.status_code == 200 and resp.text:
            lines = [l for l in resp.text.split("\n") if l]
            for line in lines:
                parsed = json.loads(line)
                assert isinstance(parsed, dict)
                assert "cycle_id" in parsed

    def test_supplies_ndjson_each_line_is_valid_json(self, client_with_cycles):
        resp = client_with_cycles.get("/api/persistence/export/supplies.ndjson?limit=10")
        if resp.status_code == 200 and resp.text:
            lines = [l for l in resp.text.split("\n") if l]
            for line in lines:
                parsed = json.loads(line)
                assert "supply_id" in parsed

    def test_matches_ndjson_each_line_is_valid_json(self, client_with_cycles):
        resp = client_with_cycles.get("/api/persistence/export/matches.ndjson?limit=10")
        if resp.status_code == 200 and resp.text:
            lines = [l for l in resp.text.split("\n") if l]
            for line in lines:
                parsed = json.loads(line)
                assert "supply_id" in parsed
                assert "demand_id" in parsed

    def test_routes_ndjson_each_line_is_valid_json(self, client_with_cycles):
        resp = client_with_cycles.get("/api/persistence/export/routes.ndjson?limit=10")
        if resp.status_code == 200 and resp.text:
            lines = [l for l in resp.text.split("\n") if l]
            for line in lines:
                parsed = json.loads(line)
                assert "vehicle_id" in parsed

    def test_ndjson_attachment_header(self, client_with_cycles):
        resp = client_with_cycles.get("/api/persistence/export/cycles.ndjson?limit=5")
        assert "attachment" in resp.headers["content-disposition"]
        assert ".ndjson" in resp.headers["content-disposition"]


# ============================================
# Helper builder unit tests
# ============================================

class TestHelperBuilders:
    """_build_json_response + _build_ndjson_response helpers。"""

    def test_build_json_response(self):
        from web.backend.main import _build_json_response
        rows = [{"a": 1, "b": "x"}, {"a": 2, "b": "y"}]
        resp = _build_json_response(rows, "test", 2)
        assert resp.status_code == 200
        data = json.loads(resp.body)
        assert data == rows
        assert "green_logistics_test_2.json" in resp.headers["content-disposition"]

    def test_build_ndjson_response(self):
        from web.backend.main import _build_ndjson_response
        rows = [{"a": 1}, {"a": 2}]
        resp = _build_ndjson_response(rows, "test", 2)
        assert resp.status_code == 200
        text = resp.body.decode("utf-8")
        lines = text.split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0]) == {"a": 1}
        assert json.loads(lines[1]) == {"a": 2}
        assert "application/x-ndjson" in resp.headers["content-type"]

    def test_build_json_empty(self):
        from web.backend.main import _build_json_response
        resp = _build_json_response([], "test", 0)
        assert resp.status_code == 200
        data = json.loads(resp.body)
        assert data == []

    def test_build_ndjson_empty(self):
        from web.backend.main import _build_ndjson_response
        resp = _build_ndjson_response([], "test", 0)
        # Empty rows → empty string
        assert resp.body == b""
