"""
Tests for /api/facilities endpoint exposing real Swedish facilities.
"""

import pytest
from fastapi.testclient import TestClient


def test_facilities_default_returns_all():
    """不传 city/facility_type 应该返回全部 13 个"""
    from web.backend.main import app

    with TestClient(app) as client:
        r = client.get("/api/facilities")
        assert r.status_code == 200
        data = r.json()
        assert data["total"] >= 13
        assert data["total_available"] == data["total"]
        assert "facility_type_counts" in data
        assert len(data["facilities"]) == data["total"]


def test_facilities_filter_by_city():
    """?city=Borås 应该只返回 Borås 的设施"""
    from web.backend.main import app

    with TestClient(app) as client:
        r = client.get("/api/facilities?city=Borås")
        data = r.json()
        assert data["total"] >= 2
        for f in data["facilities"]:
            assert f["city"] == "Borås"


def test_facilities_filter_by_type():
    """?facility_type=metal_recovery 应该只返回该类型"""
    from web.backend.main import app

    with TestClient(app) as client:
        r = client.get("/api/facilities?facility_type=metal_recovery")
        data = r.json()
        assert data["total"] >= 1
        for f in data["facilities"]:
            assert f["facility_type"] == "metal_recovery"


def test_facilities_filter_unknown_city_returns_zero():
    """未知 city → 0 (不报错)"""
    from web.backend.main import app

    with TestClient(app) as client:
        r = client.get("/api/facilities?city=Atlantis")
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 0
        assert data["facilities"] == []


def test_facilities_include_operator_and_source():
    """每个 facility 应含 operator + source 字段"""
    from web.backend.main import app

    with TestClient(app) as client:
        r = client.get("/api/facilities")
        data = r.json()
        for f in data["facilities"]:
            assert "operator" in f
            assert "source" in f
            assert f["operator"] != ""