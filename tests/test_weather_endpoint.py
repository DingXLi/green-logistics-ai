"""
iter #50: tests for /api/weather endpoint.

Covers:
1. Endpoint returns weather data with expected shape
2. Default lat/lon (Borås) is accepted
3. lat/lon validation rejects out-of-range values
4. Endpoint gracefully handles data layer failures
5. use_cache query param is forwarded
"""
import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def test_endpoint_default_boras():
    """Default lat/lon (Borås) returns a weather payload."""
    from fastapi.testclient import TestClient
    from web.backend import main as backend_main

    class _FakeWeather:
        def get_forecast(self, lat, lon, use_cache=True):
            self.kwargs = (lat, lon, use_cache)
            return {
                "current": {"t": 15.5, "pmean": 0.1, "ws": 3.0, "rh": 65},
                "next_24h_avg": {"temperature_c": 14.0, "precipitation_mm_h": 0.05, "wind_m_s": 2.5},
                "summary": "mild & dry",
                "source": "smhi",
                "timestamp": "2026-09-05T00:00:00",
            }

    # monkey-patch the data module so TestClient can call it
    import data.weather_smhi as wmod
    orig = wmod.get_forecast
    fake = _FakeWeather()
    wmod.get_forecast = fake.get_forecast
    try:
        client = TestClient(backend_main.app)
        resp = client.get("/api/weather")
        assert resp.status_code == 200
        data = resp.json()
        assert data["source"] == "smhi"
        assert data["current"]["t"] == 15.5
        # Default Borås coords
        assert fake.kwargs[0] == 57.7089
        assert fake.kwargs[1] == 14.1618
        assert fake.kwargs[2] is True  # use_cache default
    finally:
        wmod.get_forecast = orig


def test_endpoint_custom_lat_lon():
    """Custom lat/lon passed through to underlying weather function."""
    from fastapi.testclient import TestClient
    from web.backend import main as backend_main

    class _FakeWeather:
        def __init__(self):
            self.kwargs = None
        def get_forecast(self, lat, lon, use_cache=True):
            self.kwargs = (lat, lon, use_cache)
            return {"current": {}, "next_24h_avg": {}, "summary": "ok",
                    "source": "smhi", "timestamp": "2026-09-05"}

    fake = _FakeWeather()
    import data.weather_smhi as wmod
    orig = wmod.get_forecast
    wmod.get_forecast = fake.get_forecast
    try:
        client = TestClient(backend_main.app)
        resp = client.get("/api/weather?lat=59.3293&lon=18.0686&use_cache=false")
        assert resp.status_code == 200
        # Stockholm coords
        assert fake.kwargs[0] == 59.3293
        assert fake.kwargs[1] == 18.0686
        assert fake.kwargs[2] is False
    finally:
        wmod.get_forecast = orig


def test_endpoint_rejects_invalid_lat():
    """lat out of [-90, 90] returns 400."""
    from fastapi.testclient import TestClient
    from web.backend import main as backend_main
    client = TestClient(backend_main.app)
    resp = client.get("/api/weather?lat=100&lon=14")
    assert resp.status_code == 400
    assert "lat" in resp.json()["detail"]


def test_endpoint_rejects_invalid_lon():
    """lon out of [-180, 180] returns 400."""
    from fastapi.testclient import TestClient
    from web.backend import main as backend_main
    client = TestClient(backend_main.app)
    resp = client.get("/api/weather?lat=57&lon=200")
    assert resp.status_code == 400
    assert "lon" in resp.json()["detail"]


def test_endpoint_handles_failure_gracefully():
    """If data layer raises, endpoint returns fallback dict (not 500)."""
    from fastapi.testclient import TestClient
    from web.backend import main as backend_main

    def _raise(lat, lon, use_cache=True):
        raise ConnectionError("SMHI down")

    import data.weather_smhi as wmod
    orig = wmod.get_forecast
    wmod.get_forecast = _raise
    try:
        client = TestClient(backend_main.app)
        resp = client.get("/api/weather")
        assert resp.status_code == 200
        data = resp.json()
        assert data["source"] == "fallback"
        assert data["summary"] == "unknown"
        assert "error" in data
    finally:
        wmod.get_forecast = orig
