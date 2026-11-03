"""LLM 决策落库与成本分析（llm_decisions 表相关）。"""

from typing import Any, Dict, List, Optional
from datetime import datetime

from ._base import PersistenceBase
from ._forecast_math import ForecastMathMixin


class LlmMixin(PersistenceBase, ForecastMathMixin):
    """LlmMixin —— 由 agents/persistence.py 拆分而来（iter #77）。"""

    def record_llm_decision(
        self,
        cycle_id: str,
        decision_type: str,
        target_id: str,
        target_type: str = "",
        multiplier: Optional[float] = None,
        trend: Optional[str] = None,
        confidence: Optional[float] = None,
        reason: Optional[str] = None,
        source: str = "unknown",
        raw_json: Optional[str] = None,
        sim_day: Optional[int] = None,
        sim_hour: Optional[int] = None,
    ) -> None:
        """记一条 LLM 决策。决策后可以查询给报告画图。"""
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO llm_decisions
                   (cycle_id, sim_day, sim_hour, decision_type, target_id, target_type,
                    multiplier, trend, confidence, reason, source, raw_json, wall_timestamp)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    cycle_id,
                    sim_day,
                    sim_hour,
                    decision_type,
                    target_id,
                    target_type,
                    multiplier,
                    trend,
                    confidence,
                    reason,
                    source,
                    raw_json,
                    datetime.now().isoformat(),
                )
            )

    def record_llm_decisions_batch(
        self,
        cycle_id: str,
        decision_type: str,
        target_type: str,
        predictions: List[Dict[str, Any]],
        sim_day: Optional[int] = None,
        sim_hour: Optional[int] = None,
    ) -> int:
        """批量记 LLM 决策。返回插入行数。"""
        import json as _json
        if not predictions:
            return 0
        with self._conn() as conn:
            conn.executemany(
                """INSERT INTO llm_decisions
                   (cycle_id, sim_day, sim_hour, decision_type, target_id, target_type,
                    multiplier, trend, confidence, reason, source, raw_json, wall_timestamp)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        cycle_id,
                        sim_day,
                        sim_hour,
                        decision_type,
                        p.get("id") or p.get("target_id", ""),
                        target_type,
                        p.get("multiplier"),
                        p.get("trend"),
                        p.get("confidence"),
                        p.get("reason"),
                        p.get("source", "unknown"),
                        _json.dumps(p, ensure_ascii=False),
                        datetime.now().isoformat(),
                    )
                    for p in predictions
                ]
            )
        return len(predictions)

    def get_llm_decisions(
        self,
        decision_type: Optional[str] = None,
        target_id: Optional[str] = None,
        limit: Optional[int] = None,
        sim_day_min: Optional[int] = None,
        sim_day_max: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """查询 LLM 决策。可按类型/目标/时间/limit 过滤 (iter #18)。

        Args:
            decision_type: 'demand_prediction' | 'supply_prediction'
            target_id: 'DEM001' | 'SUP000'
            limit: 最多返多少行 (None = 不限)
            sim_day_min: 起始 sim_day (含)
            sim_day_max: 结束 sim_day (含)
        """
        sql = "SELECT * FROM llm_decisions"
        clauses: List[str] = []
        params: List[Any] = []
        if decision_type:
            clauses.append("decision_type = ?")
            params.append(decision_type)
        if target_id:
            clauses.append("target_id = ?")
            params.append(target_id)
        if sim_day_min is not None:
            clauses.append("sim_day >= ?")
            params.append(sim_day_min)
        if sim_day_max is not None:
            clauses.append("sim_day <= ?")
            params.append(sim_day_max)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY sim_day DESC, id DESC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]

    def get_llm_timeseries(self, decision_type: str = "demand_prediction") -> List[Dict[str, Any]]:
        """LLM 决策时间序列 (按 sim_day 求 avg multiplier / confidence)。

        返回: [{sim_day, n_decisions, avg_multiplier, avg_confidence,
                 llm_count, fallback_count, ...}, ...]
        """
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT sim_day,
                          COUNT(*) as n,
                          ROUND(AVG(multiplier), 3) as avg_mult,
                          ROUND(AVG(confidence), 3) as avg_conf,
                          SUM(CASE WHEN source='llm' THEN 1 ELSE 0 END) as llm_n,
                          SUM(CASE WHEN source='fallback' THEN 1 ELSE 0 END) as fb_n
                   FROM llm_decisions
                   WHERE decision_type = ?
                   GROUP BY sim_day
                   ORDER BY sim_day""",
                (decision_type,)
            ).fetchall()
            return [dict(r) for r in rows]

    def get_llm_cost_timeseries(self, since_sim_day: Optional[int] = None,
                                 until_sim_day: Optional[int] = None) -> List[Dict[str, Any]]:
        """iter #28: LLM cost 时间序列 (按 sim_day 聚合, from llm_decisions table)。

        Returns: [{sim_day, n_decisions, llm_n, fallback_n, source, total_calls,
                   avg_confidence, avg_multiplier, ...}, ...]

        Note: llm_decisions 表不直接存 cost_usd — 近似用 n_decisions 作为 proxy
        (iter #22 LLM tracker 有精确 cost, 但那是 in-memory; 此 endpoint 从 DB 拿)
        """
        where_clauses: List[str] = []
        params: List[Any] = []
        if since_sim_day is not None:
            where_clauses.append("sim_day >= ?")
            params.append(since_sim_day)
        if until_sim_day is not None:
            where_clauses.append("sim_day <= ?")
            params.append(until_sim_day)
        where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

        with self._conn() as conn:
            rows = conn.execute(
                f"""SELECT sim_day,
                          COUNT(*) as n_decisions,
                          SUM(CASE WHEN source='llm' THEN 1 ELSE 0 END) as llm_n,
                          SUM(CASE WHEN source='fallback' THEN 1 ELSE 0 END) as fallback_n,
                          ROUND(AVG(multiplier), 3) as avg_multiplier,
                          ROUND(AVG(confidence), 3) as avg_confidence,
                          ROUND(AVG(CASE WHEN source='llm' THEN 1 ELSE 0 END) * 100, 2) as llm_success_rate_pct
                   FROM llm_decisions
                   {where_sql}
                   GROUP BY sim_day
                   ORDER BY sim_day""",
                params,
            ).fetchall()
            return [dict(r) for r in rows]

    def get_llm_decision_targets(
        self,
        decision_type: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """
        iter #48: List unique LLM call targets with stats.

        Groups llm_decisions by target_id, returns per-target stats:
        - decision_type
        - n_calls
        - n_real_llm / n_fallback
        - last_called_sim_day
        - avg_multiplier (across calls)
        - avg_confidence

        Args:
            decision_type: optional filter ('demand_prediction' / etc.)
            limit: max targets to return (default 50)

        Returns:
            [{
              target_id, decision_type, target_type,
              n_calls, n_real_llm, n_fallback,
              last_called_sim_day, first_called_sim_day,
              avg_multiplier, avg_confidence,
            }, ...]
            Sorted by n_calls DESC (most-called targets first).
        """
        where_clauses: List[str] = []
        params: List[Any] = []
        if decision_type:
            where_clauses.append("decision_type = ?")
            params.append(decision_type)
        where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

        with self._conn() as conn:
            rows = conn.execute(
                f"""SELECT target_id, decision_type, target_type,
                          COUNT(*) AS n_calls,
                          SUM(CASE WHEN source='llm' THEN 1 ELSE 0 END) AS n_real_llm,
                          SUM(CASE WHEN source='fallback' THEN 1 ELSE 0 END) AS n_fallback,
                          MAX(sim_day) AS last_called_sim_day,
                          MIN(sim_day) AS first_called_sim_day,
                          ROUND(AVG(multiplier), 3) AS avg_multiplier,
                          ROUND(AVG(confidence), 3) AS avg_confidence
                   FROM llm_decisions
                   {where_sql}
                   GROUP BY target_id, decision_type
                   ORDER BY n_calls DESC
                   LIMIT ?""",
                params + [int(limit)],
            ).fetchall()
        return [dict(r) for r in rows]

    def get_llm_cost_by_decision_type(
        self,
        since_sim_day: Optional[int] = None,
        until_sim_day: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        iter #48: LLM usage breakdown by decision_type.

        Aggregates llm_decisions by decision_type ('demand_prediction' /
        'supply_prediction') to show which type uses LLM the most.

        Returns:
            [{
              decision_type: str,
              n_total: int,
              n_llm: int,
              n_fallback: int,
              llm_rate_pct: float,
              avg_multiplier: float | None,
              avg_confidence: float | None,
              n_unique_targets: int,
              first_decision_sim_day: int | None,
              last_decision_sim_day: int | None,
            }, ...]
            Sorted by n_total DESC.
        """
        where_clauses: List[str] = []
        params: List[Any] = []
        if since_sim_day is not None:
            where_clauses.append("sim_day >= ?")
            params.append(int(since_sim_day))
        if until_sim_day is not None:
            where_clauses.append("sim_day <= ?")
            params.append(int(until_sim_day))
        where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

        with self._conn() as conn:
            rows = conn.execute(
                f"""SELECT decision_type,
                          COUNT(*) AS n_total,
                          SUM(CASE WHEN source='llm' THEN 1 ELSE 0 END) AS n_llm,
                          SUM(CASE WHEN source='fallback' THEN 1 ELSE 0 END) AS n_fallback,
                          ROUND(AVG(multiplier), 3) AS avg_multiplier,
                          ROUND(AVG(confidence), 3) AS avg_confidence,
                          COUNT(DISTINCT target_id) AS n_unique_targets,
                          MIN(sim_day) AS first_decision_sim_day,
                          MAX(sim_day) AS last_decision_sim_day
                   FROM llm_decisions
                   {where_sql}
                   GROUP BY decision_type
                   ORDER BY n_total DESC""",
                params,
            ).fetchall()

        results = []
        for r in rows:
            d = dict(r)
            n_total = d.get("n_total") or 0
            n_llm = d.get("n_llm") or 0
            d["llm_rate_pct"] = round(100 * n_llm / max(n_total, 1), 2)
            results.append(d)
        return results

    def forecast_llm_cost(
        self,
        horizon: int = 7,
        history_n: int = 14,
        method: str = "linear",
        since_sim_day: Optional[int] = None,
        until_sim_day: Optional[int] = None,
    ) -> Dict[str, Any]:
        """iter #29: 预测 LLM usage/cost 时间序列。

        预测 5 个字段: n_decisions, llm_n, fallback_n,
        avg_multiplier, avg_confidence. 使用与 KPI forecast 相同的
        linear / moving_average / exponential_smoothing 方法。

        Note: 这是 usage forecast, 不是美元 cost forecast。精确 cost 仍以
        /api/admin/llm-stats 的 total_cost_usd 为准。
        """
        if horizon < 1 or horizon > 30:
            raise ValueError(f"horizon must be 1-30, got {horizon}")
        if history_n < 2 or history_n > 90:
            raise ValueError(f"history_n must be 2-90, got {history_n}")
        rows = self.get_llm_cost_timeseries(
            since_sim_day=since_sim_day,
            until_sim_day=until_sim_day,
        )
        if len(rows) < 2:
            return {
                "horizon": horizon,
                "history_n": history_n,
                "method": method,
                "last_sim_day": rows[-1]["sim_day"] if rows else None,
                "forecast_sim_days": [],
                "metrics": {},
                "note": "need at least 2 historical sim_days for LLM cost forecast",
            }

        try:
            import numpy as np
        except ImportError as e:
            raise ImportError("numpy required for LLM cost forecast") from e

        history = rows[-history_n:]
        sim_days = np.array([r["sim_day"] for r in history], dtype=float)
        forecast_sim_days = list(range(int(sim_days[-1]) + 1, int(sim_days[-1]) + horizon + 1))
        valid_methods = ("linear", "moving_average", "exponential_smoothing")
        if method not in valid_methods:
            raise ValueError(f"method must be one of {valid_methods}, got {method!r}")

        metric_names = ("n_decisions", "llm_n", "fallback_n", "avg_multiplier", "avg_confidence")
        metrics: Dict[str, Any] = {}
        for metric_name in metric_names:
            values = np.array([r.get(metric_name, 0) or 0 for r in history], dtype=float)
            if method == "linear":
                fit = self._fit_linear(sim_days, values)
            elif method == "moving_average":
                fit = self._fit_moving_average(sim_days, values)
            else:
                fit = self._fit_exponential_smoothing(sim_days, values)
            forecast_values = fit["forecast_values_fn"](np.array(forecast_sim_days, dtype=float))
            forecast = []
            for sim_day, value in zip(forecast_sim_days, forecast_values):
                ci_half = fit["ci_half"]
                forecast.append({
                    "sim_day": sim_day,
                    "value": round(float(value), 2),
                    "lower_95": round(float(value) - ci_half, 2),
                    "upper_95": round(float(value) + ci_half, 2),
                    "is_forecast": True,
                })
            metrics[metric_name] = {
                "history": [{"sim_day": r["sim_day"], "value": r.get(metric_name, 0) or 0,
                             "is_forecast": False} for r in history],
                "forecast": forecast,
                "trend": fit["trend"],
                "method": method,
                "r_squared": round(fit["r_squared"], 4),
                "residual_std": round(fit["residual_std"], 2),
                "mean_value": round(float(np.mean(values)), 2),
                **fit["method_meta"],
            }

        return {
            "horizon": horizon,
            "history_n": history_n,
            "method": method,
            "last_sim_day": int(sim_days[-1]),
            "forecast_sim_days": forecast_sim_days,
            "metrics": metrics,
        }
