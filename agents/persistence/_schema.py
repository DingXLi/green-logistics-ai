"""SQLite schema 与共享类型定义。

schema 是单一来源：所有建表语句都来自 SCHEMA_SQL。
"""

from typing import Optional, TypedDict

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


class _BucketSpec(TypedDict):
    """吨位分桶定义（label 用于输出，min/max_tons 界定区间，max=None 表示无上限）。"""
    label: str
    min_tons: float
    max_tons: Optional[float]
