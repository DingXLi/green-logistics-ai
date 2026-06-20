"""
世界构建器 (World Builder)

用 SyntheticDataGenerator 在瑞典 Borås / Göteborg / Stockholm 三角区域
生成 supply / demand / fleet 三类节点，作为 Coordinator 的初始化世界。

Design:
- Supply 在 3 个城市的 jittered cluster 内随机分布（建模仿真城市废料）
- Demand 在 3 个城市的工厂/回收厂坐标附近，3 个固定种子位置 + jitter
- Vehicle 全部在 Borås 仓库出发（depot 中心）
- 位置用 Borås 中心 + 半径 ~0.5° 随机化（≈ 50km 半径）
"""

import random
from typing import List, Dict, Any, Tuple
from dataclasses import dataclass

from synthetic.data_generator import (
    SyntheticDataGenerator,
    SupplyReading,
    DemandReading,
)


# 瑞典 3 个主要城市的废料/回收中心
CITY_CENTERS = {
    "Borås":      (57.7089, 14.1618),  # 西南，纺织品/废料回收
    "Göteborg":   (57.7089, 11.9746),  # 西海岸，大港口
    "Stockholm":  (59.3293, 18.0686),  # 东，最大城市
}

# 工厂 / 回收厂 / 破碎机（按需求点类型）
DEMAND_FACILITIES = [
    {"id": "DEM001", "name": "Borås Recycling Plant",     "city": "Borås",     "preferred_materials": ["mixed_waste", "metal_scrap"]},
    {"id": "DEM002", "name": "Göteborg Harbor Crusher",   "city": "Göteborg",  "preferred_materials": ["concrete", "wood_waste"]},
    {"id": "DEM003", "name": "Stockholm Processing",      "city": "Stockholm", "preferred_materials": ["mixed_waste", "plastic"]},
    {"id": "DEM004", "name": "Borås Metal Recovery",      "city": "Borås",     "preferred_materials": ["metal_scrap"]},
    {"id": "DEM005", "name": "Göteborg Paper Mill",       "city": "Göteborg",  "preferred_materials": ["paper_cardboard", "wood_waste"]},
    {"id": "DEM006", "name": "Stockholm Plastic Plant",   "city": "Stockholm", "preferred_materials": ["plastic"]},
    {"id": "DEM007", "name": "Borås Concrete Recycler",   "city": "Borås",     "preferred_materials": ["concrete"]},
    {"id": "DEM008", "name": "Göteborg Composite Plant",  "city": "Göteborg",  "preferred_materials": ["mixed_waste", "paper_cardboard"]},
    {"id": "DEM009", "name": "Stockholm Energy Recovery", "city": "Stockholm", "preferred_materials": ["mixed_waste", "wood_waste"]},
    {"id": "DEM010", "name": "Borås Textile Recycling",   "city": "Borås",     "preferred_materials": ["mixed_waste"]},
]


@dataclass
class WorldConfig:
    """世界配置"""
    n_supply_points: int = 20
    n_demand_points: int = 10
    n_vehicles: int = 30
    seed: int = 42
    # 供应点 jitter 半径（度，~0.3° ≈ 30km）
    supply_jitter: float = 0.3
    # 需求点 jitter 半径
    demand_jitter: float = 0.1
    # depot 位置（Borås 中心）
    depot_location: Tuple[float, float] = CITY_CENTERS["Borås"]


class WorldBuilder:
    """
    从 SyntheticDataGenerator 引导一个完整的仿真世界
    """

    def __init__(self, config: WorldConfig = None):
        self.config = config or WorldConfig()
        self.data_gen = SyntheticDataGenerator(seed=self.config.seed)
        # 显式重置内部 RNG，确保可复现
        random.seed(self.config.seed)

    def build_supply_points(self) -> List[Dict[str, Any]]:
        """
        构建供应点列表。每个 supply 有：
        - id, location {lat, lon}
        - material_type（按 data_generator 抽样）
        - current_stock（吨）
        - daily_capacity（吨/天）
        """
        supplies = []
        for i in range(self.config.n_supply_points):
            loc_id = f"SUP{i:03d}"
            city_name = random.choice(list(CITY_CENTERS.keys()))
            base_lat, base_lon = CITY_CENTERS[city_name]
            lat = base_lat + random.uniform(-self.config.supply_jitter, self.config.supply_jitter)
            lon = base_lon + random.uniform(-self.config.supply_jitter, self.config.supply_jitter)

            # 用 data_generator 的 generate_supply_reading 抽样 material + weight
            reading = self.data_gen.generate_supply_reading(loc_id)
            # Bug fix: cap current_stock / daily_capacity at 20t.
            # OR-Tools VRP has a per-vehicle capacity ceiling (~20t); a single supply
            # point with weight_tons > 13.3t would push current_stock > 20t and the
            # solver would return no_solution (routes=0). Clamp both for safety.
            stock = round(min(20.0, reading.weight_tons * 1.5), 2)        # 当前库存 ≤ 20t
            daily_cap = round(min(20.0, reading.weight_tons * 0.6), 2)    # 日产能 ≤ 20t

            supplies.append({
                "agent_id": loc_id,
                "location": {"lat": round(lat, 6), "lon": round(lon, 6)},
                "material_type": reading.material_type,
                "current_stock": stock,
                "daily_capacity": daily_cap,
                "moisture_percent": reading.moisture_percent,
                "quality_score": reading.quality_score,
                "city": city_name,
            })
        return supplies

    def build_demand_points(self) -> List[Dict[str, Any]]:
        """
        构建需求点列表。每个 demand 有：
        - id, name, location, preferred_materials
        - required_tons（当前需求）
        - priority, deadline
        """
        demands = []
        # 取前 n_demand_points 个工厂模板
        facility_templates = DEMAND_FACILITIES[: self.config.n_demand_points]

        for template in facility_templates:
            base_lat, base_lon = CITY_CENTERS[template["city"]]
            lat = base_lat + random.uniform(-self.config.demand_jitter, self.config.demand_jitter)
            lon = base_lon + random.uniform(-self.config.demand_jitter, self.config.demand_jitter)

            # 用 data_generator 抽 demand 字段
            reading = self.data_gen.generate_demand_reading(template["id"])
            # 选该 facility 的第一个 preferred material 作为主要 material
            primary_material = template["preferred_materials"][0]

            demands.append({
                "id": template["id"],
                "name": template["name"],
                "location": {"lat": round(lat, 6), "lon": round(lon, 6)},
                "preferred_materials": template["preferred_materials"],
                "material_type": primary_material,
                # base_demand_tons 是该 facility 的“理论日需求”上限，per-cycle 真实 demand_tons
                # 会由 Coordinator 在每周期用 weekday × noise × per-id jitter 扰动后写入
                # current_demand_tons。这样可以让 KPI 真正随时间变化。
                "base_demand_tons": reading.required_tons,
                "current_demand_tons": reading.required_tons,
                "daily_capacity_tons": round(reading.required_tons * 1.5, 2),
                "priority": reading.priority,
                "deadline": reading.deadline,
                "city": template["city"],
            })
        return demands

    def build_fleet(self) -> List[Dict[str, Any]]:
        """构建车辆（位置在 Borås depot）

        车辆异构性 (让 Pareto 路由有真实 trade-off):
        - 10 辆 type_A: 便宜但污染重 (cost=1.8 SEK/km, co2=1.2 kg/km)  - 柴油重型车
        - 15 辆 type_B: 平衡型         (cost=2.6 SEK/km, co2=0.85 kg/km) - 标准货车
        - 5 辆 type_C: 贵但环保         (cost=4.0 SEK/km, co2=0.4 kg/km)  - 电动车
        分布: cost_weight 高 -> 多用 A; co2_weight 高 -> 多用 C
        """
        # 按顺序分配 type 轮转
        types = (["A"] * 10) + (["B"] * 15) + (["C"] * 5)
        type_params = {
            "A": {"cost_per_km": 1.8, "co2_emission_rate": 1.2},
            "B": {"cost_per_km": 2.6, "co2_emission_rate": 0.85},
            "C": {"cost_per_km": 4.0, "co2_emission_rate": 0.4},
        }
        vehicles = []
        for i in range(self.config.n_vehicles):
            t = types[i % len(types)]
            p = type_params[t]
            vehicles.append({
                "vehicle_id": f"VEH{i:03d}",
                "vehicle_type": t,  # A/B/C
                "status": "available",
                "current_location": {
                    "lat": self.config.depot_location[0],
                    "lon": self.config.depot_location[1],
                },
                "current_load_tons": 0.0,
                "max_capacity_tons": 20.0,
                "fuel_level": 100.0,
                "cost_per_km": p["cost_per_km"],
                "co2_emission_rate": p["co2_emission_rate"],
                "total_distance_km": 0.0,
                "route_history": [],
            })
        return vehicles

    def build(self) -> Dict[str, Any]:
        """一次性构建整个世界"""
        return {
            "supplies": self.build_supply_points(),
            "demands": self.build_demand_points(),
            "fleet": self.build_fleet(),
            "config": self.config,
        }
