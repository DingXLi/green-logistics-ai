"""
物流智能体 (Logistics Agent)

负责：
- 车辆路径规划 (VRP)
- 运输成本优化
- 碳排放计算
- 与优化引擎集成
"""

from google.adk import Agent
from google.adk import tools
from typing import List, Dict, Any, Optional
from datetime import datetime
import asyncio


class LogisticsAgent:
    """
    物流智能体 - 管理运输车辆和路径优化
    """
    
    def __init__(self, fleet_size: int = 10, depot_location: Dict[str, float] = None):
        self.fleet_size = fleet_size
        self.depot_location = depot_location or {"lat": 57.7089, "lon": 14.1618}  # 默认仓库位置
        
        # 车辆状态
        self.vehicles = self._initialize_vehicles()
        
        # 创建 ADK Agent
        self.agent = Agent(
            name="logistics_coordinator",
            model="gemini-2.0-flash",
            description="Logistics agent for vehicle routing and optimization",
            instruction=self._get_instruction(),
        )
    
    def _initialize_vehicles(self) -> List[Dict[str, Any]]:
        """初始化车队"""
        vehicles = []
        for i in range(self.fleet_size):
            vehicles.append({
                "vehicle_id": f"VEH{i:03d}",
                "status": "available",  # available, en_route, loading, unloading
                "current_location": self.depot_location.copy(),
                "current_load_tons": 0.0,
                "max_capacity_tons": 20.0,
                "fuel_level": 100.0,  # percentage
                "co2_emission_rate": 0.85,  # kg CO2 per km
                "total_distance_km": 0.0,
                "route_history": []
            })
        return vehicles
    
    def _get_instruction(self) -> str:
        return f"""
你是物流智能体，负责管理 {self.fleet_size} 辆运输车辆的路径规划和调度。

你的职责：
1. 接收收集请求并分配车辆
2. 优化车辆路径 (VRP) 以最小化成本和碳排放
3. 实时监控车辆状态和位置
4. 与供应智能体和市场智能体协调

仓库位置：{self.depot_location}
"""
    
    async def get_fleet_status(self) -> Dict[str, Any]:
        """获取车队状态"""
        available = sum(1 for v in self.vehicles if v["status"] == "available")
        en_route = sum(1 for v in self.vehicles if v["status"] == "en_route")
        
        return {
            "total_vehicles": self.fleet_size,
            "available": available,
            "en_route": en_route,
            "utilization_rate": (self.fleet_size - available) / self.fleet_size * 100,
            "timestamp": datetime.now().isoformat()
        }

    # ------------------------------------------------------------
    # 由 WorldBuilder / Coordinator 调用的状态注入接口
    # ------------------------------------------------------------

    def inject_fleet(self, vehicles: List[Dict[str, Any]]) -> None:
        """
        注入车辆（来自 WorldBuilder 的预生成车队）。
        覆盖 self.fleet_size 和 self.vehicles。
        """
        self.fleet_size = len(vehicles)
        self.vehicles = vehicles

    def reset_vehicles_for_new_cycle(self) -> None:
        """
        加速时钟下，每个 sim-day 开始时重置车辆状态：
        - 所有 en_route / loading / unloading → available
        - 保留 current_location（作为下一次出发位置）
        - 保留 total_distance_km 累计
        """
        for v in self.vehicles:
            if v["status"] in ("en_route", "loading", "unloading"):
                v["status"] = "available"
            # 重置负载（货物已卸）
            v["current_load_tons"] = 0.0
    
    async def get_vehicle_details(self, vehicle_id: str) -> Optional[Dict[str, Any]]:
        """获取特定车辆详情"""
        for vehicle in self.vehicles:
            if vehicle["vehicle_id"] == vehicle_id:
                return vehicle
        return None
    
    async def assign_route(
        self,
        vehicle_id: str,
        stops: List[Dict[str, Any]],
        total_distance_km: float,
        estimated_duration_hours: float
    ) -> Dict[str, Any]:
        """为车辆分配路线"""
        vehicle = await self.get_vehicle_details(vehicle_id)
        if not vehicle or vehicle["status"] != "available":
            return {"status": "error", "message": "Vehicle not available"}
        
        # 更新车辆状态
        vehicle["status"] = "en_route"
        vehicle["route_history"].append({
            "assigned_at": datetime.now().isoformat(),
            "stops": stops,
            "distance_km": total_distance_km,
            "duration_hours": estimated_duration_hours
        })
        
        return {
            "status": "assigned",
            "vehicle_id": vehicle_id,
            "stops_count": len(stops),
            "total_distance_km": total_distance_km,
            "estimated_duration_hours": estimated_duration_hours,
            "estimated_co2_kg": total_distance_km * vehicle["co2_emission_rate"]
        }
    
    async def calculate_route_cost(
        self,
        distance_km: float,
        load_tons: float,
        co2_price_per_kg: float = 0.5
    ) -> Dict[str, float]:
        """计算路线成本"""
        fuel_cost = distance_km * 1.5  # SEK per km
        driver_cost = distance_km * 0.8  # SEK per km
        co2_cost = distance_km * 0.85 * co2_price_per_kg  # CO2 成本
        maintenance_cost = distance_km * 0.3  # SEK per km
        
        total_cost = fuel_cost + driver_cost + co2_cost + maintenance_cost
        
        return {
            "fuel_cost_sek": round(fuel_cost, 2),
            "driver_cost_sek": round(driver_cost, 2),
            "co2_cost_sek": round(co2_cost, 2),
            "maintenance_cost_sek": round(maintenance_cost, 2),
            "total_cost_sek": round(total_cost, 2),
            "cost_per_ton_sek": round(total_cost / load_tons, 2) if load_tons > 0 else 0
        }
    
    async def optimize_routes(
        self,
        pickup_locations: List[Dict[str, Any]],
        delivery_locations: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        优化多车辆路径
        
        集成 OR-Tools VRP 求解器
        """
        from optimization.vrp_solver import VRPSolver, Location, Vehicle
        
        solver = VRPSolver()
        
        # 添加 depot 作为起点
        depot = Location(
            id="DEPOT",
            lat=self.depot_location["lat"],
            lon=self.depot_location["lon"],
            type="depot"
        )
        solver.add_location(depot)
        
        # 添加 pickup 点
        for i, loc in enumerate(pickup_locations):
            solver.add_location(Location(
                id=loc.get("id", f"PICKUP{i}"),
                lat=loc.get("lat", loc.get("location", {}).get("lat", 57.7)),
                lon=loc.get("lon", loc.get("location", {}).get("lon", 14.2)),
                demand_tons=loc.get("tons", 5.0),
                type="pickup"
            ))
        
        # 添加 delivery 点
        for i, loc in enumerate(delivery_locations):
            solver.add_location(Location(
                id=loc.get("id", f"DELIVERY{i}"),
                lat=loc.get("lat", loc.get("location", {}).get("lat", 57.7)),
                lon=loc.get("lon", loc.get("location", {}).get("lon", 14.2)),
                demand_tons=-loc.get("tons", 8.0),  # 负值表示卸货
                type="delivery"
            ))
        
        # 添加车辆（从可用车辆中分配）
        available_vehicles = [v for v in self.vehicles if v["status"] == "available"]
        for i, vehicle in enumerate(available_vehicles[:len(pickup_locations)]):
            solver.add_vehicle(Vehicle(
                id=vehicle["vehicle_id"],
                capacity_tons=vehicle["max_capacity_tons"],
                start_location=depot,
                co2_rate=vehicle["co2_emission_rate"],
                cost_per_km=2.6
            ))
        
        # 求解
        result = solver.solve(time_limit_seconds=10)
        
        # 更新车辆状态
        for route_info in result.get("routes", []):
            for vehicle in self.vehicles:
                if vehicle["vehicle_id"] == route_info["vehicle_id"]:
                    vehicle["status"] = "en_route"
                    break
        
        return result
    
    def get_tools(self) -> list:
        """返回智能体可用的工具"""
        return [
            self.get_fleet_status,
            self.get_vehicle_details,
            self.assign_route,
            self.calculate_route_cost,
            self.optimize_routes
        ]


# ============================================
# 使用示例
# ============================================
if __name__ == "__main__":
    async def main():
        # 创建物流智能体
        logistics = LogisticsAgent(fleet_size=5)
        
        # 获取车队状态
        status = await logistics.get_fleet_status()
        print(f"车队状态：{status}")
        
        # 获取车辆详情
        vehicle = await logistics.get_vehicle_details("VEH000")
        print(f"车辆详情：{vehicle}")
        
        # 计算路线成本
        cost = await logistics.calculate_route_cost(
            distance_km=150.0,
            load_tons=12.5
        )
        print(f"路线成本：{cost}")
    
    asyncio.run(main())
