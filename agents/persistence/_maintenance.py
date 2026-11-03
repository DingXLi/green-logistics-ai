"""DB 维护（VACUUM / ANALYZE）与维护日志。"""

from typing import Any, Dict, List
from datetime import datetime

from loguru import logger

from ._base import PersistenceBase


class MaintenanceMixin(PersistenceBase):
    """MaintenanceMixin —— 由 agents/persistence.py 拆分而来（iter #77）。"""

    def vacuum(self, triggered_by: str = "manual") -> Dict[str, Any]:
        """
        VACUUM + ANALYZE (iter #16 + iter #42 audit log) — SQLite 性能维护。

        VACUUM: rebuild DB file, 释放碎片空间, 减小文件体积
        ANALYZE: 收集统计信息, 帮助 query planner 选最优 index

        Args:
            triggered_by: 'manual' (default) | 'auto' | 'scheduled'
                          for audit log only.

        Returns:
            {action, size_before_bytes, size_after_bytes,
             reclaimed_bytes, reclaimed_pct, success, triggered_by, ran_at}
        """
        size_before = self.db_path.stat().st_size if self.db_path.exists() else 0
        ran_at = datetime.now().isoformat()
        try:
            with self._conn() as conn:
                conn.execute("VACUUM")
                conn.execute("ANALYZE")
                # iter #42: log to maintenance log table
                conn.execute(
                    """INSERT INTO db_maintenance_log
                       (action, size_before_bytes, size_after_bytes,
                        reclaimed_bytes, triggered_by, ran_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        "vacuum_analyze",
                        size_before,
                        -1,  # placeholder; updated after VACUUM completes
                        0,
                        triggered_by,
                        ran_at,
                    ),
                )
            size_after = self.db_path.stat().st_size if self.db_path.exists() else 0
            reclaimed = max(0, size_before - size_after)
            with self._conn() as conn:
                # Update the log row with actual size_after
                conn.execute(
                    """UPDATE db_maintenance_log
                       SET size_after_bytes = ?, reclaimed_bytes = ?
                       WHERE ran_at = ?""",
                    (size_after, reclaimed, ran_at),
                )
            return {
                "action": "vacuum_analyze",
                "size_before_bytes": size_before,
                "size_after_bytes": size_after,
                "reclaimed_bytes": reclaimed,
                "reclaimed_pct": round((1 - size_after / size_before) * 100, 2) if size_before > 0 else 0,
                "success": True,
                "triggered_by": triggered_by,
                "ran_at": ran_at,
            }
        except Exception as e:
            logger.error(f"VACUUM/ANALYZE failed: {e}")
            return {
                "action": "vacuum_analyze",
                "size_before_bytes": size_before,
                "size_after_bytes": size_before,
                "reclaimed_bytes": 0,
                "reclaimed_pct": 0,
                "success": False,
                "error": str(e),
                "triggered_by": triggered_by,
                "ran_at": ran_at,
            }

    def should_auto_vacuum(self) -> Dict[str, Any]:
        """
        iter #42: Auto-vacuum recommendation.

        Heuristics:
        - DB size grown > 30% since last vacuum
        - More than 1000 cycles since last vacuum
        - More than 7 days since last vacuum
        - First vacuum ever (no log rows)

        Returns:
            {
              should_vacuum: bool,
              reasons: [str, ...],
              stats: {
                db_size_bytes, db_size_mb,
                cycles_since_last_vacuum, days_since_last_vacuum,
                size_growth_pct_since_last_vacuum,
                last_vacuum_at, total_maintenance_runs,
              }
            }
        """
        # Get current DB size
        size_bytes = self.db_path.stat().st_size if self.db_path.exists() else 0

        # Get last vacuum info
        with self._conn() as conn:
            last = conn.execute(
                """SELECT ran_at, size_before_bytes, size_after_bytes
                   FROM db_maintenance_log
                   WHERE action = 'vacuum_analyze'
                   ORDER BY ran_at DESC
                   LIMIT 1"""
            ).fetchone()
            total_runs = conn.execute(
                "SELECT COUNT(*) FROM db_maintenance_log WHERE action = 'vacuum_analyze'"
            ).fetchone()[0]
            cycles_since = conn.execute(
                "SELECT COUNT(*) FROM optimization_cycles"
            ).fetchone()[0]

        reasons: List[str] = []
        last_vacuum_at = last["ran_at"] if last else None
        size_after_last = last["size_after_bytes"] if last else None

        # Heuristic 1: First vacuum ever
        if last is None:
            reasons.append("No vacuum has been run yet (first maintenance)")

        # Heuristic 2: Size growth > 30%
        if size_after_last and size_after_last > 0:
            growth_pct = (size_bytes - size_after_last) / size_after_last * 100
        else:
            growth_pct = None
        if growth_pct is not None and growth_pct > 30:
            reasons.append(
                f"DB size grew {growth_pct:.1f}% since last vacuum "
                f"({size_after_last/1024/1024:.1f}MB → {size_bytes/1024/1024:.1f}MB)"
            )

        # Heuristic 3: cycles since last vacuum > 1000
        # (we don't track "cycles since last vacuum" precisely, so use absolute cycle count)
        if last is None and cycles_since > 1000:
            reasons.append(f"DB has {cycles_since} cycles and never been vacuumed")
        elif last is not None and cycles_since > 1000:
            # Without per-vacuum cycle tracking, just flag if total cycles is large
            reasons.append(
                f"Total cycles: {cycles_since} (recommend vacuum if high churn)"
            )

        # Heuristic 4: more than 7 days since last vacuum
        days_since = None
        if last is not None:
            try:
                last_dt = datetime.fromisoformat(last["ran_at"])
                days_since = (datetime.now() - last_dt).days
                if days_since > 7:
                    reasons.append(f"{days_since} days since last vacuum (max 7 days recommended)")
            except (ValueError, TypeError):
                pass

        should = len(reasons) > 0
        return {
            "should_vacuum": should,
            "reasons": reasons,
            "stats": {
                "db_size_bytes": size_bytes,
                "db_size_mb": round(size_bytes / 1024 / 1024, 3),
                "total_cycles": cycles_since,
                "last_vacuum_at": last_vacuum_at,
                "days_since_last_vacuum": days_since,
                "size_growth_pct_since_last_vacuum": round(growth_pct, 1) if growth_pct is not None else None,
                "size_after_last_vacuum_bytes": size_after_last,
                "total_maintenance_runs": total_runs,
            },
        }

    def get_maintenance_log(self, limit: int = 20) -> List[Dict[str, Any]]:
        """iter #42: return recent maintenance log entries."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT id, action, size_before_bytes, size_after_bytes,
                          reclaimed_bytes, triggered_by, ran_at
                   FROM db_maintenance_log
                   ORDER BY ran_at DESC
                   LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
