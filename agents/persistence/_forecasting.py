"""预测校准（预测 vs 实际）追踪。"""

from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime

from ._base import PersistenceBase


class ForecastMixin(PersistenceBase):
    """ForecastMixin —— 由 agents/persistence.py 拆分而来（iter #77）。"""

    def record_forecast_predictions(
        self,
        metric: str,
        method: str,
        predictions: List[Dict[str, Any]],
        created_at_sim_day: int,
    ) -> int:
        """Record predicted values for a (metric, method) combination.

        Args:
            metric: e.g. 'cost_sek'
            method: e.g. 'linear'
            predictions: [{"sim_day": int, "value": float}, ...]
            created_at_sim_day: sim_day at time of prediction (last known cycle)

        Returns:
            int: number of rows inserted (skip predictions already recorded
                 for the same metric+method+forecast_sim_day+created_at_sim_day)
        """
        if not predictions:
            return 0
        rows_inserted = 0
        with self._conn() as conn:
            for p in predictions:
                sim_day = p.get("sim_day")
                value = p.get("value")
                if sim_day is None or value is None:
                    continue
                # Check for duplicate
                existing = conn.execute(
                    """SELECT id FROM forecast_predictions
                       WHERE metric = ? AND method = ?
                         AND forecast_sim_day = ?
                         AND created_at_sim_day = ?""",
                    (metric, method, int(sim_day), int(created_at_sim_day)),
                ).fetchone()
                if existing:
                    continue
                conn.execute(
                    """INSERT INTO forecast_predictions
                       (metric, method, forecast_sim_day, forecast_value,
                        created_at_sim_day, recorded_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        metric,
                        method,
                        int(sim_day),
                        float(value),
                        int(created_at_sim_day),
                        datetime.now().isoformat(),
                    ),
                )
                rows_inserted += 1
        return rows_inserted

    def backfill_forecast_actuals(self) -> int:
        """Compute actual values for predictions whose sim_day has arrived.

        Reads optimization_cycles to find actual cost_sek / co2_kg / matches,
        updates forecast_predictions. Returns count of rows updated.

        Skips predictions whose forecast_sim_day is in the future.
        """
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT id, metric, forecast_sim_day FROM forecast_predictions
                   WHERE actual_value IS NULL
                     AND forecast_sim_day <= (
                       SELECT COALESCE(MAX(sim_day), 0) FROM optimization_cycles
                     )"""
            ).fetchall()
            updated = 0
            for r in rows:
                metric = r["metric"]
                sim_day = r["forecast_sim_day"]
                # Map metric to optimization_cycles column
                metric_to_col = {
                    "cost_sek": "total_cost_sek",
                    "co2_kg": "total_co2_kg",
                    "matches": "n_matches",
                    "util_pct": "fleet_utilization_pct",
                }
                col = metric_to_col.get(metric)
                if col is None:
                    continue
                actual = conn.execute(
                    f"SELECT {col} AS actual FROM optimization_cycles WHERE sim_day = ?",
                    (sim_day,),
                ).fetchone()
                if actual is None:
                    continue
                actual_value = actual["actual"]
                if actual_value is None:
                    continue
                # Get predicted value
                pred_row = conn.execute(
                    "SELECT forecast_value FROM forecast_predictions WHERE id = ?",
                    (r["id"],),
                ).fetchone()
                if pred_row is None:
                    continue
                forecast_value = pred_row["forecast_value"]
                error = actual_value - forecast_value
                abs_pct = (abs(error) / abs(actual_value) * 100) if actual_value else None
                conn.execute(
                    """UPDATE forecast_predictions
                       SET actual_value = ?, error = ?, abs_pct_error = ?
                       WHERE id = ?""",
                    (actual_value, error, abs_pct, r["id"]),
                )
                updated += 1
        return updated

    def get_forecast_calibration(
        self,
        metric: Optional[str] = None,
        method: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Return calibration stats: MAE, MAPE, RMSE per metric/method.

        Args:
            metric: optional filter
            method: optional filter

        Returns:
            {
              overall: {n_predictions, n_evaluated, mae, mape_pct, rmse, bias},
              by_metric: {<metric>: {...same stats...}},
              by_method: {<method>: {...same stats...}},
              by_metric_method: {<metric>: {<method>: {...stats...}}},
            }
        """
        with self._conn() as conn:
            where_clauses = ["actual_value IS NOT NULL", "error IS NOT NULL"]
            params: List[Any] = []
            if metric:
                where_clauses.append("metric = ?")
                params.append(metric)
            if method:
                where_clauses.append("method = ?")
                params.append(method)
            where_sql = " AND ".join(where_clauses)

            rows = conn.execute(
                f"SELECT metric, method, forecast_value, actual_value, error, abs_pct_error "
                f"FROM forecast_predictions WHERE {where_sql}",
                params,
            ).fetchall()

            def _stats(rs: List[Any]) -> Dict[str, Any]:
                if not rs:
                    return {
                        "n_evaluated": 0, "mae": None, "rmse": None,
                        "mape_pct": None, "bias": None, "min_pct_err": None, "max_pct_err": None,
                    }
                errors = [r["error"] for r in rs]
                abs_pct = [r["abs_pct_error"] for r in rs if r["abs_pct_error"] is not None]
                mae = sum(abs(e) for e in errors) / len(errors)
                rmse = (sum(e * e for e in errors) / len(errors)) ** 0.5
                bias = sum(errors) / len(errors)  # +ve = under-prediction
                mape = sum(abs_pct) / len(abs_pct) if abs_pct else None
                return {
                    "n_evaluated": len(rs),
                    "mae": round(mae, 4),
                    "rmse": round(rmse, 4),
                    "mape_pct": round(mape, 2) if mape is not None else None,
                    "bias": round(bias, 4),
                    "min_pct_err": round(min(abs_pct), 2) if abs_pct else None,
                    "max_pct_err": round(max(abs_pct), 2) if abs_pct else None,
                }

            by_metric: Dict[str, List[Any]] = {}
            by_method: Dict[str, List[Any]] = {}
            by_mm: Dict[str, Dict[str, List[Any]]] = {}
            for r in rows:
                by_metric.setdefault(r["metric"], []).append(r)
                by_method.setdefault(r["method"], []).append(r)
                by_mm.setdefault(r["metric"], {}).setdefault(r["method"], []).append(r)

            return {
                "overall": _stats(rows),
                "by_metric": {m: _stats(rs) for m, rs in by_metric.items()},
                "by_method": {m: _stats(rs) for m, rs in by_method.items()},
                "by_metric_method": {
                    m: {meth: _stats(rs) for meth, rs in ms.items()}
                    for m, ms in by_mm.items()
                },
            }

    def count_forecast_predictions(
        self,
        metric: Optional[str] = None,
    ) -> int:
        """Count total forecast predictions (with or without actuals)."""
        with self._conn() as conn:
            if metric:
                row = conn.execute(
                    "SELECT COUNT(*) FROM forecast_predictions WHERE metric = ?",
                    (metric,),
                ).fetchone()
            else:
                row = conn.execute("SELECT COUNT(*) FROM forecast_predictions").fetchone()
        return row[0]

    def get_forecast_calibration_trend(
        self,
        metric: Optional[str] = None,
        method: Optional[str] = None,
        bucket: str = "evaluated_at_day",  # 'evaluated_at_day' | 'forecast_sim_day'
    ) -> List[Dict[str, Any]]:
        """
        iter #43: Calibration trend over time.

        Computes per-cycle calibration stats from forecast_predictions where
        actual_value is set. The "bucket" controls how time is grouped:
        - 'evaluated_at_day': when actual was filled in (uses recorded_at prefix? — fallback to forecast_sim_day)
        - 'forecast_sim_day': bucket by the target sim_day

        For each bucket (sim_day), we compute cumulative MAE / RMSE / MAPE /
        bias over all evaluated predictions up to and including that day.

        Returns:
            [{
              bucket_sim_day: int,
              n_evaluated: int,
              cumulative_mae: float,
              cumulative_rmse: float,
              cumulative_mape_pct: float,
              cumulative_bias: float,
            }, ...]
            Sorted by bucket_sim_day ASC.
        """
        with self._conn() as conn:
            where_clauses = ["actual_value IS NOT NULL", "error IS NOT NULL"]
            params: List[Any] = []
            if metric:
                where_clauses.append("metric = ?")
                params.append(metric)
            if method:
                where_clauses.append("method = ?")
                params.append(method)
            where_sql = " AND ".join(where_clauses)

            # Use forecast_sim_day as the time bucket (recordings happen after eval)
            rows = conn.execute(
                f"SELECT forecast_sim_day, error, abs_pct_error "
                f"FROM forecast_predictions WHERE {where_sql} "
                f"ORDER BY forecast_sim_day ASC, id ASC",
                params,
            ).fetchall()

        if not rows:
            return []

        # Group by sim_day
        from collections import defaultdict
        by_day: Dict[int, List[Any]] = defaultdict(list)
        for r in rows:
            by_day[int(r["forecast_sim_day"])].append(r)

        # Compute cumulative stats
        results: List[Dict[str, Any]] = []
        cumulative: List[Any] = []
        for day in sorted(by_day.keys()):
            cumulative.extend(by_day[day])
            errors = [r["error"] for r in cumulative]
            abs_pct = [r["abs_pct_error"] for r in cumulative if r["abs_pct_error"] is not None]
            mae = sum(abs(e) for e in errors) / len(errors)
            rmse = (sum(e * e for e in errors) / len(errors)) ** 0.5
            bias = sum(errors) / len(errors)
            mape = sum(abs_pct) / len(abs_pct) if abs_pct else None
            results.append({
                "bucket_sim_day": day,
                "n_evaluated": len(cumulative),
                "cumulative_mae": round(mae, 4),
                "cumulative_rmse": round(rmse, 4),
                "cumulative_mape_pct": round(mape, 2) if mape is not None else None,
                "cumulative_bias": round(bias, 4),
            })
        return results

    def get_prediction_accuracy_by_day(
        self,
        metric: Optional[str] = None,
        method: Optional[str] = None,
        lead_time_buckets: Optional[List[Tuple[int, int]]] = None,
        since_created_day: Optional[int] = None,
        until_created_day: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        iter #58: Per-day prediction accuracy with lead-time breakdown.

        Unlike get_forecast_calibration_trend (which is cumulative and bucketed
        by forecast_sim_day), this endpoint buckets by **created_at_sim_day**
        (the day the prediction was generated) and reports non-cumulative
        per-day accuracy, optionally broken down by lead-time buckets.

        Args:
            metric: optional filter on metric
            method: optional filter on method
            lead_time_buckets: optional list of (min_lead, max_lead) tuples
                (inclusive ranges). If None, default buckets are used:
                [(1, 1), (2, 3), (4, 7), (8, 14), (15, 30)].
            since_created_day: optional lower bound on created_at_sim_day
            until_created_day: optional upper bound on created_at_sim_day

        Returns:
          {
            metric_filter, method_filter,
            lead_time_buckets: [{label, min_lead, max_lead}, ...],
            day_range: {since_created_day, until_created_day},
            by_day: [{
              created_at_sim_day: int,
              n_predictions: int,           # predictions made that day
              n_evaluated: int,             # those with actual_value set
              n_pending: int,               # those waiting for actual
              overall_mape_pct: float,      # over all lead times
              by_lead_time: {<bucket_label>: {n_evaluated, mape_pct, mae, bias}},
            }, ...]
            overall: {n_predictions, n_evaluated, n_pending, overall_mape_pct},
          }
        """
        if lead_time_buckets is None:
            lead_time_buckets = [
                (1, 1), (2, 3), (4, 7), (8, 14), (15, 30),
            ]

        # Validate bucket ranges and convert to labels
        bucket_defs: List[Dict[str, Any]] = []
        for (lo, hi) in lead_time_buckets:
            if lo > hi:
                raise ValueError(
                    f"Invalid bucket: min_lead {lo} > max_lead {hi}"
                )
            label = f"{lo}-{hi}d"
            bucket_defs.append({"label": label, "min_lead": lo, "max_lead": hi})

        # Base where clause for filters
        where_clauses: List[str] = ["1=1"]
        params: List[Any] = []
        if metric:
            where_clauses.append("metric = ?")
            params.append(metric)
        if method:
            where_clauses.append("method = ?")
            params.append(method)
        if since_created_day is not None:
            where_clauses.append("created_at_sim_day >= ?")
            params.append(int(since_created_day))
        if until_created_day is not None:
            where_clauses.append("created_at_sim_day <= ?")
            params.append(int(until_created_day))
        where_sql = " AND ".join(where_clauses)

        # Pull all relevant rows (regardless of evaluation status)
        with self._conn() as conn:
            rows = conn.execute(
                f"""SELECT created_at_sim_day, forecast_sim_day,
                           error, abs_pct_error,
                           (actual_value IS NOT NULL) as evaluated
                FROM forecast_predictions
                WHERE {where_sql}
                ORDER BY created_at_sim_day ASC, id ASC""",
                params,
            ).fetchall()

        # Group by created_at_sim_day
        from collections import defaultdict
        by_day: Dict[int, List[Any]] = defaultdict(list)
        for r in rows:
            by_day[int(r["created_at_sim_day"])].append(r)

        # Build per-day breakdown
        results: List[Dict[str, Any]] = []
        grand_total = 0
        grand_evaluated = 0
        grand_pending = 0
        all_abs_pct: List[float] = []

        for day in sorted(by_day.keys()):
            day_rows = by_day[day]
            n_predictions = len(day_rows)
            n_evaluated = sum(1 for r in day_rows if r["evaluated"])
            n_pending = n_predictions - n_evaluated

            # Overall day stats (regardless of lead time)
            day_abs_pct = [
                r["abs_pct_error"] for r in day_rows
                if r["evaluated"] and r["abs_pct_error"] is not None
            ]
            day_errors = [
                r["error"] for r in day_rows if r["evaluated"]
            ]
            day_overall_mape = (
                round(sum(day_abs_pct) / len(day_abs_pct), 2)
                if day_abs_pct else None
            )
            day_mae = (
                round(sum(abs(e) for e in day_errors) / len(day_errors), 4)
                if day_errors else None
            )
            day_bias = (
                round(sum(day_errors) / len(day_errors), 4)
                if day_errors else None
            )

            # Per-lead-time bucket breakdown
            by_lead_time: Dict[str, Dict[str, Any]] = {}
            for bd in bucket_defs:
                lo, hi, label = bd["min_lead"], bd["max_lead"], bd["label"]
                bucket_rows = [
                    r for r in day_rows
                    if r["evaluated"]
                    and lo <= (int(r["forecast_sim_day"]) - int(r["created_at_sim_day"])) <= hi
                ]
                b_abs_pct = [
                    r["abs_pct_error"] for r in bucket_rows
                    if r["abs_pct_error"] is not None
                ]
                b_errors = [r["error"] for r in bucket_rows]
                if bucket_rows:
                    by_lead_time[label] = {
                        "n_evaluated": len(bucket_rows),
                        "mape_pct": round(sum(b_abs_pct) / len(b_abs_pct), 2)
                                    if b_abs_pct else None,
                        "mae": round(sum(abs(e) for e in b_errors) / len(b_errors), 4)
                               if b_errors else None,
                        "bias": round(sum(b_errors) / len(b_errors), 4)
                                if b_errors else None,
                    }
                else:
                    by_lead_time[label] = {
                        "n_evaluated": 0,
                        "mape_pct": None,
                        "mae": None,
                        "bias": None,
                    }

            results.append({
                "created_at_sim_day": day,
                "n_predictions": n_predictions,
                "n_evaluated": n_evaluated,
                "n_pending": n_pending,
                "overall_mape_pct": day_overall_mape,
                "overall_mae": day_mae,
                "overall_bias": day_bias,
                "by_lead_time": by_lead_time,
            })

            grand_total += n_predictions
            grand_evaluated += n_evaluated
            grand_pending += n_pending
            all_abs_pct.extend(day_abs_pct)

        overall_mape = (
            round(sum(all_abs_pct) / len(all_abs_pct), 2)
            if all_abs_pct else None
        )

        return {
            "metric_filter": metric,
            "method_filter": method,
            "lead_time_buckets": bucket_defs,
            "day_range": {
                "since_created_day": since_created_day,
                "until_created_day": until_created_day,
            },
            "by_day": results,
            "overall": {
                "n_predictions": grand_total,
                "n_evaluated": grand_evaluated,
                "n_pending": grand_pending,
                "overall_mape_pct": overall_mape,
            },
        }
