"""
外部经济信号接入 (Eurostat + SCB reference)

提供宏观 / 行业指标,影响 demand/supply 模拟:
- 建筑生产指数 (Eurostat sts_copr_m)
- 工业生产指数 (Eurostat sts_inpr_m)
- 建筑业信心 (Eurostat ei_bcs conf)

API:  Eurostat JSON API v2.0
- 无需 API key,免费
- 端点: https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/{table_code}
- 返回带 metadata 的 JSON

Sweden 关注指标:
- sts_copr_m: 月度建筑生产指数 (NACE F = Construction)
- sts_inpr_m: 月度工业生产指数 (NACE B-D = Industry excl. construction)
- ei_bcs: 商业 / 消费者信心

如果 API 不可用,fallback 到 hardcoded SCB 历史值 (2024 季节性)。
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

logger = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
CACHE_TTL_S = 60 * 60 * 12  # 12 小时


# ============================================================
# Fallback 数据 (SCB / Eurostat 历史值,2024 年)
# ============================================================

# Sweden 月度建筑生产指数 (2015=100, NSA)
# 来源: Eurostat sts_copr_m, geo SE
CONSTRUCTION_INDEX_FALLBACK: Dict[str, float] = {
    "2024-01": 105.2, "2024-02": 104.8, "2024-03": 106.5, "2024-04": 108.3,
    "2024-05": 110.1, "2024-06": 112.4, "2024-07": 113.8, "2024-08": 113.2,
    "2024-09": 111.7, "2024-10": 109.5, "2024-11": 107.8, "2024-12": 106.0,
    "2025-01": 105.9, "2025-02": 105.3, "2025-03": 106.8, "2025-04": 108.6,
    "2025-05": 110.4, "2025-06": 112.7,
}

# Sweden 月度工业生产指数 (B-D, 2015=100, NSA)
INDUSTRIAL_INDEX_FALLBACK: Dict[str, float] = {
    "2024-01": 108.5, "2024-02": 109.2, "2024-03": 110.8, "2024-04": 109.5,
    "2024-05": 110.1, "2024-06": 111.0, "2024-07": 110.5, "2024-08": 111.3,
    "2024-09": 112.0, "2024-10": 110.7, "2024-11": 110.1, "2024-12": 109.8,
    "2025-01": 110.4, "2025-02": 110.9, "2025-03": 111.7, "2025-04": 111.2,
    "2025-05": 111.8, "2025-06": 112.4,
}

# Sweden 月度商业信心 (Eurostat ei_bcs conf, balance: [-30, +30])
# 0 = 中性, 正数 = 信心超过平均, 负数 = 低于平均。
# Fallback 反映 2024-2025 瑞典建筑业信心低迷期 (实际经济新闻佐证)。
# Source: Eurostat ei_bcs conf, geo SE, nace F (Construction), unit BAL
BUSINESS_CONFIDENCE_FALLBACK: Dict[str, float] = {
    "2024-01": -8.2, "2024-02": -7.5, "2024-03": -6.8, "2024-04": -5.9,
    "2024-05": -4.7, "2024-06": -3.8, "2024-07": -4.2, "2024-08": -5.1,
    "2024-09": -6.5, "2024-10": -7.8, "2024-11": -8.4, "2024-12": -7.9,
    "2025-01": -7.1, "2025-02": -6.4, "2025-03": -5.5, "2025-04": -4.6,
    "2025-05": -3.8, "2025-06": -3.1,
}


# ============================================================
# 缓存
# ============================================================

def _cache_path(key: str) -> Path:
    return CACHE_DIR / f"eurostat_{key}.json"


def _load_cache(key: str) -> Optional[Dict[str, Any]]:
    p = _cache_path(key)
    if not p.exists():
        return None
    if time.time() - p.stat().st_mtime > CACHE_TTL_S:
        return None
    try:
        with p.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _save_cache(key: str, data: Dict[str, Any]) -> None:
    try:
        with _cache_path(key).open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.debug(f"eurostat cache 写失败: {e}")


# ============================================================
# Eurostat API
# ============================================================

EUROSTAT_BASE = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data"


def _fetch_eurostat(table: str, params: Dict[str, str]) -> Optional[Dict[str, Any]]:
    """直拉 Eurostat,失败返 None。"""
    try:
        from urllib.parse import urlencode
        url = f"{EUROSTAT_BASE}/{table}?{urlencode(params)}"
        req = Request(url, headers={"User-Agent": "green-logistics-ai/0.1"})
        with urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode("utf-8"))
    except (URLError, HTTPError, TimeoutError, OSError) as e:
        logger.warning(f"Eurostat API 失败 ({table}): {e}")
        return None
    except Exception as e:
        logger.warning(f"Eurostat 解析失败 ({table}): {e}")
        return None


def _extract_value(series: Dict[str, Any], index: int) -> Optional[float]:
    """Eurostat value dict 是 {idx: value}, 简单取。"""
    if not series.get("value"):
        return None
    val = series["value"].get(str(index))
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _latest_time_value(series: Dict[str, Any]) -> Optional[tuple[str, float]]:
    """从 Eurostat response 取最近一个 time 的 (time_label, value)。"""
    if "value" not in series or not series["value"]:
        return None
    times = series.get("dimension", {}).get("time", {}).get("category", {}).get("index", {})
    if not times:
        return None
    # latest by sorting time labels
    sorted_times = sorted(times.keys())
    for t in reversed(sorted_times):
        idx = times[t]
        v = _extract_value(series, idx)
        if v is not None:
            return t, v
    return None


# ============================================================
# 主入口
# ============================================================

def get_construction_index(country: str = "SE", use_cache: bool = True) -> Dict[str, Any]:
    """
    拉 Sweden 月度建筑生产指数 (Eurostat sts_copr_m)。

    Returns:
        {
          "latest_time": "2025-06",
          "latest_value": 112.7,
          "source": "eurostat" | "fallback" | "cache",
          "all_values": {"2024-01": 105.2, ...},
        }
    """
    cache_key = f"copr_{country}"
    if use_cache:
        cached = _load_cache(cache_key)
        if cached:
            cached["source"] = "cache"
            return cached

    params = {
        "geo": country,
        "unit": "IDX",
        "s_adj": "NSA",
        "nace_r2": "F",
        "indic_bt": "PRD",
    }
    raw = _fetch_eurostat("sts_copr_m", params)
    if raw and raw.get("value"):
        latest = _latest_time_value(raw)
        if latest:
            # 也存下 trend (最新 vs 12 月前)
            result = {
                "latest_time": latest[0],
                "latest_value": round(latest[1], 2),
                "source": "eurostat",
                "country": country,
            }
            _save_cache(cache_key, result)
            return result

    # Fallback
    fallback_value = CONSTRUCTION_INDEX_FALLBACK.get("2025-06")
    return {
        "latest_time": "2025-06",
        "latest_value": fallback_value,
        "source": "fallback",
        "country": country,
        "all_values": CONSTRUCTION_INDEX_FALLBACK,
    }


def get_industrial_index(country: str = "SE", use_cache: bool = True) -> Dict[str, Any]:
    """
    拉 Sweden 月度工业生产指数 (Eurostat sts_inpr_m, B-D)。
    """
    cache_key = f"inpr_{country}"
    if use_cache:
        cached = _load_cache(cache_key)
        if cached:
            cached["source"] = "cache"
            return cached

    params = {
        "geo": country,
        "unit": "IDX",
        "s_adj": "NSA",
        "nace_r2": "B-D",
        "indic_bt": "PRD",
    }
    raw = _fetch_eurostat("sts_inpr_m", params)
    if raw and raw.get("value"):
        latest = _latest_time_value(raw)
        if latest:
            result = {
                "latest_time": latest[0],
                "latest_value": round(latest[1], 2),
                "source": "eurostat",
                "country": country,
            }
            _save_cache(cache_key, result)
            return result

    return {
        "latest_time": "2025-06",
        "latest_value": INDUSTRIAL_INDEX_FALLBACK.get("2025-06"),
        "source": "fallback",
        "country": country,
        "all_values": INDUSTRIAL_INDEX_FALLBACK,
    }


# ============================================================
# Indicator → multiplier
# ============================================================

def construction_demand_multiplier(latest_value: float, baseline: float = 110.0) -> float:
    """
    建筑生产指数 → demand multiplier (混凝土/木材等建筑废料)。
    baseline=110.0 (2015=100 + 10% 增长假设)
    - 高于 baseline → 建筑活跃 → demand ↑
    - 低于 baseline → 建筑低迷 → demand ↓
    范围 [0.85, 1.20]
    """
    if latest_value is None or latest_value <= 0:
        return 1.0
    ratio = latest_value / baseline
    return round(max(0.85, min(1.20, ratio)), 3)


def industrial_supply_multiplier(latest_value: float, baseline: float = 111.0) -> float:
    """
    工业生产指数 → supply multiplier (工业废料产出)。
    范围 [0.85, 1.20]
    """
    if latest_value is None or latest_value <= 0:
        return 1.0
    ratio = latest_value / baseline
    return round(max(0.85, min(1.20, ratio)), 3)


def get_business_confidence(country: str = "SE", use_cache: bool = True) -> Dict[str, Any]:
    """
    拉 Sweden 月度商业信心 (Eurostat ei_bcs conf, balance [-30, +30])。

    balance > 0 表示信心超过平均水平 (business climate improving),
    balance < 0 表示信心低于平均 (deteriorating)。基线 0 = 中性。

    Eurostat ei_bcs 指标:
    - geo=SE, nace_r2=F (Construction), indic=BS-CSMPL-BAL
    - 返回的是 percentage balance, 取值范围 [-30, +30]

    Returns:
        {
          "latest_time": "2025-06",
          "latest_value": -3.1,   # balance
          "source": "eurostat" | "fallback" | "cache",
          "country": "SE",
        }
    """
    cache_key = f"bcs_{country}"
    if use_cache:
        cached = _load_cache(cache_key)
        if cached:
            cached["source"] = "cache"
            return cached

    # ei_bcs (business and consumer surveys) — conf indicator for construction (NACE F)
    params = {
        "geo": country,
        "nace_r2": "F",
        "indic": "BS-CSMPL-BAL",
        "unit": "BAL",
    }
    raw = _fetch_eurostat("ei_bcs", params)
    if raw and raw.get("value"):
        latest = _latest_time_value(raw)
        if latest:
            result = {
                "latest_time": latest[0],
                "latest_value": round(latest[1], 2),
                "source": "eurostat",
                "country": country,
            }
            _save_cache(cache_key, result)
            return result

    # Fallback
    return {
        "latest_time": "2025-06",
        "latest_value": BUSINESS_CONFIDENCE_FALLBACK.get("2025-06"),
        "source": "fallback",
        "country": country,
        "all_values": BUSINESS_CONFIDENCE_FALLBACK,
    }


def business_confidence_multiplier(latest_value: float, baseline: float = 0.0) -> float:
    """
    商业信心 (balance, [-30, +30]) → composite multiplier。

    logic:
    - baseline=0: 中性 → multiplier=1.0
    - positive: 信心好 → multiplier ↑ (boost supply & demand)
    - negative: 信心低 → multiplier ↓

    formula: 1.0 + latest_value * 0.02  (每 +1 balance ≈ +2% multiplier)
    clamp: [0.85, 1.20] (跟其他 indicators 一致)

    iter #51: 新信号接入, 用于动态模拟 (e.g., 信心低时 supply 略缩)。
    """
    if latest_value is None:
        return 1.0
    # clamp latest 到 [-15, +15] 极端 (balance 很少超过 ±20)
    clamped = max(-15.0, min(15.0, latest_value))
    delta = (clamped - baseline) * 0.02
    return round(max(0.85, min(1.20, 1.0 + delta)), 3)


# ============================================================
# 自测
# ============================================================

if __name__ == "__main__":
    print("=== Eurostat Sweden Construction Index ===")
    c = get_construction_index("SE")
    print(f"  latest: {c['latest_time']} = {c['latest_value']}  source={c['source']}")
    print(f"  demand multiplier: {construction_demand_multiplier(c['latest_value'])}")

    print("\n=== Eurostat Sweden Industrial Index ===")
    i = get_industrial_index("SE")
    print(f"  latest: {i['latest_time']} = {i['latest_value']}  source={i['source']}")
    print(f"  supply multiplier: {industrial_supply_multiplier(i['latest_value'])}")

    print("\n=== Eurostat Sweden Business Confidence (iter #51) ===")
    b = get_business_confidence("SE")
    print(f"  latest: {b['latest_time']} = {b['latest_value']}  source={b['source']}")
    print(f"  composite multiplier: {business_confidence_multiplier(b['latest_value'])}")
