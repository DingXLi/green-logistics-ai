"""
季节性扰动 (Seasonal Adjuster)

应用 Swedish 真实废料月度季节因子到 supply/demand 仿真。
数据源: data/swedish_waste_stats.SEASONAL_FACTORS (Avfall Sverige 2023, 图 4.2)

公式:
    seasonal_factor = SEASONAL_FACTORS[material][month]
    adjusted_value = baseline × seasonal_factor

注意:
- month ∈ [1, 12] (1=Jan, 12=Dec)
- sim_day 从 0 起算,每 30 天一轮月 → month = (sim_day // 30) % 12 + 1
- 对未知 material 用 1.0 (中性, 不扰动)
"""

from __future__ import annotations

from typing import Dict, Optional

from data.swedish_waste_stats import SEASONAL_FACTORS


# 安全默认值: 1.0 = 无季节扰动
DEFAULT_FACTOR = 1.0

# 30 天仿真 = 12 个月 (压缩时间, 加速演示)
DAYS_PER_MONTH = 30


def sim_day_to_month(sim_day: int) -> int:
    """
    sim_day (0-indexed) → month (1-12)

    例:
        sim_day=0  → month=1 (Jan)
        sim_day=29 → month=1 (Jan)
        sim_day=30 → month=2 (Feb)
        sim_day=359 → month=12 (Dec)
        sim_day=360 → month=1 (Jan, next year)
    """
    return (sim_day // DAYS_PER_MONTH) % 12 + 1


def get_supply_multiplier(material: str, sim_day: int) -> float:
    """
    供应侧的月度季节因子。

    - 建筑废料 (concrete/wood) 夏季峰值 (5-9 月),冬季低谷 (12-2 月)
    - 金属废料较平稳 (0.95-1.10)
    - 混合废料冬季略高 (取暖季节)

    Args:
        material: "concrete", "metal_scrap", "wood_waste", "mixed_waste", ...
        sim_day: 0-indexed simulation day

    Returns:
        multiplier ∈ [0.3, 1.5] (typically)
    """
    return _lookup_factor(material, sim_day)


def get_demand_multiplier(material: str, sim_day: int) -> float:
    """
    需求侧的月度季节因子。

    跟 supply 用同一份 SEASONAL_FACTORS — 假设 supply/demand 同步。
    (如果未来发现两者解耦, 可以拆成 SEASONAL_FACTORS_SUPPLY / _DEMAND)
    """
    return _lookup_factor(material, sim_day)


def get_all_factors(month: int) -> Dict[str, float]:
    """
    返回某 month 所有 material 的季节因子 (调试/前端用)

    Args:
        month: 1-12

    Returns:
        {material: factor}
    """
    out: Dict[str, float] = {}
    for mat, factors in SEASONAL_FACTORS.items():
        if month in factors:
            out[mat] = factors[month]
    return out


def _lookup_factor(material: str, sim_day: int) -> float:
    if material not in SEASONAL_FACTORS:
        return DEFAULT_FACTOR
    month = sim_day_to_month(sim_day)
    return SEASONAL_FACTORS[material].get(month, DEFAULT_FACTOR)