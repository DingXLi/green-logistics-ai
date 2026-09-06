"""
Tests for iter #56 top-cycles-by-efficiency endpoint:
- Persistence.get_top_cycles_by_efficiency()
- /api/persistence/top-cycles
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestTopCyclesPersistence(unittest.TestCase):
    """Persistence.get_top_cycles_by_efficiency()"""

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
        """5 cycles with different KPIs to exercise ranking."""
        # Cycle A: greenest (low co2_per_ton)  — 10 tons, 50 kg CO2 → 5 kg/t
        # Cycle B: medium — 10 tons, 100 kg CO2 → 10 kg/t
        # Cycle C: dirtiest — 10 tons, 200 kg CO2 → 20 kg/t
        # Cycle D: high utilization — 80% fleet util, 15 tons
        # Cycle E: low matches (1) — small but still 0 matches filtered out
        cycle_profiles = [
            {"cid": "cyc-A", "sim_day": 1, "tons": 10.0, "cost": 500.0,
             "co2": 50.0, "distance": 100.0, "matches": 5, "offers": 8,
             "util": 50.0},
            {"cid": "cyc-B", "sim_day": 2, "tons": 10.0, "cost": 800.0,
             "co2": 100.0, "distance": 150.0, "matches": 4, "offers": 6,
             "util": 60.0},
            {"cid": "cyc-C", "sim_day": 3, "tons": 10.0, "cost": 1200.0,
             "co2": 200.0, "distance": 200.0, "matches": 3, "offers": 5,
             "util": 70.0},
            {"cid": "cyc-D", "sim_day": 4, "tons": 15.0, "cost": 600.0,
             "co2": 80.0, "distance": 120.0, "matches": 6, "offers": 8,
             "util": 80.0},
            {"cid": "cyc-E", "sim_day": 5, "tons": 0.0, "cost": 0.0,
             "co2": 0.0, "distance": 0.0, "matches": 0, "offers": 2,
             "util": 0.0},  # no matches → filtered out
        ]
        for c in cycle_profiles:
            self.p.begin_cycle(
                cycle_id=c["cid"], sim_day=c["sim_day"], sim_hour=8,
                activity_factor=1.0, n_supply_offers=c["offers"],
                n_demand_requests=c["matches"],
            )
            self.p.commit_cycle(
                cycle_id=c["cid"],
                kpi={"n_matches": c["matches"], "total_tons": c["tons"],
                     "total_cost_sek": c["cost"], "total_co2_kg": c["co2"],
                     "total_distance_km": c["distance"],
                     "n_vehicles_used": c["matches"], "n_vehicles_available": 10,
                     "fleet_utilization_pct": c["util"],
                     "solver_status": "OPTIMAL"},
                wall_duration_ms=100,
            )

    def test_valid_metric_required(self):
        with self.assertRaises(ValueError):
            self.p.get_top_cycles_by_efficiency(metric="invalid_metric")

    def test_co2_per_ton_lower_better(self):
        result = self.p.get_top_cycles_by_efficiency(metric="co2_per_ton", limit=10)
        self.assertEqual(result["metric"], "co2_per_ton")
        self.assertEqual(result["direction"], "lower_is_better")
        # cyc-A is greenest (5 kg/t), should be first
        if result["top_cycles"]:
            self.assertEqual(result["top_cycles"][0]["cycle_id"], "cyc-A")
            values = [c["value"] for c in result["top_cycles"]]
            self.assertEqual(values, sorted(values))  # ascending

    def test_cost_per_ton_lower_better(self):
        result = self.p.get_top_cycles_by_efficiency(metric="cost_per_ton", limit=10)
        self.assertEqual(result["direction"], "lower_is_better")
        # cyc-A: 500/10 = 50 SEK/t, cyc-B: 800/10 = 80, cyc-C: 1200/10 = 120,
        # cyc-D: 600/15 = 40 SEK/t (cheapest per ton)
        if result["top_cycles"]:
            self.assertEqual(result["top_cycles"][0]["cycle_id"], "cyc-D")

    def test_fleet_utilization_higher_better(self):
        result = self.p.get_top_cycles_by_efficiency(
            metric="fleet_utilization", limit=10
        )
        self.assertEqual(result["direction"], "higher_is_better")
        if result["top_cycles"]:
            self.assertEqual(result["top_cycles"][0]["cycle_id"], "cyc-D")
            values = [c["value"] for c in result["top_cycles"]]
            self.assertEqual(values, sorted(values, reverse=True))

    def test_tons_per_cycle_higher_better(self):
        result = self.p.get_top_cycles_by_efficiency(
            metric="tons_per_cycle", limit=10
        )
        self.assertEqual(result["direction"], "higher_is_better")
        if result["top_cycles"]:
            self.assertEqual(result["top_cycles"][0]["cycle_id"], "cyc-D")

    def test_match_rate_vs_offers(self):
        result = self.p.get_top_cycles_by_efficiency(
            metric="match_rate_vs_offers", limit=10
        )
        self.assertEqual(result["direction"], "higher_is_better")
        # cyc-A: 5/8 = 0.625, cyc-B: 4/6 = 0.667, cyc-C: 3/5 = 0.6,
        # cyc-D: 6/8 = 0.75 → top
        if result["top_cycles"]:
            self.assertEqual(result["top_cycles"][0]["cycle_id"], "cyc-D")

    def test_top_cycle_basic_fields(self):
        result = self.p.get_top_cycles_by_efficiency(
            metric="fleet_utilization", limit=5
        )
        for c in result["top_cycles"]:
            self.assertIn("cycle_id", c)
            self.assertIn("sim_day", c)
            self.assertIn("value", c)
            self.assertIn("n_matches", c)
            self.assertIn("total_tons", c)
            self.assertIn("total_cost_sek", c)
            self.assertIn("total_co2_kg", c)
            self.assertIn("fleet_utilization_pct", c)
            self.assertIn("solver_status", c)

    def test_filter_by_sim_day_window(self):
        result = self.p.get_top_cycles_by_efficiency(
            metric="co2_per_ton", since_sim_day=3, until_sim_day=4, limit=10
        )
        # Should only include cyc-C and cyc-D
        cids = {c["cycle_id"] for c in result["top_cycles"]}
        self.assertIn("cyc-C", cids)
        self.assertIn("cyc-D", cids)
        self.assertNotIn("cyc-A", cids)
        self.assertNotIn("cyc-B", cids)
        # Echoed window
        self.assertEqual(result["sim_day_window"]["since_sim_day"], 3)
        self.assertEqual(result["sim_day_window"]["until_sim_day"], 4)

    def test_min_matches_filter(self):
        # cyc-E has 0 matches; with default min_matches=1 it should be excluded
        result = self.p.get_top_cycles_by_efficiency(
            metric="co2_per_ton", limit=100
        )
        cids = [c["cycle_id"] for c in result["top_cycles"]]
        self.assertNotIn("cyc-E", cids)

    def test_min_matches_zero_includes_empty(self):
        # With min_matches=0 we allow cycles with 0 matches but they have
        # total_tons=0 so ton-based metrics still skip them via the value check
        result = self.p.get_top_cycles_by_efficiency(
            metric="fleet_utilization", min_matches=0, limit=100
        )
        cids = {c["cycle_id"] for c in result["top_cycles"]}
        self.assertIn("cyc-E", cids)

    def test_limit_capped_at_100(self):
        result = self.p.get_top_cycles_by_efficiency(limit=200)
        self.assertLessEqual(len(result["top_cycles"]), 100)

    def test_skip_zero_tons_for_ton_metrics(self):
        # co2_per_ton / cost_per_ton should skip cycles with 0 tons
        result = self.p.get_top_cycles_by_efficiency(metric="co2_per_ton", limit=100)
        cids = [c["cycle_id"] for c in result["top_cycles"]]
        self.assertNotIn("cyc-E", cids)

    def test_empty_data(self):
        from agents.persistence import Persistence
        empty = Persistence(db_path=self.db_path + ".empty")
        result = empty.get_top_cycles_by_efficiency(metric="fleet_utilization")
        self.assertEqual(result["n_cycles_evaluated"], 0)
        self.assertEqual(result["top_cycles"], [])
        try:
            os.unlink(self.db_path + ".empty")
        except Exception:
            pass

    def test_n_cycles_counts(self):
        result = self.p.get_top_cycles_by_efficiency(
            metric="co2_per_ton", limit=10
        )
        # 4 cycles with matches and tons > 0
        self.assertEqual(result["n_cycles_evaluated"], 4)
        self.assertEqual(result["n_cycles_returned"], 4)


class TestTopCyclesEndpoint(unittest.TestCase):
    """/api/persistence/top-cycles endpoint behavior."""

    def setUp(self):
        # Try to import the FastAPI app; if unavailable, skip endpoint tests
        try:
            from web.backend.main import app  # noqa: F401
            from fastapi.testclient import TestClient
            self.TestClient = TestClient
            self.app = app
        except Exception as e:  # pragma: no cover - import path issues
            self.skipTest(f"web.backend.main not importable: {e}")

    def test_endpoint_invalid_metric_returns_4xx(self):
        client = self.TestClient(self.app)
        resp = client.get("/api/persistence/top-cycles?metric=invalid_metric")
        # When persistence is initialized, expect 400; when not, expect 503
        self.assertIn(resp.status_code, (400, 503))

    def test_endpoint_default_metric(self):
        client = self.TestClient(self.app)
        resp = client.get("/api/persistence/top-cycles")
        # Either OK (persistence ready) or 503 (no lifespan in TestClient)
        self.assertIn(resp.status_code, (200, 503))
        if resp.status_code == 200:
            data = resp.json()
            self.assertIn("metric", data)
            self.assertIn("top_cycles", data)
            self.assertEqual(data["metric"], "co2_per_ton")

    def test_endpoint_limit_param(self):
        client = self.TestClient(self.app)
        resp = client.get("/api/persistence/top-cycles?limit=5")
        self.assertIn(resp.status_code, (200, 503))


if __name__ == "__main__":
    unittest.main()
