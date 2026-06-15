"""1-cycle end-to-end validation for the V2 coordinator bug fixes."""
import asyncio
import os
import sqlite3

from agents.coordinator import MultiAgentCoordinator
from agents.world_builder import WorldConfig

# Make sure DB lives in repo's data/ so the rest of the script can find it
DB_PATH = "data/single_cycle.db"
os.makedirs("data", exist_ok=True)

config = WorldConfig(n_supply_points=20, n_demand_points=10, n_vehicles=30, seed=42)
coord = MultiAgentCoordinator(config=config, db_path=DB_PATH)
result = asyncio.run(coord.run_optimization_cycle())
print("kpi:", result["kpi"])

# Table-row count
print("\n--- single_cycle.db row counts ---")
tables = [
    "supply_agents",
    "demand_agents",
    "vehicles",
    "matches",
    "routes",
    "kpi_snapshots",
    "decisions",
]
conn = sqlite3.connect(DB_PATH)
try:
    for t in tables:
        try:
            n = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            print(f"  {t:20s} {n}")
        except sqlite3.OperationalError as e:
            print(f"  {t:20s} MISSING ({e})")
finally:
    conn.close()
