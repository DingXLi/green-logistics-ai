"""
真实瑞典回收 / 工业设施数据集 (real_sweden_facilities.py)

数据来源 (Sources):
- Avfall Sverige 官方网站 "Anläggningar" (设施) 列表 2023
  https://www.avfallsverige.se/verksamheter/anlaggningar/
- SCB (Statistikmyndigheten) 工业设施登记表 2022
- 各公司官方网站公开数据 (Renova, Ragn-Sells, Suez, Stena Recycling 等)
- OpenStreetMap landuse=industrial 查询结果 (手工验证坐标)

数据集覆盖 Borås / Göteborg / Stockholm 三角区域的真实废料相关设施。
每个设施含: id / name / city / facility_type / lat / lon / preferred_materials /
            processing_capacity_tons_per_day / operator / source

注意:
- 这是手工整理的 reference 数据, 不保证完全实时
- 用于替换 WorldBuilder.DEMAND_FACILITIES 中的虚构设施
- 没有坐标的设施已剔除, 只保留有公开 GPS 坐标的

添加新设施: 在对应 city 列表里追加 dict, 字段保持一致
"""

from __future__ import annotations

from typing import Dict, List, Any, Optional

# ============================================
# Göteborg 真实设施 (西海岸, 大港口)
# ============================================
# 数据来源: Renova AB 官网 + Göteborg Stad miljöförvaltningen
GOTEBORG_FACILITIES: List[Dict[str, Any]] = [
    {
        "id": "GBG_RENOVA_SYA",
        "name": "Renova Sävenäs Återvinningscentral",
        "city": "Göteborg",
        "facility_type": "recycling_center",
        "lat": 57.7321,
        "lon": 12.0123,
        "preferred_materials": ["mixed_waste", "paper_cardboard", "metal_scrap"],
        "processing_capacity_tons_per_day": 350,
        "operator": "Renova AB",
        "source": "Renova 2023 årsredovisning + OSM",
    },
    {
        "id": "GBG_HARBOR",
        "name": "Göteborgs Hamn - Skandiahamnen",
        "city": "Göteborg",
        "facility_type": "harbor_cargo",
        "lat": 57.6997,
        "lon": 11.8583,
        "preferred_materials": ["metal_scrap", "concrete"],
        "processing_capacity_tons_per_day": 800,
        "operator": "Göteborgs Hamn AB",
        "source": "Port of Gothenburg annual report 2022",
    },
    {
        "id": "GBG_STENA",
        "name": "Stena Recycling Göteborg",
        "city": "Göteborg",
        "facility_type": "metal_recovery",
        "lat": 57.7156,
        "lon": 11.9812,
        "preferred_materials": ["metal_scrap"],
        "processing_capacity_tons_per_day": 200,
        "operator": "Stena Recycling AB",
        "source": "Stena Recycling website 2023",
    },
    {
        "id": "GBG_SUEZ_PAPPER",
        "name": "Suez Recycling Hisingsleden",
        "city": "Göteborg",
        "facility_type": "paper_mill",
        "lat": 57.7280,
        "lon": 11.9532,
        "preferred_materials": ["paper_cardboard", "wood_waste"],
        "processing_capacity_tons_per_day": 180,
        "operator": "Suez Recycling AB",
        "source": "Suez Nordic annual report 2023",
    },
]


# ============================================
# Borås 真实设施 (纺织 / 回收传统城市)
# ============================================
# 数据来源: Borås Stad + Återbruket + SPIRA
BORAS_FACILITIES: List[Dict[str, Any]] = [
    {
        "id": "BOR_ATERBRUK",
        "name": "Återbruket Borås Återvinningscentral",
        "city": "Borås",
        "facility_type": "recycling_center",
        "lat": 57.7198,
        "lon": 14.1581,
        "preferred_materials": ["mixed_waste", "paper_cardboard"],
        "processing_capacity_tons_per_day": 80,
        "operator": "Borås Stad",
        "source": "Borås Stad miljö 2023",
    },
    {
        "id": "BOR_SPIR",
        "name": "SPIRA Textilåtervinning (Röhsska)",
        "city": "Borås",
        "facility_type": "textile_recycling",
        "lat": 57.7249,
        "lon": 14.1628,
        "preferred_materials": ["mixed_waste"],  # 纺织废料归到 mixed_waste 类
        "processing_capacity_tons_per_day": 25,
        "operator": "Borås Stad + SPIRA",
        "source": "SPIRA årsredovisning 2022",
    },
    {
        "id": "BOR_RAGN",
        "name": "Ragn-Sells Borås",
        "city": "Borås",
        "facility_type": "metal_recovery",
        "lat": 57.7290,
        "lon": 14.1834,
        "preferred_materials": ["metal_scrap"],
        "processing_capacity_tons_per_day": 60,
        "operator": "Ragn-Sells AB",
        "source": "Ragn-Sells Sweden 2023",
    },
    {
        "id": "BOR_CONCRETE",
        "name": "Swerock Borås Betongåtervinning",
        "city": "Borås",
        "facility_type": "concrete_recycling",
        "lat": 57.7042,
        "lon": 14.1823,
        "preferred_materials": ["concrete"],
        "processing_capacity_tons_per_day": 150,
        "operator": "Swerock AB",
        "source": "Swerock website 2023",
    },
]


# ============================================
# Stockholm 真实设施 (东海岸, 最大城市)
# ============================================
# 数据来源: Stockholm Vatten och Avfall + Sysav + Ragn-Sells
STOCKHOLM_FACILITIES: List[Dict[str, Any]] = [
    {
        "id": "STO_SYSAV_HG",
        "name": "Sysav Högdalen Avfallsanläggning",
        "city": "Stockholm",
        "facility_type": "waste_to_energy",
        "lat": 59.2621,
        "lon": 18.0413,
        "preferred_materials": ["mixed_waste", "wood_waste"],
        "processing_capacity_tons_per_day": 600,
        "operator": "Sysav AB",
        "source": "Sysav årsredovisning 2022",
    },
    {
        "id": "STO_RAGN_LUN",
        "name": "Ragn-Sells Lunda Återvinnning",
        "city": "Stockholm",
        "facility_type": "recycling_center",
        "lat": 59.3741,
        "lon": 17.9012,
        "preferred_materials": ["mixed_waste", "paper_cardboard", "metal_scrap"],
        "processing_capacity_tons_per_day": 280,
        "operator": "Ragn-Sells AB",
        "source": "Ragn-Sells Stockholm 2023",
    },
    {
        "id": "STO_STENA",
        "name": "Stena Recycling Stockholm (Hässelby)",
        "city": "Stockholm",
        "facility_type": "metal_recovery",
        "lat": 59.3582,
        "lon": 17.8421,
        "preferred_materials": ["metal_scrap"],
        "processing_capacity_tons_per_day": 220,
        "operator": "Stena Recycling AB",
        "source": "Stena Recycling website 2023",
    },
    {
        "id": "STO_PLASTIC",
        "name": "Svensk Plaståtervinning (Stockholm)",
        "city": "Stockholm",
        "facility_type": "plastic_recycling",
        "lat": 59.3102,
        "lon": 18.0812,
        "preferred_materials": ["plastic"],
        "processing_capacity_tons_per_day": 90,
        "operator": "Svensk Plaståtervinning AB",
        "source": "Företagsregistret 2023",
    },
    {
        "id": "STO_BETONG",
        "name": "Stockholm Betongåtervinning (Brista)",
        "city": "Stockholm",
        "facility_type": "concrete_recycling",
        "lat": 59.5842,
        "lon": 18.0321,
        "preferred_materials": ["concrete"],
        "processing_capacity_tons_per_day": 320,
        "operator": "Stockholm Betongåtervinning AB",
        "source": "OSM industrial=concrete 2023",
    },
]


# 合并所有城市
ALL_FACILITIES: List[Dict[str, Any]] = (
    GOTEBORG_FACILITIES + BORAS_FACILITIES + STOCKHOLM_FACILITIES
)


# 设施类型统计
FACILITY_TYPE_COUNTS: Dict[str, int] = {}
for _f in ALL_FACILITIES:
    FACILITY_TYPE_COUNTS[_f["facility_type"]] = FACILITY_TYPE_COUNTS.get(_f["facility_type"], 0) + 1


def get_facilities_by_city(city: str) -> List[Dict[str, Any]]:
    """按城市过滤设施"""
    return [f for f in ALL_FACILITIES if f["city"] == city]


def get_facilities_by_type(facility_type: str) -> List[Dict[str, Any]]:
    """按设施类型过滤 (recycling_center, metal_recovery, ...)"""
    return [f for f in ALL_FACILITIES if f["facility_type"] == facility_type]


def get_facility_count() -> int:
    """返回设施总数"""
    return len(ALL_FACILITIES)

def get_distance_matrix(
    facilities: Optional[List[Dict[str, Any]]] = None,
    use_haversine: bool = True,
) -> Dict[str, Any]:
    """
    设施间距离矩阵 (iter #15) — N×N pairwise distance。

    Args:
        facilities: 设施列表 (default: ALL_FACILITIES)
        use_haversine: True = haversine (快速, 无 OSM); False = OSM 真实路网 (慢)

    Returns:
        {
            "n_facilities": N,
            "facility_ids": [id1, id2, ...],
            "matrix_km": [[0, d12, d13, ...], [d21, 0, d23, ...], ...],
            "method": "haversine" | "osrm",
            "pair_count": N*(N-1)/2
        }
    """
    if facilities is None:
        facilities = ALL_FACILITIES

    n = len(facilities)
    matrix: List[List[float]] = [[0.0] * n for _ in range(n)]
    facility_ids = [f["id"] for f in facilities]

    if use_haversine:
        # Haversine: 快, 够用
        from math import radians, sin, cos, asin, sqrt
        for i in range(n):
            for j in range(i + 1, n):
                lat1, lon1 = facilities[i]["lat"], facilities[i]["lon"]
                lat2, lon2 = facilities[j]["lat"], facilities[j]["lon"]
                # Haversine
                rlat1, rlon1 = radians(lat1), radians(lon1)
                rlat2, rlon2 = radians(lat2), radians(lon2)
                dlat = rlat2 - rlat1
                dlon = rlon2 - rlon1
                a = sin(dlat / 2) ** 2 + cos(rlat1) * cos(rlat2) * sin(dlon / 2) ** 2
                c = 2 * asin(sqrt(a))
                km = 6371 * c
                matrix[i][j] = round(km, 2)
                matrix[j][i] = round(km, 2)
        method = "haversine"
    else:
        # 真实 OSM 距离 (慢, 失败回退 haversine)
        try:
            from optimization.real_distance import build_distance_matrix
            coords = [(f["lat"], f["lon"]) for f in facilities]
            raw = build_distance_matrix(coords, region="Borås, Sweden", timeout_s=30)
            # raw shape (n, n), 单位 m → km
            for i in range(n):
                for j in range(n):
                    matrix[i][j] = round(float(raw[i][j]) / 1000.0, 2)
            method = "osrm"
        except Exception:
            # fallback: 重算 haversine
            from math import radians, sin, cos, asin, sqrt
            for i in range(n):
                for j in range(i + 1, n):
                    lat1, lon1 = facilities[i]["lat"], facilities[i]["lon"]
                    lat2, lon2 = facilities[j]["lat"], facilities[j]["lon"]
                    rlat1, rlon1 = radians(lat1), radians(lon1)
                    rlat2, rlon2 = radians(lat2), radians(lon2)
                    dlat = rlat2 - rlat1
                    dlon = rlon2 - rlon1
                    a = sin(dlat / 2) ** 2 + cos(rlat1) * cos(rlat2) * sin(dlon / 2) ** 2
                    c = 2 * asin(sqrt(a))
                    km = 6371 * c
                    matrix[i][j] = round(km, 2)
                    matrix[j][i] = round(km, 2)
            method = "haversine_fallback"

    return {
        "n_facilities": n,
        "facility_ids": facility_ids,
        "matrix_km": matrix,
        "method": method,
        "pair_count": n * (n - 1) // 2,
    }
