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


def seed(db_path: Path, days: int, force: bool = False) -> int:
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
    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="给 CI 造最小历史 cycle fixture")
    parser.add_argument("--db", default=DEFAULT_DB, help="目标 DB 路径")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS, help="历史天数")
    parser.add_argument("--force", action="store_true", help="已有数据也强制重写")
    args = parser.parse_args()

    seed(Path(args.db), args.days, force=args.force)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
