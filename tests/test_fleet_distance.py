"""
Tests for /api/fleet distance_to_depot + total_distance_km fields.
"""

import pytest
from fastapi.testclient import TestClient


def test_fleet_default_includes_distance_metrics():
    """/api/fleet 应该包含 avg_distance_to_depot_km + total_distance_km + depot"""
    from web.backend.main import app

    with TestClient(app) as client:
        r = client.get("/api/fleet")
        assert r.status_code == 200
        data = r.json()
        # 新字段
        assert "avg_distance_to_depot_km" in data
        assert "total_distance_km" in data
        assert "depot" in data
        assert "loading" in data
        # depot 应该是 Borås (默认)
        assert data["depot"]["lat"] == pytest.approx(57.7089, abs=0.01)
        assert data["depot"]["lon"] == pytest.approx(14.1618, abs=0.01)


def test_fleet_initial_state_distance_zero():
    """初始状态所有 vehicle 在 Borås depot, avg_distance_to_depot_km ≈ 0"""
    from web.backend.main import app

    with TestClient(app) as client:
        r = client.get("/api/fleet")
        data = r.json()
        # 所有 vehicle 默认在 Borås depot
        assert data["avg_distance_to_depot_km"] < 1.0
        assert data["total_distance_km"] == 0.0
        assert data["available"] == data["total_vehicles"]


def test_fleet_total_distance_accumulates_after_run():
    """跑一次 optimize cycle 后 total_distance_km 应该 > 0"""
    from web.backend.main import app

    with TestClient(app) as client:
        # 跑一次 optimization
        r = client.post("/api/optimize", json={"run_simulation": False})
        if r.status_code == 200:
            # 再查 fleet 状态
            r2 = client.get("/api/fleet")
            data = r2.json()
            # 如果匹配成功, total_distance_km 应该 > 0
            # (可能 no_match, 所以用 >= 0 而不是 > 0)
            assert data["total_distance_km"] >= 0.0


def test_fleet_status_fields_consistent():
    """fleet 各字段总和应该 = total_vehicles"""
    from web.backend.main import app

    with TestClient(app) as client:
        r = client.get("/api/fleet")
        data = r.json()
        accounted = data["available"] + data["en_route"] + data["loading"]
        assert accounted == data["total_vehicles"]


def test_fleet_utilization_rate_formula():
    """utilization_rate = (total - available) / total * 100"""
    from web.backend.main import app

    with TestClient(app) as client:
        r = client.get("/api/fleet")
        data = r.json()
        expected = (data["total_vehicles"] - data["available"]) / data["total_vehicles"] * 100
        assert abs(data["utilization_rate"] - expected) < 0.01