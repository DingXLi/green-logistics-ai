"""
供应智能体 (Supply Agent)

负责：
- 监控废料供应点的库存水平
- 预测未来供应量
- 与市场智能体协调供需匹配
"""

from google.adk import Agent
from google.adk import tools
from typing import Optional, Dict, Any
from datetime import datetime
import asyncio


class SupplyAgent:
    """
    供应智能体 - 管理废料供应源
    """
    
    def __init__(self, agent_id: str, location: Dict[str, float]):
        self.agent_id = agent_id
        self.location = location  # {lat, lon}
        self.current_stock = 0.0  # 吨
        self.daily_capacity = 0.0  # 吨/天
        self.material_type = "mixed_waste"  # 废料类型
        self.moisture_percent = 25.0
        self.quality_score = 75.0
        
        # 创建 ADK Agent
        self.agent = Agent(
            name=f"supply_{agent_id}",
            model="gemini-2.0-flash",
            description=f"Supply agent for location {agent_id}",
            instruction=self._get_instruction(),
        )
    
    def _get_instruction(self) -> str:
        return f"""
你是供应智能体 {self.agent_id}，负责管理废料供应源。

你的职责：
1. 监控当前库存水平
2. 预测未来 7 天的供应量
3. 报告可收集的废料量
4. 与物流智能体协调收集时间

当前位置：{self.location}
"""
    
    async def get_current_stock(self) -> Dict[str, Any]:
        """获取当前库存"""
        return {
            "agent_id": self.agent_id,
            "stock_tons": self.current_stock,
            "material_type": self.material_type,
            "timestamp": datetime.now().isoformat(),
            "location": self.location
        }
    
    async def predict_supply(self, days: int = 7) -> Dict[str, Any]:
        """预测未来 N 天的供应量"""
        # TODO: 实现基于历史数据的预测模型
        daily_prediction = self.daily_capacity * 0.8  # 简化版本
        
        return {
            "agent_id": self.agent_id,
            "prediction_days": days,
            "daily_avg_tons": daily_prediction,
            "total_tons": daily_prediction * days,
            "confidence": 0.85
        }
    
    async def request_collection(self, min_load_tons: float) -> Dict[str, Any]:
        """请求废料收集"""
        if self.current_stock >= min_load_tons:
            return {
                "status": "ready",
                "available_tons": self.current_stock,
                "location": self.location,
                "priority": "normal" if self.current_stock < self.daily_capacity else "high"
            }
        else:
            return {
                "status": "insufficient",
                "current_tons": self.current_stock,
                "required_tons": min_load_tons,
                "estimated_wait_hours": (min_load_tons - self.current_stock) / (self.daily_capacity / 24)
            }
    
    async def update_stock(self, collected_tons: float) -> bool:
        """更新库存（收集后）"""
        if collected_tons <= self.current_stock:
            self.current_stock -= collected_tons
            return True
        return False

    # ------------------------------------------------------------
    # 由 WorldBuilder / Coordinator 调用的状态注入接口
    # ------------------------------------------------------------

    def set_inventory(
        self,
        current_stock: float = None,
        daily_capacity: float = None,
        material_type: str = None,
        moisture_percent: float = None,
        quality_score: float = None,
    ) -> None:
        """从合成数据/外部世界注入库存和属性（替代手动赋值）"""
        if current_stock is not None:
            self.current_stock = current_stock
        if daily_capacity is not None:
            self.daily_capacity = daily_capacity
        if material_type is not None:
            self.material_type = material_type
        if moisture_percent is not None:
            self.moisture_percent = moisture_percent
        if quality_score is not None:
            self.quality_score = quality_score

    def accumulate_stock(self, factor: float = 1.0) -> None:
        """每个 cycle 调用：模拟一天自然积累库存（factor 取自 SimClock.activity_factor）"""
        self.current_stock = round(
            self.current_stock + self.daily_capacity * 0.5 * factor, 2
        )

    def get_tools(self) -> list:
        """返回智能体可用的工具"""
        # 注：新版 ADK 使用 FunctionTool，但简单场景可以直接调用方法
        # 这里返回方法引用供协调器调用
        return [
            self.get_current_stock,
            self.predict_supply,
            self.request_collection
        ]


# ============================================
# 使用示例
# ============================================
if __name__ == "__main__":
    async def main():
        # 创建供应智能体
        supply = SupplyAgent(
            agent_id="SUP001",
            location={"lat": 57.7089, "lon": 14.1618}  # 示例：瑞典某地
        )
        
        # 获取当前库存
        stock = await supply.get_current_stock()
        print(f"当前库存：{stock}")
        
        # 预测供应
        prediction = await supply.predict_supply(days=7)
        print(f"7 天预测：{prediction}")
        
        # 请求收集
        collection = await supply.request_collection(min_load_tons=5.0)
        print(f"收集请求：{collection}")
    
    asyncio.run(main())
