"""
Tests for broader admin token auth coverage (iter #34).

_iter #34 改进_: 把 iter #33 引入的 admin token auth 扩展到所有 /api/admin/* 和 /api/debug/* endpoint:
- /api/admin/db-maintenance (POST)
- /api/admin/db-export (GET)
- /api/admin/db-stats (GET)
- /api/admin/db-info (GET)
- /api/admin/perf-stats (GET)
- /api/admin/perf-stats/reset (POST)
- /api/admin/llm-stats (GET)
- /api/admin/llm-stats/reset (POST)
- /api/debug/llm (GET)

全部通过 FastAPI dependency ``Depends(require_admin)`` 共享同一份检查逻辑。
"""

import pytest


# ============================================
# Endpoints requiring admin auth (when GL_ADMIN_TOKEN set)
# ============================================

# (method, path, [optional query params])
ADMIN_PROTECTED_ENDPOINTS = [
    ("GET", "/api/admin/db-stats", {}),
    ("GET", "/api/admin/db-info", {}),
    ("GET", "/api/admin/perf-stats", {}),
    ("GET", "/api/admin/llm-stats", {}),
    ("POST", "/api/admin/db-maintenance", {}),
    ("POST", "/api/admin/perf-stats/reset", {}),
    ("POST", "/api/admin/llm-stats/reset", {}),
    ("GET", "/api/debug/llm", {}),
    ("GET", "/api/admin/db-export", {"table": "cycles", "limit": 1}),
]


# ============================================
# Backward compat: no token = no auth required
# ============================================

class TestNoTokenBackwardCompat:
    """未配置 GL_ADMIN_TOKEN → 所有 admin endpoint 仍然可访问 (dev mode)。"""

    @pytest.mark.parametrize("method,path,params", ADMIN_PROTECTED_ENDPOINTS)
    def test_endpoint_no_token_no_auth_required(self, monkeypatch, method, path, params):
        monkeypatch.delenv("GL_ADMIN_TOKEN", raising=False)
        from fastapi.testclient import TestClient
        from web.backend import main as backend_main
        with TestClient(backend_main.app) as client:
            if method == "GET":
                resp = client.get(path, params=params)
            else:
                resp = client.post(path)
        # 200 或 3xx 都算 OK (关键是 NOT 401)
        assert resp.status_code != 401, \
            f"{method} {path} returned 401 unexpectedly: {resp.text[:200]}"


# ============================================
# Token configured: reject unauthenticated
# ============================================

class TestTokenConfiguredRejectsUnauthenticated:
    """GL_ADMIN_TOKEN 设置后, 所有 admin endpoint 无 auth 返回 401。"""

    @pytest.mark.parametrize("method,path,params", ADMIN_PROTECTED_ENDPOINTS)
    def test_endpoint_no_auth_returns_401(self, monkeypatch, method, path, params):
        monkeypatch.setenv("GL_ADMIN_TOKEN", "secret-token-abc")
        from fastapi.testclient import TestClient
        from web.backend import main as backend_main
        with TestClient(backend_main.app) as client:
            if method == "GET":
                resp = client.get(path, params=params)
            else:
                resp = client.post(path)
        assert resp.status_code == 401, \
            f"{method} {path} should return 401, got {resp.status_code}"
        assert "Admin token required" in resp.json()["detail"]
        assert resp.headers.get("www-authenticate") == "Bearer"

    @pytest.mark.parametrize("method,path,params", ADMIN_PROTECTED_ENDPOINTS)
    def test_endpoint_wrong_token_returns_401(self, monkeypatch, method, path, params):
        monkeypatch.setenv("GL_ADMIN_TOKEN", "secret-token-abc")
        from fastapi.testclient import TestClient
        from web.backend import main as backend_main
        with TestClient(backend_main.app) as client:
            if method == "GET":
                resp = client.get(
                    path, params=params,
                    headers={"X-Admin-Token": "wrong"},
                )
            else:
                resp = client.post(path, headers={"X-Admin-Token": "wrong"})
        assert resp.status_code == 401, \
            f"{method} {path} with wrong token should return 401, got {resp.status_code}"


# ============================================
# Token configured: accept authenticated
# ============================================

class TestTokenConfiguredAcceptsAuthenticated:
    """GL_ADMIN_TOKEN 设置后, 提供正确 token 可访问 admin endpoint。"""

    @pytest.mark.parametrize("method,path,params", ADMIN_PROTECTED_ENDPOINTS)
    def test_endpoint_bearer_header_works(self, monkeypatch, method, path, params):
        monkeypatch.setenv("GL_ADMIN_TOKEN", "secret-token-abc")
        from fastapi.testclient import TestClient
        from web.backend import main as backend_main
        with TestClient(backend_main.app) as client:
            if method == "GET":
                resp = client.get(
                    path, params=params,
                    headers={"Authorization": "Bearer secret-token-abc"},
                )
            else:
                resp = client.post(
                    path,
                    headers={"Authorization": "Bearer secret-token-abc"},
                )
        # 不应该是 401
        assert resp.status_code != 401, \
            f"{method} {path} with Bearer should not 401: {resp.text[:200]}"

    @pytest.mark.parametrize("method,path,params", ADMIN_PROTECTED_ENDPOINTS)
    def test_endpoint_x_admin_token_header_works(self, monkeypatch, method, path, params):
        monkeypatch.setenv("GL_ADMIN_TOKEN", "secret-token-abc")
        from fastapi.testclient import TestClient
        from web.backend import main as backend_main
        with TestClient(backend_main.app) as client:
            if method == "GET":
                resp = client.get(
                    path, params=params,
                    headers={"X-Admin-Token": "secret-token-abc"},
                )
            else:
                resp = client.post(
                    path,
                    headers={"X-Admin-Token": "secret-token-abc"},
                )
        assert resp.status_code != 401, \
            f"{method} {path} with X-Admin-Token should not 401: {resp.text[:200]}"


# ============================================
# Specific endpoint shape sanity checks
# ============================================

class TestSpecificEndpointShapes:
    """几个关键 endpoint 在 auth 后仍返回正确 shape。"""

    def test_db_stats_returns_db_size_bytes(self, monkeypatch):
        monkeypatch.delenv("GL_ADMIN_TOKEN", raising=False)
        from fastapi.testclient import TestClient
        from web.backend import main as backend_main
        with TestClient(backend_main.app) as client:
            resp = client.get("/api/admin/db-stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "db_size_bytes" in data or "error" in data  # data may have error if DB not init

    def test_admin_token_configured_visible(self, monkeypatch):
        """GL_ADMIN_TOKEN 设置后, /api/ws/stats 的 admin_token_configured=True (iter #33 字段仍在)。"""
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


# ============================================
# Dependency is reusable
# ============================================

class TestRequireAdminDependency:
    """FastAPI dependency require_admin 可被复用。"""

    def test_dependency_callable(self):
        from web.backend.main import require_admin
        assert callable(require_admin)

    def test_dependency_is_async(self):
        """require_admin 必须是 async (FastAPI dependency 协议)。"""
        import inspect
        from web.backend.main import require_admin
        assert inspect.iscoroutinefunction(require_admin), \
            "require_admin must be async to be used as FastAPI dependency"

    def test_dependency_signature(self):
        """require_admin 接受 authorization + x_admin_token header。"""
        import inspect
        from web.backend.main import require_admin
        sig = inspect.signature(require_admin)
        params = list(sig.parameters.keys())
        assert "authorization" in params
        assert "x_admin_token" in params


# ============================================
# Cross-file consistency: smoke test smoke endpoint integration
# ============================================

class TestMixedAuthEndpoints:
    """同一 request 中, 未保护 endpoint 不需要 auth, 保护的需要。"""

    def test_health_no_auth_required_with_admin_token_set(self, monkeypatch):
        """/health 在 GL_ADMIN_TOKEN 设置时仍公开。"""
        monkeypatch.setenv("GL_ADMIN_TOKEN", "secret-token-abc")
        from fastapi.testclient import TestClient
        from web.backend import main as backend_main
        with TestClient(backend_main.app) as client:
            resp = client.get("/health")
        assert resp.status_code == 200

    def test_health_metadata_no_auth_required(self, monkeypatch):
        """/api/health/deep 在 GL_ADMIN_TOKEN 设置时仍公开。"""
        monkeypatch.setenv("GL_ADMIN_TOKEN", "secret-token-abc")
        from fastapi.testclient import TestClient
        from web.backend import main as backend_main
        with TestClient(backend_main.app) as client:
            resp = client.get("/api/health/deep")
        assert resp.status_code == 200

    def test_persistence_summary_no_auth_required(self, monkeypatch):
        """/api/persistence/summary 不需要 admin auth (公共 endpoint)。"""
        monkeypatch.setenv("GL_ADMIN_TOKEN", "secret-token-abc")
        from fastapi.testclient import TestClient
        from web.backend import main as backend_main
        with TestClient(backend_main.app) as client:
            resp = client.get("/api/persistence/summary")
        # 公开 endpoint, 不需要 auth
        assert resp.status_code != 401
