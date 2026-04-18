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
            model="gemini-2.0-flash",
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
        """获取所有需求点状态"""
        return [
            {
                "id": dp["id"],
                "name": dp["name"],
                "current_demand_tons": dp["current_demand_tons"],
                "capacity_tons": dp["daily_capacity_tons"],
                "utilization_rate": dp["current_demand_tons"] / dp["daily_capacity_tons"] * 100,
                "preferred_materials": dp["preferred_materials"],
                "location": dp["location"]
            }
            for dp in self.demand_points
        ]
    
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
                    match_tons = min(
                        supply.get("available_tons", 0),
                        demand.get("demand_tons", demand.get("current_demand_tons", 0))
                    )
                    if match_tons > 0:
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
    
    async def predict_demand(self, days: int = 7) -> Dict[str, Any]:
        """预测未来需求"""
        # TODO: 实现基于历史数据和市场趋势的预测
        predictions = []
        for dp in self.demand_points:
            predictions.append({
                "demand_point_id": dp["id"],
                "daily_avg_tons": dp["daily_capacity_tons"] * 0.6,
                "confidence": 0.75,
                "trend": "stable"
            })
        
        return {
            "prediction_days": days,
            "demand_points": predictions,
            "total_daily_demand_tons": sum(p["daily_avg_tons"] for p in predictions)
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
