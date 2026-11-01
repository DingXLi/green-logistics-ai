# ADR-0003: LLM 选型与配置 —— Gemini 2.5 Flash + 配置单一来源

- **状态**：已接受（含一条 SDK 迁移债）
- **记录日期**：2026-11-01
- **相关代码**：`agents/llm_config.py`, `config/settings.yaml`（`ai:` 段）, `agents/llm_caller.py`

## 背景

三个 agent 都要调 LLM。如果 model 名、temperature、max_tokens、timeout、retry 次数
散落在各自的代码里，会出现三件很典型的坏事：

1. 换模型要改 N 处，改漏一个 = 两个 agent 行为不一致，且极难发现；
2. 本地环境和线上环境配置不同 → 结果不可复现，客户问「为什么昨天是这个数」没人答得上；
3. 超时/重试策略各写一套 → 有的 agent 无限重试把配额打爆，有的 agent 一次失败就崩。

另外，**要能回答「一次请求花多少钱、多快」**——这是 FDE 岗位要求里明确列的
LLM-native 指标（tokens/sec、cost-per-request）。

## 决策

**单一模型 + 单一配置来源。**

1. **模型：Gemini 2.5 Flash**（`agents/llm_config.py` 的 `DEFAULT_MODEL = "gemini-2.5-flash"`）。
   选它是因为在质量 / 延迟 / 成本三角里最平衡：我们的 LLM 调用是「给个倾向 + 一句解释」
   （预测 multiplier、匹配倾向），不是长文本生成，Flash 档足够；
   provider 字段留了 `google / openai / nvidia` 三档，方便横向换型做 benchmark。
2. **配置优先级（三级，单一事实来源）**：

   ```
   环境变量 GEMINI_*  >  config/settings.yaml 的 ai.*  >  代码内硬编码默认
   ```

   解析入口是 `agents/llm_config.py:get_llm_config()`，用 `lru_cache(maxsize=1)` 做单例，
   保证进程内只解析一次、所有 agent 拿到同一份。
3. **默认参数**：`temperature 0.7` / `max_tokens 4096` / `max_retries 4` / `timeout_seconds 30`，
   每一项都能被环境变量单独覆盖（`GEMINI_MODEL`、`GEMINI_TEMPERATURE`、`GEMINI_MAX_TOKENS`、
   `GEMINI_MAX_RETRIES`、`GEMINI_TIMEOUT`）。
4. **model 名不硬编码在 agent 里**：agent 一律 `from .llm_config import MODEL`。

## 备选方案

- **每个 agent 自己配** —— 否掉：配置漂移，正是上面列的三件坏事。
- **只用环境变量** —— 否掉：新人 clone 下来没设环境变量就跑不起来；
  yaml 还能让「同一份代码、不同环境」有可读的差异。
- **直接写死模型名在调用处** —— 否掉：换型要改 N 处，改漏即行为不一致。

## 后果

**正面**

- 换模型 / 调参数只改一处；`config/settings.yaml` 让环境差异可读、可复盘。
- 「单一事实来源」是能直接讲给面试官听的架构原则（对应 JD 里的「状态管理与优化」）。
- 重试/超时口径统一，配额被打爆的风险大幅下降（配合 ADR-0004 的分级重试）。

**负面 / 技术债**

- ⚠️ **SDK 仍是 legacy 的 `google-generativeai`**（`agents/llm_caller.py:22`
  `import google.generativeai as genai`）。官方已把它并入新的 `google-genai` SDK，
  而 `google-adk` 的新版本也以 `google-genai` 为准。
  这条**不影响当前功能**，但会影响未来升级路径与「SDK 用得够不够新」的细节追问 —— 属于必须记着的技术债。
- `lru_cache` 单例意味着进程内改环境变量不会立即生效（改完要重启），
  对长驻进程（比如 uvicorn worker）是个小坑。
- `temperature 0.7` 对「要可复现」的目标是矛盾的折中：LLM 侧的输出并非严格确定，
  真正的确定性来自「LLM 判断 + fallback 都落库」的设计（见 ADR-0004）。

---

_相关：[ADR-0004](0004-llm-resilience-and-observability.md) 韧性 · [ADR-0001](0001-multi-agent-orchestration.md) 编排_
