"""
SMHI 开放数据接入 (Swedish Meteorological and Hydrological Institute)

API: https://opendata.smhi.se/
- 实时数据无需 API key,免费
- 文档: https://opendata.smhi.se/apidocs/

主要端点:
- 9 天 hourly forecast by lat/lon:
  https://opendata.smhi.se/api/category/pmp3g/version/2/geotype/point/lon/{lon}/lat/{lat}/data.json

返回字段 (节选):
- t (temperature, °C)
- pmean (precipitation mean, mm/h)
- ws (wind speed, m/s)
- rh (relative humidity, %)

用法:
    from data.weather_smhi import get_forecast
    forecast = get_forecast(lat=57.7089, lon=14.1618)
    # forecast['current'] = {temperature_c, precipitation_mm_h, wind_m_s, ...}
    # forecast['summary'] = "cold & rainy" / "warm & dry" / ...
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

logger = logging.getLogger(__name__)


# ============================================================
# 缓存
# ============================================================

CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
CACHE_TTL_S = 60 * 30  # 30 分钟 (forecast 数据,半小时后过期)


def _cache_key(lat: float, lon: float) -> Path:
    safe_lat = f"{lat:.3f}".replace("-", "n")
    safe_lon = f"{lon:.3f}".replace("-", "n")
    return CACHE_DIR / f"smhi_{safe_lat}_{safe_lon}.json"


def _load_cache(key: Path) -> Optional[Dict[str, Any]]:
    if not key.exists():
        return None
    age = time.time() - key.stat().st_mtime
    if age > CACHE_TTL_S:
        return None
    try:
        with key.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _save_cache(key: Path, data: Dict[str, Any]) -> None:
    try:
        with key.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.debug(f"cache 写入失败 (忽略): {e}")


# ============================================================
# SMHI API
# ============================================================

SMHI_FORECAST_URL = (
    "https://opendata-download-metfcst.smhi.se/api/category/snow1g/version/1/"
    "geotype/point/lon/{lon}/lat/{lat}/data.json"
)
TIMEOUT_S = 10


def _http_get_json(url: str) -> Dict[str, Any]:
    req = Request(url, headers={"User-Agent": "green-logistics-ai/0.1"})
    with urlopen(req, timeout=TIMEOUT_S) as r:
        return json.loads(r.read().decode("utf-8"))


def _fetch_smhi_raw(lat: float, lon: float) -> Dict[str, Any]:
    """直拉 SMHI API,失败抛异常由 caller 兜底。"""
    url = SMHI_FORECAST_URL.format(lat=lat, lon=lon)
    return _http_get_json(url)


# ============================================================
# 解析
# ============================================================

def _parse_forecast(raw: Dict[str, Any]) -> Dict[str, Any]:
    """SMHI raw → {current, next_24h_avg, summary}。

    SMHI SNOW1gv1 格式: {"timeSeries": [{"time": "...Z", "data": {air_temperature: 10.4, ...}}]}
    """
    series: List[Dict[str, Any]] = raw.get("timeSeries", [])
    if not series:
        raise ValueError("SMHI response has no timeSeries")

    # 当前: 第一个 entry 的 data
    now_data = series[0].get("data", {})
    current = _normalize_data(now_data)

    # 未来 24 小时:取前 24 个 hourly entries
    next24 = series[:24]
    temps = []
    precs = []
    winds = []
    for entry in next24:
        d = _normalize_data(entry.get("data", {}))
        if "temperature_c" in d:
            temps.append(d["temperature_c"])
        if "precipitation_mm_h" in d:
            precs.append(d["precipitation_mm_h"])
        if "wind_m_s" in d:
            winds.append(d["wind_m_s"])
    next24_avg = {
        "temperature_c": round(sum(temps) / len(temps), 1) if temps else None,
        "precipitation_mm_h": round(sum(precs) / len(precs), 2) if precs else None,
        "wind_m_s": round(sum(winds) / len(winds), 1) if winds else None,
    }

    return {
        "current": current,
        "next_24h_avg": next24_avg,
        "n_hours": len(next24),
    }


def _normalize_data(data: Dict[str, Any]) -> Dict[str, Any]:
    """SNOW1gv1 field names → 内部名 (短名, 跨 API 通用)"""
    out: Dict[str, Any] = {}
    if "air_temperature" in data:
        out["temperature_c"] = float(data["air_temperature"])
    if "wind_speed" in data:
        out["wind_m_s"] = float(data["wind_speed"])
    if "wind_speed_of_gust" in data:
        out["wind_gust_m_s"] = float(data["wind_speed_of_gust"])
    if "precipitation_amount_mean" in data:
        # mm/h 平均
        out["precipitation_mm_h"] = float(data["precipitation_amount_mean"])
    if "relative_humidity" in data:
        out["humidity_pct"] = float(data["relative_humidity"])
    if "probability_of_precipitation" in data:
        out["precip_prob_pct"] = float(data["probability_of_precipitation"])
    if "symbol_code" in data:
        out["symbol_code"] = int(data["symbol_code"])
    return out


# ============================================================
# 总结 (用于 demand/supply multiplier)
# ============================================================

def _summarize(current: Dict[str, Any], next24: Dict[str, Any]) -> str:
    t = current.get("temperature_c")
    p = current.get("precipitation_mm_h")
    w = current.get("wind_m_s")
    bits = []
    if t is not None:
        if t < 0:
            bits.append("freezing")
        elif t < 10:
            bits.append("cold")
        elif t < 20:
            bits.append("mild")
        else:
            bits.append("warm")
    if p is not None and p > 0.1:
        if p > 1.0:
            bits.append("heavy rain")
        else:
            bits.append("light rain")
    if w is not None and w > 8:
        bits.append("windy")
    return " & ".join(bits) if bits else "no data"


def get_forecast(
    lat: float = 57.7089,
    lon: float = 14.1618,
    use_cache: bool = True,
) -> Dict[str, Any]:
    """
    拉 Borås (默认) 的 SMHI 9 天 hourly forecast,返回当前 + 24h 平均。

    Returns:
        {
          "current": {t, pmean, ws, ...},
          "next_24h_avg": {temperature_c, precipitation_mm_h, wind_m_s},
          "summary": "cold & light rain",
          "source": "smhi" | "fallback" | "cache",
          "timestamp": "2026-06-15T...",
        }

    失败 → {"source": "fallback", "summary": "unknown", ...}
    """
    cache_key = _cache_key(lat, lon)
    if use_cache:
        cached = _load_cache(cache_key)
        if cached:
            cached["source"] = cached.get("source", "cache") + "+cache" if "cache" not in cached.get("source", "") else cached["source"]
            # 简化: cache hit 直接返回,source 标 cache
            cached["source"] = "cache"
            return cached

    try:
        raw = _fetch_smhi_raw(lat, lon)
        parsed = _parse_forecast(raw)
        summary = _summarize(parsed["current"], parsed["next_24h_avg"])
        result = {
            **parsed,
            "summary": summary,
            "source": "smhi",
            "timestamp": time.time(),
            "lat": lat,
            "lon": lon,
        }
        _save_cache(cache_key, result)
        return result
    except (URLError, HTTPError, TimeoutError, OSError) as e:
        logger.warning(f"SMHI API 失败: {e} → fallback")
    except Exception as e:
        logger.warning(f"SMHI 解析失败: {e} → fallback")

    return {
        "current": {},
        "next_24h_avg": {"temperature_c": None, "precipitation_mm_h": None, "wind_m_s": None},
        "summary": "unknown (no weather data)",
        "source": "fallback",
        "timestamp": time.time(),
        "lat": lat,
        "lon": lon,
    }


# ============================================================
# Weather → multiplier
# ============================================================

def weather_demand_multiplier(forecast: Dict[str, Any]) -> float:
    """
    把 weather summary 转成 demand multiplier (~[0.7, 1.3]):
    - 极端冷 (freezing) → 1.20 (取暖废料多)
    - 冷 (cold) → 1.10
    - 温和 (mild) → 1.00
    - 暖 (warm) → 0.90
    - 雨 > 1mm/h → 0.85 (户外作业少)
    - 强风 > 8m/s → 0.95
    """
    summary = forecast.get("summary", "")
    cur = forecast.get("current", {})
    next24 = forecast.get("next_24h_avg", {})

    if forecast.get("source") == "fallback":
        return 1.0  # 无数据时不影响

    mult = 1.0
    t = next24.get("temperature_c")
    if t is not None:
        if t < 0:
            mult *= 1.20
        elif t < 10:
            mult *= 1.10
        elif t < 20:
            mult *= 1.00
        else:
            mult *= 0.90
    p = next24.get("precipitation_mm_h")
    if p is not None and p > 1.0:
        mult *= 0.85
    w = next24.get("wind_m_s")
    if w is not None and w > 8.0:
        mult *= 0.95

    return round(max(0.6, min(1.4, mult)), 3)


def weather_supply_multiplier(forecast: Dict[str, Any]) -> float:
    """
    Weather → supply multiplier (天气影响废料产生):
    - 冷 → 1.10 (更多加热废料)
    - 雨 > 1mm → 0.85 (户外施工少)
    - 风 > 8 → 0.90
    """
    if forecast.get("source") == "fallback":
        return 1.0
    next24 = forecast.get("next_24h_avg", {})
    mult = 1.0
    t = next24.get("temperature_c")
    if t is not None and t < 5:
        mult *= 1.10
    p = next24.get("precipitation_mm_h")
    if p is not None and p > 1.0:
        mult *= 0.85
    w = next24.get("wind_m_s")
    if w is not None and w > 8.0:
        mult *= 0.90
    return round(max(0.5, min(1.5, mult)), 3)


# ============================================================
# 自测
# ============================================================

if __name__ == "__main__":
    print("=== SMHI Borås forecast ===")
    f = get_forecast(57.7089, 14.1618)
    import json as _json
    print(_json.dumps(f, indent=2, ensure_ascii=False))
    print(f"\ndemand multiplier: {weather_demand_multiplier(f)}")
    print(f"supply multiplier: {weather_supply_multiplier(f)}")
