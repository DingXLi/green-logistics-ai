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
    seasonal_month INTEGER DEFAULT 1,
    -- iter #38: 季节扰动效果分析
    -- base_seasonal_factor_avg = 同一 cycle 所有 supply 点的 baseline (无扰动) 平均
    -- seasonal_factor_avg 仍然是 effective 值 (含扰动)
    -- 两者之差 = 扰动对 supply 的影响
    base_seasonal_factor_avg REAL DEFAULT 1.0,
    perturbation_count INTEGER DEFAULT 0,
    perturbation_total_multiplier REAL DEFAULT 1.0
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
    -- iter #38: 季节扰动效果 (split baseline vs effective for analysis)
    base_seasonal_multiplier REAL DEFAULT 1.0,
    seasonal_multiplier REAL DEFAULT 1.0,
    perturbation_applied INTEGER DEFAULT 0,
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

-- iter #35: forecast method preferences (最佳 method 持久化)
CREATE TABLE IF NOT EXISTS forecast_method_prefs (
    metric TEXT PRIMARY KEY,            -- 'cost_sek' | 'co2_kg' | 'util_pct' | 'matches'
    best_method TEXT NOT NULL,         -- 'linear' | 'moving_average' | 'exponential_smoothing'
    r_squared REAL,                     -- 选择该 method 的 R² (质量指标)
    history_n INTEGER,                  -- 评估时使用的 history_n
    n_samples INTEGER DEFAULT 1,        -- 累计更新次数 (用于 confidence)
    updated_at TEXT NOT NULL            -- ISO timestamp
);

-- iter #37: 季节性扰动 (real-time shocks that overlay the static SEASONAL_FACTORS)
-- One row = one perturbation rule. Multiple rules may overlap (multiplicative).
CREATE TABLE IF NOT EXISTS seasonal_perturbations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    label TEXT NOT NULL,                -- 人读标签, e.g. "Christmas paper surge"
    start_sim_day INTEGER NOT NULL,     -- 包含
    end_sim_day INTEGER NOT NULL,       -- 包含
    material_type TEXT NOT NULL,        -- 'concrete' | 'metal_scrap' | ... | '*' (all)
    multiplier REAL NOT NULL,           -- 叠加到 base seasonal factor (乘)
    active INTEGER NOT NULL DEFAULT 1,  -- 0 = 软删除 (保留历史可审计)
    created_at TEXT NOT NULL            -- ISO timestamp
);
CREATE INDEX IF NOT EXISTS idx_perturb_window
    ON seasonal_perturbations(active, start_sim_day, end_sim_day);

-- iter #42: Forecast calibration — track predictions vs actuals
-- Recorded when /api/persistence/forecast runs. actual_value / error
-- are backfilled once the forecast sim_day arrives in optimization_cycles.
CREATE TABLE IF NOT EXISTS forecast_predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    metric TEXT NOT NULL,                 -- 'cost_sek' | 'co2_kg' | 'util_pct' | 'matches'
    method TEXT NOT NULL,                 -- 'linear' | 'moving_average' | 'exponential_smoothing'
    forecast_sim_day INTEGER NOT NULL,    -- 预测的目标 sim_day
    forecast_value REAL NOT NULL,         -- 预测值
    actual_value REAL,                    -- 实际值 (后补, NULL if sim_day not yet reached)
    error REAL,                           -- actual - forecast (后补)
    abs_pct_error REAL,                   -- |actual-forecast|/|actual|*100 (后补)
    created_at_sim_day INTEGER NOT NULL,  -- 预测时已知 last_sim_day
    recorded_at TEXT NOT NULL             -- ISO timestamp
);
CREATE INDEX IF NOT EXISTS idx_fcst_pred_metric_day
    ON forecast_predictions(metric, forecast_sim_day);
CREATE INDEX IF NOT EXISTS idx_fcst_pred_actual
    ON forecast_predictions(actual_value);

-- iter #42: DB maintenance audit log
-- One row per VACUUM/ANALYZE run. Used to compute
-- "should auto-vacuum?" recommendation based on time / size / cycles.
CREATE TABLE IF NOT EXISTS db_maintenance_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL,                 -- 'vacuum_analyze'
    size_before_bytes INTEGER,
    size_after_bytes INTEGER,
    reclaimed_bytes INTEGER,
    triggered_by TEXT,                    -- 'auto' | 'manual' | 'scheduled'
    ran_at TEXT NOT NULL                  -- ISO timestamp
);
CREATE INDEX IF NOT EXISTS idx_maint_log_ran_at ON db_maintenance_log(ran_at);

-- iter #44: Runtime config persistence (overrides that survive restart)
-- One row per key. Loaded into in-memory _runtime_config at startup.
CREATE TABLE IF NOT EXISTS runtime_config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,           -- JSON-encoded value (so we can store any type)
    updated_at TEXT NOT NULL
);
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
        # iter #38: perturbation impact tracking columns
        if "base_seasonal_factor_avg" not in existing:
            try:
                conn.execute(
                    "ALTER TABLE optimization_cycles "
                    "ADD COLUMN base_seasonal_factor_avg REAL DEFAULT 1.0"
                )
            except Exception:
                pass
        if "perturbation_count" not in existing:
            try:
                conn.execute(
                    "ALTER TABLE optimization_cycles "
                    "ADD COLUMN perturbation_count INTEGER DEFAULT 0"
                )
            except Exception:
                pass
        if "perturbation_total_multiplier" not in existing:
            try:
                conn.execute(
                    "ALTER TABLE optimization_cycles "
                    "ADD COLUMN perturbation_total_multiplier REAL DEFAULT 1.0"
                )
            except Exception:
                pass
        # iter #38: supply_offers perturbation tracking
        supply_existing = {
            row[1]
            for row in conn.execute("PRAGMA table_info(supply_offers)").fetchall()
        }
        for col, sqltype, default in [
            ("base_seasonal_multiplier", "REAL", "1.0"),
            ("seasonal_multiplier", "REAL", "1.0"),
            ("perturbation_applied", "INTEGER", "0"),
        ]:
            if col not in supply_existing:
                try:
                    conn.execute(
                        f"ALTER TABLE supply_offers ADD COLUMN {col} {sqltype} DEFAULT {default}"
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

    # ------------------------------------------------------------
    # 记录子数据
    # ------------------------------------------------------------

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

    def get_llm_cost_timeseries(self, since_sim_day: Optional[int] = None,
                                 until_sim_day: Optional[int] = None) -> List[Dict[str, Any]]:
        """iter #28: LLM cost 时间序列 (按 sim_day 聚合, from llm_decisions table)。

        Returns: [{sim_day, n_decisions, llm_n, fallback_n, source, total_calls,
                   avg_confidence, avg_multiplier, ...}, ...]

        Note: llm_decisions 表不直接存 cost_usd — 近似用 n_decisions 作为 proxy
        (iter #22 LLM tracker 有精确 cost, 但那是 in-memory; 此 endpoint 从 DB 拿)
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
                          COUNT(*) as n_decisions,
                          SUM(CASE WHEN source='llm' THEN 1 ELSE 0 END) as llm_n,
                          SUM(CASE WHEN source='fallback' THEN 1 ELSE 0 END) as fallback_n,
                          ROUND(AVG(multiplier), 3) as avg_multiplier,
                          ROUND(AVG(confidence), 3) as avg_confidence,
                          ROUND(AVG(CASE WHEN source='llm' THEN 1 ELSE 0 END) * 100, 2) as llm_success_rate_pct
                   FROM llm_decisions
                   {where_sql}
                   GROUP BY sim_day
                   ORDER BY sim_day""",
                params,
            ).fetchall()
            return [dict(r) for r in rows]

    def get_llm_decision_targets(
        self,
        decision_type: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """
        iter #48: List unique LLM call targets with stats.

        Groups llm_decisions by target_id, returns per-target stats:
        - decision_type
        - n_calls
        - n_real_llm / n_fallback
        - last_called_sim_day
        - avg_multiplier (across calls)
        - avg_confidence

        Args:
            decision_type: optional filter ('demand_prediction' / etc.)
            limit: max targets to return (default 50)

        Returns:
            [{
              target_id, decision_type, target_type,
              n_calls, n_real_llm, n_fallback,
              last_called_sim_day, first_called_sim_day,
              avg_multiplier, avg_confidence,
            }, ...]
            Sorted by n_calls DESC (most-called targets first).
        """
        where_clauses: List[str] = []
        params: List[Any] = []
        if decision_type:
            where_clauses.append("decision_type = ?")
            params.append(decision_type)
        where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

        with self._conn() as conn:
            rows = conn.execute(
                f"""SELECT target_id, decision_type, target_type,
                          COUNT(*) AS n_calls,
                          SUM(CASE WHEN source='llm' THEN 1 ELSE 0 END) AS n_real_llm,
                          SUM(CASE WHEN source='fallback' THEN 1 ELSE 0 END) AS n_fallback,
                          MAX(sim_day) AS last_called_sim_day,
                          MIN(sim_day) AS first_called_sim_day,
                          ROUND(AVG(multiplier), 3) AS avg_multiplier,
                          ROUND(AVG(confidence), 3) AS avg_confidence
                   FROM llm_decisions
                   {where_sql}
                   GROUP BY target_id, decision_type
                   ORDER BY n_calls DESC
                   LIMIT ?""",
                params + [int(limit)],
            ).fetchall()
        return [dict(r) for r in rows]

    def get_llm_cost_by_decision_type(
        self,
        since_sim_day: Optional[int] = None,
        until_sim_day: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        iter #48: LLM usage breakdown by decision_type.

        Aggregates llm_decisions by decision_type ('demand_prediction' /
        'supply_prediction') to show which type uses LLM the most.

        Returns:
            [{
              decision_type: str,
              n_total: int,
              n_llm: int,
              n_fallback: int,
              llm_rate_pct: float,
              avg_multiplier: float | None,
              avg_confidence: float | None,
              n_unique_targets: int,
              first_decision_sim_day: int | None,
              last_decision_sim_day: int | None,
            }, ...]
            Sorted by n_total DESC.
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

        with self._conn() as conn:
            rows = conn.execute(
                f"""SELECT decision_type,
                          COUNT(*) AS n_total,
                          SUM(CASE WHEN source='llm' THEN 1 ELSE 0 END) AS n_llm,
                          SUM(CASE WHEN source='fallback' THEN 1 ELSE 0 END) AS n_fallback,
                          ROUND(AVG(multiplier), 3) AS avg_multiplier,
                          ROUND(AVG(confidence), 3) AS avg_confidence,
                          COUNT(DISTINCT target_id) AS n_unique_targets,
                          MIN(sim_day) AS first_decision_sim_day,
                          MAX(sim_day) AS last_decision_sim_day
                   FROM llm_decisions
                   {where_sql}
                   GROUP BY decision_type
                   ORDER BY n_total DESC""",
                params,
            ).fetchall()

        results = []
        for r in rows:
            d = dict(r)
            n_total = d.get("n_total") or 0
            n_llm = d.get("n_llm") or 0
            d["llm_rate_pct"] = round(100 * n_llm / max(n_total, 1), 2)
            results.append(d)
        return results

    def forecast_llm_cost(
        self,
        horizon: int = 7,
        history_n: int = 14,
        method: str = "linear",
        since_sim_day: Optional[int] = None,
        until_sim_day: Optional[int] = None,
    ) -> Dict[str, Any]:
        """iter #29: 预测 LLM usage/cost 时间序列。

        预测 5 个字段: n_decisions, llm_n, fallback_n,
        avg_multiplier, avg_confidence. 使用与 KPI forecast 相同的
        linear / moving_average / exponential_smoothing 方法。

        Note: 这是 usage forecast, 不是美元 cost forecast。精确 cost 仍以
        /api/admin/llm-stats 的 total_cost_usd 为准。
        """
        if horizon < 1 or horizon > 30:
            raise ValueError(f"horizon must be 1-30, got {horizon}")
        if history_n < 2 or history_n > 90:
            raise ValueError(f"history_n must be 2-90, got {history_n}")
        rows = self.get_llm_cost_timeseries(
            since_sim_day=since_sim_day,
            until_sim_day=until_sim_day,
        )
        if len(rows) < 2:
            return {
                "horizon": horizon,
                "history_n": history_n,
                "method": method,
                "last_sim_day": rows[-1]["sim_day"] if rows else None,
                "forecast_sim_days": [],
                "metrics": {},
                "note": "need at least 2 historical sim_days for LLM cost forecast",
            }

        try:
            import numpy as np
        except ImportError as e:
            raise ImportError("numpy required for LLM cost forecast") from e

        history = rows[-history_n:]
        sim_days = np.array([r["sim_day"] for r in history], dtype=float)
        forecast_sim_days = list(range(int(sim_days[-1]) + 1, int(sim_days[-1]) + horizon + 1))
        valid_methods = ("linear", "moving_average", "exponential_smoothing")
        if method not in valid_methods:
            raise ValueError(f"method must be one of {valid_methods}, got {method!r}")

        metric_names = ("n_decisions", "llm_n", "fallback_n", "avg_multiplier", "avg_confidence")
        metrics: Dict[str, Any] = {}
        for metric_name in metric_names:
            values = np.array([r.get(metric_name, 0) or 0 for r in history], dtype=float)
            if method == "linear":
                fit = self._fit_linear(sim_days, values)
            elif method == "moving_average":
                fit = self._fit_moving_average(sim_days, values)
            else:
                fit = self._fit_exponential_smoothing(sim_days, values)
            forecast_values = fit["forecast_values_fn"](np.array(forecast_sim_days, dtype=float))
            forecast = []
            for sim_day, value in zip(forecast_sim_days, forecast_values):
                ci_half = fit["ci_half"]
                forecast.append({
                    "sim_day": sim_day,
                    "value": round(float(value), 2),
                    "lower_95": round(float(value) - ci_half, 2),
                    "upper_95": round(float(value) + ci_half, 2),
                    "is_forecast": True,
                })
            metrics[metric_name] = {
                "history": [{"sim_day": r["sim_day"], "value": r.get(metric_name, 0) or 0,
                             "is_forecast": False} for r in history],
                "forecast": forecast,
                "trend": fit["trend"],
                "method": method,
                "r_squared": round(fit["r_squared"], 4),
                "residual_std": round(fit["residual_std"], 2),
                "mean_value": round(float(np.mean(values)), 2),
                **fit["method_meta"],
            }

        return {
            "horizon": horizon,
            "history_n": history_n,
            "method": method,
            "last_sim_day": int(sim_days[-1]),
            "forecast_sim_days": forecast_sim_days,
            "metrics": metrics,
        }

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

    # ============================================
    # iter #28: forecast method fitters
    # Each returns: {slope, forecast_values_fn, ci_half, r_squared,
    #                trend, residual_std, method_meta}
    # ============================================
    def _fit_linear(self, sim_days, values):
        """Linear regression (iter #26 default). y = slope * x + intercept."""
        import numpy as np
        slope, intercept = np.polyfit(sim_days, values, 1)
        # Compute R²
        y_pred_hist = slope * sim_days + intercept
        ss_res = float(np.sum((values - y_pred_hist) ** 2))
        ss_tot = float(np.sum((values - np.mean(values)) ** 2))
        r_squared = 1 - ss_res / ss_tot if ss_tot > 0 else 1.0
        # Residual std
        residuals = values - y_pred_hist
        residual_std = float(np.std(residuals, ddof=1)) if len(residuals) > 1 else 0.0
        ci_half = 1.96 * residual_std
        # Trend
        week_change = slope * 7
        if abs(week_change) < 0.05 * abs(intercept) if intercept else abs(week_change) < 1:
            trend = "flat"
        elif week_change > 0:
            trend = "up"
        else:
            trend = "down"
        return {
            "slope": float(slope),
            "forecast_values_fn": lambda x: slope * x + intercept,
            "ci_half": ci_half,
            "r_squared": r_squared,
            "trend": trend,
            "residual_std": residual_std,
            "method_meta": {"intercept": round(float(intercept), 4)},
        }

    def _fit_moving_average(self, sim_days, values):
        """Moving average (iter #28). Forecast = mean of all values, flat line."""
        import numpy as np
        window_mean = float(np.mean(values))
        window_std = float(np.std(values, ddof=1)) if len(values) > 1 else 0.0
        ci_half = 1.96 * window_std
        # No trend (flat) by definition
        return {
            "slope": 0.0,
            "forecast_values_fn": lambda x: np.full_like(x, window_mean, dtype=float),
            "ci_half": ci_half,
            "r_squared": 0.0,  # Not applicable for moving average
            "trend": "flat",
            "residual_std": window_std,
            "method_meta": {"window_mean": round(window_mean, 2), "window_n": int(len(values))},
        }

    def _fit_exponential_smoothing(self, sim_days, values):
        """Exponential smoothing (iter #28, simple SES, alpha=0.3).

        forecast = final smoothed level (constant for all future points)
        适合近期值更重要 / 不需趋势外推 的场景
        """
        import numpy as np
        alpha = 0.3  # smoothing constant (0-1, 越小越平滑)
        # Compute SES for history
        n = len(values)
        if n == 0:
            smoothed_levels = np.array([0.0])
        else:
            smoothed_levels = np.zeros(n)
            smoothed_levels[0] = float(values[0])
            for i in range(1, n):
                smoothed_levels[i] = alpha * float(values[i]) + (1 - alpha) * smoothed_levels[i - 1]
        final_level = float(smoothed_levels[-1])
        # Residual std based on one-step-ahead errors
        one_step_errors = []
        for i in range(1, n):
            pred = smoothed_levels[i - 1]
            one_step_errors.append(float(values[i]) - pred)
        residual_std = float(np.std(one_step_errors, ddof=1)) if len(one_step_errors) > 1 else 0.0
        ci_half = 1.96 * residual_std
        return {
            "slope": 0.0,
            "forecast_values_fn": lambda x: np.full_like(x, final_level, dtype=float),
            "ci_half": ci_half,
            "r_squared": 0.0,
            "trend": "flat",
            "residual_std": residual_std,
            "method_meta": {"alpha": alpha, "final_level": round(final_level, 2)},
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

    def detect_anomalous_cycles(
        self,
        z_threshold: float = 2.0,
        min_history: int = 5,
        metrics: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        iter #47: Statistical anomaly detection for cycle KPIs.

        Uses z-score (standard deviations from mean) to flag cycles where any
        of {cost_sek, co2_kg, util_pct, distance_km, tons} is significantly
        different from the historical norm. Useful for ops to spot:
        - Sudden cost spikes (potential solver bug, fuel price change)
        - CO2 anomalies (load emission regression)
        - Utilization drops (depot issue, vehicle outage)
        - Distance outliers (routing bug)

        Args:
            z_threshold: how many stddevs to flag (default 2.0 = ~5% extreme)
            min_history: need at least N cycles to compute stats (default 5)
            metrics: which KPIs to check (default all 5)

        Returns:
            [{
              cycle_id, sim_day, sim_hour, wall_timestamp,
              anomalies: [{metric, value, mean, stddev, z_score, severity}, ...],
              max_severity: "high" | "medium" | "low" | "none",
            }, ...]
        """
        if metrics is None:
            metrics = ["total_cost_sek", "total_co2_kg", "fleet_utilization_pct",
                       "total_distance_km", "total_tons"]

        with self._conn() as conn:
            rows = conn.execute(
                """SELECT cycle_id, sim_day, sim_hour, wall_timestamp,
                          total_cost_sek, total_co2_kg, fleet_utilization_pct,
                          total_distance_km, total_tons
                   FROM optimization_cycles
                   ORDER BY sim_day ASC, id ASC"""
            ).fetchall()

        if len(rows) < min_history:
            return []

        # Compute mean + stddev for each metric
        stats: Dict[str, Dict[str, float]] = {}
        for metric in metrics:
            values = [r[metric] for r in rows if r[metric] is not None]
            if not values:
                continue
            n = len(values)
            mean = sum(values) / n
            variance = sum((v - mean) ** 2 for v in values) / n
            stddev = variance ** 0.5
            stats[metric] = {"mean": mean, "stddev": stddev}

        # Flag each cycle
        anomalous = []
        for r in rows:
            cycle_anomalies = []
            for metric in metrics:
                if metric not in stats:
                    continue
                value = r[metric]
                if value is None:
                    continue
                s = stats[metric]
                if s["stddev"] < 1e-9:  # no variance = nothing to flag
                    continue
                z = abs(value - s["mean"]) / s["stddev"]
                if z >= z_threshold:
                    # severity: |z| > 3.0 high, 2.5-3.0 medium, 2.0-2.5 low
                    if z >= 3.0:
                        severity = "high"
                    elif z >= 2.5:
                        severity = "medium"
                    else:
                        severity = "low"
                    cycle_anomalies.append({
                        "metric": metric,
                        "value": round(value, 2),
                        "mean": round(s["mean"], 2),
                        "stddev": round(s["stddev"], 2),
                        "z_score": round(z, 2),
                        "severity": severity,
                    })

            if cycle_anomalies:
                # Compute max severity across this cycle's anomalies
                sev_order = {"high": 3, "medium": 2, "low": 1, "none": 0}
                max_sev = max(cycle_anomalies,
                              key=lambda a: sev_order.get(a["severity"], 0))
                anomalous.append({
                    "cycle_id": r["cycle_id"],
                    "sim_day": r["sim_day"],
                    "sim_hour": r["sim_hour"],
                    "wall_timestamp": r["wall_timestamp"],
                    "anomalies": cycle_anomalies,
                    "max_severity": max_sev["severity"],
                    "n_anomalies": len(cycle_anomalies),
                })

        return anomalous

    def vacuum(self, triggered_by: str = "manual") -> Dict[str, Any]:
        """
        VACUUM + ANALYZE (iter #16 + iter #42 audit log) — SQLite 性能维护。

        VACUUM: rebuild DB file, 释放碎片空间, 减小文件体积
        ANALYZE: 收集统计信息, 帮助 query planner 选最优 index

        Args:
            triggered_by: 'manual' (default) | 'auto' | 'scheduled'
                          for audit log only.

        Returns:
            {action, size_before_bytes, size_after_bytes,
             reclaimed_bytes, reclaimed_pct, success, triggered_by, ran_at}
        """
        size_before = self.db_path.stat().st_size if self.db_path.exists() else 0
        ran_at = datetime.now().isoformat()
        try:
            with self._conn() as conn:
                conn.execute("VACUUM")
                conn.execute("ANALYZE")
                # iter #42: log to maintenance log table
                conn.execute(
                    """INSERT INTO db_maintenance_log
                       (action, size_before_bytes, size_after_bytes,
                        reclaimed_bytes, triggered_by, ran_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        "vacuum_analyze",
                        size_before,
                        -1,  # placeholder; updated after VACUUM completes
                        0,
                        triggered_by,
                        ran_at,
                    ),
                )
            size_after = self.db_path.stat().st_size if self.db_path.exists() else 0
            reclaimed = max(0, size_before - size_after)
            with self._conn() as conn:
                # Update the log row with actual size_after
                conn.execute(
                    """UPDATE db_maintenance_log
                       SET size_after_bytes = ?, reclaimed_bytes = ?
                       WHERE ran_at = ?""",
                    (size_after, reclaimed, ran_at),
                )
            return {
                "action": "vacuum_analyze",
                "size_before_bytes": size_before,
                "size_after_bytes": size_after,
                "reclaimed_bytes": reclaimed,
                "reclaimed_pct": round((1 - size_after / size_before) * 100, 2) if size_before > 0 else 0,
                "success": True,
                "triggered_by": triggered_by,
                "ran_at": ran_at,
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
                "triggered_by": triggered_by,
                "ran_at": ran_at,
            }

    def should_auto_vacuum(self) -> Dict[str, Any]:
        """
        iter #42: Auto-vacuum recommendation.

        Heuristics:
        - DB size grown > 30% since last vacuum
        - More than 1000 cycles since last vacuum
        - More than 7 days since last vacuum
        - First vacuum ever (no log rows)

        Returns:
            {
              should_vacuum: bool,
              reasons: [str, ...],
              stats: {
                db_size_bytes, db_size_mb,
                cycles_since_last_vacuum, days_since_last_vacuum,
                size_growth_pct_since_last_vacuum,
                last_vacuum_at, total_maintenance_runs,
              }
            }
        """
        # Get current DB size
        size_bytes = self.db_path.stat().st_size if self.db_path.exists() else 0

        # Get last vacuum info
        with self._conn() as conn:
            last = conn.execute(
                """SELECT ran_at, size_before_bytes, size_after_bytes
                   FROM db_maintenance_log
                   WHERE action = 'vacuum_analyze'
                   ORDER BY ran_at DESC
                   LIMIT 1"""
            ).fetchone()
            total_runs = conn.execute(
                "SELECT COUNT(*) FROM db_maintenance_log WHERE action = 'vacuum_analyze'"
            ).fetchone()[0]
            cycles_since = conn.execute(
                "SELECT COUNT(*) FROM optimization_cycles"
            ).fetchone()[0]

        reasons: List[str] = []
        last_vacuum_at = last["ran_at"] if last else None
        size_after_last = last["size_after_bytes"] if last else None

        # Heuristic 1: First vacuum ever
        if last is None:
            reasons.append("No vacuum has been run yet (first maintenance)")

        # Heuristic 2: Size growth > 30%
        if size_after_last and size_after_last > 0:
            growth_pct = (size_bytes - size_after_last) / size_after_last * 100
        else:
            growth_pct = None
        if growth_pct is not None and growth_pct > 30:
            reasons.append(
                f"DB size grew {growth_pct:.1f}% since last vacuum "
                f"({size_after_last/1024/1024:.1f}MB → {size_bytes/1024/1024:.1f}MB)"
            )

        # Heuristic 3: cycles since last vacuum > 1000
        # (we don't track "cycles since last vacuum" precisely, so use absolute cycle count)
        if last is None and cycles_since > 1000:
            reasons.append(f"DB has {cycles_since} cycles and never been vacuumed")
        elif last is not None and cycles_since > 1000:
            # Without per-vacuum cycle tracking, just flag if total cycles is large
            reasons.append(
                f"Total cycles: {cycles_since} (recommend vacuum if high churn)"
            )

        # Heuristic 4: more than 7 days since last vacuum
        days_since = None
        if last is not None:
            try:
                last_dt = datetime.fromisoformat(last["ran_at"])
                days_since = (datetime.now() - last_dt).days
                if days_since > 7:
                    reasons.append(f"{days_since} days since last vacuum (max 7 days recommended)")
            except (ValueError, TypeError):
                pass

        should = len(reasons) > 0
        return {
            "should_vacuum": should,
            "reasons": reasons,
            "stats": {
                "db_size_bytes": size_bytes,
                "db_size_mb": round(size_bytes / 1024 / 1024, 3),
                "total_cycles": cycles_since,
                "last_vacuum_at": last_vacuum_at,
                "days_since_last_vacuum": days_since,
                "size_growth_pct_since_last_vacuum": round(growth_pct, 1) if growth_pct is not None else None,
                "size_after_last_vacuum_bytes": size_after_last,
                "total_maintenance_runs": total_runs,
            },
        }

    def get_maintenance_log(self, limit: int = 20) -> List[Dict[str, Any]]:
        """iter #42: return recent maintenance log entries."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT id, action, size_before_bytes, size_after_bytes,
                          reclaimed_bytes, triggered_by, ran_at
                   FROM db_maintenance_log
                   ORDER BY ran_at DESC
                   LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ============================================
    # iter #44: Runtime config persistence
    # ============================================
    # Saves overrides in SQLite so they survive restarts. Loaded on startup.

    def load_runtime_config(self) -> Dict[str, Any]:
        """Load all persisted runtime config overrides (called at startup).

        Returns: {key: parsed_value, ...} — only keys with a persisted row.
        Unknown keys in DB (e.g. after code update that removed the key)
        are ignored, with a debug log warning.
        """
        import json as _json
        overrides: Dict[str, Any] = {}
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT key, value FROM runtime_config"
            ).fetchall()
        for r in rows:
            try:
                overrides[r["key"]] = _json.loads(r["value"])
            except (ValueError, TypeError) as e:
                logger.warning(
                    f"runtime_config: bad value for key {r['key']}: {e}, skipping"
                )
        return overrides

    def save_runtime_config(self, key: str, value: Any) -> Dict[str, Any]:
        """Persist a single key=value to runtime_config.

        Returns: {key, value, applied: bool, updated_at}
        """
        import json as _json
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO runtime_config (key, value, updated_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(key) DO UPDATE SET
                       value = excluded.value,
                       updated_at = excluded.updated_at""",
                (key, _json.dumps(value), datetime.now().isoformat()),
            )
        return {
            "key": key,
            "value": value,
            "applied": True,
            "updated_at": datetime.now().isoformat(),
        }

    def delete_runtime_config(self, key: str) -> bool:
        """Delete a persisted override (returns True if row existed)."""
        with self._conn() as conn:
            cur = conn.execute(
                "DELETE FROM runtime_config WHERE key = ?", (key,)
            )
        return cur.rowcount > 0

    def list_runtime_config_overrides(self) -> List[Dict[str, Any]]:
        """Return all persisted overrides with their string value + parse status."""
        import json as _json
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT key, value, updated_at FROM runtime_config ORDER BY key"
            ).fetchall()
        results = []
        for r in rows:
            try:
                parsed = _json.loads(r["value"])
                parse_ok = True
            except (ValueError, TypeError):
                parsed = None
                parse_ok = False
            results.append({
                "key": r["key"],
                "value": r["value"],  # raw string
                "parsed_value": parsed,
                "parse_ok": parse_ok,
                "updated_at": r["updated_at"],
            })
        return results

    # ============================================
    # iter #35: forecast method preferences (最佳 method 持久化)
    # ============================================

    def save_method_pref(
        self,
        metric: str,
        method: str,
        r_squared: Optional[float] = None,
        history_n: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        保存 (或覆盖) 一个 metric 的最佳 method。

        UPSERT: 如果 metric 已存在, n_samples 累加 + 更新 method / r_squared;
        如果 method 改变, 重置 n_samples = 1 (新选择需要重新积累 confidence)。

        Returns:
            {metric, method, r_squared, history_n, n_samples, updated_at, action}
        """
        valid_methods = ("linear", "moving_average", "exponential_smoothing")
        if method not in valid_methods:
            raise ValueError(
                f"method must be one of {valid_methods}, got {method!r}"
            )
        valid_metrics = ("cost_sek", "co2_kg", "util_pct", "matches")
        if metric not in valid_metrics:
            raise ValueError(
                f"metric must be one of {valid_metrics}, got {metric!r}"
            )
        now = datetime.now().isoformat()
        with self._conn() as conn:
            existing = conn.execute(
                "SELECT best_method, n_samples FROM forecast_method_prefs WHERE metric = ?",
                (metric,),
            ).fetchone()
            if existing is None:
                conn.execute(
                    """INSERT INTO forecast_method_prefs
                       (metric, best_method, r_squared, history_n, n_samples, updated_at)
                       VALUES (?, ?, ?, ?, 1, ?)""",
                    (metric, method, r_squared, history_n, now),
                )
                action = "created"
                n_samples = 1
            else:
                if existing["best_method"] == method:
                    # 同一个 method 被重新选为最佳 → n_samples 累加
                    new_n_samples = (existing["n_samples"] or 1) + 1
                else:
                    # method 改变 → 重置 confidence counter
                    new_n_samples = 1
                conn.execute(
                    """UPDATE forecast_method_prefs
                       SET best_method = ?, r_squared = ?, history_n = ?,
                           n_samples = ?, updated_at = ?
                       WHERE metric = ?""",
                    (method, r_squared, history_n, new_n_samples, now, metric),
                )
                action = "updated_method_changed" if existing["best_method"] != method else "updated"
                n_samples = new_n_samples
        return {
            "metric": metric,
            "method": method,
            "r_squared": r_squared,
            "history_n": history_n,
            "n_samples": n_samples,
            "updated_at": now,
            "action": action,
        }

    def get_method_prefs(self) -> List[Dict[str, Any]]:
        """返回所有 metric 的最佳 method prefs (按 metric 名排序)。"""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT metric, best_method, r_squared, history_n,
                          n_samples, updated_at
                   FROM forecast_method_prefs
                   ORDER BY metric"""
            ).fetchall()
        return [
            {
                "metric": row["metric"],
                "best_method": row["best_method"],
                "r_squared": row["r_squared"],
                "history_n": row["history_n"],
                "n_samples": row["n_samples"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    def get_best_method(self, metric: str) -> Optional[str]:
        """返回 metric 的最佳 method, 或 None (未设置)。"""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT best_method FROM forecast_method_prefs WHERE metric = ?",
                (metric,),
            ).fetchone()
        return row["best_method"] if row else None

    def delete_method_pref(self, metric: str) -> bool:
        """删除单个 metric 的 pref。返回是否真的删了。"""
        with self._conn() as conn:
            cur = conn.execute(
                "DELETE FROM forecast_method_prefs WHERE metric = ?",
                (metric,),
            )
        return cur.rowcount > 0

    def clear_method_prefs(self) -> int:
        """删除所有 prefs。返回删除行数。"""
        with self._conn() as conn:
            cur = conn.execute("DELETE FROM forecast_method_prefs")
        return cur.rowcount

    # ====================================================================
    # iter #42: Forecast calibration — track predicted vs actual
    # ====================================================================
    # Each call to /api/persistence/forecast records predictions.
    # When the predicted sim_day actually arrives in optimization_cycles,
    # backfill actual_value + error.

    def record_forecast_predictions(
        self,
        metric: str,
        method: str,
        predictions: List[Dict[str, Any]],
        created_at_sim_day: int,
    ) -> int:
        """Record predicted values for a (metric, method) combination.

        Args:
            metric: e.g. 'cost_sek'
            method: e.g. 'linear'
            predictions: [{"sim_day": int, "value": float}, ...]
            created_at_sim_day: sim_day at time of prediction (last known cycle)

        Returns:
            int: number of rows inserted (skip predictions already recorded
                 for the same metric+method+forecast_sim_day+created_at_sim_day)
        """
        if not predictions:
            return 0
        rows_inserted = 0
        with self._conn() as conn:
            for p in predictions:
                sim_day = p.get("sim_day")
                value = p.get("value")
                if sim_day is None or value is None:
                    continue
                # Check for duplicate
                existing = conn.execute(
                    """SELECT id FROM forecast_predictions
                       WHERE metric = ? AND method = ?
                         AND forecast_sim_day = ?
                         AND created_at_sim_day = ?""",
                    (metric, method, int(sim_day), int(created_at_sim_day)),
                ).fetchone()
                if existing:
                    continue
                conn.execute(
                    """INSERT INTO forecast_predictions
                       (metric, method, forecast_sim_day, forecast_value,
                        created_at_sim_day, recorded_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        metric,
                        method,
                        int(sim_day),
                        float(value),
                        int(created_at_sim_day),
                        datetime.now().isoformat(),
                    ),
                )
                rows_inserted += 1
        return rows_inserted

    def backfill_forecast_actuals(self) -> int:
        """Compute actual values for predictions whose sim_day has arrived.

        Reads optimization_cycles to find actual cost_sek / co2_kg / matches,
        updates forecast_predictions. Returns count of rows updated.

        Skips predictions whose forecast_sim_day is in the future.
        """
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT id, metric, forecast_sim_day FROM forecast_predictions
                   WHERE actual_value IS NULL
                     AND forecast_sim_day <= (
                       SELECT COALESCE(MAX(sim_day), 0) FROM optimization_cycles
                     )"""
            ).fetchall()
            updated = 0
            for r in rows:
                metric = r["metric"]
                sim_day = r["forecast_sim_day"]
                # Map metric to optimization_cycles column
                metric_to_col = {
                    "cost_sek": "total_cost_sek",
                    "co2_kg": "total_co2_kg",
                    "matches": "n_matches",
                    "util_pct": "fleet_utilization_pct",
                }
                col = metric_to_col.get(metric)
                if col is None:
                    continue
                actual = conn.execute(
                    f"SELECT {col} AS actual FROM optimization_cycles WHERE sim_day = ?",
                    (sim_day,),
                ).fetchone()
                if actual is None:
                    continue
                actual_value = actual["actual"]
                if actual_value is None:
                    continue
                # Get predicted value
                pred_row = conn.execute(
                    "SELECT forecast_value FROM forecast_predictions WHERE id = ?",
                    (r["id"],),
                ).fetchone()
                if pred_row is None:
                    continue
                forecast_value = pred_row["forecast_value"]
                error = actual_value - forecast_value
                abs_pct = (abs(error) / abs(actual_value) * 100) if actual_value else None
                conn.execute(
                    """UPDATE forecast_predictions
                       SET actual_value = ?, error = ?, abs_pct_error = ?
                       WHERE id = ?""",
                    (actual_value, error, abs_pct, r["id"]),
                )
                updated += 1
        return updated

    def get_forecast_calibration(
        self,
        metric: Optional[str] = None,
        method: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Return calibration stats: MAE, MAPE, RMSE per metric/method.

        Args:
            metric: optional filter
            method: optional filter

        Returns:
            {
              overall: {n_predictions, n_evaluated, mae, mape_pct, rmse, bias},
              by_metric: {<metric>: {...same stats...}},
              by_method: {<method>: {...same stats...}},
              by_metric_method: {<metric>: {<method>: {...stats...}}},
            }
        """
        with self._conn() as conn:
            where_clauses = ["actual_value IS NOT NULL", "error IS NOT NULL"]
            params: List[Any] = []
            if metric:
                where_clauses.append("metric = ?")
                params.append(metric)
            if method:
                where_clauses.append("method = ?")
                params.append(method)
            where_sql = " AND ".join(where_clauses)

            rows = conn.execute(
                f"SELECT metric, method, forecast_value, actual_value, error, abs_pct_error "
                f"FROM forecast_predictions WHERE {where_sql}",
                params,
            ).fetchall()

            def _stats(rs: List[Any]) -> Dict[str, Any]:
                if not rs:
                    return {
                        "n_evaluated": 0, "mae": None, "rmse": None,
                        "mape_pct": None, "bias": None, "min_pct_err": None, "max_pct_err": None,
                    }
                errors = [r["error"] for r in rs]
                abs_pct = [r["abs_pct_error"] for r in rs if r["abs_pct_error"] is not None]
                mae = sum(abs(e) for e in errors) / len(errors)
                rmse = (sum(e * e for e in errors) / len(errors)) ** 0.5
                bias = sum(errors) / len(errors)  # +ve = under-prediction
                mape = sum(abs_pct) / len(abs_pct) if abs_pct else None
                return {
                    "n_evaluated": len(rs),
                    "mae": round(mae, 4),
                    "rmse": round(rmse, 4),
                    "mape_pct": round(mape, 2) if mape is not None else None,
                    "bias": round(bias, 4),
                    "min_pct_err": round(min(abs_pct), 2) if abs_pct else None,
                    "max_pct_err": round(max(abs_pct), 2) if abs_pct else None,
                }

            by_metric: Dict[str, List[Any]] = {}
            by_method: Dict[str, List[Any]] = {}
            by_mm: Dict[str, Dict[str, List[Any]]] = {}
            for r in rows:
                by_metric.setdefault(r["metric"], []).append(r)
                by_method.setdefault(r["method"], []).append(r)
                by_mm.setdefault(r["metric"], {}).setdefault(r["method"], []).append(r)

            return {
                "overall": _stats(rows),
                "by_metric": {m: _stats(rs) for m, rs in by_metric.items()},
                "by_method": {m: _stats(rs) for m, rs in by_method.items()},
                "by_metric_method": {
                    m: {meth: _stats(rs) for meth, rs in ms.items()}
                    for m, ms in by_mm.items()
                },
            }

    def count_forecast_predictions(
        self,
        metric: Optional[str] = None,
    ) -> int:
        """Count total forecast predictions (with or without actuals)."""
        with self._conn() as conn:
            if metric:
                row = conn.execute(
                    "SELECT COUNT(*) FROM forecast_predictions WHERE metric = ?",
                    (metric,),
                ).fetchone()
            else:
                row = conn.execute("SELECT COUNT(*) FROM forecast_predictions").fetchone()
        return row[0]

    def get_forecast_calibration_trend(
        self,
        metric: Optional[str] = None,
        method: Optional[str] = None,
        bucket: str = "evaluated_at_day",  # 'evaluated_at_day' | 'forecast_sim_day'
    ) -> List[Dict[str, Any]]:
        """
        iter #43: Calibration trend over time.

        Computes per-cycle calibration stats from forecast_predictions where
        actual_value is set. The "bucket" controls how time is grouped:
        - 'evaluated_at_day': when actual was filled in (uses recorded_at prefix? — fallback to forecast_sim_day)
        - 'forecast_sim_day': bucket by the target sim_day

        For each bucket (sim_day), we compute cumulative MAE / RMSE / MAPE /
        bias over all evaluated predictions up to and including that day.

        Returns:
            [{
              bucket_sim_day: int,
              n_evaluated: int,
              cumulative_mae: float,
              cumulative_rmse: float,
              cumulative_mape_pct: float,
              cumulative_bias: float,
            }, ...]
            Sorted by bucket_sim_day ASC.
        """
        with self._conn() as conn:
            where_clauses = ["actual_value IS NOT NULL", "error IS NOT NULL"]
            params: List[Any] = []
            if metric:
                where_clauses.append("metric = ?")
                params.append(metric)
            if method:
                where_clauses.append("method = ?")
                params.append(method)
            where_sql = " AND ".join(where_clauses)

            # Use forecast_sim_day as the time bucket (recordings happen after eval)
            rows = conn.execute(
                f"SELECT forecast_sim_day, error, abs_pct_error "
                f"FROM forecast_predictions WHERE {where_sql} "
                f"ORDER BY forecast_sim_day ASC, id ASC",
                params,
            ).fetchall()

        if not rows:
            return []

        # Group by sim_day
        from collections import defaultdict
        by_day: Dict[int, List[Any]] = defaultdict(list)
        for r in rows:
            by_day[int(r["forecast_sim_day"])].append(r)

        # Compute cumulative stats
        results: List[Dict[str, Any]] = []
        cumulative: List[Any] = []
        for day in sorted(by_day.keys()):
            cumulative.extend(by_day[day])
            errors = [r["error"] for r in cumulative]
            abs_pct = [r["abs_pct_error"] for r in cumulative if r["abs_pct_error"] is not None]
            mae = sum(abs(e) for e in errors) / len(errors)
            rmse = (sum(e * e for e in errors) / len(errors)) ** 0.5
            bias = sum(errors) / len(errors)
            mape = sum(abs_pct) / len(abs_pct) if abs_pct else None
            results.append({
                "bucket_sim_day": day,
                "n_evaluated": len(cumulative),
                "cumulative_mae": round(mae, 4),
                "cumulative_rmse": round(rmse, 4),
                "cumulative_mape_pct": round(mape, 2) if mape is not None else None,
                "cumulative_bias": round(bias, 4),
            })
        return results


    # ====================================================================
    # iter #37: Seasonal perturbation CRUD
    # ====================================================================
    # Allow operators to model one-off shocks (holiday spikes, weather
    # events, plant shutdowns) that overlay the static SEASONAL_FACTORS.
    # See data/seasonal_perturbation.py for the application logic.

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
