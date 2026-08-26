"""
Tests for /api/seasonal-factors endpoint.
"""

import pytest
from fastapi.testclient import TestClient


def test_seasonal_factors_default_returns_all_12_months():
    """不传 sim_day 应该返回全年 12 个月 factor 表"""
    from web.backend.main import app

    with TestClient(app) as client:
        r = client.get("/api/seasonal-factors")
        assert r.status_code == 200
        data = r.json()
        assert "factors_by_month" in data
        assert len(data["factors_by_month"]) == 12
        # Jan (month=1) 应该 factor < 1.0 (low season for concrete)
        assert data["factors_by_month"]["1"]["concrete"] < 1.0
        # Jun (month=6) 应该 factor > 1.0 (peak)
        assert data["factors_by_month"]["6"]["concrete"] > 1.0
        # current_month / current_factors 应该 None (没传 sim_day)
        assert data["current_sim_day"] is None
        assert data["current_month"] is None
        assert data["current_factors"] is None


def test_seasonal_factors_with_sim_day():
    """传 sim_day 应该返回对应 month + factor"""
    from web.backend.main import app

    with TestClient(app) as client:
        # sim_day=150 → month=6 (Jun) → concrete=1.4
        r = client.get("/api/seasonal-factors?sim_day=150")
        assert r.status_code == 200
        data = r.json()
        assert data["current_sim_day"] == 150
        assert data["current_month"] == 6
        assert data["current_factors"]["concrete"] == 1.4

        # sim_day=359 → month=12 → concrete=0.4
        r = client.get("/api/seasonal-factors?sim_day=359")
        data = r.json()
        assert data["current_month"] == 12
        assert data["current_factors"]["concrete"] == 0.4


def test_seasonal_factors_year_wrap():
    """sim_day=360 应该 wrap 回 Jan"""
    from web.backend.main import app

    with TestClient(app) as client:
        r = client.get("/api/seasonal-factors?sim_day=360")
        data = r.json()
        assert data["current_month"] == 1
        assert data["current_factors"]["concrete"] == 0.4


def test_seasonal_factors_includes_all_materials():
    """每个 month 应该包含所有 material 的 factor"""
    from web.backend.main import app

    with TestClient(app) as client:
        r = client.get("/api/seasonal-factors")
        data = r.json()
        for month_str, factors in data["factors_by_month"].items():
            # 6 个 material (data/swedish_waste_stats.py 定义的)
            assert len(factors) >= 6
            assert "concrete" in factors
            assert "metal_scrap" in factors
            assert "wood_waste" in factors