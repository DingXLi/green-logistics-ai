"""
数据质量检查脚本 (Data Quality Report)

跑完仿真后跑这个，自动检查 SQLite 数据库的数据健康度。
会输出 7 个维度的检查结果，最后给一个 PASS/FAIL 总结。

Usage
-----
    source venv/bin/activate
    python scripts/data_quality.py                       # 默认查 data/month_simulation.db
    python scripts/data_quality.py data/simulation.db    # 查指定 db
    python scripts/data_quality.py --ci data/month.db    # CI 模式：失败时 exit 1
    python scripts/data_quality.py --json data/...       # 输出 JSON 报告

退出码
------
    0 - 所有 fatal 检查都通过
    1 - 至少一个 fatal 检查失败（CI 用）
"""

import argparse
import json
import sqlite3
import statistics
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple


# --------------------------------------------------------------------
# 各维度检查
# --------------------------------------------------------------------

def check_schema_completeness(con: sqlite3.Connection) -> List[Tuple[str, str]]:
    """检查 schema 完整性。fatal: demand.material_type 全 NULL。"""
    issues: List[Tuple[str, str]] = []
    cur = con.execute(
        "SELECT COUNT(*), SUM(CASE WHEN material_type IS NULL THEN 1 ELSE 0 END) "
        "FROM demand_requests"
    )
    row = cur.fetchone() or (0, None)
    total, nulls = (row[0] or 0, row[1] or 0)
    if total == 0:
        issues.append(("fatal", "demand_requests 表为空"))
    elif nulls == total:
        issues.append(("fatal", f"demand.material_type 100% NULL ({nulls}/{total}) — Market Agent 没写字段"))
    elif nulls > 0:
        issues.append(("warn", f"demand.material_type 有 {nulls}/{total} 个 NULL ({100*nulls/total:.1f}%)"))
    else:
        issues.append(("ok", f"demand.material_type: {total}/{total} non-null"))

    # supply_offers material_type 也别空
    cur = con.execute(
        "SELECT COUNT(*), SUM(CASE WHEN material_type IS NULL THEN 1 ELSE 0 END) "
        "FROM supply_offers"
    )
    row = cur.fetchone() or (0, None)
    total, nulls = (row[0] or 0, row[1] or 0)
    if nulls == total and total > 0:
        issues.append(("fatal", f"supply.material_type 100% NULL ({nulls}/{total})"))
    elif nulls > 0:
        issues.append(("warn", f"supply.material_type 有 {nulls}/{total} NULL"))
    else:
        issues.append(("ok", f"supply.material_type: {total}/{total} non-null"))

    return issues


def check_kpi_variation(con: sqlite3.Connection) -> List[Tuple[str, str]]:
    """检查 KPI 是否真的在变。fatal: 完全没变化（CV=0）。"""
    cur = con.execute(
        "SELECT total_tons, total_cost_sek, total_co2_kg, total_distance_km, "
        "       fleet_utilization_pct, n_vehicles_used, n_matches "
        "FROM optimization_cycles ORDER BY sim_day"
    )
    rows = cur.fetchall()
    if not rows:
        return [("fatal", "optimization_cycles 表为空")]

    cols = [
        ("total_tons", 0),
        ("total_cost_sek", 1),
        ("total_co2_kg", 2),
        ("total_distance_km", 3),
        ("fleet_utilization_pct", 4),
        ("n_vehicles_used", 5),
        ("n_matches", 6),
    ]
    issues: List[Tuple[str, str]] = []
    n = len(rows)
    for name, idx in cols:
        vals = [r[idx] for r in rows if r[idx] is not None]
        if not vals:
            issues.append(("warn", f"{name}: 全部 NULL"))
            continue
        if len(vals) < 2:
            issues.append(("warn", f"{name}: 只有 1 个值，无 stdev"))
            continue
        stdev = statistics.stdev(vals)
        mean = statistics.mean(vals)
        cv = (stdev / mean * 100) if mean else 0

        # Plateau 检测：最后 1/3 唯一值数 ≤ 1 → 卡死
        last_third = vals[max(0, n * 2 // 3):]
        unique_last = len({round(v, 2) for v in last_third})
        plateau = " [PLATEAU: 最后 1/3 唯一值 ≤ 1]" if (n >= 6 and unique_last <= 1) else ""

        if cv < 0.1:
            issues.append(("fatal", f"{name}: CV={cv:.2f}% 几乎不变 (mean={mean:.1f} stdev={stdev:.2f}){plateau}"))
        elif cv < 5:
            issues.append(("warn", f"{name}: CV={cv:.2f}% 变异很小 (mean={mean:.1f} stdev={stdev:.1f}){plateau}"))
        else:
            issues.append(("ok", f"{name}: mean={mean:.1f} stdev={stdev:.1f} CV={cv:.1f}%{plateau}"))
    return issues


def check_solver_health(con: sqlite3.Connection) -> List[Tuple[str, str]]:
    """solver 状态分布。fatal: 100% no_solution。"""
    cur = con.execute(
        "SELECT solver_status, COUNT(*), AVG(wall_duration_ms) "
        "FROM optimization_cycles GROUP BY solver_status ORDER BY 2 DESC"
    )
    rows = cur.fetchall()
    if not rows:
        return [("fatal", "没有 cycles")]
    total = sum(r[1] for r in rows)
    issues: List[Tuple[str, str]] = []
    for status, cnt, mean_ms in rows:
        pct = 100 * cnt / total
        if status in ("optimal", "feasible"):
            tag = "ok"
        elif status and status.startswith("fallback"):
            tag = "warn"
        elif status == "no_solution":
            tag = "fatal"
        else:
            tag = "warn"
        ms = f" wall={mean_ms:.0f}ms" if mean_ms is not None else ""
        issues.append((tag, f"{status or '<NULL>'}: {cnt}/{total} ({pct:.1f}%){ms}"))
    return issues


def check_temporal_diversity(con: sqlite3.Connection) -> List[Tuple[str, str]]:
    """时间多样性：sim_hour、activity_factor 不应永远一个值。"""
    cur = con.execute(
        "SELECT sim_hour, activity_factor, COUNT(*) "
        "FROM optimization_cycles GROUP BY sim_hour, activity_factor ORDER BY 1"
    )
    rows = cur.fetchall()
    if not rows:
        return [("fatal", "无 cycles")]
    hours = sorted({r[0] for r in rows})
    factors = sorted({r[1] for r in rows})
    issues: List[Tuple[str, str]] = []
    if len(hours) == 1:
        issues.append(("fatal", f"sim_hour 永远 = {hours[0]}（缺少日内变化）"))
    elif len(hours) < 3:
        issues.append(("warn", f"sim_hour 唯一值少: {hours}"))
    else:
        issues.append(("ok", f"sim_hour 唯一值 {len(hours)} 个: {hours}"))
    if len(factors) == 1:
        issues.append(("fatal", f"activity_factor 永远 = {factors[0]}（昼夜/季节性没生效）"))
    else:
        issues.append(("ok", f"activity_factor 唯一值 {len(factors)} 个: {factors}"))
    return issues


def check_match_coverage(con: sqlite3.Connection) -> List[Tuple[str, str]]:
    """match 覆盖：是否每个 supply / demand 都被服务过。"""
    issues: List[Tuple[str, str]] = []
    cur = con.execute("SELECT COUNT(DISTINCT supply_id), COUNT(DISTINCT demand_id) FROM matches")
    uniq_sup, uniq_dem = cur.fetchone()
    cur = con.execute("SELECT COUNT(DISTINCT supply_id) FROM supply_offers")
    n_sup = cur.fetchone()[0]
    cur = con.execute("SELECT COUNT(DISTINCT demand_id) FROM demand_requests")
    n_dem = cur.fetchone()[0]
    sup_cov = 100 * uniq_sup / n_sup if n_sup else 0
    dem_cov = 100 * uniq_dem / n_dem if n_dem else 0
    if sup_cov < 80:
        issues.append(("warn", f"supply 覆盖率 {sup_cov:.1f}% ({uniq_sup}/{n_sup})"))
    else:
        issues.append(("ok", f"supply 覆盖率 {sup_cov:.1f}% ({uniq_sup}/{n_sup})"))
    if dem_cov < 80:
        issues.append(("warn", f"demand 覆盖率 {dem_cov:.1f}% ({uniq_dem}/{n_dem})"))
    else:
        issues.append(("ok", f"demand 覆盖率 {dem_cov:.1f}% ({uniq_dem}/{n_dem})"))
    return issues


def check_material_distribution(con: sqlite3.Connection) -> List[Tuple[str, str]]:
    """物料分布。"""
    issues: List[Tuple[str, str]] = []
    cur = con.execute(
        "SELECT material_type, COUNT(*) FROM supply_offers "
        "GROUP BY material_type ORDER BY 2 DESC"
    )
    rows = cur.fetchall()
    if not rows:
        return [("warn", "supply 没有 material_type 数据")]
    issues.append(("info", f"supply materials ({len(rows)} 种):"))
    for m, cnt in rows:
        issues.append(("info", f"  {m or '<NULL>'}: {cnt}"))
    return issues


def check_demand_variation(con: sqlite3.Connection) -> List[Tuple[str, str]]:
    """demand.required_tons 是否每周期真变。"""
    cur = con.execute(
        "SELECT cycle_id, ROUND(AVG(required_tons), 3), MIN(required_tons), MAX(required_tons) "
        "FROM demand_requests GROUP BY cycle_id"
    )
    rows = cur.fetchall()
    if not rows:
        return [("warn", "无 demand")]
    avgs = [r[1] for r in rows]
    if len(avgs) < 2:
        return [("warn", "只有 1 个 cycle 的 demand")]
    stdev = statistics.stdev(avgs)
    issues: List[Tuple[str, str]] = []
    if stdev < 0.1:
        issues.append(("fatal", f"demand.required_tons avg stdev={stdev:.3f} 几乎没变化"))
    elif stdev < 1.0:
        issues.append(("warn", f"demand.required_tons avg stdev={stdev:.2f} 变化较小"))
    else:
        issues.append(("ok", f"demand.required_tons avg 跨 {len(avgs)} cycle: stdev={stdev:.2f}"))
    return issues


# --------------------------------------------------------------------
# 报告输出
# --------------------------------------------------------------------

SECTIONS: List[Tuple[str, Callable[[sqlite3.Connection], List[Tuple[str, str]]]]] = [
    ("Schema 完整性",          check_schema_completeness),
    ("KPI 变异",                check_kpi_variation),
    ("Solver 健康",             check_solver_health),
    ("时间多样性",              check_temporal_diversity),
    ("Match 覆盖",              check_match_coverage),
    ("物料分布",                check_material_distribution),
    ("Demand 需求变化",         check_demand_variation),
]

TAG_ICON = {"fatal": "❌", "warn": "⚠️ ", "ok": "✅", "info": "ℹ️ "}


def run_quality(db_path: str, ci_mode: bool = False, json_mode: bool = False) -> int:
    if not Path(db_path).exists():
        print(f"❌ DB not found: {db_path}")
        return 1
    con = sqlite3.connect(db_path)

    # 收集结果
    results: Dict[str, List[Dict[str, str]]] = {}
    fatal_count = 0
    warn_count = 0
    for name, fn in SECTIONS:
        rows = fn(con)
        results[name] = [{"tag": t, "msg": m} for t, m in rows]
        for t, _ in rows:
            if t == "fatal":
                fatal_count += 1
            elif t == "warn":
                warn_count += 1

    # db 摘要
    cur = con.execute(
        "SELECT COUNT(*), MIN(wall_timestamp), MAX(wall_timestamp) "
        "FROM optimization_cycles"
    )
    row = cur.fetchone() or (0, None, None)
    n_cycles, t_min, t_max = (row[0] or 0, row[1], row[2])
    cur = con.execute(
        "SELECT COUNT(*) FROM supply_offers"
    )
    n_supply = cur.fetchone()[0]
    cur = con.execute(
        "SELECT COUNT(*) FROM demand_requests"
    )
    n_demand = cur.fetchone()[0]

    con.close()

    if json_mode:
        report = {
            "db_path": str(db_path),
            "generated_at": datetime.now().isoformat(),
            "summary": {
                "n_cycles": n_cycles,
                "n_supply_rows": n_supply,
                "n_demand_rows": n_demand,
                "first_cycle": t_min,
                "last_cycle": t_max,
                "fatal_count": fatal_count,
                "warn_count": warn_count,
                "verdict": "PASS" if fatal_count == 0 else "FAIL",
            },
            "sections": results,
        }
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        # 人类可读
        print()
        print("=" * 64)
        print(f"  📋  Data Quality Report")
        print(f"      DB:        {db_path}")
        print(f"      Generated: {datetime.now().isoformat()}")
        print(f"      Cycles:    {n_cycles}  Supply rows: {n_supply}  Demand rows: {n_demand}")
        print(f"      Time span: {t_min}  →  {t_max}")
        print("=" * 64)
        for name, rows in results.items():
            print(f"\n[{name}]")
            for r in rows:
                icon = TAG_ICON.get(r["tag"], "  ")
                print(f"  {icon} {r['msg']}")
        print()
        print("=" * 64)
        if fatal_count == 0:
            print(f"  ✅  VERDICT: PASS  ({warn_count} warning(s))")
        else:
            print(f"  ❌  VERDICT: FAIL  ({fatal_count} fatal, {warn_count} warning(s))")
        print("=" * 64)
        print()

    return 1 if (ci_mode and fatal_count > 0) else 0


def main():
    p = argparse.ArgumentParser(description="仿真数据库数据质量检查")
    p.add_argument("db", nargs="?", default="data/month_simulation.db",
                   help="SQLite 数据库路径 (默认: data/month_simulation.db)")
    p.add_argument("--ci", action="store_true",
                   help="CI 模式：发现 fatal 时 exit 1")
    p.add_argument("--json", action="store_true",
                   help="输出 JSON 报告（便于 CI 集成）")
    args = p.parse_args()
    rc = run_quality(args.db, ci_mode=args.ci, json_mode=args.json)
    sys.exit(rc)


if __name__ == "__main__":
    main()
