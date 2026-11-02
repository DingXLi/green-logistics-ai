# ADR-0008: SDK 迁移 —— 从 `google-generativeai` 切到 `google-genai`

- **状态**：已接受（解掉 ADR-0003 里记的那条 SDK 债）
- **记录日期**：2026-11-02
- **相关代码**：`agents/llm_caller.py`, `requirements.txt`,
  `tests/test_llm_integration.py`, `tests/test_llm_tracker.py`
- **关联**：[ADR-0003](0003-llm-model-and-config.md)（模型与配置）·
  [ADR-0004](0004-llm-resilience-and-observability.md)（重试与可观测）

## 背景

ADR-0003 写完时留了一条明确的债：

> ⚠️ SDK 仍是 legacy 的 `google-generativeai`（`agents/llm_caller.py:22`）。
> 官方已把它并入新的 `google-genai` SDK，而 `google-adk` 的新版本也以 `google-genai` 为准。

这条债当时判断「不影响功能」，所以先记着。但它有四个真实成本：

1. **两套 SDK 并存**：`google-adk` 1.x 自身依赖 `google-genai`，我们自己却用 legacy SDK。
   同一份模型调用在 ADK 侧和自研侧是两套代码路径 —— 出问题时很难判断是 SDK 差异还是业务 bug。
2. **新功能全部拿不到**：thinking config、structured output、context caching、
   Live API 这些都在 `google-genai`，legacy SDK 只剩维护。
3. **升级路径会被卡**：Google 已宣布 legacy SDK 进入只维护状态，
   后续 breaking change 只能忍。
4. **FDE 现场会被追问**：「你这套 agent 用的是哪代 SDK？为什么还在用旧的？」
   —— 面试官点开仓库就能看到 `import google.generativeai`。

触发迁移的时机：准备把它写进 portfolio / 讲架构取舍的时候，
一条自己写在自己 ADR 里的「已知技术债」，要么解掉，要么解释清楚为什么留着。

## 决策

**把 LLM 调用层整体迁到官方新 SDK `google-genai`，legacy SDK 彻底移除。**

1. **依赖换血**（`requirements.txt`）：
   - 删 `google-generativeai>=0.8.0`
   - 加 `google-genai>=1.0.0`（本地/CI 实测 1.73.1）
   - `google-adk>=0.1.0` 保持不变 —— 它本来就要 `google-genai`，等于顺带对齐。
2. **调用面改写**（`agents/llm_caller.py`）：

   | | legacy `google-generativeai` | 新 `google-genai` |
   |---|---|---|
   | 建客户端 | `genai.configure(api_key=...)` | `genai.Client(api_key=...)` |
   | 模型 | `genai.GenerativeModel(name)` | `client.models` |
   | 调用 | `m.generate_content(prompt, request_options={...})` | `client.models.generate_content(model=..., contents=..., config=...)` |
   | 参数 | `generation_config={...}` 散装 | `types.GenerateContentConfig(...)` 统一收口 |
   | 超时 | `request_options={"timeout": 秒}` | `types.HttpOptions(timeout=毫秒)` |
   | 用量 | `resp.usage_metadata` | `resp.usage_metadata`（同名同义，不变） |

3. **Client 复用**：模块级缓存一个 `genai.Client`（内部持 httpx 连接池），
   缓存键 = `api_key` + SDK 模块对象 —— 换 key 重建，测试里 patch 掉 SDK 时也自动重建，
   不会拿到脏缓存。
4. **错误分类改成「先看 code，再看 message」**：新 SDK 里 429 也是 `ClientError`
   （不是独立的 RateLimit 类），只看异常类会把 429 误判成不可重试的客户端错误。
   现在先读 `exc.code`（429 → 限流重试 / 5xx → 服务端重试 / 4xx → 直接抛），
   再用 message 兜底（兼容 `google.api_core.exceptions` 与手工构造的异常）。
5. **思考预算显式可控，默认关闭**（`ai.thinking_budget`，env `GEMINI_THINKING_BUDGET`）：

   新 SDK 对 Gemini 2.5 **默认开思考**，而**思考 token 计入 `max_output_tokens`**。
   实测（真实 API）：

   | max_output_tokens | thoughts_token_count | 实际可见输出 |
   |---|---|---|
   | 120 | 112 | `车辆路径问题(` ← **被截断** |
   | 4096 | 1145 | 完整句子 ✅ |

   我们的 agent 要的是严格 JSON（multiplier 数组），截断 → JSON 解析失败 →
   **静默降级到 fallback**（LLM 路径看起来「一直不生效」，但没有任何报错）。
   所以：默认 `thinking_budget = 0`（= 与迁移前 legacy SDK 行为一致），
   需要推理能力的场景再按调用点打开；且当 `max_tokens <= thinking_budget` 时
   自动把输出预算抬到 `thinking_budget + 512`，避免「预算被思考吃光」。

## 备选方案

- **两套 SDK 并存，渐进迁移** —— 否掉：并存期要维护两套 mock、两套错误分类、
  两套超时语义；「渐进」在这个代码量（调用层就一个文件）只会拖成「永远不迁」。
- **只改 import 不改调用面（包一层 shim 假装没变）** —— 否掉：
  面试官和未来的自己都会去看 shim 底下藏了什么，等于把债藏得更深。
- **干脆自己封一层 HTTP 客户端（直接打 Gemini REST API）** —— 否掉：
  能省掉 SDK，但认证、重试、流式、错误码映射全要自己维护，
  跟「用现成方案 + 把精力放在业务编排上」的项目定位冲突。
- **继续留着 legacy SDK，等它彻底停止维护再说** —— 否掉：
  它已经在只维护状态；而且 ADR 里写着「必须记着的债」却没人动，
  这正是技术债最好的下场 —— 被 ADR 逼着还掉。

## 后果

**正面**

- 依赖树里只剩一套 Gemini SDK，和 ADK 对齐，「同一模型两套调用面」的隐患消失。
- 错误分类从「猜 message」升级成「读 HTTP code」，429 重试不再靠字符串匹配碰运气。
- **迁移过程挖出两个真 bug 并修掉了**：
  1. **日志炸掉重试路径**：新 SDK 的错误消息永远带 dict payload
     （`"429 RESOURCE_EXHAUSTED. {'error': {...}}"`），而 loguru 的
     `logger.log()` 会把消息当 format string 处理 → 大括号被当占位符 → 抛
     `KeyError("'error'")`。而 tenacity 的 `before_sleep_log` 正是用 logger 记每次重试，
     一炸就**不是重试而是直接崩**。旧 SDK 的错误消息是纯字符串，所以这个坑以前从未暴露。
     修法：分类前把消息清洗成日志安全字符串（大括号 → 圆括号），
     并补了回归测试 `test_retry_survives_braces_in_error_payload`。
  2. **思考 token 吃光输出预算 → JSON 被静默截断**：见「决策 5」。
     修法：思考预算显式配置 + 默认关闭 + 预算不足时自动抬高。
- 新 SDK 统一了 Gemini API 与 Vertex AI 的调用面：将来上 Vertex 只是
  `genai.Client(vertexai=True, project=..., location=...)`，
  配合 ADR-0003 的三级配置（env > yaml > 默认）几乎零改动 ——
  正好是「客户现场要换区域/换云」时最省事的形态。

**负面 / 技术债**

- `genai.Client` 重建时旧的 httpx 连接池不会被显式关闭（靠 GC 回收）；
  key 频繁变化的长驻进程理论上会攒连接 → 目前 key 基本不变，可接受，
  真要严格可加 `client._http_client.close()`（私有 API，需谨慎）。
- 模块级 client 缓存在多进程（多 uvicorn worker）下每个进程一份 —— 正常现象，
  但要记住「限流是按 key 全局算的，不是按进程」。
- `google-genai` 的版本迭代比 legacy 快，升级时可能又有 breaking change；
  这比「永远卡在只维护的 legacy」可控，但需要 CI 常绿盯着。
- 思考预算默认 0，意味着**默认不思考** —— 对「预测 multiplier」这类
  「给个倾向 + 一句解释」的任务够用（ADR-0003 选 Flash 的理由也在这），
  但遇到需要多步推理的题型要记得把 `GEMINI_THINKING_BUDGET` 调上去，
  并同步放宽 `max_tokens`。

---

## 验证记录（2026-11-02）

- 本地全量 fast 套件：**1614 passed / 6 skipped / 122 deselected**（slow）
- LLM 相关单测：**59 passed / 1 skipped**（skipped = 需要真实 key 的集成用例）
- flake8 严格子集（CI 同款 `--select=E9,F63,F7,F82`）：**0 error**
- mypy（CI 同款参数）：**Success: no issues found in 16 source files**
- **真实 API 冒烟**（用仓库自带 `.env` 的 key）：`call_gemini` 返回正常文本 ✅
- **真实 API 端到端**：`MarketAgent.predict_demand` → `source=llm`，
  两个需求点都拿到合法 multiplier / trend / reason ✅（不是 fallback）

---

_相关：[ADR-0003](0003-llm-model-and-config.md) 模型与配置 · [ADR-0004](0004-llm-resilience-and-observability.md) 重试与可观测_
