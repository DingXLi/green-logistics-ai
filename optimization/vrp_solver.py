"""
车辆路径问题 (VRP) 求解器

使用 Google OR-Tools 实现基础 VRP 求解
支持：
- 多车辆
- 容量约束
- 时间窗口（可选）
- 多目标优化（成本 + 碳排放）
- Pareto 前沿扫描 (solve_pareto)
"""

from typing import List, Dict, Any, Tuple, Optional
from dataclasses import dataclass
import copy
import numpy as np
from loguru import logger

# 碳价参考 (SEK / kg CO2) — Swedish/EU ETS ~ 1.5 SEK/kg
DEFAULT_CO2_PRICE_SEK_PER_KG = 1.5

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
    
    def solve(
        self,
        time_limit_seconds: int = 30,
        cost_weight: float = 0.5,
        co2_weight: float = 0.5,
        co2_price: float = DEFAULT_CO2_PRICE_SEK_PER_KG,
    ) -> Dict[str, Any]:
        """
        求解 VRP 问题

        多目标：成本 + 碳排放
        - cost_weight: 成本权重 (SEK/km)
        - co2_weight:  碳排放权重 (kg CO2/km × co2_price → SEK/km)
        - co2_price:   碳价 (SEK/kg CO2)

        OR-Tools 优化目标是这两个目标的加权和；最终按实际距离
        单独计算 cost_sek / co2_kg / total_objective。

        返回：
        - 每辆车的路线
        - 总距离
        - 总成本 (SEK)
        - 总碳排放 (kg)
        - 总目标值 (加权 SEK)
        """

        if not ORTOOLS_AVAILABLE:
            return self._solve_fallback(
                cost_weight=cost_weight,
                co2_weight=co2_weight,
                co2_price=co2_price,
            )

        if self.distance_matrix is None:
            self.distance_matrix = self._calculate_distance_matrix_haversine()

        n = len(self.locations)
        num_vehicles = len(self.vehicles)

        # 创建路由模型
        manager = pywrapcp.RoutingIndexManager(n, num_vehicles, 0)  # 0 = depot
        routing = pywrapcp.RoutingModel(manager)

        # 设置加权成本回调
        # - 如果所有 vehicle 的 cost_per_km / co2_rate 一致，使用 SetArcCostEvaluatorOfAllVehicles
        # - 否则为每辆车单独注册回调，支持异构车队产生真正的 Pareto 权衡
        if num_vehicles > 0:
            first_w = (
                self.vehicles[0].cost_per_km * cost_weight
                + self.vehicles[0].co2_rate * co2_price * co2_weight
            )
            homogeneous = all(
                abs(
                    (v.cost_per_km * cost_weight + v.co2_rate * co2_price * co2_weight)
                    - first_w
                ) < 1e-9
                for v in self.vehicles
            )
        else:
            homogeneous = True
            first_w = 0.0

        def make_callback(weighted_cost_per_km):
            def _cb(from_index, to_index):
                from_node = manager.IndexToNode(from_index)
                to_node = manager.IndexToNode(to_index)
                # distance_km × weighted_cost_per_km × 10000 (整数精度)
                return int(
                    self.distance_matrix[from_node, to_node]
                    * weighted_cost_per_km
                    * 10000
                )
            return _cb

        if homogeneous and num_vehicles > 0:
            cb_idx = routing.RegisterTransitCallback(make_callback(first_w))
            routing.SetArcCostEvaluatorOfAllVehicles(cb_idx)
        else:
            # 异构车队：per-vehicle 回调
            for v_idx, vehicle in enumerate(self.vehicles):
                w_v = (
                    vehicle.cost_per_km * cost_weight
                    + vehicle.co2_rate * co2_price * co2_weight
                )
                cb_idx = routing.RegisterTransitCallback(make_callback(w_v))
                routing.SetArcCostEvaluatorOfVehicle(cb_idx, v_idx)

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
            return self._extract_solution(
                routing, manager, solution,
                cost_weight=cost_weight,
                co2_weight=co2_weight,
                co2_price=co2_price,
            )
        # OR-Tools 在 time_limit 內沒找到解（如小量、节点数太多）。退回最近邻启发式，
        # 这样 KPI 不会全为 0，数据还可用。
        logger.warning("OR-Tools 未找到可行解（time_limit 或 infeasible），退到最近邻 fallback")
        return self._solve_fallback(
            cost_weight=cost_weight,
            co2_weight=co2_weight,
            co2_price=co2_price,
        ) | {"status": "fallback_nearest_neighbor"}

    def _snapshot(self) -> "VRPSolver":
        """深拷贝当前 solver 状态（用于 pareto 扫描时复制）"""
        new_solver = VRPSolver.__new__(VRPSolver)
        new_solver.locations = copy.deepcopy(self.locations)
        new_solver.vehicles = copy.deepcopy(self.vehicles)
        new_solver.distance_matrix = (
            copy.deepcopy(self.distance_matrix)
            if self.distance_matrix is not None
            else None
        )
        new_solver.cost_matrix = (
            copy.deepcopy(self.cost_matrix)
            if self.cost_matrix is not None
            else None
        )
        return new_solver

    def solve_pareto(
        self,
        n_points: int = 10,
        time_limit_seconds: int = 10,
        co2_price: float = DEFAULT_CO2_PRICE_SEK_PER_KG,
    ) -> List[Dict[str, Any]]:
        """
        扫描 cost_weight 从 1.0 到 0.0，返回 Pareto 前沿点列表。

        每个点：{cost_weight, co2_weight, cost_sek, co2_kg,
                total_objective, total_distance_km, routes, status}
        """
        pareto: List[Dict[str, Any]] = []
        # linspace: 从全成本 (1,0) 到全碳排 (0,1)
        for i in range(n_points):
            w = 1.0 - i / max(n_points - 1, 1)
            cost_weight = float(w)
            co2_weight = float(1.0 - w)

            # 复制 solver 状态独立求解
            snap = self._snapshot()
            result = snap.solve(
                time_limit_seconds=time_limit_seconds,
                cost_weight=cost_weight,
                co2_weight=co2_weight,
                co2_price=co2_price,
            )

            if result.get("status") in ("optimal", "heuristic"):
                pareto.append({
                    "cost_weight": cost_weight,
                    "co2_weight": co2_weight,
                    "cost_sek": result["total_cost_sek"],
                    "co2_kg": result["total_co2_kg"],
                    "total_objective": result["total_objective"],
                    "total_distance_km": result["total_distance_km"],
                    "routes": result["routes"],
                    "status": result["status"],
                })
            else:
                logger.warning(
                    f"pareto point {i+1}/{n_points} (cost_w={cost_weight:.2f}) "
                    f"无解：{result.get('status')}"
                )
                pareto.append({
                    "cost_weight": cost_weight,
                    "co2_weight": co2_weight,
                    "cost_sek": None,
                    "co2_kg": None,
                    "total_objective": None,
                    "total_distance_km": None,
                    "routes": [],
                    "status": result.get("status", "unknown"),
                })

        logger.info(f"Pareto 前沿计算完成：{len(pareto)} 个点")
        return pareto
    
    def _extract_solution(
        self,
        routing: pywrapcp.RoutingModel,
        manager: pywrapcp.RoutingIndexManager,
        solution: pywrapcp.Assignment,
        cost_weight: float = 0.5,
        co2_weight: float = 0.5,
        co2_price: float = DEFAULT_CO2_PRICE_SEK_PER_KG,
    ) -> Dict[str, Any]:
        """提取求解结果（按实际 distance_matrix 计算 cost / co2 / objective）"""
        routes = []
        total_distance = 0.0
        total_cost = 0.0
        total_co2 = 0.0
        total_objective = 0.0

        for v in range(len(self.vehicles)):
            index = routing.Start(v)
            route = []
            waypoints = []  # 带坐标的路径点
            node_sequence: List[int] = []  # 节点 ID 序列，用于按 distance_matrix 算距离

            while not routing.IsEnd(index):
                node = manager.IndexToNode(index)
                loc = self.locations[node]
                route.append({
                    "location_id": loc.id,
                    "location_type": loc.type,
                    "demand_tons": loc.demand_tons
                })
                waypoints.append({
                    "lat": loc.lat,
                    "lon": loc.lon,
                    "location_id": loc.id,
                    "type": loc.type
                })
                node_sequence.append(node)
                previous_index = index
                index = solution.Value(routing.NextVar(index))

            # 终点 (depot) 加入序列
            end_node = manager.IndexToNode(index)
            node_sequence.append(end_node)
            route.append({
                "location_id": self.locations[end_node].id,
                "location_type": self.locations[end_node].type,
                "demand_tons": self.locations[end_node].demand_tons
            })
            waypoints.append({
                "lat": self.locations[end_node].lat,
                "lon": self.locations[end_node].lon,
                "location_id": self.locations[end_node].id,
                "type": self.locations[end_node].type
            })

            # 用 distance_matrix 累加真实距离（与 OR-Tools 优化目标无关）
            route_distance = 0.0
            for k in range(len(node_sequence) - 1):
                route_distance += self.distance_matrix[node_sequence[k], node_sequence[k + 1]]

            total_distance += route_distance

            vehicle = self.vehicles[v]
            route_cost = route_distance * vehicle.cost_per_km
            route_co2 = route_distance * vehicle.co2_rate
            # 加权目标（SEK 等价值）
            route_objective = (
                route_cost * cost_weight
                + route_co2 * co2_price * co2_weight
            )
            total_cost += route_cost
            total_co2 += route_co2
            total_objective += route_objective

            routes.append({
                "vehicle_id": vehicle.id,
                "route": route,
                "waypoints": waypoints,  # 供地图使用的坐标
                "distance_km": round(route_distance, 2),
                "cost_sek": round(route_cost, 2),
                "co2_kg": round(route_co2, 2),
                "objective_sek": round(route_objective, 2),
                "cargo_tons": sum(self.locations[manager.NodeToIndex(node)].demand_tons
                                  for node in range(len(self.locations)))
            })

        return {
            "status": "optimal",
            "routes": routes,
            "total_distance_km": round(total_distance, 2),
            "total_cost_sek": round(total_cost, 2),
            "total_co2_kg": round(total_co2, 2),
            "total_objective": round(total_objective, 2),
            "num_vehicles_used": sum(1 for r in routes if len(r["route"]) > 1),
            "computation_method": "OR-Tools",
            "objective": "weighted_cost_co2",
            "cost_weight": cost_weight,
            "co2_weight": co2_weight,
            "co2_price_sek_per_kg": co2_price,
        }
    
    def _solve_fallback(
        self,
        cost_weight: float = 0.5,
        co2_weight: float = 0.5,
        co2_price: float = DEFAULT_CO2_PRICE_SEK_PER_KG,
    ) -> Dict[str, Any]:
        """
        OR-Tools 不可用时的回退方案

        使用简单的最近邻启发式算法（按加权 cost 选最近点）
        """
        logger.warning("使用回退求解器（最近邻启发式）")

        if self.distance_matrix is None:
            self.distance_matrix = self._calculate_distance_matrix_haversine()

        routes = []
        total_distance = 0.0
        total_cost = 0.0
        total_co2 = 0.0
        total_objective = 0.0

        # 简单分配：每辆车服务最近的几个点
        unvisited = set(range(1, len(self.locations)))  # 排除 depot

        for v, vehicle in enumerate(self.vehicles):
            if not unvisited:
                break

            route = [{"location_id": self.locations[0].id, "location_type": "depot", "demand_tons": 0}]
            current = 0
            route_distance = 0.0
            route_load = 0.0

            while unvisited and route_load < vehicle.capacity_tons * 0.8:
                # 找最近的未访问点（按加权 cost 排序）
                nearest = min(
                    unvisited,
                    key=lambda i: (
                        self.distance_matrix[current, i]
                        * (vehicle.cost_per_km * cost_weight
                           + vehicle.co2_rate * co2_price * co2_weight)
                    )
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
            route_objective = route_cost * cost_weight + route_co2 * co2_price * co2_weight

            total_distance += route_distance
            total_cost += route_cost
            total_co2 += route_co2
            total_objective += route_objective

            routes.append({
                "vehicle_id": vehicle.id,
                "route": route,
                "distance_km": round(route_distance, 2),
                "cost_sek": round(route_cost, 2),
                "co2_kg": round(route_co2, 2),
                "objective_sek": round(route_objective, 2),
            })

        return {
            "status": "heuristic",
            "routes": routes,
            "total_distance_km": round(total_distance, 2),
            "total_cost_sek": round(total_cost, 2),
            "total_co2_kg": round(total_co2, 2),
            "total_objective": round(total_objective, 2),
            "num_vehicles_used": len(routes),
            "computation_method": "Nearest Neighbor Heuristic",
            "objective": "weighted_cost_co2",
            "cost_weight": cost_weight,
            "co2_weight": co2_weight,
            "co2_price_sek_per_kg": co2_price,
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
