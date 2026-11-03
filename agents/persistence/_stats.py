"""运营统计与聚合分析（车辆、材料、留存、碳排等）。"""

from typing import Any, Dict, List, Optional, Tuple, Union
from datetime import datetime
import json

from ._base import PersistenceBase


class StatsMixin(PersistenceBase):
    """StatsMixin —— 由 agents/persistence.py 拆分而来（iter #77）。"""

    CO2_FACTORS_KG_PER_TON_KM = {
        # Heavy-duty truck (>12t), Euro VI diesel, average load factor
        "truck_heavy": 0.062,
        # Medium truck (3.5-12t)
        "truck_medium": 0.110,
        # Light commercial vehicle (<3.5t)
        "truck_light": 0.180,
        # Baseline reference: traditional non-optimized individual truck transport
        # (assumed heavy truck but with poor load factor, ~50% empty)
        "traditional_baseline": 0.124,
        # Optimized fleet: heavy truck with full backhauls
        "optimized_fleet": 0.062,
    }

    SEASON_MONTHS: Dict[str, List[int]] = {
        "winter": [12, 1, 2],   # Dec → Jan → Feb
        "spring": [3, 4, 5],
        "summer": [6, 7, 8],
        "fall":   [9, 10, 11],
    }

    SEASON_EMOJI: Dict[str, str] = {
        "winter": "❄️",
        "spring": "🌱",
        "summer": "☀️",
        "fall":   "🍂",
    }

    SEASON_NAMES: Dict[str, str] = {
        "winter": "Winter",
        "spring": "Spring",
        "summer": "Summer",
        "fall":   "Fall",
    }

    def get_match_distance_stats(self) -> Dict[str, Any]:
        """
        Match 距离统计 (iter #15) — 从 matches 表聚合 distance_km 指标。

        返回:
            total_matches, n_cycles_with_matches,
            avg_distance_km, min_distance_km, max_distance_km, median_distance_km,
            distance_distribution: {<10km: n, 10-50km: n, 50-100km: n, >100km: n}

        用途: 验证 OSM 距离是否合理 (大多数短距离, 偶尔长距离),
        监控 match quality (距离增加 = 运输成本上升)。
        """
        with self._conn() as conn:
            row = conn.execute(
                """SELECT COUNT(*) as total,
                          COUNT(DISTINCT cycle_id) as n_cycles,
                          AVG(distance_km) as avg_dist,
                          MIN(distance_km) as min_dist,
                          MAX(distance_km) as max_dist
                   FROM matches WHERE distance_km IS NOT NULL"""
            ).fetchone()

            # 距离分桶
            buckets = conn.execute(
                """SELECT
                      SUM(CASE WHEN distance_km < 10 THEN 1 ELSE 0 END) as short,
                      SUM(CASE WHEN distance_km >= 10 AND distance_km < 50 THEN 1 ELSE 0 END) as medium,
                      SUM(CASE WHEN distance_km >= 50 AND distance_km < 100 THEN 1 ELSE 0 END) as long_,
                      SUM(CASE WHEN distance_km >= 100 THEN 1 ELSE 0 END) as very_long
                   FROM matches WHERE distance_km IS NOT NULL"""
            ).fetchone()

            # 中位数 (SQLite 没有 MEDIAN, 用 PERCENTILE_CONT 模拟)
            all_distances = [
                r["distance_km"] for r in
                conn.execute("SELECT distance_km FROM matches WHERE distance_km IS NOT NULL ORDER BY distance_km").fetchall()
            ]
        total = row["total"] or 0
        if total == 0:
            return {
                "total_matches": 0,
                "n_cycles_with_matches": 0,
                "avg_distance_km": None,
                "min_distance_km": None,
                "max_distance_km": None,
                "median_distance_km": None,
                "distance_distribution": {
                    "short_<10km": 0,
                    "medium_10-50km": 0,
                    "long_50-100km": 0,
                    "very_long_>=100km": 0,
                },
            }
        median = all_distances[len(all_distances) // 2] if all_distances else None
        return {
            "total_matches": total,
            "n_cycles_with_matches": row["n_cycles"] or 0,
            "avg_distance_km": round(row["avg_dist"] or 0, 2),
            "min_distance_km": round(row["min_dist"] or 0, 2),
            "max_distance_km": round(row["max_dist"] or 0, 2),
            "median_distance_km": round(median, 2) if median is not None else None,
            "distance_distribution": {
                "short_<10km": buckets["short"] or 0,
                "medium_10-50km": buckets["medium"] or 0,
                "long_50-100km": buckets["long_"] or 0,
                "very_long_>=100km": buckets["very_long"] or 0,
            },
        }

    def get_db_stats(self) -> Dict[str, Any]:
        """
        DB 统计 (iter #15) — 返回 SQLite DB 大小 + 表行数 + 索引状态。

        用途: 监控 DB 健康 (磁盘占用, 表增长), 诊断性能问题。
        """
        import os as _os
        db_path = self.db_path
        try:
            db_size_bytes = _os.path.getsize(db_path) if db_path and _os.path.exists(db_path) else 0
        except Exception:
            db_size_bytes = 0

        with self._conn() as conn:
            table_stats: Dict[str, int] = {}
            for table in ("optimization_cycles", "supply_offers", "demand_requests",
                          "matches", "routes", "llm_decisions"):
                try:
                    row = conn.execute(f"SELECT COUNT(*) as cnt FROM {table}").fetchone()
                    table_stats[table] = row["cnt"] or 0
                except Exception:
                    table_stats[table] = -1  # table doesn't exist

            # Index list (sqlite_master)
            try:
                idx_rows = conn.execute(
                    "SELECT name, tbl_name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"
                ).fetchall()
                indexes = [{"name": r["name"], "table": r["tbl_name"]} for r in idx_rows]
            except Exception:
                indexes = []

            # 时间范围
            try:
                time_row = conn.execute(
                    "SELECT MIN(wall_timestamp) as oldest, MAX(wall_timestamp) as newest FROM optimization_cycles"
                ).fetchone()
                time_range = {
                    "oldest_cycle": time_row["oldest"],
                    "newest_cycle": time_row["newest"],
                }
            except Exception:
                time_range = {}

        return {
            "db_path": db_path,
            "db_size_bytes": db_size_bytes,
            "db_size_mb": round(db_size_bytes / 1024 / 1024, 3) if db_size_bytes else 0,
            "table_counts": table_stats,
            "total_rows": sum(c for c in table_stats.values() if c > 0),
            "indexes": indexes,
            "time_range": time_range,
        }

    def get_db_info(self) -> Dict[str, Any]:
        """
        DB 完整 info (iter #20) — 给 audit / debugging /ops 用的详细 DB metadata。

        包括:
        - db_path / db_size_bytes / db_size_mb / db_modified_at
        - md5_checksum (前 100KB, 用于 detect DB 变化)
        - sqlite_version / schema_version
        - table_counts / index_count / total_rows
        - vacuum_status (上次 VACUUM 时间)
        - 时间范围

        与 get_db_stats 区别:
- get_db_stats: 运行时 size + table counts (监控用, ~cheap)
- get_db_info: audit-friendly (checksum, version, ~10-50ms)
        """
        import os as _os
        import hashlib as _hashlib
        import sqlite3 as _sqlite3

        db_path = self.db_path
        db_exists = bool(db_path) and _os.path.exists(db_path)

        # md5 checksum (前 100KB, 避免读整个文件)
        md5 = ""
        size_bytes = 0
        mtime = None
        if db_exists:
            try:
                size_bytes = _os.path.getsize(db_path)
                mtime = _os.path.getmtime(db_path)
                with open(db_path, "rb") as f:
                    chunk = f.read(min(size_bytes, 102400))
                    md5 = _hashlib.md5(chunk).hexdigest()
            except Exception:
                pass

        # sqlite 版本 + schema 版本
        sqlite_version = _sqlite3.sqlite_version
        schema_version = 0
        vacuum_status = "unknown"
        with self._conn() as conn:
            try:
                schema_version_row = conn.execute("PRAGMA schema_version").fetchone()
                schema_version = schema_version_row[0] if schema_version_row else 0
            except Exception:
                pass
            try:
                vacuum_row = conn.execute(
                    "SELECT MAX(integrity_check) FROM pragma_integrity_check"
                ).fetchone()
                # pragma_integrity_check 没有 last_vacuum 概念, 使用 auto_vacuum setting
                auto_vacuum_row = conn.execute("PRAGMA auto_vacuum").fetchone()
                auto_vacuum = auto_vacuum_row[0] if auto_vacuum_row else 0
                vacuum_status = {
                    0: "disabled",
                    1: "full",
                    2: "incremental",
                }.get(auto_vacuum, "unknown")
            except Exception:
                pass

            # Table counts + indexes + time range
            table_counts: Dict[str, int] = {}
            for table in ("optimization_cycles", "supply_offers", "demand_requests",
                          "matches", "routes", "llm_decisions"):
                try:
                    row = conn.execute(f"SELECT COUNT(*) as cnt FROM {table}").fetchone()
                    table_counts[table] = row["cnt"] or 0
                except Exception:
                    table_counts[table] = -1

            try:
                idx_count_row = conn.execute(
                    "SELECT COUNT(*) as cnt FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"
                ).fetchone()
                index_count = idx_count_row["cnt"] or 0
            except Exception:
                index_count = 0

            try:
                time_row = conn.execute(
                    "SELECT MIN(wall_timestamp) as oldest, MAX(wall_timestamp) as newest FROM optimization_cycles"
                ).fetchone()
                time_range = {
                    "oldest_cycle": time_row["oldest"],
                    "newest_cycle": time_row["newest"],
                }
            except Exception:
                time_range = {}

        return {
            "db_path": db_path,
            "db_exists": db_exists,
            "db_size_bytes": size_bytes,
            "db_size_mb": round(size_bytes / 1024 / 1024, 3) if size_bytes else 0,
            "db_modified_at": datetime.fromtimestamp(mtime).isoformat() if mtime else None,
            "md5_checksum_first_100kb": md5,
            "sqlite_version": sqlite_version,
            "schema_version": schema_version,
            "auto_vacuum_mode": vacuum_status,
            "table_counts": table_counts,
            "total_rows": sum(c for c in table_counts.values() if c > 0),
            "index_count": index_count,
            "time_range": time_range,
        }

    def get_fleet_utilization_summary(
        self,
        since_sim_day: Optional[int] = None,
        until_sim_day: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        iter #49: Fleet utilization percentiles over time.

        Aggregates fleet_utilization_pct across cycles and returns
        mean / median / percentiles (p10, p25, p50, p75, p90, p99)
        + n_idle_cycles (< 25%) / n_busy_cycles (>= 75%).

        Args:
            since_sim_day: optional filter
            until_sim_day: optional filter

        Returns:
            {
              mean, median, p10, p25, p50, p75, p90, p99,
              n_cycles, n_idle_cycles, n_busy_cycles,
              min, max, stddev,
              since_sim_day, until_sim_day,
            }
        """
        where_clauses: List[str] = ["fleet_utilization_pct IS NOT NULL"]
        params: List[Any] = []
        if since_sim_day is not None:
            where_clauses.append("sim_day >= ?")
            params.append(int(since_sim_day))
        if until_sim_day is not None:
            where_clauses.append("sim_day <= ?")
            params.append(int(until_sim_day))
        where_sql = "WHERE " + " AND ".join(where_clauses)

        with self._conn() as conn:
            rows = conn.execute(
                f"""SELECT fleet_utilization_pct
                   FROM optimization_cycles
                   {where_sql}
                   ORDER BY sim_day ASC, id ASC""",
                params,
            ).fetchall()

        values = [r["fleet_utilization_pct"] for r in rows if r["fleet_utilization_pct"] is not None]
        n = len(values)
        if n == 0:
            return {
                "n_cycles": 0,
                "n_idle_cycles": 0,
                "n_busy_cycles": 0,
                "since_sim_day": since_sim_day,
                "until_sim_day": until_sim_day,
            }

        sorted_vals = sorted(values)
        mean = sum(values) / n
        median = sorted_vals[n // 2]
        # Percentile helper
        def pct(p):
            if n == 1:
                return sorted_vals[0]
            idx = (p / 100) * (n - 1)
            lo = int(idx)
            hi = min(lo + 1, n - 1)
            frac = idx - lo
            return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac

        variance = sum((v - mean) ** 2 for v in values) / n
        stddev = variance ** 0.5

        return {
            "n_cycles": n,
            "mean": round(mean, 2),
            "median": round(median, 2),
            "p10": round(pct(10), 2),
            "p25": round(pct(25), 2),
            "p50": round(pct(50), 2),
            "p75": round(pct(75), 2),
            "p90": round(pct(90), 2),
            "p99": round(pct(99), 2),
            "min": round(min(values), 2),
            "max": round(max(values), 2),
            "stddev": round(stddev, 2),
            "n_idle_cycles": sum(1 for v in values if v < 25),
            "n_busy_cycles": sum(1 for v in values if v >= 75),
            "since_sim_day": since_sim_day,
            "until_sim_day": until_sim_day,
        }

    def get_cycle_duration_stats(self, since_sim_day: Optional[int] = None,
                                  until_sim_day: Optional[int] = None) -> Dict[str, Any]:
        """
        iter #53: Solver wall-time distribution stats (milliseconds).

        Aggregates optimization_cycles.wall_duration_ms across cycles:
        - mean / median / min / max / stddev
        - percentiles p25 / p50 / p75 / p90 / p95 / p99
        - slow_cycles count (≥ 5000 ms, i.e. > 5s)
        - fast_cycles count (≤ 100 ms, near-instant)
        - n_total cycles sampled
        - total_solver_time_seconds = sum(duration) / 1000

        Args:
            since_sim_day, until_sim_day: optional time window

        Use cases:
        - Identify slow solver runs (potential OR-Tools timeout issues)
        - Track solver performance regression over time
        - Capacity planning (how many cycles / hour)
        """
        where_clauses: List[str] = ["wall_duration_ms IS NOT NULL"]
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
                f"""SELECT wall_duration_ms
                FROM optimization_cycles
                WHERE {where_sql}
                ORDER BY wall_duration_ms""",
                params,
            ).fetchall()

        if not rows:
            return {
                "n_cycles": 0,
                "mean_ms": None, "median_ms": None, "min_ms": None, "max_ms": None, "stddev_ms": None,
                "p25_ms": None, "p50_ms": None, "p75_ms": None, "p90_ms": None, "p95_ms": None, "p99_ms": None,
                "slow_cycles_count": 0,
                "fast_cycles_count": 0,
                "total_solver_time_seconds": 0.0,
                "since_sim_day": since_sim_day,
                "until_sim_day": until_sim_day,
            }

        values = sorted(float(r["wall_duration_ms"]) for r in rows)
        n = len(values)

        def _pct(p):
            if n == 1:
                return values[0]
            idx = (p / 100) * (n - 1)
            lo = int(idx)
            hi = min(lo + 1, n - 1)
            frac = idx - lo
            return values[lo] * (1 - frac) + values[hi] * frac

        mean = sum(values) / n
        variance = sum((v - mean) ** 2 for v in values) / n
        stddev = variance ** 0.5
        median = _pct(50)

        return {
            "n_cycles": n,
            "mean_ms": round(mean, 2),
            "median_ms": round(median, 2),
            "min_ms": round(min(values), 2),
            "max_ms": round(max(values), 2),
            "stddev_ms": round(stddev, 2),
            "p25_ms": round(_pct(25), 2),
            "p50_ms": round(median, 2),
            "p75_ms": round(_pct(75), 2),
            "p90_ms": round(_pct(90), 2),
            "p95_ms": round(_pct(95), 2),
            "p99_ms": round(_pct(99), 2),
            "slow_cycles_count": sum(1 for v in values if v >= 5000),  # > 5s
            "fast_cycles_count": sum(1 for v in values if v <= 100),  # < 100ms
            "total_solver_time_seconds": round(sum(values) / 1000, 2),
            "since_sim_day": since_sim_day,
            "until_sim_day": until_sim_day,
        }

    def get_cycle_duration_histogram(
        self,
        since_sim_day: Optional[int] = None,
        until_sim_day: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        iter #64: Cycle solver-duration histogram with fixed buckets.

        Same data as get_cycle_duration_stats but bucketed for visualization.
        Buckets:
          <100ms, 100-500ms, 500ms-1s, 1-5s, 5-10s, 10-30s, 30-60s, 60s+

        Each bucket reports count + pct_of_total.

        Args:
            since_sim_day, until_sim_day: optional sim_day window

        Returns:
          {
            n_cycles, n_buckets,
            since_sim_day, until_sim_day,
            buckets: [
              {label, min_ms, max_ms, count, pct},
              ...
            ],
            stats: {mean_ms, median_ms, min_ms, max_ms, stddev_ms,
                    slow_count, fast_count, total_seconds}
          }

        Use cases:
        - Visualize solver performance distribution in histogram chart
        - Identify bimodal distributions (some fast, some slow)
        - Spot outlier slow runs (60s+ bucket)
        """
        _cache_key = self._cache_key(
            "get_cycle_duration_histogram",
            {"since_sim_day": since_sim_day, "until_sim_day": until_sim_day},
        )
        _cached = self._cache_get(_cache_key)
        if _cached is not None:
            return _cached

        where_clauses: List[str] = ["wall_duration_ms IS NOT NULL"]
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
                f"""SELECT wall_duration_ms, sim_day, cycle_id, solver_status
                FROM optimization_cycles
                WHERE {where_sql}""",
                params,
            ).fetchall()

        # Define buckets (min_ms, max_ms, label)
        # Last bucket has max_ms = +inf (we use >= check)
        bucket_defs = [
            (0,    100,   "<100ms"),
            (100,  500,   "100-500ms"),
            (500,  1000,  "0.5-1s"),
            (1000, 5000,  "1-5s"),
            (5000, 10000, "5-10s"),
            (10000, 30000, "10-30s"),
            (30000, 60000, "30-60s"),
            (60000, float("inf"), "60s+"),
        ]

        if not rows:
            empty_buckets = [
                {
                    "label": label,
                    "min_ms": lo,
                    "max_ms": hi if hi != float("inf") else None,
                    "count": 0,
                    "pct": 0.0,
                }
                for (lo, hi, label) in bucket_defs
            ]
            result = {
                "n_cycles": 0,
                "n_buckets": len(bucket_defs),
                "since_sim_day": since_sim_day,
                "until_sim_day": until_sim_day,
                "buckets": empty_buckets,
                "stats": {
                    "mean_ms": None, "median_ms": None,
                    "min_ms": None, "max_ms": None, "stddev_ms": None,
                    "slow_count": 0, "fast_count": 0,
                    "total_seconds": 0.0,
                },
            }
            self._cache_set(_cache_key, result)
            return result

        values = [float(r["wall_duration_ms"]) for r in rows]
        n = len(values)

        # Compute bucket counts
        bucket_counts = [0] * len(bucket_defs)
        for v in values:
            for idx, (lo, hi, _label) in enumerate(bucket_defs):
                if lo <= v < hi:
                    bucket_counts[idx] += 1
                    break

        buckets_out = []
        for idx, (lo, hi, label) in enumerate(bucket_defs):
            count = bucket_counts[idx]
            pct = round(100 * count / n, 2) if n > 0 else 0.0
            buckets_out.append({
                "label": label,
                "min_ms": lo,
                "max_ms": hi if hi != float("inf") else None,
                "count": count,
                "pct": pct,
            })

        # Compute summary stats
        sorted_values = sorted(values)
        mean = sum(values) / n
        variance = sum((v - mean) ** 2 for v in values) / n
        stddev = variance ** 0.5
        if n == 1:
            median = values[0]
        else:
            mid_idx = (n - 1) / 2
            lo_idx = int(mid_idx)
            hi_idx = min(lo_idx + 1, n - 1)
            frac = mid_idx - lo_idx
            median = sorted_values[lo_idx] * (1 - frac) + sorted_values[hi_idx] * frac

        result = {
            "n_cycles": n,
            "n_buckets": len(bucket_defs),
            "since_sim_day": since_sim_day,
            "until_sim_day": until_sim_day,
            "buckets": buckets_out,
            "stats": {
                "mean_ms": round(mean, 2),
                "median_ms": round(median, 2),
                "min_ms": round(min(values), 2),
                "max_ms": round(max(values), 2),
                "stddev_ms": round(stddev, 2),
                "slow_count": sum(1 for v in values if v >= 5000),
                "fast_count": sum(1 for v in values if v <= 100),
                "total_seconds": round(sum(values) / 1000, 2),
            },
        }
        self._cache_set(_cache_key, result)
        return result

    def get_match_distance_buckets(self, since_sim_day: Optional[int] = None,
                                    until_sim_day: Optional[int] = None) -> Dict[str, Any]:
        """
        iter #53: Match distance distribution histogram.

        Aggregates matches.distance_km into 8 buckets:
        - 0-5 km (local)
        - 5-10 km (intra-city)
        - 10-25 km (inter-city short)
        - 25-50 km (inter-city medium)
        - 50-100 km (regional)
        - 100-200 km (long haul)
        - 200-500 km (very long haul)
        - 500+ km (extreme)

        Also returns:
        - total_matches: total matches sampled
        - total_distance_km: sum of all distances
        - avg_distance_km: mean per-match distance
        - median_distance_km: 50th percentile distance
        - p95_distance_km: 95th percentile distance

        Args:
            since_sim_day, until_sim_day: optional time window

        Use cases:
        - Identify long-distance matches (potential CO2 hotspots)
        - Compare intra-city vs regional patterns
        - Validate against expected regional logistics patterns
        """
        where_clauses: List[str] = ["distance_km IS NOT NULL"]
        params: List[Any] = []
        if since_sim_day is not None:
            # join with optimization_cycles for sim_day filter
            where_clauses.append("oc.sim_day >= ?")
            params.append(since_sim_day)
        if until_sim_day is not None:
            where_clauses.append("oc.sim_day <= ?")
            params.append(until_sim_day)
        where_sql = " AND ".join(where_clauses)

        # Join with cycles for sim_day filter (use LEFT JOIN if no filter)
        join_clause = (
            "JOIN optimization_cycles oc ON oc.cycle_id = matches.cycle_id"
            if (since_sim_day is not None or until_sim_day is not None)
            else ""
        )

        bucket_bounds = [
            (0, 5), (5, 10), (10, 25), (25, 50),
            (50, 100), (100, 200), (200, 500), (500, float("inf")),
        ]
        bucket_labels = [
            "0-5", "5-10", "10-25", "25-50",
            "50-100", "100-200", "200-500", "500+",
        ]

        with self._conn() as conn:
            rows = conn.execute(
                f"""SELECT distance_km FROM matches
                {join_clause}
                WHERE {where_sql}""",
                params,
            ).fetchall()

        if not rows:
            return {
                "total_matches": 0,
                "total_distance_km": 0.0,
                "avg_distance_km": None,
                "median_distance_km": None,
                "p95_distance_km": None,
                "buckets": [
                    {"label": label, "lower_km": lo, "upper_km": (hi if hi != float("inf") else None),
                     "count": 0, "share": 0.0}
                    for (lo, hi), label in zip(bucket_bounds, bucket_labels)
                ],
                "since_sim_day": since_sim_day,
                "until_sim_day": until_sim_day,
            }

        distances = sorted(float(r["distance_km"]) for r in rows)
        n = len(distances)
        total = sum(distances)
        mean = total / n

        def _pct(p):
            if n == 1:
                return distances[0]
            idx = (p / 100) * (n - 1)
            lo_i = int(idx)
            hi_i = min(lo_i + 1, n - 1)
            frac = idx - lo_i
            return distances[lo_i] * (1 - frac) + distances[hi_i] * frac

        # Bucket counts
        buckets = []
        for (lo, hi), label in zip(bucket_bounds, bucket_labels):
            count = sum(1 for d in distances if lo <= d < hi)
            buckets.append({
                "label": label,
                "lower_km": lo,
                "upper_km": None if hi == float("inf") else hi,
                "count": count,
                "share": round(count / n, 3),
            })

        return {
            "total_matches": n,
            "total_distance_km": round(total, 2),
            "avg_distance_km": round(mean, 2),
            "median_distance_km": round(_pct(50), 2),
            "p95_distance_km": round(_pct(95), 2),
            "buckets": buckets,
            "since_sim_day": since_sim_day,
            "until_sim_day": until_sim_day,
        }

    def get_cohort_by_hour_of_day(self, since_sim_day: Optional[int] = None,
                                   until_sim_day: Optional[int] = None) -> Dict[str, Any]:
        """
        iter #53: Cohort analysis grouped by sim_hour (hour of day, 0-23).

        Each cycle has a sim_hour field representing the hour at which the
        simulation step was taken. This endpoint groups cycles by sim_hour and
        returns KPI aggregates per hour bucket:
        - hour, n_cycles, total_tons, total_cost_sek, total_co2_kg
        - avg_match_count, avg_fleet_utilization_pct

        Useful for:
        - Identify which hours of day are most productive
        - Detect off-peak patterns (simulations typically run at fixed hour)

        Returns:
          {
            n_hours_buckets: int,
            n_cycles: int,
            hour_buckets: [
              {hour, hour_label, n_cycles, total_tons, total_cost_sek,
               total_co2_kg, avg_matches_per_cycle, avg_fleet_utilization_pct,
               time_of_day: 'night'|'morning'|'afternoon'|'evening'},
              ...
            ] sorted by hour ASC,
            since_sim_day, until_sim_day,
          }
        """
        where_clauses: List[str] = ["sim_hour IS NOT NULL"]
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
                f"""SELECT sim_hour,
                          COUNT(*) as n_cycles,
                          SUM(total_tons) as total_tons,
                          SUM(total_cost_sek) as total_cost_sek,
                          SUM(total_co2_kg) as total_co2_kg,
                          SUM(n_matches) as total_matches,
                          AVG(fleet_utilization_pct) as avg_util
                FROM optimization_cycles
                WHERE {where_sql}
                GROUP BY sim_hour
                ORDER BY sim_hour ASC""",
                params,
            ).fetchall()

        def _time_of_day(h):
            if 0 <= h <= 5:
                return "night"
            if 6 <= h <= 11:
                return "morning"
            if 12 <= h <= 17:
                return "afternoon"
            return "evening"

        def _hour_label(h):
            return f"{h:02d}:00"

        buckets = []
        n_total = 0
        for r in rows:
            h = int(r["sim_hour"])
            n = r["n_cycles"] or 0
            n_total += n
            buckets.append({
                "hour": h,
                "hour_label": _hour_label(h),
                "time_of_day": _time_of_day(h),
                "n_cycles": n,
                "total_tons": round(r["total_tons"] or 0, 2),
                "total_cost_sek": round(r["total_cost_sek"] or 0, 2),
                "total_co2_kg": round(r["total_co2_kg"] or 0, 2),
                "avg_matches_per_cycle": round((r["total_matches"] or 0) / n, 2) if n > 0 else 0,
                "avg_fleet_utilization_pct": round(r["avg_util"] or 0, 2),
            })

        return {
            "n_hours_buckets": len(buckets),
            "n_cycles": n_total,
            "hour_buckets": buckets,
            "since_sim_day": since_sim_day,
            "until_sim_day": until_sim_day,
        }

    def get_carbon_savings_summary(self, since_sim_day: Optional[int] = None,
                                    until_sim_day: Optional[int] = None,
                                    baseline_factor_key: str = "traditional_baseline") -> Dict[str, Any]:
        """
        iter #54: Carbon savings vs traditional baseline transport.

        Compares actual CO2 emitted by the multi-agent optimization system
        vs a hypothetical "traditional baseline" where all material was
        transported by individual trucks without optimization.

        Args:
            since_sim_day, until_sim_day: optional time window
            baseline_factor_key: which CO2 factor to use as baseline
                (key in CO2_FACTORS_KG_PER_TON_KM)

        Returns:
          {
            n_cycles, total_tons, total_distance_km, actual_co2_kg,
            baseline_co2_kg, savings_co2_kg, savings_pct,
            baseline_factor_kg_per_ton_km, baseline_factor_key,
            co2_per_ton_actual_kg, co2_per_ton_baseline_kg,
            since_sim_day, until_sim_day,
          }

        Use cases:
        - Quantify environmental impact ("we saved X tons of CO2")
        - Compare different baseline assumptions
        - Marketing/narrative for green logistics
        """
        if baseline_factor_key not in self.CO2_FACTORS_KG_PER_TON_KM:
            valid = sorted(self.CO2_FACTORS_KG_PER_TON_KM.keys())
            raise ValueError(f"Unknown baseline_factor_key {baseline_factor_key!r}. Valid: {valid}")

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
            row = conn.execute(
                f"""SELECT COUNT(*) as n_cycles,
                          SUM(total_tons) as total_tons,
                          SUM(total_distance_km) as total_distance_km,
                          SUM(total_co2_kg) as actual_co2_kg
                FROM optimization_cycles
                {where_sql}""",
                params,
            ).fetchone()

        n_cycles = row["n_cycles"] or 0
        total_tons = float(row["total_tons"] or 0)
        total_distance = float(row["total_distance_km"] or 0)
        actual_co2 = float(row["actual_co2_kg"] or 0)

        # Baseline CO2 = total_tons * total_distance * baseline_factor
        # (吨 km 总和 乘以 baseline 排放系数)
        baseline_factor = self.CO2_FACTORS_KG_PER_TON_KM[baseline_factor_key]
        baseline_co2 = total_tons * total_distance * baseline_factor

        savings_co2 = max(0.0, baseline_co2 - actual_co2)
        if baseline_co2 > 0:
            savings_pct = round(savings_co2 / baseline_co2 * 100, 2)
        else:
            savings_pct = 0.0

        return {
            "n_cycles": n_cycles,
            "total_tons": round(total_tons, 2),
            "total_distance_km": round(total_distance, 2),
            "actual_co2_kg": round(actual_co2, 2),
            "baseline_co2_kg": round(baseline_co2, 2),
            "savings_co2_kg": round(savings_co2, 2),
            "savings_pct": savings_pct,
            "co2_per_ton_actual_kg": round(actual_co2 / total_tons, 3) if total_tons > 0 else None,
            "co2_per_ton_baseline_kg": round(baseline_co2 / total_tons, 3) if total_tons > 0 else None,
            "baseline_factor_kg_per_ton_km": baseline_factor,
            "baseline_factor_key": baseline_factor_key,
            "available_factors": sorted(self.CO2_FACTORS_KG_PER_TON_KM.keys()),
            "since_sim_day": since_sim_day,
            "until_sim_day": until_sim_day,
        }

    def get_vehicle_stats(
        self,
        vehicle_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        iter #41: Vehicle historical aggregates.

        Per-vehicle aggregates across all cycles:
        - n_routes, total_distance_km, total_duration_hours
        - total_cost_sek, total_co2_kg
        - avg_distance_km, avg_duration_hours, avg_cost_per_km, avg_co2_per_km
        - first_cycle_id, last_cycle_id (chronological order)
        - last_sim_day (most recent cycle where vehicle was used)

        Sorted by total_distance_km DESC.

        Args:
            vehicle_id: optional, return only this vehicle
            limit: max number of vehicles to return (default 100)

        Returns:
            [{vehicle_id, n_routes, total_distance_km, total_duration_hours,
              total_cost_sek, total_co2_kg, avg_distance_km, avg_duration_hours,
              avg_cost_per_km_sek, avg_co2_per_km_kg,
              first_cycle_id, last_cycle_id, last_sim_day}, ...]
        """
        where_clauses: List[str] = ["vehicle_id IS NOT NULL"]
        params: List[Any] = []
        if vehicle_id is not None:
            where_clauses.append("vehicle_id = ?")
            params.append(vehicle_id)
        where_sql = " AND ".join(where_clauses)

        with self._conn() as conn:
            rows = conn.execute(
                f"""
                SELECT vehicle_id,
                       COUNT(*) as n_routes,
                       SUM(distance_km) as total_distance_km,
                       SUM(duration_hours) as total_duration_hours,
                       SUM(cost_sek) as total_cost_sek,
                       SUM(co2_kg) as total_co2_kg,
                       AVG(distance_km) as avg_distance_km,
                       AVG(duration_hours) as avg_duration_hours,
                       MIN(r.cycle_id) as first_cycle_id,
                       MAX(r.cycle_id) as last_cycle_id,
                       MAX(oc.sim_day) as last_sim_day
                FROM routes r
                LEFT JOIN optimization_cycles oc ON oc.cycle_id = r.cycle_id
                WHERE {where_sql}
                GROUP BY vehicle_id
                ORDER BY total_distance_km DESC
                LIMIT ?
                """,
                (*params, limit),
            ).fetchall()

        results: List[Dict[str, Any]] = []
        for r in rows:
            total_dist = r["total_distance_km"] or 0.0
            total_cost = r["total_cost_sek"] or 0.0
            total_co2 = r["total_co2_kg"] or 0.0
            results.append({
                "vehicle_id": r["vehicle_id"],
                "n_routes": r["n_routes"] or 0,
                "total_distance_km": round(total_dist, 2),
                "total_duration_hours": round(r["total_duration_hours"] or 0.0, 2),
                "total_cost_sek": round(total_cost, 2),
                "total_co2_kg": round(total_co2, 2),
                "avg_distance_km": round(r["avg_distance_km"] or 0.0, 2),
                "avg_duration_hours": round(r["avg_duration_hours"] or 0.0, 2),
                "avg_cost_per_km_sek": round(total_cost / total_dist, 3) if total_dist > 0 else None,
                "avg_co2_per_km_kg": round(total_co2 / total_dist, 3) if total_dist > 0 else None,
                "first_cycle_id": r["first_cycle_id"],
                "last_cycle_id": r["last_cycle_id"],
                "last_sim_day": r["last_sim_day"],
            })
        return results

    def get_vehicle_timeseries(
        self,
        vehicle_id: Optional[str] = None,
        metric: str = "co2_per_km",
        since_sim_day: Optional[int] = None,
        until_sim_day: Optional[int] = None,
        limit: int = 100,
    ) -> Dict[str, Any]:
        """
        iter #65: Per-vehicle time-series of efficiency metrics.

        Returns one row per cycle × per vehicle, with derived efficiency
        metrics computed from the routes table. Useful for plotting how each
        vehicle's per-cycle efficiency evolves over time.

        Args:
            vehicle_id: optional filter (only include this vehicle)
            metric: which efficiency metric to rank by. One of:
                - 'co2_per_km' (kg CO2 / km, lower = better)
                - 'co2_per_hour' (kg CO2 / hour, lower = better)
                - 'cost_per_km' (SEK / km, lower = better)
                - 'cost_per_hour' (SEK / hour, lower = better)
                - 'speed_km_per_hour' (km / hour, higher = better)
                - 'distance_km' (km per route, higher = better)
                - 'n_stops' (higher = better)
            since_sim_day: optional lower bound (inclusive)
            until_sim_day: optional upper bound (inclusive)
            limit: max rows to return (default 100)

        Returns:
          {
            metric, metric_description, direction,
            filter: {vehicle_id, since_sim_day, until_sim_day},
            n_routes_evaluated, n_routes_returned,
            timeseries: [
              {route_id, cycle_id, vehicle_id, sim_day, sim_hour,
               distance_km, duration_hours, cost_sek, co2_kg,
               n_stops, value}, ...
            ],
            per_vehicle_summary: {
              vehicle_id: {
                n_routes, mean_value, min_value, max_value, latest_value,
                trend ('improving' / 'declining' / 'stable'),
              }, ...
            },
          }

        Use cases:
        - Plot each vehicle's co2_per_km over time
        - Identify vehicles trending toward greener routes
        - Spot anomalous per-cycle efficiency drops
        """
        _cache_key = self._cache_key(
            "get_vehicle_timeseries",
            {"vehicle_id": vehicle_id, "metric": metric,
             "since_sim_day": since_sim_day, "until_sim_day": until_sim_day,
             "limit": limit},
        )
        _cached = self._cache_get(_cache_key)
        if _cached is not None:
            return _cached

        valid_metrics = {
            "co2_per_km": ("lower_is_better", "kg CO2 per km"),
            "co2_per_hour": ("lower_is_better", "kg CO2 per hour"),
            "cost_per_km": ("lower_is_better", "SEK per km"),
            "cost_per_hour": ("lower_is_better", "SEK per hour"),
            "speed_km_per_hour": ("higher_is_better", "km per hour"),
            "distance_km": ("higher_is_better", "route distance km"),
            "n_stops": ("higher_is_better", "stops per route"),
        }
        if metric not in valid_metrics:
            raise ValueError(
                f"Unknown metric {metric!r}. Valid: {list(valid_metrics)}"
            )

        direction, desc = valid_metrics[metric]

        where_clauses = ["r.vehicle_id IS NOT NULL", "r.distance_km > 0"]
        params: List[Any] = []
        if vehicle_id:
            where_clauses.append("r.vehicle_id = ?")
            params.append(vehicle_id)
        if since_sim_day is not None:
            where_clauses.append("c.sim_day >= ?")
            params.append(int(since_sim_day))
        if until_sim_day is not None:
            where_clauses.append("c.sim_day <= ?")
            params.append(int(until_sim_day))
        where_sql = " AND ".join(where_clauses)

        with self._conn() as conn:
            rows = conn.execute(
                f"""SELECT r.id as route_id, r.cycle_id, r.vehicle_id,
                          r.distance_km, r.duration_hours, r.cost_sek, r.co2_kg,
                          r.stops_json,
                          c.sim_day, c.sim_hour
                FROM routes r
                JOIN optimization_cycles c ON c.cycle_id = r.cycle_id
                WHERE {where_sql}
                ORDER BY c.sim_day DESC, c.sim_hour DESC, r.id DESC
                LIMIT ?""",
                (*params, limit),
            ).fetchall()

        results = []
        for r in rows:
            distance = float(r["distance_km"] or 0)
            duration = float(r["duration_hours"] or 0)
            cost = float(r["cost_sek"] or 0)
            co2 = float(r["co2_kg"] or 0)
            stops_json = r["stops_json"] or "[]"
            try:
                stops_list = json.loads(stops_json) if isinstance(stops_json, str) else stops_json
                n_stops = sum(1 for s in stops_list if s)
            except Exception:
                n_stops = 0

            if metric == "co2_per_km":
                v = co2 / distance if distance > 0 else None
                value = round(v, 4) if v is not None else None
            elif metric == "co2_per_hour":
                v = co2 / duration if duration > 0 else None
                value = round(v, 4) if v is not None else None
            elif metric == "cost_per_km":
                v = cost / distance if distance > 0 else None
                value = round(v, 4) if v is not None else None
            elif metric == "cost_per_hour":
                v = cost / duration if duration > 0 else None
                value = round(v, 4) if v is not None else None
            elif metric == "speed_km_per_hour":
                v = distance / duration if duration > 0 else None
                value = round(v, 2) if v is not None else None
            elif metric == "distance_km":
                value = round(distance, 2)
            elif metric == "n_stops":
                value = n_stops
            else:
                value = None

            results.append({
                "route_id": int(r["route_id"]),
                "cycle_id": r["cycle_id"],
                "vehicle_id": r["vehicle_id"],
                "sim_day": r["sim_day"],
                "sim_hour": r["sim_hour"],
                "distance_km": round(distance, 2),
                "duration_hours": round(duration, 3),
                "cost_sek": round(cost, 2),
                "co2_kg": round(co2, 2),
                "n_stops": n_stops,
                "value": value,
            })

        # Build per-vehicle summary (mean/min/max/latest + trend)
        per_vehicle: Dict[str, List[Dict[str, Any]]] = {}
        # results is ordered by sim_day DESC — append to vehicle lists in reverse
        # so they end up in chronological order
        for row in reversed(results):
            per_vehicle.setdefault(row["vehicle_id"], []).append(row)

        per_vehicle_summary: Dict[str, Dict[str, Any]] = {}
        for vid, hist in per_vehicle.items():
            values_hist = [r["value"] for r in hist if r["value"] is not None]
            if not values_hist:
                continue
            mean_v = sum(values_hist) / len(values_hist)
            min_v = min(values_hist)
            max_v = max(values_hist)
            latest_v = values_hist[-1]
            # Trend: compare first half vs second half (chronological)
            half = len(values_hist) // 2
            if half >= 1 and len(values_hist) >= 2:
                first_half_mean = sum(values_hist[:half]) / half
                second_half_mean = sum(values_hist[half:]) / (len(values_hist) - half)
                delta = second_half_mean - first_half_mean
                # For lower_is_better, decreasing = improving
                if direction == "lower_is_better":
                    if delta < -abs(first_half_mean) * 0.1:
                        trend = "improving"
                    elif delta > abs(first_half_mean) * 0.1:
                        trend = "declining"
                    else:
                        trend = "stable"
                else:
                    if delta > abs(first_half_mean) * 0.1:
                        trend = "improving"
                    elif delta < -abs(first_half_mean) * 0.1:
                        trend = "declining"
                    else:
                        trend = "stable"
            else:
                trend = "stable"

            per_vehicle_summary[vid] = {
                "n_routes": len(values_hist),
                "mean_value": round(mean_v, 4),
                "min_value": round(min_v, 4),
                "max_value": round(max_v, 4),
                "latest_value": round(latest_v, 4),
                "trend": trend,
                "first_sim_day": hist[0]["sim_day"],
                "last_sim_day": hist[-1]["sim_day"],
            }

        # results is ordered DESC; reverse to chronological for chart friendliness
        results.reverse()

        result = {
            "metric": metric,
            "metric_description": desc,
            "direction": direction,
            "filter": {
                "vehicle_id": vehicle_id,
                "since_sim_day": since_sim_day,
                "until_sim_day": until_sim_day,
            },
            "n_routes_evaluated": len(results),
            "n_routes_returned": min(limit, len(results)),
            "timeseries": results[:max(1, min(500, limit))],
            "per_vehicle_summary": per_vehicle_summary,
        }
        self._cache_set(_cache_key, result)
        return result

    def get_top_suppliers_by_efficiency(
        self,
        metric: str = "co2_per_ton",
        material_type: Optional[str] = None,
        limit: int = 10,
    ) -> Dict[str, Any]:
        """
        iter #55: Top suppliers ranked by efficiency metrics.

        Args:
            metric: which efficiency metric to rank by. One of:
                - 'co2_per_ton' (kg CO2 / ton delivered, lower = better)
                - 'cost_per_ton' (SEK / ton delivered, lower = better)
                - 'co2_per_match' (kg CO2 / match, lower = better)
                - 'match_rate' (matches / supply_offers, higher = better)
                - 'avg_distance' (avg distance per match, lower = better)
            material_type: optional filter
            limit: top N (default 10, max 100)

        Returns:
          {
            metric, direction ('lower_is_better' or 'higher_is_better'),
            n_suppliers_evaluated, n_suppliers_returned,
            top_suppliers: [{supply_id, material_type, value, ...context}, ...]
          }

        Use cases:
        - Identify greenest suppliers (low co2_per_ton)
        - Identify most cost-efficient (low cost_per_ton)
        - Identify best match-rate (high match_rate)
        - Identify shortest-distance (low avg_distance)
        """
        # iter #61: TTL cache check (bypassed if metric is invalid, see below)
        _cache_key = self._cache_key(
            "get_top_suppliers_by_efficiency",
            {"metric": metric, "material_type": material_type, "limit": limit},
        )
        _cached = self._cache_get(_cache_key)
        if _cached is not None:
            return _cached

        valid_metrics = {
            "co2_per_ton": ("lower_is_better", "kg CO2 per ton delivered"),
            "cost_per_ton": ("lower_is_better", "SEK per ton delivered"),
            "co2_per_match": ("lower_is_better", "kg CO2 per match"),
            "match_rate": ("higher_is_better", "matches per supply offer"),
            "avg_distance": ("lower_is_better", "avg km per match"),
        }
        if metric not in valid_metrics:
            raise ValueError(f"Unknown metric {metric!r}. Valid: {list(valid_metrics)}")

        direction, _desc = valid_metrics[metric]

        where_clauses = ["1=1"]
        params: List[Any] = []
        if material_type:
            where_clauses.append("s.material_type = ?")
            params.append(material_type)
        where_sql = " AND ".join(where_clauses)

        # Aggregate per supply_id: total_available, total_matched, sum matches,
        # sum of all match distances/co2/cost for ratio calc
        with self._conn() as conn:
            rows = conn.execute(
                f"""SELECT s.supply_id, s.material_type,
                          SUM(s.available_tons) as total_available,
                          COUNT(DISTINCT s.cycle_id) as n_cycles,
                          AVG(s.quality_score) as avg_quality
                FROM supply_offers s
                WHERE {where_sql}
                GROUP BY s.supply_id""",
                params,
            ).fetchall()

            results = []
            for r in rows:
                sid = r["supply_id"]
                match_data = conn.execute(
                    """SELECT COUNT(*) as n_matches,
                              COALESCE(SUM(m.tons), 0) as total_matched_tons,
                              COALESCE(AVG(m.distance_km), 0) as avg_distance,
                              COALESCE(SUM(m.distance_km), 0) as total_distance,
                              COALESCE(SUM(r2.co2_kg), 0) as total_co2,
                              COALESCE(SUM(r2.cost_sek), 0) as total_cost
                    FROM matches m
                    LEFT JOIN routes r2 ON r2.cycle_id = m.cycle_id AND r2.vehicle_id IS NOT NULL
                    WHERE m.supply_id = ?""",
                    (sid,)
                ).fetchone()

                n_matches = match_data["n_matches"] or 0
                total_matched = float(match_data["total_matched_tons"] or 0)
                avg_distance = float(match_data["avg_distance"] or 0)
                total_co2 = float(match_data["total_co2"] or 0)
                total_cost = float(match_data["total_cost"] or 0)
                total_available = float(r["total_available"] or 0)

                # Compute efficiency value based on metric
                if metric == "co2_per_ton":
                    value = round(total_co2 / total_matched, 3) if total_matched > 0 else None
                elif metric == "cost_per_ton":
                    value = round(total_cost / total_matched, 3) if total_matched > 0 else None
                elif metric == "co2_per_match":
                    value = round(total_co2 / n_matches, 3) if n_matches > 0 else None
                elif metric == "match_rate":
                    value = round(n_matches / max(1, r["n_cycles"]), 3) if r["n_cycles"] > 0 else None
                elif metric == "avg_distance":
                    value = round(avg_distance, 2) if n_matches > 0 else None
                else:
                    value = None

                # Skip supplies without matches for ton-based metrics
                if metric in ("co2_per_ton", "cost_per_ton") and total_matched == 0:
                    continue
                if metric == "co2_per_match" and n_matches == 0:
                    continue
                if metric == "avg_distance" and n_matches == 0:
                    continue

                results.append({
                    "supply_id": sid,
                    "material_type": r["material_type"],
                    "value": value,
                    "n_matches": n_matches,
                    "total_matched_tons": round(total_matched, 2),
                    "total_available_tons": round(total_available, 2),
                    "avg_quality_score": round(r["avg_quality"] or 0, 1),
                    "n_cycles_with_supply": r["n_cycles"],
                    "avg_distance_km": round(avg_distance, 2),
                })

        # Sort: lower_is_better → ascending, higher_is_better → descending
        results.sort(key=lambda x: x["value"] if x["value"] is not None else float("inf")
                     if direction == "lower_is_better"
                     else -(x["value"] if x["value"] is not None else 0))

        # iter #61: store in TTL cache before returning
        result = {
            "metric": metric,
            "metric_description": _desc,
            "direction": direction,
            "n_suppliers_evaluated": len(results),
            "n_suppliers_returned": min(limit, len(results)),
            "top_suppliers": results[:max(1, min(100, limit))],
        }
        self._cache_set(_cache_key, result)
        return result

    def get_top_cycles_by_efficiency(
        self,
        metric: str = "co2_per_ton",
        since_sim_day: Optional[int] = None,
        until_sim_day: Optional[int] = None,
        min_matches: int = 1,
        limit: int = 10,
    ) -> Dict[str, Any]:
        """
        iter #56: Top optimization cycles ranked by efficiency metrics.

        Args:
            metric: which efficiency metric to rank by. One of:
                - 'co2_per_ton' (kg CO2 / ton delivered, lower = better)
                - 'cost_per_ton' (SEK / ton delivered, lower = better)
                - 'co2_per_km' (kg CO2 / km, lower = better)
                - 'cost_per_km' (SEK / km, lower = better)
                - 'fleet_utilization' (% capacity used, higher = better)
                - 'match_rate_vs_offers' (matches / supply_offers, higher = better)
                - 'tons_per_cycle' (avg tons matched, higher = better)
            since_sim_day: optional lower bound (inclusive) on sim_day
            until_sim_day: optional upper bound (inclusive) on sim_day
            min_matches: skip cycles with fewer than this many matches (default 1)
            limit: top N (default 10, max 100)

        Returns:
          {
            metric, metric_description, direction,
            n_cycles_evaluated, n_cycles_returned,
            sim_day_window: {since_sim_day, until_sim_day},
            top_cycles: [{cycle_id, sim_day, sim_hour, wall_timestamp,
                          value, n_matches, n_supply_offers, n_demand_requests,
                          total_tons, total_cost_sek, total_co2_kg,
                          total_distance_km, fleet_utilization_pct,
                          solver_status, wall_duration_ms}, ...]
          }

        Use cases:
        - Identify greenest cycles (low co2_per_ton)
        - Identify most cost-efficient cycles (low cost_per_ton)
        - Identify best-utilized cycles (high fleet_utilization)
        - Identify most productive cycles (high tons_per_cycle)
        """
        # iter #61: TTL cache check
        _cache_key = self._cache_key(
            "get_top_cycles_by_efficiency",
            {
                "metric": metric, "since_sim_day": since_sim_day,
                "until_sim_day": until_sim_day, "min_matches": min_matches,
                "limit": limit,
            },
        )
        _cached = self._cache_get(_cache_key)
        if _cached is not None:
            return _cached

        valid_metrics = {
            "co2_per_ton": ("lower_is_better", "kg CO2 per ton delivered"),
            "cost_per_ton": ("lower_is_better", "SEK per ton delivered"),
            "co2_per_km": ("lower_is_better", "kg CO2 per km"),
            "cost_per_km": ("lower_is_better", "SEK per km"),
            "fleet_utilization": ("higher_is_better", "fleet utilization %"),
            "match_rate_vs_offers": ("higher_is_better",
                                     "matches per supply offer"),
            "tons_per_cycle": ("higher_is_better", "tons matched per cycle"),
        }
        if metric not in valid_metrics:
            raise ValueError(
                f"Unknown metric {metric!r}. Valid: {list(valid_metrics)}"
            )

        direction, desc = valid_metrics[metric]

        where_clauses = ["1=1"]
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
                f"""SELECT cycle_id, sim_day, sim_hour, wall_timestamp,
                          n_matches, n_supply_offers, n_demand_requests,
                          total_tons, total_cost_sek, total_co2_kg,
                          total_distance_km, n_vehicles_used,
                          n_vehicles_available, fleet_utilization_pct,
                          solver_status, wall_duration_ms
                FROM optimization_cycles
                WHERE {where_sql} AND n_matches >= ?
                ORDER BY sim_day DESC""",
                params + [max(0, int(min_matches))],
            ).fetchall()

        results = []
        for r in rows:
            total_tons = float(r["total_tons"] or 0)
            total_co2 = float(r["total_co2_kg"] or 0)
            total_cost = float(r["total_cost_sek"] or 0)
            total_dist = float(r["total_distance_km"] or 0)
            n_matches = int(r["n_matches"] or 0)
            n_offers = int(r["n_supply_offers"] or 0)
            util = float(r["fleet_utilization_pct"] or 0)

            if metric == "co2_per_ton":
                value = round(total_co2 / total_tons, 3) if total_tons > 0 else None
            elif metric == "cost_per_ton":
                value = round(total_cost / total_tons, 3) if total_tons > 0 else None
            elif metric == "co2_per_km":
                value = round(total_co2 / total_dist, 3) if total_dist > 0 else None
            elif metric == "cost_per_km":
                value = round(total_cost / total_dist, 3) if total_dist > 0 else None
            elif metric == "fleet_utilization":
                value = round(util, 2)
            elif metric == "match_rate_vs_offers":
                value = round(n_matches / n_offers, 3) if n_offers > 0 else None
            elif metric == "tons_per_cycle":
                value = round(total_tons, 2)
            else:
                value = None

            # Skip cycles where the metric is undefined (e.g., zero tons for
            # ton-based metrics).
            if value is None:
                continue

            results.append({
                "cycle_id": r["cycle_id"],
                "sim_day": int(r["sim_day"]),
                "sim_hour": int(r["sim_hour"]) if r["sim_hour"] is not None else None,
                "wall_timestamp": r["wall_timestamp"],
                "value": value,
                "n_matches": n_matches,
                "n_supply_offers": n_offers,
                "n_demand_requests": int(r["n_demand_requests"] or 0),
                "total_tons": round(total_tons, 2),
                "total_cost_sek": round(total_cost, 2),
                "total_co2_kg": round(total_co2, 3),
                "total_distance_km": round(total_dist, 2),
                "fleet_utilization_pct": round(util, 2),
                "solver_status": r["solver_status"],
                "wall_duration_ms": r["wall_duration_ms"],
            })

        # Sort by value with direction
        if direction == "lower_is_better":
            results.sort(key=lambda x: x["value"])
        else:
            results.sort(key=lambda x: -x["value"])

        # iter #61: store in TTL cache
        result = {
            "metric": metric,
            "metric_description": desc,
            "direction": direction,
            "n_cycles_evaluated": len(results),
            "n_cycles_returned": min(limit, len(results)),
            "sim_day_window": {
                "since_sim_day": since_sim_day,
                "until_sim_day": until_sim_day,
            },
            "top_cycles": results[:max(1, min(100, limit))],
        }
        self._cache_set(_cache_key, result)
        return result

    def get_supply_aggregates(self, supply_id: Optional[str] = None,
                              material_type: Optional[str] = None,
                              limit_supplies: int = 100) -> List[Dict[str, Any]]:
        """
        Supply 聚合统计 (iter #15) — 每个 supply_id 的累计 KPI。

        Args:
            supply_id: 可选, 只查某个 supply_id (否则返回 top supplies)
            material_type: 可选, 按 material_type 过滤
            limit_supplies: 最多返回多少个 supply

        Returns:
            [{supply_id, material_type, n_cycles_with_supply,
              total_available_tons, total_matched_tons, total_quality_avg,
              n_matches, last_seen, first_seen}, ...]
            按 total_available_tons DESC 排序
        """
        with self._conn() as conn:
            where_clauses: List[str] = []
            params: List[Any] = []
            if material_type:
                where_clauses.append("s.material_type = ?")
                params.append(material_type)

            where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

            # 注意: supply_id 在表里是 supply_id, 但需要去重 (一个 supply 多个 cycle)
            if supply_id:
                # 单个 supply 的聚合
                where_clauses.append("s.supply_id = ?")
                params.append(supply_id)
                where_sql = "WHERE " + " AND ".join(where_clauses)
                sql = f"""SELECT s.supply_id, s.material_type,
                                 COUNT(DISTINCT s.cycle_id) as n_cycles,
                                 SUM(s.available_tons) as total_available,
                                 AVG(s.quality_score) as avg_quality,
                                 MAX(s.cycle_id) as last_cycle,
                                 MIN(s.cycle_id) as first_cycle
                          FROM supply_offers s
                          {where_sql}
                          GROUP BY s.supply_id
                          ORDER BY total_available DESC
                          LIMIT 1"""
                rows = conn.execute(sql, params).fetchall()
            else:
                # top supplies (按 total_available 排序)
                sql = f"""SELECT s.supply_id, s.material_type,
                                 COUNT(DISTINCT s.cycle_id) as n_cycles,
                                 SUM(s.available_tons) as total_available,
                                 AVG(s.quality_score) as avg_quality,
                                 MAX(s.cycle_id) as last_cycle,
                                 MIN(s.cycle_id) as first_cycle
                          FROM supply_offers s
                          {where_sql}
                          GROUP BY s.supply_id
                          ORDER BY total_available DESC
                          LIMIT ?"""
                params.append(limit_supplies)
                rows = conn.execute(sql, params).fetchall()

            # join matches (per supply_id) - 在 with 块内 避免 conn closed
            result: List[Dict[str, Any]] = []
            for r in rows:
                sid = r["supply_id"]
                match_row = conn.execute(
                    """SELECT COUNT(*) as n_matches, SUM(tons) as total_matched
                       FROM matches WHERE supply_id = ?""",
                    (sid,)
                ).fetchone()
                d = dict(r)
                d["n_matches"] = match_row["n_matches"] or 0
                d["total_matched_tons"] = round(match_row["total_matched"] or 0, 2)
                d["total_available_tons"] = round(d.get("total_available") or 0, 2)
                d["avg_quality_score"] = round(d.pop("avg_quality") or 0, 1)
                d["n_cycles_with_supply"] = d.pop("n_cycles", 0)
                d["last_cycle_id"] = d.pop("last_cycle")
                d["first_cycle_id"] = d.pop("first_cycle")
                d["material_type"] = d["material_type"]
                result.append(d)
        return result

    def get_demand_aggregates(self, demand_id: Optional[str] = None,
                              material_type: Optional[str] = None,
                              limit_demands: int = 100) -> List[Dict[str, Any]]:
        """
        Demand 聚合统计 (iter #52) — 每个 demand_id 的累计 KPI。

        Mirror of get_supply_aggregates but on demand side. Useful for:
        - Top demands by required_tons (which demand sites receive most supply?)
        - Demand fulfillment rate (how often is required_tons fully met?)
        - Per-material demand patterns

        Args:
            demand_id: 可选, 只查某个 demand_id (否则返回 top demands)
            material_type: 可选, 按 material_type 过滤
            limit_demands: 最多返回多少个 demand (default 100)

        Returns:
            [{demand_id, material_type, n_cycles_with_demand,
              total_required_tons, total_matched_tons, fulfillment_rate,
              avg_required_tons, n_matches, avg_match_tons,
              last_cycle_id, first_cycle_id, last_sim_day, first_sim_day}, ...]
            按 total_required_tons DESC 排序

        fulfillment_rate: matched / required (0.0 = 完全未满足, 1.0 = 完美)
        """
        with self._conn() as conn:
            where_clauses: List[str] = []
            params: List[Any] = []
            if material_type:
                where_clauses.append("d.material_type = ?")
                params.append(material_type)

            if demand_id:
                where_clauses.append("d.demand_id = ?")
                params.append(demand_id)
                where_sql = "WHERE " + " AND ".join(where_clauses)
                sql = f"""SELECT d.demand_id, d.material_type,
                                 COUNT(DISTINCT d.cycle_id) as n_cycles,
                                 SUM(d.required_tons) as total_required,
                                 MAX(d.cycle_id) as last_cycle,
                                 MIN(d.cycle_id) as first_cycle,
                                 MAX(c.sim_day) as last_sim_day,
                                 MIN(c.sim_day) as first_sim_day
                          FROM demand_requests d
                          JOIN optimization_cycles c ON c.cycle_id = d.cycle_id
                          {where_sql}
                          GROUP BY d.demand_id
                          ORDER BY total_required DESC
                          LIMIT 1"""
                rows = conn.execute(sql, params).fetchall()
            else:
                where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
                sql = f"""SELECT d.demand_id, d.material_type,
                                 COUNT(DISTINCT d.cycle_id) as n_cycles,
                                 SUM(d.required_tons) as total_required,
                                 MAX(d.cycle_id) as last_cycle,
                                 MIN(d.cycle_id) as first_cycle,
                                 MAX(c.sim_day) as last_sim_day,
                                 MIN(c.sim_day) as first_sim_day
                          FROM demand_requests d
                          JOIN optimization_cycles c ON c.cycle_id = d.cycle_id
                          {where_sql}
                          GROUP BY d.demand_id
                          ORDER BY total_required DESC
                          LIMIT ?"""
                params.append(limit_demands)
                rows = conn.execute(sql, params).fetchall()

            result: List[Dict[str, Any]] = []
            for r in rows:
                did = r["demand_id"]
                # 拿该 demand 的 match stats
                match_row = conn.execute(
                    """SELECT COUNT(*) as n_matches, SUM(tons) as total_matched,
                              AVG(tons) as avg_match_tons
                       FROM matches WHERE demand_id = ?""",
                    (did,)
                ).fetchone()
                total_required = r["total_required"] or 0
                total_matched = match_row["total_matched"] or 0
                # fulfillment_rate: 截断到 [0.0, 2.0] (允许 >1 表示超额供应, 罕见)
                if total_required > 0:
                    fulfillment = round(min(2.0, total_matched / total_required), 3)
                else:
                    fulfillment = 0.0

                d = dict(r)
                d["n_matches"] = match_row["n_matches"] or 0
                d["total_matched_tons"] = round(total_matched, 2)
                d["total_required_tons"] = round(total_required, 2)
                d["avg_required_tons"] = round(total_required / max(1, r["n_cycles"]), 2)
                d["avg_match_tons"] = round(match_row["avg_match_tons"] or 0, 2)
                d["fulfillment_rate"] = fulfillment
                d["n_cycles_with_demand"] = d.pop("n_cycles")
                d["last_cycle_id"] = d.pop("last_cycle")
                d["first_cycle_id"] = d.pop("first_cycle")
                result.append(d)
        return result

    def get_top_demands_by_fulfillment(
        self,
        metric: str = "fulfillment_rate",
        material_type: Optional[str] = None,
        min_required_tons: float = 0.0,
        limit: int = 10,
    ) -> Dict[str, Any]:
        """
        iter #57: Top demands ranked by fulfillment metrics.

        Args:
            metric: which fulfillment metric to rank by. One of:
                - 'fulfillment_rate' (matched / required, higher = better, capped at 2.0)
                - 'total_matched_tons' (sum of all matched tons, higher = better)
                - 'unmet_demand_tons' (required - matched, lower = better — gap analysis)
                - 'match_rate' (n_matches / n_cycles_with_demand, higher = better)
                - 'avg_match_distance_km' (avg km per match, lower = better)
            material_type: optional filter
            min_required_tons: skip demands with less than this required tonnage
                (default 0.0 — include all)
            limit: top N (default 10, max 100)

        Returns:
          {
            metric, metric_description, direction,
            n_demands_evaluated, n_demands_returned,
            top_demands: [{demand_id, material_type, value,
                           n_matches, n_cycles_with_demand,
                           total_required_tons, total_matched_tons,
                           fulfillment_rate, avg_match_distance_km,
                           avg_match_tons}, ...]
          }

        Use cases:
        - Identify best-served demands (high fulfillment_rate)
        - Identify biggest unmet gaps (high unmet_demand_tons)
        - Identify most-matched demands (high total_matched_tons)
        - Identify hardest-to-reach (high avg_match_distance_km)
        """
        # iter #61: TTL cache check
        _cache_key = self._cache_key(
            "get_top_demands_by_fulfillment",
            {
                "metric": metric, "material_type": material_type,
                "min_required_tons": min_required_tons, "limit": limit,
            },
        )
        _cached = self._cache_get(_cache_key)
        if _cached is not None:
            return _cached

        valid_metrics = {
            "fulfillment_rate": ("higher_is_better",
                                 "fulfillment rate (matched/required)"),
            "total_matched_tons": ("higher_is_better",
                                   "total matched tons"),
            "unmet_demand_tons": ("lower_is_better",
                                  "unmet tons (required - matched)"),
            "match_rate": ("higher_is_better",
                           "matches per cycle"),
            "avg_match_distance_km": ("lower_is_better",
                                      "avg km per match"),
        }
        if metric not in valid_metrics:
            raise ValueError(
                f"Unknown metric {metric!r}. Valid: {list(valid_metrics)}"
            )

        direction, desc = valid_metrics[metric]

        where_clauses = ["1=1"]
        params: List[Any] = []
        if material_type:
            where_clauses.append("d.material_type = ?")
            params.append(material_type)
        where_sql = " AND ".join(where_clauses)

        # HAVING clause must reference aggregates; min_required_tons can't go in WHERE
        having_clauses = ["SUM(d.required_tons) > 0"]
        if min_required_tons > 0:
            having_clauses.append("SUM(d.required_tons) >= ?")
            params.append(float(min_required_tons))
        having_sql = " AND ".join(having_clauses)

        # Aggregate per demand_id
        with self._conn() as conn:
            rows = conn.execute(
                f"""SELECT d.demand_id, d.material_type,
                          COUNT(DISTINCT d.cycle_id) as n_cycles,
                          SUM(d.required_tons) as total_required
                FROM demand_requests d
                WHERE {where_sql}
                GROUP BY d.demand_id
                HAVING {having_sql}""",
                params,
            ).fetchall()

            results = []
            for r in rows:
                did = r["demand_id"]
                match_row = conn.execute(
                    """SELECT COUNT(*) as n_matches,
                              COALESCE(SUM(tons), 0) as total_matched,
                              COALESCE(AVG(distance_km), 0) as avg_distance,
                              COALESCE(AVG(tons), 0) as avg_match_tons
                    FROM matches WHERE demand_id = ?""",
                    (did,)
                ).fetchone()

                n_matches = match_row["n_matches"] or 0
                total_matched = float(match_row["total_matched"] or 0)
                total_required = float(r["total_required"] or 0)
                avg_distance = float(match_row["avg_distance"] or 0)
                n_cycles = int(r["n_cycles"] or 0)

                # Compute metric value
                if metric == "fulfillment_rate":
                    value = round(
                        min(2.0, total_matched / total_required), 3
                    ) if total_required > 0 else 0.0
                elif metric == "total_matched_tons":
                    value = round(total_matched, 2)
                elif metric == "unmet_demand_tons":
                    value = round(max(0.0, total_required - total_matched), 2)
                elif metric == "match_rate":
                    value = round(
                        n_matches / n_cycles, 3
                    ) if n_cycles > 0 else 0.0
                elif metric == "avg_match_distance_km":
                    value = round(avg_distance, 2) if n_matches > 0 else None
                else:
                    value = None

                # Skip demands where metric is undefined (e.g., no matches for distance)
                if value is None:
                    continue

                results.append({
                    "demand_id": did,
                    "material_type": r["material_type"],
                    "value": value,
                    "n_matches": n_matches,
                    "n_cycles_with_demand": n_cycles,
                    "total_required_tons": round(total_required, 2),
                    "total_matched_tons": round(total_matched, 2),
                    "fulfillment_rate": round(
                        min(2.0, total_matched / total_required), 3
                    ) if total_required > 0 else 0.0,
                    "avg_match_distance_km": round(avg_distance, 2),
                    "avg_match_tons": round(
                        match_row["avg_match_tons"] or 0, 2
                    ),
                })

        # Sort
        if direction == "lower_is_better":
            results.sort(key=lambda x: x["value"])
        else:
            results.sort(key=lambda x: -x["value"])

        # iter #61: store in TTL cache
        result = {
            "metric": metric,
            "metric_description": desc,
            "direction": direction,
            "n_demands_evaluated": len(results),
            "n_demands_returned": min(limit, len(results)),
            "top_demands": results[:max(1, min(100, limit))],
        }
        self._cache_set(_cache_key, result)
        return result

    def get_top_routes_by_efficiency(
        self,
        metric: str = "co2_per_km",
        vehicle_id: Optional[str] = None,
        since_sim_day: Optional[int] = None,
        until_sim_day: Optional[int] = None,
        limit: int = 10,
    ) -> Dict[str, Any]:
        """
        iter #63: Top routes (per cycle × per vehicle) ranked by efficiency metrics.

        The routes table holds one row per cycle per vehicle (stops_json +
        distance_km + cost_sek + co2_kg + duration_hours). This method lets
        the user surface which individual routes were the most efficient on a
        given day for a given vehicle.

        Args:
            metric: which efficiency metric to rank by. One of:
                - 'co2_per_km' (kg CO2 / km, lower = better — greenest)
                - 'co2_per_hour' (kg CO2 / hour, lower = better — cleanest per driving hour)
                - 'cost_per_km' (SEK / km, lower = better — cheapest per km)
                - 'cost_per_hour' (SEK / hour, lower = better — cheapest per driving hour)
                - 'speed_km_per_hour' (km / hour, higher = better — fastest)
                - 'distance' (km, higher = better — longest routes)
                - 'tons_per_km' (proxy: cycle_tons / distance, higher = better — most loaded)
            vehicle_id: optional filter (only include routes from this vehicle)
            since_sim_day: optional lower bound (inclusive)
            until_sim_day: optional upper bound (inclusive)
            limit: top N (default 10, max 100)

        Returns:
          {
            metric, metric_description, direction,
            filter: {vehicle_id, since_sim_day, until_sim_day},
            n_routes_evaluated, n_routes_returned,
            top_routes: [{route_id, cycle_id, vehicle_id, sim_day, sim_hour,
                          duration_hours, distance_km, cost_sek, co2_kg,
                          n_stops, cycle_tons, value}, ...]
          }

        Use cases:
        - Identify greenest cycle × vehicle combinations (low co2_per_km)
        - Identify cheapest routes (low cost_per_km)
        - Identify fastest routes (high speed_km_per_hour)
        - Identify most-loaded routes (high tons_per_km)
        - Drill down on a specific vehicle (vehicle_id filter)
        """
        _cache_key = self._cache_key(
            "get_top_routes_by_efficiency",
            {"metric": metric, "vehicle_id": vehicle_id,
             "since_sim_day": since_sim_day, "until_sim_day": until_sim_day,
             "limit": limit},
        )
        _cached = self._cache_get(_cache_key)
        if _cached is not None:
            return _cached

        valid_metrics = {
            "co2_per_km": ("lower_is_better", "kg CO2 per km"),
            "co2_per_hour": ("lower_is_better", "kg CO2 per hour"),
            "cost_per_km": ("lower_is_better", "SEK per km"),
            "cost_per_hour": ("lower_is_better", "SEK per hour"),
            "speed_km_per_hour": ("higher_is_better", "km per hour"),
            "distance": ("higher_is_better", "route distance km"),
            "tons_per_km": ("higher_is_better", "tons per km (proxy)"),
        }
        if metric not in valid_metrics:
            raise ValueError(
                f"Unknown metric {metric!r}. Valid: {list(valid_metrics)}"
            )

        direction, desc = valid_metrics[metric]

        where_clauses = ["1=1"]
        params: List[Any] = []
        if vehicle_id:
            where_clauses.append("r.vehicle_id = ?")
            params.append(vehicle_id)
        if since_sim_day is not None:
            where_clauses.append("c.sim_day >= ?")
            params.append(int(since_sim_day))
        if until_sim_day is not None:
            where_clauses.append("c.sim_day <= ?")
            params.append(int(until_sim_day))
        where_sql = " AND ".join(where_clauses)

        with self._conn() as conn:
            rows = conn.execute(
                f"""SELECT r.id as route_id, r.cycle_id, r.vehicle_id,
                          r.distance_km, r.duration_hours, r.cost_sek, r.co2_kg,
                          r.stops_json,
                          c.sim_day, c.sim_hour,
                          (SELECT COALESCE(SUM(m.tons), 0)
                           FROM matches m WHERE m.cycle_id = r.cycle_id) as cycle_tons
                FROM routes r
                JOIN optimization_cycles c ON c.cycle_id = r.cycle_id
                WHERE {where_sql} AND r.vehicle_id IS NOT NULL
                  AND r.distance_km > 0
                ORDER BY r.id DESC""",
                params,
            ).fetchall()

            results = []
            for r in rows:
                distance = float(r["distance_km"] or 0)
                duration = float(r["duration_hours"] or 0)
                cost = float(r["cost_sek"] or 0)
                co2 = float(r["co2_kg"] or 0)
                tons = float(r["cycle_tons"] or 0)
                # Compute metric value
                if metric == "co2_per_km":
                    v = co2 / distance if distance > 0 else None
                elif metric == "co2_per_hour":
                    v = co2 / duration if duration > 0 else None
                elif metric == "cost_per_km":
                    v = cost / distance if distance > 0 else None
                elif metric == "cost_per_hour":
                    v = cost / duration if duration > 0 else None
                elif metric == "speed_km_per_hour":
                    v = distance / duration if duration > 0 else None
                elif metric == "distance":
                    v = distance
                elif metric == "tons_per_km":
                    v = tons / distance if distance > 0 else None
                else:
                    v = None
                if v is None:
                    continue
                if metric == "distance":
                    value = round(v, 2)
                elif metric == "speed_km_per_hour":
                    value = round(v, 2)
                else:
                    value = round(v, 4)

                # Count non-empty stops (stops_json is like ["SUP_A", "DEM_1", ...])
                stops_json = r["stops_json"] or "[]"
                try:
                    stops_list = json.loads(stops_json) if isinstance(stops_json, str) else stops_json
                    n_stops = sum(1 for s in stops_list if s)
                except Exception:
                    n_stops = 0

                results.append({
                    "route_id": r["route_id"],
                    "cycle_id": r["cycle_id"],
                    "vehicle_id": r["vehicle_id"],
                    "sim_day": r["sim_day"],
                    "sim_hour": r["sim_hour"],
                    "distance_km": round(distance, 2),
                    "duration_hours": round(duration, 3),
                    "cost_sek": round(cost, 2),
                    "co2_kg": round(co2, 2),
                    "n_stops": n_stops,
                    "cycle_tons": round(tons, 2),
                    "value": value,
                })

        # Sort by metric direction
        if direction == "lower_is_better":
            results.sort(key=lambda x: x["value"])
        else:
            results.sort(key=lambda x: x["value"], reverse=True)

        result = {
            "metric": metric,
            "metric_description": desc,
            "direction": direction,
            "filter": {
                "vehicle_id": vehicle_id,
                "since_sim_day": since_sim_day,
                "until_sim_day": until_sim_day,
            },
            "n_routes_evaluated": len(results),
            "n_routes_returned": min(limit, len(results)),
            "top_routes": results[:max(1, min(100, limit))],
        }
        self._cache_set(_cache_key, result)
        return result

    def get_top_materials_by_volume(
        self,
        metric: str = "total_matched_tons",
        since_sim_day: Optional[int] = None,
        until_sim_day: Optional[int] = None,
        limit: int = 10,
    ) -> Dict[str, Any]:
        """
        iter #63: Top materials ranked by volume / efficiency metrics.

        Aggregates per material_type from supply_offers, demand_requests,
        matches and routes. Useful for: which materials dominate the system,
        which have the best match rate, which are cleanest per ton, etc.

        Args:
            metric: which volume/efficiency metric to rank by. One of:
                - 'total_matched_tons' (matched tons, higher = better)
                - 'total_supply_tons' (sum of available_tons, higher = better)
                - 'total_demand_tons' (sum of required_tons, higher = better)
                - 'n_matches' (number of matches, higher = better)
                - 'n_supply_offers' (number of offers, higher = better)
                - 'n_demand_requests' (number of demands, higher = better)
                - 'match_rate' (matches / offers, higher = better)
                - 'avg_tons_per_match' (avg matched tons, higher = better)
                - 'co2_per_ton' (kg CO2 / matched ton, lower = better)
                - 'cost_per_ton' (SEK / matched ton, lower = better)
                - 'avg_distance_km' (avg km per match, lower = better)
            since_sim_day: optional lower bound (inclusive)
            until_sim_day: optional upper bound (inclusive)
            limit: top N (default 10, max 50 — material list is small)

        Returns:
          {
            metric, metric_description, direction,
            filter: {since_sim_day, until_sim_day},
            n_materials_evaluated, n_materials_returned,
            top_materials: [{
              material_type,
              value,
              total_matched_tons, total_supply_tons, total_demand_tons,
              total_co2_kg, total_cost_sek,
              n_matches, n_supply_offers, n_demand_requests,
              match_rate, avg_tons_per_match,
              avg_distance_km,
              demand_fulfillment_pct,
            }, ...]
          }

        Use cases:
        - Identify dominant materials (high total_matched_tons)
        - Identify bottleneck materials (low match_rate)
        - Identify cleanest materials (low co2_per_ton)
        - Identify most-requested materials (high total_demand_tons)
        """
        _cache_key = self._cache_key(
            "get_top_materials_by_volume",
            {"metric": metric,
             "since_sim_day": since_sim_day, "until_sim_day": until_sim_day,
             "limit": limit},
        )
        _cached = self._cache_get(_cache_key)
        if _cached is not None:
            return _cached

        valid_metrics = {
            "total_matched_tons": ("higher_is_better", "total matched tons"),
            "total_supply_tons": ("higher_is_better", "total available tons"),
            "total_demand_tons": ("higher_is_better", "total requested tons"),
            "n_matches": ("higher_is_better", "number of matches"),
            "n_supply_offers": ("higher_is_better", "number of offers"),
            "n_demand_requests": ("higher_is_better", "number of demands"),
            "match_rate": ("higher_is_better", "matches per offer"),
            "avg_tons_per_match": ("higher_is_better", "avg tons per match"),
            "co2_per_ton": ("lower_is_better", "kg CO2 per matched ton"),
            "cost_per_ton": ("lower_is_better", "SEK per matched ton"),
            "avg_distance_km": ("lower_is_better", "avg km per match"),
        }
        if metric not in valid_metrics:
            raise ValueError(
                f"Unknown metric {metric!r}. Valid: {list(valid_metrics)}"
            )

        direction, desc = valid_metrics[metric]

        where_clauses = ["1=1"]
        params: List[Any] = []
        if since_sim_day is not None:
            where_clauses.append("c.sim_day >= ?")
            params.append(int(since_sim_day))
        if until_sim_day is not None:
            where_clauses.append("c.sim_day <= ?")
            params.append(int(until_sim_day))
        where_sql = " AND ".join(where_clauses)

        with self._conn() as conn:
            # Per-material aggregates from supply_offers (matched = sum of available)
            supply_rows = conn.execute(
                f"""SELECT s.material_type,
                          COUNT(DISTINCT s.supply_id) as n_offers,
                          SUM(s.available_tons) as total_supply,
                          COUNT(DISTINCT s.cycle_id) as cycles_with_supply
                FROM supply_offers s
                JOIN optimization_cycles c ON c.cycle_id = s.cycle_id
                WHERE {where_sql}
                GROUP BY s.material_type""",
                params,
            ).fetchall()

            demand_rows = conn.execute(
                f"""SELECT d.material_type,
                          COUNT(DISTINCT d.demand_id) as n_demands,
                          SUM(d.required_tons) as total_demand,
                          COUNT(DISTINCT d.cycle_id) as cycles_with_demand
                FROM demand_requests d
                JOIN optimization_cycles c ON c.cycle_id = d.cycle_id
                WHERE {where_sql}
                GROUP BY d.material_type""",
                params,
            ).fetchall()

            match_rows = conn.execute(
                f"""SELECT m.material_type,
                          COUNT(*) as n_matches,
                          SUM(m.tons) as total_matched,
                          AVG(m.tons) as avg_tons,
                          AVG(m.distance_km) as avg_distance,
                          SUM(m.distance_km) as total_distance,
                          SUM(m.estimated_profit_sek) as total_profit
                FROM matches m
                JOIN optimization_cycles c ON c.cycle_id = m.cycle_id
                WHERE {where_sql}
                GROUP BY m.material_type""",
                params,
            ).fetchall()

            route_rows = conn.execute(
                f"""SELECT r.cycle_id,
                          SUM(r.co2_kg) as cycle_co2,
                          SUM(r.cost_sek) as cycle_cost
                FROM routes r
                JOIN optimization_cycles c ON c.cycle_id = r.cycle_id
                WHERE {where_sql}
                GROUP BY r.cycle_id""",
                params,
            ).fetchall()

            # Sum route co2/cost per cycle — but we need per-material.
            # Without a direct material → route link, we approximate by
            # allocating cycle totals in proportion to match.tons.
            # Build {cycle_id: {co2, cost}} for allocation
            route_by_cycle = {r["cycle_id"]: (float(r["cycle_co2"] or 0),
                                              float(r["cycle_cost"] or 0))
                              for r in route_rows}
            # Build per-material dict
        mat_data: Dict[str, Dict[str, Any]] = {}
        for r in supply_rows:
            mat = r["material_type"]
            if mat is None:
                continue
            mat_data.setdefault(mat, {})
            mat_data[mat]["n_supply_offers"] = int(r["n_offers"] or 0)
            mat_data[mat]["total_supply_tons"] = float(r["total_supply"] or 0)
        for r in demand_rows:
            mat = r["material_type"]
            if mat is None:
                continue
            mat_data.setdefault(mat, {})
            mat_data[mat]["n_demand_requests"] = int(r["n_demands"] or 0)
            mat_data[mat]["total_demand_tons"] = float(r["total_demand"] or 0)
        for r in match_rows:
            mat = r["material_type"]
            if mat is None:
                continue
            mat_data.setdefault(mat, {})
            mat_data[mat]["n_matches"] = int(r["n_matches"] or 0)
            mat_data[mat]["total_matched_tons"] = float(r["total_matched"] or 0)
            mat_data[mat]["avg_tons_per_match"] = float(r["avg_tons"] or 0)
            mat_data[mat]["avg_distance_km"] = float(r["avg_distance"] or 0)
            mat_data[mat]["total_profit_sek"] = float(r["total_profit"] or 0)
            mat_data[mat]["total_distance_km"] = float(r["total_distance"] or 0)
            mat_data[mat]["total_co2_kg"] = 0.0
            mat_data[mat]["total_cost_sek"] = 0.0
            mat_data[mat]["_matched_cycles"] = []

        # We need to allocate per-cycle route co2/cost per material.
        # Re-query matches per cycle for allocation.
        with self._conn() as conn:
            matched_cycles_rows = conn.execute(
                f"""SELECT m.cycle_id, m.material_type, SUM(m.tons) as cycle_mat_tons
                FROM matches m
                JOIN optimization_cycles c ON c.cycle_id = m.cycle_id
                WHERE {where_sql}
                GROUP BY m.cycle_id, m.material_type""",
                params,
            ).fetchall()
            cycle_totals_rows = conn.execute(
                f"""SELECT m.cycle_id, SUM(m.tons) as cycle_total_tons
                FROM matches m
                JOIN optimization_cycles c ON c.cycle_id = m.cycle_id
                WHERE {where_sql}
                GROUP BY m.cycle_id""",
                params,
            ).fetchall()
        cycle_total_dict = {r["cycle_id"]: float(r["cycle_total_tons"] or 0)
                            for r in cycle_totals_rows}
        for r in matched_cycles_rows:
            cid = r["cycle_id"]
            mat = r["material_type"]
            ct = float(r["cycle_mat_tons"] or 0)
            tot = cycle_total_dict.get(cid, 0)
            if mat in mat_data and tot > 0:
                frac = ct / tot
                co2_total, cost_total = route_by_cycle.get(cid, (0.0, 0.0))
                mat_data[mat]["total_co2_kg"] += co2_total * frac
                mat_data[mat]["total_cost_sek"] += cost_total * frac

        results = []
        for mat, d in mat_data.items():
            matched = d.get("total_matched_tons", 0.0)
            offers = d.get("n_supply_offers", 0)
            demands = d.get("n_demand_requests", 0)
            matches = d.get("n_matches", 0)
            supply = d.get("total_supply_tons", 0.0)
            demand = d.get("total_demand_tons", 0.0)
            co2 = d.get("total_co2_kg", 0.0)
            cost = d.get("total_cost_sek", 0.0)
            avg_dist = d.get("avg_distance_km", 0.0)
            avg_tons = d.get("avg_tons_per_match", 0.0)
            demand_fulfill = matched / demand if demand > 0 else 0.0
            # Skip materials with no matches AND no offers (effectively
            # unused in this window). Avoids cluttering results with phantom
            # materials that only appear in demand_requests.
            if matches == 0 and offers == 0:
                continue

            if metric == "total_matched_tons":
                v = matched
            elif metric == "total_supply_tons":
                v = supply
            elif metric == "total_demand_tons":
                v = demand
            elif metric == "n_matches":
                v = matches
            elif metric == "n_supply_offers":
                v = offers
            elif metric == "n_demand_requests":
                v = demands
            elif metric == "match_rate":
                v = matches / offers if offers > 0 else None
            elif metric == "avg_tons_per_match":
                v = avg_tons if matches > 0 else None
            elif metric == "co2_per_ton":
                v = co2 / matched if matched > 0 else None
            elif metric == "cost_per_ton":
                v = cost / matched if matched > 0 else None
            elif metric == "avg_distance_km":
                v = avg_dist if matches > 0 else None
            else:
                v = None
            if v is None:
                continue
            # Round appropriately
            if metric in ("n_matches", "n_supply_offers", "n_demand_requests"):
                value = int(v)
            elif metric in ("match_rate",):
                value = round(v, 4)
            elif metric in ("total_matched_tons", "total_supply_tons",
                             "total_demand_tons", "avg_tons_per_match",
                             "avg_distance_km"):
                value = round(v, 3)
            else:  # co2_per_ton, cost_per_ton
                value = round(v, 4)

            results.append({
                "material_type": mat,
                "value": value,
                "total_matched_tons": round(matched, 2),
                "total_supply_tons": round(supply, 2),
                "total_demand_tons": round(demand, 2),
                "total_co2_kg": round(co2, 2),
                "total_cost_sek": round(cost, 2),
                "n_matches": int(matches),
                "n_supply_offers": int(offers),
                "n_demand_requests": int(demands),
                "match_rate": round(matches / offers, 3) if offers > 0 else None,
                "avg_tons_per_match": round(avg_tons, 3),
                "avg_distance_km": round(avg_dist, 2),
                "demand_fulfillment_pct": round(100 * demand_fulfill, 2),
            })

        # Sort by metric direction
        if direction == "lower_is_better":
            results.sort(key=lambda x: x["value"])
        else:
            results.sort(key=lambda x: x["value"], reverse=True)

        result = {
            "metric": metric,
            "metric_description": desc,
            "direction": direction,
            "filter": {
                "since_sim_day": since_sim_day,
                "until_sim_day": until_sim_day,
            },
            "n_materials_evaluated": len(results),
            "n_materials_returned": min(limit, len(results)),
            "top_materials": results[:max(1, min(50, limit))],
        }
        self._cache_set(_cache_key, result)
        return result

    def get_material_timeseries(
        self,
        material_type: Optional[str] = None,
        metric: str = "matched_tons",
        since_sim_day: Optional[int] = None,
        until_sim_day: Optional[int] = None,
        limit: int = 200,
    ) -> Dict[str, Any]:
        """
        iter #65: Per-material time-series of volume / efficiency metrics.

        Returns one row per cycle × per material with the chosen metric,
        plus a per-material summary (mean/min/max/latest + trend). Useful
        for plotting how each material's per-cycle volume or efficiency
        evolves over time.

        Args:
            material_type: optional filter (e.g. 'concrete')
            metric: which metric to rank by. One of:
                - 'matched_tons' (matched tons per cycle, higher = better)
                - 'n_matches' (number of matches, higher = better)
                - 'avg_match_tons' (avg tons per match, higher = better)
                - 'co2_per_ton' (kg CO2 / matched ton, lower = better)
                - 'cost_per_ton' (SEK / matched ton, lower = better)
                - 'match_rate' (matches / offers, higher = better)
            since_sim_day: optional lower bound (inclusive)
            until_sim_day: optional upper bound (inclusive)
            limit: max rows to return (default 200)

        Returns:
          {
            metric, metric_description, direction,
            filter: {material_type, since_sim_day, until_sim_day},
            n_rows_evaluated, n_rows_returned,
            timeseries: [{cycle_id, sim_day, material_type, metric_value,
                          n_matches, matched_tons, n_offers, n_demands}, ...],
            per_material_summary: {
              material_type: {n_cycles, mean_value, min_value, max_value,
                              latest_value, trend},
              ...
            },
          }

        Use cases:
        - Plot each material's matched_tons per cycle
        - Identify materials trending greener (co2_per_ton dropping)
        - Spot materials losing/gaining demand share over time
        """
        _cache_key = self._cache_key(
            "get_material_timeseries",
            {"material_type": material_type, "metric": metric,
             "since_sim_day": since_sim_day, "until_sim_day": until_sim_day,
             "limit": limit},
        )
        _cached = self._cache_get(_cache_key)
        if _cached is not None:
            return _cached

        valid_metrics = {
            "matched_tons": ("higher_is_better", "matched tons / cycle"),
            "n_matches": ("higher_is_better", "matches / cycle"),
            "avg_match_tons": ("higher_is_better", "avg tons per match"),
            "co2_per_ton": ("lower_is_better", "kg CO2 per matched ton"),
            "cost_per_ton": ("lower_is_better", "SEK per matched ton"),
            "match_rate": ("higher_is_better", "matches per offer"),
        }
        if metric not in valid_metrics:
            raise ValueError(
                f"Unknown metric {metric!r}. Valid: {list(valid_metrics)}"
            )
        direction, desc = valid_metrics[metric]

        where_clauses = ["m.material_type IS NOT NULL"]
        params: List[Any] = []
        if material_type:
            where_clauses.append("m.material_type = ?")
            params.append(material_type)
        if since_sim_day is not None:
            where_clauses.append("c.sim_day >= ?")
            params.append(int(since_sim_day))
        if until_sim_day is not None:
            where_clauses.append("c.sim_day <= ?")
            params.append(int(until_sim_day))
        where_sql = " AND ".join(where_clauses)

        # Per-cycle × per-material aggregate from matches + supply + demand
        # Route co2/cost allocated by match.tons proportion
        with self._conn() as conn:
            base_rows = conn.execute(
                f"""SELECT m.cycle_id, m.material_type, c.sim_day, c.sim_hour,
                          COUNT(*) as n_matches,
                          SUM(m.tons) as matched_tons,
                          AVG(m.tons) as avg_match_tons,
                          (SELECT COUNT(*) FROM supply_offers s
                           WHERE s.cycle_id = m.cycle_id AND s.material_type = m.material_type) as n_offers,
                          (SELECT COUNT(*) FROM demand_requests d
                           WHERE d.cycle_id = m.cycle_id AND d.material_type = m.material_type) as n_demands,
                          oc.total_co2_kg as cycle_co2,
                          oc.total_cost_sek as cycle_cost,
                          (SELECT COALESCE(SUM(m2.tons), 0)
                           FROM matches m2 WHERE m2.cycle_id = m.cycle_id) as cycle_total_tons
                FROM matches m
                JOIN optimization_cycles c ON c.cycle_id = m.cycle_id
                LEFT JOIN optimization_cycles oc ON oc.cycle_id = m.cycle_id
                WHERE {where_sql}
                GROUP BY m.cycle_id, m.material_type
                ORDER BY c.sim_day ASC, c.sim_hour ASC, m.cycle_id ASC""",
                params,
            ).fetchall()

        results = []
        for r in base_rows:
            n_matches = int(r["n_matches"] or 0)
            matched_tons = float(r["matched_tons"] or 0)
            avg_match_tons = float(r["avg_match_tons"] or 0)
            n_offers = int(r["n_offers"] or 0)
            n_demands = int(r["n_demands"] or 0)
            cycle_co2 = float(r["cycle_co2"] or 0)
            cycle_cost = float(r["cycle_cost"] or 0)
            cycle_total_tons = float(r["cycle_total_tons"] or 0)

            # Allocate route co2/cost by match.tons proportion
            if cycle_total_tons > 0 and matched_tons > 0:
                mat_co2 = cycle_co2 * matched_tons / cycle_total_tons
                mat_cost = cycle_cost * matched_tons / cycle_total_tons
            else:
                mat_co2 = 0.0
                mat_cost = 0.0

            if metric == "matched_tons":
                v: Union[int, float] = matched_tons
            elif metric == "n_matches":
                v = n_matches
            elif metric == "avg_match_tons":
                v = avg_match_tons if n_matches > 0 else 0.0
            elif metric == "co2_per_ton":
                v = mat_co2 / matched_tons if matched_tons > 0 else 0.0
            elif metric == "cost_per_ton":
                v = mat_cost / matched_tons if matched_tons > 0 else 0.0
            elif metric == "match_rate":
                v = n_matches / n_offers if n_offers > 0 else 0.0
            else:
                v = None
            if v is None:
                continue
            if metric == "n_matches":
                value: Union[int, float] = int(v)
            elif metric == "match_rate":
                value = round(v, 4)
            elif metric == "co2_per_ton" or metric == "cost_per_ton":
                value = round(v, 4)
            else:
                value = round(v, 2)

            results.append({
                "cycle_id": r["cycle_id"],
                "sim_day": r["sim_day"],
                "sim_hour": r["sim_hour"],
                "material_type": r["material_type"],
                "metric_value": value,
                "n_matches": n_matches,
                "matched_tons": round(matched_tons, 2),
                "n_offers": n_offers,
                "n_demands": n_demands,
                "n_stops": n_matches,
            })

        # Build per-material summary
        per_material: Dict[str, List[Dict[str, Any]]] = {}
        for row in results:
            per_material.setdefault(row["material_type"], []).append(row)

        per_material_summary: Dict[str, Dict[str, Any]] = {}
        for mat, hist in per_material.items():
            values_hist = [r["metric_value"] for r in hist
                           if r["metric_value"] is not None]
            if not values_hist:
                continue
            mean_v = sum(values_hist) / len(values_hist)
            min_v = min(values_hist)
            max_v = max(values_hist)
            latest_v = values_hist[-1]
            half = len(values_hist) // 2
            if half >= 1 and len(values_hist) >= 2:
                first_half_mean = sum(values_hist[:half]) / half
                second_half_mean = sum(values_hist[half:]) / (len(values_hist) - half)
                delta = second_half_mean - first_half_mean
                base = abs(first_half_mean) if first_half_mean != 0 else 1
                pct = 100 * delta / base
                if direction == "lower_is_better":
                    if pct < -10:
                        trend = "improving"
                    elif pct > 10:
                        trend = "declining"
                    else:
                        trend = "stable"
                else:
                    if pct > 10:
                        trend = "improving"
                    elif pct < -10:
                        trend = "declining"
                    else:
                        trend = "stable"
            else:
                trend = "stable"

            per_material_summary[mat] = {
                "n_cycles": len(values_hist),
                "mean_value": round(mean_v, 4),
                "min_value": round(min_v, 4),
                "max_value": round(max_v, 4),
                "latest_value": round(latest_v, 4),
                "trend": trend,
                "first_sim_day": hist[0]["sim_day"],
                "last_sim_day": hist[-1]["sim_day"],
            }

        result = {
            "metric": metric,
            "metric_description": desc,
            "direction": direction,
            "filter": {
                "material_type": material_type,
                "since_sim_day": since_sim_day,
                "until_sim_day": until_sim_day,
            },
            "n_rows_evaluated": len(results),
            "n_rows_returned": min(limit, len(results)),
            "timeseries": results[:max(1, min(500, limit))],
            "per_material_summary": per_material_summary,
        }
        self._cache_set(_cache_key, result)
        return result

    def get_material_supply_demand_balance(
        self,
        since_sim_day: Optional[int] = None,
        until_sim_day: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        iter #48: Material supply vs demand balance.

        For each material_type, computes:
        - total_supply_tons: sum of available_tons from supply_offers
        - total_demand_tons: sum of required_tons from demand_requests
        - total_matched_tons: sum of tons from matches
        - supply_demand_ratio: matched / supply
        - demand_fulfillment_pct: matched / demand
        - excess_supply_tons: supply - matched (oversupply)
        - unmet_demand_tons: demand - matched (unmet)

        Useful for identifying materials with chronic oversupply or shortage.

        Returns:
            [{
              material_type: str,
              total_supply_tons, total_demand_tons, total_matched_tons,
              supply_demand_ratio, demand_fulfillment_pct,
              excess_supply_tons, unmet_demand_tons,
              n_supply_offers, n_demand_requests, n_matches,
            }, ...]
            Sorted by unmet_demand_tons DESC (most-unmet first).
        """
        where_clauses: List[str] = []
        params: List[Any] = []
        if since_sim_day is not None:
            where_clauses.append("c.sim_day >= ?")
            params.append(int(since_sim_day))
        if until_sim_day is not None:
            where_clauses.append("c.sim_day <= ?")
            params.append(int(until_sim_day))
        where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

        with self._conn() as conn:
            rows = conn.execute(
                f"""WITH supply AS (
                       SELECT s.material_type AS mt,
                              SUM(s.available_tons) AS total_supply_tons,
                              COUNT(*) AS n_supply_offers
                       FROM supply_offers s
                       JOIN optimization_cycles c ON c.cycle_id = s.cycle_id
                       {where_sql}
                       GROUP BY s.material_type
                   ),
                   demand AS (
                       SELECT d.material_type AS mt,
                              SUM(d.required_tons) AS total_demand_tons,
                              COUNT(*) AS n_demand_requests
                       FROM demand_requests d
                       JOIN optimization_cycles c ON c.cycle_id = d.cycle_id
                       {where_sql}
                       GROUP BY d.material_type
                   ),
                   matched AS (
                       SELECT m.material_type AS mt,
                              SUM(m.tons) AS total_matched_tons,
                              COUNT(*) AS n_matches
                       FROM matches m
                       JOIN optimization_cycles c ON c.cycle_id = m.cycle_id
                       {where_sql}
                       GROUP BY m.material_type
                   )
                   SELECT COALESCE(s.mt, d.mt, mat.mt) AS material_type,
                          COALESCE(s.total_supply_tons, 0) AS total_supply_tons,
                          COALESCE(d.total_demand_tons, 0) AS total_demand_tons,
                          COALESCE(mat.total_matched_tons, 0) AS total_matched_tons,
                          COALESCE(s.n_supply_offers, 0) AS n_supply_offers,
                          COALESCE(d.n_demand_requests, 0) AS n_demand_requests,
                          COALESCE(mat.n_matches, 0) AS n_matches
                   FROM supply s
                   FULL OUTER JOIN demand d ON s.mt = d.mt
                   FULL OUTER JOIN matched mat ON COALESCE(s.mt, d.mt) = mat.mt
                   WHERE COALESCE(s.mt, d.mt, mat.mt) IS NOT NULL
                   ORDER BY material_type ASC""",
                params + params + params,  # 3 copies for 3 CTEs
            ).fetchall()

        results = []
        for r in rows:
            d = dict(r)
            sup = d["total_supply_tons"] or 0
            dem = d["total_demand_tons"] or 0
            mat = d["total_matched_tons"] or 0
            d["supply_demand_ratio"] = round(mat / sup, 3) if sup > 0 else None
            d["demand_fulfillment_pct"] = round(100 * mat / dem, 2) if dem > 0 else None
            d["excess_supply_tons"] = round(sup - mat, 2)
            d["unmet_demand_tons"] = round(dem - mat, 2)
            d["total_supply_tons"] = round(sup, 2)
            d["total_demand_tons"] = round(dem, 2)
            d["total_matched_tons"] = round(mat, 2)
            results.append(d)

        # Re-sort by unmet_demand_tons DESC
        results.sort(key=lambda r: r["unmet_demand_tons"], reverse=True)
        return results

    def get_material_aggregates(self, material_type: Optional[str] = None,
                                 limit: int = 50) -> List[Dict[str, Any]]:
        """
        Material type 聚合统计 (iter #16) — 每个 material_type 的累计 KPI。

        和 get_supply_aggregates 类似, 但按 material_type 维度聚合:
        - 哪些材料最常被生成 (建筑废料? 金属? 混合废料?)
        - 哪些材料匹配率最高 (demand 多?)
        - 哪些材料运输距离最长 (供需地理分布)

        Args:
            material_type: 可选, 只查某个 material_type
            limit: 最多返回多少 material (default 50)

        Returns:
            [{material_type, n_supply_offers, n_cycles_with_material,
              total_available_tons, total_matched_tons,
              avg_quality_score, n_matches, n_distinct_supplies,
              avg_match_distance_km, max_match_distance_km,
              match_rate_pct}, ...]
            按 total_available_tons DESC 排序
        """
        with self._conn() as conn:
            where_clauses: List[str] = []
            params: List[Any] = []

            # material_type 过滤 — 注意 material_type 在 supply_offers + matches 两张表都有
            # 这里从 supply_offers 主聚合, matches 走 join
            if material_type:
                where_clauses.append("s.material_type = ?")
                params.append(material_type)

            where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

            sql = f"""SELECT s.material_type,
                             COUNT(*) as n_offers,
                             COUNT(DISTINCT s.cycle_id) as n_cycles,
                             COUNT(DISTINCT s.supply_id) as n_distinct_supplies,
                             SUM(s.available_tons) as total_available,
                             AVG(s.quality_score) as avg_quality
                      FROM supply_offers s
                      {where_sql}
                      GROUP BY s.material_type
                      ORDER BY total_available DESC
                      LIMIT ?"""
            params.append(limit)
            rows = conn.execute(sql, params).fetchall()

            result: List[Dict[str, Any]] = []
            for r in rows:
                mt = r["material_type"]
                # matches join: 同 material_type 的所有 match 行
                match_row = conn.execute(
                    """SELECT COUNT(*) as n_matches,
                              SUM(tons) as total_matched,
                              AVG(distance_km) as avg_dist,
                              MAX(distance_km) as max_dist
                       FROM matches
                       WHERE material_type = ?""",
                    (mt,),
                ).fetchone()
                total_avail = r["total_available"] or 0
                total_matched = match_row["total_matched"] or 0
                match_rate = (total_matched / total_avail * 100) if total_avail > 0 else 0.0
                d = {
                    "material_type": mt,
                    "n_supply_offers": r["n_offers"] or 0,
                    "n_cycles_with_material": r["n_cycles"] or 0,
                    "n_distinct_supplies": r["n_distinct_supplies"] or 0,
                    "total_available_tons": round(total_avail, 2),
                    "total_matched_tons": round(total_matched, 2),
                    "avg_quality_score": round(r["avg_quality"] or 0, 1),
                    "n_matches": match_row["n_matches"] or 0,
                    "avg_match_distance_km": round(match_row["avg_dist"] or 0, 2),
                    "max_match_distance_km": round(match_row["max_dist"] or 0, 2),
                    "match_rate_pct": round(match_rate, 1),
                }
                result.append(d)
        return result

    def get_supply_cohort_retention(self, material_type: Optional[str] = None) -> Dict[str, Any]:
        """
        Supply 留存分析 (iter #17) — 哪些 supply 点反复出现 vs 一次性出现。

        "留存" = supply_id 在不同 sim_day 都出现 (说明它在持续生成废料)
        "一次性" = supply_id 只在 1 个 cycle 出现 (可能是临时生成点)

        Args:
            material_type: 可选, 只查某种 material

        Returns:
            {
              total_supply_ids: int,
              n_one_time: int,           # 只出现 1 次
              n_repeating: int,          # 出现 ≥2 次
              retention_rate_pct: float, # n_repeating / total_supply_ids
              one_time_pct: float,       # n_one_time / total_supply_ids
              by_appearance_count: [{
                appearance_count: 2, n_supplies: 5, pct: 12.5
              }, ...],                  # 分布 (出现 1 次 / 2 次 / 3-5 次 / ...)
              material_type_filter: str|None,
            }
        """
        with self._conn() as conn:
            where_clause = ""
            params: List[Any] = []
            if material_type:
                where_clause = "WHERE material_type = ?"
                params.append(material_type)

            # 1. 按 supply_id 聚合 cycle 数
            counts = conn.execute(
                f"""SELECT COUNT(DISTINCT cycle_id) as n_cycles, COUNT(*) as n_offers
                    FROM supply_offers
                    {where_clause}""",
                params,
            ).fetchone()

            # 2. 按 supply_id 计数出现次数
            rows = conn.execute(
                f"""SELECT supply_id, COUNT(DISTINCT cycle_id) as n_cycles
                    FROM supply_offers
                    {where_clause}
                    GROUP BY supply_id""",
                params,
            ).fetchall()

        total_ids = len(rows)
        if total_ids == 0:
            return {
                "total_supply_ids": 0,
                "n_one_time": 0,
                "n_repeating": 0,
                "retention_rate_pct": 0.0,
                "one_time_pct": 0.0,
                "by_appearance_count": [],
                "material_type_filter": material_type,
            }

        n_one_time = sum(1 for r in rows if r["n_cycles"] == 1)
        n_repeating = total_ids - n_one_time

        # 3. 按 appearance_count 分桶 (1 / 2 / 3-5 / 6-10 / 11+)
        buckets = {
            "1 (one-time)": 0,
            "2": 0,
            "3-5": 0,
            "6-10": 0,
            "11+": 0,
        }
        for r in rows:
            c = r["n_cycles"]
            if c == 1:
                buckets["1 (one-time)"] += 1
            elif c == 2:
                buckets["2"] += 1
            elif c <= 5:
                buckets["3-5"] += 1
            elif c <= 10:
                buckets["6-10"] += 1
            else:
                buckets["11+"] += 1

        by_appearance_count = [
            {
                "appearance_count_label": label,
                "n_supplies": n,
                "pct": round(n / total_ids * 100, 1),
            }
            for label, n in buckets.items()
        ]

        return {
            "total_supply_ids": total_ids,
            "n_one_time": n_one_time,
            "n_repeating": n_repeating,
            "retention_rate_pct": round(n_repeating / total_ids * 100, 1),
            "one_time_pct": round(n_one_time / total_ids * 100, 1),
            "by_appearance_count": by_appearance_count,
            "total_supply_offers": counts["n_offers"] or 0,
            "total_cycles_with_supply": counts["n_cycles"] or 0,
            "material_type_filter": material_type,
        }

    def get_cohort_retention_by_material(self) -> List[Dict[str, Any]]:
        """
        iter #42: Per-material supply retention breakdown.

        For each material_type, compute the same retention metrics as
        ``get_supply_cohort_retention`` but as a per-material slice. This
        reveals which materials have stable vs volatile supply sources.

        Returns:
            [{
                material_type: str,
                total_supply_ids: int,
                n_one_time: int,
                n_repeating: int,
                retention_rate_pct: float,
                one_time_pct: float,
                total_supply_offers: int,
                total_cycles_with_supply: int,
            }, ...]

            Sorted by total_supply_ids DESC.
        """
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT
                       material_type,
                       COUNT(DISTINCT supply_id) as n_supply_ids,
                       COUNT(DISTINCT cycle_id) as n_cycles,
                       COUNT(*) as n_offers
                   FROM supply_offers
                   WHERE material_type IS NOT NULL
                   GROUP BY material_type
                   ORDER BY n_supply_ids DESC"""
            ).fetchall()

            results: List[Dict[str, Any]] = []
            for r in rows:
                mat = r["material_type"]
                # Per-supply_id appearance count
                supply_rows = conn.execute(
                    """SELECT COUNT(DISTINCT cycle_id) as n_cycles
                       FROM supply_offers
                       WHERE material_type = ?
                       GROUP BY supply_id""",
                    (mat,),
                ).fetchall()
                total_ids = len(supply_rows)
                n_one_time = sum(1 for s in supply_rows if s["n_cycles"] == 1)
                n_repeating = total_ids - n_one_time
                results.append({
                    "material_type": mat,
                    "total_supply_ids": total_ids,
                    "n_one_time": n_one_time,
                    "n_repeating": n_repeating,
                    "retention_rate_pct": round(n_repeating / total_ids * 100, 1) if total_ids else 0.0,
                    "one_time_pct": round(n_one_time / total_ids * 100, 1) if total_ids else 0.0,
                    "total_supply_offers": r["n_offers"] or 0,
                    "total_cycles_with_supply": r["n_cycles"] or 0,
                })
            return results

    def get_cohort_retention_crosstab(
        self,
        n_periods: int = 4,
        period_unit: str = "quartile",
        material_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        iter #44: Cross-tab cohort retention (period × material).

        Returns a 2D matrix: rows = periods, columns = materials,
        values = retention_rate_pct for that material in that period.

        Args:
            n_periods: how many time periods (1-10, default 4)
            period_unit: 'quartile' | 'day' | 'week' | 'month'
            material_type: optional filter to single material

        Returns:
            {
              n_periods: int,
              period_unit: str,
              period_labels: [{period_idx, sim_day_min, sim_day_max}, ...],
              materials: [str, ...],  # sorted list of material names
              matrix: [[float, ...], ...],  # matrix[i][j] = retention% for period i, material j
              cell_counts: [[int, ...], ...],  # n_supply_ids per cell (for sample-size context)
              material_filter: str | None,
              trend_per_material: {<material>: "improving" | "declining" | "stable" | "unknown"},
            }
        """
        with self._conn() as conn:
            # Get sim_day range
            range_row = conn.execute(
                "SELECT MIN(sim_day) as min_d, MAX(sim_day) as max_d, COUNT(*) as n_cycles FROM optimization_cycles"
            ).fetchone()
            if not range_row["min_d"]:
                return {
                    "n_periods": 0, "period_unit": period_unit,
                    "period_labels": [], "materials": [], "matrix": [], "cell_counts": [],
                    "material_filter": material_type,
                    "trend_per_material": {},
                }
            min_day = range_row["min_d"]
            max_day = range_row["max_d"]
            n_cycles = range_row["n_cycles"]

            # Get unique materials
            where = "WHERE material_type IS NOT NULL"
            params: List[Any] = []
            if material_type:
                where += " AND material_type = ?"
                params.append(material_type)
            mat_rows = conn.execute(
                f"SELECT DISTINCT material_type FROM supply_offers {where} ORDER BY material_type",
                params,
            ).fetchall()
            materials = [r["material_type"] for r in mat_rows]
            if not materials:
                return {
                    "n_periods": 0, "period_unit": period_unit,
                    "period_labels": [], "materials": [], "matrix": [], "cell_counts": [],
                    "material_filter": material_type,
                    "trend_per_material": {},
                }

            # Compute period boundaries (similar to by_period)
            day_range = max_day - min_day + 1
            n_periods_eff = min(n_periods, day_range, 10)  # cap at 10
            days_per_segment = max(1, day_range // n_periods_eff)

            period_labels = []
            for i in range(n_periods_eff):
                p_start = min_day + i * days_per_segment
                p_end = min_day + (i + 1) * days_per_segment - 1
                p_end = min(p_end, max_day)
                if p_start > max_day:
                    break
                period_labels.append({
                    "period_idx": i + 1,
                    "sim_day_min": p_start,
                    "sim_day_max": p_end,
                })
            n_periods = len(period_labels)

            # Build matrix: rows = periods, cols = materials
            matrix: List[List[Optional[float]]] = []
            cell_counts: List[List[int]] = []
            for p in period_labels:
                row_retention: List[Optional[float]] = []
                row_counts: List[int] = []
                for mat in materials:
                    rows = conn.execute(
                        """SELECT s.supply_id, COUNT(DISTINCT s.cycle_id) as n_cycles
                           FROM supply_offers s
                           JOIN optimization_cycles c ON c.cycle_id = s.cycle_id
                           WHERE c.sim_day BETWEEN ? AND ?
                             AND s.material_type = ?
                           GROUP BY s.supply_id""",
                        (p["sim_day_min"], p["sim_day_max"], mat),
                    ).fetchall()
                    n_ids = len(rows)
                    n_rep = sum(1 for r in rows if r["n_cycles"] >= 2)
                    rate = round(n_rep / n_ids * 100, 1) if n_ids > 0 else None
                    row_retention.append(rate)
                    row_counts.append(n_ids)
                matrix.append(row_retention)
                cell_counts.append(row_counts)

        # Trend per material
        trend_per_material: Dict[str, str] = {}
        for j, mat in enumerate(materials):
            col = [matrix[i][j] for i in range(n_periods) if matrix[i][j] is not None]
            if len(col) >= 2:
                first, last = col[0], col[-1]
                if first is None or last is None:
                    trend_per_material[mat] = "unknown"
                elif last - first > 5:
                    trend_per_material[mat] = "improving"
                elif first - last > 5:
                    trend_per_material[mat] = "declining"
                else:
                    trend_per_material[mat] = "stable"
            else:
                trend_per_material[mat] = "unknown"

        return {
            "n_periods": n_periods,
            "period_unit": period_unit,
            "period_labels": period_labels,
            "materials": materials,
            "matrix": matrix,
            "cell_counts": cell_counts,
            "material_filter": material_type,
            "trend_per_material": trend_per_material,
        }

    def get_cohort_retention_by_period(
        self,
        n_periods: int = 4,
        period_unit: str = "quartile",  # iter #24: day | week | month | quartile
        material_type: Optional[str] = None,  # iter #45: filter by material
    ) -> Dict[str, Any]:
        """
        Supply 留存按时段划分 (iter #19 + iter #24 时间窗口扩展) — 早期 vs 后期 retention 对比。

        把所有 cycle 按 sim_day 顺序划分成多个 period, 每段独立计算 retention rate,
        让用户看 早期 vs 后期 churn 趋势。

        Args:
            n_periods: 分多少段 (default 4 = quartiles, max 10)
                       - quartile: 忽略这个参数, 自动按 sim_day range 等分为 4 段
                       - day: 每段 = 1 sim_day, n_periods 限定 max=30
                       - week: 每段 = 7 sim_days, n_periods 限定 max=52
                       - month: 每段 = 30 sim_days, n_periods 限定 max=12

        Returns:
            {
              total_supply_ids: int,
              n_periods: int,
              period_unit: "quartile" | "day" | "week" | "month",  # iter #24
              period_labels: ["Period 1 (sim_day 1-7)", ...],
              periods: [{
                period_idx: 1,
                period_label: "...",
                sim_day_range: {min, max},
                n_supply_ids: int,
                n_one_time: int,
                n_repeating: int,
                retention_rate_pct: float,
                one_time_pct: float,
              }, ...],
              trend: "improving" | "declining" | "stable" | "unknown"
              (比较 first vs last period retention_rate_pct, ±5% 阈值)
            }
        """
        # iter #24: validate period_unit
        valid_units = ("quartile", "day", "week", "month")
        if period_unit not in valid_units:
            raise ValueError(
                f"period_unit must be one of {valid_units}, got '{period_unit}'"
            )

        # iter #24: clamp n_periods based on unit
        max_periods_map = {
            "quartile": 10,
            "day": 30,    # 30 days ≈ 1 month
            "week": 52,   # 52 weeks = 1 year
            "month": 12,  # 12 months = 1 year
        }
        max_n = max_periods_map[period_unit]
        if n_periods < 1:
            raise ValueError("n_periods must be >= 1")
        if n_periods > max_n:
            raise ValueError(
                f"n_periods must be <= {max_n} for period_unit='{period_unit}', got {n_periods}"
            )

        # iter #24: fixed segment sizes for non-quartile units
        days_per_segment_map = {
            "quartile": None,  # computed dynamically
            "day": 1,
            "week": 7,
            "month": 30,
        }
        days_per_segment = days_per_segment_map[period_unit]

        with self._conn() as conn:
            # Get min/max sim_day from supply_offers (the table we care about)
            # iter #45: optional material_type filter
            where_clause = ""
            range_params: List[Any] = []
            if material_type:
                where_clause = "WHERE s.material_type = ?"
                range_params = [material_type]
            range_row = conn.execute(
                f"""SELECT MIN(c.sim_day) as min_day, MAX(c.sim_day) as max_day,
                          COUNT(DISTINCT s.cycle_id) as n_cycles,
                          COUNT(DISTINCT s.supply_id) as n_supplies
                   FROM supply_offers s
                   JOIN optimization_cycles c ON c.cycle_id = s.cycle_id
                   {where_clause}""",
                range_params,
            ).fetchone()
            min_day = range_row["min_day"]
            max_day = range_row["max_day"]
            n_cycles = range_row["n_cycles"] or 0

            if min_day is None or max_day is None or n_cycles < 1:
                return {
                    "total_supply_ids": range_row["n_supplies"] or 0,
                    "n_periods": n_periods,
                    "period_unit": period_unit,  # iter #24
                    "period_labels": [],
                    "periods": [],
                    "trend": "unknown",
                    "material_type_filter": material_type,  # iter #45
                    "trend_per_material": {},  # iter #46
                }

            # iter #24: determine segment boundaries based on unit
            period_labels: list = []
            if period_unit == "quartile":
                # original logic: equal split
                total_days = max_day - min_day + 1
                days_per_period = max(1, total_days // n_periods)
                for i in range(n_periods):
                    p_start = min_day + i * days_per_period
                    p_end = (
                        min_day + (i + 1) * days_per_period - 1
                        if i < n_periods - 1 else max_day
                    )
                    period_labels.append({
                        "period_idx": i + 1,
                        "sim_day_min": p_start,
                        "sim_day_max": p_end,
                    })
            else:
                # iter #24: fixed segment size (day/week/month)
                # n_periods 表示要返回多少个 period (从 min_day 开始向后数)
                # 如果 n_periods=0 (未指定), 自动算
                if period_unit == "day":
                    auto_n = min(max_day - min_day + 1, max_n)
                    n_periods_eff = auto_n if n_periods == 4 else n_periods
                elif period_unit == "week":
                    auto_n = min((max_day - min_day + 1 + 6) // 7, max_n)
                    n_periods_eff = auto_n if n_periods == 4 else n_periods
                else:  # month
                    auto_n = min((max_day - min_day + 1 + 29) // 30, max_n)
                    n_periods_eff = auto_n if n_periods == 4 else n_periods
                # clamp to max_n
                n_periods_eff = min(n_periods_eff, max_n)

                for i in range(n_periods_eff):
                    p_start = min_day + i * days_per_segment
                    p_end = min_day + (i + 1) * days_per_segment - 1
                    # don't exceed max_day
                    p_end = min(p_end, max_day)
                    if p_start > max_day:
                        break
                    period_labels.append({
                        "period_idx": i + 1,
                        "sim_day_min": p_start,
                        "sim_day_max": p_end,
                    })
                # update n_periods to reflect actual segments generated
                n_periods = len(period_labels)

                if n_cycles < n_periods:
                    return {
                        "total_supply_ids": range_row["n_supplies"] or 0,
                        "n_periods": n_periods,
                        "period_unit": period_unit,
                        "period_labels": [],
                        "periods": [],
                        "trend": "unknown",
                        "material_type_filter": material_type,  # iter #45
                        "trend_per_material": {},  # iter #46
                    }

            periods_data = []
            for p in period_labels:
                # 该段内的 supply_ids + 出现次数
                # iter #45: optional material_type filter
                if material_type:
                    rows = conn.execute(
                        """SELECT s.supply_id, COUNT(DISTINCT s.cycle_id) as n_cycles
                           FROM supply_offers s
                           JOIN optimization_cycles c ON c.cycle_id = s.cycle_id
                           WHERE c.sim_day BETWEEN ? AND ?
                             AND s.material_type = ?
                           GROUP BY s.supply_id""",
                        (p["sim_day_min"], p["sim_day_max"], material_type),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        """SELECT s.supply_id, COUNT(DISTINCT s.cycle_id) as n_cycles
                           FROM supply_offers s
                           JOIN optimization_cycles c ON c.cycle_id = s.cycle_id
                           WHERE c.sim_day BETWEEN ? AND ?
                           GROUP BY s.supply_id""",
                        (p["sim_day_min"], p["sim_day_max"]),
                    ).fetchall()

                n_ids = len(rows)
                n_one = sum(1 for r in rows if r["n_cycles"] == 1)
                n_rep = n_ids - n_one
                ret_pct = round(n_rep / n_ids * 100, 1) if n_ids > 0 else 0.0
                one_pct = round(n_one / n_ids * 100, 1) if n_ids > 0 else 0.0

                # iter #24: period_label uses unit-aware format
                if period_unit == "day":
                    label = f"Day {p['period_idx']} (sim_day {p['sim_day_min']})"
                elif period_unit == "week":
                    label = f"Week {p['period_idx']} (sim_day {p['sim_day_min']}-{p['sim_day_max']})"
                elif period_unit == "month":
                    label = f"Month {p['period_idx']} (sim_day {p['sim_day_min']}-{p['sim_day_max']})"
                else:
                    label = f"Period {p['period_idx']} (sim_day {p['sim_day_min']}-{p['sim_day_max']})"

                periods_data.append({
                    "period_idx": p["period_idx"],
                    "period_label": label,
                    "sim_day_range": {"min": p["sim_day_min"], "max": p["sim_day_max"]},
                    "n_supply_ids": n_ids,
                    "n_one_time": n_one,
                    "n_repeating": n_rep,
                    "retention_rate_pct": ret_pct,
                    "one_time_pct": one_pct,
                })

        # Trend: 比较 first vs last period
        trend = "unknown"
        if len(periods_data) >= 2:
            first_ret = periods_data[0]["retention_rate_pct"]
            last_ret = periods_data[-1]["retention_rate_pct"]
            diff = last_ret - first_ret
            if diff > 5:
                trend = "improving"
            elif diff < -5:
                trend = "declining"
            else:
                trend = "stable"

        # iter #46: Per-material trend (consistency with crosstab).
        # Only computed when no material_type filter (otherwise it's redundant).
        # Uses a separate connection to avoid disturbing the main with block scope.
        trend_per_material: Dict[str, str] = {}
        if not material_type and len(periods_data) >= 2:
            with self._conn() as conn2:
                for mat_row in conn2.execute(
                    "SELECT DISTINCT material_type FROM supply_offers "
                    "WHERE material_type IS NOT NULL ORDER BY material_type"
                ).fetchall():
                    mat = mat_row["material_type"]
                    per_period_rates = []
                    for p in period_labels:
                        mat_rows = conn2.execute(
                            """SELECT s.supply_id, COUNT(DISTINCT s.cycle_id) as n_cycles
                               FROM supply_offers s
                               JOIN optimization_cycles c ON c.cycle_id = s.cycle_id
                               WHERE c.sim_day BETWEEN ? AND ?
                                 AND s.material_type = ?
                               GROUP BY s.supply_id""",
                            (p["sim_day_min"], p["sim_day_max"], mat),
                        ).fetchall()
                        n_ids = len(mat_rows)
                        n_rep = sum(1 for r in mat_rows if r["n_cycles"] >= 2)
                        if n_ids > 0:
                            per_period_rates.append(round(n_rep / n_ids * 100, 1))
                        else:
                            per_period_rates.append(None)
                    rates = [r for r in per_period_rates if r is not None]
                    if len(rates) >= 2:
                        diff = rates[-1] - rates[0]
                        if diff > 5:
                            trend_per_material[mat] = "improving"
                        elif diff < -5:
                            trend_per_material[mat] = "declining"
                        else:
                            trend_per_material[mat] = "stable"
                    else:
                        trend_per_material[mat] = "unknown"

        return {
            "total_supply_ids": range_row["n_supplies"] or 0,
            "n_periods": n_periods,
            "period_unit": period_unit,  # iter #24
            "period_labels": [p["period_label"] for p in periods_data],
            "periods": periods_data,
            "trend": trend,
            "material_type_filter": material_type,  # iter #45
            "trend_per_material": trend_per_material,  # iter #46
        }

    def get_cohort_retention_by_season(
        self,
        material_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        iter #70: Cohort retention broken down by season (winter/spring/summer/fall).

        Extends iter #19/65 cohort retention with a SEASONAL dimension. For each
        season (defined by the cycle's `seasonal_month` column), compute retention
        rates independently, then provide a global summary with the best/worst
        season for retention.

        Args:
            material_type: optional filter to single material

        Returns:
            {
              n_seasons_with_data: int,             # 0..4
              total_supply_ids: int,                # dedup count across all seasons
              seasons: [{
                season: "winter" | "spring" | "summer" | "fall",
                season_name: "Winter",
                season_emoji: "❄️",
                months: [12, 1, 2],
                n_supply_ids: int,
                n_one_time: int,
                n_repeating: int,
                retention_rate_pct: float,         # 0-100
                one_time_pct: float,               # 0-100
                total_supply_offers: int,
                n_cycles_in_season: int,
              }, ...],
              best_season: "winter" | ...,          # by retention_rate_pct, None if 0 seasons
              worst_season: ...,
              best_season_pct: float,
              worst_season_pct: float,
              worst_vs_best_pct: float,            # (worst - best) / best * 100, 0 if best==0
              material_type_filter: Optional[str],
            }
        """
        # Iter #70: each season is a deterministic list of months
        # using the cycle's seasonal_month column (set in begin_cycle).
        season_results: List[Dict[str, Any]] = []
        # Track best/worst as we go (avoid second pass)
        best_season: Optional[str] = None
        worst_season: Optional[str] = None
        best_pct: float = -1.0  # sentinel
        worst_pct: float = 101.0  # sentinel (so first real value overwrites)

        seen_supply_ids: set = set()  # global dedup for total_supply_ids

        with self._conn() as conn:
            for season_key, months in self.SEASON_MONTHS.items():
                # Build WHERE clause for season months + optional material filter
                month_clause = ",".join("?" for _ in months)
                mat_params: List[Any] = list(months)
                extra_where = ""
                if material_type:
                    extra_where = " AND s.material_type = ?"
                    mat_params.append(material_type)
                # Get supply_ids + appearance counts for this season
                rows = conn.execute(
                    f"""SELECT s.supply_id, COUNT(DISTINCT s.cycle_id) as n_cycles
                        FROM supply_offers s
                        JOIN optimization_cycles c ON c.cycle_id = s.cycle_id
                        WHERE c.seasonal_month IN ({month_clause})
                          {extra_where}
                        GROUP BY s.supply_id""",
                    mat_params,
                ).fetchall()
                n_ids = len(rows)
                n_one = sum(1 for r in rows if r["n_cycles"] == 1)
                n_rep = n_ids - n_one
                ret_pct = round(n_rep / n_ids * 100, 1) if n_ids > 0 else 0.0
                one_pct = round(n_one / n_ids * 100, 1) if n_ids > 0 else 0.0

                # Total supply_offers (rows) in this season
                # n_cycles_in_season: number of optimization_cycles whose seasonal_month is in this season
                count_params: List[Any] = list(months)
                count_extra = ""
                if material_type:
                    count_extra = " AND s.material_type = ?"
                    count_params.append(material_type)
                total_offers_row = conn.execute(
                    f"""SELECT COUNT(*) as n_offers, COUNT(DISTINCT s.cycle_id) as n_cycles
                        FROM supply_offers s
                        JOIN optimization_cycles c ON c.cycle_id = s.cycle_id
                        WHERE c.seasonal_month IN ({month_clause})
                          {count_extra}""",
                    count_params,
                ).fetchone()
                total_offers = total_offers_row["n_offers"] or 0
                n_cycles_in_season = total_offers_row["n_cycles"] or 0

                season_results.append({
                    "season": season_key,
                    "season_name": self.SEASON_NAMES[season_key],
                    "season_emoji": self.SEASON_EMOJI[season_key],
                    "months": months,
                    "n_supply_ids": n_ids,
                    "n_one_time": n_one,
                    "n_repeating": n_rep,
                    "retention_rate_pct": ret_pct,
                    "one_time_pct": one_pct,
                    "total_supply_offers": total_offers,
                    "n_cycles_in_season": n_cycles_in_season,
                })

                if n_ids > 0:
                    if ret_pct > best_pct:
                        best_pct = ret_pct
                        best_season = season_key
                    if ret_pct < worst_pct:
                        worst_pct = ret_pct
                        worst_season = season_key

                for r in rows:
                    seen_supply_ids.add(r["supply_id"])

        # Normalize best/worst when no seasons have data
        if best_season is None:
            best_pct = 0.0
            worst_pct = 0.0
            worst_vs_best_pct = 0.0
        else:
            worst_vs_best_pct = round((worst_pct - best_pct) / best_pct * 100, 1) if best_pct > 0 else 0.0

        return {
            "n_seasons_with_data": sum(1 for s in season_results if s["n_supply_ids"] > 0),
            "total_supply_ids": len(seen_supply_ids),
            "seasons": season_results,
            "best_season": best_season,
            "worst_season": worst_season,
            "best_season_pct": best_pct,
            "worst_season_pct": worst_pct,
            "worst_vs_best_pct": worst_vs_best_pct,
            "material_type_filter": material_type,
        }

    def get_cohort_retention_by_region(
        self,
        material_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        iter #73: Cohort retention broken down by GEOGRAPHIC REGION (city).

        Extends iter #19/42/65/70 cohort retention with a REGION dimension.
        For each city (Göteborg / Borås / Stockholm — the 3 cities where
        real_sweden_facilities has facilities), compute retention rates
        independently, then provide a global summary with the best/worst
        region for retention.

        City assignment uses haversine distance from each supply_offers row's
        (location_lat, location_lon) to the nearest facility in
        real_sweden_facilities.ALL_FACILITIES. Rows with NULL coords fall
        into the `unknown` bucket (separate from any real region).

        Args:
            material_type: optional filter to single material

        Returns:
            {
              n_regions_with_data: int,             # 0..3 (real cities with data)
              n_unknown_with_data: int,             # 0 or 1 (NULL coords bucket)
              total_supply_ids: int,                # dedup count across all regions
              regions: [{
                region: "Göteborg" | "Borås" | "Stockholm" | "unknown",
                region_name: "Göteborg",
                region_emoji: "🏙️",
                n_supply_ids: int,
                n_one_time: int,
                n_repeating: int,
                retention_rate_pct: float,         # 0-100
                one_time_pct: float,               # 0-100
                total_supply_offers: int,
                n_cycles_in_region: int,
              }, ...],                             # always 4 entries (consistent shape)
              best_region: str,                    # by retention_rate_pct, None if 0 regions
              worst_region: str,
              best_region_pct: float,
              worst_region_pct: float,
              worst_vs_best_pct: float,            # (worst - best) / best * 100
              material_type_filter: Optional[str],
              city_assignment_method: str,         # "haversine_nearest_facility"
            }
        """
        # Load facility list (real_sweden_facilities) once at top of method.
        try:
            from data.real_sweden_facilities import ALL_FACILITIES as _ALL_FACILITIES
        except Exception:
            _ALL_FACILITIES = []
        facilities = list(_ALL_FACILITIES or [])

        # Build city → [(lat, lon)] map for haversine clustering.
        # If a city has 0 facilities (data not available), it will still appear
        # in the regions array with n_supply_ids=0 — consistent shape.
        city_to_coords: Dict[str, List[Tuple[float, float]]] = {}
        for f in facilities:
            try:
                lat = float(f["lat"])
                lon = float(f["lon"])
                city = str(f.get("city", "")).strip()
                if not city:
                    continue
                city_to_coords.setdefault(city, []).append((lat, lon))
            except (KeyError, TypeError, ValueError):
                continue

        # Stable city ordering: Göteborg → Borås → Stockholm → unknown.
        # (Matches the Sweden west→east geographic flow.)
        ordered_cities = ["Göteborg", "Borås", "Stockholm"]

        # Emoji per city (Sweden Vasa-rose + regional icons).
        region_emoji_map = {
            "Göteborg": "⚓",     # port/west coast
            "Borås": "🧵",        # textile recycling tradition
            "Stockholm": "🏛️",    # capital
            "unknown": "❓",
        }

        def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
            """Pure-python haversine (avoids numpy dep for tests)."""
            import math
            r = 6371.0
            lat1r = math.radians(lat1)
            lat2r = math.radians(lat2)
            dlat = math.radians(lat2 - lat1)
            dlon = math.radians(lon2 - lon1)
            a = (
                math.sin(dlat / 2.0) ** 2
                + math.cos(lat1r) * math.cos(lat2r) * math.sin(dlon / 2.0) ** 2
            )
            c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
            return r * c

        def _city_for(lat: Optional[float], lon: Optional[float]) -> str:
            """Return nearest city by haversine to its facilities; else 'unknown'."""
            if lat is None or lon is None:
                return "unknown"
            best_city = "unknown"
            best_km = float("inf")
            for city, coords in city_to_coords.items():
                for flat, flon in coords:
                    d = _haversine_km(lat, lon, flat, flon)
                    if d < best_km:
                        best_km = d
                        best_city = city
            return best_city

        # Fetch all supply_offers rows (with material filter applied at SQL).
        sql = """SELECT supply_id, location_lat, location_lon, material_type,
                        COUNT(DISTINCT cycle_id) as n_cycles
                 FROM supply_offers
                 WHERE 1=1"""
        params: List[Any] = []
        if material_type:
            sql += " AND material_type = ?"
            params.append(material_type)
        sql += " GROUP BY supply_id, location_lat, location_lon"

        # Track per-region supply_ids + appearance counts (id → n_cycles).
        region_to_ids: Dict[str, Dict[str, int]] = {
            c: {} for c in ordered_cities
        }
        region_to_ids["unknown"] = {}
        # Also track total_supply_offers + n_cycles per region (across rows).
        region_to_offers: Dict[str, int] = {c: 0 for c in ordered_cities}
        region_to_offers["unknown"] = 0
        region_to_cycles: Dict[str, set] = {c: set() for c in ordered_cities}
        region_to_cycles["unknown"] = set()
        seen_global_ids: set = set()

        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()

        for r in rows:
            sid = r["supply_id"]
            try:
                lat_v: Optional[float] = (
                    float(r["location_lat"]) if r["location_lat"] is not None else None
                )
                lon_v: Optional[float] = (
                    float(r["location_lon"]) if r["location_lon"] is not None else None
                )
            except (TypeError, ValueError):
                lat_v, lon_v = None, None
            n_cycles = int(r["n_cycles"] or 0)
            city = _city_for(lat_v, lon_v)
            region_to_ids[city][sid] = region_to_ids[city].get(sid, 0) + n_cycles
            region_to_offers[city] += n_cycles
            # Track distinct cycles per region (approximation: we don't have
            # cycle_id per row here; use cycles-in-which-this-supply-appeared).
            seen_global_ids.add(sid)

        # Build regions list (always 4 entries for stable shape).
        region_results: List[Dict[str, Any]] = []
        best_region: Optional[str] = None
        worst_region: Optional[str] = None
        best_pct: float = -1.0
        worst_pct: float = 101.0

        # Real cities first (3), then "unknown" bucket last.
        ordered_keys = ordered_cities + ["unknown"]
        for city in ordered_keys:
            ids_map = region_to_ids[city]
            n_ids = len(ids_map)
            n_one = sum(1 for v in ids_map.values() if v == 1)
            n_rep = n_ids - n_one
            ret_pct = round(n_rep / n_ids * 100, 1) if n_ids > 0 else 0.0
            one_pct = round(n_one / n_ids * 100, 1) if n_ids > 0 else 0.0
            region_results.append({
                "region": city,
                "region_name": city,
                "region_emoji": region_emoji_map.get(city, "📍"),
                "n_supply_ids": n_ids,
                "n_one_time": n_one,
                "n_repeating": n_rep,
                "retention_rate_pct": ret_pct,
                "one_time_pct": one_pct,
                "total_supply_offers": region_to_offers[city],
                "n_cycles_in_region": len(region_to_cycles[city]),
            })
            if n_ids > 0:
                if ret_pct > best_pct:
                    best_pct = ret_pct
                    best_region = city
                if ret_pct < worst_pct:
                    worst_pct = ret_pct
                    worst_region = city

        if best_region is None:
            best_pct = 0.0
            worst_pct = 0.0
            worst_vs_best_pct = 0.0
        else:
            worst_vs_best_pct = (
                round((worst_pct - best_pct) / best_pct * 100, 1)
                if best_pct > 0
                else 0.0
            )

        n_regions_with_data = sum(
            1 for r in region_results
            if r["region"] != "unknown" and r["n_supply_ids"] > 0
        )
        n_unknown_with_data = sum(
            1 for r in region_results
            if r["region"] == "unknown" and r["n_supply_ids"] > 0
        )

        return {
            "n_regions_with_data": n_regions_with_data,
            "n_unknown_with_data": n_unknown_with_data,
            "total_supply_ids": len(seen_global_ids),
            "regions": region_results,
            "best_region": best_region,
            "worst_region": worst_region,
            "best_region_pct": best_pct,
            "worst_region_pct": worst_pct,
            "worst_vs_best_pct": worst_vs_best_pct,
            "material_type_filter": material_type,
            "city_assignment_method": "haversine_nearest_facility",
        }

    def get_cycle_kpi_summary(self, last_n: Optional[int] = None,
                           since_sim_day: Optional[int] = None,
                           until_sim_day: Optional[int] = None) -> Dict[str, Any]:
        """
        Cycle KPI summary (iter #16 + iter #17 时间窗口过滤) — 所有 cycles 的整体 KPI。

        用于 dashboard 顶部数字 + 趋势 (last cycle, best cycle, worst cycle)。

        Args (iter #17 新增):
            last_n: 只看最近 N 个 cycle (按 sim_day DESC)
            since_sim_day: 起始 sim_day (含), None = 不限
            until_sim_day: 结束 sim_day (含), None = 不限

        Returns:
            {
              total_cycles, n_cycles_with_matches,
              total_tons_matched, total_distance_km, total_co2_kg, total_cost_sek,
              avg_tons_per_cycle, avg_cost_per_ton_sek, avg_co2_per_ton_kg,
              fleet_utilization_avg_pct,
              best_cycle: {cycle_id, sim_day, total_tons, total_cost_sek},
              worst_cycle: {...},
              last_cycle: {...},
              sim_day_range: {min, max},
              filter: {last_n, since_sim_day, until_sim_day} (echo back)
            }
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
            # 如果指定 last_n, 拿最近 N 个 cycle_id (按 sim_day DESC) 作为子集过滤
            if last_n is not None and last_n > 0:
                # 拿到最近 N 个 cycle_id
                limit_clause = "ORDER BY sim_day DESC LIMIT ?"
                # 重写 where: 子查询取最近 N 个
                inner_where = where_sql or "WHERE 1=1"
                cycle_ids_subq = (
                    f"SELECT cycle_id FROM optimization_cycles "
                    f"{inner_where} {limit_clause}"
                )
                row = conn.execute(
                    f"""SELECT COUNT(*) as total_cycles,
                              SUM(CASE WHEN n_matches > 0 THEN 1 ELSE 0 END) as n_with_matches,
                              SUM(total_tons) as total_tons,
                              SUM(total_distance_km) as total_distance,
                              SUM(total_co2_kg) as total_co2,
                              SUM(total_cost_sek) as total_cost,
                              AVG(total_tons) as avg_tons,
                              AVG(total_distance_km) as avg_distance,
                              AVG(fleet_utilization_pct) as avg_util,
                              MIN(sim_day) as min_day,
                              MAX(sim_day) as max_day
                       FROM optimization_cycles
                       WHERE cycle_id IN ({cycle_ids_subq})""",
                    params + [last_n],
                ).fetchone()
            else:
                row = conn.execute(
                    f"""SELECT COUNT(*) as total_cycles,
                              SUM(CASE WHEN n_matches > 0 THEN 1 ELSE 0 END) as n_with_matches,
                              SUM(total_tons) as total_tons,
                              SUM(total_distance_km) as total_distance,
                              SUM(total_co2_kg) as total_co2,
                              SUM(total_cost_sek) as total_cost,
                              AVG(total_tons) as avg_tons,
                              AVG(total_distance_km) as avg_distance,
                              AVG(fleet_utilization_pct) as avg_util,
                              MIN(sim_day) as min_day,
                              MAX(sim_day) as max_day
                       FROM optimization_cycles
                       {where_sql}""",
                    params,
                ).fetchone()

            # best/worst/last cycle: 需遵循同一过滤
            if last_n is not None and last_n > 0:
                # 取同一个 cycle_id 子集
                cycle_ids_subq_for_bw = (
                    f"SELECT cycle_id FROM optimization_cycles {where_sql} "
                    f"ORDER BY sim_day DESC LIMIT ?"
                )
                bw_params = params + [last_n]
            else:
                cycle_ids_subq_for_bw = (
                    f"SELECT cycle_id FROM optimization_cycles {where_sql}"
                )
                bw_params = params

            bw_where = "WHERE cycle_id IN (" + cycle_ids_subq_for_bw + ")"
            best = conn.execute(
                f"""SELECT cycle_id, sim_day, total_tons, total_cost_sek
                   FROM optimization_cycles
                   {bw_where} AND total_tons > 0
                   ORDER BY total_tons DESC LIMIT 1""",
                bw_params,
            ).fetchone()
            worst = conn.execute(
                f"""SELECT cycle_id, sim_day, total_tons, total_cost_sek
                   FROM optimization_cycles
                   {bw_where} AND total_tons > 0
                   ORDER BY total_tons ASC LIMIT 1""",
                bw_params,
            ).fetchone()
            last = conn.execute(
                f"""SELECT cycle_id, sim_day, total_tons, total_cost_sek, n_matches
                   FROM optimization_cycles
                   {bw_where}
                   ORDER BY sim_day DESC LIMIT 1""",
                bw_params,
            ).fetchone()

        total_tons = row["total_tons"] or 0
        total_cost = row["total_cost"] or 0
        total_co2 = row["total_co2"] or 0
        n_cycles = row["total_cycles"] or 0
        avg_tons = row["avg_tons"] or 0
        return {
            "total_cycles": n_cycles,
            "n_cycles_with_matches": int(row["n_with_matches"] or 0),
            "total_tons_matched": round(total_tons, 2),
            "total_distance_km": round(row["total_distance"] or 0, 2),
            "total_co2_kg": round(total_co2, 2),
            "total_cost_sek": round(total_cost, 2),
            "avg_tons_per_cycle": round(avg_tons, 2),
            "avg_cost_per_ton_sek": round(total_cost / total_tons, 2) if total_tons > 0 else None,
            "avg_co2_per_ton_kg": round(total_co2 / total_tons, 2) if total_tons > 0 else None,
            "fleet_utilization_avg_pct": round(row["avg_util"] or 0, 1),
            "sim_day_range": {
                "min": row["min_day"],
                "max": row["max_day"],
            },
            "best_cycle": dict(best) if best else None,
            "worst_cycle": dict(worst) if worst else None,
            "last_cycle": dict(last) if last else None,
            "filter": {
                "last_n": last_n,
                "since_sim_day": since_sim_day,
                "until_sim_day": until_sim_day,
            },
        }
