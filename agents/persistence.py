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
import csv
import io
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
        limit: Optional[int] = None,
        sim_day_min: Optional[int] = None,
        sim_day_max: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """查询 LLM 决策。可按类型/目标/时间/limit 过滤 (iter #18)。

        Args:
            decision_type: 'demand_prediction' | 'supply_prediction'
            target_id: 'DEM001' | 'SUP000'
            limit: 最多返多少行 (None = 不限)
            sim_day_min: 起始 sim_day (含)
            sim_day_max: 结束 sim_day (含)
        """
        sql = "SELECT * FROM llm_decisions"
        clauses: List[str] = []
        params: List[Any] = []
        if decision_type:
            clauses.append("decision_type = ?")
            params.append(decision_type)
        if target_id:
            clauses.append("target_id = ?")
            params.append(target_id)
        if sim_day_min is not None:
            clauses.append("sim_day >= ?")
            params.append(sim_day_min)
        if sim_day_max is not None:
            clauses.append("sim_day <= ?")
            params.append(sim_day_max)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY sim_day DESC, id DESC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
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

    def get_kpi_timeseries(self, since_sim_day: Optional[int] = None,
                         until_sim_day: Optional[int] = None) -> List[Dict[str, Any]]:
        """KPI 时间序列 (iter #8 + iter #18 时间窗口) — 按 sim_day 聚合。

        Args (iter #18 新增):
            since_sim_day: 起始 sim_day (含)
            until_sim_day: 结束 sim_day (含)
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
            rows = conn.execute(
                f"""SELECT sim_day,
                          SUM(total_tons) as tons,
                          SUM(total_cost_sek) as cost_sek,
                          SUM(total_co2_kg) as co2_kg,
                          AVG(fleet_utilization_pct) as util_pct,
                          SUM(n_matches) as matches,
                          COUNT(*) as n_cycles_in_day
                   FROM optimization_cycles
                   {where_sql}
                   GROUP BY sim_day
                   ORDER BY sim_day ASC""",
                params,
            ).fetchall()
            result = []
            for r in rows:
                d = dict(r)
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

    def export_cycles_csv(self, limit: int = 1000) -> str:
        """
        Export cycle history as CSV string (iter #11) — 让用户下载 KPI 数据。

        Columns (15):
            cycle_id, sim_day, sim_hour, wall_timestamp, activity_factor,
            n_supply_offers, n_demand_requests, n_matches, n_routes,
            total_tons, total_cost_sek, total_co2_kg, total_distance_km,
            n_vehicles_used, n_vehicles_available, fleet_utilization_pct,
            solver_status, wall_duration_ms, seasonal_factor_avg, seasonal_month

        Returns: CSV string with header + rows (UTF-8).
        """
        rows = self.get_cycle_history(limit=limit)
        if not rows:
            return "cycle_id,sim_day,sim_hour,wall_timestamp,activity_factor,n_supply_offers,n_demand_requests,n_matches,total_tons,total_cost_sek,total_co2_kg,total_distance_km,n_vehicles_used,n_vehicles_available,fleet_utilization_pct,solver_status,wall_duration_ms,seasonal_factor_avg,seasonal_month\n"

        # 定义稳定 column 顺序 (避免 dict order 不一致)
        columns = [
            "cycle_id", "sim_day", "sim_hour", "wall_timestamp",
            "activity_factor", "n_supply_offers", "n_demand_requests",
            "n_matches", "total_tons", "total_cost_sek", "total_co2_kg",
            "total_distance_km", "n_vehicles_used", "n_vehicles_available",
            "fleet_utilization_pct", "solver_status", "wall_duration_ms",
            "seasonal_factor_avg", "seasonal_month",
        ]

        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            writer.writerow(r)
        return buf.getvalue()

    def export_supplies_csv(self, limit: int = 10000) -> str:
        """
        Export supply_offers as CSV (iter #17) — analyst-friendly.

        Columns (10):
            cycle_id, supply_id, material_type, location_lat, location_lon,
            available_tons, moisture_percent, quality_score, sim_day, sim_hour

        Args:
            limit: max rows to export (default 10000)
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
        writer = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            writer.writerow(dict(r))
        return buf.getvalue()

    def export_matches_csv(self, limit: int = 10000) -> str:
        """
        Export matches as CSV (iter #17) — analyst-friendly.

        Columns (8):
            cycle_id, supply_id, demand_id, material_type,
            tons, distance_km, estimated_profit_sek, sim_day

        Args:
            limit: max rows (default 10000)
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
        writer = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            writer.writerow(dict(r))
        return buf.getvalue()

    def export_routes_csv(self, limit: int = 10000) -> str:
        """
        Export routes as CSV (iter #17) — analyst-friendly.

        Columns (8):
            cycle_id, vehicle_id, distance_km, duration_hours,
            cost_sek, co2_kg, stops_count, sim_day

        Args:
            limit: max rows (default 10000)
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
        writer = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            writer.writerow(dict(r))
        return buf.getvalue()

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

    def vacuum(self) -> Dict[str, Any]:
        """
        VACUUM + ANALYZE (iter #16) — SQLite 性能维护。

        VACUUM: rebuild DB file, 释放碎片空间, 减小文件体积
        ANALYZE: 收集统计信息, 帮助 query planner 选最优 index

        Returns:
            {action: "vacuum_analyze", size_before_bytes, size_after_bytes,
             reclaimed_bytes, success: bool}
        """
        size_before = self.db_path.stat().st_size if self.db_path.exists() else 0
        try:
            with self._conn() as conn:
                conn.execute("VACUUM")
                conn.execute("ANALYZE")
            size_after = self.db_path.stat().st_size if self.db_path.exists() else 0
            return {
                "action": "vacuum_analyze",
                "size_before_bytes": size_before,
                "size_after_bytes": size_after,
                "reclaimed_bytes": max(0, size_before - size_after),
                "reclaimed_pct": round((1 - size_after / size_before) * 100, 2) if size_before > 0 else 0,
                "success": True,
            }
        except Exception as e:
            logger.error(f"VACUUM/ANALYZE failed: {e}")
            return {
                "action": "vacuum_analyze",
                "size_before_bytes": size_before,
                "size_after_bytes": size_before,
                "reclaimed_bytes": 0,
                "reclaimed_pct": 0,
                "success": False,
                "error": str(e),
            }
