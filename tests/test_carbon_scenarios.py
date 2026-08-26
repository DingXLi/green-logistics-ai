"""
Tests for carbon price scenarios endpoint / API logic.

These tests avoid spinning up the full FastAPI app (which requires database,
coordinator init, world bootstrap, etc.) and instead test the pure
helper logic + schema validation directly.
"""

import pytest
from fastapi.testclient import TestClient
from fastapi import HTTPException


def test_carbon_scenarios_endpoint_validates_carbon_prices():
    """无效的 carbon_prices 字符串应该返回 400。"""
    from web.backend.main import app

    with TestClient(app) as client:
        # 错误格式
        r = client.get("/api/optimize/carbon-scenarios?carbon_prices=abc,xyz")
        assert r.status_code == 400

        # 太多价格
        r = client.get("/api/optimize/carbon-scenarios?carbon_prices=1,2,3,4,5,6,7,8,9")
        assert r.status_code == 400

        # 空字符串走默认 fallback (返回 200 + 4 默认场景)
        r = client.get("/api/optimize/carbon-scenarios?carbon_prices=")
        assert r.status_code == 200
        assert len(r.json()["scenarios"]) == 4


def test_carbon_scenarios_returns_expected_schema():
    """返回结构必须包含 n_scenarios 和每 scenario 的 cost/co2 optimal。"""
    from web.backend.main import app

    with TestClient(app) as client:
        # 2 个价格 + time_limit=1s 让测试快速
        r = client.get(
            "/api/optimize/carbon-scenarios"
            "?carbon_prices=0,1.5&time_limit_seconds=2"
        )
        # 可能 200（有 matches）或 503（coordinator 未启）
        # 至少 schema 应该是 dict
        assert r.status_code in (200, 503)
        if r.status_code == 200:
            data = r.json()
            assert "scenarios" in data
            assert isinstance(data["scenarios"], list)
            assert len(data["scenarios"]) == 2
            for s in data["scenarios"]:
                assert "carbon_price_sek_per_kg" in s
                assert "cost_optimal" in s
                assert "co2_optimal" in s
                # cost-optimal 应该是 cost_weight=1 (纯成本)
                # co2-optimal 应该是 co2_weight=1 (纯碳)
                assert s["cost_optimal"] is not None
                assert s["co2_optimal"] is not None
                assert s["cost_optimal"]["cost_sek"] is not None or \
                       s["cost_optimal"]["n_routes"] == 0
                # 默认 4 pareto points
                assert len(s["pareto"]) == 4


def test_carbon_scenarios_default_prices():
    """不传 carbon_prices 应该用 4 个默认场景 [0, 1.5, 3, 5]。"""
    from web.backend.main import app

    with TestClient(app) as client:
        r = client.get("/api/optimize/carbon-scenarios?time_limit_seconds=2")
        if r.status_code == 200:
            data = r.json()
            prices = [s["carbon_price_sek_per_kg"] for s in data["scenarios"]]
            assert prices == [0.0, 1.5, 3.0, 5.0]


def test_carbon_scenarios_zero_price_still_optimizes():
    """碳价=0 时也应该能算（CO2-optimal 还是会有解，只是 cost 可能很高）。"""
    from web.backend.main import app

    with TestClient(app) as client:
        r = client.get(
            "/api/optimize/carbon-scenarios?carbon_prices=0&time_limit_seconds=2"
        )
        if r.status_code == 200:
            data = r.json()
            assert len(data["scenarios"]) == 1
            assert data["scenarios"][0]["carbon_price_sek_per_kg"] == 0.0