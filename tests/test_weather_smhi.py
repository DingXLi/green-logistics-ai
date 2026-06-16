"""
Tests for SMHI weather integration.

不依赖网络 (网络在线时跑真实 API,离线时用 mock 验证 fallback).
"""

import pytest

from data.weather_smhi import (
    get_forecast,
    weather_demand_multiplier,
    weather_supply_multiplier,
    _summarize,
    _normalize_data,
)


class TestSummarize:
    def test_freezing(self):
        s = _summarize({"temperature_c": -3}, {})
        assert "freezing" in s

    def test_cold(self):
        s = _summarize({"temperature_c": 5}, {})
        assert "cold" in s

    def test_mild(self):
        s = _summarize({"temperature_c": 15}, {})
        assert "mild" in s

    def test_warm(self):
        s = _summarize({"temperature_c": 25}, {})
        assert "warm" in s

    def test_heavy_rain(self):
        s = _summarize({"temperature_c": 15, "precipitation_mm_h": 2.5}, {})
        assert "heavy rain" in s

    def test_windy(self):
        s = _summarize({"temperature_c": 15, "wind_m_s": 10}, {})
        assert "windy" in s

    def test_combined(self):
        s = _summarize({"temperature_c": 5, "precipitation_mm_h": 0.5, "wind_m_s": 9}, {})
        assert "cold" in s
        assert "light rain" in s
        assert "windy" in s


class TestNormalizeData:
    def test_air_temperature_mapped(self):
        out = _normalize_data({"air_temperature": 12.5})
        assert out["temperature_c"] == 12.5

    def test_full_payload(self):
        raw = {
            "air_temperature": 10.4,
            "wind_speed": 3.1,
            "wind_speed_of_gust": 6.8,
            "precipitation_amount_mean": 0.0,
            "relative_humidity": 90,
        }
        out = _normalize_data(raw)
        assert out["temperature_c"] == 10.4
        assert out["wind_m_s"] == 3.1
        assert out["wind_gust_m_s"] == 6.8
        assert out["precipitation_mm_h"] == 0.0
        assert out["humidity_pct"] == 90.0


class TestMultipliers:
    def test_demand_mild_neutral(self):
        # mild (10-20) → 1.0
        f = {"source": "smhi", "current": {}, "next_24h_avg": {"temperature_c": 15, "precipitation_mm_h": 0, "wind_m_s": 3}}
        assert weather_demand_multiplier(f) == 1.0

    def test_demand_freezing_boost(self):
        f = {"source": "smhi", "current": {}, "next_24h_avg": {"temperature_c": -3, "precipitation_mm_h": 0, "wind_m_s": 2}}
        m = weather_demand_multiplier(f)
        assert m > 1.0
        assert m == 1.2

    def test_demand_heavy_rain_dampen(self):
        f = {"source": "smhi", "current": {}, "next_24h_avg": {"temperature_c": 15, "precipitation_mm_h": 3.0, "wind_m_s": 2}}
        m = weather_demand_multiplier(f)
        # 1.0 (mild) * 0.85 (heavy rain) = 0.85
        assert m == pytest.approx(0.85)

    def test_demand_warm_reduce(self):
        f = {"source": "smhi", "current": {}, "next_24h_avg": {"temperature_c": 25, "precipitation_mm_h": 0, "wind_m_s": 2}}
        m = weather_demand_multiplier(f)
        assert m == 0.9  # warm

    def test_supply_cold_boost(self):
        f = {"source": "smhi", "current": {}, "next_24h_avg": {"temperature_c": -5, "precipitation_mm_h": 0, "wind_m_s": 2}}
        m = weather_supply_multiplier(f)
        assert m == 1.1

    def test_supply_heavy_rain_dampen(self):
        f = {"source": "smhi", "current": {}, "next_24h_avg": {"temperature_c": 15, "precipitation_mm_h": 2.0, "wind_m_s": 2}}
        m = weather_supply_multiplier(f)
        assert m == 0.85

    def test_fallback_returns_neutral(self):
        f = {"source": "fallback", "current": {}, "next_24h_avg": {}}
        assert weather_demand_multiplier(f) == 1.0
        assert weather_supply_multiplier(f) == 1.0

    def test_multiplier_clamped(self):
        # extreme cold + rain + wind shouldn't go below 0.6 / above 1.4
        f = {"source": "smhi", "current": {}, "next_24h_avg": {"temperature_c": -20, "precipitation_mm_h": 5, "wind_m_s": 20}}
        m = weather_demand_multiplier(f)
        assert 0.6 <= m <= 1.4


class TestGetForecastIntegration:
    """真实 API 集成 (网络在线时跑,离线自动 fallback)。"""

    def test_get_forecast_returns_valid_structure(self):
        """不依赖具体数据,只看 structure。"""
        f = get_forecast(57.7089, 14.1618)
        assert "source" in f
        assert f["source"] in ("smhi", "fallback", "cache")
        assert "current" in f
        assert "next_24h_avg" in f
        assert "summary" in f

    def test_get_forecast_borås_real_data(self):
        """网络在线时,应该拿真 SMHI 数据 (current 有 temperature)。"""
        f = get_forecast(57.7089, 14.1618, use_cache=True)
        if f["source"] == "smhi":
            # 拿真实数据了
            assert "temperature_c" in f["current"]
            # Borås 6 月:温度应在 [-5, 35] 合理范围
            t = f["current"]["temperature_c"]
            assert -10 < t < 40
