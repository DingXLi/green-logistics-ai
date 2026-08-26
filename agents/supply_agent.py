"""
供应智能体 (Supply Agent)

负责：
- 监控废料供应点的库存水平
- 预测未来供应量
- 与市场智能体协调供需匹配
"""

from google.adk import Agent
from google.adk import tools
from typing import Optional, Dict, Any, List
from datetime import datetime
import asyncio

from .llm_config import MODEL  # 中心化 model 名 (env > yaml > 默认)


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
            model=MODEL,
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
        """
        预测未来 N 天的供应量。

        升级点 (对比之前 TODO 简化版):
        - 复用 predict_supply_batch() 拿 LLM multiplier (趋势驱动)
        - 没 GOOGLE_API_KEY 或 LLM 报错时走 deterministic fallback
        - 给出 trend / reason, 不仅仅是 daily_capacity * 0.8
        """
        try:
            from .clock import SimClock  # local import 避免循环
            from datetime import datetime
            weekday = datetime.now().weekday()
            sim_day = days  # best-effort, callers should pass real sim_day
        except Exception:
            weekday, sim_day = 0, 0

        try:
            llm_pred = await SupplyAgent.predict_supply_batch(
                agents=[self],
                days=1,
                sim_day=sim_day,
                weekday=weekday,
            )
            meta = llm_pred.get(self.agent_id, {})
        except Exception:
            meta = {}

        multiplier = float(meta.get("multiplier", 0.8))
        # batch 默认返回 multiplier=1.0 fallback；这里需要 baseline 0.8 形式
        # 让 LLM multiplier 压到 0.3–1.6 区间，避免预测腿大
        baseline = 0.8
        effective_multiplier = baseline * multiplier  # LLM 影响 baseline

        daily_prediction = self.daily_capacity * effective_multiplier
        total_tons = daily_prediction * days

        return {
            "agent_id": self.agent_id,
            "prediction_days": days,
            "daily_avg_tons": round(daily_prediction, 2),
            "total_tons": round(total_tons, 2),
            "confidence": meta.get("confidence", 0.7),
            "trend": meta.get("trend", "stable"),
            "reason": meta.get("reason", ""),
            "source": meta.get("source", "fallback"),
            "multiplier": round(effective_multiplier, 3),
        }

    @classmethod
    async def predict_supply_batch(
        cls,
        agents: List["SupplyAgent"],
        days: int = 1,
        sim_day: int = 0,
        weekday: int = 0,
    ) -> Dict[str, Dict[str, Any]]:
        """
        LLM-driven 供应预测 (单次调用) 返回所有 supply 点的 multiplier。

        Args:
            agents: 同一世界里的 SupplyAgent 列表
            days: 预测天数 (1 = next day)
            sim_day: 当前 sim day (给 LLM 上文)
            weekday: 0-6 (Mon-Sun)

        Returns:
            {agent_id: {multiplier, trend, confidence, reason, source}}

        Fallback: LLM 调不到 / JSON 错 / 任意 4xx → 统一 multiplier=1.0
        """
        from .llm_caller import call_gemini, GeminiAPIError
        import json as _json
        import re as _re

        if not agents:
            return {}

        supply_summaries = [
            {
                "id": a.agent_id,
                "material_type": a.material_type,
                "current_stock_tons": round(a.current_stock, 2),
                "daily_capacity_tons": round(a.daily_capacity, 2),
                "location": a.location,
            }
            for a in agents
        ]
        weekday_name = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][weekday % 7]
        system_inst = (
            "You are a senior waste-supply analyst for a Swedish circular-economy "
            "logistics network. You give concise, evidence-based next-day "
            "accumulation-rate forecasts. Always respond with strictly valid JSON — "
            "no prose, no code fences, no trailing commas."
        )
        user_prompt = (
            f"Forecast next-day stock-accumulation multipliers for a Swedish waste-recycling network.\n\n"
            f"**Context**\n"
            f"- Sim day: {sim_day} ({weekday_name})\n"
            f"- Region: Borås / Göteborg / Stockholm triangle, Sweden\n"
            f"- Season: summer (active construction, more waste generated)\n"
            f"- Supply points: {len(supply_summaries)}\n\n"
            f"**Supply points (JSON)**:\n"
            f"{_json.dumps(supply_summaries, ensure_ascii=False, indent=2)}\n\n"
            f"**Task**: For EACH supply point, predict a next-day accumulation multiplier:\n"
            f"- `id`: supply point id (keep as-is)\n"
            f"- `multiplier`: float in [0.3, 1.8] — how much MORE or LESS than baseline to accumulate tomorrow\n"
            f"  (1.0 = baseline, 1.5 = 50% more, 0.5 = 50% less, <0.3 = no accumulation today)\n"
            f"- `trend`: one of \"rising\", \"stable\", \"falling\"\n"
            f"- `confidence`: float in [0.0, 1.0]\n"
            f"- `reason`: 1 short sentence (≤15 words) explaining\n\n"
            f"**Output**: Return ONLY a JSON array. Example:\n"
            f'[{{"id":"SUP000","multiplier":1.2,"trend":"rising","confidence":0.8,"reason":"Construction peak"}}]'
        )

        try:
            text = call_gemini(
                user_prompt,
                system_instruction=system_inst,
                max_tokens=2048,
            )
            text = text.strip()
            text = _re.sub(r"^```(?:json)?\s*", "", text)
            text = _re.sub(r"\s*```\s*$", "", text)
            raw_list = _json.loads(text)
            if not isinstance(raw_list, list):
                raise ValueError(f"expected JSON array, got {type(raw_list).__name__}")

            predictions: Dict[str, Dict[str, Any]] = {}
            for p in raw_list:
                if not isinstance(p, dict) or "id" not in p or "multiplier" not in p:
                    continue
                m = float(p["multiplier"])
                m = max(0.1, min(2.0, m))  # supply side: wider clamp
                aid = str(p["id"])
                predictions[aid] = {
                    "id": aid,
                    "multiplier": round(m, 3),
                    "trend": str(p.get("trend", "stable"))[:16],
                    "confidence": round(float(p.get("confidence", 0.5)), 2),
                    "reason": str(p.get("reason", ""))[:200],
                    "source": "llm",
                }
            if not predictions:
                raise ValueError("LLM returned no valid predictions")
            return predictions
        except (GeminiAPIError, _json.JSONDecodeError, ValueError, KeyError) as e:
            import logging
            logging.warning(
                f"predict_supply_batch LLM call failed ({type(e).__name__}: {e}), "
                f"falling back to uniform multiplier=1.0"
            )
            return {
                a.agent_id: {
                    "id": a.agent_id,
                    "multiplier": 1.0,
                    "trend": "stable",
                    "confidence": 0.4,
                    "reason": "fallback: LLM unavailable",
                    "source": "fallback",
                }
                for a in agents
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
            # Bug fix: daily_capacity may be 0/unset → avoid ZeroDivisionError.
            if self.daily_capacity <= 0:
                estimated_wait_hours = None
                reason = "daily_capacity_unset"
            else:
                estimated_wait_hours = (min_load_tons - self.current_stock) / (self.daily_capacity / 24)
                reason = None
            return {
                "status": "insufficient",
                "current_tons": self.current_stock,
                "required_tons": min_load_tons,
                "estimated_wait_hours": estimated_wait_hours,
                "reason": reason,
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

    def accumulate_stock(
        self,
        factor: float = 1.0,
        llm_multiplier: float = 1.0,
        seasonal_multiplier: float = 1.0,
    ) -> None:
        """每个 cycle 调用：模拟一天自然积累库存。

        Args:
            factor: SimClock.activity_factor (昼夜 0.5/1.5x)
            llm_multiplier: LLM 预测的 multiplier (默认 1.0 = 无影响),
                            由 SupplyAgent.predict_supply_batch 给出
            seasonal_multiplier: 月度季节因子 (默认 1.0 = 无影响),
                                 建筑夏高冬低 (1.4 ↔ 0.4)
        """
        self.current_stock = round(
            self.current_stock
            + self.daily_capacity * 0.5 * factor * llm_multiplier * seasonal_multiplier,
            2,
        )

    def consume_shipped(self, shipped_tons: float, hard_cap_tons: float = 30.0) -> None:
        """每个 cycle 末尾调用：根据实际被匹配的出运量扣减库存。

        设计动机：原实现只有 accumulate，没有 consume，导致 30 天后库存
        持续递增到远大于单车容量，VRP 在单车 20t cap 下把 total_tons
        锁死成“1 车 1 趟 20t × N”的平台值。引入本方法后，库存可与
        仿真形成 quasi-steady 状态，total_tons 会随扰动真正变化。

        hard_cap_tons 是安全上限，防止单点库存失控（默认 30t）。
        """
        if shipped_tons and shipped_tons > 0:
            self.current_stock = round(max(0.0, self.current_stock - shipped_tons), 2)
        # 安全网：就算 consume 不平衡（极端扰动），也不要让单点涨到荒谬的值
        if self.current_stock > hard_cap_tons:
            self.current_stock = hard_cap_tons

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
