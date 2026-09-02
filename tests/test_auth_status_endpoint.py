"""
Tests for /api/admin/auth/status endpoint (iter #36).

iter #36 adds a public discovery endpoint that lets clients (admin UIs,
debug scripts, CI runners) detect whether ``GL_ADMIN_TOKEN`` is configured
on the server before attempting to call protected endpoints.

The endpoint is intentionally **not** protected — that's the whole point:
an unauthenticated client should be able to ask "is auth enabled?".
"""

import os
import pytest


# ============================================
# Endpoint basics (response shape)
# ============================================

class TestAuthStatusEndpoint:
    """/api/admin/auth/status — public discovery endpoint."""

    def test_endpoint_returns_200_when_auth_disabled(self, monkeypatch):
        """No token configured → 200 with auth_enabled=False."""
        monkeypatch.delenv("GL_ADMIN_TOKEN", raising=False)
        from fastapi.testclient import TestClient
        from web.backend import main as backend_main
        with TestClient(backend_main.app) as client:
            resp = client.get("/api/admin/auth/status")
        assert resp.status_code == 200

    def test_endpoint_returns_200_when_auth_enabled(self, monkeypatch):
        """Token configured → 200 (still public, just informative)."""
        monkeypatch.setenv("GL_ADMIN_TOKEN", "secret-token-abc123")
        from fastapi.testclient import TestClient
        from web.backend import main as backend_main
        with TestClient(backend_main.app) as client:
            resp = client.get("/api/admin/auth/status")
        assert resp.status_code == 200

    def test_response_has_required_fields_when_disabled(self, monkeypatch):
        """Required response fields exist when auth is disabled."""
        monkeypatch.delenv("GL_ADMIN_TOKEN", raising=False)
        from fastapi.testclient import TestClient
        from web.backend import main as backend_main
        with TestClient(backend_main.app) as client:
            data = client.get("/api/admin/auth/status").json()
        for key in (
            "auth_enabled",
            "token_length",
            "token_preview",
            "header_formats",
            "protected_endpoints",
            "protected_endpoint_count",
            "usage_hint",
        ):
            assert key in data, f"missing field: {key}"
        assert data["auth_enabled"] is False
        assert data["token_length"] == 0
        assert data["token_preview"] is None

    def test_response_has_required_fields_when_enabled(self, monkeypatch):
        """Required response fields exist + populated correctly when auth on."""
        monkeypatch.setenv("GL_ADMIN_TOKEN", "secret-token-abc123")
        from fastapi.testclient import TestClient
        from web.backend import main as backend_main
        with TestClient(backend_main.app) as client:
            data = client.get("/api/admin/auth/status").json()
        assert data["auth_enabled"] is True
        assert data["token_length"] == len("secret-token-abc123")
        # preview should mask the middle
        assert data["token_preview"] is not None
        assert "secret-token-abc123" not in data["token_preview"]
        assert "****" in data["token_preview"]


# ============================================
# Token preview masking
# ============================================

class TestTokenPreviewMasking:
    """token_preview must never leak the actual secret."""

    def test_long_token_preview_masks_middle(self, monkeypatch):
        """Token >= 8 chars → first 2 + **** + last 2."""
        monkeypatch.setenv("GL_ADMIN_TOKEN", "abcdef1234567890xyz")
        from fastapi.testclient import TestClient
        from web.backend import main as backend_main
        with TestClient(backend_main.app) as client:
            data = client.get("/api/admin/auth/status").json()
        assert data["token_preview"] == "ab****yz"

    def test_exactly_8_chars_token_preview(self, monkeypatch):
        """Token exactly 8 chars → still uses first/last 2 masking."""
        monkeypatch.setenv("GL_ADMIN_TOKEN", "abcdefgh")
        from fastapi.testclient import TestClient
        from web.backend import main as backend_main
        with TestClient(backend_main.app) as client:
            data = client.get("/api/admin/auth/status").json()
        assert data["token_preview"] == "ab****gh"

    def test_short_token_fully_masked(self, monkeypatch):
        """Token < 8 chars → fully masked (can't safely show any chars)."""
        monkeypatch.setenv("GL_ADMIN_TOKEN", "abc")
        from fastapi.testclient import TestClient
        from web.backend import main as backend_main
        with TestClient(backend_main.app) as client:
            data = client.get("/api/admin/auth/status").json()
        assert data["token_preview"] == "****"
        # sanity: actual token not in preview
        assert "abc" not in data["token_preview"]

    def test_min_length_token(self, monkeypatch):
        """Token length 1 → fully masked."""
        monkeypatch.setenv("GL_ADMIN_TOKEN", "x")
        from fastapi.testclient import TestClient
        from web.backend import main as backend_main
        with TestClient(backend_main.app) as client:
            data = client.get("/api/admin/auth/status").json()
        assert data["token_preview"] == "****"
        assert data["token_length"] == 1


# ============================================
# Header formats documentation
# ============================================

class TestHeaderFormatsField:
    """header_formats documents how to pass the token."""

    def test_lists_both_supported_formats(self, monkeypatch):
        """Both Authorization Bearer and X-Admin-Token listed."""
        monkeypatch.delenv("GL_ADMIN_TOKEN", raising=False)
        from fastapi.testclient import TestClient
        from web.backend import main as backend_main
        with TestClient(backend_main.app) as client:
            data = client.get("/api/admin/auth/status").json()
        formats = data["header_formats"]
        assert len(formats) >= 2
        assert any("Bearer" in f for f in formats)
        assert any("X-Admin-Token" in f for f in formats)

    def test_header_formats_consistent_when_auth_on(self, monkeypatch):
        """header_formats returns same list regardless of auth state."""
        monkeypatch.setenv("GL_ADMIN_TOKEN", "secret-12345")
        from fastapi.testclient import TestClient
        from web.backend import main as backend_main
        with TestClient(backend_main.app) as client:
            data = client.get("/api/admin/auth/status").json()
        formats = data["header_formats"]
        assert any("Bearer" in f for f in formats)
        assert any("X-Admin-Token" in f for f in formats)


# ============================================
# Protected endpoints list
# ============================================

class TestProtectedEndpointsList:
    """protected_endpoints + protected_endpoint_count must stay in sync."""

    def test_count_matches_list_length(self, monkeypatch):
        """protected_endpoint_count == len(protected_endpoints)."""
        monkeypatch.delenv("GL_ADMIN_TOKEN", raising=False)
        from fastapi.testclient import TestClient
        from web.backend import main as backend_main
        with TestClient(backend_main.app) as client:
            data = client.get("/api/admin/auth/status").json()
        assert data["protected_endpoint_count"] == len(data["protected_endpoints"])

    def test_known_endpoints_in_list(self, monkeypatch):
        """Critical protected endpoints are listed."""
        monkeypatch.delenv("GL_ADMIN_TOKEN", raising=False)
        from fastapi.testclient import TestClient
        from web.backend import main as backend_main
        with TestClient(backend_main.app) as client:
            data = client.get("/api/admin/auth/status").json()
        paths = data["protected_endpoints"]
        # iter #34 protected endpoints (must include)
        assert "/api/ws/stats" in paths
        assert "/api/admin/db-stats" in paths
        assert "/api/admin/db-maintenance" in paths
        assert "/api/admin/db-export" in paths
        assert "/api/admin/perf-stats" in paths
        assert "/api/admin/llm-stats" in paths
        # iter #33/35 protected
        assert "/api/debug/llm" in paths
        assert "/api/persistence/forecast-method-prefs" in paths

    def test_status_endpoint_not_in_protected_list(self, monkeypatch):
        """The auth/status endpoint itself is not protected (it shouldn't list itself)."""
        monkeypatch.delenv("GL_ADMIN_TOKEN", raising=False)
        from fastapi.testclient import TestClient
        from web.backend import main as backend_main
        with TestClient(backend_main.app) as client:
            data = client.get("/api/admin/auth/status").json()
        assert "/api/admin/auth/status" not in data["protected_endpoints"]


# ============================================
# Usage hint
# ============================================

class TestUsageHint:
    """usage_hint should be context-aware (different when auth on/off)."""

    def test_usage_hint_when_disabled(self, monkeypatch):
        """Disabled → hint tells user auth is off and how to enable."""
        monkeypatch.delenv("GL_ADMIN_TOKEN", raising=False)
        from fastapi.testclient import TestClient
        from web.backend import main as backend_main
        with TestClient(backend_main.app) as client:
            data = client.get("/api/admin/auth/status").json()
        hint = data["usage_hint"]
        # Mentions disabled state
        assert "disabled" in hint.lower() or "public" in hint.lower()
        # Mentions GL_ADMIN_TOKEN (so user knows how to enable)
        assert "GL_ADMIN_TOKEN" in hint

    def test_usage_hint_when_enabled(self, monkeypatch):
        """Enabled → hint shows curl-style usage example."""
        monkeypatch.setenv("GL_ADMIN_TOKEN", "secret-abc123")
        from fastapi.testclient import TestClient
        from web.backend import main as backend_main
        with TestClient(backend_main.app) as client:
            data = client.get("/api/admin/auth/status").json()
        hint = data["usage_hint"]
        assert "Bearer" in hint or "curl" in hint.lower()


# ============================================
# Public access (no auth required)
# ============================================

class TestStatusEndpointIsPublic:
    """The status endpoint itself must NOT require admin auth."""

    def test_accessible_without_auth_when_enabled(self, monkeypatch):
        """When token IS configured, /api/admin/auth/status still works
        without any auth header (so clients can discover auth state)."""
        monkeypatch.setenv("GL_ADMIN_TOKEN", "secret-token-abc123")
        from fastapi.testclient import TestClient
        from web.backend import main as backend_main
        with TestClient(backend_main.app) as client:
            resp = client.get("/api/admin/auth/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["auth_enabled"] is True

    def test_accessible_with_wrong_token_when_enabled(self, monkeypatch):
        """Wrong token still gets through to status (no 401)."""
        monkeypatch.setenv("GL_ADMIN_TOKEN", "correct-token")
        from fastapi.testclient import TestClient
        from web.backend import main as backend_main
        with TestClient(backend_main.app) as client:
            resp = client.get(
                "/api/admin/auth/status",
                headers={"Authorization": "Bearer wrong-token"},
            )
        assert resp.status_code == 200


# ============================================
# Hot reload: env var changes are picked up at request time
# ============================================

class TestAuthStatusHotReload:
    """Auth status should reflect env var state at request time."""

    def test_status_changes_when_token_added(self, monkeypatch):
        """Set token → next call sees auth_enabled=True."""
        monkeypatch.delenv("GL_ADMIN_TOKEN", raising=False)
        from fastapi.testclient import TestClient
        from web.backend import main as backend_main
        with TestClient(backend_main.app) as client:
            # Before
            data1 = client.get("/api/admin/auth/status").json()
            assert data1["auth_enabled"] is False
            # After (in-process monkeypatch.setenv persists for TestClient lifetime)
            monkeypatch.setenv("GL_ADMIN_TOKEN", "new-token-xyz")
            data2 = client.get("/api/admin/auth/status").json()
            assert data2["auth_enabled"] is True
