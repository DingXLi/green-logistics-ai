"""周期 / 材料 / 路线对比分析。"""

from typing import Any, Dict, List, Optional
import json

from ._base import PersistenceBase


class ComparisonMixin(PersistenceBase):
    """ComparisonMixin —— 由 agents/persistence.py 拆分而来（iter #77）。"""

    def get_top_facilities_by_distance(
        self,
        metric: str = "avg_distance",
        facility_ids: Optional[List[str]] = None,
        city: Optional[str] = None,
        facility_type: Optional[str] = None,
        material_type: Optional[str] = None,
        since_sim_day: Optional[int] = None,
        until_sim_day: Optional[int] = None,
        limit: int = 10,
    ) -> Dict[str, Any]:
        """
        iter #59: Top real-Sweden facilities ranked by supply-distance metrics.

        Real Sweden facilities (13 hand-curated facilities in
        data/real_sweden_facilities.py — Renova, Stena, Suez, harbor_cargo,
        recycling_center, paper_mill, etc.) are used as demand_ids in
        demand_requests. Each match links a supply to a facility
        (demand_id = facility.id), so we can aggregate per-facility
        supply → facility haul metrics.

        Args:
            metric: which facility-distance metric to rank by. One of:
                - 'avg_distance' (avg km per match to facility, lower = better
                                  — closest suppliers)
                - 'min_distance' (shortest match to facility, lower = better)
                - 'max_distance' (longest match to facility, lower = better)
                - 'total_matched_tons' (sum of matched tons, higher = better)
                - 'match_count' (n matches, higher = better)
                - 'match_rate' (matches / cycles_with_demand, higher = better)
                - 'utilization_pct' (matched_tons / capacity * 100, higher = better — only for
                                     facilities with capacity metadata; None otherwise)
                - 'co2_per_ton' (total_co2 / total_matched_tons, lower = better)
            facility_ids: optional explicit list of facility IDs to include
                (default: all facilities in data/real_sweden_facilities.ALL_FACILITIES)
            city: optional city filter (Göteborg / Borås / Stockholm)
            facility_type: optional facility_type filter (recycling_center,
                harbor_cargo, metal_recovery, paper_mill, etc.)
            material_type: optional filter — only count matches of this material
            since_sim_day: optional lower bound (inclusive)
            until_sim_day: optional upper bound (inclusive)
            limit: top N (default 10, max 100)

        Returns:
          {
            metric, metric_description, direction,
            filter: {city, facility_type, material_type, facility_ids, sim_day_window},
            n_facilities_evaluated, n_facilities_returned,
            top_facilities: [{facility_id, name, city, facility_type,
                              processing_capacity_tons_per_day,
                              value, n_matches, n_cycles_with_demand,
                              total_matched_tons, total_required_tons,
                              avg_match_distance_km, min_match_distance_km,
                              max_match_distance_km, avg_match_tons,
                              utilization_pct, operator, source,
                              last_sim_day}, ...]
          }

        Use cases:
        - Identify facilities served by closest suppliers (low avg_distance)
        - Identify most-supplied facilities (high total_matched_tons)
        - Identify facilities with highest utilization vs capacity
        - Identify hardest-to-reach facilities (high avg_distance)
        """
        # iter #61: TTL cache check
        _cache_key = self._cache_key(
            "get_top_facilities_by_distance",
            {
                "metric": metric, "facility_ids": facility_ids,
                "city": city, "facility_type": facility_type,
                "material_type": material_type,
                "since_sim_day": since_sim_day, "until_sim_day": until_sim_day,
                "limit": limit,
            },
        )
        _cached = self._cache_get(_cache_key)
        if _cached is not None:
            return _cached

        valid_metrics = {
            "avg_distance": ("lower_is_better", "avg km per match"),
            "min_distance": ("lower_is_better", "shortest match km"),
            "max_distance": ("lower_is_better", "longest match km"),
            "total_matched_tons": ("higher_is_better", "total matched tons"),
            "match_count": ("higher_is_better", "number of matches"),
            "match_rate": ("higher_is_better", "matches per cycle"),
            "utilization_pct": ("higher_is_better", "matched/capacity %"),
            "co2_per_ton": ("lower_is_better", "kg CO2 per matched ton"),
        }
        if metric not in valid_metrics:
            raise ValueError(
                f"Unknown metric {metric!r}. Valid: {list(valid_metrics)}"
            )

        direction, desc = valid_metrics[metric]

        # Load facility metadata
        try:
            from data.real_sweden_facilities import ALL_FACILITIES
        except Exception:
            ALL_FACILITIES = []
        facility_meta = {f["id"]: f for f in (ALL_FACILITIES or [])}

        # Determine facility ID set
        if facility_ids:
            ids_to_use = list(facility_ids)
        else:
            ids_to_use = list(facility_meta.keys())
        # Apply city/facility_type filters at metadata level
        if city or facility_type:
            ids_to_use = [
                fid for fid in ids_to_use
                if fid in facility_meta
                and (not city or facility_meta[fid].get("city") == city)
                and (not facility_type or facility_meta[fid].get("facility_type") == facility_type)
            ]

        # Build SQL to aggregate per facility
        where_clauses = ["1=1"]
        params: List[Any] = []
        if material_type:
            where_clauses.append("d.material_type = ?")
            params.append(material_type)
        if since_sim_day is not None:
            where_clauses.append("c.sim_day >= ?")
            params.append(int(since_sim_day))
        if until_sim_day is not None:
            where_clauses.append("c.sim_day <= ?")
            params.append(int(until_sim_day))
        where_sql = " AND ".join(where_clauses)

        with self._conn() as conn:
            # Use only ids in ids_to_use (handles facility_ids + city/type filters)
            if ids_to_use:
                placeholders = ",".join(["?"] * len(ids_to_use))
                facility_where = f"d.demand_id IN ({placeholders})"
                facility_params: List[Any] = list(ids_to_use)
            else:
                facility_where = "1=0"
                facility_params = []

            rows = conn.execute(
                f"""SELECT d.demand_id,
                          COUNT(DISTINCT d.cycle_id) as n_cycles,
                          SUM(d.required_tons) as total_required
                FROM demand_requests d
                JOIN optimization_cycles c ON c.cycle_id = d.cycle_id
                WHERE {where_sql} AND {facility_where}
                GROUP BY d.demand_id""",
                (*params, *facility_params),
            ).fetchall()

            results = []
            for r in rows:
                fid = r["demand_id"]
                meta = facility_meta.get(fid, {})

                # Build the match-side WHERE clause that respects the sim_day window.
                # Placeholders: (1) inner subquery demand_id, (2) outer m.demand_id,
                # (3) outer c2.sim_day >=, (4) outer c2.sim_day <=.
                match_where = ["m.demand_id = ?"]
                match_params: List[Any] = []  # outer m.demand_id placeholder #2
                if since_sim_day is not None:
                    match_where.append("c2.sim_day >= ?")
                    match_params.append(int(since_sim_day))
                if until_sim_day is not None:
                    match_where.append("c2.sim_day <= ?")
                    match_params.append(int(until_sim_day))
                match_where_sql = " AND ".join(match_where)

                match_row = conn.execute(
                    f"""SELECT COUNT(*) as n_matches,
                              COALESCE(SUM(m.tons), 0) as total_matched,
                              COALESCE(AVG(m.distance_km), 0) as avg_distance,
                              COALESCE(MIN(m.distance_km), 0) as min_distance,
                              COALESCE(MAX(m.distance_km), 0) as max_distance,
                              COALESCE(AVG(m.tons), 0) as avg_tons,
                              MAX(c2.sim_day) as last_sim_day,
                              (SELECT COALESCE(SUM(co2_kg), 0)
                               FROM routes
                               WHERE vehicle_id IS NOT NULL
                                 AND cycle_id IN (
                                   SELECT DISTINCT cycle_id FROM matches
                                   WHERE demand_id = ?
                                 )
                              ) as total_co2
                    FROM matches m
                    JOIN optimization_cycles c2 ON c2.cycle_id = m.cycle_id
                    WHERE {match_where_sql}
                    GROUP BY m.demand_id""",
                    (fid, fid, *match_params),
                ).fetchone()

                # match_row is None if facility has demand rows but no matches
                if match_row is None:
                    n_matches = 0
                    total_matched = 0.0
                    total_co2 = 0.0
                    avg_distance = 0.0
                    min_distance = 0.0
                    max_distance = 0.0
                    last_sim_day = None
                    avg_match_tons = 0.0
                else:
                    n_matches = int(match_row["n_matches"] or 0)
                    total_matched = float(match_row["total_matched"] or 0)
                    total_co2 = float(match_row["total_co2"] or 0)
                    avg_distance = float(match_row["avg_distance"] or 0)
                    min_distance = float(match_row["min_distance"] or 0)
                    max_distance = float(match_row["max_distance"] or 0)
                    last_sim_day = match_row["last_sim_day"]
                    avg_match_tons = float(match_row["avg_tons"] or 0)
                total_required = float(r["total_required"] or 0)
                n_cycles = int(r["n_cycles"] or 0)
                capacity = float(meta.get("processing_capacity_tons_per_day") or 0)

                # Compute metric value
                if metric == "avg_distance":
                    value = round(avg_distance, 2) if n_matches > 0 else None
                elif metric == "min_distance":
                    value = round(min_distance, 2) if n_matches > 0 else None
                elif metric == "max_distance":
                    value = round(max_distance, 2) if n_matches > 0 else None
                elif metric == "total_matched_tons":
                    value = round(total_matched, 2)
                elif metric == "match_count":
                    value = int(n_matches)
                elif metric == "match_rate":
                    value = round(n_matches / n_cycles, 3) if n_cycles > 0 else None
                elif metric == "utilization_pct":
                    if capacity > 0 and total_matched > 0:
                        # n_cycles are the "active days" — capacity is per-day,
                        # so capacity over n_cycles is total available capacity
                        capacity_total = capacity * max(1, n_cycles)
                        value = round(100 * total_matched / capacity_total, 2)
                    else:
                        value = None
                elif metric == "co2_per_ton":
                    value = round(total_co2 / total_matched, 3) if total_matched > 0 else None
                else:
                    value = None

                # Skip facilities where metric is undefined (no matches for distance metrics)
                if value is None:
                    continue

                results.append({
                    "facility_id": fid,
                    "name": meta.get("name"),
                    "city": meta.get("city"),
                    "facility_type": meta.get("facility_type"),
                    "processing_capacity_tons_per_day": (
                        int(capacity) if capacity > 0 else None
                    ),
                    "operator": meta.get("operator"),
                    "source": meta.get("source"),
                    "value": value,
                    "n_matches": n_matches,
                    "n_cycles_with_demand": n_cycles,
                    "total_matched_tons": round(total_matched, 2),
                    "total_required_tons": round(total_required, 2),
                    "avg_match_distance_km": round(avg_distance, 2),
                    "min_match_distance_km": round(min_distance, 2),
                    "max_match_distance_km": round(max_distance, 2),
                    "avg_match_tons": round(avg_match_tons, 2),
                    "utilization_pct": (
                        round(100 * total_matched / (capacity * max(1, n_cycles)), 2)
                        if capacity > 0 and n_cycles > 0 else None
                    ),
                    "last_sim_day": int(last_sim_day) if last_sim_day is not None else None,
                })

        # Sort
        if direction == "lower_is_better":
            results.sort(key=lambda x: x["value"])
        else:
            results.sort(key=lambda x: -x["value"])

        result = {
            "metric": metric,
            "metric_description": desc,
            "direction": direction,
            "filter": {
                "city": city,
                "facility_type": facility_type,
                "material_type": material_type,
                "facility_ids": facility_ids,
                "since_sim_day": int(since_sim_day) if since_sim_day is not None else None,
                "until_sim_day": int(until_sim_day) if until_sim_day is not None else None,
            },
            "n_facilities_evaluated": len(results),
            "n_facilities_returned": min(limit, len(results)),
            "top_facilities": results[:max(1, min(100, limit))],
        }
        # iter #61: store in TTL cache
        self._cache_set(_cache_key, result)
        return result

    def compare_cycles(
        self,
        cycle_id_a: str,
        cycle_id_b: str,
    ) -> Dict[str, Any]:
        """
        iter #59: Side-by-side comparison of two optimization cycles.

        Compares the two cycles on:
        - Cycle metadata (sim_day, sim_hour, wall_timestamp, solver_status, wall_duration_ms)
        - Core KPIs (n_matches, total_tons, total_cost_sek, total_co2_kg,
          total_distance_km, n_vehicles_used, n_vehicles_available,
          fleet_utilization_pct)
        - Derived metrics (cost_per_ton_sek, co2_per_ton_kg, cost_per_km_sek,
          co2_per_km_kg, avg_tons_per_match)
        - Seasonal context (seasonal_factor_avg, seasonal_month, perturbation_count)
        - Differences (b - a for each numeric field, plus pct_change where useful)

        Args:
            cycle_id_a: first cycle_id (e.g. "OPT0001")
            cycle_id_b: second cycle_id (e.g. "OPT0005")

        Returns:
            {
              cycle_a: {cycle_id, ...all KPIs...},
              cycle_b: {cycle_id, ...all KPIs...},
              differences: {
                absolute: {field: b - a, ...},
                pct_change: {field: 100*(b-a)/a, ...}
              },
              winner: {
                lower_is_better: {cycle_id, by},
                higher_is_better: {cycle_id, by}
              } | None,
            }

        Use cases:
        - Compare greenest cycle vs worst cycle
        - A/B test solver changes (same input, different solver config)
        - Before/after perturbation impact analysis
        - Compare seasonal cycles (winter vs summer)
        """
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT cycle_id, sim_day, sim_hour, wall_timestamp,
                          activity_factor,
                          n_supply_offers, n_demand_requests, n_matches,
                          total_tons, total_cost_sek, total_co2_kg,
                          total_distance_km, n_vehicles_used, n_vehicles_available,
                          fleet_utilization_pct, solver_status, wall_duration_ms,
                          seasonal_factor_avg, base_seasonal_factor_avg,
                          seasonal_month, perturbation_count,
                          perturbation_total_multiplier
                FROM optimization_cycles
                WHERE cycle_id IN (?, ?)""",
                (cycle_id_a, cycle_id_b),
            ).fetchall()

        by_id = {r["cycle_id"]: dict(r) for r in rows}
        # If only one or zero cycles found, return partial with nulls
        a = by_id.get(cycle_id_a)
        b = by_id.get(cycle_id_b)

        def _serialize(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
            if row is None:
                return None
            total_tons = float(row.get("total_tons") or 0)
            total_cost = float(row.get("total_cost_sek") or 0)
            total_co2 = float(row.get("total_co2_kg") or 0)
            total_dist = float(row.get("total_distance_km") or 0)
            n_matches = int(row.get("n_matches") or 0)
            n_veh_used = int(row.get("n_vehicles_used") or 0)
            return {
                "cycle_id": row.get("cycle_id"),
                "sim_day": int(row["sim_day"]) if row.get("sim_day") is not None else None,
                "sim_hour": int(row["sim_hour"]) if row.get("sim_hour") is not None else None,
                "wall_timestamp": row.get("wall_timestamp"),
                "activity_factor": row.get("activity_factor"),
                "solver_status": row.get("solver_status"),
                "wall_duration_ms": row.get("wall_duration_ms"),
                # Core KPIs
                "n_supply_offers": int(row.get("n_supply_offers") or 0),
                "n_demand_requests": int(row.get("n_demand_requests") or 0),
                "n_matches": n_matches,
                "total_tons": round(total_tons, 2),
                "total_cost_sek": round(total_cost, 2),
                "total_co2_kg": round(total_co2, 3),
                "total_distance_km": round(total_dist, 2),
                "n_vehicles_used": n_veh_used,
                "n_vehicles_available": int(row.get("n_vehicles_available") or 0),
                "fleet_utilization_pct": round(float(row.get("fleet_utilization_pct") or 0), 2),
                # Derived
                "cost_per_ton_sek": round(total_cost / total_tons, 2) if total_tons > 0 else None,
                "co2_per_ton_kg": round(total_co2 / total_tons, 3) if total_tons > 0 else None,
                "cost_per_km_sek": round(total_cost / total_dist, 3) if total_dist > 0 else None,
                "co2_per_km_kg": round(total_co2 / total_dist, 3) if total_dist > 0 else None,
                "avg_tons_per_match": round(total_tons / n_matches, 2) if n_matches > 0 else None,
                # Seasonal
                "seasonal_factor_avg": row.get("seasonal_factor_avg"),
                "base_seasonal_factor_avg": row.get("base_seasonal_factor_avg"),
                "seasonal_month": row.get("seasonal_month"),
                "perturbation_count": int(row.get("perturbation_count") or 0),
                "perturbation_total_multiplier": row.get("perturbation_total_multiplier"),
            }

        sa = _serialize(a)
        sb = _serialize(b)

        differences: Dict[str, Any] = {"absolute": {}, "pct_change": {}}
        winner: Optional[Dict[str, Any]] = None
        if sa is not None and sb is not None:
            # Compute absolute differences (b - a) for numeric fields
            numeric_fields = (
                "n_supply_offers", "n_demand_requests", "n_matches",
                "total_tons", "total_cost_sek", "total_co2_kg", "total_distance_km",
                "n_vehicles_used", "n_vehicles_available", "fleet_utilization_pct",
                "cost_per_ton_sek", "co2_per_ton_kg", "cost_per_km_sek",
                "co2_per_km_kg", "avg_tons_per_match",
            )
            for f in numeric_fields:
                va = sa.get(f)
                vb = sb.get(f)
                if va is None or vb is None:
                    continue
                abs_diff = round(vb - va, 3)
                differences["absolute"][f] = abs_diff
                if va != 0:
                    pct = round(100 * (vb - va) / abs(va), 2)
                    differences["pct_change"][f] = pct

            # Determine winner on three key axes
            winner_axes = {
                "lowest_co2_per_ton_kg": ("co2_per_ton_kg", "lower_is_better"),
                "lowest_cost_per_ton_sek": ("cost_per_ton_sek", "lower_is_better"),
                "highest_fleet_utilization_pct": ("fleet_utilization_pct", "higher_is_better"),
                "most_matches": ("n_matches", "higher_is_better"),
                "most_tons": ("total_tons", "higher_is_better"),
            }
            winner = {}
            for axis, (field, direction) in winner_axes.items():
                va = sa.get(field)
                vb = sb.get(field)
                if va is None or vb is None:
                    winner[axis] = None
                    continue
                if direction == "lower_is_better":
                    win_id = cycle_id_a if va <= vb else cycle_id_b
                else:
                    win_id = cycle_id_a if va >= vb else cycle_id_b
                winning_val = min(va, vb) if direction == "lower_is_better" else max(va, vb)
                losing_val = max(va, vb) if direction == "lower_is_better" else min(va, vb)
                by_abs = round(winning_val - losing_val, 3)
                by_pct = (
                    round(100 * abs(va - vb) / abs(losing_val), 2)
                    if losing_val != 0 else None
                )
                winner[axis] = {
                    "cycle_id": win_id,
                    "direction": direction,
                    "by_abs": by_abs,
                    "by_pct": by_pct,
                    "a_value": va,
                    "b_value": vb,
                }

        return {
            "cycle_a": sa,
            "cycle_b": sb,
            "differences": differences,
            "winner": winner,
        }

    def compare_materials(
        self,
        material_a: str,
        material_b: str,
        since_sim_day: Optional[int] = None,
        until_sim_day: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        iter #64: Side-by-side comparison of two materials (iter #63 top-metrics).

        Compares the two materials on:
        - Volume metrics (total_matched_tons / total_supply_tons / total_demand_tons)
        - Activity metrics (n_matches / n_supply_offers / n_demand_requests / match_rate)
        - Efficiency metrics (avg_tons_per_match / avg_distance_km)
        - Sustainability metrics (total_co2_kg / co2_per_ton / total_cost_sek / cost_per_ton)
        - Demand context (demand_fulfillment_pct)
        - Differences (absolute + pct_change)
        - Winner on 5 axes (cleanest per ton, cheapest per ton, most matches,
          highest match rate, highest demand fulfillment)

        Args:
            material_a: first material_type (e.g. "concrete")
            material_b: second material_type (e.g. "metal_scrap")
            since_sim_day: optional lower bound (inclusive)
            until_sim_day: optional upper bound (inclusive)

        Returns:
          {
            material_a: {material_type, ...all KPIs...} | None,
            material_b: {material_type, ...all KPIs...} | None,
            differences: {
              absolute: {field: b - a, ...},
              pct_change: {field: 100*(b-a)/abs(a), ...}
            },
            winner: {
              axis: {material, direction, by_abs, by_pct, a_value, b_value}
            } | None,
          }

        Use cases:
        - Compare dominant material (concrete) vs rare material (metal)
        - A/B greenness comparison (lowest co2_per_ton)
        - Demand-side comparison (best fulfillment)
        - Identify bottleneck materials (low match_rate)
        """
        # iter #64: TTL cache check
        _cache_key = self._cache_key(
            "compare_materials",
            {"material_a": material_a, "material_b": material_b,
             "since_sim_day": since_sim_day, "until_sim_day": until_sim_day},
        )
        _cached = self._cache_get(_cache_key)
        if _cached is not None:
            return _cached

        def _material_data(mat: str) -> Optional[Dict[str, Any]]:
            """Compute per-material aggregate dict (same shape as
            get_top_materials_by_volume row)."""
            with self._conn() as conn:
                where_clauses = ["m.material_type = ?"]
                params: List[Any] = [mat]
                if since_sim_day is not None:
                    where_clauses.append("c.sim_day >= ?")
                    params.append(int(since_sim_day))
                if until_sim_day is not None:
                    where_clauses.append("c.sim_day <= ?")
                    params.append(int(until_sim_day))
                where_sql = " AND ".join(where_clauses)

                # Match-based: n_matches, total_matched, avg_tons, avg_distance, total_profit
                m_row = conn.execute(
                    f"""SELECT COUNT(*) as n_matches,
                              COALESCE(SUM(m.tons), 0) as total_matched,
                              COALESCE(AVG(m.tons), 0) as avg_tons,
                              COALESCE(AVG(m.distance_km), 0) as avg_distance,
                              COALESCE(SUM(m.distance_km), 0) as total_distance,
                              COALESCE(SUM(m.estimated_profit_sek), 0) as total_profit
                    FROM matches m
                    JOIN optimization_cycles c ON c.cycle_id = m.cycle_id
                    WHERE {where_sql}""",
                    params,
                ).fetchone()

                # Supply-based
                s_row = conn.execute(
                    f"""SELECT COUNT(DISTINCT s.supply_id) as n_offers,
                              COALESCE(SUM(s.available_tons), 0) as total_supply,
                              COUNT(DISTINCT s.cycle_id) as cycles_with_supply
                    FROM supply_offers s
                    JOIN optimization_cycles c ON c.cycle_id = s.cycle_id
                    WHERE s.material_type = ?
                      {"AND c.sim_day >= ?" if since_sim_day is not None else ""}
                      {"AND c.sim_day <= ?" if until_sim_day is not None else ""}
                    """,
                    tuple([mat]
                          + ([int(since_sim_day)] if since_sim_day is not None else [])
                          + ([int(until_sim_day)] if until_sim_day is not None else [])),
                ).fetchone()

                # Demand-based
                d_row = conn.execute(
                    f"""SELECT COUNT(DISTINCT d.demand_id) as n_demands,
                              COALESCE(SUM(d.required_tons), 0) as total_demand,
                              COUNT(DISTINCT d.cycle_id) as cycles_with_demand
                    FROM demand_requests d
                    JOIN optimization_cycles c ON c.cycle_id = d.cycle_id
                    WHERE d.material_type = ?
                      {"AND c.sim_day >= ?" if since_sim_day is not None else ""}
                      {"AND c.sim_day <= ?" if until_sim_day is not None else ""}
                    """,
                    tuple([mat]
                          + ([int(since_sim_day)] if since_sim_day is not None else [])
                          + ([int(until_sim_day)] if until_sim_day is not None else [])),
                ).fetchone()

                # Route-based co2/cost allocation per material
                cycle_totals = conn.execute(
                    f"""SELECT m.cycle_id, SUM(m.tons) as cycle_mat_tons
                    FROM matches m
                    JOIN optimization_cycles c ON c.cycle_id = m.cycle_id
                    WHERE {where_sql}
                    GROUP BY m.cycle_id""",
                    params,
                ).fetchall()
                cycle_total_dict = {
                    r["cycle_id"]: float(r["cycle_mat_tons"] or 0)
                    for r in cycle_totals
                }
                # Build a window-only where clause for global cycle totals + routes
                win_where_clauses: List[str] = ["1=1"]
                win_params: List[Any] = []
                if since_sim_day is not None:
                    win_where_clauses.append("c.sim_day >= ?")
                    win_params.append(int(since_sim_day))
                if until_sim_day is not None:
                    win_where_clauses.append("c.sim_day <= ?")
                    win_params.append(int(until_sim_day))
                win_where_sql = " AND ".join(win_where_clauses)
                # Get global per-cycle total for proportional allocation
                cycle_global_totals = conn.execute(
                    f"""SELECT m.cycle_id, SUM(m.tons) as cycle_total
                    FROM matches m
                    JOIN optimization_cycles c ON c.cycle_id = m.cycle_id
                    WHERE {win_where_sql}
                    GROUP BY m.cycle_id""",
                    win_params,
                ).fetchall()
                cycle_global_dict = {
                    r["cycle_id"]: float(r["cycle_total"] or 0)
                    for r in cycle_global_totals
                }
                # Sum route co2/cost per cycle in this window
                cycle_route_rows = conn.execute(
                    f"""SELECT r.cycle_id,
                              COALESCE(SUM(r.co2_kg), 0) as total_co2,
                              COALESCE(SUM(r.cost_sek), 0) as total_cost
                    FROM routes r
                    JOIN optimization_cycles c ON c.cycle_id = r.cycle_id
                    WHERE {win_where_sql}
                    GROUP BY r.cycle_id""",
                    win_params,
                ).fetchall()
                cycle_route_dict = {
                    r["cycle_id"]: (float(r["total_co2"]), float(r["total_cost"]))
                    for r in cycle_route_rows
                }

            if m_row is None and s_row is None and d_row is None:
                return None

            n_matches = int(m_row["n_matches"] if m_row else 0)
            total_matched = float(m_row["total_matched"] if m_row else 0)
            avg_tons = float(m_row["avg_tons"] if m_row else 0)
            avg_distance = float(m_row["avg_distance"] if m_row else 0)
            total_profit = float(m_row["total_profit"] if m_row else 0)
            n_offers = int(s_row["n_offers"] if s_row else 0)
            total_supply = float(s_row["total_supply"] if s_row else 0)
            n_demands = int(d_row["n_demands"] if d_row else 0)
            total_demand = float(d_row["total_demand"] if d_row else 0)

            # Allocate route co2/cost by match.tons proportion
            mat_co2 = 0.0
            mat_cost = 0.0
            for cid, mat_tons in cycle_total_dict.items():
                cycle_total = cycle_global_dict.get(cid, 0)
                if cycle_total > 0 and mat_tons > 0:
                    frac = mat_tons / cycle_total
                    rco2, rcost = cycle_route_dict.get(cid, (0.0, 0.0))
                    mat_co2 += rco2 * frac
                    mat_cost += rcost * frac

            match_rate = round(n_matches / n_offers, 4) if n_offers > 0 else None
            co2_per_ton = round(mat_co2 / total_matched, 4) if total_matched > 0 else None
            cost_per_ton = round(mat_cost / total_matched, 4) if total_matched > 0 else None
            demand_fulfill = round(100 * total_matched / total_demand, 2) if total_demand > 0 else None

            return {
                "material_type": mat,
                "n_matches": n_matches,
                "n_supply_offers": n_offers,
                "n_demand_requests": n_demands,
                "match_rate": match_rate,
                "total_matched_tons": round(total_matched, 2),
                "total_supply_tons": round(total_supply, 2),
                "total_demand_tons": round(total_demand, 2),
                "avg_tons_per_match": round(avg_tons, 3),
                "avg_distance_km": round(avg_distance, 2),
                "total_co2_kg": round(mat_co2, 2),
                "total_cost_sek": round(mat_cost, 2),
                "co2_per_ton": co2_per_ton,
                "cost_per_ton": cost_per_ton,
                "total_profit_sek": round(total_profit, 2),
                "demand_fulfillment_pct": demand_fulfill,
            }

        sa = _material_data(material_a)
        sb = _material_data(material_b)

        differences: Dict[str, Any] = {"absolute": {}, "pct_change": {}}
        winner: Optional[Dict[str, Any]] = None
        if sa is not None and sb is not None:
            numeric_fields = (
                "n_matches", "n_supply_offers", "n_demand_requests",
                "match_rate",
                "total_matched_tons", "total_supply_tons", "total_demand_tons",
                "avg_tons_per_match", "avg_distance_km",
                "total_co2_kg", "total_cost_sek",
                "co2_per_ton", "cost_per_ton",
                "total_profit_sek", "demand_fulfillment_pct",
            )
            for f in numeric_fields:
                va = sa.get(f)
                vb = sb.get(f)
                if va is None or vb is None:
                    continue
                abs_diff = round(vb - va, 3)
                differences["absolute"][f] = abs_diff
                if va != 0:
                    pct = round(100 * (vb - va) / abs(va), 2)
                    differences["pct_change"][f] = pct

            winner_axes = {
                "lowest_co2_per_ton": ("co2_per_ton", "lower_is_better"),
                "lowest_cost_per_ton": ("cost_per_ton", "lower_is_better"),
                "most_matches": ("n_matches", "higher_is_better"),
                "highest_match_rate": ("match_rate", "higher_is_better"),
                "highest_demand_fulfillment": ("demand_fulfillment_pct", "higher_is_better"),
            }
            winner = {}
            for axis, (field, direction) in winner_axes.items():
                va = sa.get(field)
                vb = sb.get(field)
                if va is None or vb is None:
                    winner[axis] = None
                    continue
                if direction == "lower_is_better":
                    win_mat = material_a if va <= vb else material_b
                    winning_val = min(va, vb)
                    losing_val = max(va, vb)
                else:
                    win_mat = material_a if va >= vb else material_b
                    winning_val = max(va, vb)
                    losing_val = min(va, vb)
                by_abs = round(winning_val - losing_val, 3)
                by_pct = (
                    round(100 * abs(va - vb) / abs(losing_val), 2)
                    if losing_val != 0 else None
                )
                winner[axis] = {
                    "material": win_mat,
                    "direction": direction,
                    "by_abs": by_abs,
                    "by_pct": by_pct,
                    "a_value": va,
                    "b_value": vb,
                }

        result = {
            "material_a": sa,
            "material_b": sb,
            "differences": differences,
            "winner": winner,
            "filter": {
                "since_sim_day": since_sim_day,
                "until_sim_day": until_sim_day,
            },
        }
        self._cache_set(_cache_key, result)
        return result

    def compare_routes(
        self,
        route_id_a: int,
        route_id_b: int,
    ) -> Dict[str, Any]:
        """
        iter #64: Side-by-side comparison of two specific route rows.

        Each route row is identified by its primary key route_id (the same
        id surfaced in /api/persistence/top-routes). Compares the two on:
        - Route identity (cycle_id, vehicle_id, sim_day, sim_hour, n_stops)
        - Volume metrics (distance_km, duration_hours, cycle_tons)
        - Cost metrics (cost_sek, cost_per_km, cost_per_hour)
        - Sustainability metrics (co2_kg, co2_per_km, co2_per_hour)
        - Speed proxy (speed_km_per_hour = distance_km / duration_hours)
        - Differences (absolute + pct_change)
        - Winner on 5 axes (lowest CO₂/km, lowest CO₂/hour, lowest cost/km,
          highest speed, lowest duration)

        Args:
            route_id_a: first route's primary key (route.id from top-routes)
            route_id_b: second route's primary key

        Returns:
          {
            route_a: {route_id, ...all KPIs...} | None,
            route_b: {route_id, ...all KPIs...} | None,
            differences: {absolute, pct_change},
            winner: {axis: {route, direction, by_abs, by_pct, a_value, b_value}},
          }

        Use cases:
        - Compare greenest route vs dirtiest (co2_per_km leaderboard)
        - Compare most-loaded route (cycle_tons) vs least-loaded
        - Compare same-vehicle across 2 cycles
        """
        _cache_key = self._cache_key(
            "compare_routes",
            {"route_id_a": route_id_a, "route_id_b": route_id_b},
        )
        _cached = self._cache_get(_cache_key)
        if _cached is not None:
            return _cached

        def _route_data(rid: int) -> Optional[Dict[str, Any]]:
            with self._conn() as conn:
                row = conn.execute(
                    """SELECT r.id as route_id, r.cycle_id, r.vehicle_id,
                              r.distance_km, r.duration_hours, r.cost_sek,
                              r.co2_kg, r.stops_json,
                              c.sim_day, c.sim_hour,
                              (SELECT COALESCE(SUM(m.tons), 0)
                               FROM matches m WHERE m.cycle_id = r.cycle_id) as cycle_tons
                    FROM routes r
                    JOIN optimization_cycles c ON c.cycle_id = r.cycle_id
                    WHERE r.id = ?""",
                    (rid,),
                ).fetchone()
            if row is None:
                return None
            distance = float(row["distance_km"] or 0)
            duration = float(row["duration_hours"] or 0)
            cost = float(row["cost_sek"] or 0)
            co2 = float(row["co2_kg"] or 0)
            cycle_tons = float(row["cycle_tons"] or 0)

            stops_json = row["stops_json"] or "[]"
            try:
                stops_list = json.loads(stops_json) if isinstance(stops_json, str) else stops_json
                n_stops = sum(1 for s in stops_list if s)
            except Exception:
                n_stops = 0

            return {
                "route_id": int(row["route_id"]),
                "cycle_id": row["cycle_id"],
                "vehicle_id": row["vehicle_id"],
                "sim_day": row["sim_day"],
                "sim_hour": row["sim_hour"],
                "n_stops": n_stops,
                "distance_km": round(distance, 2),
                "duration_hours": round(duration, 3),
                "cost_sek": round(cost, 2),
                "co2_kg": round(co2, 2),
                "cycle_tons": round(cycle_tons, 2),
                "co2_per_km": round(co2 / distance, 4) if distance > 0 else None,
                "co2_per_hour": round(co2 / duration, 4) if duration > 0 else None,
                "cost_per_km": round(cost / distance, 4) if distance > 0 else None,
                "cost_per_hour": round(cost / duration, 4) if duration > 0 else None,
                "speed_km_per_hour": round(distance / duration, 2) if duration > 0 else None,
            }

        sa = _route_data(route_id_a)
        sb = _route_data(route_id_b)

        differences: Dict[str, Any] = {"absolute": {}, "pct_change": {}}
        winner: Optional[Dict[str, Any]] = None
        if sa is not None and sb is not None:
            numeric_fields = (
                "n_stops", "distance_km", "duration_hours",
                "cost_sek", "co2_kg", "cycle_tons",
                "co2_per_km", "co2_per_hour", "cost_per_km", "cost_per_hour",
                "speed_km_per_hour",
            )
            for f in numeric_fields:
                va = sa.get(f)
                vb = sb.get(f)
                if va is None or vb is None:
                    continue
                abs_diff = round(vb - va, 3)
                differences["absolute"][f] = abs_diff
                if va != 0:
                    pct = round(100 * (vb - va) / abs(va), 2)
                    differences["pct_change"][f] = pct

            winner_axes = {
                "lowest_co2_per_km": ("co2_per_km", "lower_is_better"),
                "lowest_co2_per_hour": ("co2_per_hour", "lower_is_better"),
                "lowest_cost_per_km": ("cost_per_km", "lower_is_better"),
                "highest_speed": ("speed_km_per_hour", "higher_is_better"),
                "lowest_duration": ("duration_hours", "lower_is_better"),
            }
            winner = {}
            for axis, (field, direction) in winner_axes.items():
                va = sa.get(field)
                vb = sb.get(field)
                if va is None or vb is None:
                    winner[axis] = None
                    continue
                if direction == "lower_is_better":
                    win_rid = route_id_a if va <= vb else route_id_b
                    winning_val = min(va, vb)
                    losing_val = max(va, vb)
                else:
                    win_rid = route_id_a if va >= vb else route_id_b
                    winning_val = max(va, vb)
                    losing_val = min(va, vb)
                by_abs = round(winning_val - losing_val, 3)
                by_pct = (
                    round(100 * abs(va - vb) / abs(losing_val), 2)
                    if losing_val != 0 else None
                )
                winner[axis] = {
                    "route_id": win_rid,
                    "direction": direction,
                    "by_abs": by_abs,
                    "by_pct": by_pct,
                    "a_value": va,
                    "b_value": vb,
                }

        result = {
            "route_a": sa,
            "route_b": sb,
            "differences": differences,
            "winner": winner,
        }
        self._cache_set(_cache_key, result)
        return result

    def cycle_trend_comparison(
        self,
        early_window: int = 5,
        late_window: int = 5,
        metric: str = "co2_per_ton",
        since_sim_day: Optional[int] = None,
        until_sim_day: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        iter #65: Compare early-window vs late-window cycles by metric.

        Aggregates the first N cycles (oldest by sim_day) vs the last N
        cycles (newest by sim_day) and compares their average value of the
        chosen metric. Useful for visualizing how a system metric evolves
        over the simulation horizon.

        Args:
            early_window: number of cycles from the start of window (default 5)
            late_window: number of cycles from the end of window (default 5)
            metric: which efficiency metric to compare. One of:
                - 'co2_per_ton' (kg CO2 / matched ton, lower = better)
                - 'cost_per_ton' (SEK / matched ton, lower = better)
                - 'co2_per_km' (kg CO2 / km, lower = better)
                - 'cost_per_km' (SEK / km, lower = better)
                - 'fleet_utilization_pct' (avg util %, higher = better)
                - 'match_rate_vs_offers' (matches / offers, higher = better)
                - 'tons_per_cycle' (avg matched tons, higher = better)
            since_sim_day: optional lower bound (inclusive)
            until_sim_day: optional upper bound (inclusive)

        Returns:
          {
            metric, metric_description, direction,
            window: {since_sim_day, until_sim_day},
            early: {n_cycles, cycle_ids, mean_value, min_value, max_value,
                    median_value, stddev_value},
            late: {n_cycles, cycle_ids, mean_value, min_value, max_value,
                   median_value, stddev_value},
            delta: {absolute (late - early), pct_change},
            trend: 'improving' / 'declining' / 'stable',
          }

        Use cases:
        - Show that fleet is becoming greener over time (co2_per_ton drops)
        - Show utilization is rising
        - Identify whether optimization is paying off vs baseline
        """
        _cache_key = self._cache_key(
            "cycle_trend_comparison",
            {"early_window": early_window, "late_window": late_window,
             "metric": metric,
             "since_sim_day": since_sim_day, "until_sim_day": until_sim_day},
        )
        _cached = self._cache_get(_cache_key)
        if _cached is not None:
            return _cached

        valid_metrics = {
            "co2_per_ton": ("lower_is_better", "kg CO2 / matched ton"),
            "cost_per_ton": ("lower_is_better", "SEK / matched ton"),
            "co2_per_km": ("lower_is_better", "kg CO2 / km"),
            "cost_per_km": ("lower_is_better", "SEK / km"),
            "fleet_utilization_pct": ("higher_is_better", "avg util %"),
            "match_rate_vs_offers": ("higher_is_better", "matches / offers"),
            "tons_per_cycle": ("higher_is_better", "avg matched tons"),
        }
        if metric not in valid_metrics:
            raise ValueError(
                f"Unknown metric {metric!r}. Valid: {list(valid_metrics)}"
            )
        direction, desc = valid_metrics[metric]

        where_clauses: List[str] = ["1=1"]
        params: List[Any] = []
        if since_sim_day is not None:
            where_clauses.append("oc.sim_day >= ?")
            params.append(int(since_sim_day))
        if until_sim_day is not None:
            where_clauses.append("oc.sim_day <= ?")
            params.append(int(until_sim_day))
        where_sql = " AND ".join(where_clauses)

        # Compute per-cycle metric value via aggregation
        # For ton-based: needs sum of match.tons per cycle
        # For km-based: needs sum of routes.distance_km per cycle
        metric_sql_fragment = {
            "co2_per_ton": "oc.total_co2_kg / NULLIF(oc.total_tons, 0)",
            "cost_per_ton": "oc.total_cost_sek / NULLIF(oc.total_tons, 0)",
            "co2_per_km": "oc.total_co2_kg / NULLIF(oc.total_distance_km, 0)",
            "cost_per_km": "oc.total_cost_sek / NULLIF(oc.total_distance_km, 0)",
            "fleet_utilization_pct": "oc.fleet_utilization_pct",
            "match_rate_vs_offers": "oc.n_matches / NULLIF(oc.n_supply_offers, 0)",
            "tons_per_cycle": "oc.total_tons",
        }[metric]

        with self._conn() as conn:
            rows = conn.execute(
                f"""SELECT oc.cycle_id, oc.sim_day, oc.sim_hour,
                          oc.n_matches, oc.n_supply_offers, oc.total_tons,
                          oc.total_cost_sek, oc.total_co2_kg, oc.total_distance_km,
                          oc.fleet_utilization_pct,
                          {metric_sql_fragment} as metric_value
                FROM optimization_cycles oc
                WHERE {where_sql}
                  AND oc.n_matches > 0
                ORDER BY oc.sim_day ASC, oc.sim_hour ASC, oc.cycle_id ASC""",
                params,
            ).fetchall()

        def _stats(rows_subset):
            n = len(rows_subset)
            vals = [r["metric_value"] for r in rows_subset if r["metric_value"] is not None]
            if not vals:
                return {
                    "n_cycles": n, "cycle_ids": [],
                    "mean_value": None, "min_value": None,
                    "max_value": None, "median_value": None,
                    "stddev_value": None,
                }
            sorted_vals = sorted(vals)
            mean = sum(vals) / len(vals)
            variance = sum((v - mean) ** 2 for v in vals) / len(vals)
            stddev = variance ** 0.5
            if len(vals) == 1:
                median = vals[0]
            else:
                mid_idx = (len(vals) - 1) / 2
                lo_idx = int(mid_idx)
                hi_idx = min(lo_idx + 1, len(vals) - 1)
                frac = mid_idx - lo_idx
                median = sorted_vals[lo_idx] * (1 - frac) + sorted_vals[hi_idx] * frac
            return {
                "n_cycles": len(vals),
                "cycle_ids": [r["cycle_id"] for r in rows_subset if r["metric_value"] is not None],
                "mean_value": round(mean, 4),
                "min_value": round(min(vals), 4),
                "max_value": round(max(vals), 4),
                "median_value": round(median, 4),
                "stddev_value": round(stddev, 4),
            }

        early_rows = rows[:early_window]
        late_rows = rows[-late_window:] if late_window > 0 else []
        early_stats = _stats(early_rows)
        late_stats = _stats(late_rows)

        # Compute delta + trend
        delta_dict: Dict[str, Any] = {"absolute": None, "pct_change": None}
        trend = "stable"
        if (early_stats["mean_value"] is not None and
                late_stats["mean_value"] is not None):
            abs_diff = round(late_stats["mean_value"] - early_stats["mean_value"], 4)
            delta_dict["absolute"] = abs_diff
            if early_stats["mean_value"] != 0:
                pct = round(
                    100 * (late_stats["mean_value"] - early_stats["mean_value"])
                    / abs(early_stats["mean_value"]), 2)
                delta_dict["pct_change"] = pct
                # Trend: > 5% threshold
                if direction == "lower_is_better":
                    if pct < -5:
                        trend = "improving"
                    elif pct > 5:
                        trend = "declining"
                    else:
                        trend = "stable"
                else:
                    if pct > 5:
                        trend = "improving"
                    elif pct < -5:
                        trend = "declining"
                    else:
                        trend = "stable"

        result = {
            "metric": metric,
            "metric_description": desc,
            "direction": direction,
            "window": {"since_sim_day": since_sim_day,
                       "until_sim_day": until_sim_day},
            "early": early_stats,
            "late": late_stats,
            "delta": delta_dict,
            "trend": trend,
        }
        self._cache_set(_cache_key, result)
        return result
