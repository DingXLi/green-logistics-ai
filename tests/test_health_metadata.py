"""
Tests for /health endpoint metadata (production mode indicator).
"""

import pytest
from fastapi.testclient import TestClient


def test_health_returns_environment_metadata():
    """/health 应该返回 environment + data_mode + features"""
    from web.backend.main import app

    with TestClient(app) as client:
        r = client.get("/health")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "healthy"
        assert "timestamp" in data
        # 新字段: production 标记
        assert "environment" in data
        assert data["environment"] in ("production", "development")
        # data_mode 应该总是 "real" (不再支持 demo mode)
        assert data["data_mode"] == "real"
        # features 字典包含所有关键功能
        assert "features" in data
        features = data["features"]
        assert features["websocket_enabled"] is True
        assert features["carbon_scenarios"] is True
        assert features["seasonal_factors"] is True
        assert features["real_sweden_facilities"] is True
        assert "scheduler_enabled" in features


def test_health_production_with_hf_env(monkeypatch):
    """HF Space env var (SPACE_ID) 应该让 environment=production"""
    from web.backend.main import app

    monkeypatch.setenv("SPACE_ID", "test-space-id")

    with TestClient(app) as client:
        r = client.get("/health")
        data = r.json()
        assert data["environment"] == "production"


def test_health_production_with_env_var(monkeypatch):
    """ENVIRONMENT=production env var 也应该让 environment=production"""
    from web.backend.main import app

    monkeypatch.setenv("ENVIRONMENT", "production")

    with TestClient(app) as client:
        r = client.get("/health")
        data = r.json()
        assert data["environment"] == "production"


def test_health_no_demo_mode():
    """确认 /health 响应不包含 demo 标记 (生产模式)"""
    from web.backend.main import app

    with TestClient(app) as client:
        r = client.get("/health")
        data = r.json()
        # data_mode 必须是 "real", 不能是 "demo" 或 "sample"
        assert "demo" not in str(data).lower(), "health should not mention demo mode"
        assert "sample" not in str(data).lower(), "health should not mention sample mode"