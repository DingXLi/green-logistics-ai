"""CSV / 行式导出。"""

from typing import Any, Dict, List, Optional
import csv
from datetime import datetime
import io
import json

from ._base import PersistenceBase
from ._contract import SharedQueryMixin


class ExportMixin(PersistenceBase, SharedQueryMixin):
    """ExportMixin —— 由 agents/persistence.py 拆分而来（iter #77）。"""

    def export_cycles_csv(self, limit: int = 1000,
                           include_metadata: bool = True) -> str:
        """
        Export cycle history as CSV string (iter #11 + iter #19 metadata)。

        Columns (15):
            cycle_id, sim_day, sim_hour, wall_timestamp, activity_factor,
            n_supply_offers, n_demand_requests, n_matches, n_routes,
            total_tons, total_cost_sek, total_co2_kg, total_distance_km,
            n_vehicles_used, n_vehicles_available, fleet_utilization_pct,
            solver_status, wall_duration_ms, seasonal_factor_avg, seasonal_month

        Args (iter #19):
            include_metadata: 是否在 CSV 顶部加 metadata header (生成时间 + row count)

        Returns: CSV string with header + rows (UTF-8).
        Metadata 格式 (iter #19):
            # Green Logistics AI CSV export
            # generated_at: 2026-08-29T20:00:00Z
            # db_path: /data/simulation.db
            # db_size_bytes: 90112
            # table: cycles
            # row_count: 9
        """
        rows = self.get_cycle_history(limit=limit)
        columns = [
            "cycle_id", "sim_day", "sim_hour", "wall_timestamp",
            "activity_factor", "n_supply_offers", "n_demand_requests",
            "n_matches", "total_tons", "total_cost_sek", "total_co2_kg",
            "total_distance_km", "n_vehicles_used", "n_vehicles_available",
            "fleet_utilization_pct", "solver_status", "wall_duration_ms",
            "seasonal_factor_avg", "seasonal_month",
        ]
        buf = io.StringIO()
        if include_metadata:
            buf.write(f"# Green Logistics AI CSV export\n")
            buf.write(f"# generated_at: {datetime.now().isoformat()}\n")
            buf.write(f"# db_path: {self.db_path}\n")
            buf.write(f"# db_size_bytes: {self.db_path.stat().st_size if self.db_path.exists() else 0}\n")
            buf.write(f"# table: cycles\n")
            buf.write(f"# row_count: {len(rows)}\n")
        writer = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            writer.writerow(r)
        return buf.getvalue()

    def export_supplies_csv(self, limit: int = 10000,
                           include_metadata: bool = True) -> str:
        """
        Export supply_offers as CSV (iter #17 + iter #19 metadata)。

        Columns (10):
            cycle_id, supply_id, material_type, location_lat, location_lon,
            available_tons, moisture_percent, quality_score, sim_day, sim_hour

        Args:
            limit: max rows to export (default 10000)
            include_metadata: iter #19, 是否在顶部加 metadata header
        """
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT s.cycle_id, s.supply_id, s.material_type,
                          s.location_lat, s.location_lon,
                          s.available_tons, s.moisture_percent, s.quality_score,
                          c.sim_day, c.sim_hour
                   FROM supply_offers s
                   LEFT JOIN optimization_cycles c ON c.cycle_id = s.cycle_id
                   ORDER BY c.sim_day DESC, s.supply_id
                   LIMIT ?""",
                (limit,),
            ).fetchall()

        columns = [
            "cycle_id", "supply_id", "material_type", "location_lat",
            "location_lon", "available_tons", "moisture_percent",
            "quality_score", "sim_day", "sim_hour",
        ]
        buf = io.StringIO()
        if include_metadata:
            buf.write(f"# Green Logistics AI CSV export\n")
            buf.write(f"# generated_at: {datetime.now().isoformat()}\n")
            buf.write(f"# db_path: {self.db_path}\n")
            buf.write(f"# db_size_bytes: {self.db_path.stat().st_size if self.db_path.exists() else 0}\n")
            buf.write(f"# table: supplies\n")
            buf.write(f"# row_count: {len(rows)}\n")
        writer = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            writer.writerow(dict(r))
        return buf.getvalue()

    def export_perturbed_supplies_csv(
        self,
        limit: int = 10000,
        include_metadata: bool = True,
        only_perturbed: bool = False,
    ) -> str:
        """
        Export supply_offers with perturbation tracking (iter #47).

        Extends the standard supplies export (iter #17) with the iter #38
        perturbation columns: base_seasonal_multiplier, seasonal_multiplier,
        perturbation_applied. Useful for analyzing how active shocks
        affected individual supply points.

        Columns (13):
            cycle_id, supply_id, material_type, location_lat, location_lon,
            available_tons, sim_day, sim_hour,
            base_seasonal_multiplier, seasonal_multiplier, perturbation_applied,
            multiplier_ratio (effective / base), was_perturbed (bool)

        Args (iter #47):
            limit: max rows (default 10000)
            include_metadata: iter #19, top metadata header
            only_perturbed: if True, only include rows where perturbation_applied=1
                           (saves space when only analyzing shocks)
        """
        where_clause = ""
        if only_perturbed:
            where_clause = "WHERE s.perturbation_applied = 1"

        with self._conn() as conn:
            rows = conn.execute(
                f"""SELECT s.cycle_id, s.supply_id, s.material_type,
                          s.location_lat, s.location_lon,
                          s.available_tons, s.moisture_percent, s.quality_score,
                          c.sim_day, c.sim_hour,
                          s.base_seasonal_multiplier, s.seasonal_multiplier,
                          s.perturbation_applied
                   FROM supply_offers s
                   LEFT JOIN optimization_cycles c ON c.cycle_id = s.cycle_id
                   {where_clause}
                   ORDER BY c.sim_day DESC, s.supply_id
                   LIMIT ?""",
                (limit,),
            ).fetchall()

        # Enrich rows with derived fields
        enriched = []
        for r in rows:
            d = dict(r)
            # Use 'is None' (not 'or 1.0') to preserve 0.0 (which is invalid
            # but should be handled, not silently coerced to 1.0).
            base_raw = d.get("base_seasonal_multiplier")
            base = 1.0 if base_raw is None else base_raw
            eff_raw = d.get("seasonal_multiplier")
            eff = 1.0 if eff_raw is None else eff_raw
            d["multiplier_ratio"] = round(eff / base, 3) if base not in (0, None) else None
            d["was_perturbed"] = bool(d.get("perturbation_applied"))
            enriched.append(d)

        columns = [
            "cycle_id", "supply_id", "material_type", "location_lat",
            "location_lon", "available_tons", "moisture_percent",
            "quality_score", "sim_day", "sim_hour",
            "base_seasonal_multiplier", "seasonal_multiplier",
            "perturbation_applied", "multiplier_ratio", "was_perturbed",
        ]
        buf = io.StringIO()
        if include_metadata:
            buf.write(f"# Green Logistics AI CSV export\n")
            buf.write(f"# generated_at: {datetime.now().isoformat()}\n")
            buf.write(f"# db_path: {self.db_path}\n")
            buf.write(f"# db_size_bytes: {self.db_path.stat().st_size if self.db_path.exists() else 0}\n")
            buf.write(f"# table: perturbed_supplies\n")
            buf.write(f"# row_count: {len(enriched)}\n")
            buf.write(f"# only_perturbed: {only_perturbed}\n")
        writer = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for r in enriched:
            writer.writerow(r)
        return buf.getvalue()

    def export_matches_csv(self, limit: int = 10000,
                           include_metadata: bool = True) -> str:
        """
        Export matches as CSV (iter #17 + iter #19 metadata)。

        Columns (8):
            cycle_id, supply_id, demand_id, material_type,
            tons, distance_km, estimated_profit_sek, sim_day

        Args:
            limit: max rows (default 10000)
            include_metadata: iter #19, 是否在顶部加 metadata header
        """
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT m.cycle_id, m.supply_id, m.demand_id, m.material_type,
                          m.tons, m.distance_km, m.estimated_profit_sek,
                          c.sim_day
                   FROM matches m
                   LEFT JOIN optimization_cycles c ON c.cycle_id = m.cycle_id
                   ORDER BY c.sim_day DESC, m.id DESC
                   LIMIT ?""",
                (limit,),
            ).fetchall()

        columns = [
            "cycle_id", "supply_id", "demand_id", "material_type",
            "tons", "distance_km", "estimated_profit_sek", "sim_day",
        ]
        buf = io.StringIO()
        if include_metadata:
            buf.write(f"# Green Logistics AI CSV export\n")
            buf.write(f"# generated_at: {datetime.now().isoformat()}\n")
            buf.write(f"# db_path: {self.db_path}\n")
            buf.write(f"# db_size_bytes: {self.db_path.stat().st_size if self.db_path.exists() else 0}\n")
            buf.write(f"# table: matches\n")
            buf.write(f"# row_count: {len(rows)}\n")
        writer = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            writer.writerow(dict(r))
        return buf.getvalue()

    def export_routes_csv(self, limit: int = 10000,
                          include_metadata: bool = True) -> str:
        """
        Export routes as CSV (iter #17 + iter #19 metadata)。

        Columns (8):
            cycle_id, vehicle_id, distance_km, duration_hours,
            cost_sek, co2_kg, stops_count, sim_day

        Args:
            limit: max rows (default 10000)
            include_metadata: iter #19, 是否在顶部加 metadata header
        """
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT r.cycle_id, r.vehicle_id,
                          r.distance_km, r.duration_hours,
                          r.cost_sek, r.co2_kg,
                          COALESCE(json_array_length(r.stops_json), 0) AS stops_count,
                          c.sim_day
                   FROM routes r
                   LEFT JOIN optimization_cycles c ON c.cycle_id = r.cycle_id
                   ORDER BY c.sim_day DESC, r.id DESC
                   LIMIT ?""",
                (limit,),
            ).fetchall()

        columns = [
            "cycle_id", "vehicle_id", "distance_km", "duration_hours",
            "cost_sek", "co2_kg", "stops_count", "sim_day",
        ]
        buf = io.StringIO()
        if include_metadata:
            buf.write(f"# Green Logistics AI CSV export\n")
            buf.write(f"# generated_at: {datetime.now().isoformat()}\n")
            buf.write(f"# db_path: {self.db_path}\n")
            buf.write(f"# db_size_bytes: {self.db_path.stat().st_size if self.db_path.exists() else 0}\n")
            buf.write(f"# table: routes\n")
            buf.write(f"# row_count: {len(rows)}\n")
        writer = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            writer.writerow(dict(r))
        return buf.getvalue()

    def export_cycle_detail_csv(
        self,
        cycle_id: str,
        include_metadata: bool = True,
    ) -> Optional[str]:
        """
        iter #48: Export full cycle detail as one combined CSV (iter #11 data).

        Includes all sections in one file, separated by header comments:
            - cycle metadata (single row: 25 KPI columns)
            - supply_offers (n_supplies rows)
            - demand_requests (n_demands rows)
            - matches (n_matches rows)
            - routes (n_routes rows, stops_count derived from stops_json)

        Args:
            cycle_id: the cycle to export
            include_metadata: iter #19, top file metadata header

        Returns:
            CSV string with multiple sections, or None if cycle_id not found.
        """
        detail = self.get_cycle_detail(cycle_id)
        if detail is None:
            return None

        cycle = detail["cycle"]
        supplies = detail.get("supply_offers", [])
        demands = detail.get("demand_requests", [])
        matches = detail.get("matches", [])
        routes = detail.get("routes", [])

        buf = io.StringIO()
        if include_metadata:
            buf.write(f"# Green Logistics AI CSV export\n")
            buf.write(f"# generated_at: {datetime.now().isoformat()}\n")
            buf.write(f"# db_path: {self.db_path}\n")
            buf.write(f"# db_size_bytes: {self.db_path.stat().st_size if self.db_path.exists() else 0}\n")
            buf.write(f"# cycle_id: {cycle_id}\n")
            buf.write(f"# n_supplies: {len(supplies)}\n")
            buf.write(f"# n_demands: {len(demands)}\n")
            buf.write(f"# n_matches: {len(matches)}\n")
            buf.write(f"# n_routes: {len(routes)}\n")

        # Section 1: cycle metadata (single row)
        buf.write(f"# section: cycle_metadata\n")
        cycle_cols = [
            "cycle_id", "sim_day", "sim_hour", "wall_timestamp", "activity_factor",
            "n_supply_offers", "n_demand_requests", "n_matches",
            "total_tons", "total_cost_sek", "total_co2_kg", "total_distance_km",
            "n_vehicles_used", "n_vehicles_available", "fleet_utilization_pct",
            "solver_status", "wall_duration_ms",
            "seasonal_factor_avg", "seasonal_month", "base_seasonal_factor_avg",
            "perturbation_count", "perturbation_total_multiplier",
        ]
        writer = csv.DictWriter(buf, fieldnames=cycle_cols, extrasaction="ignore")
        writer.writeheader()
        writer.writerow(cycle)
        buf.write("\n")

        # Section 2: supply_offers
        buf.write(f"# section: supply_offers\n")
        supply_cols = [
            "supply_id", "material_type", "location_lat", "location_lon",
            "available_tons", "moisture_percent", "quality_score",
            "base_seasonal_multiplier", "seasonal_multiplier", "perturbation_applied",
        ]
        writer = csv.DictWriter(buf, fieldnames=supply_cols, extrasaction="ignore")
        writer.writeheader()
        for s in supplies:
            writer.writerow(s)
        buf.write("\n")

        # Section 3: demand_requests
        buf.write(f"# section: demand_requests\n")
        demand_cols = [
            "demand_id", "name", "location_lat", "location_lon",
            "material_type", "required_tons", "priority", "deadline",
        ]
        writer = csv.DictWriter(buf, fieldnames=demand_cols, extrasaction="ignore")
        writer.writeheader()
        for d in demands:
            writer.writerow(d)
        buf.write("\n")

        # Section 4: matches
        buf.write(f"# section: matches\n")
        match_cols = [
            "supply_id", "demand_id", "material_type",
            "tons", "distance_km", "estimated_profit_sek",
        ]
        writer = csv.DictWriter(buf, fieldnames=match_cols, extrasaction="ignore")
        writer.writeheader()
        for m in matches:
            writer.writerow(m)
        buf.write("\n")

        # Section 5: routes
        buf.write(f"# section: routes\n")
        route_cols = [
            "vehicle_id", "distance_km", "duration_hours",
            "cost_sek", "co2_kg", "stops_count",
        ]
        writer = csv.DictWriter(buf, fieldnames=route_cols, extrasaction="ignore")
        writer.writeheader()
        for r in routes:
            # derive stops_count from stops list (already parsed by get_cycle_detail)
            row = dict(r)
            stops = row.get("stops")
            if isinstance(stops, list):
                row["stops_count"] = len(stops)
            else:
                # Fallback: try parsing stops_json string
                stops_json = row.get("stops_json")
                if stops_json:
                    try:
                        stops_parsed = json.loads(stops_json) if isinstance(stops_json, str) else stops_json
                        row["stops_count"] = len(stops_parsed) if isinstance(stops_parsed, list) else 0
                    except (json.JSONDecodeError, TypeError):
                        row["stops_count"] = 0
                else:
                    row["stops_count"] = 0
            writer.writerow(row)

        return buf.getvalue()

    # ============================================
    # iter #27: Native row exports (for parquet / json / ndjson)
    # 避免 CSV 解析 round-trip — 直接返 list of dicts
    # ============================================
    def export_cycles_rows(self, limit: int = 1000) -> List[Dict[str, Any]]:
        """iter #27: 返回 cycles 行的 list of dicts (无 CSV parsing 开销)。"""
        rows = self.get_cycle_history(limit=limit)
        return [dict(r) for r in rows]

    def export_supplies_rows(self, limit: int = 10000) -> List[Dict[str, Any]]:
        """iter #27: 返回 supply_offers 行的 list of dicts。"""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT s.cycle_id, s.supply_id, s.material_type,
                          s.location_lat, s.location_lon,
                          s.available_tons, s.moisture_percent, s.quality_score,
                          c.sim_day, c.sim_hour
                   FROM supply_offers s
                   LEFT JOIN optimization_cycles c ON c.cycle_id = s.cycle_id
                   ORDER BY c.sim_day DESC, s.supply_id
                   LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def export_matches_rows(self, limit: int = 10000) -> List[Dict[str, Any]]:
        """iter #27: 返回 matches 行的 list of dicts。"""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT m.cycle_id, m.supply_id, m.demand_id, m.material_type,
                          m.tons, m.distance_km, m.estimated_profit_sek,
                          c.sim_day
                   FROM matches m
                   LEFT JOIN optimization_cycles c ON c.cycle_id = m.cycle_id
                   ORDER BY c.sim_day DESC, m.id DESC
                   LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def export_routes_rows(self, limit: int = 10000) -> List[Dict[str, Any]]:
        """iter #27: 返回 routes 行的 list of dicts。"""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT r.cycle_id, r.vehicle_id,
                          r.distance_km, r.duration_hours,
                          r.cost_sek, r.co2_kg,
                          COALESCE(json_array_length(r.stops_json), 0) AS stops_count,
                          c.sim_day
                   FROM routes r
                   LEFT JOIN optimization_cycles c ON c.cycle_id = r.cycle_id
                   ORDER BY c.sim_day DESC, r.id DESC
                   LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
