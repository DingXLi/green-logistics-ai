"""共享的 fake 世界(测试用) —— iter #86 (ADR-0018)

为什么要有这个模块
------------------
``/api/optimize/sweet-spot`` 和 ``/api/optimize/carbon-scenarios`` 这两个端点
都需要一个「有 supply agent / market agent / logistics agent」的 coordinator。

以前每个测试文件**各造一遍**自己的 fake —— 而且两边的 fake 字段还悄悄对不上。
后果很具体: 端点行为不确定 → 断言只能写成 ``in (200, 503)`` 这种
「许愿式断言」(端点整个挂掉, 测试照样绿)。ADR-0018 要消灭的正是这一类。

现在: **一份 fake 世界, 两处共用**, 端点行为确定 → 断言可以精确到唯一结果。

设计约束
--------
- 不依赖真实路网(``use_real_roads=false`` 即可), 不调 LLM, 不连外网。
- 字段要和端点真的用到的字段一一对应:
  ``supply_agents[*].{agent_id, material_type, daily_capacity, location}`` ·
  ``market_agent.demand_points[*].{id, name, current_demand_tons,
  preferred_materials, location, material_type}`` ·
  ``market_agent.match_supply_demand()`` ·
  ``logistics_agent.{depot_location, vehicles[*]}``。
"""

from __future__ import annotations

from typing import Any, Dict, List


class FakeMarketAgent:
    """一个最小的 market agent: 1 个需求点, 匹配到唯一的 supply。"""

    def __init__(self) -> None:
        self.demand_points: List[Dict[str, Any]] = [
            {
                "id": "D1",
                "name": "Demand 1",
                "current_demand_tons": 10.0,
                "preferred_materials": ["concrete"],
                "location": {"lat": 57.72, "lon": 12.94},
                "material_type": "concrete",
            },
        ]

    async def match_supply_demand(self, supply_offers, demand_requests):
        """固定匹配: SUP1 → D1, 5 吨。"""
        return {
            "matches": [{
                "supply_id": "SUP1",
                "demand_id": "D1",
                "tons": 5.0,
            }]
        }


class FakeSupply:
    """一个最小的 supply agent。"""

    agent_id = "SUP1"
    material_type = "concrete"
    daily_capacity = 10.0
    location = {"lat": 57.72, "lon": 12.94}


class FakeLogisticsAgent:
    """一个最小的 logistics agent: 1 个 depot + 1 辆可用车。"""

    depot_location = {"lat": 57.72, "lon": 12.94}
    vehicles = [
        {
            "vehicle_id": "V1",
            "status": "available",
            "capacity_tons": 20.0,
            "co2_emission_rate": 0.85,
        },
    ]


class FakeCoordinator:
    """端点眼里的 coordinator: 只有这三个属性, 但够跑通优化链路。"""

    def __init__(self) -> None:
        self.market_agent = FakeMarketAgent()
        self.supply_agents = {"SUP1": FakeSupply()}
        self.logistics_agent = FakeLogisticsAgent()


def build_fake_coordinator() -> FakeCoordinator:
    """造一个可预测的 coordinator(每个测试文件共用同一份语义)。"""
    return FakeCoordinator()
