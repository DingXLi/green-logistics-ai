"""
LLM 集中配置 / Centralised LLM configuration

设计目标:
- 不在 agent 代码里硬编码 model 名 (避免到处改 1 处 vs N 处)
- 优先级: 环境变量 > config/settings.yaml > 硬编码默认
- 单一来源,所有 agent / caller 都从这 import

环境变量:
    GEMINI_MODEL       覆盖默认 model (例: gemini-2.5-flash, gemini-2.5-flash-lite)
    GEMINI_TEMPERATURE 覆盖 temperature
    GEMINI_MAX_TOKENS  覆盖 max output tokens
    GEMINI_MAX_RETRIES 429/5xx 时重试次数
    GEMINI_TIMEOUT     单次请求超时秒数
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional

# 硬编码 fallback (仅当 env + yaml 都没值时)
DEFAULT_MODEL = "gemini-2.5-flash"
DEFAULT_TEMPERATURE = 0.7
DEFAULT_MAX_TOKENS = 4096
DEFAULT_MAX_RETRIES = 4
DEFAULT_TIMEOUT_S = 30


def _load_yaml_settings() -> Dict[str, Any]:
    """从 config/settings.yaml 读 ai.* 段。读不到就返回空 dict。"""
    try:
        import yaml  # type: ignore
    except ImportError:
        return {}
    p = Path(__file__).resolve().parent.parent / "config" / "settings.yaml"
    if not p.exists():
        return {}
    try:
        with p.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return (data.get("ai") or {})
    except Exception:
        return {}


@lru_cache(maxsize=1)
def get_llm_config() -> Dict[str, Any]:
    """
    按优先级解析 LLM 配置:
      1. 环境变量 (GEMINI_*)
      2. config/settings.yaml 的 ai.* 段
      3. 模块内硬编码默认
    """
    yaml_ai = _load_yaml_settings()

    def pick(env_key: str, yaml_key: str, default: Any) -> Any:
        v = os.environ.get(env_key)
        if v is not None and v != "":
            return v
        if yaml_key in yaml_ai and yaml_ai[yaml_key] is not None:
            return yaml_ai[yaml_key]
        return default

    def as_int(env_key: str, yaml_key: str, default: int) -> int:
        raw = pick(env_key, yaml_key, default)
        try:
            return int(raw)
        except (TypeError, ValueError):
            return default

    def as_float(env_key: str, yaml_key: str, default: float) -> float:
        raw = pick(env_key, yaml_key, default)
        try:
            return float(raw)
        except (TypeError, ValueError):
            return default

    return {
        "model": pick("GEMINI_MODEL", "model", DEFAULT_MODEL),
        "temperature": as_float("GEMINI_TEMPERATURE", "temperature", DEFAULT_TEMPERATURE),
        "max_tokens": as_int("GEMINI_MAX_TOKENS", "max_tokens", DEFAULT_MAX_TOKENS),
        "max_retries": as_int("GEMINI_MAX_RETRIES", "max_retries", DEFAULT_MAX_RETRIES),
        "timeout_s": as_int("GEMINI_TIMEOUT", "timeout_seconds", DEFAULT_TIMEOUT_S),
        "provider": pick("GEMINI_PROVIDER", "provider", "google"),
    }


# 便捷常量 (用 property 风格,在 import 时一次性解析)
MODEL: str = get_llm_config()["model"]
TEMPERATURE: float = get_llm_config()["temperature"]
MAX_TOKENS: int = get_llm_config()["max_tokens"]
MAX_RETRIES: int = get_llm_config()["max_retries"]
TIMEOUT_S: int = get_llm_config()["timeout_s"]


def reload() -> Dict[str, Any]:
    """清掉 lru_cache 并重读。配置变更后调用 (例如测试)。"""
    get_llm_config.cache_clear()
    return get_llm_config()


__all__ = [
    "MODEL",
    "TEMPERATURE",
    "MAX_TOKENS",
    "MAX_RETRIES",
    "TIMEOUT_S",
    "get_llm_config",
    "reload",
]
