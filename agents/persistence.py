"""
SQLite 持久化层 (Persistence Layer)

将每次优化周期的结果落到 SQLite，方便：
1. 实验复现（同一 seed + 历史数据 → 同一结果）
2. 论文 figure 绘制（直接 SQL 拉 KPI 时间序列）
3. 失败回放（crash 后从断点继续）

Schema:
- optimization_cycles: 1 行 / 周期（KPI）
- supply_offers: 1 行 / 供应点 / 周期
- demand_requests: 1 行 / 需求点 / 周期
- matches: 1 行 / 匹配 / 周期
- routes: 1 行 / 车辆路径 / 周期
"""

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional
from contextlib import contextmanager

from loguru import logger


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS optimization_cycles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_id TEXT NOT NULL UNIQUE,
    sim_day INTEGER NOT NULL,
    sim_hour INTEGER NOT NULL,
    activity_factor REAL,
    wall_timestamp TEXT NOT NULL,
    n_supply_offers INTEGER DEFAULT 0,
    n_demand_requests INTEGER DEFAULT 0,
    n_matches INTEGER DEFAULT 0,
    total_tons REAL DEFAULT 0,
    total_cost_sek REAL DEFAULT 0,
    total_co2_kg REAL DEFAULT 0,
    total_distance_km REAL DEFAULT 0,
    n_vehicles_used INTEGER DEFAULT 0,
    n_vehicles_available INTEGER DEFAULT 0,
    fleet_utilization_pct REAL DEFAULT 0,
    solver_status TEXT,
    wall_duration_ms INTEGER,
    -- iter #4: 季节扰动跟踪
    -- seasonal_factor_avg = 该 cycle 所有 supply 点的 seasonal_multiplier 平均
    -- seasonal_month = (sim_day // 30) % 12 + 1 (1-12)
    seasonal_factor_avg REAL DEFAULT 1.0,
    seasonal_month INTEGER DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_cycles_day ON optimization_cycles(sim_day);

CREATE TABLE IF NOT EXISTS supply_offers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_id TEXT NOT NULL,
    supply_id TEXT NOT NULL,
    location_lat REAL,
    location_lon REAL,
    material_type TEXT,
    available_tons REAL,
    moisture_percent REAL,
    quality_score REAL,
    FOREIGN KEY(cycle_id) REFERENCES optimization_cycles(cycle_id)
);

CREATE INDEX IF NOT EXISTS idx_supply_cycle ON supply_offers(cycle_id);

CREATE TABLE IF NOT EXISTS demand_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_id TEXT NOT NULL,
    demand_id TEXT NOT NULL,
    name TEXT,
    location_lat REAL,
    location_lon REAL,
    material_type TEXT,
    required_tons REAL,
    priority TEXT,
    deadline TEXT,
    FOREIGN KEY(cycle_id) REFERENCES optimization_cycles(cycle_id)
);

CREATE INDEX IF NOT EXISTS idx_demand_cycle ON demand_requests(cycle_id);

CREATE TABLE IF NOT EXISTS matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_id TEXT NOT NULL,
    supply_id TEXT,
    demand_id TEXT,
    material_type TEXT,
    tons REAL,
    distance_km REAL,
    estimated_profit_sek REAL,
    FOREIGN KEY(cycle_id) REFERENCES optimization_cycles(cycle_id)
);

CREATE INDEX IF NOT EXISTS idx_matches_cycle ON matches(cycle_id);

CREATE TABLE IF NOT EXISTS routes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_id TEXT NOT NULL,
    vehicle_id TEXT,
    stops_json TEXT,
    distance_km REAL,
    duration_hours REAL,
    cost_sek REAL,
    co2_kg REAL,
    FOREIGN KEY(cycle_id) REFERENCES optimization_cycles(cycle_id)
);

CREATE INDEX IF NOT EXISTS idx_routes_cycle ON routes(cycle_id);

CREATE TABLE IF NOT EXISTS llm_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_id TEXT NOT NULL,
    sim_day INTEGER NOT NULL,
    sim_hour INTEGER,
    decision_type TEXT NOT NULL,        -- 'demand_prediction' | 'supply_prediction'
    target_id TEXT NOT NULL,            -- 'DEM001' | 'SUP000'
    target_type TEXT,                    -- 'demand_point' | 'supply_point'
    multiplier REAL,                     -- LLM 提供的 next-day multiplier (0.3-1.8)
    trend TEXT,                          -- 'rising' | 'stable' | 'falling'
    confidence REAL,                     -- 0-1
    reason TEXT,                         -- 1 句解释
    source TEXT,                         -- 'llm' | 'fallback'
    raw_json TEXT,                       -- 完整 LLM 响应 (调试用)
    wall_timestamp TEXT,
    FOREIGN KEY(cycle_id) REFERENCES optimization_cycles(cycle_id)
);

CREATE INDEX IF NOT EXISTS idx_llm_cycle ON llm_decisions(cycle_id);
CREATE INDEX IF NOT EXISTS idx_llm_day ON llm_decisions(sim_day);
CREATE INDEX IF NOT EXISTS idx_llm_type ON llm_decisions(decision_type);
"""


class Persistence:
    """
    SQLite 持久化封装

    Usage
    -----
    >>> db = Persistence("data/simulation.db")
    >>> db.init_schema()
    >>> db.begin_cycle("OPT0001", day=0, hour=0)
    >>> db.record_supply("OPT0001", {...})
    >>> db.record_match("OPT0001", {...})
    >>> db.commit_cycle("OPT0001", kpi={...})
    """

    def __init__(self, db_path: str = "data/simulation.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()
        logger.info(f"持久化层初始化：{self.db_path}")

    @contextmanager
    def _conn(self):
        """上下文管理 connection（自动 commit/rollback）"""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._conn() as conn:
            conn.executescript(SCHEMA_SQL)
            # iter #4: 兼容旧 DB, 如 seasonal_factor_avg / seasonal_month 列缺失则 ALTER TABLE
            self._migrate_add_seasonal_columns(conn)

    def _migrate_add_seasonal_columns(self, conn) -> None:
        """为旧 DB 加 seasonal_factor_avg + seasonal_month 列。

        使用 PRAGMA table_info 检查存在性, 再决定是否 ALTER。
        如果多 process 同时初始化, ALTER TABLE 可能会偶发报错 — 用 try 宽容。
        """
        existing = {
            row[1]
            for row in conn.execute(
                "PRAGMA table_info(optimization_cycles)"
            ).fetchall()
        }
        if "seasonal_factor_avg" not in existing:
            try:
                conn.execute(
                    "ALTER TABLE optimization_cycles "
                    "ADD COLUMN seasonal_factor_avg REAL DEFAULT 1.0"
                )
            except Exception:
                pass
        if "seasonal_month" not in existing:
            try:
                conn.execute(
                    "ALTER TABLE optimization_cycles "
                    "ADD COLUMN seasonal_month INTEGER DEFAULT 1"
                )
            except Exception:
                pass

    # ------------------------------------------------------------
    # 周期级 KPI
    # ------------------------------------------------------------

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
    ) -> None:
        """开始一个新周期（先写一行 cycle 记录）

        Args:
            seasonal_factor_avg: 本 cycle 所有 supply 点的 seasonal_multiplier
                                  平均值 (e.g. 1.4 if summer peak)
            seasonal_month: 1-12 对应该 cycle 的月份
        """
        with self._conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO optimization_cycles
                   (cycle_id, sim_day, sim_hour, activity_factor,
                    wall_timestamp, n_supply_offers, n_demand_requests,
                    seasonal_factor_avg, seasonal_month)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (cycle_id, sim_day, sim_hour, activity_factor,
                 datetime.now().isoformat(), n_supply_offers, n_demand_requests,
                 seasonal_factor_avg, seasonal_month)
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

    # ------------------------------------------------------------
    # 记录子数据
    # ------------------------------------------------------------

    def record_supply(self, cycle_id: str, supply: Dict[str, Any]) -> None:
        with self._conn() as conn:
            loc = supply.get("location", {})
            conn.execute(
                """INSERT INTO supply_offers
                   (cycle_id, supply_id, location_lat, location_lon,
                    material_type, available_tons, moisture_percent, quality_score)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    cycle_id,
                    supply.get("agent_id") or supply.get("supply_id"),
                    loc.get("lat"),
                    loc.get("lon"),
                    supply.get("material_type"),
                    supply.get("available_tons", supply.get("weight_tons", 0)),
                    supply.get("moisture_percent"),
                    supply.get("quality_score"),
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

    # ------------------------------------------------------------
    # 查询（论文 / dashboard 用）
    # ------------------------------------------------------------

    # ------------------------------------------------------------
    # 记录 LLM 决策
    # ------------------------------------------------------------

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
    ) -> List[Dict[str, Any]]:
        """查询 LLM 决策。可按类型/目标过滤。"""
        sql = "SELECT * FROM llm_decisions"
        clauses: List[str] = []
        params: List[Any] = []
        if decision_type:
            clauses.append("decision_type = ?")
            params.append(decision_type)
        if target_id:
            clauses.append("target_id = ?")
            params.append(target_id)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY sim_day, id"
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

    # ------------------------------------------------------------
    # 查询（论文 / dashboard 用）
    # ------------------------------------------------------------

    def get_recent_cycles(self, limit: int = 10) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT * FROM optimization_cycles
                   ORDER BY id DESC LIMIT ?""",
                (limit,)
            ).fetchall()
            return [dict(r) for r in rows]

    def get_kpi_timeseries(self) -> List[Dict[str, Any]]:
        """KPI 时间序列（按 sim_day 排序）"""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT sim_day,
                          SUM(total_tons) as tons,
                          SUM(total_cost_sek) as cost_sek,
                          SUM(total_co2_kg) as co2_kg,
                          AVG(fleet_utilization_pct) as util_pct,
                          SUM(n_matches) as matches
                   FROM optimization_cycles
                   GROUP BY sim_day
                   ORDER BY sim_day ASC"""
            ).fetchall()
            return [dict(r) for r in rows]

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
