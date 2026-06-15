"""
30 天仿真脚本 (Task 4/5 Part A)

- 30 个 sim-day 跑 V2 多智能体协调器
- KPI 时间序列 -> /tmp/month_kpi.json
- 全局 summary    -> /tmp/month_summary.json
- Pareto 前沿 5 点 -> /tmp/pareto.json

用法：
    source venv/bin/activate
    python scripts/run_month.py
"""

import sys
import os
import asyncio
import json

# 让脚本能 import 顶层包
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.coordinator import MultiAgentCoordinator
from agents.world_builder import WorldConfig


async def main():
    config = WorldConfig(n_supply_points=20, n_demand_points=10, n_vehicles=30, seed=42)
    coord = MultiAgentCoordinator(config=config, db_path="data/month_simulation.db")

    results = await coord.simulate_day(days=30)

    kpi_ts = coord.persistence.get_kpi_timeseries()
    with open("/tmp/month_kpi.json", "w") as f:
        json.dump(kpi_ts, f, indent=2, default=str)

    summary = coord.persistence.get_summary()
    with open("/tmp/month_summary.json", "w") as f:
        json.dump(summary, f, indent=2, default=str)

    # 拉 Pareto — 用与 run_pareto.py 相同的 Borås 小型问题
    from optimization.vrp_solver import VRPSolver, Location, Vehicle

    solver = VRPSolver()
    depot = Location(id="DEPOT", lat=57.7089, lon=14.1618, type="depot")
    solver.add_location(depot)

    pickups = [
        (57.7300, 14.1900, "PICKUP_A"),
        (57.6900, 14.1300, "PICKUP_B"),
        (57.7500, 14.2000, "PICKUP_C"),
        (57.6700, 14.1000, "PICKUP_D"),
    ]
    for lat, lon, pid in pickups:
        solver.add_location(Location(
            id=pid, lat=lat, lon=lon,
            demand_tons=4.0, type="pickup",
        ))

    deliveries = [
        (57.6000, 14.0500, "CRUSH_W"),
        (57.8000, 14.2500, "CRUSH_E"),
    ]
    for lat, lon, did in deliveries:
        solver.add_location(Location(
            id=did, lat=lat, lon=lon,
            demand_tons=-8.0, type="delivery",
        ))

    fleet = [
        Vehicle(id="GAS_A", capacity_tons=8.0, start_location=depot,
                cost_per_km=1.0, co2_rate=1.20),
        Vehicle(id="GAS_B", capacity_tons=8.0, start_location=depot,
                cost_per_km=1.0, co2_rate=1.20),
        Vehicle(id="EV_A",  capacity_tons=8.0, start_location=depot,
                cost_per_km=3.0, co2_rate=0.02),
        Vehicle(id="EV_B",  capacity_tons=8.0, start_location=depot,
                cost_per_km=3.0, co2_rate=0.02),
    ]
    for v in fleet:
        solver.add_vehicle(v)

    pareto = solver.solve_pareto(n_points=5, time_limit_seconds=3)
    with open("/tmp/pareto.json", "w") as f:
        json.dump(pareto, f, indent=2, default=str)

    print("=== 30 days summary ===")
    print(json.dumps(summary, indent=2, default=str))
    print("\n=== Per-day KPI ===")
    for r in results:
        k = r["kpi"]
        print(
            f"Day {r['sim_day']:>2}: matches={k['n_matches']:>3} "
            f"tons={k['total_tons']:>6.1f} cost={k['total_cost_sek']:>7.0f} "
            f"CO2={k['total_co2_kg']:>7.1f}kg util={k['fleet_utilization_pct']:>5.1f}%"
        )

    print("\n=== Pareto ===")
    for i, p in enumerate(pareto):
        cost = p.get("cost_sek")
        co2 = p.get("co2_kg")
        cw = p.get("cost_weight")
        co2w = p.get("co2_weight")
        st = p.get("status")
        print(
            f"P{i+1} cost_w={cw} co2_w={co2w} cost={cost} co2={co2} status={st}"
        )


if __name__ == "__main__":
    asyncio.run(main())
