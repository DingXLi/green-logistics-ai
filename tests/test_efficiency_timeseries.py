"""
Efficiency timeseries tests (iter #52) — verify new fields in get_kpi_timeseries.

测试覆盖:
- cost_per_ton_sek = cost_sek / tons
- co2_per_ton_kg = co2_kg / tons
- cost_per_match_sek = cost_sek / matches
- supply_demand_ratio = matches / max(supply, demand)
- None when denominators are zero
- API endpoint returns efficiency fields in JSON
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestEfficiencyTimeseriesPersistence(unittest.TestCase):
    """Persistence.get_kpi_timeseries() with iter #52 efficiency fields."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False, dir="/tmp")
        self.tmp.close()
        self.db_path = self.tmp.name
        from agents.persistence import Persistence
        self.p = Persistence(db_path=self.db_path)
        self._seed()

    def tearDown(self):
        try:
            os.unlink(self.db_path)
        except Exception:
            pass

    def _seed(self):
        """2 cycles with known values to verify formulas."""
        # Cycle 1: tons=10, cost=100 SEK, co2=50 kg, matches=2, supply=5, demand=3
        self.p.begin_cycle(
            cycle_id="eff-1", sim_day=1, sim_hour=8, activity_factor=1.0,
            n_supply_offers=5, n_demand_requests=3,
        )
        self.p.commit_cycle(
            cycle_id="eff-1",
            kpi={"n_matches": 2, "total_tons": 10, "total_cost_sek": 100,
                 "total_co2_kg": 50, "total_distance_km": 30,
                 "n_vehicles_used": 2, "n_vehicles_available": 5,
                 "fleet_utilization_pct": 40, "solver_status": "OPTIMAL"},
            wall_duration_ms=50,
        )
        # Cycle 2: tons=20, cost=200, co2=80, matches=4, supply=8, demand=6
        self.p.begin_cycle(
            cycle_id="eff-2", sim_day=2, sim_hour=8, activity_factor=1.0,
            n_supply_offers=8, n_demand_requests=6,
        )
        self.p.commit_cycle(
            cycle_id="eff-2",
            kpi={"n_matches": 4, "total_tons": 20, "total_cost_sek": 200,
                 "total_co2_kg": 80, "total_distance_km": 60,
                 "n_vehicles_used": 3, "n_vehicles_available": 5,
                 "fleet_utilization_pct": 60, "solver_status": "OPTIMAL"},
            wall_duration_ms=50,
        )

    def test_efficiency_fields_present(self):
        ts = self.p.get_kpi_timeseries()
        self.assertEqual(len(ts), 2)
        for entry in ts:
            self.assertIn("cost_per_ton_sek", entry)
            self.assertIn("co2_per_ton_kg", entry)
            self.assertIn("cost_per_match_sek", entry)
            self.assertIn("n_supply_offers", entry)
            self.assertIn("n_demand_requests", entry)
            self.assertIn("supply_demand_ratio", entry)

    def test_cost_per_ton_formula(self):
        # Day 1: 100 SEK / 10 t = 10 SEK/t
        # Day 2: 200 SEK / 20 t = 10 SEK/t
        ts = self.p.get_kpi_timeseries()
        self.assertEqual(ts[0]["cost_per_ton_sek"], 10.0)
        self.assertEqual(ts[1]["cost_per_ton_sek"], 10.0)

    def test_co2_per_ton_formula(self):
        # Day 1: 50 kg / 10 t = 5 kg/t
        # Day 2: 80 kg / 20 t = 4 kg/t
        ts = self.p.get_kpi_timeseries()
        self.assertEqual(ts[0]["co2_per_ton_kg"], 5.0)
        self.assertEqual(ts[1]["co2_per_ton_kg"], 4.0)

    def test_cost_per_match_formula(self):
        # Day 1: 100 SEK / 2 matches = 50 SEK/match
        # Day 2: 200 SEK / 4 matches = 50 SEK/match
        ts = self.p.get_kpi_timeseries()
        self.assertEqual(ts[0]["cost_per_match_sek"], 50.0)
        self.assertEqual(ts[1]["cost_per_match_sek"], 50.0)

    def test_supply_demand_ratio(self):
        # Day 1: matches=2, max(5,3)=5 → 2/5 = 0.4
        # Day 2: matches=4, max(8,6)=8 → 4/8 = 0.5
        ts = self.p.get_kpi_timeseries()
        self.assertEqual(ts[0]["supply_demand_ratio"], 0.4)
        self.assertEqual(ts[1]["supply_demand_ratio"], 0.5)

    def test_supply_demand_counts(self):
        ts = self.p.get_kpi_timeseries()
        self.assertEqual(ts[0]["n_supply_offers"], 5)
        self.assertEqual(ts[0]["n_demand_requests"], 3)
        self.assertEqual(ts[1]["n_supply_offers"], 8)
        self.assertEqual(ts[1]["n_demand_requests"], 6)

    def test_zero_tons_returns_none_per_ton(self):
        # Seed a 0-ton cycle
        self.p.begin_cycle(
            cycle_id="eff-3", sim_day=3, sim_hour=8, activity_factor=1.0,
            n_supply_offers=2, n_demand_requests=1,
        )
        self.p.commit_cycle(
            cycle_id="eff-3",
            kpi={"n_matches": 0, "total_tons": 0, "total_cost_sek": 0,
                 "total_co2_kg": 0, "total_distance_km": 0,
                 "n_vehicles_used": 0, "n_vehicles_available": 5,
                 "fleet_utilization_pct": 0, "solver_status": "OPTIMAL"},
            wall_duration_ms=10,
        )
        ts = self.p.get_kpi_timeseries()
        # Last entry should be day 3 with all zero denominators
        day3 = next(t for t in ts if t["sim_day"] == 3)
        self.assertIsNone(day3["cost_per_ton_sek"])
        self.assertIsNone(day3["co2_per_ton_kg"])
        self.assertIsNone(day3["cost_per_match_sek"])
        # supply_demand_ratio with denom=2 → 0/2 = 0.0
        self.assertEqual(day3["supply_demand_ratio"], 0.0)

    def test_time_window_filter_still_works(self):
        # Filter day >= 2 should only return day 2
        ts = self.p.get_kpi_timeseries(since_sim_day=2)
        self.assertEqual(len(ts), 1)
        self.assertEqual(ts[0]["sim_day"], 2)


class TestEfficiencyTimeseriesEndpoint(unittest.TestCase):
    """/api/persistence/kpi-timeseries returns iter #52 efficiency fields."""

    def test_endpoint_includes_efficiency_fields(self):
        from fastapi.testclient import TestClient
        from web.backend.main import app

        client = TestClient(app)
        resp = client.get("/api/persistence/kpi-timeseries")
        self.assertIn(resp.status_code, (200, 503))
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list) and len(data) > 0:
                first = data[0]
                # Should have iter #52 fields
                self.assertIn("cost_per_ton_sek", first)
                self.assertIn("co2_per_ton_kg", first)
                self.assertIn("supply_demand_ratio", first)

    def test_endpoint_invalid_range(self):
        from fastapi.testclient import TestClient
        from web.backend.main import app

        client = TestClient(app)
        resp = client.get("/api/persistence/kpi-timeseries?since_sim_day=10&until_sim_day=5")
        # 400 invalid range, or 503 if no coordinator
        self.assertIn(resp.status_code, (400, 503))


if __name__ == "__main__":
    unittest.main()