"""求解器耗时分析。"""

from typing import Any, Dict, List, Optional, Tuple

from ._schema import _BucketSpec

from ._base import PersistenceBase


class SolverMixin(PersistenceBase):
    """SolverMixin —— 由 agents/persistence.py 拆分而来（iter #77）。"""

    def get_cycle_duration_by_problem_size(
        self,
        since_sim_day: Optional[int] = None,
        until_sim_day: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        iter #67: Solver wall-time + efficiency breakdown by problem size.

        Bucket definition (based on ``optimization_cycles.total_tons``):
        - "small"  :  total_tons < 10
        - "medium" :  10 <= total_tons < 50
        - "large"  :  50 <= total_tons < 200
        - "xlarge" :  total_tons >= 200

        For each bucket we report:
        - count, pct_of_total
        - wall_duration_ms stats: mean / median / min / max / stddev
        - cost stats: mean total_cost_sek, mean cost_per_ton_sek
        - distance stats: mean total_distance_km
        - mean n_matches (problem complexity proxy)
        - solver_status breakdown (optimal / feasible / infeasible / unknown)
        - dominant_material (most common material_type in bucket)

        Args:
            since_sim_day, until_sim_day: optional time window

        Returns:
            {
              n_cycles, n_buckets, since_sim_day, until_sim_day,
              bucket_bounds: [
                {label, min_tons, max_tons, ...}, ...
              ],
              buckets: [
                {label, min_tons, max_tons, count, pct_of_total,
                 duration_ms: {mean, median, min, max, stddev},
                 cost_sek: {mean, median},
                 cost_per_ton_sek: {mean, median},
                 distance_km: {mean, median},
                 n_matches: {mean, median},
                 solver_status_counts: {optimal, feasible, infeasible, unknown},
                 dominant_material: str | None},
                ...
              ],
              scaling_signal: "linear" | "superlinear" | "sublinear" | "unknown"
                # compares large vs small bucket p50 duration ratio to tonnage ratio
            }
        """
        cache_kwargs = {
            "since_sim_day": since_sim_day,
            "until_sim_day": until_sim_day,
        }
        _cache_key = self._cache_key("get_cycle_duration_by_problem_size", cache_kwargs)
        cached = self._cache_get(_cache_key)
        if cached is not None:
            return cached

        # Bucket definitions — kept in sync with the docstring above
        BUCKETS: List[_BucketSpec] = [
            {"label": "small",  "min_tons": 0,   "max_tons": 10},
            {"label": "medium", "min_tons": 10,  "max_tons": 50},
            {"label": "large",  "min_tons": 50,  "max_tons": 200},
            {"label": "xlarge", "min_tons": 200, "max_tons": None},  # no upper bound
        ]

        where_clauses: List[str] = [
            "wall_duration_ms IS NOT NULL",
            "total_tons IS NOT NULL",
        ]
        params: List[Any] = []
        if since_sim_day is not None:
            where_clauses.append("sim_day >= ?")
            params.append(since_sim_day)
        if until_sim_day is not None:
            where_clauses.append("sim_day <= ?")
            params.append(until_sim_day)
        where_sql = " AND ".join(where_clauses)

        with self._conn() as conn:
            rows = conn.execute(
                f"""SELECT cycle_id, sim_day, total_tons, wall_duration_ms,
                          total_cost_sek, total_co2_kg, total_distance_km,
                          n_matches, solver_status, seasonal_factor_avg
                    FROM optimization_cycles
                    WHERE {where_sql}""",
                params,
            ).fetchall()
            # material_type lives on the matched_supply table; fetch via
            # a separate aggregate to avoid JOINing every cycle row.
            mat_rows = conn.execute(
                f"""SELECT s.cycle_id, s.material_type
                    FROM supply_offers s
                    JOIN optimization_cycles c ON c.cycle_id = s.cycle_id
                    WHERE {where_sql}""",
                params,
            ).fetchall()

        # Bucket each cycle row by total_tons
        def _bucket_for(tons: float) -> Optional[str]:
            for b in BUCKETS:
                max_tons = b["max_tons"]
                if max_tons is None:
                    if tons >= b["min_tons"]:
                        return b["label"]
                else:
                    if b["min_tons"] <= tons < max_tons:
                        return b["label"]
            return None

        bucket_cycles: Dict[str, List[Dict[str, Any]]] = {b["label"]: [] for b in BUCKETS}
        # material_type lookup per cycle (first material wins for "dominant")
        cycle_materials: Dict[str, str] = {}
        for r in mat_rows:
            cid = r["cycle_id"]
            mat = r["material_type"]
            if cid not in cycle_materials and mat:
                cycle_materials[cid] = mat

        for r in rows:
            label = _bucket_for(float(r["total_tons"]))
            if label is None:
                continue
            bucket_cycles[label].append({
                "cycle_id": r["cycle_id"],
                "sim_day": r["sim_day"],
                "total_tons": float(r["total_tons"]),
                "wall_duration_ms": float(r["wall_duration_ms"]),
                "total_cost_sek": float(r["total_cost_sek"] or 0),
                "total_co2_kg": float(r["total_co2_kg"] or 0),
                "total_distance_km": float(r["total_distance_km"] or 0),
                "n_matches": int(r["n_matches"] or 0),
                "solver_status": r["solver_status"] or "UNKNOWN",
                "material_type": cycle_materials.get(r["cycle_id"]),
            })

        def _stats(values: List[float]) -> Dict[str, Any]:
            if not values:
                return {
                    "mean": None, "median": None, "min": None,
                    "max": None, "stddev": None,
                }
            n = len(values)
            sorted_v = sorted(values)
            mean = sum(values) / n
            variance = sum((v - mean) ** 2 for v in values) / n
            stddev = variance ** 0.5
            if n == 1:
                median = sorted_v[0]
            else:
                idx = (n - 1) * 0.5
                lo = int(idx)
                hi = min(lo + 1, n - 1)
                frac = idx - lo
                median = sorted_v[lo] * (1 - frac) + sorted_v[hi] * frac
            return {
                "mean": round(mean, 2),
                "median": round(median, 2),
                "min": round(min(values), 2),
                "max": round(max(values), 2),
                "stddev": round(stddev, 2),
            }

        def _status_counts(statuses: List[str]) -> Dict[str, int]:
            counts = {"OPTIMAL": 0, "FEASIBLE": 0, "INFEASIBLE": 0, "UNKNOWN": 0}
            for s in statuses:
                key = s.upper() if s else "UNKNOWN"
                if key not in counts:
                    key = "UNKNOWN"
                counts[key] += 1
            return counts

        def _dominant_material(materials: List[Optional[str]]) -> Optional[str]:
            counts: Dict[str, int] = {}
            for m in materials:
                if m:
                    counts[m] = counts.get(m, 0) + 1
            if not counts:
                return None
            return max(counts.items(), key=lambda kv: kv[1])[0]

        n_cycles = sum(len(v) for v in bucket_cycles.values())

        buckets_out: List[Dict[str, Any]] = []
        for b in BUCKETS:
            cycles = bucket_cycles[b["label"]]
            count = len(cycles)
            durations = [c["wall_duration_ms"] for c in cycles]
            costs = [c["total_cost_sek"] for c in cycles]
            cost_per_ton = [
                c["total_cost_sek"] / c["total_tons"]
                for c in cycles
                if c["total_tons"] > 0
            ]
            distances = [c["total_distance_km"] for c in cycles]
            n_matches = [c["n_matches"] for c in cycles]
            statuses = [c["solver_status"] for c in cycles]
            materials = [c["material_type"] for c in cycles]
            buckets_out.append({
                "label": b["label"],
                "min_tons": b["min_tons"],
                "max_tons": b["max_tons"],
                "count": count,
                "pct_of_total": round(100 * count / n_cycles, 2) if n_cycles > 0 else 0.0,
                "duration_ms": _stats(durations),
                "cost_sek": _stats(costs),
                "cost_per_ton_sek": _stats(cost_per_ton),
                "distance_km": _stats(distances),
                "n_matches": _stats([float(v) for v in n_matches]),
                "solver_status_counts": _status_counts(statuses),
                "dominant_material": _dominant_material(materials),
            })

        # Scaling signal: ratio of (large median duration) to (small median duration)
        # vs ratio of (large median tons) to (small median tons).
        # If duration ratio ≈ tonnage ratio → linear, much larger → superlinear, smaller → sublinear.
        scaling_signal = "unknown"
        try:
            small = next(b for b in buckets_out if b["label"] == "small")
            large = next(b for b in buckets_out if b["label"] == "large")
            if (
                small["duration_ms"]["median"] is not None
                and large["duration_ms"]["median"] is not None
                and small["count"] > 0
                and large["count"] > 0
            ):
                # Approximate bucket midpoint for ratio
                small_mid = 5.0   # midpoint of [0, 10)
                large_mid = 125.0 # midpoint of [50, 200)
                tonnage_ratio = large_mid / small_mid  # 25x
                duration_ratio = (
                    large["duration_ms"]["median"] / small["duration_ms"]["median"]
                )
                if duration_ratio <= tonnage_ratio * 1.2:
                    scaling_signal = "linear"
                elif duration_ratio <= tonnage_ratio * 2.5:
                    scaling_signal = "superlinear"
                else:
                    scaling_signal = "superlinear"  # extreme — still superlinear
                # If duration barely changes despite large tonnage difference → sublinear
                if duration_ratio < max(1.0, tonnage_ratio * 0.3):
                    scaling_signal = "sublinear"
        except (StopIteration, KeyError, TypeError, ZeroDivisionError):
            scaling_signal = "unknown"

        result = {
            "n_cycles": n_cycles,
            "n_buckets": len(BUCKETS),
            "since_sim_day": since_sim_day,
            "until_sim_day": until_sim_day,
            "bucket_bounds": [
                {
                    "label": b["label"],
                    "min_tons": b["min_tons"],
                    "max_tons": b["max_tons"] if b["max_tons"] is not None else None,
                    "open_upper_bound": b["max_tons"] is None,
                }
                for b in BUCKETS
            ],
            "buckets": buckets_out,
            "scaling_signal": scaling_signal,
        }
        self._cache_set(_cache_key, result)
        return result

    def get_solver_duration_trend(
        self,
        window_size: int = 5,
        since_sim_day: Optional[int] = None,
        until_sim_day: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        iter #68: Solver duration p50/p95 trend over time windows.

        Complements get_cycle_duration_stats (aggregate) and
        get_cycle_duration_histogram (distribution) by showing how
        solver wall-time evolves across cycles.

        Each row represents a **chronological window of N cycles** (default 5),
        with p50 / p95 / mean / min / max wall_duration_ms for that window.
        Also computes an overall trend across windows (improving / declining /
        stable / unknown) by comparing first-half vs second-half p50.

        Args:
            window_size: number of cycles per window (clamped to [2, 30])
            since_sim_day, until_sim_day: optional sim_day filter

        Returns:
          {
            n_windows, window_size, n_cycles_evaluated,
            since_sim_day, until_sim_day,
            trend: 'improving' | 'declining' | 'stable' | 'unknown',
            trend_delta_pct, trend_confidence (0-1, based on n windows),
            windows: [
              {window_index, start_cycle_id, end_cycle_id, start_sim_day,
               end_sim_day, n_cycles, p50_ms, p95_ms, mean_ms,
               min_ms, max_ms, stddev_ms}, ...
            ],
            first_window_p50_ms, last_window_p50_ms,
          }

        Use cases:
        - Plot solver wall_duration_ms trend over time
        - Detect performance regression after model changes
        - Identify when solver started being slower
        """
        _cache_key = self._cache_key(
            "get_solver_duration_trend",
            {"window_size": window_size,
             "since_sim_day": since_sim_day, "until_sim_day": until_sim_day},
        )
        _cached = self._cache_get(_cache_key)
        if _cached is not None:
            return _cached

        window_size = max(2, min(30, int(window_size)))

        where_clauses: List[str] = ["wall_duration_ms IS NOT NULL"]
        params: List[Any] = []
        if since_sim_day is not None:
            where_clauses.append("sim_day >= ?")
            params.append(int(since_sim_day))
        if until_sim_day is not None:
            where_clauses.append("sim_day <= ?")
            params.append(int(until_sim_day))
        where_sql = " AND ".join(where_clauses)

        with self._conn() as conn:
            rows = conn.execute(
                f"""SELECT cycle_id, sim_day, sim_hour, wall_duration_ms,
                           total_tons, total_cost_sek, total_co2_kg,
                           n_matches, solver_status
                FROM optimization_cycles
                WHERE {where_sql}
                ORDER BY id ASC""",
                params,
            ).fetchall()

        n_cycles = len(rows)
        if n_cycles == 0:
            empty_result: Dict[str, Any] = {
                "n_windows": 0,
                "window_size": window_size,
                "n_cycles_evaluated": 0,
                "since_sim_day": since_sim_day,
                "until_sim_day": until_sim_day,
                "trend": "unknown",
                "trend_delta_pct": None,
                "trend_confidence": 0.0,
                "windows": [],
                "first_window_p50_ms": None,
                "last_window_p50_ms": None,
            }
            self._cache_set(_cache_key, empty_result)
            return empty_result

        def _percentile(values: List[float], pct: float) -> float:
            if not values:
                return 0.0
            sorted_v = sorted(values)
            n = len(sorted_v)
            if n == 1:
                return sorted_v[0]
            idx = (pct / 100.0) * (n - 1)
            lo = int(idx)
            hi = min(lo + 1, n - 1)
            frac = idx - lo
            return sorted_v[lo] * (1 - frac) + sorted_v[hi] * frac

        # Split into windows of `window_size` consecutive cycles (by id order).
        # If last window is partial (fewer than window_size cycles), keep it.
        windows_out: List[Dict[str, Any]] = []
        for w_idx in range(0, n_cycles, window_size):
            chunk = rows[w_idx:w_idx + window_size]
            durations = [float(r["wall_duration_ms"]) for r in chunk]
            p50 = _percentile(durations, 50)
            p95 = _percentile(durations, 95) if len(durations) >= 2 else durations[0]
            mean = sum(durations) / len(durations)
            variance = sum((d - mean) ** 2 for d in durations) / len(durations)
            stddev = variance ** 0.5
            windows_out.append({
                "window_index": len(windows_out),
                "start_cycle_id": chunk[0]["cycle_id"],
                "end_cycle_id": chunk[-1]["cycle_id"],
                "start_sim_day": int(chunk[0]["sim_day"]),
                "end_sim_day": int(chunk[-1]["sim_day"]),
                "n_cycles": len(chunk),
                "p50_ms": round(p50, 2),
                "p95_ms": round(p95, 2),
                "mean_ms": round(mean, 2),
                "min_ms": round(min(durations), 2),
                "max_ms": round(max(durations), 2),
                "stddev_ms": round(stddev, 2),
            })

        n_windows = len(windows_out)

        # Trend: first-half vs second-half median p50.
        # Lower wall_duration_ms = better (solver faster), so:
        #   first > last → improving (got faster)
        #   first < last → declining (got slower)
        if n_windows < 2:
            trend = "unknown"
            trend_delta_pct: Optional[float] = None
            trend_confidence = 0.0
        else:
            half = n_windows // 2
            first_half_p50s = [w["p50_ms"] for w in windows_out[:half]]
            second_half_p50s = [w["p50_ms"] for w in windows_out[half:]]
            first_avg = sum(first_half_p50s) / len(first_half_p50s)
            second_avg = sum(second_half_p50s) / len(second_half_p50s)
            if first_avg > 0:
                trend_delta_pct = round(
                    (second_avg - first_avg) / first_avg * 100, 2
                )
            else:
                trend_delta_pct = None
            # 10% threshold to avoid noisy classification
            if trend_delta_pct is not None:
                if trend_delta_pct < -10.0:
                    trend = "improving"
                elif trend_delta_pct > 10.0:
                    trend = "declining"
                else:
                    trend = "stable"
            else:
                trend = "unknown"
            # Confidence: ratio of second_half windows that agree with overall
            # trend direction (n_windows capped at 1.0)
            trend_confidence = min(1.0, n_windows / 5.0)

        result: Dict[str, Any] = {
            "n_windows": n_windows,
            "window_size": window_size,
            "n_cycles_evaluated": n_cycles,
            "since_sim_day": since_sim_day,
            "until_sim_day": until_sim_day,
            "trend": trend,
            "trend_delta_pct": trend_delta_pct,
            "trend_confidence": round(trend_confidence, 2),
            "windows": windows_out,
            "first_window_p50_ms": windows_out[0]["p50_ms"] if windows_out else None,
            "last_window_p50_ms": windows_out[-1]["p50_ms"] if windows_out else None,
        }
        self._cache_set(_cache_key, result)
        return result

    def get_solver_duration_by_season(
        self,
        since_sim_day: Optional[int] = None,
        until_sim_day: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        iter #69: Solver duration breakdown by season (winter/spring/summer/fall).

        Complements get_solver_duration_trend (iter #68, time-based) by
        slicing cycles into 4 seasons based on ``seasonal_month``:

        - winter:  Dec (12), Jan (1), Feb (2)
        - spring:  Mar (3), Apr (4), May (5)
        - summer:  Jun (6), Jul (7), Aug (8)
        - fall:    Sep (9), Oct (10), Nov (11)

        For each season we report wall_duration_ms p50/p95/mean/min/max/
        stddev, plus solver_status counts (OPTIMAL / FEASIBLE / INFEASIBLE
        / UNKNOWN). Also reports overall n_cycles + slowest/fastest season
        + global stats for comparison.

        Args:
            since_sim_day: optional lower bound (inclusive)
            until_sim_day: optional upper bound (inclusive)

        Returns:
          {
            n_cycles_evaluated, n_seasons_with_data,
            since_sim_day, until_sim_day,
            seasons: [
              {season, months: [int], n_cycles,
               p50_ms, p95_ms, mean_ms, min_ms, max_ms, stddev_ms,
               solver_status_counts: {OPTIMAL, FEASIBLE, INFEASIBLE, UNKNOWN}},
              ...
            ],
            global_stats: {
              n_cycles, p50_ms, p95_ms, mean_ms, min_ms, max_ms, stddev_ms
            },
            slowest_season: "winter" | "spring" | "summer" | "fall" | null,
            fastest_season: "winter" | "spring" | "summer" | "fall" | null,
            slowest_p50_ms, fastest_p50_ms,
            slowest_vs_fastest_pct: float | null,
          }

        Use cases:
        - Identify seasonal solver-performance drag (winter road conditions)
        - Compare solver wall-time across Sweden's 4 seasons
        - Spot if INFEASIBLE counts spike in any season (harder problem instances)
        """
        _cache_key = self._cache_key(
            "get_solver_duration_by_season",
            {"since_sim_day": since_sim_day, "until_sim_day": until_sim_day},
        )
        _cached = self._cache_get(_cache_key)
        if _cached is not None:
            return _cached

        season_defs: List[Dict[str, Any]] = [
            {"season": "winter", "months": [12, 1, 2]},
            {"season": "spring", "months": [3, 4, 5]},
            {"season": "summer", "months": [6, 7, 8]},
            {"season": "fall",   "months": [9, 10, 11]},
        ]

        where_clauses: List[str] = ["wall_duration_ms IS NOT NULL"]
        params: List[Any] = []
        if since_sim_day is not None:
            where_clauses.append("sim_day >= ?")
            params.append(int(since_sim_day))
        if until_sim_day is not None:
            where_clauses.append("sim_day <= ?")
            params.append(int(until_sim_day))
        where_sql = " AND ".join(where_clauses)

        with self._conn() as conn:
            rows = conn.execute(
                f"""SELECT seasonal_month, wall_duration_ms, solver_status
                    FROM optimization_cycles
                    WHERE {where_sql}
                      AND seasonal_month BETWEEN 1 AND 12""",
                params,
            ).fetchall()

        # Bucket rows by season (using month as the join key)
        season_buckets: Dict[str, List[Dict[str, Any]]] = {
            sd["season"]: [] for sd in season_defs
        }
        for r in rows:
            m = r["seasonal_month"]
            for sd in season_defs:
                if m in sd["months"]:
                    season_buckets[sd["season"]].append(
                        {"duration": float(r["wall_duration_ms"]),
                         "status": r["solver_status"]}
                    )
                    break

        def _status_counts(statuses: List[Optional[str]]) -> Dict[str, int]:
            counts = {"OPTIMAL": 0, "FEASIBLE": 0, "INFEASIBLE": 0, "UNKNOWN": 0}
            for s in statuses:
                key = (s.upper() if s else "UNKNOWN")
                if key not in counts:
                    key = "UNKNOWN"
                counts[key] += 1
            return counts

        def _percentile(values: List[float], pct: float) -> float:
            if not values:
                return 0.0
            sorted_v = sorted(values)
            n = len(sorted_v)
            if n == 1:
                return sorted_v[0]
            idx = (pct / 100.0) * (n - 1)
            lo = int(idx)
            hi = min(lo + 1, n - 1)
            frac = idx - lo
            return sorted_v[lo] * (1 - frac) + sorted_v[hi] * frac

        seasons_out: List[Dict[str, Any]] = []
        for sd in season_defs:
            season = sd["season"]
            bucket = season_buckets[season]
            durations = [b["duration"] for b in bucket]
            statuses = [b["status"] for b in bucket]
            n = len(durations)
            if n == 0:
                seasons_out.append({
                    "season": season,
                    "months": sd["months"],
                    "n_cycles": 0,
                    "p50_ms": None,
                    "p95_ms": None,
                    "mean_ms": None,
                    "min_ms": None,
                    "max_ms": None,
                    "stddev_ms": None,
                    "solver_status_counts": {
                        "OPTIMAL": 0, "FEASIBLE": 0,
                        "INFEASIBLE": 0, "UNKNOWN": 0,
                    },
                })
                continue
            mean = sum(durations) / n
            variance = sum((d - mean) ** 2 for d in durations) / n
            stddev = variance ** 0.5
            seasons_out.append({
                "season": season,
                "months": sd["months"],
                "n_cycles": n,
                "p50_ms": round(_percentile(durations, 50), 2),
                "p95_ms": round(_percentile(durations, 95), 2),
                "mean_ms": round(mean, 2),
                "min_ms": round(min(durations), 2),
                "max_ms": round(max(durations), 2),
                "stddev_ms": round(stddev, 2),
                "solver_status_counts": _status_counts(statuses),
            })

        # Global stats across all cycles in window
        all_durations = [float(r["wall_duration_ms"]) for r in rows]
        n_total = len(all_durations)
        if n_total > 0:
            mean_g = sum(all_durations) / n_total
            variance_g = sum((d - mean_g) ** 2 for d in all_durations) / n_total
            stddev_g = variance_g ** 0.5
            global_stats = {
                "n_cycles": n_total,
                "p50_ms": round(_percentile(all_durations, 50), 2),
                "p95_ms": round(_percentile(all_durations, 95), 2),
                "mean_ms": round(mean_g, 2),
                "min_ms": round(min(all_durations), 2),
                "max_ms": round(max(all_durations), 2),
                "stddev_ms": round(stddev_g, 2),
            }
        else:
            global_stats = {
                "n_cycles": 0, "p50_ms": None, "p95_ms": None,
                "mean_ms": None, "min_ms": None, "max_ms": None,
                "stddev_ms": None,
            }

        # Slowest / fastest by p50 (only consider seasons with data)
        seasons_with_data = [s for s in seasons_out if s["n_cycles"] > 0]
        if seasons_with_data:
            slowest = max(seasons_with_data, key=lambda s: s["p50_ms"])
            fastest = min(seasons_with_data, key=lambda s: s["p50_ms"])
            slowest_season = slowest["season"]
            fastest_season = fastest["season"]
            slowest_p50 = slowest["p50_ms"]
            fastest_p50 = fastest["p50_ms"]
            if fastest_p50 and fastest_p50 > 0:
                slowest_vs_fastest_pct = round(
                    (slowest_p50 - fastest_p50) / fastest_p50 * 100, 2
                )
            else:
                slowest_vs_fastest_pct = None
        else:
            slowest_season = None
            fastest_season = None
            slowest_p50 = None
            fastest_p50 = None
            slowest_vs_fastest_pct = None

        result: Dict[str, Any] = {
            "n_cycles_evaluated": n_total,
            "n_seasons_with_data": len(seasons_with_data),
            "since_sim_day": since_sim_day,
            "until_sim_day": until_sim_day,
            "seasons": seasons_out,
            "global_stats": global_stats,
            "slowest_season": slowest_season,
            "fastest_season": fastest_season,
            "slowest_p50_ms": slowest_p50,
            "fastest_p50_ms": fastest_p50,
            "slowest_vs_fastest_pct": slowest_vs_fastest_pct,
        }
        self._cache_set(_cache_key, result)
        return result

    def get_solver_duration_by_status(
        self,
        since_sim_day: Optional[int] = None,
        until_sim_day: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        iter #71: Solver wall-time breakdown by solver_status (OPTIMAL / FEASIBLE / INFEASIBLE / UNKNOWN).

        Complements iter #68 (time-based: ``get_solver_duration_trend``) and
        iter #69 (season-based: ``get_solver_duration_by_season``) by slicing
        cycles into status buckets. Combined with iter #69, this gives a 3D
        view (time / season / status) of solver performance.

        For each status we report:
          - n_cycles
          - wall_duration_ms p50 / p95 / mean / min / max / stddev
          - pct_of_total (share of all cycles in window)
          - cost_sek mean + cost_per_ton_sek mean
          - n_matches mean (solver output volume)
          - tons mean (problem size proxy)

        Plus overall global stats and a "slowest_status" badge by p50.

        Args:
            since_sim_day: optional lower bound (inclusive)
            until_sim_day: optional upper bound (inclusive)

        Returns:
          {
            n_cycles_evaluated, n_statuses_with_data,
            since_sim_day, until_sim_day,
            statuses: [
              {status, n_cycles, pct_of_total,
               p50_ms, p95_ms, mean_ms, min_ms, max_ms, stddev_ms,
               mean_cost_sek, mean_cost_per_ton_sek,
               mean_n_matches, mean_tons}, ...
            ],
            global_stats: {n_cycles, p50_ms, p95_ms, mean_ms,
                           min_ms, max_ms, stddev_ms},
            slowest_status, fastest_status,
            slowest_p50_ms, fastest_p50_ms, slowest_vs_fastest_pct,
            infeasible_rate_pct,  # share of cycles that are INFEASIBLE
            feasible_rate_pct,    # share of cycles that are FEASIBLE
            optimal_rate_pct,     # share of cycles that are OPTIMAL
          }

        Use cases:
        - Detect if INFEASIBLE cycles are dramatically slower (solver
          exhausts search budget on hard instances).
        - Compare cost-per-ton across solver outcomes (FEASIBLE may be
          costlier because the solver had to compromise).
        - Compute infeasible_rate_pct as an SLO-style health metric.
        - Spot if OPTIMAL cycles have lower p50 vs FEASIBLE (FEASIBLE
          sometimes = solver worked harder to find any solution).
        """
        _cache_key = self._cache_key(
            "get_solver_duration_by_status",
            {"since_sim_day": since_sim_day, "until_sim_day": until_sim_day},
        )
        _cached = self._cache_get(_cache_key)
        if _cached is not None:
            return _cached

        # Always return the same 4 buckets for consistent frontend shape,
        # even if some buckets have zero data.
        status_defs: List[str] = ["OPTIMAL", "FEASIBLE", "INFEASIBLE", "UNKNOWN"]

        where_clauses: List[str] = ["wall_duration_ms IS NOT NULL"]
        params: List[Any] = []
        if since_sim_day is not None:
            where_clauses.append("sim_day >= ?")
            params.append(int(since_sim_day))
        if until_sim_day is not None:
            where_clauses.append("sim_day <= ?")
            params.append(int(until_sim_day))
        where_sql = " AND ".join(where_clauses)

        with self._conn() as conn:
            rows = conn.execute(
                f"""SELECT cycle_id, sim_day, wall_duration_ms,
                           solver_status, total_cost_sek, total_tons,
                           n_matches
                    FROM optimization_cycles
                    WHERE {where_sql}""",
                params,
            ).fetchall()

        n_total = len(rows)
        # Bucket rows by status (case-insensitive, fall back to UNKNOWN)
        status_buckets: Dict[str, List[Dict[str, Any]]] = {s: [] for s in status_defs}
        for r in rows:
            raw = r["solver_status"]
            key = (raw.upper() if raw else "UNKNOWN")
            if key not in status_buckets:
                key = "UNKNOWN"
            status_buckets[key].append({
                "duration": float(r["wall_duration_ms"]),
                "cost_sek": r["total_cost_sek"],
                "tons": r["total_tons"],
                "n_matches": r["n_matches"],
            })

        def _percentile(values: List[float], pct: float) -> float:
            if not values:
                return 0.0
            sorted_v = sorted(values)
            n = len(sorted_v)
            if n == 1:
                return sorted_v[0]
            idx = (pct / 100.0) * (n - 1)
            lo = int(idx)
            hi = min(lo + 1, n - 1)
            frac = idx - lo
            return sorted_v[lo] * (1 - frac) + sorted_v[hi] * frac

        def _safe_mean(values: List[Any]) -> Optional[float]:
            nums = [v for v in values if v is not None]
            if not nums:
                return None
            return round(sum(float(v) for v in nums) / len(nums), 2)

        statuses_out: List[Dict[str, Any]] = []
        for status in status_defs:
            bucket = status_buckets[status]
            durations = [b["duration"] for b in bucket]
            n = len(durations)
            if n == 0:
                statuses_out.append({
                    "status": status,
                    "n_cycles": 0,
                    "pct_of_total": 0.0,
                    "p50_ms": None,
                    "p95_ms": None,
                    "mean_ms": None,
                    "min_ms": None,
                    "max_ms": None,
                    "stddev_ms": None,
                    "mean_cost_sek": None,
                    "mean_cost_per_ton_sek": None,
                    "mean_n_matches": None,
                    "mean_tons": None,
                })
                continue
            mean = sum(durations) / n
            variance = sum((d - mean) ** 2 for d in durations) / n
            stddev = variance ** 0.5
            mean_cost = _safe_mean([b["cost_sek"] for b in bucket])
            mean_tons = _safe_mean([b["tons"] for b in bucket])
            mean_matches = _safe_mean([b["n_matches"] for b in bucket])
            if mean_cost is not None and mean_tons is not None and mean_tons > 0:
                mean_cpt = round(mean_cost / mean_tons, 2)
            else:
                mean_cpt = None
            statuses_out.append({
                "status": status,
                "n_cycles": n,
                "pct_of_total": round(100.0 * n / n_total, 2) if n_total > 0 else 0.0,
                "p50_ms": round(_percentile(durations, 50), 2),
                "p95_ms": round(_percentile(durations, 95), 2),
                "mean_ms": round(mean, 2),
                "min_ms": round(min(durations), 2),
                "max_ms": round(max(durations), 2),
                "stddev_ms": round(stddev, 2),
                "mean_cost_sek": mean_cost,
                "mean_cost_per_ton_sek": mean_cpt,
                "mean_n_matches": mean_matches,
                "mean_tons": mean_tons,
            })

        # Global stats across all cycles
        all_durations = [float(r["wall_duration_ms"]) for r in rows]
        n_all = len(all_durations)
        if n_all > 0:
            mean_g = sum(all_durations) / n_all
            variance_g = sum((d - mean_g) ** 2 for d in all_durations) / n_all
            stddev_g = variance_g ** 0.5
            global_stats = {
                "n_cycles": n_all,
                "p50_ms": round(_percentile(all_durations, 50), 2),
                "p95_ms": round(_percentile(all_durations, 95), 2),
                "mean_ms": round(mean_g, 2),
                "min_ms": round(min(all_durations), 2),
                "max_ms": round(max(all_durations), 2),
                "stddev_ms": round(stddev_g, 2),
            }
        else:
            global_stats = {
                "n_cycles": 0, "p50_ms": None, "p95_ms": None,
                "mean_ms": None, "min_ms": None, "max_ms": None,
                "stddev_ms": None,
            }

        # Slowest / fastest status by p50 (only buckets with data)
        with_data = [s for s in statuses_out if s["n_cycles"] > 0]
        if with_data:
            slowest = max(with_data, key=lambda s: s["p50_ms"])
            fastest = min(with_data, key=lambda s: s["p50_ms"])
            slowest_status = slowest["status"]
            fastest_status = fastest["status"]
            slowest_p50 = slowest["p50_ms"]
            fastest_p50 = fastest["p50_ms"]
            if fastest_p50 and fastest_p50 > 0:
                slowest_vs_fastest_pct = round(
                    (slowest_p50 - fastest_p50) / fastest_p50 * 100, 2
                )
            else:
                slowest_vs_fastest_pct = None
        else:
            slowest_status = None
            fastest_status = None
            slowest_p50 = None
            fastest_p50 = None
            slowest_vs_fastest_pct = None

        # Rate percentages
        def _rate(status: str) -> float:
            if n_total == 0:
                return 0.0
            n = len(status_buckets[status])
            return round(100.0 * n / n_total, 2)

        infeasible_rate_pct = _rate("INFEASIBLE")
        feasible_rate_pct = _rate("FEASIBLE")
        optimal_rate_pct = _rate("OPTIMAL")

        result: Dict[str, Any] = {
            "n_cycles_evaluated": n_total,
            "n_statuses_with_data": len(with_data),
            "since_sim_day": since_sim_day,
            "until_sim_day": until_sim_day,
            "statuses": statuses_out,
            "global_stats": global_stats,
            "slowest_status": slowest_status,
            "fastest_status": fastest_status,
            "slowest_p50_ms": slowest_p50,
            "fastest_p50_ms": fastest_p50,
            "slowest_vs_fastest_pct": slowest_vs_fastest_pct,
            "infeasible_rate_pct": infeasible_rate_pct,
            "feasible_rate_pct": feasible_rate_pct,
            "optimal_rate_pct": optimal_rate_pct,
        }
        self._cache_set(_cache_key, result)
        return result

    def get_solver_duration_by_season_status(
        self,
        since_sim_day: Optional[int] = None,
        until_sim_day: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        iter #72: Solver wall-time 3D heatmap (season × status).

        Complements iter #69 (``get_solver_duration_by_season``) and iter
        #71 (``get_solver_duration_by_status``) by slicing cycles along
        BOTH axes simultaneously, yielding a 4×4 = 16-cell matrix (4
        seasons × 4 statuses):

        - winter/spring/summer/fall × OPTIMAL/FEASIBLE/INFEASIBLE/UNKNOWN

        Each cell contains n_cycles + p50_ms + mean_ms + min/max + stddev
        + pct_of_total (share of all cycles in window). Cells with no data
        are reported as ``null`` values (not omitted) so the frontend can
        render a stable 16-cell heatmap.

        Also reports:
          - per-season rollup (sum of all statuses for that season)
          - per-status rollup (sum of all seasons for that status)
          - global stats across the entire matrix
          - slowest_cell / fastest_cell (season, status) by p50
          - top_5 slowest_cells (sorted by p50, useful for hotspot table)

        Args:
            since_sim_day: optional lower bound (inclusive)
            until_sim_day: optional upper bound (inclusive)

        Returns:
          {
            n_cycles_evaluated, n_cells_with_data,
            since_sim_day, until_sim_day,
            seasons: ["winter", "spring", "summer", "fall"],
            statuses: ["OPTIMAL", "FEASIBLE", "INFEASIBLE", "UNKNOWN"],
            cells: [
              {
                season, status, n_cycles,
                pct_of_total,
                p50_ms, p95_ms, mean_ms, min_ms, max_ms, stddev_ms,
                mean_cost_sek, mean_cost_per_ton_sek,
              },
              ...
            ],
            season_rollup: [
              {season, n_cycles, p50_ms, mean_ms}, ...
            ],
            status_rollup: [
              {status, n_cycles, p50_ms, mean_ms}, ...
            ],
            global_stats: {
              n_cycles, p50_ms, p95_ms, mean_ms,
              min_ms, max_ms, stddev_ms,
            },
            slowest_cell: {season, status, p50_ms} | null,
            fastest_cell: {season, status, p50_ms} | null,
            slowest_vs_fastest_pct: float | null,
            top_5_slowest_cells: [
              {season, status, n_cycles, p50_ms, mean_ms}, ...
            ],
          }

        Use cases:
        - 3D heatmap of solver performance (color = p50, size = n_cycles)
        - Spot "winter INFEASIBLE" hotspot — slowest cell across both axes
        - Detect "summer OPTIMAL" sweet spot — fastest cell across both axes
        - Find the top-5 problematic (season, status) combinations for ops
        """
        _cache_key = self._cache_key(
            "get_solver_duration_by_season_status",
            {"since_sim_day": since_sim_day, "until_sim_day": until_sim_day},
        )
        _cached = self._cache_get(_cache_key)
        if _cached is not None:
            return _cached

        season_defs: List[Dict[str, Any]] = [
            {"season": "winter", "months": [12, 1, 2]},
            {"season": "spring", "months": [3, 4, 5]},
            {"season": "summer", "months": [6, 7, 8]},
            {"season": "fall",   "months": [9, 10, 11]},
        ]
        status_defs: List[str] = ["OPTIMAL", "FEASIBLE", "INFEASIBLE", "UNKNOWN"]
        season_names: List[str] = [s["season"] for s in season_defs]

        # Build season lookup: month -> season
        month_to_season: Dict[int, str] = {}
        for sd in season_defs:
            for m in sd["months"]:
                month_to_season[m] = sd["season"]

        where_clauses: List[str] = ["wall_duration_ms IS NOT NULL"]
        params: List[Any] = []
        if since_sim_day is not None:
            where_clauses.append("sim_day >= ?")
            params.append(int(since_sim_day))
        if until_sim_day is not None:
            where_clauses.append("sim_day <= ?")
            params.append(int(until_sim_day))
        where_sql = " AND ".join(where_clauses)

        with self._conn() as conn:
            rows = conn.execute(
                f"""SELECT cycle_id, sim_day, wall_duration_ms,
                           seasonal_month, solver_status,
                           total_cost_sek, total_tons
                    FROM optimization_cycles
                    WHERE {where_sql}""",
                params,
            ).fetchall()

        n_total = len(rows)

        # Bucket rows by (season, status)
        # cells[(season, status)] = list of dicts {duration, cost_sek, tons}
        cells: Dict[Tuple[str, str], List[Dict[str, Any]]] = {
            (s, st): [] for s in season_names for st in status_defs
        }
        for r in rows:
            sm = r["seasonal_month"]
            raw = r["solver_status"]
            season = month_to_season.get(sm)
            if season is None:
                # Unknown month → assign to UNKNOWN cell of that status
                # (preserve data, bucket by status only).
                status = (raw.upper() if raw else "UNKNOWN")
                if status not in status_defs:
                    status = "UNKNOWN"
                # Use winter as default bucket so we don't drop the row.
                season = "winter"
            else:
                status = (raw.upper() if raw else "UNKNOWN")
                if status not in status_defs:
                    status = "UNKNOWN"
            cells[(season, status)].append({
                "duration": float(r["wall_duration_ms"]),
                "cost_sek": r["total_cost_sek"],
                "tons": r["total_tons"],
            })

        def _percentile(values: List[float], pct: float) -> float:
            if not values:
                return 0.0
            sorted_v = sorted(values)
            n = len(sorted_v)
            if n == 1:
                return sorted_v[0]
            idx = (pct / 100.0) * (n - 1)
            lo = int(idx)
            hi = min(lo + 1, n - 1)
            frac = idx - lo
            return sorted_v[lo] * (1 - frac) + sorted_v[hi] * frac

        def _safe_mean(values: List[Any]) -> Optional[float]:
            nums = [v for v in values if v is not None]
            if not nums:
                return None
            return round(sum(float(v) for v in nums) / len(nums), 2)

        # Build 16-cell matrix in stable order
        cells_out: List[Dict[str, Any]] = []
        for season in season_names:
            for status in status_defs:
                bucket = cells[(season, status)]
                durations = [b["duration"] for b in bucket]
                n = len(durations)
                if n == 0:
                    cells_out.append({
                        "season": season,
                        "status": status,
                        "n_cycles": 0,
                        "pct_of_total": 0.0,
                        "p50_ms": None,
                        "p95_ms": None,
                        "mean_ms": None,
                        "min_ms": None,
                        "max_ms": None,
                        "stddev_ms": None,
                        "mean_cost_sek": None,
                        "mean_cost_per_ton_sek": None,
                    })
                    continue
                mean = sum(durations) / n
                variance = sum((d - mean) ** 2 for d in durations) / n
                stddev = variance ** 0.5
                mean_cost = _safe_mean([b["cost_sek"] for b in bucket])
                mean_tons = _safe_mean([b["tons"] for b in bucket])
                if (
                    mean_cost is not None
                    and mean_tons is not None
                    and mean_tons > 0
                ):
                    mean_cpt = round(mean_cost / mean_tons, 2)
                else:
                    mean_cpt = None
                cells_out.append({
                    "season": season,
                    "status": status,
                    "n_cycles": n,
                    "pct_of_total": round(
                        100.0 * n / n_total, 2
                    ) if n_total > 0 else 0.0,
                    "p50_ms": round(_percentile(durations, 50), 2),
                    "p95_ms": round(_percentile(durations, 95), 2),
                    "mean_ms": round(mean, 2),
                    "min_ms": round(min(durations), 2),
                    "max_ms": round(max(durations), 2),
                    "stddev_ms": round(stddev, 2),
                    "mean_cost_sek": mean_cost,
                    "mean_cost_per_ton_sek": mean_cpt,
                })

        # Per-season rollup (across all statuses for that season)
        season_rollup: List[Dict[str, Any]] = []
        for season in season_names:
            all_durs: List[float] = []
            for status in status_defs:
                for b in cells[(season, status)]:
                    all_durs.append(b["duration"])
            n = len(all_durs)
            if n == 0:
                season_rollup.append({
                    "season": season,
                    "n_cycles": 0,
                    "p50_ms": None,
                    "mean_ms": None,
                })
                continue
            mean = sum(all_durs) / n
            season_rollup.append({
                "season": season,
                "n_cycles": n,
                "p50_ms": round(_percentile(all_durs, 50), 2),
                "mean_ms": round(mean, 2),
            })

        # Per-status rollup (across all seasons for that status)
        status_rollup: List[Dict[str, Any]] = []
        for status in status_defs:
            status_durs: List[float] = []
            for season in season_names:
                for b in cells[(season, status)]:
                    status_durs.append(b["duration"])
            n = len(status_durs)
            if n == 0:
                status_rollup.append({
                    "status": status,
                    "n_cycles": 0,
                    "p50_ms": None,
                    "mean_ms": None,
                })
                continue
            mean = sum(status_durs) / n
            status_rollup.append({
                "status": status,
                "n_cycles": n,
                "p50_ms": round(_percentile(status_durs, 50), 2),
                "mean_ms": round(mean, 2),
            })

        # Global stats
        all_durations = [float(r["wall_duration_ms"]) for r in rows]
        n_all = len(all_durations)
        if n_all > 0:
            mean_g = sum(all_durations) / n_all
            variance_g = sum((d - mean_g) ** 2 for d in all_durations) / n_all
            stddev_g = variance_g ** 0.5
            global_stats = {
                "n_cycles": n_all,
                "p50_ms": round(_percentile(all_durations, 50), 2),
                "p95_ms": round(_percentile(all_durations, 95), 2),
                "mean_ms": round(mean_g, 2),
                "min_ms": round(min(all_durations), 2),
                "max_ms": round(max(all_durations), 2),
                "stddev_ms": round(stddev_g, 2),
            }
        else:
            global_stats = {
                "n_cycles": 0,
                "p50_ms": None,
                "p95_ms": None,
                "mean_ms": None,
                "min_ms": None,
                "max_ms": None,
                "stddev_ms": None,
            }

        # Slowest / fastest cell
        with_data = [c for c in cells_out if c["n_cycles"] > 0 and c["p50_ms"] is not None]
        if with_data:
            slowest_cell_obj = max(with_data, key=lambda c: c["p50_ms"])
            fastest_cell_obj = min(with_data, key=lambda c: c["p50_ms"])
            slowest_cell = {
                "season": slowest_cell_obj["season"],
                "status": slowest_cell_obj["status"],
                "p50_ms": slowest_cell_obj["p50_ms"],
            }
            fastest_cell = {
                "season": fastest_cell_obj["season"],
                "status": fastest_cell_obj["status"],
                "p50_ms": fastest_cell_obj["p50_ms"],
            }
            sp = slowest_cell["p50_ms"]
            fp = fastest_cell["p50_ms"]
            if fp and fp > 0 and sp is not None:
                slowest_vs_fastest_pct = round(
                    (sp - fp) / fp * 100, 2
                )
            else:
                slowest_vs_fastest_pct = None
        else:
            slowest_cell = None
            fastest_cell = None
            slowest_vs_fastest_pct = None

        # Top-5 slowest cells (by p50)
        top_5_slowest = sorted(
            with_data, key=lambda c: c["p50_ms"], reverse=True
        )[:5]
        top_5_slowest_cells = [
            {
                "season": c["season"],
                "status": c["status"],
                "n_cycles": c["n_cycles"],
                "p50_ms": c["p50_ms"],
                "mean_ms": c["mean_ms"],
            }
            for c in top_5_slowest
        ]

        result: Dict[str, Any] = {
            "n_cycles_evaluated": n_total,
            "n_cells_with_data": len(with_data),
            "since_sim_day": since_sim_day,
            "until_sim_day": until_sim_day,
            "seasons": season_names,
            "statuses": status_defs,
            "cells": cells_out,
            "season_rollup": season_rollup,
            "status_rollup": status_rollup,
            "global_stats": global_stats,
            "slowest_cell": slowest_cell,
            "fastest_cell": fastest_cell,
            "slowest_vs_fastest_pct": slowest_vs_fastest_pct,
            "top_5_slowest_cells": top_5_slowest_cells,
        }
        self._cache_set(_cache_key, result)
        return result
