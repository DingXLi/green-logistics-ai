"""
Green Logistics AI - Web Backend

FastAPI 应用提供 REST API
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
from datetime import datetime
from loguru import logger
import sys
import os
import random

# 添加父目录到路径以便导入
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from agents.coordinator import MultiAgentCoordinator
from agents.world_builder import WorldConfig
from optimization.vrp_solver import VRPSolver, Location, Vehicle
from synthetic.data_generator import SyntheticDataGenerator

# ============================================
# FastAPI 应用
# ============================================
app = FastAPI(
    title="Green Logistics AI",
    description="多智能体 AI 系统 - 绿色物流优化",
    version="0.1.0"
)

# CORS 配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 生产环境应该限制
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 全局状态
coordinator: Optional[MultiAgentCoordinator] = None
data_generator: Optional[SyntheticDataGenerator] = None


# ============================================
# 启动事件
# ============================================
@app.on_event("startup")
async def startup_event():
    """应用启动时初始化"""
    global coordinator, data_generator

    logger.info("初始化 Green Logistics AI 后端 (V2)...")

    # 初始化协调器（V2：自动从 WorldBuilder 引导 20/10/30 世界）
    world_config = WorldConfig(
        n_supply_points=20,
        n_demand_points=10,
        n_vehicles=30,
        seed=42,
    )
    # db_path 走环境变量 (HF Spaces 部署时设为 /data/simulation.db 用持久化卷)
    db_path = os.environ.get("GL_DB_PATH", "data/simulation.db")
    coordinator = MultiAgentCoordinator(
        config=world_config,
        db_path=db_path,
    )

    # 初始化数据生成器（IoT/fleet 端点用）
    data_generator = SyntheticDataGenerator(seed=42)

    logger.info(
        f"后端初始化完成：{len(coordinator.supply_agents)} supply / "
        f"{len(coordinator.market_agent.demand_points)} demand / "
        f"{len(coordinator.logistics_agent.vehicles)} vehicles"
    )

    # 预热: 首次启动 DB 还没有 cycle 数据, 跑 1 个 cycle
    # 让 Lovable 前端立刻能看到真实 KPI, 而不是 0 cycles
    # 后续启动 (DB 已有数据) 跳过, 保持快速重启
    try:
        n_cycles = (coordinator.persistence.get_summary() or {}).get("n_cycles") or 0
        if n_cycles == 0:
            logger.info("DB 空, 预热 1 个优化 cycle (预计 5-15s)...")
            result = await coordinator.run_optimization_cycle()
            matches = result.get("matches", {}) or {}
            opt_id = (result.get("optimization_id") or "?")[:8]
            logger.info(
                f"预热完成: {matches.get('total_matches', 0)} matches, "
                f"cycle_id={opt_id}"
            )
        else:
            logger.info(f"DB 已有 {n_cycles} cycles, 跳过预热")
    except Exception as e:
        # 预热失败不能阻止服务启动
        logger.warning(f"启动预热失败 (服务继续运行): {e}")


# ============================================
# 数据模型
# ============================================
class OptimizationRequest(BaseModel):
    """优化请求"""
    run_simulation: bool = False
    simulation_days: int = 1


class OptimizationResponse(BaseModel):
    """优化响应"""
    status: str
    optimization_id: Optional[str] = None
    timestamp: str
    matches_count: int
    total_tons: float
    total_cost_sek: float
    total_co2_kg: float


class FleetStatusResponse(BaseModel):
    """车队状态响应"""
    total_vehicles: int
    available: int
    en_route: int
    utilization_rate: float


class SupplyPoint(BaseModel):
    """供应点"""
    agent_id: str
    stock_tons: float
    material_type: str
    location: Dict[str, float]


# ============================================
# API 端点
# ============================================

@app.get("/")
async def root():
    """根路径"""
    return {
        "name": "Green Logistics AI",
        "version": "0.1.0",
        "status": "running"
    }


@app.get("/health")
async def health_check():
    """健康检查"""
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat()
    }


@app.get("/api/status", response_model=Dict[str, Any])
async def get_system_status():
    """获取系统状态"""
    if coordinator is None:
        raise HTTPException(status_code=503, detail="System not initialized")
    
    overview = await coordinator.get_system_overview()
    return overview


@app.get("/api/fleet", response_model=FleetStatusResponse)
async def get_fleet_status():
    """获取车队状态"""
    if coordinator is None:
        raise HTTPException(status_code=503, detail="System not initialized")
    
    status = await coordinator.logistics_agent.get_fleet_status()
    return FleetStatusResponse(**status)


@app.get("/api/supply-points", response_model=List[SupplyPoint])
async def get_supply_points():
    """获取所有供应点"""
    if coordinator is None:
        raise HTTPException(status_code=503, detail="System not initialized")
    
    points = []
    for agent_id, agent in coordinator.supply_agents.items():
        stock = await agent.get_current_stock()
        points.append(SupplyPoint(**stock))
    
    return points


@app.post("/api/optimize", response_model=OptimizationResponse)
async def run_optimization(request: OptimizationRequest = None):
    """运行优化"""
    if coordinator is None:
        raise HTTPException(status_code=503, detail="System not initialized")

    if request and request.run_simulation:
        # 运行模拟
        results = await coordinator.simulate_day(days=request.simulation_days)
        last_result = results[-1]
    else:
        # 单次优化
        last_result = await coordinator.run_optimization_cycle()

    # 提取关键指标
    matches = last_result.get("matches", {})
    routes = last_result.get("route_optimization", {})

    return OptimizationResponse(
        status="success",
        optimization_id=last_result.get("optimization_id"),
        timestamp=last_result.get("timestamp"),
        matches_count=matches.get("total_matches", 0),
        total_tons=matches.get("total_tons", 0),
        total_cost_sek=routes.get("total_cost_sek", 0),
        total_co2_kg=routes.get("total_co2_kg", 0)
    )


# ============================================
# V3: Pareto 前沿端点
# ============================================
@app.get("/api/optimize/pareto")
async def get_pareto_front(n_points: int = 10, time_limit_seconds: int = 5):
    """
    返回多目标 (cost vs CO2) Pareto 前沿

    - n_points: 扫描权重点数 (2..20)
    - time_limit_seconds: 每个点的 OR-Tools 时限

    用 coordinator 当前世界的 supply/demand/vehicle 状态构建 VRPSolver。
    """
    if coordinator is None:
        raise HTTPException(status_code=503, detail="System not initialized")
    if n_points < 2 or n_points > 20:
        raise HTTPException(
            status_code=400,
            detail="n_points must be in [2, 20]",
        )

    from optimization.vrp_solver import VRPSolver, Location, Vehicle

    # 收集当前 supply / demand 状态
    supply_offers = []
    for agent_id, agent in coordinator.supply_agents.items():
        stock = await agent.get_current_stock()
        if stock["stock_tons"] > 0.5:
            supply_offers.append({
                "agent_id": agent_id,
                "available_tons": stock["stock_tons"],
                "material_type": stock["material_type"],
                "location": stock["location"],
            })

    demand_requests = []
    for dp in coordinator.market_agent.demand_points:
        demand_requests.append({
            "id": dp["id"],
            "name": dp["name"],
            "demand_tons": dp["current_demand_tons"],
            "preferred_materials": dp["preferred_materials"],
            "location": dp["location"],
            "material_type": dp.get("material_type"),
        })

    # 重新匹配
    matches_result = await coordinator.market_agent.match_supply_demand(
        supply_offers=supply_offers,
        demand_requests=demand_requests,
    )
    matches = matches_result.get("matches", [])

    if not matches:
        raise HTTPException(
            status_code=404,
            detail="No matches available to build VRP problem",
        )

    # 取前 15 个匹配，避免 OR-Tools 超时
    matches = matches[:15]

    # 收集 depot + pickup + delivery
    depot_loc = coordinator.logistics_agent.depot_location
    depot = Location(id="DEPOT", lat=depot_loc["lat"], lon=depot_loc["lon"], type="depot")

    supply_idx = {a.agent_id: a for a in coordinator.supply_agents.values()}
    demand_idx = {d["id"]: d for d in coordinator.market_agent.demand_points}

    pickup_locations = []
    delivery_locations = []
    for m in matches:
        sid = m.get("supply_id")
        did = m.get("demand_id")
        if sid not in supply_idx or did not in demand_idx:
            continue
        sup = supply_idx[sid]
        dem = demand_idx[did]
        pickup_locations.append({
            "id": sid, "lat": sup.location["lat"], "lon": sup.location["lon"],
            "tons": m.get("tons", 5.0),
        })
        delivery_locations.append({
            "id": did, "lat": dem["location"]["lat"], "lon": dem["location"]["lon"],
            "tons": m.get("tons", 5.0),
        })

    if not pickup_locations:
        raise HTTPException(
            status_code=404,
            detail="No usable supply/demand locations",
        )

    # 配车辆（不超过 pickup 数）
    vehicles_data = [
        v for v in coordinator.logistics_agent.vehicles
        if v.get("status") == "available"
    ][:len(pickup_locations)]
    if not vehicles_data:
        raise HTTPException(status_code=404, detail="No vehicles available")

    # 构建 solver 并扫描 Pareto
    solver = VRPSolver()
    solver.add_location(depot)
    for loc in pickup_locations:
        solver.add_location(Location(
            id=loc["id"], lat=loc["lat"], lon=loc["lon"],
            demand_tons=loc["tons"], type="pickup",
        ))
    for loc in delivery_locations:
        solver.add_location(Location(
            id=loc["id"], lat=loc["lat"], lon=loc["lon"],
            demand_tons=-loc["tons"], type="delivery",
        ))
    for vd in vehicles_data:
        solver.add_vehicle(Vehicle(
            id=vd["vehicle_id"],
            capacity_tons=vd.get("max_capacity_tons", 20.0),
            start_location=depot,
            co2_rate=vd.get("co2_emission_rate", 0.85),
            cost_per_km=2.6,
        ))

    pareto = solver.solve_pareto(
        n_points=n_points, time_limit_seconds=time_limit_seconds,
    )

    # 序列化：去掉完整 routes（保留数量）
    summary = []
    for p in pareto:
        summary.append({
            "cost_weight": p["cost_weight"],
            "co2_weight": p["co2_weight"],
            "cost_sek": p["cost_sek"],
            "co2_kg": p["co2_kg"],
            "total_objective": p["total_objective"],
            "total_distance_km": p["total_distance_km"],
            "n_routes": len(p["routes"]),
            "status": p["status"],
        })

    return {
        "n_points": len(summary),
        "n_pickups": len(pickup_locations),
        "n_deliveries": len(delivery_locations),
        "n_vehicles": len(vehicles_data),
        "pareto": summary,
    }


@app.get("/api/iot-telemetry/{vehicle_id}")
async def get_iot_telemetry(vehicle_id: str, hours: int = 4):
    """获取车辆 IoT 遥测数据"""
    if data_generator is None:
        raise HTTPException(status_code=503, detail="Data generator not initialized")
    
    telemetry = data_generator.generate_iot_telemetry(
        vehicle_id=vehicle_id,
        duration_hours=hours,
        interval_minutes=5
    )
    
    return {
        "vehicle_id": vehicle_id,
        "duration_hours": hours,
        "data_points": len(telemetry),
        "telemetry": telemetry
    }


@app.get("/api/fleet-snapshot")
async def get_fleet_snapshot():
    """获取车队实时快照"""
    if data_generator is None:
        raise HTTPException(status_code=503, detail="Data generator not initialized")
    
    vehicle_ids = [f"VEH{i:03d}" for i in range(10)]
    snapshot = data_generator.generate_fleet_snapshot(vehicle_ids)
    
    # 转换为前端期望的格式
    vehicles = []
    for v in snapshot:
        vehicles.append({
            "vehicle_id": v["vehicle_id"],
            "status": v["status"],
            "latitude": v["location"]["lat"],
            "longitude": v["location"]["lon"],
            "battery_level": v["fuel_level"],
            "cargo_load": v["current_load_tons"],
            "speed": 0 if v["status"] == "available" else random.uniform(30, 70),
            "carbon_emission_rate": 0.85,
            "heading": random.uniform(0, 360),
            "last_update": v["last_update"]
        })
    
    return {
        "timestamp": datetime.now().isoformat(),
        "vehicles": vehicles
    }


@app.get("/api/sample-data/supply/{location_id}")
async def get_sample_supply_data(location_id: str, days: int = 1):
    """获取示例供应数据"""
    if data_generator is None:
        raise HTTPException(status_code=503, detail="Data generator not initialized")
    
    from datetime import timedelta
    all_data = []
    
    for day in range(days):
        date = datetime.now() + timedelta(days=day)
        data = data_generator.generate_daily_supply(
            location_id=location_id,
            date=date,
            intervals_per_day=24
        )
        all_data.extend(data)
    
    return {
        "location_id": location_id,
        "days": days,
        "data_points": len(all_data),
        "data": all_data
    }


# ============================================
# V2 新增：持久化 KPI 查询端点
# ============================================

@app.get("/api/persistence/recent-cycles")
async def get_recent_cycles(limit: int = 10):
    """获取最近 N 个优化周期的 KPI（从 SQLite 读）"""
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    return coordinator.persistence.get_recent_cycles(limit=limit)


@app.get("/api/persistence/kpi-timeseries")
async def get_kpi_timeseries():
    """KPI 时间序列（按 sim_day 聚合）"""
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    return coordinator.persistence.get_kpi_timeseries()


@app.get("/api/persistence/summary")
async def get_persistence_summary():
    """全局统计汇总"""
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    return coordinator.persistence.get_summary()


# ============================================
# 主程序
# ============================================
if __name__ == "__main__":
    import uvicorn
    
    logger.info("启动 Green Logistics AI 后端服务器...")
    uvicorn.run(app, host="0.0.0.0", port=8000)
