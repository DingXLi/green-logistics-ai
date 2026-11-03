"""周期写入与周期级查询（begin/commit cycle、周期明细）。"""

from typing import Any, Dict, List, Optional
from datetime import datetime
import json

from ._base import PersistenceBase
from ._contract import SharedQueryMixin


class CycleMixin(PersistenceBase, SharedQueryMixin):
    """CycleMixin —— 由 agents/persistence.py 拆分而来（iter #77）。"""

    def begin_cycle(
        self,
        cycle_id: str,
        sim_day: int,
        sim_hour: int,
        activity_factor: float,
        n_supply_offers: int = 0,
        n_demand_requests: int = 0,
        seasonal_factor_avg: float = 1.0,
        seasonal_month: int = 1,
        base_seasonal_factor_avg: float = 1.0,
        perturbation_count: int = 0,
        perturbation_total_multiplier: float = 1.0,
    ) -> None:
        """开始一个新周期（先写一行 cycle 记录）

        Args:
            seasonal_factor_avg: 本 cycle 所有 supply 点的 effective seasonal_multiplier
                                 平均值 (e.g. 1.4 if summer peak + perturbation)
            seasonal_month: 1-12 对应该 cycle 的月份
            base_seasonal_factor_avg (iter #38): 同 cycle 所有 supply 点的 baseline
                                  (无扰动) 平均值, 用于分析 perturbation effect
            perturbation_count (iter #38): 该 cycle 命中几个 perturbation rule
            perturbation_total_multiplier (iter #38): 该 cycle 所有 perturbation
                                  multiplier 之乘积 (e.g. 0.7 * 1.2 = 0.84)
        """
        with self._conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO optimization_cycles
                   (cycle_id, sim_day, sim_hour, activity_factor,
                    wall_timestamp, n_supply_offers, n_demand_requests,
                    seasonal_factor_avg, seasonal_month,
                    base_seasonal_factor_avg, perturbation_count,
                    perturbation_total_multiplier)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (cycle_id, sim_day, sim_hour, activity_factor,
                 datetime.now().isoformat(), n_supply_offers, n_demand_requests,
                 seasonal_factor_avg, seasonal_month,
                 base_seasonal_factor_avg, perturbation_count,
                 perturbation_total_multiplier)
            )

    def commit_cycle(
        self,
        cycle_id: str,
        kpi: Dict[str, Any],
        wall_duration_ms: int = 0,
    ) -> None:
        """周期结束时更新 KPI"""
        with self._conn() as conn:
            conn.execute(
                """UPDATE optimization_cycles SET
                   n_matches = ?,
                   total_tons = ?,
                   total_cost_sek = ?,
                   total_co2_kg = ?,
                   total_distance_km = ?,
                   n_vehicles_used = ?,
                   n_vehicles_available = ?,
                   fleet_utilization_pct = ?,
                   solver_status = ?,
                   wall_duration_ms = ?
                   WHERE cycle_id = ?""",
                (
                    kpi.get("n_matches", 0),
                    kpi.get("total_tons", 0),
                    kpi.get("total_cost_sek", 0),
                    kpi.get("total_co2_kg", 0),
                    kpi.get("total_distance_km", 0),
                    kpi.get("n_vehicles_used", 0),
                    kpi.get("n_vehicles_available", 0),
                    kpi.get("fleet_utilization_pct", 0),
                    kpi.get("solver_status", "unknown"),
                    wall_duration_ms,
                    cycle_id,
                )
            )

    def record_supply(self, cycle_id: str, supply: Dict[str, Any]) -> None:
        with self._conn() as conn:
            loc = supply.get("location", {})
            conn.execute(
                """INSERT INTO supply_offers
                   (cycle_id, supply_id, location_lat, location_lon,
                    material_type, available_tons, moisture_percent, quality_score,
                    base_seasonal_multiplier, seasonal_multiplier, perturbation_applied)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    cycle_id,
                    supply.get("agent_id") or supply.get("supply_id"),
                    loc.get("lat"),
                    loc.get("lon"),
                    supply.get("material_type"),
                    supply.get("available_tons", supply.get("weight_tons", 0)),
                    supply.get("moisture_percent"),
                    supply.get("quality_score"),
                    float(supply.get("base_seasonal_multiplier", 1.0)),
                    float(supply.get("seasonal_multiplier", 1.0)),
                    int(bool(supply.get("perturbation_applied", False))),
                )
            )

    def record_demand(self, cycle_id: str, demand: Dict[str, Any]) -> None:
        with self._conn() as conn:
            loc = demand.get("location", {})
            conn.execute(
                """INSERT INTO demand_requests
                   (cycle_id, demand_id, name, location_lat, location_lon,
                    material_type, required_tons, priority, deadline)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    cycle_id,
                    demand.get("id") or demand.get("demand_id"),
                    demand.get("name"),
                    loc.get("lat"),
                    loc.get("lon"),
                    demand.get("material_type") or demand.get("preferred_material"),
                    demand.get("required_tons") or demand.get("demand_tons") or demand.get("current_demand_tons"),
                    demand.get("priority"),
                    demand.get("deadline"),
                )
            )

    def record_match(self, cycle_id: str, match: Dict[str, Any]) -> None:
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO matches
                   (cycle_id, supply_id, demand_id, material_type,
                    tons, distance_km, estimated_profit_sek)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    cycle_id,
                    match.get("supply_id"),
                    match.get("demand_id"),
                    match.get("material_type"),
                    match.get("tons", 0),
                    match.get("distance_km", 0),
                    match.get("estimated_profit_sek", 0),
                )
            )

    def record_route(self, cycle_id: str, route: Dict[str, Any]) -> None:
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO routes
                   (cycle_id, vehicle_id, stops_json, distance_km,
                    duration_hours, cost_sek, co2_kg)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    cycle_id,
                    route.get("vehicle_id"),
                    json.dumps(route.get("stops", []), ensure_ascii=False),
                    route.get("distance_km", 0),
                    route.get("duration_hours", 0),
                    route.get("cost_sek", 0),
                    route.get("co2_kg", 0),
                )
            )

    def get_recent_cycles(self, limit: int = 10) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT * FROM optimization_cycles
                   ORDER BY id DESC LIMIT ?""",
                (limit,)
            ).fetchall()
            return [dict(r) for r in rows]

    def get_cycle_history(self, limit: int = 50, sim_day_min: Optional[int] = None,
                          sim_day_max: Optional[int] = None,
                          has_matches_only: bool = False) -> List[Dict[str, Any]]:
        """
        Cycle history list (iter #11) — 列出过往 optimization cycles。

        每个 cycle 含 KPI 摘要 + match/route counts (joins sub-tables),
        供 dashboard 的 "Cycle History" 表格展示。

        Args:
            limit: 最多返回多少条 (默认 50)
            sim_day_min: 最小的 sim_day (可选过滤)
            sim_day_max: 最大的 sim_day (可选过滤)
            has_matches_only: True → 仅返回 n_matches > 0 的 cycle

        返回:
            [{cycle_id, sim_day, sim_hour, wall_timestamp,
              activity_factor, n_supply_offers, n_demand_requests, n_matches,
              total_tons, total_cost_sek, total_co2_kg, total_distance_km,
              n_vehicles_used, n_vehicles_available, fleet_utilization_pct,
              solver_status, wall_duration_ms, seasonal_factor_avg,
              seasonal_month, n_routes}, ...]
        """
        where_clauses = []
        params: List[Any] = []
        if sim_day_min is not None:
            where_clauses.append("oc.sim_day >= ?")
            params.append(sim_day_min)
        if sim_day_max is not None:
            where_clauses.append("oc.sim_day <= ?")
            params.append(sim_day_max)
        if has_matches_only:
            where_clauses.append("oc.n_matches > 0")
        where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

        # routes 用 sub-query 避免 LEFT JOIN 大表
        sql = f"""SELECT oc.cycle_id, oc.sim_day, oc.sim_hour,
                          oc.wall_timestamp, oc.activity_factor,
                          oc.n_supply_offers, oc.n_demand_requests, oc.n_matches,
                          oc.total_tons, oc.total_cost_sek, oc.total_co2_kg,
                          oc.total_distance_km, oc.n_vehicles_used,
                          oc.n_vehicles_available, oc.fleet_utilization_pct,
                          oc.solver_status, oc.wall_duration_ms,
                          oc.seasonal_factor_avg, oc.seasonal_month,
                          (SELECT COUNT(*) FROM routes r WHERE r.cycle_id = oc.cycle_id) as n_routes
                     FROM optimization_cycles oc
                     {where_sql}
                     ORDER BY oc.id DESC
                     LIMIT ?"""
        params.append(limit)
        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()

        result = []
        for r in rows:
            d = dict(r)
            # 数字 round
            for k in ("total_tons", "total_cost_sek", "total_co2_kg",
                      "total_distance_km", "fleet_utilization_pct",
                      "seasonal_factor_avg"):
                if d.get(k) is not None:
                    d[k] = round(d[k], 2)
            result.append(d)
        return result

    def get_cycle_detail(self, cycle_id: str,
                         match_limit: Optional[int] = None,
                         match_offset: int = 0,
                         route_limit: Optional[int] = None,
                         route_offset: int = 0) -> Optional[Dict[str, Any]]:
        """
        单个 cycle 的完整 detail (iter #11) — KPI + 全部 supply/demand/match/route。

        iter #13: 加 pagination 支持 (match_limit / match_offset / route_limit / route_offset)。
        None / 0 = 不限。

        Args:
            cycle_id: cycle 的 UUID (string)
            match_limit: 最多返回多少 match 行 (None = 全返)
            match_offset: match 起始偏移
            route_limit: 最多返回多少 route 行 (None = 全返)
            route_offset: route 起始偏移

        Returns:
            {cycle: {...}, supply_offers: [...], demand_requests: [...],
             matches: [...], routes: [...],
             pagination: {matches: {total, limit, offset, has_more},
                          routes: {total, limit, offset, has_more}}}
            或 None (cycle 不存在)
        """
        with self._conn() as conn:
            cycle_row = conn.execute(
                "SELECT * FROM optimization_cycles WHERE cycle_id = ?",
                (cycle_id,)
            ).fetchone()
            if not cycle_row:
                return None
            cycle = dict(cycle_row)

            supplies = conn.execute(
                """SELECT supply_id, location_lat, location_lon, material_type,
                          available_tons, moisture_percent, quality_score
                   FROM supply_offers WHERE cycle_id = ?
                   ORDER BY id ASC""",
                (cycle_id,)
            ).fetchall()

            demands = conn.execute(
                """SELECT demand_id, name, location_lat, location_lon,
                          material_type, required_tons, priority, deadline
                   FROM demand_requests WHERE cycle_id = ?
                   ORDER BY id ASC""",
                (cycle_id,)
            ).fetchall()

            # iter #13: matches pagination
            match_total = conn.execute(
                "SELECT COUNT(*) as cnt FROM matches WHERE cycle_id = ?",
                (cycle_id,)
            ).fetchone()["cnt"]
            match_query = """SELECT supply_id, demand_id, material_type, tons,
                                    distance_km, estimated_profit_sek
                             FROM matches WHERE cycle_id = ?
                             ORDER BY id ASC"""
            match_params: List[Any] = [cycle_id]
            if match_limit is not None:
                match_query += " LIMIT ? OFFSET ?"
                match_params.extend([match_limit, match_offset])
            matches = conn.execute(match_query, match_params).fetchall()

            # iter #13: routes pagination
            route_total = conn.execute(
                "SELECT COUNT(*) as cnt FROM routes WHERE cycle_id = ?",
                (cycle_id,)
            ).fetchone()["cnt"]
            route_query = """SELECT vehicle_id, stops_json, distance_km, duration_hours,
                                    cost_sek, co2_kg
                             FROM routes WHERE cycle_id = ?
                             ORDER BY id ASC"""
            route_params: List[Any] = [cycle_id]
            if route_limit is not None:
                route_query += " LIMIT ? OFFSET ?"
                route_params.extend([route_limit, route_offset])
            routes = conn.execute(route_query, route_params).fetchall()

        # parse stops_json
        routes_list = []
        for r in routes:
            d = dict(r)
            try:
                d["stops"] = json.loads(d.pop("stops_json") or "[]")
            except Exception:
                d["stops"] = []
            # round 数字
            for k in ("distance_km", "duration_hours", "cost_sek", "co2_kg"):
                if d.get(k) is not None:
                    d[k] = round(d[k], 2)
            routes_list.append(d)

        # round cycle 数字
        for k in ("total_tons", "total_cost_sek", "total_co2_kg",
                  "total_distance_km", "fleet_utilization_pct",
                  "seasonal_factor_avg"):
            if cycle.get(k) is not None:
                cycle[k] = round(cycle[k], 2)

        return {
            "cycle": cycle,
            "supply_offers": [dict(r) for r in supplies],
            "demand_requests": [dict(r) for r in demands],
            "matches": [dict(r) for r in matches],
            "routes": routes_list,
            # iter #13: pagination metadata
            "pagination": {
                "matches": {
                    "total": match_total,
                    "limit": match_limit,
                    "offset": match_offset,
                    "has_more": (
                        (match_offset + len(matches)) < match_total
                        if match_limit is not None
                        else False
                    ),
                },
                "routes": {
                    "total": route_total,
                    "limit": route_limit,
                    "offset": route_offset,
                    "has_more": (
                        (route_offset + len(routes)) < route_total
                        if route_limit is not None
                        else False
                    ),
                },
            },
        }

    def get_monthly_efficiency_trend(self) -> List[Dict[str, Any]]:
        """
        按月份聚合的 efficiency 趋势 (iter #8)。

        返回: month (1-12) → {month, month_name, n_cycles, total_tons,
                total_cost_sek, total_co2_kg, cost_per_ton_sek, co2_per_ton_kg,
                avg_seasonal_factor, avg_fleet_util_pct, match_rate_pct}

        用于:
        - Dashboard 月度趋势图
        - 季节性对 efficiency 的影响分析
        """
        month_names = [
            "Jan", "Feb", "Mar", "Apr", "May", "Jun",
            "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
        ]
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT seasonal_month,
                          COUNT(*) as n_cycles,
                          SUM(total_tons) as total_tons,
                          SUM(total_cost_sek) as total_cost_sek,
                          SUM(total_co2_kg) as total_co2_kg,
                          AVG(seasonal_factor_avg) as avg_seasonal_factor,
                          AVG(fleet_utilization_pct) as avg_fleet_util_pct,
                          SUM(CASE WHEN n_matches > 0 THEN 1 ELSE 0 END) as cycles_with_matches
                   FROM optimization_cycles
                   WHERE seasonal_month BETWEEN 1 AND 12
                   GROUP BY seasonal_month
                   ORDER BY seasonal_month ASC"""
            ).fetchall()

        result = []
        for row in rows:
            r = dict(row)
            # SUM 可能 None 在某些边界情况
            total_tons = r.get("total_tons") or 0.0
            total_cost = r.get("total_cost_sek") or 0.0
            total_co2 = r.get("total_co2_kg") or 0.0
            n = r.get("n_cycles", 0) or 0
            matches = r.get("cycles_with_matches", 0) or 0
            r["month_name"] = month_names[(r["seasonal_month"] or 1) - 1]
            r["total_tons"] = round(total_tons, 2)
            r["total_cost_sek"] = round(total_cost, 2)
            r["total_co2_kg"] = round(total_co2, 2)
            r["cost_per_ton_sek"] = round(total_cost / total_tons, 2) if total_tons > 0 else None
            r["co2_per_ton_kg"] = round(total_co2 / total_tons, 2) if total_tons > 0 else None
            r["avg_seasonal_factor"] = round(r["avg_seasonal_factor"], 3) if r.get("avg_seasonal_factor") is not None else None
            r["avg_fleet_util_pct"] = round(r["avg_fleet_util_pct"], 2) if r.get("avg_fleet_util_pct") is not None else None
            r["match_rate_pct"] = round(100.0 * matches / n, 1) if n > 0 else None
            result.append(r)
        return result
