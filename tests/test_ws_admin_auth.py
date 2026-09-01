"""
Tests for admin token auth on /api/ws/stats (iter #33).

_iter #33 改进_: GL_ADMIN_TOKEN 设置后, /api/ws/stats 和 /api/ws/stats/reset 需要 admin token。
支持两种传递方式:
- Authorization: Bearer <token>
- X-Admin-Token: <token>

未配置 GL_ADMIN_TOKEN (默认) → 不启用 auth (向后兼容 dev / 本地)。
"""

import os
import pytest


# ============================================
# Unit tests for _check_admin_token / _extract_admin_token
# ============================================

class TestExtractAdminToken:
    """_extract_admin_token 从 headers 提取 token。"""

    def test_extract_from_bearer_header(self):
        from web.backend.main import _extract_admin_token
        assert _extract_admin_token("Bearer abc123", None) == "abc123"
        # case-insensitive scheme
        assert _extract_admin_token("bearer xyz789", None) == "xyz789"
        assert _extract_admin_token("BEARER xyz", None) == "xyz"

    def test_extract_bare_token_from_authorization(self):
        """Authorization 不带 Bearer 前缀也能用 (方便调试)。"""
        from web.backend.main import _extract_admin_token
        assert _extract_admin_token("abc123", None) == "abc123"

    def test_extract_from_x_admin_token(self):
        from web.backend.main import _extract_admin_token
        assert _extract_admin_token(None, "abc123") == "abc123"

    def test_extract_x_admin_token_priority_over_authorization(self):
        """x_admin_token 提供时优先使用 (Authorization 被忽略)。"""
        from web.backend.main import _extract_admin_token
        # 两个都给, Authorization 是空的, X-Admin-Token 有值
        result = _extract_admin_token("", "abc123")
        assert result == "abc123"

    def test_extract_returns_none_when_no_token(self):
        from web.backend.main import _extract_admin_token
        assert _extract_admin_token(None, None) is None
        assert _extract_admin_token("", "") is None


class TestCheckAdminToken:
    """_check_admin_token 验证逻辑。"""

    def test_no_token_configured_allows_all(self, monkeypatch):
        """GL_ADMIN_TOKEN 未设置 → 所有请求通过 (dev 模式)。"""
        monkeypatch.delenv("GL_ADMIN_TOKEN", raising=False)
        from web.backend.main import _check_admin_token
        assert _check_admin_token(None, None) is True
        assert _check_admin_token("Bearer wrong", "wrong") is True

    def test_token_configured_requires_match(self, monkeypatch):
        """GL_ADMIN_TOKEN 设置 → 需要提供正确 token。"""
        monkeypatch.setenv("GL_ADMIN_TOKEN", "secret-token-abc")
        from web.backend.main import _check_admin_token
        # 正确
        assert _check_admin_token("Bearer secret-token-abc", None) is True
        assert _check_admin_token(None, "secret-token-abc") is True
        # 错误
        assert _check_admin_token("Bearer wrong", None) is False
        assert _check_admin_token(None, "wrong") is False
        # 没提供
        assert _check_admin_token(None, None) is False
        assert _check_admin_token("", None) is False
        assert _check_admin_token(None, "") is False

    def test_constant_time_compare(self, monkeypatch):
        """即使 token 长度差异, 错误 token 都被拒 (timing-safe)。"""
        monkeypatch.setenv("GL_ADMIN_TOKEN", "a" * 64)
        from web.backend.main import _check_admin_token
        # 部分匹配 / 完全不同 / 全空 — 全部拒绝
        assert _check_admin_token("Bearer a" * 32, None) is False
        assert _check_admin_token("Bearer b" * 64, None) is False
        assert _check_admin_token("Bearer ", None) is False


# ============================================
# /api/ws/stats endpoint auth integration
# ============================================

class TestWsStatsEndpointAuth:
    """/api/ws/stats 端到端 auth 测试。"""

    def test_no_token_configured_allows_unauthenticated(self, monkeypatch):
        """未配置 GL_ADMIN_TOKEN → 无 auth 请求 200。"""
        monkeypatch.delenv("GL_ADMIN_TOKEN", raising=False)
        from fastapi.testclient import TestClient
        from web.backend import main as backend_main
        from web.backend.main import ws_broadcaster
        ws_broadcaster.reset_stats()
        with TestClient(backend_main.app) as client:
            resp = client.get("/api/ws/stats")
        assert resp.status_code == 200

    def test_token_configured_rejects_unauthenticated(self, monkeypatch):
        """配置 GL_ADMIN_TOKEN 但无 auth header → 401。"""
        monkeypatch.setenv("GL_ADMIN_TOKEN", "secret-token-abc")
        from fastapi.testclient import TestClient
        from web.backend import main as backend_main
        with TestClient(backend_main.app) as client:
            resp = client.get("/api/ws/stats")
        assert resp.status_code == 401
        assert "Admin token required" in resp.json()["detail"]
        assert resp.headers.get("www-authenticate") == "Bearer"

    def test_token_configured_accepts_bearer_header(self, monkeypatch):
        """Bearer header 正确 → 200。"""
        monkeypatch.setenv("GL_ADMIN_TOKEN", "secret-token-abc")
        from fastapi.testclient import TestClient
        from web.backend import main as backend_main
        with TestClient(backend_main.app) as client:
            resp = client.get(
                "/api/ws/stats",
                headers={"Authorization": "Bearer secret-token-abc"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["admin_token_configured"] is True

    def test_token_configured_accepts_x_admin_token(self, monkeypatch):
        """X-Admin-Token header 正确 → 200。"""
        monkeypatch.setenv("GL_ADMIN_TOKEN", "secret-token-abc")
        from fastapi.testclient import TestClient
        from web.backend import main as backend_main
        with TestClient(backend_main.app) as client:
            resp = client.get(
                "/api/ws/stats",
                headers={"X-Admin-Token": "secret-token-abc"},
            )
        assert resp.status_code == 200
        assert resp.json()["admin_token_configured"] is True

    def test_wrong_token_rejected(self, monkeypatch):
        """错误 token → 401, 即使 header 格式对。"""
        monkeypatch.setenv("GL_ADMIN_TOKEN", "secret-token-abc")
        from fastapi.testclient import TestClient
        from web.backend import main as backend_main
        with TestClient(backend_main.app) as client:
            resp_bearer = client.get(
                "/api/ws/stats",
                headers={"Authorization": "Bearer wrong-token"},
            )
            resp_x = client.get(
                "/api/ws/stats",
                headers={"X-Admin-Token": "wrong-token"},
            )
        assert resp_bearer.status_code == 401
        assert resp_x.status_code == 401

    def test_admin_token_configured_field_visible(self, monkeypatch):
        """stats 响应包含 admin_token_configured 字段。"""
        monkeypatch.setenv("GL_ADMIN_TOKEN", "secret-token-abc")
        from fastapi.testclient import TestClient
        from web.backend import main as backend_main
        with TestClient(backend_main.app) as client:
            resp = client.get(
                "/api/ws/stats",
                headers={"Authorization": "Bearer secret-token-abc"},
            )
        data = resp.json()
        assert "admin_token_configured" in data
        assert data["admin_token_configured"] is True

    def test_admin_token_configured_false_when_unset(self, monkeypatch):
        """未配置 token → admin_token_configured = False。"""
        monkeypatch.delenv("GL_ADMIN_TOKEN", raising=False)
        from fastapi.testclient import TestClient
        from web.backend import main as backend_main
        with TestClient(backend_main.app) as client:
            resp = client.get("/api/ws/stats")
        data = resp.json()
        assert data["admin_token_configured"] is False


# ============================================
# /api/ws/stats/reset endpoint auth integration
# ============================================

class TestWsStatsResetEndpointAuth:
    """/api/ws/stats/reset 也需要 auth。"""

    def test_reset_unauthenticated_rejected_when_token_set(self, monkeypatch):
        monkeypatch.setenv("GL_ADMIN_TOKEN", "secret-token-abc")
        from fastapi.testclient import TestClient
        from web.backend import main as backend_main
        with TestClient(backend_main.app) as client:
            resp = client.post("/api/ws/stats/reset")
        assert resp.status_code == 401

    def test_reset_authenticated_works(self, monkeypatch):
        monkeypatch.setenv("GL_ADMIN_TOKEN", "secret-token-abc")
        from fastapi.testclient import TestClient
        from web.backend import main as backend_main
        from web.backend.main import ws_broadcaster
        with TestClient(backend_main.app) as client:
            # 制造一些活动
            async def run():
                class FakeWS:
                    async def accept(self): pass
                    async def send_text(self, msg): pass
                await ws_broadcaster.connect(FakeWS(), client_ip="10.0.0.1")
            import asyncio
            asyncio.run(run())
            # 用正确 token reset
            resp = client.post(
                "/api/ws/stats/reset",
                headers={"X-Admin-Token": "secret-token-abc"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["reset"] is True
        assert data["stats"]["total_connections_accepted"] == 0

    def test_reset_no_auth_required_when_token_unset(self, monkeypatch):
        """未配置 token → reset 无需 auth (向后兼容)。"""
        monkeypatch.delenv("GL_ADMIN_TOKEN", raising=False)
        from fastapi.testclient import TestClient
        from web.backend import main as backend_main
        with TestClient(backend_main.app) as client:
            resp = client.post("/api/ws/stats/reset")
        assert resp.status_code == 200


# ============================================
# End-to-end: stats endpoint includes auth status
# ============================================

class TestAuthDisabledBackwardCompat:
    """默认 GL_ADMIN_TOKEN 未设置 → 所有现有调用方式继续工作。"""

    def test_get_stats_no_header_works(self, monkeypatch):
        monkeypatch.delenv("GL_ADMIN_TOKEN", raising=False)
        from fastapi.testclient import TestClient
        from web.backend import main as backend_main
        with TestClient(backend_main.app) as client:
            resp = client.get("/api/ws/stats")
        assert resp.status_code == 200
        # 新字段应该显示 admin_token_configured=False
        assert resp.json()["admin_token_configured"] is False

    def test_post_reset_no_header_works(self, monkeypatch):
        monkeypatch.delenv("GL_ADMIN_TOKEN", raising=False)
        from fastapi.testclient import TestClient
        from web.backend import main as backend_main
        with TestClient(backend_main.app) as client:
            resp = client.post("/api/ws/stats/reset")
        assert resp.status_code == 200
