"""
30 天 KPI 图表 (Task 4/5 Part B)

- 读 /tmp/month_kpi.json (30 天时序 KPI)
- 读 /tmp/pareto.json    (5 点 Pareto 前沿)
- 输出 docs/month1_kpi.png (2x3 网格, 5 个时序图 + 1 个 Pareto 散点图)

用法：
    source venv/bin/activate
    python scripts/make_month_charts.py
"""

import json
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

with open("/tmp/month_kpi.json") as f:
    kpi = json.load(f)
with open("/tmp/pareto.json") as f:
    pareto = json.load(f)

days = [r["sim_day"] for r in kpi]
tons = [r["tons"] for r in kpi]
costs = [r["cost_sek"] for r in kpi]
co2s = [r["co2_kg"] for r in kpi]
utils = [r["util_pct"] for r in kpi]
matches = [r["matches"] for r in kpi]

fig, axes = plt.subplots(2, 3, figsize=(18, 10))
fig.suptitle("Green Logistics AI — 30-Day Simulation (Borås, seed=42)", fontsize=14)

axes[0, 0].plot(days, tons, marker="o")
axes[0, 0].set_title("Daily Tons")
axes[0, 0].set_xlabel("Sim Day")
axes[0, 0].set_ylabel("Tons")
axes[0, 0].grid(True, alpha=0.3)

axes[0, 1].plot(days, costs, marker="o", color="orange")
axes[0, 1].set_title("Daily Cost (SEK)")
axes[0, 1].set_xlabel("Sim Day")
axes[0, 1].set_ylabel("SEK")
axes[0, 1].grid(True, alpha=0.3)

axes[0, 2].plot(days, co2s, marker="o", color="red")
axes[0, 2].set_title("Daily CO2 (kg)")
axes[0, 2].set_xlabel("Sim Day")
axes[0, 2].set_ylabel("kg CO2")
axes[0, 2].grid(True, alpha=0.3)

axes[1, 0].plot(days, utils, marker="o", color="green")
axes[1, 0].set_title("Fleet Utilization (%)")
axes[1, 0].set_xlabel("Sim Day")
axes[1, 0].set_ylabel("%")
axes[1, 0].grid(True, alpha=0.3)

# 7 天移动平均
if len(matches) >= 7:
    ma = [sum(matches[max(0,i-6):i+1])/min(i+1,7) for i in range(len(matches))]
    axes[1, 1].plot(days, matches, alpha=0.4, label="Daily")
    axes[1, 1].plot(days, ma, color="purple", linewidth=2, label="7-day MA")
    axes[1, 1].legend()
else:
    axes[1, 1].plot(days, matches)
axes[1, 1].set_title("Daily Matches + 7-day MA")
axes[1, 1].set_xlabel("Sim Day")
axes[1, 1].set_ylabel("Matches")
axes[1, 1].grid(True, alpha=0.3)

# Pareto 前沿
pcosts = [p.get("cost_sek", 0) for p in pareto]
pco2s = [p.get("co2_kg", 0) for p in pareto]
axes[1, 2].scatter(pcosts, pco2s, c=range(len(pareto)), cmap="viridis", s=100)
axes[1, 2].plot(pcosts, pco2s, "--", alpha=0.5)
for i, (c, co) in enumerate(zip(pcosts, pco2s)):
    axes[1, 2].annotate(f"P{i+1}", (c, co), xytext=(5, 5), textcoords="offset points")
axes[1, 2].set_title("Pareto Front: Cost vs CO2")
axes[1, 2].set_xlabel("Cost (SEK)")
axes[1, 2].set_ylabel("CO2 (kg)")
axes[1, 2].grid(True, alpha=0.3)

plt.tight_layout()
plt.savefig("docs/month1_kpi.png", dpi=120, bbox_inches="tight")
print("Saved docs/month1_kpi.png")
