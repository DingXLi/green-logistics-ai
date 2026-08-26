"""
市场智能体 (Market Agent)

负责：
- 监控回收材料需求
- 价格预测和市场分析
- 供需匹配优化
- 利润最大化策略
"""

from google.adk import Agent
from google.adk import tools
from typing import List, Dict, Any, Optional
from datetime import datetime
import asyncio
import math
from loguru import logger

from .llm_config import MODEL  # 中心化 model 名 (env > yaml > 默认)


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """两点间的 Haversine 距离 (km)"""
    R = 6371.0  # 地球半径 (km)
    lat1_rad, lat2_rad = math.radians(lat1), math.radians(lat2)
    delta_lat = math.radians(lat2 - lat1)
    delta_lon = math.radians(lon2 - lon1)
    a = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lon / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


def _analyze_price_trend(
    history: List[float],
    current_price: float,
    threshold_pct: float = 5.0,
) -> tuple[str, float]:
    """
    基于历史价格样本计算 trend。

    - history: 旧到新价格列表 (从 price_history 取)
    - current_price: 当前市场价
    - threshold_pct: 升/降阈值 (默认 ±5%, 避免噪音)

    Returns:
        (trend_label, change_pct)
        trend_label ∈ {'rising', 'stable', 'falling', 'unknown'}
        change_pct = (current - mean_of_history) / mean * 100
    """
    if not history:
        return "unknown", 0.0
    mean = sum(history) / len(history)
    if mean == 0:
        return "unknown", 0.0
    change_pct = (current_price - mean) / mean * 100.0
    if change_pct > threshold_pct:
        trend = "rising"
    elif change_pct < -threshold_pct:
        trend = "falling"
    else:
        trend = "stable"
    return trend, round(change_pct, 2)


class MarketAgent:
    """
    市场智能体 - 管理需求和价格
    """
    
    def __init__(self):
        # 需求点示例（破碎机、回收厂等）
        self.demand_points = self._initialize_demand_points()
        
        # 材料价格 (SEK/吨)
        self.material_prices = {
            "mixed_waste": 150,
            "metal_scrap": 800,
            "wood_waste": 200,
            "plastic": 450,
            "concrete": 100
        }
        # 价格历史: 最近 N 价样本, 推 trend (升 / 稳定 / 降)
        # 最多保留 30 个样本 (典型月度分析足够)
        self.price_history: Dict[str, List[float]] = {
            mat: [p] for mat, p in self.material_prices.items()
        }
        # baseline 均价, get_material_price() 会跟它比
        self._price_baseline: Dict[str, float] = dict(self.material_prices)
        
        # 创建 ADK Agent
        self.agent = Agent(
            name="market_coordinator",
            model=MODEL,
            description="Market agent for demand and price management",
            instruction=self._get_instruction(),
        )
    
    def _initialize_demand_points(self) -> List[Dict[str, Any]]:
        """初始化需求点"""
        return [
            {
                "id": "DEM001",
                "name": "Borås Recycling Plant",
                "location": {"lat": 57.7089, "lon": 14.1618},
                "daily_capacity_tons": 100,
                "current_demand_tons": 45,
                "preferred_materials": ["mixed_waste", "metal_scrap"]
            },
            {
                "id": "DEM002",
                "name": "Gothenburg Crusher",
                "location": {"lat": 57.7089, "lon": 11.9746},
                "daily_capacity_tons": 150,
                "current_demand_tons": 80,
                "preferred_materials": ["concrete", "wood_waste"]
            },
            {
                "id": "DEM003",
                "name": "Stockholm Processing",
                "location": {"lat": 59.3293, "lon": 18.0686},
                "daily_capacity_tons": 200,
                "current_demand_tons": 120,
                "preferred_materials": ["mixed_waste", "plastic"]
            }
        ]
    
    def _get_instruction(self) -> str:
        return f"""
你是市场智能体，负责管理回收材料的需求和价格。

你的职责：
1. 监控各需求点的库存和需求
2. 预测价格走势
3. 匹配供应和需求以实现利润最大化
4. 考虑运输成本和碳排放进行优化决策

当前管理 {len(self.demand_points)} 个需求点
"""
    
    async def get_demand_status(self) -> List[Dict[str, Any]]:
        """获取所有需求点状态

        必须把 material_type / priority / deadline 一起返回，
        否则下游 coordinator 在写 demand_requests 持久化时这些字段会变成 NULL。
        """
        return [
            {
                "id": dp["id"],
                "name": dp["name"],
                "current_demand_tons": dp["current_demand_tons"],
                "capacity_tons": dp["daily_capacity_tons"],
                "utilization_rate": (
                    dp["current_demand_tons"] / dp["daily_capacity_tons"] * 100
                    if dp["daily_capacity_tons"] else 0
                ),
                "preferred_materials": dp["preferred_materials"],
                "material_type": dp.get("material_type"),
                "priority": dp.get("priority", "normal"),
                "deadline": dp.get("deadline"),
                "location": dp["location"]
            }
            for dp in self.demand_points
        ]

    # ------------------------------------------------------------
    # 由 WorldBuilder / Coordinator 调用的状态注入接口
    # ------------------------------------------------------------

    def inject_demands(self, demands: List[Dict[str, Any]]) -> None:
        """
        注入需求点（来自 WorldBuilder 或运行时合成数据）。
        每次调用会完整替换 self.demand_points。
        """
        self.demand_points = demands
        logger.info(f"市场智能体已注入 {len(demands)} 个需求点")
    
    async def get_material_price(self, material_type: str) -> Dict[str, Any]:
        """获取材料价格 + trend 分析 (基于历史价跟 baseline 对比)"""
        price = self.material_prices.get(material_type, 0)
        trend, trend_pct = _analyze_price_trend(
            self.price_history.get(material_type, []),
            current_price=price,
        )
        return {
            "material_type": material_type,
            "price_sek_per_ton": price,
            "price_trend": trend,
            "price_change_pct": trend_pct,
            "last_updated": datetime.now().isoformat()
        }

    def record_price_update(self, material_type: str, price: float) -> None:
        """外部调用：记录一次市场价格。多次调用后 trend 会更新。

        例子:
            await market_agent.record_price_update('metal_scrap', 850)
            price = await market_agent.get_material_price('metal_scrap')
            # price_trend = 'rising' (if 850 > prev 800)
        """
        history = self.price_history.setdefault(material_type, [])
        history.append(float(price))
        # 保留最近 30 个样本
        if len(history) > 30:
            history.pop(0)
        # 更新 baseline (滑动平均)
        if len(history) >= 3:
            self._price_baseline[material_type] = sum(history) / len(history)
        # 同步 material_prices (让 match_supply_demand 等下游逻辑反映新价)
        self.material_prices[material_type] = float(price)
    
    async def calculate_profit(
        self,
        material_type: str,
        tons: float,
        transport_cost_sek: float,
        co2_cost_sek: float
    ) -> Dict[str, float]:
        """计算利润"""
        revenue = tons * self.material_prices.get(material_type, 0)
        total_cost = transport_cost_sek + co2_cost_sek
        profit = revenue - total_cost
        
        return {
            "revenue_sek": round(revenue, 2),
            "transport_cost_sek": round(transport_cost_sek, 2),
            "co2_cost_sek": round(co2_cost_sek, 2),
            "total_cost_sek": round(total_cost, 2),
            "profit_sek": round(profit, 2),
            "profit_margin_percent": round(profit / revenue * 100, 2) if revenue > 0 else 0
        }
    
    async def match_supply_demand(
        self,
        supply_offers: List[Dict[str, Any]],
        demand_requests: List[Dict[str, Any]],
        cost_per_km_sek: float = 2.6,
        co2_per_km_kg: float = 0.85,
        co2_price_sek_per_kg: float = 1.5,
        max_vehicle_tons: float = 20.0,
        min_match_tons: float = 0.5,
    ) -> Dict[str, Any]:
        """
        供需匹配 - 多目标优化版（profit-aware greedy assignment）

        升级点（对比 basic_matching）：
        1. Haversine 距离计算 — 真实考虑运输成本/碳排放
        2. Profit-aware 排序 — profit_per_ton_km 越高越优先
        3. Greedy 1-to-1 — 每个 supply/demand 最多被用一次（避免重复锁库存）
        4. Profit 计算包含：revenue - transport_cost - co2_cost

        Args:
            supply_offers: 每个含 agent_id / material_type / available_tons / location
            demand_requests: 每个含 id / preferred_materials / demand_tons / location
            cost_per_km_sek: 车辆运输成本 (默认 2.6 SEK/km)
            co2_per_km_kg: 排放率 (默认 0.85 kg CO2/km)
            co2_price_sek_per_kg: 碳价 (默认 1.5 SEK/kg, 近似 EU ETS)
            max_vehicle_tons: 单车容量上限（避免 OR-Tools 不可解）
            min_match_tons: 最小匹配量（过滤碎屑以减少 solver 节点）

        Returns:
            {total_matches, total_tons, matches[], optimization_status}
            matches[] 每项含 supply_id / demand_id / material_type / tons /
                       distance_km / transport_cost_sek / co2_cost_sek /
                       revenue_sek / estimated_profit_sek / profit_per_ton_km
        """
        MAX_VEHICLE_TONS = max_vehicle_tons
        MIN_MATCH_TONS = min_match_tons

        # Step 1: 生成所有可行的 (s, d) 候选 pair
        candidates: List[Dict[str, Any]] = []
        for s_idx, supply in enumerate(supply_offers):
            sup_loc = supply.get("location") or {}
            sup_lat = sup_loc.get("lat")
            sup_lon = sup_loc.get("lon")
            if sup_lat is None or sup_lon is None:
                continue
            sup_mat = supply.get("material_type")
            sup_avail = supply.get("available_tons", 0)
            if sup_avail < MIN_MATCH_TONS:
                continue
            price_per_ton = self.material_prices.get(sup_mat, 0)

            for d_idx, demand in enumerate(demand_requests):
                preferred = demand.get("preferred_materials") or []
                if sup_mat not in preferred:
                    continue
                dem_loc = demand.get("location") or {}
                dem_lat = dem_loc.get("lat")
                dem_lon = dem_loc.get("lon")
                if dem_lat is None or dem_lon is None:
                    continue

                # Haversine 距离 (km)
                dist_km = _haversine_km(sup_lat, sup_lon, dem_lat, dem_lon)

                demand_tons = demand.get(
                    "demand_tons",
                    demand.get("current_demand_tons", 0),
                )
                if demand_tons < MIN_MATCH_TONS:
                    continue

                # match tons = min(supply_avail, demand, single vehicle cap)
                tons = min(sup_avail, demand_tons, MAX_VEHICLE_TONS)
                if tons < MIN_MATCH_TONS:
                    continue

                # 成本拆解
                transport_cost = tons * dist_km * cost_per_km_sek
                co2_kg = tons * dist_km * co2_per_km_kg
                co2_cost = co2_kg * co2_price_sek_per_kg
                revenue = tons * price_per_ton
                profit = revenue - transport_cost - co2_cost
                # 越高越优先（profit / (tons × km)）
                profit_intensity = profit / max(tons * dist_km, 0.01)

                candidates.append({
                    "supply_id": supply["agent_id"],
                    "demand_id": demand["id"],
                    "material_type": sup_mat,
                    "tons": round(tons, 2),
                    "distance_km": round(dist_km, 2),
                    "transport_cost_sek": round(transport_cost, 2),
                    "co2_kg": round(co2_kg, 2),
                    "co2_cost_sek": round(co2_cost, 2),
                    "revenue_sek": round(revenue, 2),
                    "estimated_profit_sek": round(profit, 2),
                    "profit_per_ton_km": round(profit_intensity, 4),
                    "_s_idx": s_idx,
                    "_d_idx": d_idx,
                })

        # Step 2: 按 profit_per_ton_km 降序排序
        candidates.sort(key=lambda c: c["profit_per_ton_km"], reverse=True)

        # Step 3: Greedy assignment — 每个 supply / demand 最多被选中一次
        used_supply: set = set()
        used_demand: set = set()
        matches: List[Dict[str, Any]] = []
        for c in candidates:
            if c["supply_id"] in used_supply or c["demand_id"] in used_demand:
                continue
            used_supply.add(c["supply_id"])
            used_demand.add(c["demand_id"])
            # 去掉内部 index 字段
            match = {k: v for k, v in c.items() if not k.startswith("_")}
            matches.append(match)

        # Step 4: 统计与状态
        total_tons = sum(m["tons"] for m in matches)
        total_distance = sum(m["distance_km"] * m["tons"] for m in matches)
        total_profit = sum(m["estimated_profit_sek"] for m in matches)
        total_co2 = sum(m["co2_kg"] for m in matches)
        # 状态判断：按 profit 阈值和 match 数量
        if not matches:
            status = "no_matches"
        elif total_profit > 0 and len(matches) >= max(1, len(supply_offers) // 2):
            status = "optimized"
        elif total_profit > 0:
            status = "partial_optimized"
        else:
            status = "loss_making"

        logger.info(
            f"供需匹配: {len(matches)} matches / {total_tons:.1f}t / "
            f"{total_distance:.1f}t·km / profit={total_profit:.0f} SEK / "
            f"co2={total_co2:.1f}kg / status={status}"
        )

        return {
            "total_matches": len(matches),
            "total_tons": round(total_tons, 2),
            "total_distance_ton_km": round(total_distance, 2),
            "total_profit_sek": round(total_profit, 2),
            "total_co2_kg": round(total_co2, 2),
            "matches": matches,
            "optimization_status": status,
        }
    
    async def predict_demand(
        self,
        days: int = 1,
        sim_day: int = 0,
        weekday: int = 0,
    ) -> Dict[str, Any]:
        """
        LLM-driven 需求预测。

        单次 Gemini 调用: 让模型根据上下文 (region、material、weekday、season)
        给所有需求点一个 next-day multiplier (+ trend + reason)。
        失败 fallback 到简单的 deterministic 估计。

        预算:
          - 1 LLM call / cycle,30 days = 30 calls,远低于 250 RPD 限制
          - 8 分钟仿真 3.75 calls/min,低于 10 RPM 限制
        """
        from .llm_caller import call_gemini, GeminiAPIError
        import json as _json
        import re as _re

        # 构造简明上下文 (为了节省 token)
        demand_summaries = [
            {
                "id": dp["id"],
                "name": dp.get("name", dp["id"]),
                "city": dp.get("city", "unknown"),
                "preferred_materials": dp.get("preferred_materials", []),
                "base_demand_tons": dp.get(
                    "base_demand_tons", dp.get("current_demand_tons", 0)
                ),
            }
            for dp in self.demand_points
        ]
        weekday_name = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][weekday % 7]
        system_inst = (
            "You are a senior logistics analyst for a Swedish circular-economy "
            "waste-recycling network. You give concise, evidence-based demand "
            "forecasts. Always respond with strictly valid JSON — no prose, no "
            "code fences, no trailing commas."
        )
        user_prompt = (
            f"Forecast next-day demand multipliers for a Swedish waste-recycling network.\n\n"
            f"**Context**\n"
            f"- Sim day: {sim_day} ({weekday_name})\n"
            f"- Region: Borås / Göteborg / Stockholm triangle, Sweden\n"
            f"- Season: summer (long days, active construction)\n"
            f"- Demand points: {len(demand_summaries)}\n\n"
            f"**Demand points (JSON)**:\n"
            f"{_json.dumps(demand_summaries, ensure_ascii=False, indent=2)}\n\n"
            f"**Task**: For EACH demand point, predict a next-day multiplier:\n"
            f"- `id`: demand point id (keep as-is)\n"
            f"- `multiplier`: float in [0.5, 1.5] — next-day demand vs base\n"
            f"- `trend`: one of \"rising\", \"stable\", \"falling\"\n"
            f"- `confidence`: float in [0.0, 1.0]\n"
            f"- `reason`: 1 short sentence (≤15 words) explaining\n\n"
            f"**Output**: Return ONLY a JSON array. Example:\n"
            f'[{{"id":"DEM001","multiplier":1.1,"trend":"rising","confidence":0.8,"reason":"Construction season peak"}}]'
        )

        try:
            text = call_gemini(
                user_prompt,
                system_instruction=system_inst,
                max_tokens=2048,
            )
            # 去掉 ```json fences
            text = text.strip()
            text = _re.sub(r"^```(?:json)?\s*", "", text)
            text = _re.sub(r"\s*```\s*$", "", text)
            raw_list = _json.loads(text)
            if not isinstance(raw_list, list):
                raise ValueError(f"expected JSON array, got {type(raw_list).__name__}")

            predictions = []
            for p in raw_list:
                if not isinstance(p, dict) or "id" not in p or "multiplier" not in p:
                    continue
                m = float(p["multiplier"])
                m = max(0.3, min(1.8, m))  # 安全 clamp
                predictions.append({
                    "id": str(p["id"]),
                    "multiplier": round(m, 3),
                    "trend": str(p.get("trend", "stable"))[:16],
                    "confidence": round(float(p.get("confidence", 0.5)), 2),
                    "reason": str(p.get("reason", ""))[:200],
                    "source": "llm",
                })
            if not predictions:
                raise ValueError("LLM returned no valid predictions")
            return {
                "predictions": predictions,
                "source": "llm",
                "sim_day": sim_day,
                "weekday": weekday,
            }
        except (GeminiAPIError, _json.JSONDecodeError, ValueError, KeyError) as e:
            logger.warning(
                f"predict_demand LLM call failed ({type(e).__name__}: {e}), "
                f"falling back to deterministic estimate"
            )
            # Fallback: 跟之前 _compute_demand_multiplier 一致
            fallback_mult = 0.85 if weekday >= 5 else 1.0
            return {
                "predictions": [
                    {
                        "id": dp["id"],
                        "multiplier": fallback_mult,
                        "trend": "stable",
                        "confidence": 0.4,
                        "reason": "fallback: LLM unavailable",
                        "source": "fallback",
                    }
                    for dp in self.demand_points
                ],
                "source": "fallback",
                "sim_day": sim_day,
                "weekday": weekday,
                "error": str(e),
            }
    
    def get_tools(self) -> list:
        """返回智能体可用的工具"""
        return [
            self.get_demand_status,
            self.get_material_price,
            self.calculate_profit,
            self.match_supply_demand,
            self.predict_demand
        ]


# ============================================
# 使用示例
# ============================================
if __name__ == "__main__":
    async def main():
        # 创建市场智能体
        market = MarketAgent()
        
        # 获取需求状态
        demand = await market.get_demand_status()
        print(f"需求状态：{demand}")
        
        # 获取材料价格
        price = await market.get_material_price("mixed_waste")
        print(f"价格：{price}")
        
        # 计算利润
        profit = await market.calculate_profit(
            material_type="mixed_waste",
            tons=10.0,
            transport_cost_sek=500,
            co2_cost_sek=100
        )
        print(f"利润：{profit}")
    
    asyncio.run(main())
