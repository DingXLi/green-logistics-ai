"""
多智能体协调器 (Multi-Agent Coordinator) — V2

负责：
- 协调 Supply、Market、Logistics 智能体之间的通信
- 用 SyntheticDataGenerator + WorldBuilder 引导仿真世界
- 用 SimClock 推进加速时间（1 cycle = 1 sim-day）
- 用 Persistence 把每个 cycle 的 KPI / supply / demand / matches / routes 落 SQLite
- 提供统一的 API 接口

数据流（Plan A — 协调器集中注入）：
    WorldBuilder ─┐
                  ├─→ Coordinator.__init__ 一次性建好世界
    SimClock ─────┤
                  └─→ Coordinator.run_optimization_cycle() 每周期：
                        1. clock.advance_day()
                        2. 每个 SupplyAgent.accumulate_stock(factor)
                        3. 收集 supply_offers / demand_requests
                        4. market_agent.match_supply_demand()
                        5. logistics_agent.optimize_routes()
                        6. persistence 落盘 KPI + 子数据
"""

import asyncio
import math
import time
from collections import defaultdict
from typing import List, Dict, Any, Optional
from datetime import datetime
from loguru import logger

from .supply_agent import SupplyAgent
from .market_agent import MarketAgent
from .logistics_agent import LogisticsAgent
from .clock import SimClock
from .persistence import Persistence
from .world_builder import WorldBuilder, WorldConfig
from data.seasonal_adjuster import get_supply_multiplier as _get_supply_seasonal
from data.seasonal_adjuster import get_demand_multiplier as _get_demand_seasonal
from data.seasonal_adjuster import sim_day_to_month as _seasonal_month
from data.seasonal_perturbation import (
    apply_perturbations as _apply_perturbations,
    SeasonalPerturbation as _SeasonalPerturbation,
)

def _get_perturbed_seasonal(
    base_factor: float,
    material_type: str,
    day: int,
    persistence: Any,
) -> float:
    """
    iter #37: Apply any active perturbations to the base seasonal factor.

    Coordinator calls this once per (agent, day) tuple inside the cycle loop.
    We pre-fetch the active perturbations ONCE per cycle (not per agent) to
    avoid N+1 queries; coordinator caches it on self._active_perturbations.
    """
    if persistence is None:
        return base_factor
    cached = getattr(persistence, "_active_perturbations_cache", None)
    if cached is None or cached.get("sim_day") != day:
        try:
            rows = persistence.get_active_perturbations(day)
            cache_objs = [_SeasonalPerturbation(
                id=r["id"], label=r["label"],
                start_sim_day=r["start_sim_day"],
                end_sim_day=r["end_sim_day"],
                material_type=r["material_type"],
                multiplier=r["multiplier"],
                active=bool(r["active"]),
                created_at=r["created_at"],
            ) for r in rows]
            persistence._active_perturbations_cache = {"sim_day": day, "objs": cache_objs}
        except Exception as e:
            # Persistence failure should not break the cycle — log + return base
            import loguru
            loguru.logger.debug(f"[perturb] cache refresh failed: {e}")
            return base_factor
        cached = persistence._active_perturbations_cache
    return _apply_perturbations(
        base_factor=base_factor,
        material_type=material_type,
        sim_day=day,
        active_perturbations=cached["objs"],
    )



class MultiAgentCoordinator:
    """
    多智能体系统协调器 (V2 — 数据驱动版)
    """

    def __init__(
        self,
        config: WorldConfig = None,
        db_path: str = "data/simulation.db",
        auto_init_world: bool = True,
    ):
        self.config = config or WorldConfig()
        self.clock = SimClock()
        self.persistence = Persistence(db_path=db_path)

        logger.info(
            f"初始化协调器：{self.config.n_supply_points} supply / "
            f"{self.config.n_demand_points} demand / {self.config.n_vehicles} vehicles / seed={self.config.seed}"
        )

        # 初始化 agents（占位，后面会注入）
        self.supply_agents: Dict[str, SupplyAgent] = {}
        self.market_agent = MarketAgent()
        self.logistics_agent = LogisticsAgent(
            fleet_size=self.config.n_vehicles,
            depot_location={
                "lat": self.config.depot_location[0],
                "lon": self.config.depot_location[1],
            },
        )

        self.system_status = {
            "initialized_at": datetime.now().isoformat(),
            "status": "running",
            "total_optimizations": 0,
            "last_optimization": None,
            "config": {
                "n_supply_points": self.config.n_supply_points,
                "n_demand_points": self.config.n_demand_points,
                "n_vehicles": self.config.n_vehicles,
                "seed": self.config.seed,
            },
        }

        # iter #8: cache 最近一次 cycle result (供 /api/optimize/last 读 distance_source)
        self._last_cycle_result: Optional[Dict[str, Any]] = None

        if auto_init_world:
            self._bootstrap_world()

        logger.info("协调器初始化完成")

    # ------------------------------------------------------------
    # 世界引导
    # ------------------------------------------------------------

    def _bootstrap_world(self) -> None:
        """用 WorldBuilder 一次性建好 supply / demand / fleet 节点"""
        builder = WorldBuilder(self.config)
        world = builder.build()

        # 1. 注册供应点
        for sup in world["supplies"]:
            agent = SupplyAgent(sup["agent_id"], sup["location"])
            agent.set_inventory(
                current_stock=sup["current_stock"],
                daily_capacity=sup["daily_capacity"],
                material_type=sup["material_type"],
                moisture_percent=sup["moisture_percent"],
                quality_score=sup["quality_score"],
            )
            self.supply_agents[sup["agent_id"]] = agent

        # 2. 注入需求点
        self.market_agent.inject_demands(world["demands"])

        # 3. 注入车队（覆盖 LogisticsAgent 默认生成的 10 车）
        self.logistics_agent.inject_fleet(world["fleet"])

        logger.info(
            f"世界引导完成：{len(self.supply_agents)} supply / "
            f"{len(self.market_agent.demand_points)} demand / "
            f"{len(self.logistics_agent.vehicles)} vehicles"
        )

    # ------------------------------------------------------------
    # 兼容旧 API：手动注册供应点（保留测试兼容）
    # ------------------------------------------------------------

    def register_supply_agent(self, agent_id: str, location: Dict[str, float]):
        """注册供应智能体（手动模式，仅在 auto_init_world=False 时使用）"""
        self.supply_agents[agent_id] = SupplyAgent(agent_id, location)
        logger.info(f"手动注册供应智能体：{agent_id}")

    # ------------------------------------------------------------
    # 系统概览
    # ------------------------------------------------------------

    async def get_system_overview(self) -> Dict[str, Any]:
        """获取系统概览"""
        supply_status = []
        for agent_id, agent in self.supply_agents.items():
            stock = await agent.get_current_stock()
            supply_status.append(stock)

        fleet_status = await self.logistics_agent.get_fleet_status()
        demand_status = await self.market_agent.get_demand_status()

        return {
            "system_status": self.system_status,
            "clock": self.clock.state(),
            "supply_points": len(self.supply_agents),
            "supply_status": supply_status,
            "fleet_status": fleet_status,
            "demand_points": len(demand_status),
            "demand_status": demand_status,
        }

    # ------------------------------------------------------------
    # 单周期优化（核心循环）
    # ------------------------------------------------------------

    async def run_optimization_cycle(
        self,
        use_real_roads: bool = True,
        region: Optional[str] = None,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """
        运行一次完整的优化周期（= 1 sim-day）。

        流程：
        0. 推进时钟 + 开始持久化
        1. 每个 supply 自然积累库存（用 activity_factor）
        2. 收集所有 supply_offers
        3. 收集所有 demand_requests
        4. 匹配供需
        5. 优化物流路径
        6. 更新车辆状态 + 落盘

        iter #8 新参数:
        - use_real_roads: bool = True (VRP 走 OSM 真实路网 vs Haversine)
        - region: OSM 地区名 (None = 从 depot_location 反推)

        iter #12 新参数:
        - dry_run: bool = False — 跑 cycle 但跳过 persistence (scheduler 调试用)
        """
        t_start = time.time()

        # 0. 时钟 + 周期 ID
        self.clock.advance_day()
        cycle_id = f"OPT{self.clock.total_cycles:04d}"
        factor = self.clock.activity_factor
        day = self.clock.now.day
        weekday = day % 7
        logger.info(f"开始周期 {cycle_id} @ {self.clock.now} (factor={factor})")

        # 0. LLM 供应预测 (单次调用拿全部 supply 点的 accumulation multiplier)
        # Sleep 错峰: 避免 supply + demand 两调连发撞 Gemini RPM 限制
        # 实测 2 calls / cycle: supply 调 + demand 调,加 2s sleep 拉开间距
        await asyncio.sleep(0.5)  # 微量 startup 延迟 (DNS / connection warmup)
        supply_llm = await SupplyAgent.predict_supply_batch(
            list(self.supply_agents.values()),
            days=1, sim_day=day, weekday=weekday,
        )
        await asyncio.sleep(2.0)  # 错峰: 等 supply 调用完全退出

        # 1. 库存自然积累 + 车辆状态重置（LLM multiplier 影响 accumulation 速率）
        for agent_id, agent in self.supply_agents.items():
            llm_m = supply_llm.get(agent_id, {}).get("multiplier", 1.0)
            # 季节因子: 不同 material 不同月度 pattern (建筑夏高冬低, 金属平稳)
            base_seasonal = _get_supply_seasonal(agent.material_type, day)
            # iter #37: overlay any active perturbation rules (e.g. holiday spike)
            seasonal_m = _get_perturbed_seasonal(
                base_seasonal, agent.material_type, day, self.persistence
            )
            agent.accumulate_stock(
                factor=factor,
                llm_multiplier=llm_m,
                seasonal_multiplier=seasonal_m,
            )
        self.logistics_agent.reset_vehicles_for_new_cycle()

        # 2. 收集供应 offers (LLM multiplier + seasonal_factor 调整 predicted_tons)
        supply_offers = []
        for agent_id, agent in self.supply_agents.items():
            stock = await agent.get_current_stock()
            base_pred = agent.daily_capacity * 0.8 * 1
            llm_m = supply_llm.get(agent_id, {}).get("multiplier", 1.0)
            base_seasonal = _get_supply_seasonal(agent.material_type, day)
            seasonal_m = _get_perturbed_seasonal(
                base_seasonal, agent.material_type, day, self.persistence
            )
            # LLM + seasonal + perturbation 三者叠加：predicted_tons 受所有因素影响
            predicted_tons = round(base_pred * llm_m * seasonal_m, 2)
            sup_meta = supply_llm.get(agent_id, {})
            supply_offers.append({
                "agent_id": agent_id,
                "available_tons": stock["stock_tons"],
                "predicted_tons": predicted_tons,
                "material_type": stock["material_type"],
                "location": stock["location"],
                "moisture_percent": agent.moisture_percent,
                "quality_score": agent.quality_score,
                # LLM 决策可追溯
                "llm_multiplier": round(llm_m, 3),
                "llm_trend": sup_meta.get("trend"),
                "llm_confidence": sup_meta.get("confidence"),
                "llm_reason": sup_meta.get("reason"),
                "llm_source": sup_meta.get("source", "unknown"),
                # Seasonal 决策可追溯
                "seasonal_multiplier": round(seasonal_m, 3),
                "sim_month": _seasonal_month(day),
            })

        # 3. 收集需求 requests（LLM 驱动的 multiplier + 小幅 deterministic jitter）
        # 优先 LLM 预测 (per-point multiplier)；失败 / 不可用时 fallback 到 deterministic
        await asyncio.sleep(0.5)  # supply 后的额外冷却
        llm_pred = await self.market_agent.predict_demand(
            days=1, sim_day=day, weekday=weekday
        )
        llm_mults = {p["id"]: p["multiplier"] for p in llm_pred.get("predictions", [])}
        llm_meta_by_id = {
            p["id"]: {
                "trend": p.get("trend", "unknown"),
                "confidence": p.get("confidence", 0.0),
                "reason": p.get("reason", ""),
            }
            for p in llm_pred.get("predictions", [])
        }
        # Fallback 全局 multiplier (仅在 LLM 没给某 id 时使用)
        cycle_mult = self._compute_demand_multiplier(day)
        demand_status = await self.market_agent.get_demand_status()
        demand_requests = []
        for dp in demand_status:
            # 以 base_demand_tons 为唯一来源重新计算，避免在 in-memory 上累乘造成失控
            base = dp.get("base_demand_tons") or dp.get("current_demand_tons", 0)
            jitter = self._per_demand_jitter(dp["id"], day)
            llm_m = llm_mults.get(dp["id"], cycle_mult)  # LLM 缺某 id 时用 deterministic
            # Seasonal: demand 同样随月份波动
            dp_material = dp.get("material_type") or (dp.get("preferred_materials") or ["mixed_waste"])[0]
            base_seasonal = _get_demand_seasonal(dp_material, day)
            seasonal_m = _get_perturbed_seasonal(
                base_seasonal, dp_material, day, self.persistence
            )
            perturbed = round(base * llm_m * jitter * seasonal_m, 2)
            # 同步 in-memory 状态供 dashboard / 下游使用
            for live_dp in self.market_agent.demand_points:
                if live_dp.get("id") == dp["id"]:
                    live_dp["current_demand_tons"] = perturbed
                    break
            llm_meta = llm_meta_by_id.get(dp["id"], {})
            demand_requests.append({
                "id": dp["id"],
                "name": dp["name"],
                "demand_tons": perturbed,
                "preferred_materials": dp["preferred_materials"],
                "location": dp["location"],
                "priority": dp.get("priority", "normal"),
                "deadline": dp.get("deadline"),
                "material_type": dp.get("material_type"),
                # 额外：让 LLM 决策可追溯
                "llm_multiplier": round(llm_m, 3),
                "llm_trend": llm_meta.get("trend"),
                "llm_confidence": llm_meta.get("confidence"),
                "llm_reason": llm_meta.get("reason"),
                "llm_source": llm_pred.get("source", "unknown"),
            })

        # 4. 持久化：开始周期 + 写子数据
        # 季节因子: 本 cycle 所有 supply 点的 seasonal_multiplier 平均 + 月份
        seasonal_factor_avg = (
            sum(s.get("seasonal_multiplier", 1.0) for s in supply_offers)
            / max(len(supply_offers), 1)
        )
        sim_month = _seasonal_month(day)
        # iter #12: dry_run 模式下跳过所有 persistence (但 cycle 逻辑仍完整)
        if not dry_run:
            self.persistence.begin_cycle(
                cycle_id=cycle_id,
                sim_day=self.clock.now.day,
                sim_hour=self.clock.now.hour,
                activity_factor=factor,
                n_supply_offers=len(supply_offers),
                n_demand_requests=len(demand_requests),
                seasonal_factor_avg=round(seasonal_factor_avg, 3),
                seasonal_month=sim_month,
            )
            for sup in supply_offers:
                self.persistence.record_supply(cycle_id, sup)
            for dem in demand_requests:
                self.persistence.record_demand(cycle_id, dem)
            # 持久化 LLM 决策 (供报告画图)
            if supply_llm:
                self.persistence.record_llm_decisions_batch(
                    cycle_id=cycle_id,
                    decision_type="supply_prediction",
                    target_type="supply_point",
                    predictions=list(supply_llm.values()),
                    sim_day=day,
                    sim_hour=self.clock.now.hour,
                )
            if llm_pred.get("predictions"):
                self.persistence.record_llm_decisions_batch(
                    cycle_id=cycle_id,
                    decision_type="demand_prediction",
                    target_type="demand_point",
                    predictions=llm_pred["predictions"],
                    sim_day=day,
                    sim_hour=self.clock.now.hour,
                )

        # 5. 匹配供需
        matches = await self.market_agent.match_supply_demand(
            supply_offers=supply_offers,
            demand_requests=demand_requests,
        )
        if not dry_run:
            for m in matches.get("matches", []):
                self.persistence.record_match(cycle_id, m)

        # 6. 优化物流路径
        route_optimization: Dict[str, Any] = {"status": "no_matches"}
        if matches["total_matches"] > 0:
            pickup_locations, delivery_locations = self._build_vrp_inputs(
                matches, supply_offers, demand_status
            )
            route_optimization = await self.logistics_agent.optimize_routes(
                pickup_locations=pickup_locations,
                delivery_locations=delivery_locations,
                use_real_roads=use_real_roads,
                region=region,
            )
            # 落盘 routes
            if not dry_run:
                for route in route_optimization.get("routes", []):
                    self.persistence.record_route(cycle_id, route)

        # 6b. 让 supply 库存反映本周期实际被出运的量（quasi-steady，避免单调递增
        # 锁死在单车 cap 上）。**只有当 route opt 真的成功时**才扣减，否则 no_solution
        # 那天会白白消耗 stock、产生下行螺旋，tens → 0。
        shipped_by_supply: Dict[str, float] = defaultdict(float)
        if route_optimization.get("status") in ("optimal", "feasible"):
            # 优先用 routes 的 stops 反算实际出运量；如果没有则退到 matches。
            for r in route_optimization.get("routes", []):
                for stop in r.get("stops", []):
                    sid = stop.get("id")
                    t = float(stop.get("tons", 0) or 0)
                    if sid and t > 0:
                        shipped_by_supply[sid] += t
            if not shipped_by_supply:
                for m in matches.get("matches", []):
                    sid = m.get("supply_id")
                    if sid:
                        shipped_by_supply[sid] += float(m.get("tons", 0) or 0)
        for sid, tons in shipped_by_supply.items():
            if sid in self.supply_agents:
                self.supply_agents[sid].consume_shipped(tons)

        # 7. KPI
        fleet_status = await self.logistics_agent.get_fleet_status()
        kpi = self._extract_kpi(matches, route_optimization, fleet_status)

        t_end = time.time()
        wall_ms = int((t_end - t_start) * 1000)
        # iter #12: dry_run 跳过 final commit_cycle
        if not dry_run:
            self.persistence.commit_cycle(cycle_id, kpi, wall_duration_ms=wall_ms)

        # 8. 更新系统状态
        self.system_status["total_optimizations"] = self.clock.total_cycles
        self.system_status["last_optimization"] = datetime.now().isoformat()

        result = {
            "optimization_id": cycle_id,
            "timestamp": self.clock.iso_now,
            "sim_day": self.clock.now.day,
            "sim_hour": self.clock.now.hour,
            "activity_factor": factor,
            "supply_offers_count": len(supply_offers),
            "demand_requests_count": len(demand_requests),
            "matches": matches,
            "route_optimization": route_optimization,
            "kpi": kpi,
            "system_status": self.system_status,
            "wall_duration_ms": wall_ms,
            # iter #8: route_optimization 的 distance_source / use_real_roads
            # 提取出来, 供 API endpoint (/api/optimize, /api/optimize/last) 直接返回
            "distance_source": route_optimization.get("distance_source", "unknown")
                if isinstance(route_optimization, dict) else "unknown",
        }

        logger.info(
            f"周期 {cycle_id} 完成：matches={kpi['n_matches']} tons={kpi['total_tons']:.1f} "
            f"cost={kpi['total_cost_sek']:.0f} SEK co2={kpi['total_co2_kg']:.1f}kg "
            f"({wall_ms}ms){' (DRY RUN)' if dry_run else ''}"
        )
        # iter #8: cache this result (供 /api/optimize/last 读 distance_source)
        # iter #12: dry_run 不覆盖 cache (避免污染 production cache)
        if not dry_run:
            self._last_cycle_result = result
        return result

    # ------------------------------------------------------------
    # 扰动函数（per-cycle demand variability）
    # ------------------------------------------------------------

    @staticmethod
    def _compute_demand_multiplier(day: int) -> float:
        """每周期需求乘子。
        设计：
        - weekday 0-4 （Mon-Fri）= 1.0，weekday 5-6 （Sat/Sun）= 0.85（现实里废料产量周末下降）
        - bounded noise = 0.90 + 0.10 * sin(day * 0.91)，结果在 [0.80, 1.00]
        两者相乘后范围约 [0.68, 1.00] 决定扰动。
        保守选择：保证 perturb 后需求不会太极端，VRP 可用性可接受。
        """
        weekday = day % 7
        weekend_factor = 0.85 if weekday >= 5 else 1.0
        noise = 0.90 + 0.10 * math.sin(day * 0.91)
        return round(weekend_factor * noise, 3)

    @staticmethod
    def _per_demand_jitter(demand_id: str, day: int) -> float:
        """每个 demand 点在当天的额外 jitter。
        hash((id, day)) → [0.90, 1.10] 的小偏移，避免所有点同步变化。
        """
        h = hash((demand_id, day)) % 1000
        return 0.90 + 0.2 * (h / 1000.0)

    def _build_vrp_inputs(self, matches, supply_offers, demand_status):
        """从 matches + supply_offers + demand_status 构造 VRP 输入
        关键：合并同一 supply_id / demand_id 的多个 match（OR-Tools VRP 需要 unique node）。
        并且：每个 pickup/delivery 的 demand_tons 不得超过单车容量（20t），否则 OR-Tools 返回 no_solution。
        """
        from collections import defaultdict

        MAX_VEHICLE_TONS = 20.0

        # 合并 pickups
        pickup_tons = defaultdict(float)
        pickup_loc = {}
        for s in supply_offers:
            pickup_loc[s["agent_id"]] = s["location"]
        for m in matches["matches"]:
            pickup_tons[m["supply_id"]] += m["tons"]

        pickups = []
        for sid, tons in pickup_tons.items():
            loc = pickup_loc.get(sid, {"lat": 57.7, "lon": 14.2})
            pickups.append({
                "id": sid,
                "tons": round(min(tons, MAX_VEHICLE_TONS), 2),
                "lat": loc["lat"],
                "lon": loc["lon"],
            })

        # 合并 deliveries
        delivery_tons = defaultdict(float)
        delivery_loc = {}
        for d in demand_status:
            delivery_loc[d["id"]] = d["location"]
        for m in matches["matches"]:
            delivery_tons[m["demand_id"]] += m["tons"]

        deliveries = []
        for did, tons in delivery_tons.items():
            loc = delivery_loc.get(did, {"lat": 57.7, "lon": 14.2})
            deliveries.append({
                "id": did,
                "tons": round(min(tons, MAX_VEHICLE_TONS), 2),
                "lat": loc["lat"],
                "lon": loc["lon"],
            })

        return pickups, deliveries

    def _extract_kpi(self, matches, route_opt, fleet_status) -> Dict[str, Any]:
        """从 match + route 结果里抽 KPI"""
        return {
            "n_matches": matches.get("total_matches", 0),
            "total_tons": matches.get("total_tons", 0),
            "total_cost_sek": route_opt.get("total_cost_sek", 0) if isinstance(route_opt, dict) else 0,
            "total_co2_kg": route_opt.get("total_co2_kg", 0) if isinstance(route_opt, dict) else 0,
            "total_distance_km": route_opt.get("total_distance_km", 0) if isinstance(route_opt, dict) else 0,
            "n_vehicles_used": len(route_opt.get("routes", [])) if isinstance(route_opt, dict) else 0,
            "n_vehicles_available": fleet_status.get("available", 0),
            "fleet_utilization_pct": fleet_status.get("utilization_rate", 0),
            "solver_status": route_opt.get("status", "unknown") if isinstance(route_opt, dict) else "unknown",
        }

    # ------------------------------------------------------------
    # 多日模拟
    # ------------------------------------------------------------

    async def simulate_day(self, days: int = 1) -> List[Dict[str, Any]]:
        """
        模拟运行 N 个 sim-day

        每个 day = 1 个 optimization cycle（= clock.advance_day() + 库存积累 + 优化）
        """
        results = []
        for day in range(days):
            result = await self.run_optimization_cycle()
            result["simulation_day"] = day + 1
            results.append(result)
            # 不 sleep（加速时钟）；如果想慢一点演示可以 asyncio.sleep(0.1)
        return results


# ============================================
# 主程序入口
# ============================================
async def main():
    """演示：用合成数据跑 7 天仿真"""
    from agents.world_builder import WorldConfig

    print("\n" + "="*60)
    print("🟢 Green Logistics AI — 多智能体协调器 (V2)")
    print("="*60)

    config = WorldConfig(
        n_supply_points=20,
        n_demand_points=10,
        n_vehicles=30,
        seed=42,
    )
    coordinator = MultiAgentCoordinator(config=config)

    # 系统概览
    overview = await coordinator.get_system_overview()
    import json
    print("\n[系统概览]")
    print(json.dumps({
        "supply_points": overview["supply_points"],
        "demand_points": overview["demand_points"],
        "fleet": overview["fleet_status"],
        "clock": overview["clock"],
    }, indent=2, ensure_ascii=False))

    # 跑 7 天
    print("\n" + "="*60)
    print("⏩ 跑 7 天仿真")
    print("="*60)
    simulation = await coordinator.simulate_day(days=7)

    # 汇总
    print("\n" + "="*60)
    print("📊 7 天 KPI 汇总")
    print("="*60)
    total_tons = sum(r["kpi"]["total_tons"] for r in simulation)
    total_cost = sum(r["kpi"]["total_cost_sek"] for r in simulation)
    total_co2 = sum(r["kpi"]["total_co2_kg"] for r in simulation)
    avg_util = sum(r["kpi"]["fleet_utilization_pct"] for r in simulation) / len(simulation)
    print(f"总运输：{total_tons:.1f} 吨")
    print(f"总成本：{total_cost:.0f} SEK")
    print(f"总碳排放：{total_co2:.1f} kg CO2")
    print(f"平均车队利用率：{avg_util:.1f}%")

    # DB summary
    print("\n" + "="*60)
    print("💾 SQLite 落盘汇总")
    print("="*60)
    print(json.dumps(coordinator.persistence.get_summary(), indent=2))


if __name__ == "__main__":
    asyncio.run(main())
