"""
V3 Pareto 前沿扫描脚本

按 5 个权重组合 (cost_weight: 1.0 → 0.0) 求解多目标 VRP，
打印每个点的 (cost_sek, co2_kg, total_objective)。

用法：
    source venv/bin/activate
    python scripts/run_pareto.py
"""

import sys
import os

# 让脚本能 import 顶层包
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from optimization.vrp_solver import VRPSolver, Location, Vehicle


def main():
    # 构建一个 Borås 区域的小型 VRP 问题
    # depot 在 Borås 中心
    solver = VRPSolver()

    depot = Location(
        id="DEPOT",
        lat=57.7089,
        lon=14.1618,
        type="depot",
    )
    solver.add_location(depot)

    # 4 个 pickup (废料供应点)
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

    # 2 个 delivery (破碎厂/再利用厂)
    deliveries = [
        (57.6000, 14.0500, "CRUSH_W"),
        (57.8000, 14.2500, "CRUSH_E"),
    ]
    for lat, lon, did in deliveries:
        solver.add_location(Location(
            id=did, lat=lat, lon=lon,
            demand_tons=-8.0, type="delivery",
        ))

    # 4 辆车：异构车队 —— Gas 便宜但高排，EV 贵但清洁
    # 这样在高 cost_w 时优 Gas，高 co2_w 时优 EV，产生真正的 Pareto 权衡
    fleet = [
        Vehicle(id="GAS_A",  capacity_tons=8.0, start_location=depot,
                cost_per_km=1.0, co2_rate=1.20),
        Vehicle(id="GAS_B",  capacity_tons=8.0, start_location=depot,
                cost_per_km=1.0, co2_rate=1.20),
        Vehicle(id="EV_A",   capacity_tons=8.0, start_location=depot,
                cost_per_km=3.0, co2_rate=0.02),
        Vehicle(id="EV_B",   capacity_tons=8.0, start_location=depot,
                cost_per_km=3.0, co2_rate=0.02),
    ]
    for v in fleet:
        solver.add_vehicle(v)

    # Pareto 扫描
    n = 5
    print("=" * 70)
    print(f"Pareto 前沿扫描 (n_points={n})")
    print("=" * 70)
    print(f"{'Point':<6} {'cost_w':<8} {'co2_w':<8} "
          f"{'cost_sek':<12} {'co2_kg':<10} {'objective':<12} {'status':<10}")
    print("-" * 70)

    pareto = solver.solve_pareto(n_points=n, time_limit_seconds=3)

    for i, p in enumerate(pareto):
        cost = p["cost_sek"] if p["cost_sek"] is not None else float("nan")
        co2 = p["co2_kg"] if p["co2_kg"] is not None else float("nan")
        obj = p["total_objective"] if p["total_objective"] is not None else float("nan")
        print(
            f"{i+1:<6} "
            f"{p['cost_weight']:<8.2f} "
            f"{p['co2_weight']:<8.2f} "
            f"{cost:<12.1f} "
            f"{co2:<10.2f} "
            f"{obj:<12.2f} "
            f"{p['status']:<10}"
        )

    print("=" * 70)
    # 计算权衡幅度
    valid = [p for p in pareto if p["cost_sek"] is not None]
    if len(valid) >= 2:
        cost_min = min(p["cost_sek"] for p in valid)
        cost_max = max(p["cost_sek"] for p in valid)
        co2_min = min(p["co2_kg"] for p in valid)
        co2_max = max(p["co2_kg"] for p in valid)
        print(f"cost_sek 范围：{cost_min:.1f} → {cost_max:.1f}  ({cost_max - cost_min:.1f} SEK 跨度)")
        print(f"co2_kg  范围：{co2_min:.2f} → {co2_max:.2f}  ({co2_max - co2_min:.2f} kg 跨度)")
    print(f"最优 cost-only (Point 1) vs 最优 co2-only (Point {n})："
          f"cost ratio = {valid[0]['cost_sek']/valid[-1]['cost_sek']:.2%}, "
          f"co2 ratio = {valid[0]['co2_kg']/valid[-1]['co2_kg']:.2%}")


if __name__ == "__main__":
    main()