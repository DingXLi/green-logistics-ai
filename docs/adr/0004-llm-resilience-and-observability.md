# ADR-0004: LLM 韧性 + 调用可观测 —— tenacity 分级重试 + 自建 llm_tracker

- **状态**：已接受
- **记录日期**：2026-11-01
- **相关代码**：`agents/llm_caller.py`, `agents/llm_tracker.py`, `agents/persistence.py`（`llm_decisions` 表）

## 背景

把 LLM 接进一个要连跑上千 cycle 的仿真里，会遇到两类问题：

**一、调用本身会失败，而且失败原因不同，处置方式完全不同。**
429（RESOURCE_EXHAUSTED / 配额超限）和 5xx 是**暂时性**的，退避重试就能救；
而 400/401/403 这类是**永久性**的（key 写错、请求体非法），重试一万次也是白烧配额。
最糟的做法是「一律重试」—— 会把配额打爆，让整批仿真崩掉。

**二、客户一定会问：这个流程花了多少钱、快不快、慢在哪。**
这不是锦上添花 —— JD 里明确写了 LLM-native 指标（tokens/sec、cost-per-request），
而没有这些数字，FDE 在客户面前就只能讲架构、报不出账。

## 决策

**按错误类型分级重试（tenacity）+ 自建调用追踪器（llm_tracker）+ 决策全量落库。**

### 1. 分级重试（`agents/llm_caller.py`）

用 tenacity 的 `retry` + `retry_if_exception_type`，自己定义异常类型：

| 错误类型 | 判定 | 处置 |
|---|---|---|
| 429 / `RESOURCE_EXHAUSTED` / quota | 可恢复 | **backoff 重试** |
| 5xx | 可恢复 | **backoff 重试** |
| 其他 4xx | 不可恢复（请求本身有问题） | **立即抛出，不重试** |

重试次数与单次超时来自 `llm_config`（默认 4 次 / 30 秒），由 `_build_retry_decorator(max_attempts)`
按当前配置构造，不写死在调用点。

### 2. 调用追踪（`agents/llm_tracker.py`）

- 每次调用记录：model、prompt/completion tokens、耗时、估算 USD 成本；
  成本由 `_estimate_cost_usd(model, prompt_tokens, candidate_tokens)` 按 model 单价表估算。
- 存储结构：**`threading.Lock` + `deque(maxlen=500)` 的线程安全 ring buffer**
  —— agent 是 async 并发跑写的，用 list 会无限增长且有竞态，ring buffer 天然只留最近 500 条。
- 对外接口：`get_stats()`（汇总）、`get_recent(n)`（明细）、`reset()`（换场景时清零）。

### 3. 决策全量落库（`llm_decisions` 表）

每次 LLM 判断都写一行：目标 id、`multiplier`（LLM 给的次日需求乘数）、`trend`
（rising/stable/falling）、`confidence`、`reason`（一句话解释）、`source`
（`'llm' | 'fallback'`）、`raw_json`（完整响应，调试用）、`wall_timestamp`。

**fallback 也要落库** —— 这样「今天为什么是这个数」永远有答案：要么是模型判的，要么是 fallback 兜的。

## 备选方案

- **不重试** —— 否掉：429 在真实配额下是常态，一次抖动就让整轮仿真失败。
- **一律重试** —— 否掉：4xx 重试是纯浪费，还会打爆配额掩盖真实错误。
- **改用 Vertex AI SDK 自带的重试策略** —— 否掉：行为锁定在一个 provider，
  以后换 OpenAI/NVIDIA 就得重写这一层。
- **上 OpenTelemetry / Prometheus 做全量追踪** —— 否掉：对当前体量过重；
  我们要的只是「token / 延迟 / 成本」三个数，自建 200 行就够，且能直接进 dashboard。

## 后果

**正面**

- 错误分级清晰：429 不再直接炸，4xx 立刻报错并指向真正的原因。
- 成本与延迟可以**当场报数**（token、延迟、USD），直接对应 JD 要求的 LLM-native 指标。
- 每个决策都可追溯 → 客户问「为什么这么排」时，能从库里拿出证据（decision_type / reason / raw_json）。
- tracker 是进程内的、无外部依赖 —— 部署到 HF Spaces 也不需要额外接监控系统。

**负面 / 技术债**

- ring buffer 只留最近 500 条，**长期趋势靠 SQLite 侧**（`llm_decisions` 表），
  两套数据的口径要自己对齐。
- 成本是**按本地单价表估算**，不是账单真实值；对账单时会有偏差，需要在文档里讲清楚口径。
- `raw_json` 存完整响应，会让表快速膨胀；目前靠调试便利性优先，需要定期清理策略。

---

_相关：[ADR-0003](0003-llm-model-and-config.md) 配置 · [ADR-0005](0005-persistence-sqlite.md) 落盘_
