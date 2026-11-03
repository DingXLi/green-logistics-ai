"""运行时配置覆盖与 forecast method 偏好持久化。"""

from typing import Any, Dict, List, Optional
from datetime import datetime

from loguru import logger

from ._base import PersistenceBase


class ConfigMixin(PersistenceBase):
    """ConfigMixin —— 由 agents/persistence.py 拆分而来（iter #77）。"""

    def load_runtime_config(self) -> Dict[str, Any]:
        """Load all persisted runtime config overrides (called at startup).

        Returns: {key: parsed_value, ...} — only keys with a persisted row.
        Unknown keys in DB (e.g. after code update that removed the key)
        are ignored, with a debug log warning.
        """
        import json as _json
        overrides: Dict[str, Any] = {}
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT key, value FROM runtime_config"
            ).fetchall()
        for r in rows:
            try:
                overrides[r["key"]] = _json.loads(r["value"])
            except (ValueError, TypeError) as e:
                logger.warning(
                    f"runtime_config: bad value for key {r['key']}: {e}, skipping"
                )
        return overrides

    def save_runtime_config(self, key: str, value: Any) -> Dict[str, Any]:
        """Persist a single key=value to runtime_config.

        Returns: {key, value, applied: bool, updated_at}
        """
        import json as _json
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO runtime_config (key, value, updated_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(key) DO UPDATE SET
                       value = excluded.value,
                       updated_at = excluded.updated_at""",
                (key, _json.dumps(value), datetime.now().isoformat()),
            )
        return {
            "key": key,
            "value": value,
            "applied": True,
            "updated_at": datetime.now().isoformat(),
        }

    def delete_runtime_config(self, key: str) -> bool:
        """Delete a persisted override (returns True if row existed)."""
        with self._conn() as conn:
            cur = conn.execute(
                "DELETE FROM runtime_config WHERE key = ?", (key,)
            )
        return cur.rowcount > 0

    def list_runtime_config_overrides(self) -> List[Dict[str, Any]]:
        """Return all persisted overrides with their string value + parse status."""
        import json as _json
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT key, value, updated_at FROM runtime_config ORDER BY key"
            ).fetchall()
        results = []
        for r in rows:
            try:
                parsed = _json.loads(r["value"])
                parse_ok = True
            except (ValueError, TypeError):
                parsed = None
                parse_ok = False
            results.append({
                "key": r["key"],
                "value": r["value"],  # raw string
                "parsed_value": parsed,
                "parse_ok": parse_ok,
                "updated_at": r["updated_at"],
            })
        return results

    def save_method_pref(
        self,
        metric: str,
        method: str,
        r_squared: Optional[float] = None,
        history_n: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        保存 (或覆盖) 一个 metric 的最佳 method。

        UPSERT: 如果 metric 已存在, n_samples 累加 + 更新 method / r_squared;
        如果 method 改变, 重置 n_samples = 1 (新选择需要重新积累 confidence)。

        Returns:
            {metric, method, r_squared, history_n, n_samples, updated_at, action}
        """
        valid_methods = ("linear", "moving_average", "exponential_smoothing")
        if method not in valid_methods:
            raise ValueError(
                f"method must be one of {valid_methods}, got {method!r}"
            )
        valid_metrics = ("cost_sek", "co2_kg", "util_pct", "matches")
        if metric not in valid_metrics:
            raise ValueError(
                f"metric must be one of {valid_metrics}, got {metric!r}"
            )
        now = datetime.now().isoformat()
        with self._conn() as conn:
            existing = conn.execute(
                "SELECT best_method, n_samples FROM forecast_method_prefs WHERE metric = ?",
                (metric,),
            ).fetchone()
            if existing is None:
                conn.execute(
                    """INSERT INTO forecast_method_prefs
                       (metric, best_method, r_squared, history_n, n_samples, updated_at)
                       VALUES (?, ?, ?, ?, 1, ?)""",
                    (metric, method, r_squared, history_n, now),
                )
                action = "created"
                n_samples = 1
            else:
                if existing["best_method"] == method:
                    # 同一个 method 被重新选为最佳 → n_samples 累加
                    new_n_samples = (existing["n_samples"] or 1) + 1
                else:
                    # method 改变 → 重置 confidence counter
                    new_n_samples = 1
                conn.execute(
                    """UPDATE forecast_method_prefs
                       SET best_method = ?, r_squared = ?, history_n = ?,
                           n_samples = ?, updated_at = ?
                       WHERE metric = ?""",
                    (method, r_squared, history_n, new_n_samples, now, metric),
                )
                action = "updated_method_changed" if existing["best_method"] != method else "updated"
                n_samples = new_n_samples
        return {
            "metric": metric,
            "method": method,
            "r_squared": r_squared,
            "history_n": history_n,
            "n_samples": n_samples,
            "updated_at": now,
            "action": action,
        }

    def get_method_prefs(self) -> List[Dict[str, Any]]:
        """返回所有 metric 的最佳 method prefs (按 metric 名排序)。"""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT metric, best_method, r_squared, history_n,
                          n_samples, updated_at
                   FROM forecast_method_prefs
                   ORDER BY metric"""
            ).fetchall()
        return [
            {
                "metric": row["metric"],
                "best_method": row["best_method"],
                "r_squared": row["r_squared"],
                "history_n": row["history_n"],
                "n_samples": row["n_samples"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    def get_best_method(self, metric: str) -> Optional[str]:
        """返回 metric 的最佳 method, 或 None (未设置)。"""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT best_method FROM forecast_method_prefs WHERE metric = ?",
                (metric,),
            ).fetchone()
        return row["best_method"] if row else None

    def delete_method_pref(self, metric: str) -> bool:
        """删除单个 metric 的 pref。返回是否真的删了。"""
        with self._conn() as conn:
            cur = conn.execute(
                "DELETE FROM forecast_method_prefs WHERE metric = ?",
                (metric,),
            )
        return cur.rowcount > 0

    def clear_method_prefs(self) -> int:
        """删除所有 prefs。返回删除行数。"""
        with self._conn() as conn:
            cur = conn.execute("DELETE FROM forecast_method_prefs")
        return cur.rowcount
