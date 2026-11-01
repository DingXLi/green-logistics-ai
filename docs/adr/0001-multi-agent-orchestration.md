# ADR-0001: 多智能体编排 —— ADK 定义 Agent，手写 Coordinator 编排

- **状态**：已接受
- **记录日期**：2026-11-01
- **相关代码**：`agents/coordinator.py`, `agents/supply_agent.py`, `agents/market_agent.py`,
  `agents/logistics_agent.py`, `agents/clock.py`

## 背景

系统要跑一个「废料供应 → 供需匹配 → 车辆路线」的三段闭环，每个环节的判断都要满足三件事：

1. **可单独测** —— 供应预测错在哪，不能靠跑完整仿真才能发现；
2. **可复现** —— 同一份输入 + 同一个种子，结果必须一致（客户拿到的报表不能每次都不一样）；
3. **LLM 与确定性计算分离** —— LLM 只负责「判断」（预测、匹配倾向、路线偏好），
   VRP 求解、KPI 计算、落盘这些必须是纯 Python，出了问题能二分定位。

额外约束：仿真要能连跑上千个 cycle，每个 cycle 都要落盘、都能回溯。

## 决策

**用 google.adk 的 `Agent` 定义三个领域 agent，用手写的 `MultiAgentCoordinator` 做编排；
刻意不引入 LangGraph / CrewAI，也不使用 ADK 自带的 SequentialAgent / LoopAgent 编排。**

具体形态：

- 三个 agent 各自是独立类（`SupplyAgent` / `MarketAgent` / `LogisticsAgent`），
  内部各建一个 `google.adk.Agent`，模型名统一从 `agents/llm_config.py` 的 `MODEL` 取，
  杜绝 model 名散落（见 [ADR-0003](0003-llm-model-and-config.md)）。
- 编排是 `agents/coordinator.py` 里手写的 async 代码（29,696 B），
  依赖（`WorldBuilder` / `SimClock` / `Persistence`）由 Coordinator **一次性集中注入**。
- 一个 cycle 的顺序是显式写死的：

  ```
  clock.advance_day()
    → 每个 SupplyAgent.accumulate_stock(factor)
    → 收集 supply_offers / demand_requests
    → market_agent.match_supply_demand()
    → logistics_agent.optimize_routes()
    → persistence 落盘 KPI + 子数据
  ```

- 每次 LLM 判断连同 fallback 一起写进 `llm_decisions` 表，用 `source` 字段区分 `'llm' | 'fallback'`，
  决策永远可追溯（见 [ADR-0004](0004-llm-resilience-and-observability.md)）。

## 备选方案

- **LangGraph 状态图** —— 节点/边显式、checkpoint 生态成熟，是当下 multi-agent 的主流答案。
  否掉的原因：我们的闭环本质是「顺序 + 时钟推进 + 落盘」，用状态图表达每个 cycle 的 state
  会多出一层建模成本；而且引入新框架意味着团队要额外学一套心智模型，换来的 checkpoint/resume
  在当前场景里没被真正用到。
- **CrewAI role-based** —— 读起来像「角色扮演」，跟业务语义贴合。
  否掉的原因：我们要的是**可复现的数据流**，不是角色对话；角色扮演式框架的顺序与确定性更难保证。
- **ADK SequentialAgent / LoopAgent** —— 最贴 ADK 生态。
  否掉的原因：它们的调度语义跟「仿真时钟 + 每 cycle 落盘」不匹配，落盘和 KPI 提取仍要自己写钩子，
  反而多一层间接。

## 后果

**正面**

- 编排顺序完全确定 → 同一随机种子下结果 **bit-reproducible**
  （`tests/test_agents.py::test_build_reproducible` 背书）。
- 每个 agent 能独立单测 —— 仓库 118 个测试文件、1,600+ 用例，一半以上是 agent 级别的。
- 没有框架黑盒：线上/仿真出问题时，能直接断点进 cycle 循环看每一步。
- 与 ADK 保持「用到就用」的关系 —— 用了 `Agent` + `tools`，其余按需。

**负面 / 技术债**

- ADK 只吃了 `Agent` + `tools` 两块能力，`Runner` / session / callback 这些没深入，
  在「ADK 到底熟到什么程度」这个问题上目前只能答到这一层。
- 自己实现编排 = checkpoint / resume、并行 fan-out 都要自己写；
  一旦需要多阶段异步编排，改动量会比用现成框架大。

---

_相关：[ADR-0002](0002-vrp-optimization-core.md) 优化内核 · [ADR-0005](0005-persistence-sqlite.md) 落盘_
