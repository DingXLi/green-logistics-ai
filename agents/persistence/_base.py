"""存储核心：连接管理、schema 初始化/迁移、读缓存。

所有领域 mixin 都继承本类 —— 这是唯一的 SQLite 连接与 schema 来源。
"""

from typing import Any, Dict, Optional
from pathlib import Path
from contextlib import contextmanager
import sqlite3

from loguru import logger

from ._schema import SCHEMA_SQL


class PersistenceBase:
    """PersistenceBase —— 由 agents/persistence.py 拆分而来（iter #77）。"""

    def __init__(self, db_path: str = "data/simulation.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()
        # iter #61: In-memory TTL cache for read-heavy top-X endpoints.
        # Frontend auto-refresh hits /top-suppliers, /top-cycles, /top-demands,
        # /top-facilities every 60s — each issues several DB queries.
        # A 30s TTL cache drops DB load by ~50% during normal dashboard use.
        # Key format: "<method_name>:<json-sorted-args>"
        # Value format: {"value": <result>, "expires_at": <epoch_seconds>}
        self._top_cache: Dict[str, Dict[str, Any]] = {}
        self._top_cache_ttl_s: float = 30.0
        logger.info(f"持久化层初始化：{self.db_path}")

    def _cache_key(self, method_name: str, kwargs: Dict[str, Any]) -> str:
        """Build a deterministic cache key from method name + sorted kwargs."""
        import json
        # Sort keys for stability, repr types for hashability
        normalized = {k: repr(v) for k, v in sorted(kwargs.items())}
        return f"{method_name}:{json.dumps(normalized, ensure_ascii=False)}"

    def _cache_get(self, key: str) -> Optional[Any]:
        """Get cached value if not expired; else None. Removes expired entries."""
        import time as _time
        entry = self._top_cache.get(key)
        if entry is None:
            return None
        if entry["expires_at"] < _time.time():
            self._top_cache.pop(key, None)
            return None
        return entry["value"]

    def _cache_set(self, key: str, value: Any) -> None:
        """Store a value in cache with TTL."""
        import time as _time
        self._top_cache[key] = {
            "value": value,
            "expires_at": _time.time() + self._top_cache_ttl_s,
        }

    def cache_clear(self) -> int:
        """Clear all cached entries. Returns number of entries cleared."""
        n = len(self._top_cache)
        self._top_cache.clear()
        return n

    def cache_stats(self) -> Dict[str, Any]:
        """Return cache stats (size, ttl) for diagnostics."""
        import time as _time
        n_total = len(self._top_cache)
        n_expired = sum(
            1 for e in self._top_cache.values()
            if e["expires_at"] < _time.time()
        )
        return {
            "n_entries": n_total,
            "n_expired": n_expired,
            "n_active": n_total - n_expired,
            "ttl_seconds": self._top_cache_ttl_s,
        }

    @contextmanager
    def _conn(self):
        """上下文管理 connection（自动 commit/rollback）"""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._conn() as conn:
            conn.executescript(SCHEMA_SQL)
            # iter #4: 兼容旧 DB, 如 seasonal_factor_avg / seasonal_month 列缺失则 ALTER TABLE
            self._migrate_add_seasonal_columns(conn)

    def _migrate_add_seasonal_columns(self, conn) -> None:
        """为旧 DB 加 seasonal_factor_avg + seasonal_month 列。

        使用 PRAGMA table_info 检查存在性, 再决定是否 ALTER。
        如果多 process 同时初始化, ALTER TABLE 可能会偶发报错 — 用 try 宽容。
        """
        existing = {
            row[1]
            for row in conn.execute(
                "PRAGMA table_info(optimization_cycles)"
            ).fetchall()
        }
        if "seasonal_factor_avg" not in existing:
            try:
                conn.execute(
                    "ALTER TABLE optimization_cycles "
                    "ADD COLUMN seasonal_factor_avg REAL DEFAULT 1.0"
                )
            except Exception:
                pass
        if "seasonal_month" not in existing:
            try:
                conn.execute(
                    "ALTER TABLE optimization_cycles "
                    "ADD COLUMN seasonal_month INTEGER DEFAULT 1"
                )
            except Exception:
                pass
        # iter #38: perturbation impact tracking columns
        if "base_seasonal_factor_avg" not in existing:
            try:
                conn.execute(
                    "ALTER TABLE optimization_cycles "
                    "ADD COLUMN base_seasonal_factor_avg REAL DEFAULT 1.0"
                )
            except Exception:
                pass
        if "perturbation_count" not in existing:
            try:
                conn.execute(
                    "ALTER TABLE optimization_cycles "
                    "ADD COLUMN perturbation_count INTEGER DEFAULT 0"
                )
            except Exception:
                pass
        if "perturbation_total_multiplier" not in existing:
            try:
                conn.execute(
                    "ALTER TABLE optimization_cycles "
                    "ADD COLUMN perturbation_total_multiplier REAL DEFAULT 1.0"
                )
            except Exception:
                pass
        # iter #38: supply_offers perturbation tracking
        supply_existing = {
            row[1]
            for row in conn.execute("PRAGMA table_info(supply_offers)").fetchall()
        }
        for col, sqltype, default in [
            ("base_seasonal_multiplier", "REAL", "1.0"),
            ("seasonal_multiplier", "REAL", "1.0"),
            ("perturbation_applied", "INTEGER", "0"),
        ]:
            if col not in supply_existing:
                try:
                    conn.execute(
                        f"ALTER TABLE supply_offers ADD COLUMN {col} {sqltype} DEFAULT {default}"
                    )
                except Exception:
                    pass
