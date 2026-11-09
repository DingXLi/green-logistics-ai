#!/usr/bin/env python3
"""
seed_ci_fixture.py — 给 CI 造一份最小的「历史 cycle」fixture (iter #74)

为什么需要这个脚本
------------------
`tests/test_dashboard_top_summary.py` 和 `tests/test_forecast_confidence.py`
里的断言依赖「DB 里已经有多天历史 cycle」(至少 2 个 sim_day, forecast 还要
≥2 个点才能拟合)。

但是 `data/*.db` 被 .gitignore 排除 → GitHub Actions 的 fresh checkout 里
根本没有 DB：

- 本地：data/simulation.db 里有几个月跑出来的真实数据 → 测试全绿
- CI：DB 不存在 → 这些测试 100% 挂（"need >= 2 cycles" / confidence 为空）

这就是典型的 "works on my machine"。本脚本用确定性的合成数据补上这段历史，
让 CI 的结果只取决于代码，不取决于谁本地跑过什么。

设计约束
--------
1. **幂等**：DB 里已经有 >= 2 个 sim_day 就直接跳过 —— 绝不覆盖开发者本地的
   真实 simulation.db。
2. **确定性**：不跑 OR-Tools、不用随机数、不依赖网络，纯 SQL insert，
   同一份代码在任何机器上生成完全相同的数据。
3. **快**：整个脚本 <1 秒完成。

用法
----
    python scripts/seed_ci_fixture.py            # 默认造 data/simulation.db
    python scripts/seed_ci_fixture.py --days 14  # 自定义历史天数
    python scripts/seed_ci_fixture.py --with-detail  # 额外铺 supply/match/route 明细

iter #82 (ADR-0014): ``--with-detail`` 会额外铺
supply_offers / demand_requests / matches / routes / llm_decisions 的确定性明细行。

**为什么需要它**: 导出契约门禁(ADR-0014)要观测「一张表导出成 CSV/Parquet/NDJSON
之后到底有哪些列」—— 而**空表的 Parquet 导出返回的是一个假的 schema**
(只有一列 ``_empty`` int64, 见 ``web/backend/main.py::_rows_to_parquet_bytes``),
NDJSON 空表则一行都没有, 什么列都观测不到。

也就是说: **没有明细行, 导出契约门禁冻的就是一个假契约。**
所以明细行是门禁的前提, 不是可选项。

默认**不开**: 铺明细会改变很多端点的响应体形状(数组从空变成有元素),
会让 ADR-0012 的响应体快照基线整体漂移 —— 那是一次独立的、需要单独评审的变更。
"""


from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

# 让脚本能 import 顶层包 (agents/persistence.py)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DEFAULT_DB = "data/simulation.db"
DEFAULT_DAYS = 14


def _existing_sim_days(db_path: Path) -> int:
    """已存在的 sim_day 数 (DB 不存在或表不存在都算 0)。"""
    if not db_path.exists():
        return 0
    try:
        conn = sqlite3.connect(str(db_path))
        row = conn.execute(
            "SELECT COUNT(DISTINCT sim_day) FROM optimization_cycles"
        ).fetchone()
        conn.close()
        return int(row[0]) if row and row[0] is not None else 0
    except sqlite3.Error:
        return 0


def _ensure_schema(db_path: Path) -> None:
    """用 Persistence 建表 (schema 单一来源, 不在这里抄一份 CREATE TABLE)。"""
    from agents.persistence import Persistence

    Persistence(str(db_path))


def _seed_rows(days: int) -> list[tuple]:
    """生成 days 天的合成 cycle (确定性: 只跟 sim_day 有关)。"""
    base_ts = datetime(2026, 1, 1, 8, 0, 0)
    rows = []
    for sim_day in range(1, days + 1):
        # 轻微上升 + 确定性锯齿 —— 让 linear regression / moving average /
        # exponential smoothing 都有可拟合的形状 (全常数会让 r_squared 无意义)
        tons = 40.0 + sim_day * 1.5
        cost = 1200.0 + sim_day * 85.0 + (sim_day % 3) * 40.0
        co2 = 300.0 + sim_day * 12.0 + (sim_day % 4) * 5.0
        util = 70.0 + (sim_day % 5) * 3.0
        matches = 8 + (sim_day % 4)
        ts = (base_ts + timedelta(days=sim_day - 1)).isoformat(sep=" ")
        rows.append(
            (
                f"ci-fixture-cycle-{sim_day:03d}",
                sim_day,
                8,
                1.0,
                ts,
                20,            # n_supply_offers
                10,            # n_demand_requests
                matches,       # n_matches
                tons,          # total_tons
                cost,          # total_cost_sek
                co2,           # total_co2_kg
                120.0 + sim_day,  # total_distance_km
                12,            # n_vehicles_used
                30,            # n_vehicles_available
                util,          # fleet_utilization_pct
                "OPTIMAL",     # solver_status
                1200 + sim_day * 10,  # wall_duration_ms
            )
        )
    return rows


def _seed_detail_rows(days: int) -> dict:
    """生成明细行(确定性: 只跟 sim_day 有关, 无随机数)。

    每一天挂 2 个 supply + 1 个 demand + 1 条 match + 1 条 route + 1 条 llm_decision,
    全部挂在 ``ci-fixture-cycle-{day:03d}`` 上 —— 跟 ``_seed_rows`` 用同一个 cycle_id 空间。
    """
    supplies, demands, matches, routes, llm = [], [], [], [], []

    for sim_day in range(1, days + 1):
        cycle_id = f"ci-fixture-cycle-{sim_day:03d}"
        base_ts = datetime(2026, 1, 1, 8, 0, 0) + timedelta(days=sim_day - 1)
        ts = base_ts.isoformat(sep=" ")
        lat = 59.30 + sim_day * 0.01
        lon = 18.05 + sim_day * 0.01

        for idx, material in enumerate(("wood", "metal_scrap")):
            supplies.append((
                cycle_id, f"SUP{sim_day:03d}{idx}", lat + idx * 0.001, lon + idx * 0.001,
                material, round(10.0 + sim_day * 0.5 + idx, 2),
                round(8.0 + (sim_day % 7), 2), round(70.0 + (sim_day % 20), 2),
                1.0, round(1.0 + (sim_day % 5) * 0.05, 3), 1 if sim_day % 3 == 0 else 0,
            ))
        demands.append((
            cycle_id, f"DEM{sim_day:03d}", f"ci plant {sim_day:03d}",
            lat + 0.05, lon + 0.05, "wood", round(5.0 + sim_day * 0.2, 2),
            "high" if sim_day % 2 else "normal", ts,
        ))
        matches.append((
            cycle_id, f"SUP{sim_day:03d}0", f"DEM{sim_day:03d}", "wood",
            round(4.0 + sim_day * 0.1, 2), round(12.0 + sim_day * 0.3, 3),
            round(300.0 + sim_day * 11.0, 2),
        ))
        routes.append((
            cycle_id, f"VEH{sim_day:03d}",
            '[{"lat":59.30,"lng":18.05},{"lat":59.35,"lng":18.10}]',
            round(40.0 + sim_day * 1.5, 3), round(3.0 + sim_day * 0.05, 3),
            round(1100.0 + sim_day * 40.0, 2), round(70.0 + sim_day * 3.0, 2),
        ))
        llm.append((
            cycle_id, sim_day, 8, "demand_prediction", f"DEM{sim_day:03d}", "demand_point",
            round(1.0 + (sim_day % 5) * 0.08, 3), "rising" if sim_day % 3 else "stable",
            round(0.55 + (sim_day % 4) * 0.1, 2), "ci fixture decision", "fallback",
            "{}", ts,
        ))

    return {
        "supply_offers": supplies,
        "demand_requests": demands,
        "matches": matches,
        "routes": routes,
        "llm_decisions": llm,
    }


def seed_detail_tables(db_path: Path, days: int = DEFAULT_DAYS) -> int:
    """铺明细表(supply/demand/match/route/llm_decision) —— 导出契约门禁的前提。

    幂等: 先删掉 ``ci-fixture-cycle-%`` 名下的旧明细行再插 —— 重复跑不会翻倍。
    不碰开发者本地 DB 的既有数据(只动 ci-fixture-cycle-* 这些 fixture 自己的行)。
    """
    _ensure_schema(db_path)
    conn = sqlite3.connect(str(db_path))
    total = 0
    try:
        for table in ("supply_offers", "demand_requests", "matches", "routes", "llm_decisions"):
            conn.execute(
                f"DELETE FROM {table} WHERE cycle_id LIKE 'ci-fixture-cycle-%'"
            )
        rows = _seed_detail_rows(days)
        conn.executemany(
            """INSERT INTO supply_offers (cycle_id, supply_id, location_lat, location_lon,
                   material_type, available_tons, moisture_percent, quality_score,
                   base_seasonal_multiplier, seasonal_multiplier, perturbation_applied)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            rows["supply_offers"],
        )
        conn.executemany(
            """INSERT INTO demand_requests (cycle_id, demand_id, name, location_lat, location_lon,
                   material_type, required_tons, priority, deadline)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            rows["demand_requests"],
        )
        conn.executemany(
            """INSERT INTO matches (cycle_id, supply_id, demand_id, material_type,
                   tons, distance_km, estimated_profit_sek)
               VALUES (?,?,?,?,?,?,?)""",
            rows["matches"],
        )
        conn.executemany(
            """INSERT INTO routes (cycle_id, vehicle_id, stops_json, distance_km,
                   duration_hours, cost_sek, co2_kg)
               VALUES (?,?,?,?,?,?,?)""",
            rows["routes"],
        )
        conn.executemany(
            """INSERT INTO llm_decisions (cycle_id, sim_day, sim_hour, decision_type,
                   target_id, target_type, multiplier, trend, confidence, reason,
                   source, raw_json, wall_timestamp)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            rows["llm_decisions"],
        )
        conn.commit()
        total = sum(len(v) for v in rows.values())
    finally:
        conn.close()

    print(
        f"✅ 明细 fixture 就绪: {db_path} 写入 {total} 行 "
        f"(supply/demand/match/route/llm_decision, 确定性合成数据)"
    )
    return total


def seed(db_path: Path, days: int, force: bool = False, with_detail: bool = False) -> int:
    """返回实际写入的 row 数 (0 = 跳过)。"""
    existing = _existing_sim_days(db_path)
    if existing >= 2 and not force:
        print(f"⏭  跳过: {db_path} 已有 {existing} 个 sim_day (不动开发者本地数据)")
        return 0

    _ensure_schema(db_path)
    rows = _seed_rows(days)

    conn = sqlite3.connect(str(db_path))
    try:
        conn.executemany(
            """INSERT OR REPLACE INTO optimization_cycles (
                   cycle_id, sim_day, sim_hour, activity_factor, wall_timestamp,
                   n_supply_offers, n_demand_requests, n_matches,
                   total_tons, total_cost_sek, total_co2_kg, total_distance_km,
                   n_vehicles_used, n_vehicles_available, fleet_utilization_pct,
                   solver_status, wall_duration_ms
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            rows,
        )
        conn.commit()
    finally:
        conn.close()

    print(
        f"✅ fixture 就绪: {db_path} 写入 {len(rows)} 个 cycle "
        f"(sim_day 1..{days}, 确定性合成数据)"
    )
    if with_detail:
        seed_detail_tables(db_path, days)
    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="给 CI 造最小历史 cycle fixture")
    parser.add_argument("--db", default=DEFAULT_DB, help="目标 DB 路径")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS, help="历史天数")
    parser.add_argument("--force", action="store_true", help="已有数据也强制重写")
    parser.add_argument(
        "--with-detail",
        action="store_true",
        help="额外铺 supply/demand/match/route/llm_decision 明细行 (导出契约门禁需要)",
    )
    args = parser.parse_args()

    seed(Path(args.db), args.days, force=args.force, with_detail=args.with_detail)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
