"""异常周期检测与汇总。"""

from typing import Any, Dict, List, Optional

from ._base import PersistenceBase
from ._contract import SharedQueryMixin


class AnomalyMixin(PersistenceBase, SharedQueryMixin):
    """AnomalyMixin —— 由 agents/persistence.py 拆分而来（iter #77）。"""

    def detect_anomalous_cycles(
        self,
        z_threshold: float = 2.0,
        min_history: int = 5,
        metrics: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        iter #47: Statistical anomaly detection for cycle KPIs.

        Uses z-score (standard deviations from mean) to flag cycles where any
        of {cost_sek, co2_kg, util_pct, distance_km, tons} is significantly
        different from the historical norm. Useful for ops to spot:
        - Sudden cost spikes (potential solver bug, fuel price change)
        - CO2 anomalies (load emission regression)
        - Utilization drops (depot issue, vehicle outage)
        - Distance outliers (routing bug)

        Args:
            z_threshold: how many stddevs to flag (default 2.0 = ~5% extreme)
            min_history: need at least N cycles to compute stats (default 5)
            metrics: which KPIs to check (default all 5)

        Returns:
            [{
              cycle_id, sim_day, sim_hour, wall_timestamp,
              anomalies: [{metric, value, mean, stddev, z_score, severity}, ...],
              max_severity: "high" | "medium" | "low" | "none",
            }, ...]
        """
        if metrics is None:
            metrics = ["total_cost_sek", "total_co2_kg", "fleet_utilization_pct",
                       "total_distance_km", "total_tons"]

        with self._conn() as conn:
            rows = conn.execute(
                """SELECT cycle_id, sim_day, sim_hour, wall_timestamp,
                          total_cost_sek, total_co2_kg, fleet_utilization_pct,
                          total_distance_km, total_tons
                   FROM optimization_cycles
                   ORDER BY sim_day ASC, id ASC"""
            ).fetchall()

        if len(rows) < min_history:
            return []

        # Compute mean + stddev for each metric
        stats: Dict[str, Dict[str, float]] = {}
        for metric in metrics:
            values = [r[metric] for r in rows if r[metric] is not None]
            if not values:
                continue
            n = len(values)
            mean = sum(values) / n
            variance = sum((v - mean) ** 2 for v in values) / n
            stddev = variance ** 0.5
            stats[metric] = {"mean": mean, "stddev": stddev}

        # Flag each cycle
        anomalous = []
        for r in rows:
            cycle_anomalies = []
            for metric in metrics:
                if metric not in stats:
                    continue
                value = r[metric]
                if value is None:
                    continue
                s = stats[metric]
                if s["stddev"] < 1e-9:  # no variance = nothing to flag
                    continue
                z = abs(value - s["mean"]) / s["stddev"]
                if z >= z_threshold:
                    # severity: |z| > 3.0 high, 2.5-3.0 medium, 2.0-2.5 low
                    if z >= 3.0:
                        severity = "high"
                    elif z >= 2.5:
                        severity = "medium"
                    else:
                        severity = "low"
                    cycle_anomalies.append({
                        "metric": metric,
                        "value": round(value, 2),
                        "mean": round(s["mean"], 2),
                        "stddev": round(s["stddev"], 2),
                        "z_score": round(z, 2),
                        "severity": severity,
                    })

            if cycle_anomalies:
                # Compute max severity across this cycle's anomalies
                sev_order = {"high": 3, "medium": 2, "low": 1, "none": 0}
                max_sev = max(cycle_anomalies,
                              key=lambda a: sev_order.get(a["severity"], 0))
                anomalous.append({
                    "cycle_id": r["cycle_id"],
                    "sim_day": r["sim_day"],
                    "sim_hour": r["sim_hour"],
                    "wall_timestamp": r["wall_timestamp"],
                    "anomalies": cycle_anomalies,
                    "max_severity": max_sev["severity"],
                    "n_anomalies": len(cycle_anomalies),
                })

        return anomalous

    def get_anomaly_summary(
        self,
        z_threshold: float = 2.0,
        min_history: int = 5,
    ) -> Dict[str, Any]:
        """
        iter #66: Aggregate anomaly statistics across all cycles.

        Wraps ``detect_anomalous_cycles`` and adds per-metric counts,
        severity distribution, and multi-anomaly cycle detection. Useful
        for ops to answer questions like:
        - Which metric is the most unstable? (e.g. cost_sek spikes weekly)
        - Are anomalies clustered in time? (multi-anomaly cycles = systemic)
        - Is the anomaly rate increasing or stable?

        Args:
            z_threshold: forwarded to ``detect_anomalous_cycles``
            min_history: forwarded to ``detect_anomalous_cycles``

        Returns:
            {
              n_anomalous_cycles: int,
              n_total_cycles: int,
              anomaly_rate_pct: float,
              z_threshold: float,
              min_history: int,
              total_anomaly_events: int,           # total per-metric anomalies
              per_metric_counts: {metric: count, ...},    # raw counts
              per_metric_pct: {metric: pct, ...},          # % of cycles with this metric flagged
              per_severity_counts: {high: int, medium: int, low: int},
              top_anomalous_metrics: [{metric, count, pct_of_cycles}, ...]  # sorted desc
              cycles_with_multiple_anomalies: int,         # cycles with >= 2 metric anomalies
              multi_anomaly_rate_pct: float,               # cycles_with_multiple / n_anomalous
              most_common_metric: str | None,
              most_common_severity: str | None,
              insufficient_history: bool,                  # True if history < min_history
            }
        """
        cache_kwargs = {"z_threshold": float(z_threshold), "min_history": int(min_history)}
        _cache_key = self._cache_key("get_anomaly_summary", cache_kwargs)
        cached = self._cache_get(_cache_key)
        if cached is not None:
            return cached

        anomalies = self.detect_anomalous_cycles(
            z_threshold=z_threshold, min_history=min_history,
        )
        try:
            n_total = self.get_summary().get("n_cycles", 0)
        except Exception:
            n_total = 0

        insufficient_history = n_total < min_history

        # Initialize per-metric counter for all known metrics (so frontend
        # always gets consistent keys)
        all_metrics = [
            "total_cost_sek", "total_co2_kg", "fleet_utilization_pct",
            "total_distance_km", "total_tons",
        ]
        per_metric_counts: Dict[str, int] = {m: 0 for m in all_metrics}
        per_severity_counts: Dict[str, int] = {"high": 0, "medium": 0, "low": 0}
        cycles_with_multiple_anomalies = 0
        total_anomaly_events = 0

        for cyc in anomalies:
            cycle_anoms = cyc.get("anomalies", [])
            n_cycle = len(cycle_anoms)
            total_anomaly_events += n_cycle
            if n_cycle >= 2:
                cycles_with_multiple_anomalies += 1
            for a in cycle_anoms:
                metric = a.get("metric")
                severity = a.get("severity")
                if metric in per_metric_counts:
                    per_metric_counts[metric] += 1
                if severity in per_severity_counts:
                    per_severity_counts[severity] += 1

        # Per-metric percentage: % of total cycles where this metric was flagged
        per_metric_pct: Dict[str, float] = {}
        for m, c in per_metric_counts.items():
            per_metric_pct[m] = round(100 * c / n_total, 2) if n_total > 0 else 0.0

        # Top metrics (sorted desc by count, drop zero-count to keep list compact)
        top_metrics = [
            {"metric": m, "count": c, "pct_of_cycles": per_metric_pct[m]}
            for m, c in sorted(per_metric_counts.items(), key=lambda kv: kv[1], reverse=True)
            if c > 0
        ]

        n_anomalous = len(anomalies)
        most_common_metric = top_metrics[0]["metric"] if top_metrics else None
        most_common_severity = max(
            per_severity_counts.items(), key=lambda kv: kv[1]
        )[0] if any(per_severity_counts.values()) else None

        result = {
            "n_anomalous_cycles": n_anomalous,
            "n_total_cycles": n_total,
            "anomaly_rate_pct": round(100 * n_anomalous / n_total, 2) if n_total > 0 else 0.0,
            "z_threshold": float(z_threshold),
            "min_history": int(min_history),
            "total_anomaly_events": total_anomaly_events,
            "per_metric_counts": per_metric_counts,
            "per_metric_pct": per_metric_pct,
            "per_severity_counts": per_severity_counts,
            "top_anomalous_metrics": top_metrics,
            "cycles_with_multiple_anomalies": cycles_with_multiple_anomalies,
            "multi_anomaly_rate_pct": (
                round(100 * cycles_with_multiple_anomalies / n_anomalous, 2)
                if n_anomalous > 0 else 0.0
            ),
            "most_common_metric": most_common_metric,
            "most_common_severity": most_common_severity,
            "insufficient_history": insufficient_history,
        }
        self._cache_set(_cache_key, result)
        return result
