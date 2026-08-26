"""
Tests for seasonal persistence + /api/persistence/seasonal-timeseries endpoint.
"""

import pytest
import os
import asyncio
from fastapi.testclient import TestClient


def test_persistence_schema_has_seasonal_columns():
    """optimization_cycles 表应该含 seasonal_factor_avg + seasonal_month 列"""
    from agents.persistence import Persistence

    # 用 test db
    test_db = "data/test_seasonal_schema.db"
    try:
        p = Persistence(db_path=test_db)
        import sqlite3
        conn = sqlite3.connect(test_db)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(optimization_cycles)").fetchall()}
        assert "seasonal_factor_avg" in cols
        assert "seasonal_month" in cols
        conn.close()
    finally:
        try: os.remove(test_db)
        except OSError: pass


def test_migrate_add_seasonal_columns_backward_compat():
    """旧 DB (无 seasonal 列) 应该被自动 ALTER TABLE 加列"""
    import sqlite3
    from agents.persistence import Persistence

    test_db = "data/test_migrate_seasonal.db"
    try:
        # 1. 创建旧版 schema (无 seasonal 列)
        conn = sqlite3.connect(test_db)
        conn.executescript("""
            CREATE TABLE optimization_cycles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cycle_id TEXT NOT NULL UNIQUE,
                sim_day INTEGER NOT NULL,
                sim_hour INTEGER NOT NULL,
                activity_factor REAL,
                wall_timestamp TEXT NOT NULL,
                n_supply_offers INTEGER DEFAULT 0,
                n_demand_requests INTEGER DEFAULT 0
            );
        """)
        conn.execute(
            "INSERT INTO optimization_cycles (cycle_id, sim_day, sim_hour, activity_factor, wall_timestamp) "
            "VALUES ('OPT0000', 0, 0, 1.0, '2026-01-01T00:00:00')"
        )
        conn.commit()
        conn.close()

        # 2. Persistence 应该自动加 seasonal 列
        p = Persistence(db_path=test_db)

        # 3. 验证列已加 + 旧数据默认值
        conn = sqlite3.connect(test_db)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(optimization_cycles)").fetchall()}
        assert "seasonal_factor_avg" in cols
        assert "seasonal_month" in cols

        # 旧 cycle 的 seasonal 应该用 DEFAULT (1.0 / 1)
        row = conn.execute(
            "SELECT seasonal_factor_avg, seasonal_month FROM optimization_cycles WHERE cycle_id = 'OPT0000'"
        ).fetchone()
        assert row[0] == 1.0  # DEFAULT
        assert row[1] == 1    # DEFAULT
        conn.close()
    finally:
        try: os.remove(test_db)
        except OSError: pass


def test_seasonal_timeseries_endpoint():
    """/api/persistence/seasonal-timeseries 应该返回 12 个月 dict (可能为空)"""
    from web.backend.main import app

    with TestClient(app) as client:
        r = client.get("/api/persistence/seasonal-timeseries")
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list)
        # 即使空数据也应该返回 [] (而不是 404)
        for m in data:
            assert "month" in m
            assert "month_name" in m
            assert 1 <= m["month"] <= 12
            assert m["month_name"] in (
                "Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
            )


@pytest.mark.asyncio
async def test_coordinator_begin_cycle_records_seasonal():
    """Coordinator.run_optimization_cycle() 应该把 seasonal_factor + month 写入 begin_cycle"""
    from agents.coordinator import MultiAgentCoordinator

    coord = MultiAgentCoordinator(db_path="data/test_seasonal_coord.db")
    coord.config.n_supply_points = 3
    coord.config.n_demand_points = 3
    coord.config.n_vehicles = 3
    coord.supply_agents.clear()
    coord._bootstrap_world()

    # Summer cycle (sim_day 150→151 after advance, month=6 Jun)
    coord.clock.now.day = 150
    coord.clock.now.month = 6
    await coord.run_optimization_cycle()

    # Winter cycle (sim_day 358→359 after advance, month=12 Dec)
    # Coordinator.run_optimization_cycle() 会 advance_day() +1, 所以 day=358 advance → 359
    coord.clock.now.day = 358
    coord.clock.now.month = 12
    await coord.run_optimization_cycle()

    # Verify seasonal-timeseries contains both
    ts = coord.persistence.get_seasonal_timeseries()
    months_present = {m["month"] for m in ts}
    assert 6 in months_present, "summer month 6 missing"
    assert 12 in months_present, "winter month 12 missing"

    # Summer should have higher seasonal_factor than winter
    by_month = {m["month"]: m for m in ts}
    if 6 in by_month and 12 in by_month:
        assert by_month[6]["avg_seasonal_factor"] > by_month[12]["avg_seasonal_factor"]

    import os
    try: os.remove("data/test_seasonal_coord.db")
    except OSError: pass