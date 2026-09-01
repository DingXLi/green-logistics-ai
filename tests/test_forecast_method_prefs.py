"""
Tests for forecast method persistence (iter #35).

_iter #35 改进_:
- 新 SQLite table forecast_method_prefs (metric → best_method, r_squared, ...)
- Persistence methods: save_method_pref, get_method_prefs, get_best_method,
  delete_method_pref, clear_method_prefs
- /api/persistence/forecast-confidence 自动持久化 best_method (fire-and-forget)
- /api/persistence/forecast 支持 method=auto → 用持久化的最佳 method
- 新 endpoint: GET /api/persistence/forecast-method-prefs (admin)
- 新 endpoint: DELETE /api/persistence/forecast-method-prefs (admin)
"""

import os
import tempfile
import pytest


# ============================================
# Persistence layer unit tests
# ============================================

class TestPersistenceMethodPrefs:
    """Persistence.save/get/delete/clear method prefs."""

    def _make_persistence(self):
        """Create a fresh in-memory-ish persistence for test isolation."""
        from agents.persistence import Persistence
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        return Persistence(db_path=tmp.name), tmp.name

    def test_save_creates_new_pref(self):
        p, path = self._make_persistence()
        try:
            result = p.save_method_pref("cost_sek", "linear", r_squared=0.85, history_n=14)
            assert result["action"] == "created"
            assert result["metric"] == "cost_sek"
            assert result["method"] == "linear"
            assert result["r_squared"] == 0.85
            assert result["history_n"] == 14
            assert result["n_samples"] == 1
        finally:
            os.unlink(path)

    def test_save_same_method_increments_samples(self):
        """同一 method 重复保存 → n_samples 累加。"""
        p, path = self._make_persistence()
        try:
            p.save_method_pref("cost_sek", "linear", r_squared=0.80)
            r2 = p.save_method_pref("cost_sek", "linear", r_squared=0.82)
            assert r2["action"] == "updated"
            assert r2["n_samples"] == 2
        finally:
            os.unlink(path)

    def test_save_different_method_resets_samples(self):
        """method 改变 → n_samples 重置为 1。"""
        p, path = self._make_persistence()
        try:
            p.save_method_pref("cost_sek", "linear", r_squared=0.80)
            p.save_method_pref("cost_sek", "linear", r_squared=0.82)  # n_samples=2
            r3 = p.save_method_pref("cost_sek", "moving_average", r_squared=0.90)
            assert r3["action"] == "updated_method_changed"
            assert r3["n_samples"] == 1
        finally:
            os.unlink(path)

    def test_save_validates_method(self):
        p, path = self._make_persistence()
        try:
            with pytest.raises(ValueError, match="method must be"):
                p.save_method_pref("cost_sek", "invalid_method")
        finally:
            os.unlink(path)

    def test_save_validates_metric(self):
        p, path = self._make_persistence()
        try:
            with pytest.raises(ValueError, match="metric must be"):
                p.save_method_pref("invalid_metric", "linear")
        finally:
            os.unlink(path)

    def test_get_method_prefs_empty(self):
        p, path = self._make_persistence()
        try:
            prefs = p.get_method_prefs()
            assert prefs == []
        finally:
            os.unlink(path)

    def test_get_method_prefs_multiple(self):
        p, path = self._make_persistence()
        try:
            p.save_method_pref("cost_sek", "linear", r_squared=0.85)
            p.save_method_pref("co2_kg", "moving_average", r_squared=0.70)
            p.save_method_pref("matches", "exponential_smoothing", r_squared=0.65)
            prefs = p.get_method_prefs()
            assert len(prefs) == 3
            # Ordered by metric
            assert [p["metric"] for p in prefs] == ["co2_kg", "cost_sek", "matches"]
            assert prefs[1]["best_method"] == "linear"
        finally:
            os.unlink(path)

    def test_get_best_method_returns_none_when_not_set(self):
        p, path = self._make_persistence()
        try:
            assert p.get_best_method("cost_sek") is None
        finally:
            os.unlink(path)

    def test_get_best_method_returns_saved(self):
        p, path = self._make_persistence()
        try:
            p.save_method_pref("cost_sek", "moving_average", r_squared=0.75)
            assert p.get_best_method("cost_sek") == "moving_average"
        finally:
            os.unlink(path)

    def test_delete_method_pref(self):
        p, path = self._make_persistence()
        try:
            p.save_method_pref("cost_sek", "linear", r_squared=0.85)
            assert p.delete_method_pref("cost_sek") is True
            assert p.get_best_method("cost_sek") is None
            # 二次删除返回 False
            assert p.delete_method_pref("cost_sek") is False
        finally:
            os.unlink(path)

    def test_clear_method_prefs(self):
        p, path = self._make_persistence()
        try:
            p.save_method_pref("cost_sek", "linear", r_squared=0.85)
            p.save_method_pref("co2_kg", "moving_average", r_squared=0.70)
            p.save_method_pref("matches", "exponential_smoothing", r_squared=0.65)
            n = p.clear_method_prefs()
            assert n == 3
            assert p.get_method_prefs() == []
        finally:
            os.unlink(path)


# ============================================
# /api/persistence/forecast endpoint: method=auto
# ============================================

class TestForecastMethodAuto:
    """method=auto 用持久化的最佳 method。"""

    def test_auto_method_default_to_linear_when_no_pref(self, monkeypatch):
        """没持久化 pref 时, method=auto → 默认 linear。"""
        from fastapi.testclient import TestClient
        from web.backend import main as backend_main

        # 确保 prefs 为空
        if backend_main.coordinator and backend_main.coordinator.persistence:
            backend_main.coordinator.persistence.clear_method_prefs()

        with TestClient(backend_main.app) as client:
            resp = client.get("/api/persistence/forecast?method=auto&metrics=cost_sek")
        assert resp.status_code == 200
        data = resp.json()
        # 没有持久化 → 默认 linear
        assert data["method"] == "linear"

    def test_auto_method_uses_persisted_pref(self, monkeypatch):
        """有持久化 pref 时, method=auto → 用该 method。"""
        from fastapi.testclient import TestClient
        from web.backend import main as backend_main

        if backend_main.coordinator and backend_main.coordinator.persistence:
            # 强制持久化 co2_kg → moving_average
            backend_main.coordinator.persistence.save_method_pref(
                "co2_kg", "moving_average", r_squared=0.75
            )

        with TestClient(backend_main.app) as client:
            resp = client.get("/api/persistence/forecast?method=auto&metrics=co2_kg")
        assert resp.status_code == 200
        data = resp.json()
        assert data["method"] == "moving_average"

    def test_invalid_method_returns_400(self):
        from fastapi.testclient import TestClient
        from web.backend import main as backend_main
        with TestClient(backend_main.app) as client:
            resp = client.get("/api/persistence/forecast?method=bogus")
        assert resp.status_code == 400
        assert "invalid method" in resp.json()["detail"]


# ============================================
# /api/persistence/forecast-confidence auto-persist
# ============================================

class TestForecastConfidenceAutoPersist:
    """调用 forecast-confidence 应该把 best_method 持久化。"""

    def test_confidence_call_persists_best_method(self):
        """调用 forecast-confidence 后, best_method 被写入 prefs。"""
        from fastapi.testclient import TestClient
        from web.backend import main as backend_main

        if backend_main.coordinator and backend_main.coordinator.persistence:
            backend_main.coordinator.persistence.clear_method_prefs()

        with TestClient(backend_main.app) as client:
            resp = client.get("/api/persistence/forecast-confidence?metrics=cost_sek,co2_kg&horizon=3")
        assert resp.status_code == 200
        data = resp.json()
        # best_method 字段存在
        assert "cost_sek" in data["confidence"]
        assert "co2_kg" in data["confidence"]
        assert "best_method" in data["confidence"]["cost_sek"]

        # 持久化检查
        if backend_main.coordinator and backend_main.coordinator.persistence:
            prefs = backend_main.coordinator.persistence.get_method_prefs()
            metrics_persisted = [p["metric"] for p in prefs]
            assert "cost_sek" in metrics_persisted
            assert "co2_kg" in metrics_persisted


# ============================================
# /api/persistence/forecast-method-prefs GET / DELETE
# ============================================

class TestForecastMethodPrefsEndpoints:
    """GET / DELETE endpoint (需要 admin auth)。"""

    def test_get_prefs_requires_auth_when_token_set(self, monkeypatch):
        monkeypatch.setenv("GL_ADMIN_TOKEN", "secret")
        from fastapi.testclient import TestClient
        from web.backend import main as backend_main
        with TestClient(backend_main.app) as client:
            resp = client.get("/api/persistence/forecast-method-prefs")
        assert resp.status_code == 401

    def test_get_prefs_no_auth_required_when_token_unset(self, monkeypatch):
        monkeypatch.delenv("GL_ADMIN_TOKEN", raising=False)
        from fastapi.testclient import TestClient
        from web.backend import main as backend_main
        with TestClient(backend_main.app) as client:
            resp = client.get("/api/persistence/forecast-method-prefs")
        assert resp.status_code == 200
        data = resp.json()
        assert "prefs" in data
        assert "count" in data
        assert "metrics_covered" in data

    def test_get_prefs_returns_persisted(self, monkeypatch):
        monkeypatch.delenv("GL_ADMIN_TOKEN", raising=False)
        from fastapi.testclient import TestClient
        from web.backend import main as backend_main
        if backend_main.coordinator and backend_main.coordinator.persistence:
            backend_main.coordinator.persistence.clear_method_prefs()
            backend_main.coordinator.persistence.save_method_pref(
                "cost_sek", "linear", r_squared=0.85, history_n=14
            )
        with TestClient(backend_main.app) as client:
            resp = client.get("/api/persistence/forecast-method-prefs")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] >= 1
        assert "cost_sek" in data["metrics_covered"]

    def test_delete_prefs_requires_auth(self, monkeypatch):
        monkeypatch.setenv("GL_ADMIN_TOKEN", "secret")
        from fastapi.testclient import TestClient
        from web.backend import main as backend_main
        with TestClient(backend_main.app) as client:
            resp = client.delete("/api/persistence/forecast-method-prefs")
        assert resp.status_code == 401

    def test_delete_prefs_all_when_no_metric_param(self, monkeypatch):
        monkeypatch.delenv("GL_ADMIN_TOKEN", raising=False)
        from fastapi.testclient import TestClient
        from web.backend import main as backend_main
        if backend_main.coordinator and backend_main.coordinator.persistence:
            backend_main.coordinator.persistence.save_method_pref(
                "cost_sek", "linear", r_squared=0.85
            )
        with TestClient(backend_main.app) as client:
            resp = client.delete("/api/persistence/forecast-method-prefs")
        assert resp.status_code == 200
        data = resp.json()
        assert data["scope"] == "all"
        assert data["deleted"] >= 1

    def test_delete_prefs_specific_metric(self, monkeypatch):
        monkeypatch.delenv("GL_ADMIN_TOKEN", raising=False)
        from fastapi.testclient import TestClient
        from web.backend import main as backend_main
        if backend_main.coordinator and backend_main.coordinator.persistence:
            backend_main.coordinator.persistence.save_method_pref(
                "cost_sek", "linear", r_squared=0.85
            )
        with TestClient(backend_main.app) as client:
            resp = client.delete(
                "/api/persistence/forecast-method-prefs?metric=cost_sek"
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["scope"] == "metric"
        assert data["metric"] == "cost_sek"
        assert data["deleted"] is True


# ============================================
# Schema migration safety
# ============================================

class TestSchemaMigration:
    """init_schema 应该 idempotent + 兼容旧 DB。"""

    def test_schema_idempotent(self):
        """多次调用 init_schema 不报错。"""
        from agents.persistence import Persistence
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        try:
            p1 = Persistence(db_path=tmp.name)
            p2 = Persistence(db_path=tmp.name)  # 第二打开 = 第二次 init_schema
            # 应该不报错
            prefs = p2.get_method_prefs()
            assert prefs == []
        finally:
            os.unlink(tmp.name)
