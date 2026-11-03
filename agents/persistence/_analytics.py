"""KPI / 时间序列 / 预测趋势分析。"""

from typing import Any, Dict, List, Optional

from ._base import PersistenceBase
from ._forecast_math import ForecastMathMixin
from ._contract import SharedQueryMixin


class AnalyticsMixin(PersistenceBase, ForecastMathMixin, SharedQueryMixin):
    """AnalyticsMixin —— 由 agents/persistence.py 拆分而来（iter #77）。"""

    def get_kpi_timeseries(self, since_sim_day: Optional[int] = None,
                         until_sim_day: Optional[int] = None) -> List[Dict[str, Any]]:
        """KPI 时间序列 (iter #8 + iter #18 时间窗口) — 按 sim_day 聚合。

        Args (iter #18 新增):
            since_sim_day: 起始 sim_day (含)
            until_sim_day: 结束 sim_day (含)

        iter #52 新增 efficiency 指标 (per-ton / per-match efficiency):
  - cost_per_ton_sek: cost_sek / tons (运输每吨成本)
  - co2_per_ton_kg: co2_kg / tons (运输每吨 CO2)
  - cost_per_match_sek: cost_sek / matches (单次匹配成本)
  - n_supply_offers: 该 sim_day 总供应数
  - n_demand_requests: 该 sim_day 总需求数
  - supply_demand_ratio: matches / max(supply, demand) (供需匹配率, 0-1)
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
            # iter #52: 加入 supply/demand counts 和 efficiency metrics
            rows = conn.execute(
                f"""SELECT oc.sim_day,
                          SUM(oc.total_tons) as tons,
                          SUM(oc.total_cost_sek) as cost_sek,
                          SUM(oc.total_co2_kg) as co2_kg,
                          AVG(oc.fleet_utilization_pct) as util_pct,
                          SUM(oc.n_matches) as matches,
                          COUNT(*) as n_cycles_in_day,
                          COALESCE(SUM(oc.n_supply_offers), 0) as n_supply_offers,
                          COALESCE(SUM(oc.n_demand_requests), 0) as n_demand_requests
                   FROM optimization_cycles oc
                   {where_sql}
                   GROUP BY oc.sim_day
                   ORDER BY oc.sim_day ASC""",
                params,
            ).fetchall()
            result = []
            for r in rows:
                d = dict(r)
                tons = d.get("tons") or 0
                cost = d.get("cost_sek") or 0
                co2 = d.get("co2_kg") or 0
                matches = d.get("matches") or 0
                supply = d.get("n_supply_offers") or 0
                demand = d.get("n_demand_requests") or 0

                # iter #52: per-ton efficiency (None if no transport)
                d["cost_per_ton_sek"] = round(cost / tons, 3) if tons > 0 else None
                d["co2_per_ton_kg"] = round(co2 / tons, 3) if tons > 0 else None
                d["cost_per_match_sek"] = round(cost / matches, 2) if matches > 0 else None
                # supply_demand_ratio: 供需匹配效率
                denom = max(supply, demand)
                d["supply_demand_ratio"] = round(matches / denom, 3) if denom > 0 else None

                for k in ("tons", "cost_sek", "co2_kg", "util_pct"):
                    if d.get(k) is not None:
                        d[k] = round(d[k], 2)
                result.append(d)
            return result

    def get_fleet_timeseries(self) -> List[Dict[str, Any]]:
        """
        Fleet 时间序列 (iter #9) — 按 sim_day 聚合 fleet 指标。

        返回:
            sim_day → {sim_day, n_vehicles_used, n_vehicles_available,
                       fleet_utilization_pct, total_distance_km,
                       n_matches, total_tons}

        用途:
        - Dashboard fleet trend 图 (utilization 趋势 / 车队使用率)
        - 分析调度模式 (高峰/低谷 sim_day)
        - 长期车队 ROI (util 与 cost 关系)
        """
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT sim_day,
                          SUM(n_vehicles_used) as n_vehicles_used,
                          SUM(n_vehicles_available) as n_vehicles_available,
                          AVG(fleet_utilization_pct) as fleet_utilization_pct,
                          SUM(total_distance_km) as total_distance_km,
                          SUM(n_matches) as n_matches,
                          SUM(total_tons) as total_tons
                   FROM optimization_cycles
                   GROUP BY sim_day
                   ORDER BY sim_day ASC"""
            ).fetchall()

        result = []
        for row in rows:
            r = dict(row)
            # round 数字
            if r.get("fleet_utilization_pct") is not None:
                r["fleet_utilization_pct"] = round(r["fleet_utilization_pct"], 2)
            if r.get("total_distance_km") is not None:
                r["total_distance_km"] = round(r["total_distance_km"], 2)
            # 防御性 defaults
            r.setdefault("n_vehicles_used", 0)
            r.setdefault("n_vehicles_available", 0)
            r.setdefault("total_distance_km", 0.0)
            r.setdefault("n_matches", 0)
            r.setdefault("total_tons", 0.0)
            result.append(r)
        return result

    def get_seasonal_timeseries(self) -> List[Dict[str, Any]]:
        """按月份聚合的 KPI + seasonal_factor (iter #4)

        返回:
            month (1-12) → {month, month_name, n_cycles, total_tons,
                            total_cost_sek, total_co2_kg, avg_seasonal_factor,
                            avg_cost_sek_per_cycle, avg_co2_per_cycle}

        供前端分析 "夏季 vs 冬季” cost/CO2 差异。
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
                          AVG(seasonal_factor_avg) as avg_seasonal_factor
                   FROM optimization_cycles
                   WHERE seasonal_month BETWEEN 1 AND 12
                   GROUP BY seasonal_month
                   ORDER BY seasonal_month ASC"""
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            month = d["seasonal_month"]
            n = max(1, d.get("n_cycles", 0))
            d["month"] = month
            d["month_name"] = month_names[month - 1]
            d["avg_cost_sek_per_cycle"] = round(d.get("total_cost_sek", 0) / n, 2)
            d["avg_co2_per_cycle"] = round(d.get("total_co2_kg", 0) / n, 2)
            d["avg_seasonal_factor"] = round(d.get("avg_seasonal_factor", 1.0), 3)
            out.append(d)
        return out

    def get_seasonal_timeseries_by_material(
        self,
    ) -> Dict[str, Any]:
        """
        iter #46: Seasonal time-series broken down by material_type.

        JOINs supply_offers with optimization_cycles so we can answer
        "in July, how many tons of concrete were produced vs metal_scrap?".

        Output shape:
            {
              "n_materials": int,
              "n_months": int,                # months that have any data
              "materials": [str, ...],        # sorted list
              "month_labels": [str, ...],     # ["Jan", ..., "Dec"]
              "matrix": [
                {material, month, month_name, n_supply_offers,
                 total_tons, avg_seasonal_multiplier, avg_base_multiplier}
                ...
              ]
            }

        Empty when no cycles exist.
        """
        month_names = [
            "Jan", "Feb", "Mar", "Apr", "May", "Jun",
            "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
        ]
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT s.material_type AS material_type,
                          c.seasonal_month AS seasonal_month,
                          COUNT(*) AS n_supply_offers,
                          SUM(s.available_tons) AS total_tons,
                          AVG(s.seasonal_multiplier) AS avg_seasonal_multiplier,
                          AVG(s.base_seasonal_multiplier) AS avg_base_multiplier
                   FROM supply_offers s
                   JOIN optimization_cycles c ON s.cycle_id = c.cycle_id
                   WHERE c.seasonal_month BETWEEN 1 AND 12
                     AND s.material_type IS NOT NULL
                   GROUP BY s.material_type, c.seasonal_month
                   ORDER BY s.material_type ASC, c.seasonal_month ASC"""
            ).fetchall()

        matrix = []
        materials_set = set()
        months_set = set()
        for r in rows:
            d = dict(r)
            month = d["seasonal_month"]
            material = d["material_type"]
            materials_set.add(material)
            months_set.add(month)
            d["month_name"] = month_names[month - 1] if 1 <= month <= 12 else "?"
            d["total_tons"] = round(d.get("total_tons") or 0.0, 2)
            d["avg_seasonal_multiplier"] = round(d.get("avg_seasonal_multiplier") or 1.0, 3)
            d["avg_base_multiplier"] = round(d.get("avg_base_multiplier") or 1.0, 3)
            matrix.append(d)

        return {
            "n_materials": len(materials_set),
            "n_months": len(months_set),
            "materials": sorted(materials_set),
            "month_labels": month_names,
            "matrix": matrix,
        }

    def forecast_next_n_sim_days(
        self,
        horizon: int = 7,
        history_n: int = 14,
        metrics: Optional[List[str]] = None,
        method: str = "linear",
    ) -> Dict[str, Any]:
        """
        Predict future KPI trends using linear regression on history (iter #26 + iter #28).

        给一个 horizon (默认 7 sim_days), 对每个 metric 用选定 method 拟合:
        - "linear" (default): y = slope * sim_day + intercept (iter #26)
        - "moving_average": 未来 horizon 步都 = last_window_mean (简单平滑)
        - "exponential_smoothing": alpha-weighted, future = smoothed value (iter #28)

        Args:
            horizon: 预测未来多少个 sim_day (default 7, max 30)
            history_n: 用多少历史 sim_day 拟合 (default 14, max 90)
            metrics: 预测哪些 metric (default = 所有 4 个)
                - "cost_sek" (总成本 SEK)
                - "co2_kg" (总 CO2 kg)
                - "util_pct" (车队利用率 %)
                - "matches" (总匹配数)
            method (iter #28): "linear" / "moving_average" / "exponential_smoothing"
                - linear: 拟合直线, 适合趋势性数据
                - moving_average: 简单平滑, 适合无趋势噪响数据
                - exponential_smoothing: 加权平滑, 适合近期更重要的数据

        Returns:
            {
              horizon: int,
              history_n: int,
              last_sim_day: int (最后已知 sim_day),
              forecast_sim_days: [last+1, ..., last+horizon],
              metrics: {
                "cost_sek": {
                  history: [{sim_day, value, is_forecast: false}, ...],
                  forecast: [{sim_day, value, is_forecast: true,
                              lower_95, upper_95, residual_std}, ...],
                  trend: "up" | "down" | "flat",
                  slope_per_day: float,
                  r_squared: float,  # 拟合优度
                  mean_value: float,
                },
                ...
              }
            }

        错误处理:
        - < 2 history points → 返空 metrics dict
        - 非法 metric → 跳过 (silent)
        - history < history_n 拟合仍工作 (用全部)
        """
        # iter #26: validation
        if horizon < 1 or horizon > 30:
            raise ValueError(f"horizon must be 1-30, got {horizon}")
        if history_n < 2 or history_n > 90:
            raise ValueError(f"history_n must be 2-90, got {history_n}")

        if metrics is None:
            metrics = ["cost_sek", "co2_kg", "util_pct", "matches"]

        # iter #26: import numpy (lazy — not always needed)
        try:
            import numpy as np
        except ImportError:
            raise ImportError(
                "numpy required for forecast. Install with: pip install numpy"
            )

        # 拿 KPI 时间序列
        kpi_rows = self.get_kpi_timeseries()
        if len(kpi_rows) < 2:
            # Not enough data to forecast
            return {
                "horizon": horizon,
                "history_n": history_n,
                "last_sim_day": kpi_rows[-1]["sim_day"] if kpi_rows else None,
                "forecast_sim_days": [],
                "metrics": {},
                "note": "need at least 2 historical sim_days for forecast",
            }

        # Take last history_n points (most recent)
        history = kpi_rows[-history_n:]
        sim_days = np.array([r["sim_day"] for r in history], dtype=float)
        last_sim_day = int(sim_days[-1])

        # Build forecast sim_days array
        forecast_sim_days = list(range(last_sim_day + 1, last_sim_day + horizon + 1))
        forecast_x = np.array(forecast_sim_days, dtype=float)

        # iter #28: dispatch on method
        valid_methods = ("linear", "moving_average", "exponential_smoothing")
        if method not in valid_methods:
            raise ValueError(
                f"method must be one of {valid_methods}, got {method!r}"
            )

        # iter #26: for each metric, fit + forecast
        result_metrics = {}
        for metric in metrics:
            if metric not in ("cost_sek", "co2_kg", "util_pct", "matches"):
                continue
            values = np.array([r.get(metric, 0) or 0 for r in history], dtype=float)

            # iter #28: dispatch to method-specific fitter
            if method == "linear":
                fit = self._fit_linear(sim_days, values)
            elif method == "moving_average":
                fit = self._fit_moving_average(sim_days, values)
            else:  # exponential_smoothing
                fit = self._fit_exponential_smoothing(sim_days, values)

            slope = fit["slope"]
            forecast_values = fit["forecast_values_fn"](forecast_x)
            ci_half = fit["ci_half"]
            r_squared = fit["r_squared"]
            trend = fit["trend"]
            method_meta = fit["method_meta"]

            # Build history output (with is_forecast flag for consistency)
            history_out = [
                {
                    "sim_day": int(r["sim_day"]),
                    "value": float(r.get(metric, 0) or 0),
                    "is_forecast": False,
                }
                for r in history
            ]

            # Build forecast output
            forecast_out = []
            for i, sd in enumerate(forecast_sim_days):
                val = float(forecast_values[i])
                forecast_out.append({
                    "sim_day": sd,
                    "value": round(val, 2),
                    "is_forecast": True,
                    "lower_95": round(val - ci_half, 2),
                    "upper_95": round(val + ci_half, 2),
                })

            result_metrics[metric] = {
                "history": history_out,
                "forecast": forecast_out,
                "trend": trend,
                "method": method,
                "slope_per_day": round(float(slope), 4),
                "r_squared": round(r_squared, 4),
                "residual_std": round(fit["residual_std"], 2),
                "mean_value": round(float(np.mean(values)), 2),
                **method_meta,
            }

        return {
            "horizon": horizon,
            "history_n": history_n,
            "method": method,
            "last_sim_day": last_sim_day,
            "forecast_sim_days": forecast_sim_days,
            "metrics": result_metrics,
        }

    def get_summary(self) -> Dict[str, Any]:
        """全局统计 summary (含 LLM 决策统计)"""
        with self._conn() as conn:
            row = conn.execute(
                """SELECT COUNT(*) as n_cycles,
                          SUM(total_tons) as total_tons,
                          SUM(total_cost_sek) as total_cost_sek,
                          SUM(total_co2_kg) as total_co2_kg,
                          AVG(fleet_utilization_pct) as avg_utilization
                   FROM optimization_cycles"""
            ).fetchone()
            base = dict(row) if row else {}
            # 加上 LLM 决策统计
            try:
                llm_row = conn.execute(
                    """SELECT COUNT(*) as n_total,
                              SUM(CASE WHEN source='llm' THEN 1 ELSE 0 END) as n_real_llm,
                              SUM(CASE WHEN source='fallback' THEN 1 ELSE 0 END) as n_fallback,
                              COUNT(DISTINCT decision_type) as n_types
                       FROM llm_decisions"""
                ).fetchone()
                base["llm_decisions"] = dict(llm_row) if llm_row else {}
            except Exception:
                base["llm_decisions"] = {}
            return base

    def get_efficiency_metrics(self) -> Dict[str, Any]:
        """
        效率指标 (iter #7) — 从 optimization_cycles 聚合 cost/CO2/util 的 "per ton" 比率。

        用于:
        - Dashboard 顶部 KPI (cost per ton SEK / co2 per ton kg / avg fleet util)
        - 长期趋势分析
        - ROI 报告 (每吨废料省多少 SEK / 减排多少 CO2)

        返回:
            n_cycles, total_tons, total_cost_sek, total_co2_kg
            cost_per_ton_sek, co2_per_ton_kg, avg_fleet_util_pct
            min_sim_day, max_sim_day, cycles_with_matches
            avg_tons_per_cycle, avg_cost_per_cycle, avg_co2_per_cycle
        """
        with self._conn() as conn:
            row = conn.execute(
                """SELECT COUNT(*) as n_cycles,
                          SUM(total_tons) as total_tons,
                          SUM(total_cost_sek) as total_cost_sek,
                          SUM(total_co2_kg) as total_co2_kg,
                          AVG(fleet_utilization_pct) as avg_fleet_util_pct,
                          MIN(sim_day) as min_sim_day,
                          MAX(sim_day) as max_sim_day,
                          SUM(CASE WHEN n_matches > 0 THEN 1 ELSE 0 END) as cycles_with_matches,
                          AVG(total_tons) as avg_tons_per_cycle,
                          AVG(total_cost_sek) as avg_cost_per_cycle,
                          AVG(total_co2_kg) as avg_co2_per_cycle
                   FROM optimization_cycles"""
            ).fetchone()

            result = dict(row) if row else {}
            # SQLite SUM/AVG 返回 None 在空表上 → 设为 0.0
            for k in ("total_tons", "total_cost_sek", "total_co2_kg",
                      "avg_tons_per_cycle", "avg_cost_per_cycle", "avg_co2_per_cycle"):
                if result.get(k) is None:
                    result[k] = 0.0

        # 防御性 defaults
        for k, v in {
            "n_cycles": 0,
            "total_tons": 0.0,
            "total_cost_sek": 0.0,
            "total_co2_kg": 0.0,
            "avg_fleet_util_pct": None,
            "min_sim_day": None,
            "max_sim_day": None,
            "cycles_with_matches": 0,
            "avg_tons_per_cycle": None,
            "avg_cost_per_cycle": None,
            "avg_co2_per_cycle": None,
        }.items():
            result.setdefault(k, v)

        # 衍生指标: cost/co2 per ton (避免除零)
        tons = result["total_tons"] or 0.0
        result["cost_per_ton_sek"] = (
            round(result["total_cost_sek"] / tons, 2) if tons > 0 else None
        )
        result["co2_per_ton_kg"] = (
            round(result["total_co2_kg"] / tons, 2) if tons > 0 else None
        )

        # round 浮点数便于前端展示
        for k in ("total_tons", "total_cost_sek", "total_co2_kg",
                  "avg_tons_per_cycle", "avg_cost_per_cycle", "avg_co2_per_cycle"):
            if result.get(k) is not None:
                result[k] = round(result[k], 2)
        if result.get("avg_fleet_util_pct") is not None:
            result["avg_fleet_util_pct"] = round(result["avg_fleet_util_pct"], 2)

        # match_rate: 有 match 的 cycle 占总 cycle 的比例
        n = result["n_cycles"] or 0
        result["match_rate_pct"] = (
            round(100.0 * result["cycles_with_matches"] / n, 1) if n > 0 else None
        )

        return result
