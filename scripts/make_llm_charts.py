"""
AI 决策时间序列图表 (Task 4/5 Part C)

读 SQLite 里 llm_decisions 表,画 2 个时间序列图:
  1. Demand multiplier 每日均值 + 真实 LLM 占比
  2. Supply multiplier 每日均值 + 真实 LLM 占比

用法:
    source venv/bin/activate
    python scripts/make_llm_charts.py data/month_simulation.db
    # 默认读 data/month_simulation.db
    # 输出 docs/llm_decisions.png
"""

import json
import os
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def fetch_llm_timeseries(db_path: str, decision_type: str):
    """从 SQLite 拉一个 decision_type 的每日时间序列。

    Returns: list of (sim_day, n, avg_mult, avg_conf, llm_n, fb_n)
    """
    con = sqlite3.connect(db_path)
    cur = con.execute(
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
        (decision_type,),
    )
    rows = cur.fetchall()
    con.close()
    return rows


def fetch_per_target_timeseries(db_path: str, decision_type: str):
    """拉每个 target_id 自己的 multiplier 时间序列 (细线)。"""
    con = sqlite3.connect(db_path)
    cur = con.execute(
        """SELECT sim_day, target_id, multiplier
           FROM llm_decisions
           WHERE decision_type = ? AND source = 'llm' AND multiplier IS NOT NULL
           ORDER BY sim_day, target_id""",
        (decision_type,),
    )
    rows = cur.fetchall()
    con.close()
    by_target = defaultdict(list)
    by_day = defaultdict(dict)
    for day, tid, m in rows:
        by_target[tid].append((day, m))
        by_day[day][tid] = m
    return by_target, by_day


def plot_decision_panel(db_path: str, out_path: str = "docs/llm_decisions.png"):
    """画 2x2 网格: demand / supply 各自的 multiplier 时序 + LLM 占比。"""
    demand_rows = fetch_llm_timeseries(db_path, "demand_prediction")
    supply_rows = fetch_llm_timeseries(db_path, "supply_prediction")

    if not demand_rows and not supply_rows:
        print(f"No llm_decisions found in {db_path}; skip chart.")
        return

    demand_per_target, _ = fetch_per_target_timeseries(db_path, "demand_prediction")
    supply_per_target, _ = fetch_per_target_timeseries(db_path, "supply_prediction")

    fig, axes = plt.subplots(2, 2, figsize=(15, 9))
    fig.suptitle("AI Decisions Time Series — Gemini 2.5 Flash", fontsize=14)

    # (0,0): demand multiplier per day
    if demand_rows:
        days = [r[0] for r in demand_rows]
        avg = [r[2] for r in demand_rows]
        axes[0, 0].plot(days, avg, marker="o", linewidth=2, color="steelblue", label="avg multiplier")
        # 细线: 每个 demand_id
        for tid, series in demand_per_target.items():
            xs = [d for d, _ in series]
            ys = [m for _, m in series]
            axes[0, 0].plot(xs, ys, alpha=0.25, linewidth=0.8)
        axes[0, 0].axhline(1.0, color="grey", linestyle="--", alpha=0.4, label="baseline (1.0)")
        axes[0, 0].set_title("Demand Multiplier per Day (per-point + avg)")
        axes[0, 0].set_xlabel("Sim Day")
        axes[0, 0].set_ylabel("multiplier")
        axes[0, 0].set_ylim(0.3, 1.8)
        axes[0, 0].legend()
        axes[0, 0].grid(True, alpha=0.3)
    else:
        axes[0, 0].text(0.5, 0.5, "no demand_prediction data", ha="center", va="center",
                         transform=axes[0, 0].transAxes)
        axes[0, 0].set_title("Demand Multiplier (empty)")

    # (0,1): demand LLM 占比
    if demand_rows:
        days = [r[0] for r in demand_rows]
        llm_pct = [100 * (r[4] or 0) / r[1] for r in demand_rows]
        axes[0, 1].bar(days, llm_pct, color="seagreen", alpha=0.7)
        axes[0, 1].set_title("Demand — LLM Real-call Coverage (%)")
        axes[0, 1].set_xlabel("Sim Day")
        axes[0, 1].set_ylabel("%")
        axes[0, 1].set_ylim(0, 105)
        axes[0, 1].grid(True, alpha=0.3, axis="y")

    # (1,0): supply multiplier per day
    if supply_rows:
        days = [r[0] for r in supply_rows]
        avg = [r[2] for r in supply_rows]
        axes[1, 0].plot(days, avg, marker="o", linewidth=2, color="darkorange", label="avg multiplier")
        for tid, series in supply_per_target.items():
            xs = [d for d, _ in series]
            ys = [m for _, m in series]
            axes[1, 0].plot(xs, ys, alpha=0.25, linewidth=0.8)
        axes[1, 0].axhline(1.0, color="grey", linestyle="--", alpha=0.4, label="baseline (1.0)")
        axes[1, 0].set_title("Supply Multiplier per Day (per-point + avg)")
        axes[1, 0].set_xlabel("Sim Day")
        axes[1, 0].set_ylabel("multiplier")
        axes[1, 0].set_ylim(0.1, 2.0)
        axes[1, 0].legend()
        axes[1, 0].grid(True, alpha=0.3)
    else:
        axes[1, 0].text(0.5, 0.5, "no supply_prediction data", ha="center", va="center",
                         transform=axes[1, 0].transAxes)
        axes[1, 0].set_title("Supply Multiplier (empty)")

    # (1,1): supply LLM 占比
    if supply_rows:
        days = [r[0] for r in supply_rows]
        llm_pct = [100 * (r[4] or 0) / r[1] for r in supply_rows]
        axes[1, 1].bar(days, llm_pct, color="indianred", alpha=0.7)
        axes[1, 1].set_title("Supply — LLM Real-call Coverage (%)")
        axes[1, 1].set_xlabel("Sim Day")
        axes[1, 1].set_ylabel("%")
        axes[1, 1].set_ylim(0, 105)
        axes[1, 1].grid(True, alpha=0.3, axis="y")

    plt.tight_layout()
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(out_path, dpi=120, bbox_inches="tight")
    print(f"Saved {out_path}")

    # 简短摘要
    def summarize(rows, label):
        if not rows:
            return f"  {label}: no data"
        n = sum(r[1] for r in rows)
        llm_n = sum(r[4] or 0 for r in rows)
        avg_mult = sum(r[2] for r in rows) / len(rows)
        return (
            f"  {label}: {len(rows)} days, {n} total decisions, "
            f"{llm_n} real-LLM ({100*llm_n/n:.1f}%), avg multiplier {avg_mult:.3f}"
        )

    print("\nAI decisions summary:")
    print(summarize(demand_rows, "demand_prediction"))
    print(summarize(supply_rows, "supply_prediction"))


if __name__ == "__main__":
    db_path = sys.argv[1] if len(sys.argv) > 1 else "data/month_simulation.db"
    plot_decision_panel(db_path)
