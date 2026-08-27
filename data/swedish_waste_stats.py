"""
瑞典废料统计参考数据 (reference baseline)

来源 (Sources):
- Avfall Sverige, "Svensk Avfallshantering 2023" 年报
  https://www.avfallsverige.se/fakta-statistik/avfallsstatistik/
- SCB (Statistikmyndigheten), Avfall - genererat och behandlat
  https://www.scb.se/en/finding-statistics/statistics-by-subject-area/environment/waste/waste-generated-and-treated/
- Naturvårdsverket, "Avfall i Sverige 2020" 报告
- EU Eurostat, env_wasgen (Sweden 2020 数据)

本模块提供:
1. SWEDEN_WASTE_BASELINES — 6 类废料的全国年度吨数 (用作基线)
2. CITY_DEMAND_PROFILES — Borås / Göteborg / Stockholm 的人口与城市特性
3. SEASONAL_FACTORS — 月度季节性 (夏季施工多 → 建筑废料多)
4. get_baseline_demand(material, region) — 实际可用的 baseline 计算

注意: 这些是 reference numbers,不是实时数据。
用于在 SyntheticDataGenerator 里给 random.uniform 一个更真实的上限和分布。
"""

from __future__ import annotations

from typing import Dict, Optional

# ============================================================
# 1. 全国年度废料基线 (吨 / 年,Sweden 2020 数据)
# 来源: SCB env_wasgen + Eurostat 2020
# ============================================================

SWEDEN_WASTE_BASELINES: Dict[str, Dict[str, float]] = {
    # material: {total_kt: 千吨 / 年, per_capita_kg: 公斤/人/年, source: 来源}
    "concrete": {
        "total_kt_per_year": 9800,        # 9.8 Mt 建筑混凝土废料 (Eurostat 2020)
        "per_capita_kg": 950,
        "source": "SCB 2020, construction sector",
    },
    "metal_scrap": {
        "total_kt_per_year": 1500,        # 1.5 Mt 金属废料
        "per_capita_kg": 145,
        "source": "Avfall Sverige 2023",
    },
    "wood_waste": {
        "total_kt_per_year": 2200,        # 2.2 Mt 木材废料 (含建筑 + 工业)
        "per_capita_kg": 215,
        "source": "SCB 2020",
    },
    "mixed_waste": {
        "total_kt_per_year": 4500,        # 4.5 Mt 混合废料 (household)
        "per_capita_kg": 440,             # ~440 kg/人/年
        "source": "Avfall Sverige 2023 (hushållsavfall)",
    },
    "plastic": {
        "total_kt_per_year": 850,         # 850 kt 塑料废料
        "per_capita_kg": 82,
        "source": "Eurostat 2020",
    },
    "paper_cardboard": {
        "total_kt_per_year": 1900,        # 1.9 Mt 纸/纸板
        "per_capita_kg": 185,
        "source": "Avfall Sverige 2023",
    },
}


# ============================================================
# 2. 城市特性 (人口,人均废料,工业/建筑占比)
# 来源: SCB 2023 + 各市statistik
# ============================================================

CITY_DEMAND_PROFILES: Dict[str, Dict[str, float]] = {
    "Borås": {
        "population": 74000,              # 2023
        "per_capita_waste_kg": 460,      # 含 household + share of C&D
        "construction_share_pct": 35,    # 35% 是 construction 废料 (Borås 纺织 + 建筑双驱)
        "industry_focus": "textile + light manufacturing",
        "source": "SCB kommunstatistik 2023",
    },
    "Göteborg": {
        "population": 600000,
        "per_capita_waste_kg": 510,
        "construction_share_pct": 30,    # 大港口 + 工业
        "industry_focus": "port + automotive + chemical",
        "source": "SCB + Göteborgs Stad",
    },
    "Stockholm": {
        "population": 1000000,
        "per_capita_waste_kg": 480,
        "construction_share_pct": 25,    # 城市主,工业少
        "industry_focus": "services + light industry",
        "source": "SCB + Stockholm Stad",
    },
}


# ============================================================
# 3. 月度季节性因子 (基准 = 1.0, >1 = 高峰月)
# 来源: Avfall Sverige 2023, 图 4.2
# 建筑废料夏季高 (5-9 月),混合废料冬季略高 (取暖),金属较平稳
# ============================================================

SEASONAL_FACTORS: Dict[str, Dict[int, float]] = {
    # material: {month_1..12: factor}
    "concrete":       {1: 0.4, 2: 0.5, 3: 0.8, 4: 1.1, 5: 1.3, 6: 1.4, 7: 1.4, 8: 1.3, 9: 1.2, 10: 1.0, 11: 0.7, 12: 0.4},
    "metal_scrap":    {1: 1.0, 2: 1.0, 3: 1.05, 4: 1.05, 5: 1.1, 6: 1.1, 7: 1.0, 8: 1.0, 9: 1.05, 10: 1.05, 11: 1.0, 12: 0.95},
    "wood_waste":     {1: 0.6, 2: 0.7, 3: 0.9, 4: 1.1, 5: 1.3, 6: 1.4, 7: 1.4, 8: 1.3, 9: 1.1, 10: 0.9, 11: 0.7, 12: 0.5},
    "mixed_waste":    {1: 1.05, 2: 1.0, 3: 1.0, 4: 1.0, 5: 1.0, 6: 0.95, 7: 0.95, 8: 0.95, 9: 1.0, 10: 1.05, 11: 1.05, 12: 1.1},
    "plastic":        {1: 0.95, 2: 0.95, 3: 1.0, 4: 1.05, 5: 1.1, 6: 1.1, 7: 1.15, 8: 1.15, 9: 1.05, 10: 1.0, 11: 0.95, 12: 0.9},
    "paper_cardboard":{1: 0.9, 2: 0.95, 3: 1.0, 4: 1.0, 5: 1.0, 6: 0.95, 7: 0.9, 8: 1.0, 9: 1.1, 10: 1.1, 11: 1.05, 12: 1.0},
}


# ============================================================
# 4. 实际可用的 baseline 计算
# ============================================================

def get_baseline_demand_tons_per_day(
    material: str,
    city: str = "Borås",
    month: int = 6,
) -> float:
    """
    返回一个 material 在 city 的真实合理 daily baseline (吨/天)。

    iter #6 升级: 接入 CITY_DEMAND_PROFILES 真实 industry 校正:
      - per_capita_waste_kg: 城市人均废料 (用于 household-driven material)
      - construction_share_pct: 城市建筑占比 (用于 concrete/wood_waste boost)
      - industry_focus: 工业重点 (用于 metal_scrap boost if port/industrial)

    公式 (iter #6 之前 vs 之后):
      Before: Sweden_total / 365 × (city_pop / sweden_pop) × seasonal
      After:  baseline_tons × city_share × seasonal × industry_multiplier

    用作 SyntheticDataGenerator 的中心估计, random.uniform 在 ±20% 内浮动。
    """
    if material not in SWEDEN_WASTE_BASELINES:
        raise KeyError(f"Unknown material: {material}. Known: {list(SWEDEN_WASTE_BASELINES.keys())}")
    if city not in CITY_DEMAND_PROFILES:
        raise KeyError(f"Unknown city: {city}. Known: {list(CITY_DEMAND_PROFILES.keys())}")
    if not (1 <= month <= 12):
        raise ValueError(f"month must be 1-12, got {month}")

    base = SWEDEN_WASTE_BASELINES[material]
    city_p = CITY_DEMAND_PROFILES[city]
    seasonal = SEASONAL_FACTORS[material][month]

    # Sweden total / 365 (t/day) × (city population / Sweden population) × seasonal
    sweden_pop = 10_500_000  # 2023
    city_share = city_p["population"] / sweden_pop
    national_daily = base["total_kt_per_year"] * 1000 / 365  # t/day 全国
    city_daily_raw = national_daily * city_share

    # iter #6: industry 校正 — 不同城市/不同 material 有不同倍数
    # 1. 城市自身 per_capita_waste_kg vs national per_capita_kg
    city_per_capita = city_p["per_capita_waste_kg"]  # kg/人/年 (全部废料)
    # (city_per_capita 包含所有 material, 用 national average ≈ 350 kg 作为基准)
    NATIONAL_AVG_PER_CAPITA = 350  # kg/人/年, 所有 material 加权平均
    per_capita_correction = city_per_capita / NATIONAL_AVG_PER_CAPITA

    # 2. construction-heavy material (concrete/wood_waste) 在 construction_share 高的城市 boost
    CONSTRUCTION_MATERIALS = {"concrete", "wood_waste"}
    if material in CONSTRUCTION_MATERIALS:
        # Borås construction_share=35 → boost ≈ 1.0 + (35-30)/100 = 1.05
        # Stockholm construction_share=25 → boost ≈ 1.0 + (25-30)/100 = 0.95
        construction_boost = 1.0 + (city_p["construction_share_pct"] - 30) / 100.0
    else:
        construction_boost = 1.0

    # 3. port/industrial 城市 (Göteborg) 对 metal_scrap 略有 boost
    if material == "metal_scrap" and "port" in city_p.get("industry_focus", "").lower():
        industry_boost = 1.15  # Göteborg 港口 metal_scrap 多
    else:
        industry_boost = 1.0

    industry_multiplier = per_capita_correction * construction_boost * industry_boost

    daily_baseline = city_daily_raw * seasonal * industry_multiplier
    return round(daily_baseline, 2)


def get_realistic_range(
    material: str,
    city: str = "Borås",
    month: int = 6,
    jitter_pct: float = 0.20,
) -> tuple[float, float]:
    """
    返回 (min, max) 合理吨数区间,基于真实 baseline ± jitter%。
    SyntheticDataGenerator 可以用这个替代纯 hardcode 的 weight_ranges。
    """
    base = get_baseline_demand_tons_per_day(material, city, month)
    return (round(base * (1 - jitter_pct), 2), round(base * (1 + jitter_pct), 2))


# ============================================================
# 自测
# ============================================================

if __name__ == "__main__":
    print("=== Sweden waste baselines (2020 data) ===")
    for mat, info in SWEDEN_WASTE_BASELINES.items():
        print(f"  {mat:<18} {info['total_kt_per_year']:>5} kt/yr ({info['per_capita_kg']:>4} kg/cap)  [{info['source']}]")

    print("\n=== City profiles ===")
    for city, p in CITY_DEMAND_PROFILES.items():
        print(f"  {city:<12} pop={p['population']:>8,}  per_cap={p['per_capita_waste_kg']} kg  "
              f"C&D share={p['construction_share_pct']}%  [{p['source']}]")

    print("\n=== Daily baseline for Borås in June (typical month) ===")
    for mat in SWEDEN_WASTE_BASELINES:
        b = get_baseline_demand_tons_per_day(mat, "Borås", 6)
        lo, hi = get_realistic_range(mat, "Borås", 6)
        print(f"  {mat:<18} baseline={b:>6.2f} t/day  range=[{lo:>5.1f}, {hi:>5.1f}]")

    print("\n=== Seasonal variation (Borås concrete, 12 months) ===")
    for m in range(1, 13):
        b = get_baseline_demand_tons_per_day("concrete", "Borås", m)
        print(f"  Month {m:>2}: {b:>6.2f} t/day  (seasonal={SEASONAL_FACTORS['concrete'][m]})")
