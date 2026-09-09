"""
Tests for iter #65 material-timeseries endpoint:
- Persistence.get_material_timeseries()
- /api/persistence/material-timeseries
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestMaterialTimeseriesPersistence(unittest.TestCase):
    """Persistence.get_material_timeseries()"""

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
        """6 cycles × 2 materials with different trends.

        concrete: improving volume (5t → 30t over 6 cycles)
        metal_scrap: stable volume (10t throughout)
        """
        for i, (c_tons, m_tons) in enumerate([
            (5.0, 10.0),    # cycle 1: low concrete, stable metal
            (10.0, 10.0),
            (15.0, 10.0),
            (20.0, 10.0),
            (25.0, 10.0),
            (30.0, 10.0),   # cycle 6: high concrete
        ]):
            cid = f"mts-{i+1}"
            self.p.begin_cycle(
                cycle_id=cid, sim_day=i+1, sim_hour=8,
                activity_factor=1.0, n_supply_offers=2, n_demand_requests=2,
            )
            # Match each material separately so we have 2 matches per cycle
            self.p.commit_cycle(
                cycle_id=cid,
                kpi={"n_matches": 2, "total_tons": c_tons + m_tons,
                     "total_cost_sek": 100, "total_co2_kg": 50,
                     "total_distance_km": 50, "n_vehicles_used": 1,
                     "n_vehicles_available": 5,
                     "fleet_utilization_pct": 20, "solver_status": "OPTIMAL"},
                wall_duration_ms=100,
            )
            # Concrete supply
            self.p.record_supply(cid, {
                "agent_id": f"S_CONCRETE_{cid}",
                "location": {"lat": 57.7, "lon": 12.9},
                "material_type": "concrete",
                "available_tons": c_tons * 2,
                "quality_score": 80.0,
            })
            self.p.record_demand(cid, {
                "demand_id": f"D_CONCRETE_{cid}",
                "material_type": "concrete",
                "required_tons": c_tons,
                "preferred_materials": ["concrete"],
                "location": {"lat": 57.71, "lon": 12.91},
            })
            self.p.record_match(cid, {
                "supply_id": f"S_CONCRETE_{cid}",
                "demand_id": f"D_CONCRETE_{cid}",
                "material_type": "concrete", "tons": c_tons,
                "distance_km": 50.0, "estimated_profit_sek": 100.0,
            })
            # Metal supply
            self.p.record_supply(cid, {
                "agent_id": f"S_METAL_{cid}",
                "location": {"lat": 57.7, "lon": 12.9},
                "material_type": "metal_scrap",
                "available_tons": m_tons * 2,
                "quality_score": 85.0,
            })
            self.p.record_demand(cid, {
                "demand_id": f"D_METAL_{cid}",
                "material_type": "metal_scrap",
                "required_tons": m_tons,
                "preferred_materials": ["metal_scrap"],
                "location": {"lat": 57.71, "lon": 12.91},
            })
            self.p.record_match(cid, {
                "supply_id": f"S_METAL_{cid}",
                "demand_id": f"D_METAL_{cid}",
                "material_type": "metal_scrap", "tons": m_tons,
                "distance_km": 50.0, "estimated_profit_sek": 100.0,
            })

    # --- core ---

    def test_basic_envelope(self):
        """Returns standard timeseries envelope."""
        result = self.p.get_material_timeseries()
        for k in ("metric", "metric_description", "direction",
                  "filter", "n_rows_evaluated", "n_rows_returned",
                  "timeseries", "per_material_summary"):
            self.assertIn(k, result)

    def test_default_metric(self):
        """Default metric is matched_tons."""
        result = self.p.get_material_timeseries()
        self.assertEqual(result["metric"], "matched_tons")
        self.assertEqual(result["direction"], "higher_is_better")

    def test_n_rows(self):
        """6 cycles × 2 materials = 12 rows."""
        result = self.p.get_material_timeseries()
        self.assertEqual(result["n_rows_evaluated"], 12)

    def test_each_row_has_fields(self):
        """Each row has full context."""
        result = self.p.get_material_timeseries(limit=1)
        row = result["timeseries"][0]
        for k in ("cycle_id", "sim_day", "material_type", "metric_value",
                  "n_matches", "matched_tons", "n_offers", "n_demands"):
            self.assertIn(k, row)

    # --- per-material summary ---

    def test_per_material_summary(self):
        """per_material_summary has both materials."""
        result = self.p.get_material_timeseries()
        self.assertIn("concrete", result["per_material_summary"])
        self.assertIn("metal_scrap", result["per_material_summary"])

    def test_concrete_summary(self):
        """concrete: 6 cycles, mean=17.5t, latest=30t, improving."""
        result = self.p.get_material_timeseries()
        s = result["per_material_summary"]["concrete"]
        self.assertEqual(s["n_cycles"], 6)
        # Mean: (5+10+15+20+25+30)/6 = 17.5
        self.assertEqual(s["mean_value"], 17.5)
        self.assertEqual(s["min_value"], 5.0)
        self.assertEqual(s["max_value"], 30.0)
        self.assertEqual(s["latest_value"], 30.0)
        self.assertEqual(s["trend"], "improving")

    def test_metal_summary(self):
        """metal_scrap: 6 cycles, mean=10t, stable."""
        result = self.p.get_material_timeseries()
        s = result["per_material_summary"]["metal_scrap"]
        self.assertEqual(s["n_cycles"], 6)
        self.assertEqual(s["mean_value"], 10.0)
        self.assertEqual(s["latest_value"], 10.0)
        self.assertEqual(s["trend"], "stable")

    # --- filters ---

    def test_material_filter(self):
        """material_type filter restricts to single material."""
        result = self.p.get_material_timeseries(material_type="concrete")
        self.assertEqual(result["n_rows_evaluated"], 6)
        for r in result["timeseries"]:
            self.assertEqual(r["material_type"], "concrete")

    def test_sim_day_window(self):
        """since/until_sim_day restricts window."""
        result = self.p.get_material_timeseries(since_sim_day=2, until_sim_day=4)
        # Cycles 2-4 × 2 materials = 6 rows
        self.assertEqual(result["n_rows_evaluated"], 6)

    def test_metric_n_matches(self):
        """Switch metric to n_matches."""
        result = self.p.get_material_timeseries(metric="n_matches")
        self.assertEqual(result["metric"], "n_matches")
        # Each cycle × material has exactly 1 match
        for r in result["timeseries"]:
            self.assertEqual(r["metric_value"], 1)

    def test_metric_co2_per_ton(self):
        """Switch metric to co2_per_ton (lower_is_better)."""
        result = self.p.get_material_timeseries(metric="co2_per_ton")
        self.assertEqual(result["metric"], "co2_per_ton")
        self.assertEqual(result["direction"], "lower_is_better")
        # Total CO2 50kg / total matched (varies by cycle)
        # Cycle 1: 50/(5+10) = 50/15 ≈ 3.33 (split evenly)
        # Concrete cycle 1: 5t * (50/15) ≈ 16.67 kg → 3.33 kg/t
        v1 = result["timeseries"][0]["metric_value"]
        self.assertAlmostEqual(v1, 3.333, places=2)

    def test_invalid_metric_raises(self):
        """Unknown metric raises ValueError."""
        with self.assertRaises(ValueError):
            self.p.get_material_timeseries(metric="invalid")

    def test_cache_returns_same_result(self):
        """TTL cache returns identical result on second call."""
        r1 = self.p.get_material_timeseries()
        r2 = self.p.get_material_timeseries()
        self.assertEqual(r1, r2)


class TestMaterialTimeseriesEndpoint(unittest.TestCase):
    """/api/persistence/material-timeseries endpoint."""

    def setUp(self):
        try:
            from web.backend.main import app  # noqa: F401
            from fastapi.testclient import TestClient
            self.TestClient = TestClient
            self.app = app
        except Exception as e:
            self.skipTest(f"web.backend.main not importable: {e}")

    def test_endpoint_default(self):
        client = self.TestClient(self.app)
        resp = client.get("/api/persistence/material-timeseries")
        self.assertIn(resp.status_code, (200, 503))
        if resp.status_code == 200:
            data = resp.json()
            self.assertIn("metric", data)
            self.assertIn("timeseries", data)
            self.assertIn("per_material_summary", data)

    def test_endpoint_material_filter(self):
        client = self.TestClient(self.app)
        resp = client.get(
            "/api/persistence/material-timeseries?material_type=concrete")
        self.assertIn(resp.status_code, (200, 503))

    def test_endpoint_metric(self):
        client = self.TestClient(self.app)
        resp = client.get(
            "/api/persistence/material-timeseries?metric=n_matches")
        self.assertIn(resp.status_code, (200, 503))

    def test_endpoint_sim_day_window(self):
        client = self.TestClient(self.app)
        resp = client.get(
            "/api/persistence/material-timeseries?since_sim_day=1&until_sim_day=5")
        self.assertIn(resp.status_code, (200, 503))

    def test_endpoint_invalid_metric_returns_400(self):
        client = self.TestClient(self.app)
        resp = client.get(
            "/api/persistence/material-timeseries?metric=invalid")
        self.assertIn(resp.status_code, (400, 503))


if __name__ == "__main__":
    unittest.main()