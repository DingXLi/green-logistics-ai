"""
Tests for iter #59 cycle-comparison endpoint:
- Persistence.compare_cycles()
- /api/persistence/compare-cycles
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestCompareCyclesPersistence(unittest.TestCase):
    """Persistence.compare_cycles()"""

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
        """Seed 2 cycles with contrasting performance profiles.

        Cycle A (OPT0001): efficient — low cost, low CO2, high utilization
        Cycle B (OPT0002): wasteful — high cost, high CO2, low utilization
        """
        with self.p._conn() as conn:
            conn.execute("""INSERT INTO optimization_cycles
                (cycle_id, sim_day, sim_hour, wall_timestamp, activity_factor,
                 n_supply_offers, n_demand_requests, n_matches, total_tons,
                 total_cost_sek, total_co2_kg, total_distance_km, n_vehicles_used,
                 n_vehicles_available, fleet_utilization_pct, solver_status,
                 wall_duration_ms, seasonal_factor_avg, base_seasonal_factor_avg,
                 seasonal_month, perturbation_count, perturbation_total_multiplier)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?)""",
                ('OPT0001', 1, 8, '2026-09-07T08:00:00', 1.0, 5, 4, 4, 40.0,
                 200.0, 100.0, 100.0, 3, 10, 60.0, 'OPTIMAL', 150,
                 0.95, 0.95, 1, 0, 1.0))
            conn.execute("""INSERT INTO optimization_cycles
                (cycle_id, sim_day, sim_hour, wall_timestamp, activity_factor,
                 n_supply_offers, n_demand_requests, n_matches, total_tons,
                 total_cost_sek, total_co2_kg, total_distance_km, n_vehicles_used,
                 n_vehicles_available, fleet_utilization_pct, solver_status,
                 wall_duration_ms, seasonal_factor_avg, base_seasonal_factor_avg,
                 seasonal_month, perturbation_count, perturbation_total_multiplier)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?)""",
                ('OPT0002', 2, 9, '2026-09-07T09:00:00', 1.0, 5, 4, 2, 25.0,
                 250.0, 150.0, 80.0, 2, 10, 30.0, 'FEASIBLE', 300,
                 1.05, 1.20, 2, 1, 0.85))

    # ----- Basic structure -----

    def test_both_cycles_returned(self):
        result = self.p.compare_cycles('OPT0001', 'OPT0002')
        self.assertIsNotNone(result['cycle_a'])
        self.assertIsNotNone(result['cycle_b'])
        self.assertEqual(result['cycle_a']['cycle_id'], 'OPT0001')
        self.assertEqual(result['cycle_b']['cycle_id'], 'OPT0002')

    def test_required_fields_in_cycle_a(self):
        result = self.p.compare_cycles('OPT0001', 'OPT0002')
        a = result['cycle_a']
        for key in ('cycle_id', 'sim_day', 'sim_hour', 'wall_timestamp',
                    'solver_status', 'wall_duration_ms', 'n_matches',
                    'total_tons', 'total_cost_sek', 'total_co2_kg',
                    'total_distance_km', 'fleet_utilization_pct',
                    'cost_per_ton_sek', 'co2_per_ton_kg',
                    'cost_per_km_sek', 'co2_per_km_kg', 'avg_tons_per_match',
                    'seasonal_factor_avg', 'perturbation_count'):
            self.assertIn(key, a, f"Missing key {key}")

    def test_derived_metrics_cycle_a(self):
        result = self.p.compare_cycles('OPT0001', 'OPT0002')
        a = result['cycle_a']
        # cost_per_ton_sek = 200/40 = 5.0
        self.assertEqual(a['cost_per_ton_sek'], 5.0)
        # co2_per_ton_kg = 100/40 = 2.5
        self.assertEqual(a['co2_per_ton_kg'], 2.5)
        # cost_per_km_sek = 200/100 = 2.0
        self.assertEqual(a['cost_per_km_sek'], 2.0)
        # co2_per_km_kg = 100/100 = 1.0
        self.assertEqual(a['co2_per_km_kg'], 1.0)
        # avg_tons_per_match = 40/4 = 10.0
        self.assertEqual(a['avg_tons_per_match'], 10.0)

    def test_derived_metrics_cycle_b(self):
        result = self.p.compare_cycles('OPT0001', 'OPT0002')
        b = result['cycle_b']
        # cost_per_ton_sek = 250/25 = 10.0
        self.assertEqual(b['cost_per_ton_sek'], 10.0)
        # co2_per_ton_kg = 150/25 = 6.0
        self.assertEqual(b['co2_per_ton_kg'], 6.0)
        # avg_tons_per_match = 25/2 = 12.5
        self.assertEqual(b['avg_tons_per_match'], 12.5)

    # ----- Differences -----

    def test_differences_absolute(self):
        result = self.p.compare_cycles('OPT0001', 'OPT0002')
        diff = result['differences']['absolute']
        # total_tons: b - a = 25 - 40 = -15
        self.assertEqual(diff['total_tons'], -15.0)
        # total_cost_sek: b - a = 250 - 200 = 50
        self.assertEqual(diff['total_cost_sek'], 50.0)
        # fleet_utilization_pct: 30 - 60 = -30
        self.assertEqual(diff['fleet_utilization_pct'], -30.0)

    def test_differences_pct_change(self):
        result = self.p.compare_cycles('OPT0001', 'OPT0002')
        pct = result['differences']['pct_change']
        # total_tons: 100 * (25-40)/40 = -37.5
        self.assertEqual(pct['total_tons'], -37.5)
        # total_cost_sek: 100 * (250-200)/200 = 25
        self.assertEqual(pct['total_cost_sek'], 25.0)

    # ----- Winner -----

    def test_winner_5_axes(self):
        result = self.p.compare_cycles('OPT0001', 'OPT0002')
        w = result['winner']
        self.assertIn('lowest_co2_per_ton_kg', w)
        self.assertIn('lowest_cost_per_ton_sek', w)
        self.assertIn('highest_fleet_utilization_pct', w)
        self.assertIn('most_matches', w)
        self.assertIn('most_tons', w)

    def test_winner_chooses_better(self):
        result = self.p.compare_cycles('OPT0001', 'OPT0002')
        w = result['winner']
        # OPT0001 is better on lower-is-better axes (lower CO2, lower cost)
        self.assertEqual(w['lowest_co2_per_ton_kg']['cycle_id'], 'OPT0001')
        self.assertEqual(w['lowest_cost_per_ton_sek']['cycle_id'], 'OPT0001')
        # OPT0001 is better on higher-is-better axes (higher util, more matches)
        self.assertEqual(w['highest_fleet_utilization_pct']['cycle_id'], 'OPT0001')
        self.assertEqual(w['most_matches']['cycle_id'], 'OPT0001')
        self.assertEqual(w['most_tons']['cycle_id'], 'OPT0001')

    def test_winner_axis_has_required_fields(self):
        result = self.p.compare_cycles('OPT0001', 'OPT0002')
        w = result['winner']['lowest_co2_per_ton_kg']
        for key in ('cycle_id', 'direction', 'by_abs', 'by_pct',
                    'a_value', 'b_value'):
            self.assertIn(key, w)

    # ----- Edge cases -----

    def test_missing_cycle_b(self):
        """cycle_b doesn't exist → cycle_b=None, differences empty, winner None."""
        result = self.p.compare_cycles('OPT0001', 'NONEXISTENT')
        self.assertIsNotNone(result['cycle_a'])
        self.assertIsNone(result['cycle_b'])
        self.assertEqual(result['differences']['absolute'], {})
        self.assertEqual(result['differences']['pct_change'], {})
        self.assertIsNone(result['winner'])

    def test_missing_cycle_a(self):
        """cycle_a doesn't exist → cycle_a=None."""
        result = self.p.compare_cycles('NONEXISTENT', 'OPT0002')
        self.assertIsNone(result['cycle_a'])
        self.assertIsNotNone(result['cycle_b'])

    def test_both_missing(self):
        result = self.p.compare_cycles('NONEXISTENT_A', 'NONEXISTENT_B')
        self.assertIsNone(result['cycle_a'])
        self.assertIsNone(result['cycle_b'])
        self.assertIsNone(result['winner'])

    def test_perturbation_count_diff(self):
        result = self.p.compare_cycles('OPT0001', 'OPT0002')
        a = result['cycle_a']
        b = result['cycle_b']
        self.assertEqual(a['perturbation_count'], 0)
        self.assertEqual(b['perturbation_count'], 1)


class TestCompareCyclesEndpoint(unittest.TestCase):
    """/api/persistence/compare-cycles endpoint behavior."""

    def setUp(self):
        try:
            from web.backend.main import app
            from fastapi.testclient import TestClient
            self.TestClient = TestClient
            self.app = app
        except Exception as e:  # pragma: no cover
            self.skipTest(f"web.backend.main not importable: {e}")

    def _request(self, url: str):
        """Use TestClient as context manager so startup event fires (coordinator
        gets initialized). Without it, the first test in a fresh session sees
        coordinator=None and the 503 check fires before the validation checks."""
        with self.TestClient(self.app) as client:
            return client.get(url)

    def test_endpoint_default_cycles(self):
        """Calling with any cycle_ids should return 200 or 503."""
        resp = self._request(
            "/api/persistence/compare-cycles?cycle_id_a=OPT0001&cycle_id_b=OPT0002"
        )
        self.assertIn(resp.status_code, (200, 503))
        if resp.status_code == 200:
            data = resp.json()
            self.assertIn('cycle_a', data)
            self.assertIn('cycle_b', data)
            self.assertIn('differences', data)
            self.assertIn('winner', data)

    def test_endpoint_same_cycle_returns_4xx(self):
        resp = self._request(
            "/api/persistence/compare-cycles?cycle_id_a=OPT0001&cycle_id_b=OPT0001"
        )
        self.assertEqual(resp.status_code, 400)

    def test_endpoint_missing_param_returns_4xx(self):
        resp = self._request(
            "/api/persistence/compare-cycles?cycle_id_a=OPT0001"
        )
        self.assertEqual(resp.status_code, 422)  # missing required param

    def test_endpoint_empty_param_returns_4xx(self):
        resp = self._request(
            "/api/persistence/compare-cycles?cycle_id_a=&cycle_id_b=OPT0002"
        )
        self.assertEqual(resp.status_code, 400)


if __name__ == "__main__":
    unittest.main()
