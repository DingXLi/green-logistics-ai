# Architecture Decision Records (ADR)

这个目录记录 **green-logistics-ai 的架构决策**：每个「为什么这么做」背后的背景、备选方案、
代价和被推翻的选项。

**为什么 FDE 岗需要它？**
FDE 的工作现场反复被问的一句话就是「为什么这么设计，而不是另一种？」。
代码只能回答「做了什么」，ADR 才能回答「凭什么」—— 这是从「写代码的人」到「能被客户追问的人」
之间最缺的一块。

> 纪律：ADR 写完不删。被新决策推翻时，新增一条 ADR 并把旧的标成 `Superseded by ADR-XXXX`。

---

## 目录

| 编号 | 标题 | 一句话决策 | 状态 |
|---|---|---|---|
| [0001](0001-multi-agent-orchestration.md) | 多智能体编排 | ADK 定义 agent，手写 Coordinator 编排，刻意不引 LangGraph | 已接受 |
| [0002](0002-vrp-optimization-core.md) | VRP 优化内核 | 用 OR-Tools RoutingModel，贪心解不可接受 | 已接受 |
| [0003](0003-llm-model-and-config.md) | LLM 选型与配置 | Gemini 2.5 Flash + 配置单一来源（env > yaml > 默认） | 已接受 |
| [0004](0004-llm-resilience-and-observability.md) | LLM 韧性 + 可观测 | tenacity 分级重试 + 自建 llm_tracker 记 token/延迟/成本 | 已接受 |
| [0005](0005-persistence-sqlite.md) | 持久化 | SQLite 11 张表 + 单一建表来源，承认 persistence.py 过大 | 已接受（含技术债） |
| [0006](0006-ci-strategy-and-deterministic-fixture.md) | CI 策略 | 分层 + 多版本矩阵 + fail-fast=false + 确定性 DB fixture | 已接受 |
| [0007](0007-frontend-and-deployment.md) | 前端与部署 | React+Vite 单页 + Docker 双镜像 + HF Spaces 公网 demo | 已接受（含技术债） |

---

## 怎么在面试里用

不要 7 条都讲。挑 **2-3 条**，每条按同一个四段式讲，2 分钟以内：

```
背景（当时要解决什么） → 备选（考虑过什么，为什么否掉） → 决策（选了什么） → 代价（代价是什么，怎么缓解）
```

推荐组合：

- **0001 多智能体编排** → 讲「为什么不 LangGraph」，这是 multi-agent 岗最常问的一刀
- **0004 韧性 + 可观测** → 讲「客户现场 429 怎么办、成本怎么回答」，对应岗位要求里的
  tokens/sec、cost-per-request
- **0006 CI 策略** → 讲「我交付的东西自己先得是绿的」，这是 FDE 区别于纯后端的地方

三条合起来正好覆盖 JD 里的「解决方案架构」「技术决策权衡」「把痛点转化为产品需求」。

---

## 模板（新增 ADR 时复制这个）

```markdown
# ADR-XXXX: <标题>

- **状态**：提议 / 已接受 / 已弃用 / 被 ADR-YYYY 取代
- **记录日期**：YYYY-MM-DD
- **相关代码**：path/to/file.py, path/to/other.py

## 背景
当时要解决什么问题、有什么约束。

## 决策
我们决定怎么做（写成祈使句，一句话能概括）。

## 备选方案
考虑过哪些做法，为什么没选（每条写「否掉的原因」）。

## 后果
**正面**：…
**负面 / 技术债**：…
```

---

_最后更新：2026-11-01（AI FDE 突击 · Day 70 补写）_
