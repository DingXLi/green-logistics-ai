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
    coordinator = MultiAgentCoordinator(
        config=world_config,
        db_path="data/simulation.db",
    )

    # 初始化数据生成器（IoT/fleet 端点用）
    data_generator = SyntheticDataGenerator(seed=42)

    logger.info(
        f"后端初始化完成：{len(coordinator.supply_agents)} supply / "
        f"{len(coordinator.market_agent.demand_points)} demand / "
        f"{len(coordinator.logistics_agent.vehicles)} vehicles"
    )


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
