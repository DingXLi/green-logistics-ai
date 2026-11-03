"""季节扰动规则的增删改查与影响分析。"""

from typing import Any, Dict, List, Optional
from datetime import datetime

from ._base import PersistenceBase


class PerturbationMixin(PersistenceBase):
    """PerturbationMixin —— 由 agents/persistence.py 拆分而来（iter #77）。"""

    def add_seasonal_perturbation(
        self,
        label: str,
        start_sim_day: int,
        end_sim_day: int,
        material_type: str,
        multiplier: float,
    ) -> Dict[str, Any]:
        """
        Insert a new perturbation rule. Validates bounds (raises ValueError on
        bad input so the API layer can convert to HTTP 400).

        Returns:
            The persisted row (including auto-generated id + created_at).
        """
        # Local import keeps the module importable in tests without DB.
        from data.seasonal_perturbation import validate_perturbation

        err = validate_perturbation(
            label, start_sim_day, end_sim_day, material_type, multiplier
        )
        if err:
            raise ValueError(err)

        now = datetime.now().isoformat()
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO seasonal_perturbations
                       (label, start_sim_day, end_sim_day,
                        material_type, multiplier, active, created_at)
                   VALUES (?, ?, ?, ?, ?, 1, ?)""",
                (
                    label.strip(),
                    int(start_sim_day),
                    int(end_sim_day),
                    material_type,
                    float(multiplier),
                    now,
                ),
            )
            new_id = cur.lastrowid
        return {
            "id": new_id,
            "label": label.strip(),
            "start_sim_day": int(start_sim_day),
            "end_sim_day": int(end_sim_day),
            "material_type": material_type,
            "multiplier": float(multiplier),
            "active": True,
            "created_at": now,
        }

    def list_seasonal_perturbations(
        self, active_only: bool = True
    ) -> List[Dict[str, Any]]:
        """Return all perturbations, optionally filtering to active=1."""
        sql = (
            "SELECT id, label, start_sim_day, end_sim_day, "
            "material_type, multiplier, active, created_at "
            "FROM seasonal_perturbations"
        )
        if active_only:
            sql += " WHERE active = 1"
        sql += " ORDER BY start_sim_day, id"
        with self._conn() as conn:
            rows = conn.execute(sql).fetchall()
        return [dict(r) for r in rows]

    def get_perturbation_history(
        self,
        include_inactive: bool = True,
        since_sim_day: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        iter #49: Full perturbation history (CRUD audit log).

        Lists all perturbations ever created, including deactivated ones.
        Useful for ops to see what shocks have been applied over time.

        Args:
            include_inactive: if False, only return active=1
            since_sim_day: only include perturbations whose window
                          started on or after this sim_day

        Returns:
            [{
              id, label, start_sim_day, end_sim_day, material_type,
              multiplier, active, created_at,
              duration_sim_days (end - start + 1),
            }, ...]
            Sorted by start_sim_day DESC (newest first).
        """
        where_clauses: List[str] = []
        params: List[Any] = []
        if not include_inactive:
            where_clauses.append("active = 1")
        if since_sim_day is not None:
            where_clauses.append("start_sim_day >= ?")
            params.append(int(since_sim_day))
        where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

        with self._conn() as conn:
            rows = conn.execute(
                f"""SELECT id, label, start_sim_day, end_sim_day,
                          material_type, multiplier, active, created_at
                   FROM seasonal_perturbations
                   {where_sql}
                   ORDER BY start_sim_day DESC, id DESC""",
                params,
            ).fetchall()

        results = []
        for r in rows:
            d = dict(r)
            d["duration_sim_days"] = d["end_sim_day"] - d["start_sim_day"] + 1
            results.append(d)
        return results

    def get_active_perturbations(
        self, sim_day: int, material_type: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Return perturbations active at ``sim_day`` (window matches).

        If ``material_type`` is provided, includes both:
        - rules with that material_type
        - wildcard rules (material_type='*')

        This is the hot-path query called once per cycle by the coordinator.
        Index ``idx_perturb_window`` makes it O(log n + k).
        """
        sql = (
            "SELECT id, label, start_sim_day, end_sim_day, "
            "material_type, multiplier, active, created_at "
            "FROM seasonal_perturbations "
            "WHERE active = 1 AND start_sim_day <= ? AND end_sim_day >= ?"
        )
        params: List[Any] = [int(sim_day), int(sim_day)]
        if material_type is not None:
            sql += " AND (material_type = ? OR material_type = '*')"
            params.append(material_type)
        sql += " ORDER BY id"
        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def delete_seasonal_perturbation(self, perturbation_id: int) -> bool:
        """Hard-delete a single perturbation. Returns whether a row was removed."""
        with self._conn() as conn:
            cur = conn.execute(
                "DELETE FROM seasonal_perturbations WHERE id = ?",
                (int(perturbation_id),),
            )
        return cur.rowcount > 0

    def deactivate_seasonal_perturbation(self, perturbation_id: int) -> bool:
        """Soft-delete (set active=0). Returns whether a row was updated."""
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE seasonal_perturbations SET active = 0 WHERE id = ?",
                (int(perturbation_id),),
            )
        return cur.rowcount > 0

    def clear_seasonal_perturbations(self) -> int:
        """Hard-delete all perturbations. Returns rowcount."""
        with self._conn() as conn:
            cur = conn.execute("DELETE FROM seasonal_perturbations")
        return cur.rowcount

    # ====================================================================
    # iter #38: Perturbation impact analytics
    # ====================================================================
    def get_perturbation_impact(
        self,
        since_sim_day: Optional[int] = None,
        until_sim_day: Optional[int] = None,
        limit: int = 90,
    ) -> Dict[str, Any]:
        """
        Per-cycle perturbation impact analysis (iter #38).

        Joins per-cycle aggregates with perturbation tracking fields to
        show how much active shocks moved the seasonal_factor_avg.

        Returns:
            {
              "cycles": [{sim_day, base_seasonal_factor_avg,
                            seasonal_factor_avg, delta,
                            perturbation_count,
                            perturbation_total_multiplier,
                            wall_timestamp}, ...],
              "summary": {
                "n_cycles_total": int,
                "n_cycles_with_perturbation": int,
                "avg_delta": float | None,
                "max_delta": float | None,
                "min_delta": float | None,
                "max_total_multiplier": float | None,
                "window_start": int | None,
                "window_end": int | None,
              }
            }
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

        # iter #38: ORDER BY id DESC (most-recent-inserted first)
        # sim_day DESC is misleading because the live sim may cycle back
        # to low sim_day values after year wrap (sim_day % 360). id is
        # monotonic per insert and reflects true wall-clock ordering.
        sql = f"""SELECT sim_day, wall_timestamp,
                          base_seasonal_factor_avg, seasonal_factor_avg,
                          perturbation_count, perturbation_total_multiplier
                   FROM optimization_cycles
                   {where_sql}
                   ORDER BY id DESC
                   LIMIT ?"""
        params.append(int(limit))
        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()

        cycles = []
        deltas: List[float] = []
        multipliers: List[float] = []
        n_perturbed = 0
        for r in rows:
            base_f = r["base_seasonal_factor_avg"] or 1.0
            eff_f = r["seasonal_factor_avg"] or 1.0
            delta = round(eff_f - base_f, 3)
            cycles.append({
                "sim_day": r["sim_day"],
                "wall_timestamp": r["wall_timestamp"],
                "base_seasonal_factor_avg": round(base_f, 3),
                "seasonal_factor_avg": round(eff_f, 3),
                "delta": delta,
                "perturbation_count": r["perturbation_count"] or 0,
                "perturbation_total_multiplier": r["perturbation_total_multiplier"] or 1.0,
            })
            deltas.append(delta)
            multipliers.append(r["perturbation_total_multiplier"] or 1.0)
            if (r["perturbation_count"] or 0) > 0:
                n_perturbed += 1
        # Reverse cycles list so response is ordered OLDEST→NEWEST (more
        # intuitive for time-series charts on the frontend).
        cycles.reverse()

        summary = {
            "n_cycles_total": len(cycles),
            "n_cycles_with_perturbation": n_perturbed,
            "avg_delta": round(sum(deltas) / len(deltas), 3) if deltas else None,
            "max_delta": round(max(deltas), 3) if deltas else None,
            "min_delta": round(min(deltas), 3) if deltas else None,
            "max_total_multiplier": round(max(multipliers), 3) if multipliers else None,
            # cycles is now OLDEST → NEWEST (reversed in loop above)
            "window_start": cycles[0]["sim_day"] if cycles else None,
            "window_end": cycles[-1]["sim_day"] if cycles else None,
        }
        return {"cycles": cycles, "summary": summary}

    def get_perturbation_impact_by_material(
        self,
        since_sim_day: Optional[int] = None,
        until_sim_day: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        iter #46: Per-material perturbation impact breakdown.

        Aggregates supply_offers.perturbation_applied by material_type
        to show "which materials get hit most by perturbations" and
        the avg seasonal_multiplier ratio (effective / baseline).

        Returns:
            {
              "by_material": [
                {material_type, n_perturbed, n_total,
                 perturbation_rate_pct, avg_effective_multiplier,
                 avg_base_multiplier, avg_ratio},
                ...
              ],
              "summary": {
                "n_materials": int,
                "n_perturbed_total": int,
                "n_supply_offers_total": int,
                "overall_perturbation_rate_pct": float,
              },
              "window": {
                "since_sim_day": int | None,
                "until_sim_day": int | None,
              },
            }

        Excludes NULL material_type rows.
        """
        where_clauses: List[str] = ["s.material_type IS NOT NULL"]
        params: List[Any] = []
        if since_sim_day is not None:
            where_clauses.append("c.sim_day >= ?")
            params.append(int(since_sim_day))
        if until_sim_day is not None:
            where_clauses.append("c.sim_day <= ?")
            params.append(int(until_sim_day))
        where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
        # Join supply_offers with cycles for the window filter.
        # material_type NULL rows are excluded; "perturbed" means
        # perturbation_applied == 1.
        sql = f"""SELECT s.material_type AS material_type,
                          COUNT(*) AS n_total,
                          SUM(CASE WHEN s.perturbation_applied = 1 THEN 1 ELSE 0 END)
                              AS n_perturbed,
                          AVG(CASE WHEN s.perturbation_applied = 1
                                   THEN s.seasonal_multiplier END) AS avg_effective,
                          AVG(CASE WHEN s.perturbation_applied = 1
                                   THEN s.base_seasonal_multiplier END) AS avg_base
                   FROM supply_offers s
                   JOIN optimization_cycles c ON s.cycle_id = c.cycle_id
                   {where_sql}
                   GROUP BY s.material_type
                   ORDER BY n_perturbed DESC, n_total DESC"""

        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()

        by_material: List[Dict[str, Any]] = []
        n_perturbed_total = 0
        n_total_all = 0
        for r in rows:
            d = dict(r)
            n_total = d.get("n_total") or 0
            n_perturbed = d.get("n_perturbed") or 0
            n_perturbed_total += n_perturbed
            n_total_all += n_total
            avg_eff = d.get("avg_effective")
            avg_base = d.get("avg_base")
            ratio = None
            if avg_eff is not None and avg_base not in (None, 0):
                ratio = round(avg_eff / avg_base, 3)
            by_material.append({
                "material_type": d["material_type"],
                "n_perturbed": n_perturbed,
                "n_total": n_total,
                "perturbation_rate_pct": round(100 * n_perturbed / max(n_total, 1), 1),
                "avg_effective_multiplier": round(avg_eff, 3) if avg_eff is not None else None,
                "avg_base_multiplier": round(avg_base, 3) if avg_base is not None else None,
                "avg_ratio": ratio,
            })

        return {
            "by_material": by_material,
            "summary": {
                "n_materials": len(by_material),
                "n_perturbed_total": n_perturbed_total,
                "n_supply_offers_total": n_total_all,
                "overall_perturbation_rate_pct": (
                    round(100 * n_perturbed_total / max(n_total_all, 1), 1)
                ),
            },
            "window": {
                "since_sim_day": int(since_sim_day) if since_sim_day is not None else None,
                "until_sim_day": int(until_sim_day) if until_sim_day is not None else None,
            },
        }
