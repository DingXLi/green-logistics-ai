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
    wall_duration_ms INTEGER
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
    ) -> None:
        """开始一个新周期（先写一行 cycle 记录）"""
        with self._conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO optimization_cycles
                   (cycle_id, sim_day, sim_hour, activity_factor,
                    wall_timestamp, n_supply_offers, n_demand_requests)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (cycle_id, sim_day, sim_hour, activity_factor,
                 datetime.now().isoformat(), n_supply_offers, n_demand_requests)
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

    def get_summary(self) -> Dict[str, Any]:
        """全局统计 summary"""
        with self._conn() as conn:
            row = conn.execute(
                """SELECT COUNT(*) as n_cycles,
                          SUM(total_tons) as total_tons,
                          SUM(total_cost_sek) as total_cost_sek,
                          SUM(total_co2_kg) as total_co2_kg,
                          AVG(fleet_utilization_pct) as avg_utilization
                   FROM optimization_cycles"""
            ).fetchone()
            return dict(row) if row else {}
