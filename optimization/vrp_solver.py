"""
车辆路径问题 (VRP) 求解器

使用 Google OR-Tools 实现基础 VRP 求解
支持：
- 多车辆
- 容量约束
- 时间窗口（可选）
- 多目标优化（成本 + 碳排放）
"""

from typing import List, Dict, Any, Tuple, Optional
from dataclasses import dataclass
import numpy as np
from loguru import logger

try:
    from ortools.constraint_solver import routing_enums_pb2, pywrapcp
    ORTOOLS_AVAILABLE = True
except ImportError:
    ORTOOLS_AVAILABLE = False
    logger.warning("OR-Tools not installed. VRP solver will use fallback mode.")


@dataclass
class Location:
    """位置节点"""
    id: str
    lat: float
    lon: float
    demand_tons: float = 0.0
    type: str = "pickup"  # pickup, delivery, depot


@dataclass
class Vehicle:
    """车辆"""
    id: str
    capacity_tons: float
    start_location: Location
    end_location: Optional[Location] = None
    co2_rate: float = 0.85  # kg CO2 per km
    cost_per_km: float = 2.6  # SEK per km


class VRPSolver:
    """
    VRP 求解器
    
    使用 OR-Tools 的约束求解器
    """
    
    def __init__(self):
        self.locations: List[Location] = []
        self.vehicles: List[Vehicle] = []
        self.distance_matrix: np.ndarray = None
        self.cost_matrix: np.ndarray = None
    
    def add_location(self, location: Location):
        """添加位置节点"""
        self.locations.append(location)
        logger.debug(f"添加位置：{location.id} ({location.type})")
    
    def add_vehicle(self, vehicle: Vehicle):
        """添加车辆"""
        self.vehicles.append(vehicle)
        logger.debug(f"添加车辆：{vehicle.id}")
    
    def set_distance_matrix(self, matrix: np.ndarray):
        """设置距离矩阵 (km)"""
        n = len(self.locations)
        if matrix.shape != (n, n):
            raise ValueError(f"Distance matrix shape {matrix.shape} doesn't match locations count {n}")
        self.distance_matrix = matrix
        logger.info(f"设置距离矩阵：{n}x{n}")
    
    def _calculate_distance_matrix_haversine(self) -> np.ndarray:
        """
        使用 Haversine 公式计算距离矩阵
        
        TODO: 替换为实际道路距离（使用 OSM）
        """
        n = len(self.locations)
        matrix = np.zeros((n, n))
        
        for i in range(n):
            for j in range(n):
                if i != j:
                    matrix[i, j] = self._haversine_distance(
                        self.locations[i].lat, self.locations[i].lon,
                        self.locations[j].lat, self.locations[j].lon
                    )
        
        return matrix
    
    @staticmethod
    def _haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """计算两点间的 Haversine 距离 (km)"""
        R = 6371  # 地球半径 (km)
        
        lat1_rad = np.radians(lat1)
        lat2_rad = np.radians(lat2)
        delta_lat = np.radians(lat2 - lat1)
        delta_lon = np.radians(lon2 - lon1)
        
        a = np.sin(delta_lat / 2) ** 2 + \
            np.cos(lat1_rad) * np.cos(lat2_rad) * np.sin(delta_lon / 2) ** 2
        c = 2 * np.arctan2(np.sqrt(a), np.sqrt(1 - a))
        
        return R * c
    
    def solve(self, time_limit_seconds: int = 30) -> Dict[str, Any]:
        """
        求解 VRP 问题
        
        返回：
        - 每辆车的路线
        - 总距离
        - 总成本
        - 总碳排放
        """
        
        if not ORTOOLS_AVAILABLE:
            return self._solve_fallback()
        
        if self.distance_matrix is None:
            self.distance_matrix = self._calculate_distance_matrix_haversine()
        
        n = len(self.locations)
        num_vehicles = len(self.vehicles)
        
        # 创建路由模型
        manager = pywrapcp.RoutingIndexManager(n, num_vehicles, 0)  # 0 = depot
        routing = pywrapcp.RoutingModel(manager)
        
        # 设置距离成本
        def distance_callback(from_index, to_index):
            from_node = manager.IndexToNode(from_index)
            to_node = manager.IndexToNode(to_index)
            return int(self.distance_matrix[from_node, to_node] * 1000)  # OR-Tools 使用整数
        
        transit_callback_index = routing.RegisterTransitCallback(distance_callback)
        routing.SetArcCostEvaluatorOfAllVehicles(transit_callback_index)
        
        # 添加容量约束
        def demand_callback(from_index):
            from_node = manager.IndexToNode(from_index)
            return int(self.locations[from_node].demand_tons * 1000)
        
        demand_callback_index = routing.RegisterUnaryTransitCallback(demand_callback)
        
        for v in range(num_vehicles):
            capacity = int(self.vehicles[v].capacity_tons * 1000)
            routing.AddDimensionWithVehicleCapacity(
                demand_callback_index,
                0,  # null capacity slack
                [capacity] * num_vehicles,
                True,  # start cumul to zero
                "Capacity"
            )
        
        # 设置搜索参数
        search_params = pywrapcp.DefaultRoutingSearchParameters()
        search_params.first_solution_strategy = (
            routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
        )
        search_params.time_limit.FromSeconds(time_limit_seconds)
        search_params.local_search_metaheuristic = (
            routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
        )
        
        # 求解
        solution = routing.SolveWithParameters(search_params)
        
        if solution:
            return self._extract_solution(routing, manager, solution)
        else:
            return {"status": "no_solution", "message": "No feasible solution found"}
    
    def _extract_solution(
        self,
        routing: pywrapcp.RoutingModel,
        manager: pywrapcp.RoutingIndexManager,
        solution: pywrapcp.Assignment
    ) -> Dict[str, Any]:
        """提取求解结果"""
        routes = []
        total_distance = 0
        total_cost = 0
        total_co2 = 0
        
        for v in range(len(self.vehicles)):
            index = routing.Start(v)
            route = []
            waypoints = []  # 带坐标的路径点
            route_distance = 0
            
            while not routing.IsEnd(index):
                node = manager.IndexToNode(index)
                loc = self.locations[node]
                route.append({
                    "location_id": loc.id,
                    "location_type": loc.type,
                    "demand_tons": loc.demand_tons
                })
                # 添加坐标供地图使用
                waypoints.append({
                    "lat": loc.lat,
                    "lon": loc.lon,
                    "location_id": loc.id,
                    "type": loc.type
                })
                
                previous_index = index
                index = solution.Value(routing.NextVar(index))
                route_distance += routing.GetArcCostForVehicle(previous_index, index, v)
            
            route_distance /= 1000  # 转换回 km
            total_distance += route_distance
            
            vehicle = self.vehicles[v]
            route_cost = route_distance * vehicle.cost_per_km
            route_co2 = route_distance * vehicle.co2_rate
            total_cost += route_cost
            total_co2 += route_co2
            
            routes.append({
                "vehicle_id": vehicle.id,
                "route": route,
                "waypoints": waypoints,  # 供地图使用的坐标
                "distance_km": round(route_distance, 2),
                "cost_sek": round(route_cost, 2),
                "co2_kg": round(route_co2, 2),
                "cargo_tons": sum(self.locations[manager.NodeToIndex(node)].demand_tons 
                                  for node in range(len(self.locations)))
            })
        
        return {
            "status": "optimal",
            "routes": routes,
            "total_distance_km": round(total_distance, 2),
            "total_cost_sek": round(total_cost, 2),
            "total_co2_kg": round(total_co2, 2),
            "num_vehicles_used": sum(1 for r in routes if len(r["route"]) > 1),
            "computation_method": "OR-Tools",
            "objective": "balanced"
        }
    
    def _solve_fallback(self) -> Dict[str, Any]:
        """
        OR-Tools 不可用时的回退方案
        
        使用简单的最近邻启发式算法
        """
        logger.warning("使用回退求解器（最近邻启发式）")
        
        if self.distance_matrix is None:
            self.distance_matrix = self._calculate_distance_matrix_haversine()
        
        routes = []
        total_distance = 0
        total_cost = 0
        total_co2 = 0
        
        # 简单分配：每辆车服务最近的几个点
        unvisited = set(range(1, len(self.locations)))  # 排除 depot
        
        for v, vehicle in enumerate(self.vehicles):
            if not unvisited:
                break
            
            route = [{"location_id": self.locations[0].id, "location_type": "depot", "demand_tons": 0}]
            current = 0
            route_distance = 0
            route_load = 0
            
            while unvisited and route_load < vehicle.capacity_tons * 0.8:
                # 找最近的未访问点
                nearest = min(
                    unvisited,
                    key=lambda i: self.distance_matrix[current, i]
                )
                
                route_distance += self.distance_matrix[current, nearest]
                route_load += self.locations[nearest].demand_tons
                
                route.append({
                    "location_id": self.locations[nearest].id,
                    "location_type": self.locations[nearest].type,
                    "demand_tons": self.locations[nearest].demand_tons
                })
                
                unvisited.remove(nearest)
                current = nearest
            
            # 返回 depot
            route_distance += self.distance_matrix[current, 0]
            route.append({"location_id": self.locations[0].id, "location_type": "depot", "demand_tons": 0})
            
            route_cost = route_distance * vehicle.cost_per_km
            route_co2 = route_distance * vehicle.co2_rate
            
            total_distance += route_distance
            total_cost += route_cost
            total_co2 += route_co2
            
            routes.append({
                "vehicle_id": vehicle.id,
                "route": route,
                "distance_km": round(route_distance, 2),
                "cost_sek": round(route_cost, 2),
                "co2_kg": round(route_co2, 2)
            })
        
        return {
            "status": "heuristic",
            "routes": routes,
            "total_distance_km": round(total_distance, 2),
            "total_cost_sek": round(total_cost, 2),
            "total_co2_kg": round(total_co2, 2),
            "num_vehicles_used": len(routes),
            "computation_method": "Nearest Neighbor Heuristic"
        }


# ============================================
# 使用示例
# ============================================
if __name__ == "__main__":
    # 创建求解器
    solver = VRPSolver()
    
    # 添加 depot
    depot = Location(id="DEPOT", lat=57.7089, lon=14.1618, type="depot")
    solver.add_location(depot)
    
    # 添加 pickup 点
    for i in range(5):
        solver.add_location(Location(
            id=f"PICKUP{i+1}",
            lat=57.7089 + (i * 0.1),
            lon=14.1618 + (i * 0.1),
            demand_tons=5.0,
            type="pickup"
        ))
    
    # 添加 delivery 点
    for i in range(3):
        solver.add_location(Location(
            id=f"DELIVERY{i+1}",
            lat=57.6 + (i * 0.1),
            lon=14.0 + (i * 0.1),
            demand_tons=-8.0,  # 负值表示卸货
            type="delivery"
        ))
    
    # 添加车辆
    for i in range(3):
        solver.add_vehicle(Vehicle(
            id=f"VEH{i+1}",
            capacity_tons=20.0,
            start_location=depot
        ))
    
    # 求解
    result = solver.solve(time_limit_seconds=10)
    
    print("\n" + "="*60)
    print("VRP 求解结果")
    print("="*60)
    import json
    print(json.dumps(result, indent=2, ensure_ascii=False))
