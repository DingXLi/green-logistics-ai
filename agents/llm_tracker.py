"""
LLM call tracker (iter #22) — token usage + cost tracking.

为什么需要这个:
- Gemini API 按 token 计费 (虽然 2.5-flash 免费, 但要 observability)
- 想看哪个 cycle / agent / prompt type 用了多少 token
- 计算 $ cost 假设未来切换到 paid tier

设计:
- 线程安全 in-memory ring buffer (最近 N 次调用)
- 累计 total_calls / total_prompt_tokens / total_candidate_tokens / total_tokens
- 支持按 caller (agent name) 分组
- 暴露给 web backend 给 admin dashboard
- 失败时不影响 LLM call 本身 (best-effort)

不要存储原始 prompt (可能含 PII, 占用大量内存)。
只存储聚合: caller / tokens / duration_ms / success / model / error type。
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field, asdict
from typing import Any, Deque, Dict, List, Optional, TypedDict


# ============================================================
# 成本配置 (per 1M tokens, USD) — Gemini 2.5-flash pricing
# 来源: https://ai.google.dev/pricing (2026-08)
# ============================================================

# Gemini 2.5 Flash (input/output)
COST_PER_1M_PROMPT_TOKENS_USD = 0.075
COST_PER_1M_CANDIDATE_TOKENS_USD = 0.30

# Gemini 2.0 Flash (cheaper tier)
COST_PER_1M_PROMPT_TOKENS_USD_20 = 0.10
COST_PER_1M_CANDIDATE_TOKENS_USD_20 = 0.40


# ============================================================
# 数据结构
# ============================================================

@dataclass
class LLMCallRecord:
    """一次 LLM 调用的聚合数据。"""
    timestamp: float
    caller: str  # e.g., "supply_agent.predict_supply_batch"
    model: str
    prompt_tokens: int
    candidate_tokens: int
    total_tokens: int
    duration_ms: float
    success: bool
    error_type: Optional[str] = None  # e.g., "rate_limit", "server_error"
    cost_usd: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# Per-caller / per-model aggregator schema (iter #22, mypy-clean).
# cost_usd is float; token counts + calls/errors are int.
class LLMStatsBucket(TypedDict, total=False):
    calls: int
    prompt_tokens: int
    candidate_tokens: int
    total_tokens: int
    cost_usd: float
    errors: int


def _empty_stats_dict() -> LLMStatsBucket:
    return {
        "calls": 0,
        "prompt_tokens": 0,
        "candidate_tokens": 0,
        "total_tokens": 0,
        "cost_usd": 0.0,
        "errors": 0,
    }


# ============================================================
# Tracker
# ============================================================

class LLMTracker:
    """
    线程安全的 LLM 调用 tracker。

    Usage:
        tracker = get_llm_tracker()
        record = tracker.record(
            caller="supply_agent.predict_supply_batch",
            model="gemini-2.5-flash",
            usage={"prompt_token_count": 100, "candidates_token_count": 50, "total_token_count": 150},
            duration_ms=1200,
            success=True,
        )

    暴露:
        - get_stats()       — 聚合 (total / by_caller / by_model)
        - get_recent(n)     — 最近 N 条 record
        - reset()           — 清空 (for testing)
    """

    def __init__(self, maxlen: int = 500) -> None:
        self._lock = threading.Lock()
        self._records: Deque[LLMCallRecord] = deque(maxlen=maxlen)
        self._total_calls = 0
        self._total_prompt_tokens = 0
        self._total_candidate_tokens = 0
        self._total_tokens = 0
        self._total_cost_usd = 0.0
        self._total_errors = 0
        self._by_caller: Dict[str, LLMStatsBucket] = {}  # {caller: {calls, prompt, candidate, total, cost_usd, errors}}
        self._by_model: Dict[str, LLMStatsBucket] = {}

    def _estimate_cost_usd(self, model: str, prompt_tokens: int, candidate_tokens: int) -> float:
        """按 model 估算 USD cost。"""
        if "2.0" in model:
            p_rate = COST_PER_1M_PROMPT_TOKENS_USD_20
            c_rate = COST_PER_1M_CANDIDATE_TOKENS_USD_20
        else:
            p_rate = COST_PER_1M_PROMPT_TOKENS_USD
            c_rate = COST_PER_1M_CANDIDATE_TOKENS_USD
        return (prompt_tokens / 1_000_000.0) * p_rate + (candidate_tokens / 1_000_000.0) * c_rate

    def record(
        self,
        caller: str,
        model: str,
        usage: Optional[Dict[str, int]],
        duration_ms: float,
        success: bool,
        error_type: Optional[str] = None,
    ) -> LLMCallRecord:
        """
        记录一次 LLM 调用。

        Args:
            caller: 调用方标识 (e.g., "supply_agent.predict_supply_batch")
            model: model 名
            usage: Gemini usage_metadata dict (可 None if call failed)
            duration_ms: 调用耗时
            success: 是否成功
            error_type: 失败时的 error 类别 (rate_limit/server_error/client_error)
        """
        prompt_tokens = int((usage or {}).get("prompt_token_count", 0))
        candidate_tokens = int((usage or {}).get("candidates_token_count", 0))
        total_tokens = int((usage or {}).get("total_token_count", prompt_tokens + candidate_tokens))
        cost = self._estimate_cost_usd(model, prompt_tokens, candidate_tokens) if success else 0.0

        rec = LLMCallRecord(
            timestamp=time.time(),
            caller=caller,
            model=model,
            prompt_tokens=prompt_tokens,
            candidate_tokens=candidate_tokens,
            total_tokens=total_tokens,
            duration_ms=duration_ms,
            success=success,
            error_type=error_type,
            cost_usd=round(cost, 8),
        )

        with self._lock:
            self._records.append(rec)
            self._total_calls += 1
            self._total_prompt_tokens += prompt_tokens
            self._total_candidate_tokens += candidate_tokens
            self._total_tokens += total_tokens
            self._total_cost_usd += cost
            if not success:
                self._total_errors += 1
            # by_caller
            cstats = self._by_caller.setdefault(caller, _empty_stats_dict())
            cstats["calls"] += 1
            cstats["prompt_tokens"] += prompt_tokens
            cstats["candidate_tokens"] += candidate_tokens
            cstats["total_tokens"] += total_tokens
            cstats["cost_usd"] = cstats["cost_usd"] + cost
            if not success:
                cstats["errors"] += 1
            # by_model
            mstats = self._by_model.setdefault(model, _empty_stats_dict())
            mstats["calls"] += 1
            mstats["prompt_tokens"] += prompt_tokens
            mstats["candidate_tokens"] += candidate_tokens
            mstats["total_tokens"] += total_tokens
            mstats["cost_usd"] = mstats["cost_usd"] + cost
            if not success:
                mstats["errors"] += 1
        return rec

    def get_stats(self) -> Dict[str, Any]:
        """聚合统计 — 总数 + 按 caller/model 分组。"""
        with self._lock:
            return {
                "total_calls": self._total_calls,
                "total_errors": self._total_errors,
                "error_rate_pct": round(100.0 * self._total_errors / max(1, self._total_calls), 2),
                "total_prompt_tokens": self._total_prompt_tokens,
                "total_candidate_tokens": self._total_candidate_tokens,
                "total_tokens": self._total_tokens,
                "total_cost_usd": round(self._total_cost_usd, 6),
                "avg_tokens_per_call": round(self._total_tokens / max(1, self._total_calls), 1),
                "by_caller": dict(self._by_caller),
                "by_model": dict(self._by_model),
                "buffer_size": len(self._records),
                "buffer_max": self._records.maxlen,
            }

    def get_recent(self, n: int = 50) -> List[Dict[str, Any]]:
        """最近 N 条 record (按时间倒序)。"""
        with self._lock:
            recent = list(self._records)[-n:][::-1]
            return [r.to_dict() for r in recent]

    def reset(self) -> None:
        """清空所有状态 (测试用)。"""
        with self._lock:
            self._records.clear()
            self._total_calls = 0
            self._total_prompt_tokens = 0
            self._total_candidate_tokens = 0
            self._total_tokens = 0
            self._total_cost_usd = 0.0
            self._total_errors = 0
            self._by_caller.clear()
            self._by_model.clear()


# ============================================================
# Singleton
# ============================================================

_TRACKER: Optional[LLMTracker] = None
_TRACKER_LOCK = threading.Lock()


def get_llm_tracker() -> LLMTracker:
    """Get singleton LLMTracker."""
    global _TRACKER
    if _TRACKER is None:
        with _TRACKER_LOCK:
            if _TRACKER is None:
                _TRACKER = LLMTracker()
    return _TRACKER


__all__ = [
    "LLMTracker",
    "LLMCallRecord",
    "get_llm_tracker",
]
