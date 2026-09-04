"""
iter #47: tests for /api/regions endpoint.

Covers:
1. /api/regions returns the 3 expected cities
2. Each region has population + per_capita_waste_kg + lat/lon
3. estimated_daily_waste_tons is correctly computed
4. Total population and waste aggregates are correct
"""
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def test_regions_endpoint_returns_three_cities():
    """Endpoint should return exactly 3 cities (Borås, Göteborg, Stockholm)."""
    from fastapi.testclient import TestClient
    from web.backend import main as backend_main
    client = TestClient(backend_main.app)
    resp = client.get("/api/regions")
    assert resp.status_code == 200
    data = resp.json()
    assert data["n_regions"] == 3
    city_names = {r["city"] for r in data["regions"]}
    assert city_names == {"Borås", "Göteborg", "Stockholm"}


def test_regions_endpoint_required_fields():
    """Each region must have all required fields populated."""
    from fastapi.testclient import TestClient
    from web.backend import main as backend_main
    client = TestClient(backend_main.app)
    resp = client.get("/api/regions")
    data = resp.json()
    for r in data["regions"]:
        assert "city" in r
        assert "population" in r
        assert "per_capita_waste_kg" in r
        assert "construction_share_pct" in r
        assert "industry_focus" in r
        assert "lat" in r
        assert "lon" in r
        assert "estimated_daily_waste_tons" in r
        # All required fields should be non-null
        assert r["population"] > 0
        assert r["per_capita_waste_kg"] > 0
        assert r["construction_share_pct"] >= 0
        assert r["lat"] is not None
        assert r["lon"] is not None


def test_regions_estimated_daily_waste_correct():
    """estimated_daily_waste_tons = population × per_capita_kg / 365 / 1000."""
    from fastapi.testclient import TestClient
    from web.backend import main as backend_main
    client = TestClient(backend_main.app)
    resp = client.get("/api/regions")
    data = resp.json()
    for r in data["regions"]:
        expected = round(r["population"] * r["per_capita_waste_kg"] / 365 / 1000, 1)
        assert abs(r["estimated_daily_waste_tons"] - expected) < 0.5


def test_regions_aggregates():
    """total_population + total_estimated_daily_waste_tons should sum fields."""
    from fastapi.testclient import TestClient
    from web.backend import main as backend_main
    client = TestClient(backend_main.app)
    resp = client.get("/api/regions")
    data = resp.json()
    pop_sum = sum(r["population"] for r in data["regions"])
    assert data["total_population"] == pop_sum
    waste_sum = round(
        sum(r["estimated_daily_waste_tons"] or 0 for r in data["regions"]), 1
    )
    assert data["total_estimated_daily_waste_tons"] == waste_sum


def test_regions_sorted_by_population_desc():
    """Stockholm > Göteborg > Borås by population."""
    from fastapi.testclient import TestClient
    from web.backend import main as backend_main
    client = TestClient(backend_main.app)
    resp = client.get("/api/regions")
    data = resp.json()
    pops = [r["population"] for r in data["regions"]]
    # Stockholm should be the largest
    assert max(pops) == 1_000_000  # Stockholm
    # Borås should be the smallest
    assert min(pops) == 74_000  # Borås


def test_regions_data_source_includes_scb():
    """Data source should reference SCB (the source of population data)."""
    from fastapi.testclient import TestClient
    from web.backend import main as backend_main
    client = TestClient(backend_main.app)
    resp = client.get("/api/regions")
    data = resp.json()
    assert "SCB" in data["data_source"]
