"""
Tests for iter #59 top-facilities-by-distance endpoint:
- Persistence.get_top_facilities_by_distance()
- /api/persistence/top-facilities

Real Sweden facilities (data/real_sweden_facilities.ALL_FACILITIES) are used
as demand_ids. Test seeds 3 facility-IDs from ALL_FACILITIES so that the
metadata enrichment code path is exercised.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestTopFacilitiesPersistence(unittest.TestCase):
    """Persistence.get_top_facilities_by_distance()"""

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
        """Seed 3 cycles with 3 real-Sweden facilities of varying match quality.

        Facility profile:
        - GBG_RENOVA_SYA: well-served (paper_cardboard, 3 matches, avg 3km)
        - GBG_STENA: under-served (metal_scrap, 1 match, 4km)
        - GBG_HARBOR: large capacity, no matches (hav harbor_cargo, 800t/day)
        """
        # Cycle 1: GBG_RENOVA_SYA (1 match, 2.5km), GBG_STENA (1 match, 4.0km)
        self.p.begin_cycle(
            cycle_id="fac-1", sim_day=1, sim_hour=8, activity_factor=1.0,
            n_supply_offers=2, n_demand_requests=2,
        )
        self.p.record_demand("fac-1", {
            "id": "GBG_RENOVA_SYA", "name": "Renova Sävenäs",
            "location": {"lat": 57.7321, "lon": 12.0123},
            "material_type": "paper_cardboard", "required_tons": 10.0,
        })
        self.p.record_demand("fac-1", {
            "id": "GBG_STENA", "name": "Stena Recycling",
            "location": {"lat": 57.7156, "lon": 11.9812},
            "material_type": "metal_scrap", "required_tons": 5.0,
        })
        self.p.record_match("fac-1", {
            "supply_id": "SUP_A", "demand_id": "GBG_RENOVA_SYA",
            "material_type": "paper_cardboard", "tons": 8.0,
            "distance_km": 2.5, "estimated_profit_sek": 50.0,
        })
        self.p.record_match("fac-1", {
            "supply_id": "SUP_B", "demand_id": "GBG_STENA",
            "material_type": "metal_scrap", "tons": 4.0,
            "distance_km": 4.0, "estimated_profit_sek": 30.0,
        })
        self.p.record_route("fac-1", {
            "vehicle_id": "VH_1", "stops": [],
            "distance_km": 6.5, "duration_hours": 1.0,
            "cost_sek": 80.0, "co2_kg": 4.0,
        })
        self.p.commit_cycle("fac-1", kpi={
            "n_matches": 2, "total_tons": 12.0,
            "total_cost_sek": 80.0, "total_co2_kg": 4.0,
            "total_distance_km": 6.5,
            "n_vehicles_used": 1, "n_vehicles_available": 5,
            "fleet_utilization_pct": 30.0, "solver_status": "OPTIMAL",
        }, wall_duration_ms=100)

        # Cycle 2: GBG_RENOVA_SYA gets 2 more matches (closer suppliers)
        self.p.begin_cycle(
            cycle_id="fac-2", sim_day=2, sim_hour=8, activity_factor=1.0,
            n_supply_offers=2, n_demand_requests=1,
        )
        self.p.record_demand("fac-2", {
            "id": "GBG_RENOVA_SYA", "name": "Renova Sävenäs",
            "location": {"lat": 57.7321, "lon": 12.0123},
            "material_type": "paper_cardboard", "required_tons": 12.0,
        })
        self.p.record_demand("fac-2", {
            "id": "GBG_HARBOR", "name": "Göteborgs Hamn",
            "location": {"lat": 57.6997, "lon": 11.8583},
            "material_type": "metal_scrap", "required_tons": 50.0,
        })
        self.p.record_match("fac-2", {
            "supply_id": "SUP_C", "demand_id": "GBG_RENOVA_SYA",
            "material_type": "paper_cardboard", "tons": 7.0,
            "distance_km": 3.0, "estimated_profit_sek": 40.0,
        })
        self.p.record_match("fac-2", {
            "supply_id": "SUP_D", "demand_id": "GBG_RENOVA_SYA",
            "material_type": "paper_cardboard", "tons": 5.0,
            "distance_km": 4.0, "estimated_profit_sek": 30.0,
        })
        self.p.record_route("fac-2", {
            "vehicle_id": "VH_1", "stops": [],
            "distance_km": 7.0, "duration_hours": 1.0,
            "cost_sek": 70.0, "co2_kg": 3.0,
        })
        self.p.commit_cycle("fac-2", kpi={
            "n_matches": 2, "total_tons": 12.0,
            "total_cost_sek": 70.0, "total_co2_kg": 3.0,
            "total_distance_km": 7.0,
            "n_vehicles_used": 1, "n_vehicles_available": 5,
            "fleet_utilization_pct": 25.0, "solver_status": "OPTIMAL",
        }, wall_duration_ms=100)

        # Cycle 3: ONLY GBG_HARBOR demanded but no matches → tests no-match skip
        self.p.begin_cycle(
            cycle_id="fac-3", sim_day=3, sim_hour=8, activity_factor=1.0,
            n_supply_offers=0, n_demand_requests=1,
        )
        self.p.record_demand("fac-3", {
            "id": "GBG_HARBOR", "name": "Göteborgs Hamn",
            "location": {"lat": 57.6997, "lon": 11.8583},
            "material_type": "concrete", "required_tons": 100.0,
        })
        self.p.commit_cycle("fac-3", kpi={
            "n_matches": 0, "total_tons": 0,
            "total_cost_sek": 0, "total_co2_kg": 0,
            "total_distance_km": 0,
            "n_vehicles_used": 0, "n_vehicles_available": 5,
            "fleet_utilization_pct": 0.0, "solver_status": "NO_FEASIBLE",
        }, wall_duration_ms=50)

    # ----- Metric validation -----

    def test_valid_metric_required(self):
        with self.assertRaises(ValueError) as ctx:
            self.p.get_top_facilities_by_distance(metric="bogus_metric")
        self.assertIn("Unknown metric", str(ctx.exception))

    def test_empty_db_returns_empty(self):
        """Empty DB → 0 facilities evaluated, empty list."""
        empty_db = tempfile.NamedTemporaryFile(
            suffix=".db", delete=False, dir="/tmp"
        )
        empty_db.close()
        try:
            from agents.persistence import Persistence
            ep = Persistence(db_path=empty_db.name)
            result = ep.get_top_facilities_by_distance()
            self.assertEqual(result["n_facilities_evaluated"], 0)
            self.assertEqual(result["top_facilities"], [])
        finally:
            try:
                os.unlink(empty_db.name)
            except Exception:
                pass

    # ----- avg_distance metric (lower is better) -----

    def test_avg_distance_lower_better(self):
        result = self.p.get_top_facilities_by_distance(metric="avg_distance")
        # GBG_HARBOR has 0 matches → excluded
        # GBG_RENOVA_SYA: 3 matches (2.5 + 3.0 + 4.0 = 9.5 / 3 = 3.167)
        # GBG_STENA: 1 match (4.0)
        self.assertEqual(result["metric"], "avg_distance")
        self.assertEqual(result["direction"], "lower_is_better")
        self.assertEqual(result["n_facilities_evaluated"], 2)
        # Sorted ascending → GBG_RENOVA_SYA (3.17) first
        self.assertEqual(result["top_facilities"][0]["facility_id"], "GBG_RENOVA_SYA")
        self.assertEqual(result["top_facilities"][1]["facility_id"], "GBG_STENA")

    def test_avg_distance_excludes_no_match(self):
        result = self.p.get_top_facilities_by_distance(metric="avg_distance")
        ids = [f["facility_id"] for f in result["top_facilities"]]
        self.assertNotIn("GBG_HARBOR", ids)  # no matches → excluded

    def test_avg_distance_value(self):
        result = self.p.get_top_facilities_by_distance(metric="avg_distance")
        renova = next(f for f in result["top_facilities"]
                      if f["facility_id"] == "GBG_RENOVA_SYA")
        # avg of (2.5, 3.0, 4.0) = 3.17 (rounded to 2 decimals)
        self.assertAlmostEqual(renova["value"], 3.17, places=1)
        self.assertEqual(renova["n_matches"], 3)

    # ----- total_matched_tons (higher is better) -----

    def test_total_matched_tons_higher_better(self):
        result = self.p.get_top_facilities_by_distance(metric="total_matched_tons")
        # GBG_RENOVA_SYA: 8+7+5 = 20t
        # GBG_STENA: 4t
        # Sorted DESC
        self.assertEqual(result["direction"], "higher_is_better")
        self.assertEqual(result["top_facilities"][0]["facility_id"], "GBG_RENOVA_SYA")
        self.assertEqual(result["top_facilities"][0]["value"], 20.0)
        self.assertEqual(result["top_facilities"][1]["value"], 4.0)

    # ----- match_rate metric -----

    def test_match_rate(self):
        result = self.p.get_top_facilities_by_distance(metric="match_rate")
        # GBG_RENOVA_SYA: 3 matches / 2 cycles (fac-1 + fac-2) = 1.5
        # GBG_STENA: 1 match / 1 cycle = 1.0
        renova = next(f for f in result["top_facilities"]
                      if f["facility_id"] == "GBG_RENOVA_SYA")
        stena = next(f for f in result["top_facilities"]
                     if f["facility_id"] == "GBG_STENA")
        self.assertAlmostEqual(renova["value"], 1.5, places=2)
        self.assertAlmostEqual(stena["value"], 1.0, places=2)

    # ----- utilization_pct metric -----

    def test_utilization_pct(self):
        result = self.p.get_top_facilities_by_distance(metric="utilization_pct")
        # GBG_RENOVA_SYA capacity = 350t/day, 2 cycles with demand
        #   utilization = 20 / (350 * 2) = 2.86%
        # GBG_STENA capacity = 200t/day, 1 cycle with demand
        #   utilization = 4 / 200 = 2.0%
        renova = next(f for f in result["top_facilities"]
                      if f["facility_id"] == "GBG_RENOVA_SYA")
        self.assertEqual(renova["processing_capacity_tons_per_day"], 350)
        self.assertAlmostEqual(renova["value"], 2.86, places=1)

    def test_utilization_pct_no_capacity(self):
        """Facilities without capacity should not appear in utilization ranking."""
        result = self.p.get_top_facilities_by_distance(
            metric="utilization_pct",
            facility_ids=["NONEXISTENT_FAC"],
        )
        # No capacity → value is None → excluded
        self.assertEqual(result["n_facilities_evaluated"], 0)

    # ----- co2_per_ton metric -----

    def test_co2_per_ton(self):
        result = self.p.get_top_facilities_by_distance(metric="co2_per_ton")
        # co2 = sum of routes.co2_kg for cycles with this facility's matches
        # cycle 1 route co2 = 4kg, cycle 2 route co2 = 3kg → total = 7kg
        # GBG_RENOVA_SYA: 20t matched → 7/20 = 0.35 kg/t
        # GBG_STENA: 4t matched, only cycle 1 → 4/4 = 1.0 kg/t
        renova = next(f for f in result["top_facilities"]
                      if f["facility_id"] == "GBG_RENOVA_SYA")
        stena = next(f for f in result["top_facilities"]
                     if f["facility_id"] == "GBG_STENA")
        self.assertAlmostEqual(renova["value"], 0.35, places=2)
        self.assertAlmostEqual(stena["value"], 1.0, places=2)

    # ----- Filters -----

    def test_city_filter(self):
        """city=Göteborg should only include Göteborg facilities."""
        result = self.p.get_top_facilities_by_distance(city="Göteborg")
        ids = [f["facility_id"] for f in result["top_facilities"]]
        # All 3 seeded facilities are Göteborg (no Borås/Stockholm)
        self.assertIn("GBG_RENOVA_SYA", ids)
        for f in result["top_facilities"]:
            self.assertEqual(f["city"], "Göteborg")

    def test_city_filter_nonexistent(self):
        """city=Tokyo → 0 facilities."""
        result = self.p.get_top_facilities_by_distance(city="Tokyo")
        self.assertEqual(result["n_facilities_evaluated"], 0)

    def test_facility_type_filter(self):
        """facility_type=metal_recovery → only Stena."""
        result = self.p.get_top_facilities_by_distance(
            metric="avg_distance", facility_type="metal_recovery"
        )
        ids = [f["facility_id"] for f in result["top_facilities"]]
        self.assertEqual(ids, ["GBG_STENA"])

    def test_material_filter(self):
        """material=paper_cardboard → only Renova."""
        result = self.p.get_top_facilities_by_distance(
            metric="avg_distance", material_type="paper_cardboard"
        )
        ids = [f["facility_id"] for f in result["top_facilities"]]
        self.assertEqual(ids, ["GBG_RENOVA_SYA"])

    def test_explicit_facility_ids(self):
        """facility_ids filter restricts to those IDs only."""
        result = self.p.get_top_facilities_by_distance(
            facility_ids=["GBG_RENOVA_SYA", "GBG_HARBOR"]
        )
        ids = [f["facility_id"] for f in result["top_facilities"]]
        self.assertIn("GBG_RENOVA_SYA", ids)
        # HARBOR has no matches → excluded
        self.assertNotIn("GBG_HARBOR", ids)

    def test_sim_day_window(self):
        """since_sim_day=2 should only count cycle 2+."""
        result = self.p.get_top_facilities_by_distance(
            metric="avg_distance", since_sim_day=2, until_sim_day=2
        )
        # GBG_RENOVA_SYA: only cycle 2's matches (3.0, 4.0) → avg 3.5
        renova = result["top_facilities"][0]
        self.assertEqual(renova["facility_id"], "GBG_RENOVA_SYA")
        self.assertAlmostEqual(renova["value"], 3.5, places=1)

    # ----- Field enrichment -----

    def test_top_facility_basic_fields(self):
        result = self.p.get_top_facilities_by_distance(limit=1)
        f = result["top_facilities"][0]
        # Required fields
        for key in ("facility_id", "name", "city", "facility_type",
                    "processing_capacity_tons_per_day", "operator",
                    "source", "value", "n_matches", "n_cycles_with_demand",
                    "total_matched_tons", "total_required_tons",
                    "avg_match_distance_km", "min_match_distance_km",
                    "max_match_distance_km", "avg_match_tons",
                    "utilization_pct", "last_sim_day"):
            self.assertIn(key, f, f"Missing field {key}")
        # Real Sweden facility metadata should be present
        self.assertEqual(f["city"], "Göteborg")
        self.assertEqual(f["processing_capacity_tons_per_day"], 350)

    def test_filter_block(self):
        """The 'filter' block in response should reflect query params."""
        result = self.p.get_top_facilities_by_distance(
            city="Göteborg", material_type="paper_cardboard",
            since_sim_day=1, until_sim_day=10
        )
        self.assertEqual(result["filter"]["city"], "Göteborg")
        self.assertEqual(result["filter"]["material_type"], "paper_cardboard")
        self.assertEqual(result["filter"]["since_sim_day"], 1)
        self.assertEqual(result["filter"]["until_sim_day"], 10)

    # ----- Limit -----

    def test_limit(self):
        result = self.p.get_top_facilities_by_distance(limit=1)
        self.assertEqual(len(result["top_facilities"]), 1)
        self.assertEqual(result["n_facilities_returned"], 1)


class TestTopFacilitiesEndpoint(unittest.TestCase):
    """/api/persistence/top-facilities endpoint behavior."""

    def setUp(self):
        try:
            from web.backend.main import app
            from fastapi.testclient import TestClient
            self.TestClient = TestClient
            self.app = app
        except Exception as e:  # pragma: no cover
            self.skipTest(f"web.backend.main not importable: {e}")

    def test_endpoint_default_metric(self):
        client = self.TestClient(self.app)
        resp = client.get("/api/persistence/top-facilities")
        self.assertIn(resp.status_code, (200, 503))
        if resp.status_code == 200:
            data = resp.json()
            self.assertEqual(data["metric"], "avg_distance")
            self.assertIn("top_facilities", data)
            self.assertIn("filter", data)
            self.assertIn("n_facilities_evaluated", data)

    def test_endpoint_invalid_metric_returns_4xx(self):
        client = self.TestClient(self.app)
        resp = client.get("/api/persistence/top-facilities?metric=invalid_metric")
        self.assertIn(resp.status_code, (400, 503))

    def test_endpoint_city_filter(self):
        client = self.TestClient(self.app)
        resp = client.get("/api/persistence/top-facilities?city=Göteborg")
        self.assertIn(resp.status_code, (200, 503))
        if resp.status_code == 200:
            data = resp.json()
            self.assertEqual(data["filter"]["city"], "Göteborg")

    def test_endpoint_facility_ids_csv(self):
        client = self.TestClient(self.app)
        resp = client.get(
            "/api/persistence/top-facilities"
            "?facility_ids=GBG_RENOVA_SYA,GBG_STENA"
        )
        self.assertIn(resp.status_code, (200, 503))
        if resp.status_code == 200:
            data = resp.json()
            self.assertEqual(
                set(data["filter"]["facility_ids"]),
                {"GBG_RENOVA_SYA", "GBG_STENA"},
            )

    def test_endpoint_invalid_sim_day_window(self):
        client = self.TestClient(self.app)
        resp = client.get(
            "/api/persistence/top-facilities?since_sim_day=20&until_sim_day=10"
        )
        self.assertIn(resp.status_code, (400, 503))

    def test_endpoint_empty_facility_ids(self):
        """Empty facility_ids=,, should 400."""
        client = self.TestClient(self.app)
        resp = client.get("/api/persistence/top-facilities?facility_ids=,,")
        self.assertIn(resp.status_code, (400, 503))

    def test_endpoint_limit_param(self):
        client = self.TestClient(self.app)
        resp = client.get("/api/persistence/top-facilities?limit=5")
        self.assertIn(resp.status_code, (200, 503))


if __name__ == "__main__":
    unittest.main()
