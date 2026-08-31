"""
Tests for WebSocket Origin allowlist (iter #27 security improvement).

- is_ws_origin_allowed() helper
- /ws/cycle-updates endpoint behavior
- /api/ws/stats metadata
"""

import os
import pytest
from unittest.mock import patch


# ============================================
# is_ws_origin_allowed() unit tests
# ============================================

class TestIsWsOriginAllowed:
    """直接测试 is_ws_origin_allowed() 辅助函数。"""

    def test_empty_allowlist_allows_all(self, monkeypatch):
        """没设 env var 时 (allowlist 空) → 所有 origin 允许。"""
        from web.backend.main import is_ws_origin_allowed
        monkeypatch.setattr("web.backend.main._WS_ALLOWED_ORIGINS_RAW", "")
        assert is_ws_origin_allowed("https://example.com") is True
        assert is_ws_origin_allowed("https://attacker.com") is True
        assert is_ws_origin_allowed(None) is True
        assert is_ws_origin_allowed("") is True

    def test_allowlist_allows_listed(self, monkeypatch):
        """设了 env var 后, allowlist 内的 origin 被允许。"""
        from web.backend.main import is_ws_origin_allowed
        monkeypatch.setattr(
            "web.backend.main._WS_ALLOWED_ORIGINS_RAW",
            "https://lidingx-green-logistics.hf.space,http://localhost:5173",
        )
        assert is_ws_origin_allowed("https://lidingx-green-logistics.hf.space") is True
        assert is_ws_origin_allowed("http://localhost:5173") is True

    def test_allowlist_rejects_unlisted(self, monkeypatch):
        """Allowlist 内的以外 origin 被拒绝。"""
        from web.backend.main import is_ws_origin_allowed
        monkeypatch.setattr(
            "web.backend.main._WS_ALLOWED_ORIGINS_RAW",
            "https://lidingx-green-logistics.hf.space",
        )
        assert is_ws_origin_allowed("https://attacker.com") is False
        assert is_ws_origin_allowed("https://evil.example.org") is False
        assert is_ws_origin_allowed("http://localhost:5173") is False  # 端口不一致

    def test_allowlist_allows_missing_origin(self, monkeypatch):
        """Allowlist 非空时, missing origin (None) 仍允许 (非浏览器 client)。"""
        from web.backend.main import is_ws_origin_allowed
        monkeypatch.setattr(
            "web.backend.main._WS_ALLOWED_ORIGINS_RAW",
            "https://lidingx-green-logistics.hf.space",
        )
        # 非浏览器 WS client 可能不传 Origin header
        assert is_ws_origin_allowed(None) is True
        assert is_ws_origin_allowed("") is True

    def test_allowlist_handles_whitespace(self, monkeypatch):
        """Env var 解析时 strip 空格。"""
        from web.backend.main import is_ws_origin_allowed, _get_ws_allowed_origins
        monkeypatch.setattr(
            "web.backend.main._WS_ALLOWED_ORIGINS_RAW",
            "  https://a.com , https://b.com  ,, https://c.com  ",
        )
        allowed = _get_ws_allowed_origins()
        assert allowed == {"https://a.com", "https://b.com", "https://c.com"}

    def test_get_ws_allowed_origins_returns_fresh_set(self, monkeypatch):
        """_get_ws_allowed_origins 每次返回新 set (不共享引用)。"""
        from web.backend.main import _get_ws_allowed_origins
        monkeypatch.setattr(
            "web.backend.main._WS_ALLOWED_ORIGINS_RAW",
            "https://a.com,https://b.com",
        )
        s1 = _get_ws_allowed_origins()
        s2 = _get_ws_allowed_origins()
        assert s1 is not s2
        # mutate s1, s2 unaffected
        s1.add("https://c.com")
        assert "https://c.com" not in s2


# ============================================
# /api/ws/stats metadata tests
# ============================================

class TestWsStatsAllowlistMetadata:
    """/api/ws/stats 现在暴露 allowlist 元信息。"""

    def test_stats_no_allowlist(self, monkeypatch):
        """Allowlist 未设 → origin_allowlist_active=false, size=0。"""
        from fastapi.testclient import TestClient
        from web.backend import main as backend_main

        monkeypatch.setattr("web.backend.main._WS_ALLOWED_ORIGINS_RAW", "")
        with TestClient(backend_main.app) as client:
            resp = client.get("/api/ws/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["origin_allowlist_active"] is False
        assert data["origin_allowlist_size"] == 0
        assert "origin_allowlist_sample" not in data

    def test_stats_with_allowlist(self, monkeypatch):
        """Allowlist 设有值 → 暴露 size + sample (前 5 个)。"""
        from fastapi.testclient import TestClient
        from web.backend import main as backend_main

        monkeypatch.setattr(
            "web.backend.main._WS_ALLOWED_ORIGINS_RAW",
            "https://a.com,https://b.com,https://c.com",
        )
        with TestClient(backend_main.app) as client:
            resp = client.get("/api/ws/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["origin_allowlist_active"] is True
        assert data["origin_allowlist_size"] == 3
        assert set(data["origin_allowlist_sample"]) == {
            "https://a.com",
            "https://b.com",
            "https://c.com",
        }


# ============================================
# /ws/cycle-updates Origin 校验 tests
# ============================================

class TestWsEndpointOriginEnforcement:
    """/ws/cycle-updates 端点应该根据 allowlist 拒绝非法 origin。"""

    def test_no_allowlist_accepts_any_origin(self, monkeypatch):
        """Allowlist 未设时, 任意 origin 应该被 accept。"""
        from fastapi.testclient import TestClient
        from web.backend import main as backend_main

        monkeypatch.setattr("web.backend.main._WS_ALLOWED_ORIGINS_RAW", "")
        with TestClient(backend_main.app) as client:
            with client.websocket_connect(
                "/ws/cycle-updates",
                headers={"origin": "https://random-site.com"},
            ) as ws:
                # 应该收到 hello message
                hello = ws.receive_text()
                assert "hello" in hello

    def test_allowlist_rejects_unlisted_origin(self, monkeypatch):
        """Allowlist 设有值时, unlisted origin 应该被 close (code=1008)。"""
        from fastapi.testclient import TestClient
        from fastapi.websockets import WebSocketDisconnect
        from web.backend import main as backend_main

        monkeypatch.setattr(
            "web.backend.main._WS_ALLOWED_ORIGINS_RAW",
            "https://allowed.com",
        )
        with TestClient(backend_main.app) as client:
            with pytest.raises(WebSocketDisconnect):
                with client.websocket_connect(
                    "/ws/cycle-updates",
                    headers={"origin": "https://attacker.com"},
                ) as ws:
                    ws.receive_text()

    def test_allowlist_accepts_listed_origin(self, monkeypatch):
        """Allowlist 内的 origin 应该被 accept。"""
        from fastapi.testclient import TestClient
        from web.backend import main as backend_main

        monkeypatch.setattr(
            "web.backend.main._WS_ALLOWED_ORIGINS_RAW",
            "https://allowed.com,https://other.com",
        )
        with TestClient(backend_main.app) as client:
            with client.websocket_connect(
                "/ws/cycle-updates",
                headers={"origin": "https://allowed.com"},
            ) as ws:
                hello = ws.receive_text()
                assert "hello" in hello

    def test_allowlist_allows_missing_origin(self, monkeypatch):
        """Allowlist 非空时, missing origin header 应该仍被 accept (非浏览器 client)。"""
        from fastapi.testclient import TestClient
        from web.backend import main as backend_main

        monkeypatch.setattr(
            "web.backend.main._WS_ALLOWED_ORIGINS_RAW",
            "https://allowed.com",
        )
        with TestClient(backend_main.app) as client:
            # 不传 origin header
            with client.websocket_connect("/ws/cycle-updates") as ws:
                hello = ws.receive_text()
                assert "hello" in hello
