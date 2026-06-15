"""Save /tmp/pareto.json from a fresh Pareto solve (for charts)."""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from optimization.vrp_solver import VRPSolver, Location, Vehicle

solver = VRPSolver()
depot = Location(id="DEPOT", lat=57.7089, lon=14.1618, type="depot")
solver.add_location(depot)
for lat, lon, pid in [
    (57.7300, 14.1900, "PICKUP_A"),
    (57.6900, 14.1300, "PICKUP_B"),
    (57.7500, 14.2000, "PICKUP_C"),
    (57.6700, 14.1000, "PICKUP_D"),
]:
    solver.add_location(Location(id=pid, lat=lat, lon=lon, demand_tons=4.0, type="pickup"))
for lat, lon, did in [
    (57.6000, 14.0500, "CRUSH_W"),
    (57.8000, 14.2500, "CRUSH_E"),
]:
    solver.add_location(Location(id=did, lat=lat, lon=lon, demand_tons=-8.0, type="delivery"))
for vid, cpkm, co2 in [
    ("GAS_A", 1.0, 1.20), ("GAS_B", 1.0, 1.20),
    ("EV_A",  3.0, 0.02), ("EV_B",  3.0, 0.02),
]:
    solver.add_vehicle(Vehicle(id=vid, capacity_tons=8.0, start_location=depot,
                                cost_per_km=cpkm, co2_rate=co2))

pareto = solver.solve_pareto(n_points=5, time_limit_seconds=3)
with open("/tmp/pareto.json", "w") as f:
    json.dump(pareto, f, indent=2, default=str)
print(f"Saved /tmp/pareto.json with {len(pareto)} points")
for i, p in enumerate(pareto):
    print(f"P{i+1} cost_w={p['cost_weight']} co2_w={p['co2_weight']} "
          f"cost={p['cost_sek']} co2={p['co2_kg']} status={p['status']}")
