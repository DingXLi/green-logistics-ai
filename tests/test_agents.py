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
        """测试供应预测 (含 LLM fallback + trend field)"""
        agent = SupplyAgent("TEST001", {"lat": 57.7089, "lon": 14.1618})
        agent.daily_capacity = 20.0

        prediction = await agent.predict_supply(days=7)

        assert prediction["prediction_days"] == 7
        assert "total_tons" in prediction
        assert "confidence" in prediction
        # 新增字段
        assert "trend" in prediction
        assert "source" in prediction
        assert "multiplier" in prediction
        # fallback (无 GOOGLE_API_KEY) 时 source = 'fallback', trend = 'stable'
        assert prediction["trend"] in ("stable", "rising", "falling")
        assert prediction["source"] in ("fallback", "llm")
        assert prediction["multiplier"] > 0

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

    @pytest.mark.asyncio
    async def test_match_supply_demand_distance_and_profit(self):
        """新匹配算法：计算距离、按 profit 排序、greedy 1-to-1"""
        agent = MarketAgent()
        offers = [
            {
                "agent_id": "S_BORAS",
                "material_type": "concrete",
                "available_tons": 15.0,
                "location": {"lat": 57.7089, "lon": 14.1618},
            },
            {
                "agent_id": "S_NEAR",
                "material_type": "wood_waste",
                "available_tons": 8.0,
                "location": {"lat": 57.78, "lon": 14.20},
            },
        ]
        demands = [
            {
                "id": "D_FAR",
                "preferred_materials": ["concrete"],
                "demand_tons": 18.0,
                "location": {"lat": 57.71, "lon": 12.0},  # ~128 km from Borås
            },
            {
                "id": "D_NEAR",
                "preferred_materials": ["wood_waste"],
                "demand_tons": 12.0,
                "location": {"lat": 58.0, "lon": 13.5},  # ~50 km
            },
            {
                "id": "D_CONCRETE_NEAR",
                "preferred_materials": ["concrete"],
                "demand_tons": 5.0,
                "location": {"lat": 57.5, "lon": 13.0},  # ~73 km
            },
        ]

        res = await agent.match_supply_demand(offers, demands)

        # 验证结构
        assert "matches" in res
        assert "total_profit_sek" in res
        assert "total_co2_kg" in res
        assert "total_distance_ton_km" in res
        assert res["optimization_status"] in (
            "no_matches", "loss_making", "partial_optimized", "optimized"
        )

        # 验证距离被计算 (不再硬编码 0)
        for m in res["matches"]:
            assert "distance_km" in m
            assert m["distance_km"] > 0
            assert "transport_cost_sek" in m
            assert "co2_kg" in m
            assert "co2_cost_sek" in m
            assert "revenue_sek" in m
            assert "estimated_profit_sek" in m
            assert "profit_per_ton_km" in m

        # 验证 greedy 1-to-1 (没有 supply_id / demand_id 重复)
        supply_ids = [m["supply_id"] for m in res["matches"]]
        demand_ids = [m["demand_id"] for m in res["matches"]]
        assert len(supply_ids) == len(set(supply_ids))
        assert len(demand_ids) == len(len(set(demand_ids))) if False else True
        assert len(demand_ids) == len(set(demand_ids))

        # 验证 profit 排序 (降序)
        profits = [m["profit_per_ton_km"] for m in res["matches"]]
        assert profits == sorted(profits, reverse=True)

        # 验证：wood_waste (近 demand) 应该被选上 (profit > 0)
        wood_match = next(
            (m for m in res["matches"] if m["material_type"] == "wood_waste"),
            None,
        )
        assert wood_match is not None
        assert wood_match["distance_km"] < 100

    @pytest.mark.asyncio
    async def test_match_supply_demand_respects_min_match_tons(self):
        """低于 min_match_tons 的碎屑 demand 应该被跳过"""
        agent = MarketAgent()
        offers = [
            {
                "agent_id": "S1",
                "material_type": "concrete",
                "available_tons": 5.0,
                "location": {"lat": 57.7, "lon": 14.1},
            },
        ]
        demands = [
            {
                "id": "D_TINY",
                "preferred_materials": ["concrete"],
                "demand_tons": 0.1,  # < 0.5 MIN_MATCH_TONS
                "location": {"lat": 57.71, "lon": 14.16},
            },
        ]
        res = await agent.match_supply_demand(offers, demands)
        assert res["total_matches"] == 0
        assert res["optimization_status"] == "no_matches"

    @pytest.mark.asyncio
    async def test_match_supply_demand_no_location_skips(self):
        """没有 location 的 supply/demand 应该被跳过（不 crash）"""
        agent = MarketAgent()
        offers = [
            {"agent_id": "S_NOLOC", "material_type": "concrete", "available_tons": 5.0},  # no location
        ]
        demands = [
            {
                "id": "D_OK",
                "preferred_materials": ["concrete"],
                "demand_tons": 3.0,
                "location": {"lat": 57.7, "lon": 14.1},
            },
        ]
        res = await agent.match_supply_demand(offers, demands)
        assert res["total_matches"] == 0
        assert res["optimization_status"] == "no_matches"

    @pytest.mark.asyncio
    async def test_match_supply_demand_material_mismatch(self):
        """材料类型不匹配应该被过滤"""
        agent = MarketAgent()
        offers = [
            {
                "agent_id": "S1",
                "material_type": "metal_scrap",
                "available_tons": 10.0,
                "location": {"lat": 57.7, "lon": 14.1},
            },
        ]
        demands = [
            {
                "id": "D1",
                "preferred_materials": ["wood_waste"],  # 不匹配
                "demand_tons": 5.0,
                "location": {"lat": 57.71, "lon": 14.16},
            },
        ]
        res = await agent.match_supply_demand(offers, demands)
        assert res["total_matches"] == 0

    @pytest.mark.asyncio
    async def test_price_trend_history_based(self):
        """get_material_price 返回 trend 应基于历史价格样本"""
        agent = MarketAgent()
        # 初始价 = 800 (metal_scrap default), history 只有 1 样本
        p = await agent.get_material_price("metal_scrap")
        assert p["price_sek_per_ton"] == 800
        # 只有 1 个样本 → trend = stable (default)
        assert p["price_trend"] in ("stable", "unknown")

        # 3 次上升 → baseline 上调
        for new_price in [810, 830, 870]:
            agent.record_price_update("metal_scrap", new_price)
        p = await agent.get_material_price("metal_scrap")
        # 870 vs avg ≈ 827 → +5.2% → rising
        assert p["price_trend"] == "rising"
        assert p["price_change_pct"] > 0

        # 3 次下降 → falling
        for new_price in [700, 650, 600]:
            agent.record_price_update("metal_scrap", new_price)
        p = await agent.get_material_price("metal_scrap")
        # 600 vs avg ≈ 720 → -16.7% → falling
        assert p["price_trend"] == "falling"
        assert p["price_change_pct"] < -5

    @pytest.mark.asyncio
    async def test_price_trend_stable_within_threshold(self):
        """小波动 (±5%) 应该被识别为 stable"""
        agent = MarketAgent()
        # 3 次轻微波动 (baseline 800, ±2%)
        for new_price in [800, 810, 805]:
            agent.record_price_update("metal_scrap", new_price)
        p = await agent.get_material_price("metal_scrap")
        assert p["price_trend"] == "stable"

    @pytest.mark.asyncio
    async def test_price_history_cap_at_30_samples(self):
        """history 最多 30 个样本, 超出 FIFO"""
        agent = MarketAgent()
        for i in range(35):
            agent.record_price_update("wood_waste", 200 + i)
        assert len(agent.price_history["wood_waste"]) == 30
        # 最后 5 个样本应该是 [230, 231, 232, 233, 234]
        assert agent.price_history["wood_waste"][-1] == 234


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

    def test_weighted_solve(self):
        """多目标加权 solve：返回 total_objective / cost_weight / co2_weight"""
        from optimization.vrp_solver import VRPSolver, Location, Vehicle

        solver = VRPSolver()
        depot = Location(id="DEPOT", lat=57.7089, lon=14.1618, type="depot")
        solver.add_location(depot)
        for i in range(3):
            solver.add_location(Location(
                id=f"P{i}",
                lat=57.7089 + (i * 0.05),
                lon=14.1618 + (i * 0.05),
                demand_tons=4.0,
                type="pickup",
            ))
        for i in range(2):
            solver.add_location(Location(
                id=f"D{i}",
                lat=57.6 + (i * 0.05),
                lon=14.0 + (i * 0.05),
                demand_tons=-6.0,
                type="delivery",
            ))
        for i in range(3):
            solver.add_vehicle(Vehicle(
                id=f"V{i}",
                capacity_tons=20.0,
                start_location=depot,
            ))

        result = solver.solve(time_limit_seconds=3, cost_weight=0.7, co2_weight=0.3)
        assert result["status"] in ("optimal", "heuristic")
        assert "total_objective" in result
        assert "total_cost_sek" in result
        assert "total_co2_kg" in result
        assert result["cost_weight"] == 0.7
        assert result["co2_weight"] == 0.3
        # total_objective = cost * cost_w + co2 * co2_price * co2_w
        co2_price = result["co2_price_sek_per_kg"]
        expected_obj = (
            result["total_cost_sek"] * 0.7
            + result["total_co2_kg"] * co2_price * 0.3
        )
        assert abs(result["total_objective"] - round(expected_obj, 2)) < 0.5

    def test_pareto_front(self):
        """Pareto 前沿：n_points 个点，覆盖 [1,0] -> [0,1] 权重扫描"""
        from optimization.vrp_solver import VRPSolver, Location, Vehicle

        solver = VRPSolver()
        depot = Location(id="DEPOT", lat=57.7089, lon=14.1618, type="depot")
        solver.add_location(depot)
        for i in range(3):
            solver.add_location(Location(
                id=f"P{i}",
                lat=57.7089 + (i * 0.05),
                lon=14.1618 + (i * 0.05),
                demand_tons=4.0,
                type="pickup",
            ))
        for i in range(2):
            solver.add_location(Location(
                id=f"D{i}",
                lat=57.6 + (i * 0.05),
                lon=14.0 + (i * 0.05),
                demand_tons=-6.0,
                type="delivery",
            ))
        for i in range(3):
            solver.add_vehicle(Vehicle(
                id=f"V{i}",
                capacity_tons=20.0,
                start_location=depot,
            ))

        n = 5
        pareto = solver.solve_pareto(n_points=n, time_limit_seconds=3)

        assert isinstance(pareto, list)
        assert len(pareto) == n
        for i, p in enumerate(pareto):
            assert set(p.keys()) >= {
                "cost_weight", "co2_weight", "cost_sek", "co2_kg",
                "total_objective", "routes", "status",
            }
            # 权重单调变化
            expected_cost_w = 1.0 - i / max(n - 1, 1)
            assert abs(p["cost_weight"] - expected_cost_w) < 1e-6
            # cost + co2 权重和为 1
            assert abs(p["cost_weight"] + p["co2_weight"] - 1.0) < 1e-6

        # 第一个点 cost_w=1.0 → total_objective 应该 == cost_sek
        first = pareto[0]
        assert abs(first["total_objective"] - first["cost_sek"]) < 0.01
        # 最后一个点 co2_w=1.0 → total_objective == co2_kg * co2_price
        last = pareto[-1]
        co2_price = 1.5  # default
        expected = last["co2_kg"] * co2_price
        assert abs(last["total_objective"] - round(expected, 2)) < 0.5


# ============================================
# V2 集成测试：WorldBuilder + SimClock + Persistence + Coordinator
# ============================================

from agents.world_builder import WorldBuilder, WorldConfig
from agents.coordinator import MultiAgentCoordinator
from agents.clock import SimClock
from agents.persistence import Persistence


class TestSimClock:
    """加速时钟测试"""

    def test_initial_state(self):
        clock = SimClock()
        assert clock.now.day == 0
        assert clock.now.hour == 0
        # 0 点是夜间
        assert clock.activity_factor == 0.5

    def test_advance_day(self):
        clock = SimClock()
        clock.advance_day()
        assert clock.now.day == 1
        # hour 从 HOUR_PATTERN = (8, 12, 18, 0, 6, 14, 22) 里按 total_cycles 选
        # 第一个 cycle 选 8（覆盖白天，避免原来 hour 永远 0 的问题）
        assert clock.now.hour == 8
        assert clock.total_cycles == 1

    def test_activity_factor_day(self):
        clock = SimClock(start_hour=12)  # 中午
        assert clock.activity_factor == 1.5

    def test_advance_hours_rollover(self):
        clock = SimClock(start_day=0, start_hour=20)
        clock.advance_hours(10)  # 20 + 10 = 30 → day 1, hour 6
        assert clock.now.day == 1
        assert clock.now.hour == 6


class TestWorldBuilder:
    """世界构建器测试"""

    def test_build_reproducible(self):
        cfg = WorldConfig(n_supply_points=5, n_demand_points=3, n_vehicles=4, seed=42)
        w1 = WorldBuilder(cfg).build()
        w2 = WorldBuilder(cfg).build()
        # 同一 seed 必须可复现（deadline 用 wall-clock，不参与比较）
        s1 = {k: v for k, v in w1["supplies"][0].items()}
        s2 = {k: v for k, v in w2["supplies"][0].items()}
        assert s1 == s2
        d1 = {k: v for k, v in w1["demands"][0].items() if k != "deadline"}
        d2 = {k: v for k, v in w2["demands"][0].items() if k != "deadline"}
        assert d1 == d2

    def test_build_supply_count(self):
        cfg = WorldConfig(n_supply_points=10, n_demand_points=3, n_vehicles=5, seed=1)
        world = WorldBuilder(cfg).build()
        assert len(world["supplies"]) == 10
        assert len(world["demands"]) == 3
        assert len(world["fleet"]) == 5

    def test_supply_has_required_fields(self):
        cfg = WorldConfig(n_supply_points=2, n_demand_points=1, n_vehicles=2, seed=1)
        world = WorldBuilder(cfg).build()
        sup = world["supplies"][0]
        assert "agent_id" in sup
        assert "location" in sup
        assert "lat" in sup["location"]
        assert "lon" in sup["location"]
        assert sup["current_stock"] > 0
        assert sup["daily_capacity"] > 0


class TestPersistence:
    """SQLite 持久化测试"""

    def test_create_and_persist(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        p = Persistence(db_path=db_path)

        p.begin_cycle("OPT0001", sim_day=0, sim_hour=0, activity_factor=1.5)
        p.record_supply("OPT0001", {
            "agent_id": "SUP001",
            "location": {"lat": 57.7, "lon": 14.2},
            "material_type": "mixed_waste",
            "available_tons": 25.5,
            "moisture_percent": 22.0,
            "quality_score": 80.0,
        })
        p.record_demand("OPT0001", {
            "id": "DEM001",
            "name": "Test Plant",
            "location": {"lat": 57.8, "lon": 14.1},
            "material_type": "metal_scrap",
            "required_tons": 50.0,
            "priority": "high",
            "deadline": "2026-06-20T00:00:00",
        })
        p.record_match("OPT0001", {
            "supply_id": "SUP001",
            "demand_id": "DEM001",
            "material_type": "mixed_waste",
            "tons": 20.0,
            "distance_km": 12.5,
            "estimated_profit_sek": 1500.0,
        })
        p.commit_cycle("OPT0001", {
            "n_matches": 1, "total_tons": 20.0, "total_cost_sek": 800.0,
            "total_co2_kg": 100.0, "total_distance_km": 50.0,
            "n_vehicles_used": 1, "n_vehicles_available": 5,
            "fleet_utilization_pct": 20.0, "solver_status": "optimal",
        }, wall_duration_ms=42)

        recent = p.get_recent_cycles(limit=5)
        assert len(recent) == 1
        assert recent[0]["cycle_id"] == "OPT0001"
        assert recent[0]["total_tons"] == 20.0

        summary = p.get_summary()
        assert summary["n_cycles"] == 1
        assert summary["total_tons"] == 20.0


class TestCoordinatorV2Integration:
    """V2 协调器集成测试（小规模，确保跑通）"""

    @pytest.mark.asyncio
    async def test_full_cycle(self, tmp_path):
        db_path = str(tmp_path / "integration.db")
        cfg = WorldConfig(
            n_supply_points=5,
            n_demand_points=3,
            n_vehicles=5,
            seed=42,
        )
        coord = MultiAgentCoordinator(config=cfg, db_path=db_path)

        overview = await coord.get_system_overview()
        assert overview["supply_points"] == 5
        assert overview["demand_points"] == 3
        assert overview["fleet_status"]["total_vehicles"] == 5

        result = await coord.run_optimization_cycle()
        assert result["optimization_id"] == "OPT0001"
        assert result["sim_day"] == 1
        assert "kpi" in result
        assert "wall_duration_ms" in result

        # 持久化
        recent = coord.persistence.get_recent_cycles(limit=5)
        assert len(recent) == 1
        assert recent[0]["cycle_id"] == "OPT0001"

    @pytest.mark.asyncio
    async def test_simulate_3_days(self, tmp_path):
        db_path = str(tmp_path / "sim3.db")
        cfg = WorldConfig(
            n_supply_points=4,
            n_demand_points=2,
            n_vehicles=4,
            seed=7,
        )
        coord = MultiAgentCoordinator(config=cfg, db_path=db_path)
        results = await coord.simulate_day(days=3)
        assert len(results) == 3
        assert results[0]["sim_day"] == 1
        assert results[2]["sim_day"] == 3

        # KPI 时间序列
        ts = coord.persistence.get_kpi_timeseries()
        assert len(ts) == 3

    @pytest.mark.asyncio
    async def test_stock_accumulation(self, tmp_path):
        """库存每个 cycle 都会自然增长。
        注意：per-cycle 后 supply 会被 consume_shipped 扣减（如果 route opt 成功）。
        这里用 10 supply / 1 demand 这样一个 supply 远多于 demand 的配置，
        保证 accumulate 的总量 > consume 的总量，总量净增长。
        """
        db_path = str(tmp_path / "acc.db")
        cfg = WorldConfig(n_supply_points=10, n_demand_points=1, n_vehicles=3, seed=1)
        coord = MultiAgentCoordinator(config=cfg, db_path=db_path)

        before = sum(a.current_stock for a in coord.supply_agents.values())
        await coord.run_optimization_cycle()
        after = sum(a.current_stock for a in coord.supply_agents.values())
        # supply >> demand 场景下，accumulate 应能压过 consume，总量增长
        assert after > before


# ============================================
# V3 OSM Road Network 测试（按网络/超时自动 skip）
# ============================================
class TestOSMRoadNetwork:
    """OSM Road Network 集成测试 — 需要网络访问 Overpass"""

    @pytest.fixture
    def rn(self):
        from data.osm_loader import OSMRoadNetwork, OSMNX_AVAILABLE
        if not OSMNX_AVAILABLE:
            pytest.skip("osmnx not installed")
        rn = OSMRoadNetwork()
        try:
            # 小半径 5km 加快测试，120s 超时
            info = rn.load_region(
                57.7089, 14.1618,  # Borås
                dist_meters=5000,
                timeout=120,
            )
        except Exception as e:
            pytest.skip(f"OSM download failed or timed out: {e}")
        if not rn.is_loaded():
            pytest.skip("OSM graph not loaded")
        return rn

    def test_load_region_returns_metadata(self, rn):
        """load_region 返回节点/边元数据"""
        assert rn.graph is not None
        assert len(rn.graph.nodes) > 0
        assert len(rn.graph.edges) > 0
        assert rn.dist_meters == 5000

    def test_shortest_path_distance_known_pair(self, rn):
        """同一点距离应为 0；不同点距离应为正"""
        d_same = rn.shortest_path_distance(57.7089, 14.1618, 57.7089, 14.1618)
        assert d_same == 0.0
        # 5km 半径内另一个点
        d_other = rn.shortest_path_distance(
            57.7089, 14.1618,
            57.7300, 14.2000,
        )
        assert d_other > 0
        # 不应超过 50km（5km 半径缓冲）
        assert d_other < 50.0

    def test_get_distance_matrix(self, rn):
        """N 个点 → NxN 对称距离矩阵"""
        locs = [
            {"lat": 57.7089, "lon": 14.1618},
            {"lat": 57.7200, "lon": 14.1800},
            {"lat": 57.6900, "lon": 14.1400},
        ]
        D = rn.get_distance_matrix(locs)
        assert D.shape == (3, 3)
        # 对角线为 0
        assert D[0, 0] == 0.0
        assert D[1, 1] == 0.0
        assert D[2, 2] == 0.0
        # 对称
        assert abs(D[0, 1] - D[1, 0]) < 1e-6
        assert abs(D[0, 2] - D[2, 0]) < 1e-6
        assert abs(D[1, 2] - D[2, 1]) < 1e-6
        # 正距离
        assert D[0, 1] > 0
        assert D[0, 2] > 0
        assert D[1, 2] > 0


class TestLogisticsAgentUseRealRoads:
    """iter #8 — optimize_routes 接受 use_real_roads / region 参数"""

    @pytest.mark.asyncio
    async def test_optimize_routes_accepts_use_real_roads_false(self):
        """use_real_roads=False → solve 仍跑通, 返回 distance_source=haversine"""
        from optimization.vrp_solver import VRPSolver
        agent = LogisticsAgent(fleet_size=2)
        # 替换 depot (Borås)
        agent.depot_location = {"lat": 57.7089, "lon": 14.1618}

        pickup = [{"id": "P1", "lat": 57.7300, "lon": 14.1900, "tons": 3.0}]
        delivery = [{"id": "D1", "lat": 57.6700, "lon": 14.1000, "tons": 3.0}]

        result = await agent.optimize_routes(
            pickup_locations=pickup,
            delivery_locations=delivery,
            use_real_roads=False,  # 强制 Haversine, 避免网络
        )

        # 应该返回 status + distance_source
        assert result["status"] in ("optimal", "heuristic", "fallback_nearest_neighbor")
        assert result.get("distance_source") == "haversine"
        assert result.get("use_real_roads") is False

    @pytest.mark.asyncio
    async def test_optimize_routes_accepts_use_real_roads_true(self):
        """use_real_roads=True → distance_source 应该是 osm 或 haversine (网络决定)"""
        agent = LogisticsAgent(fleet_size=2)
        agent.depot_location = {"lat": 57.7089, "lon": 14.1618}

        pickup = [{"id": "P1", "lat": 57.7300, "lon": 14.1900, "tons": 3.0}]
        delivery = [{"id": "D1", "lat": 57.6700, "lon": 14.1000, "tons": 3.0}]

        result = await agent.optimize_routes(
            pickup_locations=pickup,
            delivery_locations=delivery,
            use_real_roads=True,
            region="Borås, Sweden",
            distance_timeout_s=10,
        )

        assert result["status"] in ("optimal", "heuristic", "fallback_nearest_neighbor")
        assert result.get("distance_source") in ("osm", "haversine")
        assert result.get("use_real_roads") is True

    @pytest.mark.asyncio
    async def test_optimize_routes_empty_inputs(self):
        """空 pickup/delivery → 不抛异常"""
        agent = LogisticsAgent(fleet_size=2)
        agent.depot_location = {"lat": 57.7089, "lon": 14.1618}

        result = await agent.optimize_routes(
            pickup_locations=[],
            delivery_locations=[],
            use_real_roads=False,
        )
        # 空输入会进入 fallback 路径或返回 no_routes
        assert "status" in result or "routes" in result


class TestOptimizeRequestFields:
    """iter #8 — OptimizationRequest 应该接受 use_real_roads + region"""

    def test_request_default_use_real_roads_true(self):
        from web.backend.main import OptimizationRequest
        req = OptimizationRequest()
        assert req.use_real_roads is True
        assert req.region is None

    def test_request_accepts_use_real_roads_false(self):
        from web.backend.main import OptimizationRequest
        req = OptimizationRequest(use_real_roads=False)
        assert req.use_real_roads is False

    def test_request_accepts_custom_region(self):
        from web.backend.main import OptimizationRequest
        req = OptimizationRequest(region="Göteborg, Sweden")
        assert req.region == "Göteborg, Sweden"

    def test_response_default_distance_source_none(self):
        from web.backend.main import OptimizationResponse
        resp = OptimizationResponse(
            status="success",
            timestamp="2026-08-27T10:00:00",
            matches_count=3,
            total_tons=10.0,
            total_cost_sek=500.0,
            total_co2_kg=200.0,
        )
        assert resp.distance_source is None

    def test_response_with_distance_source(self):
        from web.backend.main import OptimizationResponse
        resp = OptimizationResponse(
            status="success",
            timestamp="2026-08-27T10:00:00",
            matches_count=3,
            total_tons=10.0,
            total_cost_sek=500.0,
            total_co2_kg=200.0,
            distance_source="osm",
        )
        assert resp.distance_source == "osm"
