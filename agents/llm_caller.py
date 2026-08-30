"""
带 retry 的 Gemini API 调用工具 (tenacity)

为什么需要这个:
- Gemini 免费层有 RPM/RPD/TPM 三重限速
- 网络抖动 / 5xx 偶发
- 429 (RESOURCE_EXHAUSTED) 应该 backoff 重试,不要直接抛
- 5xx 同样 backoff 重试
- 4xx (除 429) 直接抛(用户错,重试无用)

使用:
    from agents.llm_caller import call_gemini
    text = call_gemini("用一句话解释什么是 VRP")
"""

from __future__ import annotations

import os
import logging
from typing import Any, Dict, Optional

import google.generativeai as genai  # type: ignore

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
    get_llm_config,
)


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


def _classify_http_error(exc: Exception) -> Exception:
    """把 google-generativeai 的异常分类成我们自己的类型。"""
    msg = str(exc)
    if "429" in msg or "RESOURCE_EXHAUSTED" in msg or "quota" in msg.lower():
        return GeminiRateLimitError(f"Rate limit hit: {msg[:200]}")
    if any(s in msg for s in ("500", "502", "503", "504", "Internal Server Error", "UNAVAILABLE")):
        return GeminiServerError(f"Server error: {msg[:200]}")
    return GeminiClientError(f"Client error: {msg[:200]}")


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

def _ensure_configured() -> None:
    """确保 API key 已设置,只做一次。"""
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise GeminiClientError(
            "GOOGLE_API_KEY env var is not set. "
            "Add it to .env (see .env.example) or export it before calling."
        )
    genai.configure(api_key=api_key)


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

    _ensure_configured()

    gen_config: Dict[str, Any] = {
        "temperature": use_temp,
        "max_output_tokens": use_max,
    }

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
            m = genai.GenerativeModel(
                model_name=use_model,
                generation_config=gen_config,  # type: ignore[arg-type]
                system_instruction=system_instruction,
            )
            resp = m.generate_content(prompt, request_options={"timeout": use_timeout})
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
            return resp.text or ""
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
