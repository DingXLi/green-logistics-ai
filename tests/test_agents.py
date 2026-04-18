"""
多智能体协调器测试
"""
import pytest
import asyncio
import sys
import os

# 添加父目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.coordinator import MultiAgentCoordinator
from agents.supply_agent import SupplyAgent
from agents.market_agent import MarketAgent
from agents.logistics_agent import LogisticsAgent


class TestSupplyAgent:
    """供应智能体测试"""

    @pytest.mark.asyncio
    async def test_get_current_stock(self):
        """测试获取当前库存"""
        agent = SupplyAgent("TEST001", {"lat": 57.7089, "lon": 14.1618})
        agent.current_stock = 10.0
        
        stock = await agent.get_current_stock()
        
        assert stock["agent_id"] == "TEST001"
        assert stock["stock_tons"] == 10.0
        assert "location" in stock

    @pytest.mark.asyncio
    async def test_predict_supply(self):
        """测试供应预测"""
        agent = SupplyAgent("TEST001", {"lat": 57.7089, "lon": 14.1618})
        agent.daily_capacity = 20.0
        
        prediction = await agent.predict_supply(days=7)
        
        assert prediction["prediction_days"] == 7
        assert "total_tons" in prediction
        assert "confidence" in prediction

    @pytest.mark.asyncio
    async def test_request_collection(self):
        """测试收集请求"""
        agent = SupplyAgent("TEST001", {"lat": 57.7089, "lon": 14.1618})
        agent.current_stock = 15.0
        
        # 测试库存充足
        result = await agent.request_collection(min_load_tons=10.0)
        assert result["status"] == "ready"
        
        # 测试库存不足
        result = await agent.request_collection(min_load_tons=20.0)
        assert result["status"] == "insufficient"


class TestMarketAgent:
    """市场智能体测试"""

    @pytest.mark.asyncio
    async def test_get_demand_status(self):
        """测试获取需求状态"""
        agent = MarketAgent()
        
        status = await agent.get_demand_status()
        
        assert len(status) > 0
        assert "id" in status[0]
        assert "current_demand_tons" in status[0]

    @pytest.mark.asyncio
    async def test_get_material_price(self):
        """测试获取材料价格"""
        agent = MarketAgent()
        
        price = await agent.get_material_price("mixed_waste")
        
        assert price["material_type"] == "mixed_waste"
        assert price["price_sek_per_ton"] > 0

    @pytest.mark.asyncio
    async def test_calculate_profit(self):
        """测试利润计算"""
        agent = MarketAgent()
        
        profit = await agent.calculate_profit(
            material_type="mixed_waste",
            tons=10.0,
            transport_cost_sek=500,
            co2_cost_sek=100
        )
        
        assert "revenue_sek" in profit
        assert "profit_sek" in profit
        assert profit["revenue_sek"] > profit["total_cost_sek"]


class TestLogisticsAgent:
    """物流智能体测试"""

    @pytest.mark.asyncio
    async def test_get_fleet_status(self):
        """测试获取车队状态"""
        agent = LogisticsAgent(fleet_size=5)
        
        status = await agent.get_fleet_status()
        
        assert status["total_vehicles"] == 5
        assert status["available"] == 5
        assert "utilization_rate" in status

    @pytest.mark.asyncio
    async def test_get_vehicle_details(self):
        """测试获取车辆详情"""
        agent = LogisticsAgent(fleet_size=5)
        
        vehicle = await agent.get_vehicle_details("VEH000")
        
        assert vehicle is not None
        assert vehicle["vehicle_id"] == "VEH000"
        assert "capacity_tons" in vehicle

    @pytest.mark.asyncio
    async def test_calculate_route_cost(self):
        """测试路线成本计算"""
        agent = LogisticsAgent()
        
        cost = await agent.calculate_route_cost(
            distance_km=100.0,
            load_tons=10.0
        )
        
        assert "total_cost_sek" in cost
        assert cost["total_cost_sek"] > 0


class TestMultiAgentCoordinator:
    """多智能体协调器测试"""

    @pytest.mark.asyncio
    async def test_initialization(self):
        """测试协调器初始化"""
        coordinator = MultiAgentCoordinator()
        
        assert coordinator.system_status["status"] == "running"
        assert coordinator.market_agent is not None
        assert coordinator.logistics_agent is not None

    @pytest.mark.asyncio
    async def test_register_supply_agent(self):
        """测试注册供应智能体"""
        coordinator = MultiAgentCoordinator()
        
        coordinator.register_supply_agent("TEST001", {"lat": 57.7089, "lon": 14.1618})
        
        assert "TEST001" in coordinator.supply_agents

    @pytest.mark.asyncio
    async def test_get_system_overview(self):
        """测试获取系统概览"""
        coordinator = MultiAgentCoordinator()
        
        # 注册测试供应点
        coordinator.register_supply_agent("TEST001", {"lat": 57.7089, "lon": 14.1618})
        for agent in coordinator.supply_agents.values():
            agent.current_stock = 10.0
        
        overview = await coordinator.get_system_overview()
        
        assert "system_status" in overview
        assert "supply_points" in overview
        assert "fleet_status" in overview


class TestVRPSolver:
    """VRP 求解器测试"""

    def test_haversine_distance(self):
        """测试 Haversine 距离计算"""
        from optimization.vrp_solver import VRPSolver
        
        # 斯德哥尔摩到哥德堡的近似距离
        distance = VRPSolver._haversine_distance(
            59.3293, 18.0686,  # 斯德哥尔摩
            57.7089, 11.9746   # 哥德堡
        )
        
        # 实际距离约 400km，允许误差
        assert 350 < distance < 450

    def test_fallback_solver(self):
        """测试回退求解器"""
        from optimization.vrp_solver import VRPSolver, Location, Vehicle
        
        solver = VRPSolver()
        
        # 添加 depot
        depot = Location(id="DEPOT", lat=57.7089, lon=14.1618, type="depot")
        solver.add_location(depot)
        
        # 添加 pickup 点
        for i in range(3):
            solver.add_location(Location(
                id=f"P{i}",
                lat=57.7089 + (i * 0.01),
                lon=14.1618 + (i * 0.01),
                demand_tons=5.0,
                type="pickup"
            ))
        
        # 添加车辆
        solver.add_vehicle(Vehicle(
            id="V1",
            capacity_tons=20.0,
            start_location=depot
        ))
        
        result = solver.solve()
        
        assert result["status"] in ["optimal", "heuristic"]
        assert "routes" in result
        assert "total_distance_km" in result
