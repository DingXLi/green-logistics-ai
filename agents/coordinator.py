"""
多智能体协调器 (Multi-Agent Coordinator)

负责：
- 协调 Supply、Market、Logistics 智能体之间的通信
-  orchestrating 整体优化流程
- 提供统一的 API 接口
"""

import asyncio
from typing import List, Dict, Any
from datetime import datetime
from loguru import logger

from .supply_agent import SupplyAgent
from .market_agent import MarketAgent
from .logistics_agent import LogisticsAgent


class MultiAgentCoordinator:
    """
    多智能体系统协调器
    """
    
    def __init__(self):
        logger.info("初始化多智能体协调器...")
        
        # 初始化各个智能体
        self.supply_agents: Dict[str, SupplyAgent] = {}
        self.market_agent = MarketAgent()
        self.logistics_agent = LogisticsAgent(fleet_size=10)
        
        # 系统状态
        self.system_status = {
            "initialized_at": datetime.now().isoformat(),
            "status": "running",
            "total_optimizations": 0,
            "last_optimization": None
        }
        
        logger.info("多智能体协调器初始化完成")
    
    def register_supply_agent(self, agent_id: str, location: Dict[str, float]):
        """注册供应智能体"""
        self.supply_agents[agent_id] = SupplyAgent(agent_id, location)
        logger.info(f"注册供应智能体：{agent_id}")
    
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
            "supply_points": len(self.supply_agents),
            "supply_status": supply_status,
            "fleet_status": fleet_status,
            "demand_points": len(demand_status),
            "demand_status": demand_status
        }
    
    async def run_optimization_cycle(self) -> Dict[str, Any]:
        """
        运行一次完整的优化周期
        
        流程：
        1. 收集所有供应点的库存和预测
        2. 收集所有需求点的需求
        3. 匹配供需
        4. 优化物流路径
        5. 返回优化结果
        """
        logger.info("开始优化周期...")
        
        # 1. 收集供应信息
        supply_offers = []
        for agent_id, agent in self.supply_agents.items():
            stock = await agent.get_current_stock()
            prediction = await agent.predict_supply(days=1)
            supply_offers.append({
                "agent_id": agent_id,
                "available_tons": stock["stock_tons"],
                "predicted_tons": prediction["total_tons"],
                "material_type": stock["material_type"],
                "location": stock["location"]
            })
        
        # 2. 收集需求信息
        demand_status = await self.market_agent.get_demand_status()
        demand_requests = [
            {
                "id": dp["id"],
                "demand_tons": dp["current_demand_tons"],
                "preferred_materials": dp["preferred_materials"],
                "location": dp["location"]
            }
            for dp in demand_status
        ]
        
        # 3. 匹配供需
        matches = await self.market_agent.match_supply_demand(
            supply_offers=supply_offers,
            demand_requests=demand_requests
        )
        
        # 4. 优化物流路径
        if matches["total_matches"] > 0:
            pickup_locations = []
            delivery_locations = []
            
            for m in matches["matches"]:
                # 获取供应点坐标
                supply_loc = None
                for agent_id, agent in self.supply_agents.items():
                    if agent_id == m["supply_id"]:
                        stock = await agent.get_current_stock()
                        supply_loc = stock["location"]
                        break
                
                # 获取需求点坐标
                demand_loc = None
                for dp in demand_status:
                    if dp["id"] == m["demand_id"]:
                        demand_loc = dp["location"]
                        break
                
                pickup_locations.append({
                    "id": m["supply_id"],
                    "tons": m["tons"],
                    "lat": supply_loc["lat"] if supply_loc else 57.7,
                    "lon": supply_loc["lon"] if supply_loc else 14.2
                })
                delivery_locations.append({
                    "id": m["demand_id"],
                    "tons": m["tons"],
                    "lat": demand_loc["lat"] if demand_loc else 57.7,
                    "lon": demand_loc["lon"] if demand_loc else 14.2
                })
            
            route_optimization = await self.logistics_agent.optimize_routes(
                pickup_locations=pickup_locations,
                delivery_locations=delivery_locations
            )
        else:
            route_optimization = {"status": "no_matches", "message": "No supply-demand matches found"}
        
        # 5. 更新系统状态
        self.system_status["total_optimizations"] += 1
        self.system_status["last_optimization"] = datetime.now().isoformat()
        
        result = {
            "optimization_id": f"OPT{self.system_status['total_optimizations']:04d}",
            "timestamp": datetime.now().isoformat(),
            "supply_offers_count": len(supply_offers),
            "demand_requests_count": len(demand_requests),
            "matches": matches,
            "route_optimization": route_optimization,
            "system_status": self.system_status
        }
        
        logger.info(f"优化周期完成：{result['optimization_id']}")
        return result
    
    async def simulate_day(self, days: int = 1) -> List[Dict[str, Any]]:
        """
        模拟运行 N 天
        
        每天：
        - 更新供应点库存
        - 更新需求点需求
        - 运行优化
        """
        results = []
        
        for day in range(days):
            logger.info(f"模拟第 {day + 1} 天...")
            
            # 更新供应（简化：每天增加固定量）
            for agent in self.supply_agents.values():
                agent.current_stock += agent.daily_capacity * 0.8
            
            # 运行优化
            daily_result = await self.run_optimization_cycle()
            daily_result["simulation_day"] = day + 1
            results.append(daily_result)
            
            # 等待一小段时间（模拟时间流逝）
            await asyncio.sleep(0.1)
        
        return results


# ============================================
# 主程序入口
# ============================================
async def main():
    """测试多智能体协调器"""
    
    # 创建协调器
    coordinator = MultiAgentCoordinator()
    
    # 注册供应点（示例：瑞典各地）
    coordinator.register_supply_agent("SUP001", {"lat": 57.7089, "lon": 14.1618})  # Borås
    coordinator.register_supply_agent("SUP002", {"lat": 57.7089, "lon": 11.9746})  # Gothenburg
    coordinator.register_supply_agent("SUP003", {"lat": 59.3293, "lon": 18.0686})  # Stockholm
    
    # 设置供应能力
    for agent in coordinator.supply_agents.values():
        agent.current_stock = 15.0  # 初始库存
        agent.daily_capacity = 20.0  # 日产能
    
    # 获取系统概览
    overview = await coordinator.get_system_overview()
    print("\n" + "="*60)
    print("系统概览")
    print("="*60)
    import json
    print(json.dumps(overview, indent=2, ensure_ascii=False))
    
    # 运行一次优化
    print("\n" + "="*60)
    print("运行优化")
    print("="*60)
    optimization = await coordinator.run_optimization_cycle()
    print(json.dumps(optimization, indent=2, ensure_ascii=False))
    
    # 模拟 3 天
    print("\n" + "="*60)
    print("模拟 3 天运行")
    print("="*60)
    simulation = await coordinator.simulate_day(days=3)
    print(f"完成 {len(simulation)} 天的模拟")
    print(f"总优化次数：{simulation[-1]['system_status']['total_optimizations']}")


if __name__ == "__main__":
    asyncio.run(main())
