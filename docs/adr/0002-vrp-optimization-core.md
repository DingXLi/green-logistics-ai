# ADR-0002: VRP 优化内核 —— OR-Tools RoutingModel

- **状态**：已接受
- **记录日期**：2026-11-01
- **相关代码**：`optimization/vrp_solver.py`, `agents/logistics_agent.py`,
  `.github/workflows/ci-cd.yml`（slow-tests job）

## 背景

「把匹配上的供需分配给车队，选一组路线，使总里程/成本最低、约束尽量满足」
本质是带容量、时间窗、多起讫点约束的 **VRP（Vehicle Routing Problem）**。

场景特征：

- 供应点/需求点分散在城市坐标系里（经纬度），车辆数与容量随仿真日变化；
- 结果直接进 KPI（总成本 SEK、CO₂ kg、车队利用率 %），是客户最关心的三个数；
- 仿真要连跑上千个 cycle，**每次求解的耗时和结果稳定性都会放大**。

## 决策

**用 OR-Tools 的 `RoutingModel`（`ortools.constraint_solver.pywrapcp`）做 VRP 建模与求解**，
不自己写启发式，也不退化成贪心。

- 建模入口：`optimization/vrp_solver.py:225` 的 `routing = pywrapcp.RoutingModel(manager)`，
  容量、时间窗、多目标（里程/成本）在模型层表达，而不是在业务代码里 if/else 拼。
- OR-Tools 属于**重依赖**：本地与 CI 镜像都要装，因此相关测试全部标记 `slow`，
  CI 里只在 Python 3.12 跑一档（见 [ADR-0006](0006-ci-strategy-and-deterministic-fixture.md)），
  避免测试矩阵翻倍。

## 备选方案

- **贪心最近邻** —— 实现成本极低、速度快。否掉的原因：多车辆场景下总里程明显劣化，
  而总里程正是客户 KPI 之一，方案会在「成本最优」这个卖点上自相矛盾。
- **纯启发式 / 局部搜索（自己写）** —— 可控性最高。否掉的原因：要自己调参、自己保证收敛质量，
  调试成本远超收益；而 OR-Tools 的默认元启发式（Guided Local Search）在同规模问题上
  质量/耗时比更划算。
- **Gurobi / CPLEX / 商业 MILP 求解器** —— 解质量更高。否掉的原因：商业许可与成本，
  对当前规模是过度设计；FDE 现场要给客户交付可维护的方案，依赖越少越好解释。

## 后果

**正面**

- 解质量可接受，且**同样输入必得同样解**（确定性求解器 + 固定输入）→ 结果可复现，
  这条对「给客户交付可审计的优化结果」是硬要求。
- 约束变化时改模型即可（例如加入碳价 1.5 SEK/kg 的 CO₂ 目标），
  不需要重写搜索逻辑。
- 与 KPI 系统天然衔接：求解器输出的成本/里程直接进 `optimization_cycles` 与 dashboard。

**负面 / 技术债**

- OR-Tools 是重型依赖：wheel 大、镜像变大，冷启动慢；**不能用 OR-Tools 的模块在 fast 档跑**，
  这直接推高了 CI 时长（见 ADR-0006）。
- 单次求解比贪心慢 1~2 个数量级；上千 cycle 连跑时，总时长受它支配。
- 当前只建模了容量与时间窗，**没有建模司机工时、车辆异构、装卸时间**这类真实约束，
  与真实客户现场仍有差距。

---

_相关：[ADR-0001](0001-multi-agent-orchestration.md) 编排 · [ADR-0006](0006-ci-strategy-and-deterministic-fixture.md) 测试分层_
