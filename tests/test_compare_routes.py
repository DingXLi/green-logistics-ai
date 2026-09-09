"""
Tests for iter #64 compare-routes endpoint:
- Persistence.compare_routes()
- /api/persistence/compare-routes
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestCompareRoutesPersistence(unittest.TestCase):
    """Persistence.compare_routes()"""

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
        """2 cycles with different route profiles."""
        for r in [
            {"cid": "rt-cmp-1", "sim_day": 1, "vehicle": "VEH_A",
             "stops": ["S1", "D1"], "distance": 10.0, "duration": 1.0,
             "cost": 50.0, "co2": 5.0, "tons": 8.0},
            {"cid": "rt-cmp-2", "sim_day": 2, "vehicle": "VEH_B",
             "stops": ["S2", "D2", "D3"], "distance": 30.0, "duration": 2.0,
             "cost": 120.0, "co2": 36.0, "tons": 22.0},
        ]:
            self.p.begin_cycle(
                cycle_id=r["cid"], sim_day=r["sim_day"], sim_hour=8,
                activity_factor=1.0, n_supply_offers=1, n_demand_requests=1,
            )
            self.p.commit_cycle(
                cycle_id=r["cid"],
                kpi={"n_matches": 1, "total_tons": r["tons"],
                     "total_cost_sek": r["cost"], "total_co2_kg": r["co2"],
                     "total_distance_km": r["distance"],
                     "n_vehicles_used": 1, "n_vehicles_available": 5,
                     "fleet_utilization_pct": 20, "solver_status": "OPTIMAL"},
                wall_duration_ms=100,
            )
            self.p.record_match(r["cid"], {
                "supply_id": "S_" + r["cid"],
                "demand_id": "D_" + r["cid"],
                "material_type": "concrete",
                "tons": r["tons"] / 2,
                "distance_km": r["distance"],
                "estimated_profit_sek": 100.0,
            })
            self.p.record_route(r["cid"], {
                "vehicle_id": r["vehicle"], "stops": r["stops"],
                "distance_km": r["distance"],
                "duration_hours": r["duration"],
                "cost_sek": r["cost"], "co2_kg": r["co2"],
            })

    def _route_ids(self):
        """Get the two route_ids in seed order."""
        import sqlite3
        conn = sqlite3.connect(self.db_path)
        cur = conn.execute("SELECT id, cycle_id FROM routes ORDER BY id")
        rows = cur.fetchall()
        conn.close()
        return rows  # [(1, 'rt-cmp-1'), (2, 'rt-cmp-2')]

    # --- core ---

    def test_basic_envelope(self):
        """Returns the standard compare envelope (a, b, diff, winner)."""
        ids = self._route_ids()
        result = self.p.compare_routes(ids[0][0], ids[1][0])
        for k in ("route_a", "route_b", "differences", "winner"):
            self.assertIn(k, result)

    def test_route_a_fields(self):
        """route_a row has full context."""
        ids = self._route_ids()
        result = self.p.compare_routes(ids[0][0], ids[1][0])
        a = result["route_a"]
        for k in ("route_id", "cycle_id", "vehicle_id", "sim_day", "sim_hour",
                  "n_stops", "distance_km", "duration_hours", "cost_sek",
                  "co2_kg", "cycle_tons",
                  "co2_per_km", "co2_per_hour", "cost_per_km",
                  "cost_per_hour", "speed_km_per_hour"):
            self.assertIn(k, a)

    def test_route_a_metrics(self):
        """route_a (rt-cmp-1) basic metrics correct."""
        ids = self._route_ids()
        result = self.p.compare_routes(ids[0][0], ids[1][0])
        a = result["route_a"]
        self.assertEqual(a["cycle_id"], "rt-cmp-1")
        self.assertEqual(a["vehicle_id"], "VEH_A")
        self.assertEqual(a["distance_km"], 10.0)
        self.assertEqual(a["duration_hours"], 1.0)
        self.assertEqual(a["cost_sek"], 50.0)
        self.assertEqual(a["co2_kg"], 5.0)
        # co2_per_km = 5/10 = 0.5
        self.assertAlmostEqual(a["co2_per_km"], 0.5, places=3)
        # speed = 10/1 = 10
        self.assertAlmostEqual(a["speed_km_per_hour"], 10.0, places=2)

    def test_route_b_metrics(self):
        """route_b (rt-cmp-2) basic metrics correct."""
        ids = self._route_ids()
        result = self.p.compare_routes(ids[0][0], ids[1][0])
        b = result["route_b"]
        self.assertEqual(b["cycle_id"], "rt-cmp-2")
        self.assertEqual(b["vehicle_id"], "VEH_B")
        self.assertEqual(b["distance_km"], 30.0)
        self.assertEqual(b["duration_hours"], 2.0)
        self.assertEqual(b["cost_sek"], 120.0)
        self.assertEqual(b["co2_kg"], 36.0)
        # co2_per_km = 36/30 = 1.2
        self.assertAlmostEqual(b["co2_per_km"], 1.2, places=3)
        # speed = 30/2 = 15
        self.assertAlmostEqual(b["speed_km_per_hour"], 15.0, places=2)
        self.assertEqual(b["n_stops"], 3)

    # --- differences ---

    def test_differences_absolute(self):
        """Absolute differences (b - a)."""
        ids = self._route_ids()
        result = self.p.compare_routes(ids[0][0], ids[1][0])
        diffs = result["differences"]["absolute"]
        # distance: 30 - 10 = 20
        self.assertEqual(diffs["distance_km"], 20.0)
        # cost: 120 - 50 = 70
        self.assertEqual(diffs["cost_sek"], 70.0)

    def test_differences_pct_change(self):
        """Pct change is 100 * (b - a) / |a|."""
        ids = self._route_ids()
        result = self.p.compare_routes(ids[0][0], ids[1][0])
        pct = result["differences"]["pct_change"]
        # 100 * 20 / 10 = 200%
        self.assertAlmostEqual(pct["distance_km"], 200.0, places=1)

    # --- winner ---

    def test_winner_axes(self):
        """Winner dict has 5 axes."""
        ids = self._route_ids()
        result = self.p.compare_routes(ids[0][0], ids[1][0])
        winner = result["winner"]
        for axis in ("lowest_co2_per_km", "lowest_co2_per_hour",
                     "lowest_cost_per_km", "highest_speed", "lowest_duration"):
            self.assertIn(axis, winner)

    def test_winner_lowest_co2_per_km(self):
        """route_a (0.5 kg/km) wins lowest co2_per_km."""
        ids = self._route_ids()
        result = self.p.compare_routes(ids[0][0], ids[1][0])
        win = result["winner"]["lowest_co2_per_km"]
        self.assertEqual(win["route_id"], ids[0][0])
        self.assertEqual(win["direction"], "lower_is_better")

    def test_winner_highest_speed(self):
        """route_b (15 km/hr) wins highest_speed."""
        ids = self._route_ids()
        result = self.p.compare_routes(ids[0][0], ids[1][0])
        win = result["winner"]["highest_speed"]
        self.assertEqual(win["route_id"], ids[1][0])
        self.assertEqual(win["direction"], "higher_is_better")

    def test_winner_lowest_duration(self):
        """route_a (1 hr) wins lowest_duration."""
        ids = self._route_ids()
        result = self.p.compare_routes(ids[0][0], ids[1][0])
        win = result["winner"]["lowest_duration"]
        self.assertEqual(win["route_id"], ids[0][0])

    # --- error cases ---

    def test_unknown_route_id_returns_none(self):
        """Unknown route_id returns None for that side."""
        ids = self._route_ids()
        result = self.p.compare_routes(ids[0][0], 999999)
        self.assertIsNotNone(result["route_a"])
        self.assertIsNone(result["route_b"])
        # winner should still be None (need both sides)
        self.assertIsNone(result["winner"])

    def test_both_unknown(self):
        """Both route_ids unknown → both None."""
        result = self.p.compare_routes(999990, 999991)
        self.assertIsNone(result["route_a"])
        self.assertIsNone(result["route_b"])

    def test_cache_returns_same_result(self):
        """TTL cache should return identical result on second call."""
        ids = self._route_ids()
        r1 = self.p.compare_routes(ids[0][0], ids[1][0])
        r2 = self.p.compare_routes(ids[0][0], ids[1][0])
        self.assertEqual(r1, r2)


class TestCompareRoutesEndpoint(unittest.TestCase):
    """/api/persistence/compare-routes endpoint."""

    def setUp(self):
        try:
            from web.backend.main import app  # noqa: F401
            from fastapi.testclient import TestClient
            self.TestClient = TestClient
            self.app = app
        except Exception as e:
            self.skipTest(f"web.backend.main not importable: {e}")

    def test_endpoint_basic(self):
        client = self.TestClient(self.app)
        resp = client.get(
            "/api/persistence/compare-routes?route_id_a=1&route_id_b=2")
        self.assertIn(resp.status_code, (200, 503))
        if resp.status_code == 200:
            data = resp.json()
            self.assertIn("route_a", data)
            self.assertIn("route_b", data)
            self.assertIn("differences", data)
            self.assertIn("winner", data)

    def test_endpoint_same_route_returns_400(self):
        client = self.TestClient(self.app)
        resp = client.get(
            "/api/persistence/compare-routes?route_id_a=1&route_id_b=1")
        # Should return 400 (same route_id) or 503 (no persistence)
        self.assertIn(resp.status_code, (400, 503))

    def test_endpoint_missing_params(self):
        client = self.TestClient(self.app)
        resp = client.get("/api/persistence/compare-routes")
        # 422 for missing required params
        self.assertIn(resp.status_code, (422, 503))


if __name__ == "__main__":
    unittest.main()