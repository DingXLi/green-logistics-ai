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
from loguru import logger

from .llm_config import MODEL  # 中心化 model 名 (env > yaml > 默认)


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
        """获取材料价格"""
        price = self.material_prices.get(material_type, 0)
        return {
            "material_type": material_type,
            "price_sek_per_ton": price,
            "price_trend": "stable",  # TODO: 实现价格趋势分析
            "last_updated": datetime.now().isoformat()
        }
    
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
        demand_requests: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        匹配供需
        
        TODO: 实现优化算法考虑：
        - 运输距离
        - 材料类型匹配
        - 价格优化
        - 碳排放最小化
        """
        matches = []
        
        for supply in supply_offers:
            for demand in demand_requests:
                # 检查材料类型是否匹配
                if supply.get("material_type") in demand.get("preferred_materials", []):
                    # Cap at typical vehicle capacity (20t) so VRP 不超过单车上限
                    # 多出来的需求可以由后续供应或下一天补上
                    MAX_VEHICLE_TONS = 20.0
                    # 跳过碎屑 match（< 0.5t），减少 OR-Tools 的节点压力，
                    # 让 solver 更集中于“真正的业务量”，避免 no_solution。
                    MIN_MATCH_TONS = 0.5
                    match_tons = min(
                        supply.get("available_tons", 0),
                        demand.get("demand_tons", demand.get("current_demand_tons", 0)),
                        MAX_VEHICLE_TONS,
                    )
                    if match_tons >= MIN_MATCH_TONS:
                        matches.append({
                            "supply_id": supply["agent_id"],
                            "demand_id": demand["id"],
                            "material_type": supply["material_type"],
                            "tons": match_tons,
                            "distance_km": 0,  # TODO: 计算实际距离
                            "estimated_profit_sek": match_tons * self.material_prices.get(supply["material_type"], 0) * 0.3
                        })
        
        return {
            "total_matches": len(matches),
            "total_tons": sum(m["tons"] for m in matches),
            "matches": matches,
            "optimization_status": "basic_matching"  # TODO: 升级为优化算法
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
