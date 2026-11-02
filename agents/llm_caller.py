"""
带 retry 的 Gemini API 调用工具 (tenacity) —— 基于官方新 SDK `google-genai`

为什么需要这个:
- Gemini 免费层有 RPM/RPD/TPM 三重限速
- 网络抖动 / 5xx 偶发
- 429 (RESOURCE_EXHAUSTED) 应该 backoff 重试,不要直接抛
- 5xx 同样 backoff 重试
- 4xx (除 429) 直接抛(用户错,重试无用)

为什么用 `google-genai` 而不是旧的 `google-generativeai`（iter #76）:
- 官方已把新功能（thinking config / structured output / caching / live API）
  全部放在 `google-genai`,legacy SDK 进入只维护状态;
- `google-adk` 1.x 自身依赖的就是 `google-genai`,两套 SDK 并存会让
  「同一份模型调用在 ADK 侧和自研侧行为不一致」;
- 新 SDK 统一了 Gemini API 与 Vertex AI 的调用面:
  将来要上 Vertex(见 ADR-0003 的三级配置)只是加 `vertexai=True` + project/location,
  不需要重写调用层 —— 这正是 FDE 现场「客户要换云 / 换区域」时最省事的形态。

使用:
    from agents.llm_caller import call_gemini
    text = call_gemini("用一句话解释什么是 VRP")
"""

from __future__ import annotations

import os
import logging
from typing import Any, Dict, Optional

from google import genai  # type: ignore
from google.genai import types  # type: ignore

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
    wait_random_exponential,
    before_sleep_log,
    RetryError,
)
from loguru import logger

from .llm_config import (
    MODEL,
    TEMPERATURE,
    MAX_TOKENS,
    MAX_RETRIES,
    TIMEOUT_S,
    THINKING_BUDGET,
    get_llm_config,
)


# ============================================================
# 客户端缓存
# ============================================================

# 复用 client 而不是每次调用新建: genai.Client 内部持有 httpx 连接池,
# 复用能省掉 TCP/TLS 握手 + 连接池重建(在高频 agent cycle 里是可观的)。
_CLIENT: Optional[Any] = None
_CLIENT_KEY: Optional[str] = None   # 建 client 时用的 api_key
_CLIENT_SDK: Any = None              # 建 client 时的 SDK 模块对象(测试打桩时用来强制重建)


def _reset_client_cache() -> None:
    """清掉缓存的 client(测试用;生产上不需要手动调用)。"""
    global _CLIENT, _CLIENT_KEY, _CLIENT_SDK
    _CLIENT = None
    _CLIENT_KEY = None
    _CLIENT_SDK = None


def _get_client(api_key: str) -> Any:
    """拿（或建）一个 genai.Client。

    缓存键 = api_key + SDK 模块对象:
    - api_key 变了 → 重建(换 key / 换环境)
    - SDK 模块对象变了 → 重建(测试里 patch 掉 genai 时自动生效,不会拿到脏缓存)
    """
    global _CLIENT, _CLIENT_KEY, _CLIENT_SDK
    if _CLIENT is None or _CLIENT_KEY != api_key or _CLIENT_SDK is not genai:
        _CLIENT = genai.Client(api_key=api_key)
        _CLIENT_KEY = api_key
        _CLIENT_SDK = genai
    return _CLIENT


# ============================================================
# 异常分类
# ============================================================

class GeminiAPIError(Exception):
    """所有 Gemini API 错误的基类。"""


class GeminiRateLimitError(GeminiAPIError):
    """429 RESOURCE_EXHAUSTED — 应该 backoff 重试。"""


class GeminiServerError(GeminiAPIError):
    """5xx — 应该 backoff 重试。"""


class GeminiClientError(GeminiAPIError):
    """4xx (除 429) — 用户错误,直接抛,重试无用。"""


def _safe_msg(raw: object, limit: int = 200) -> str:
    """把 SDK 原始错误消息清洗成「日志安全」的短字符串。

    为什么需要这一步（iter #76 迁移时踩到的真坑）:
    loguru 的 `logger.log(level, message)` 会把 message 当 **format string** 处理
    （loguru 0.7.x `_log()` 里无条件 `message.format(*args, **kwargs)`），
    而新 SDK `google-genai` 的错误消息永远带 dict payload:
        "429 RESOURCE_EXHAUSTED. {'error': {'code': 429, ...}}"
    大括号会被当成占位符 → loguru 抛 KeyError("'error'")。

    而 tenacity 的 `before_sleep_log` 正是用 logger 记录每次重试 ——
    一旦这里炸掉，**重试逻辑整个挂掉**（不是重试，是直接崩）。
    旧 SDK 的错误消息是纯字符串，所以这个坑以前没暴露过；迁移后才显形。

    处理方式：把大括号换成圆括号 + 压掉多余空白 + 截断。
    原始异常对象不会丢 —— 调用处用 `raise classified from e` 保留因果链。
    """
    text = str(raw).replace("{", "(").replace("}", ")")
    text = " ".join(text.split())
    return text[:limit]


def _classify_http_error(exc: Exception) -> Exception:
    """把 SDK / api_core 的异常分类成我们自己的类型。

    新 SDK (`google-genai`) 的错误模型:
    - 4xx → errors.ClientError, 5xx → errors.ServerError, 429 也是 ClientError(带 code=429)
    所以**不能只看异常类**,必须看 code;这里先看 code,再看 message 兜底。
    """
    code = getattr(exc, "code", None)
    msg = _safe_msg(exc)

    if isinstance(code, int):
        if code == 429:
            return GeminiRateLimitError(f"Rate limit hit: {msg}")
        if 500 <= code < 600:
            return GeminiServerError(f"Server error: {msg}")
        if 400 <= code < 500:
            return GeminiClientError(f"Client error: {msg}")

    # 兜底:按 message 判定(覆盖 google.api_core.exceptions 与手工构造的异常)
    if "429" in msg or "RESOURCE_EXHAUSTED" in msg or "quota" in msg.lower():
        return GeminiRateLimitError(f"Rate limit hit: {msg}")
    if any(s in msg for s in ("500", "502", "503", "504", "Internal Server Error", "UNAVAILABLE")):
        return GeminiServerError(f"Server error: {msg}")
    return GeminiClientError(f"Client error: {msg}")


# ============================================================
# 重试装饰器
# ============================================================

def _build_retry_decorator(max_attempts: int) -> Any:
    """根据配置构造 tenacity 装饰器。
    策略: 仅对 RateLimit / ServerError 重试,ClientError 直接抛。
    backoff: exponential + jitter (0.5s -> 1s -> 2s -> 4s ... up to 30s)。
    """
    return retry(
        retry=retry_if_exception_type((GeminiRateLimitError, GeminiServerError)),
        stop=stop_after_attempt(max_attempts),
        wait=wait_random_exponential(multiplier=0.5, max=30),
        reraise=True,
        before_sleep=before_sleep_log(logger, logging.WARNING),
    )


# ============================================================
# 主入口
# ============================================================

def _ensure_configured() -> str:
    """确保 API key 已设置,返回 key(缺失直接抛 client error)。"""
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise GeminiClientError(
            "GOOGLE_API_KEY env var is not set. "
            "Add it to .env (see .env.example) or export it before calling."
        )
    return api_key


def call_gemini(
    prompt: str,
    *,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    system_instruction: Optional[str] = None,
    timeout_s: Optional[int] = None,
    caller: Optional[str] = None,
) -> str:
    """
    调一次 Gemini,带 retry。

    Args:
        prompt: 用户提示
        model: 覆盖默认 model (None = 用 llm_config.MODEL)
        temperature: 覆盖默认
        max_tokens: 覆盖默认
        system_instruction: 可选系统指令
        timeout_s: 覆盖默认
        caller: 调用方标识 (用于 token usage tracking, e.g., "supply_agent.predict_supply_batch")

    Returns:
        模型返回的文本

    Raises:
        GeminiClientError: 4xx (非 429) — 不重试
        GeminiRateLimitError / GeminiServerError: 重试用尽后抛出
    """
    import time as _time
    cfg = get_llm_config()
    use_model = model or cfg["model"]
    use_temp = temperature if temperature is not None else cfg["temperature"]
    use_max = max_tokens if max_tokens is not None else cfg["max_tokens"]
    use_timeout = timeout_s if timeout_s is not None else cfg["timeout_s"]
    max_attempts = max(1, cfg["max_retries"] + 1)  # 1 initial + N retries
    caller_label = caller or "call_gemini"
    thinking_budget = int(cfg.get("thinking_budget", 0) or 0)

    # 新 SDK 对 Gemini 2.5 默认开思考，而思考 token 计入 max_output_tokens ——
    # 输出预算被思考吃掉一半时，JSON 答案会被截断（→ 上层解析失败 → 静默走 fallback）。
    # 开了思考就保证「输出预算 > 思考预算 + 余量」。
    if thinking_budget > 0 and use_max <= thinking_budget:
        _old_max = use_max
        use_max = thinking_budget + 512
        logger.warning(
            f"max_tokens({_old_max}) 不足以容纳 thinking_budget({thinking_budget}),已上调到 {use_max}"
        )

    api_key = _ensure_configured()
    client = _get_client(api_key)

    # 新 SDK: 参数统一收进 GenerateContentConfig
    # - max_output_tokens: 旧 SDK 的 generation_config 里也是这个名字,语义一致
    # - http_options.timeout 单位是毫秒(旧 SDK 的 request_options.timeout 是秒)
    gen_config = types.GenerateContentConfig(
        temperature=use_temp,
        max_output_tokens=use_max,
        system_instruction=system_instruction,
        thinking_config=types.ThinkingConfig(thinking_budget=thinking_budget),
        http_options=types.HttpOptions(timeout=use_timeout * 1000),
    )

    # iter #22: token usage tracking — best-effort, failures in tracker don't affect LLM call
    try:
        from .llm_tracker import get_llm_tracker
        _tracker = get_llm_tracker()
    except Exception:
        _tracker = None

    @_build_retry_decorator(max_attempts)
    def _do_call() -> str:
        _t0 = _time.monotonic()
        usage: Optional[Dict[str, int]] = None
        try:
            resp = client.models.generate_content(
                model=use_model,
                contents=prompt,
                config=gen_config,
            )
            # iter #22: capture usage_metadata for cost tracking
            try:
                um = getattr(resp, "usage_metadata", None)
                if um is not None:
                    usage = {
                        "prompt_token_count": int(getattr(um, "prompt_token_count", 0) or 0),
                        "candidates_token_count": int(getattr(um, "candidates_token_count", 0) or 0),
                        "total_token_count": int(getattr(um, "total_token_count", 0) or 0),
                    }
            except Exception:
                usage = None
            # 新 SDK 的 .text 在「无候选」时可能抛异常/返回 None —— 都按空串处理,
            # 让上层的 JSON 解析走 fallback 分支而不是整条链路炸掉。
            try:
                text = resp.text
            except Exception:
                text = None
            return text or ""
        except Exception as e:
            classified = _classify_http_error(e)
            if _tracker is not None:
                try:
                    error_type = type(classified).__name__.replace("Gemini", "").replace("Error", "").lower() or "unknown"
                    _tracker.record(
                        caller=caller_label, model=use_model, usage=None,
                        duration_ms=(_time.monotonic() - _t0) * 1000,
                        success=False, error_type=error_type,
                    )
                except Exception:
                    pass
            raise classified from e
        finally:
            if _tracker is not None and usage is not None:
                try:
                    _tracker.record(
                        caller=caller_label, model=use_model, usage=usage,
                        duration_ms=(_time.monotonic() - _t0) * 1000,
                        success=True,
                    )
                except Exception:
                    pass

    return _do_call()


def call_gemini_safe(prompt: str, **kwargs: Any) -> Optional[str]:
    """call_gemini 但失败时返回 None 而不是抛异常 (供 best-effort 调用方)。"""
    try:
        return call_gemini(prompt, **kwargs)
    except GeminiAPIError as e:
        logger.error(f"Gemini call failed after retries: {e}")
        return None


__all__ = [
    "call_gemini",
    "call_gemini_safe",
    "GeminiAPIError",
    "GeminiRateLimitError",
    "GeminiServerError",
    "GeminiClientError",
    "RetryError",
]
