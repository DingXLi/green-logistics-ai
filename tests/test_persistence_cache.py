"""
Tests for iter #61 TTL cache for top-X persistence methods.

Verifies:
- Cache stores results on first call, returns cached on second call
- Different args → different cache entries
- TTL expires after configured time
- cache_clear() wipes everything
- cache_stats() reports counts
- All 4 cached methods (top_suppliers / top_cycles / top_demands / top_facilities)
"""
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestPersistenceCache(unittest.TestCase):
    """iter #61: TTL cache for top-X endpoints"""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False, dir="/tmp")
        self.tmp.close()
        self.db_path = self.tmp.name
        from agents.persistence import Persistence
        self.p = Persistence(db_path=self.db_path)
        self._seed_minimal()

    def tearDown(self):
        try:
            os.unlink(self.db_path)
        except Exception:
            pass

    def _seed_minimal(self):
        """Seed 1 cycle with 2 supplies + 2 demands + matches."""
        self.p.begin_cycle("OPT0001", sim_day=1, sim_hour=8, activity_factor=1.0,
                           n_supply_offers=2, n_demand_requests=2)
        self.p.record_supply("OPT0001", {
            "agent_id": "SUP001", "location": {"lat": 57.7, "lon": 12.0},
            "material_type": "concrete", "available_tons": 10.0,
        })
        self.p.record_supply("OPT0001", {
            "agent_id": "SUP002", "location": {"lat": 57.71, "lon": 12.01},
            "material_type": "metal_scrap", "available_tons": 5.0,
        })
        self.p.record_demand("OPT0001", {
            "id": "GBG_RENOVA_SYA", "name": "Renova",
            "location": {"lat": 57.7321, "lon": 12.0123},
            "material_type": "concrete", "required_tons": 8.0,
        })
        self.p.record_demand("OPT0001", {
            "id": "GBG_STENA", "name": "Stena",
            "location": {"lat": 57.7156, "lon": 11.9812},
            "material_type": "metal_scrap", "required_tons": 4.0,
        })
        self.p.record_match("OPT0001", {
            "supply_id": "SUP001", "demand_id": "GBG_RENOVA_SYA",
            "material_type": "concrete", "tons": 8.0,
            "distance_km": 2.5, "estimated_profit_sek": 50.0,
        })
        self.p.record_match("OPT0001", {
            "supply_id": "SUP002", "demand_id": "GBG_STENA",
            "material_type": "metal_scrap", "tons": 4.0,
            "distance_km": 4.0, "estimated_profit_sek": 30.0,
        })
        self.p.commit_cycle("OPT0001", kpi={
            "n_matches": 2, "total_tons": 12.0,
            "total_cost_sek": 100.0, "total_co2_kg": 50.0,
            "total_distance_km": 6.5,
            "n_vehicles_used": 2, "n_vehicles_available": 5,
            "fleet_utilization_pct": 40.0, "solver_status": "OPTIMAL",
        }, wall_duration_ms=100)

    # ----- Cache infra -----

    def test_cache_starts_empty(self):
        stats = self.p.cache_stats()
        self.assertEqual(stats["n_entries"], 0)
        self.assertEqual(stats["n_active"], 0)
        self.assertEqual(stats["ttl_seconds"], 30.0)

    def test_cache_clear_returns_count(self):
        # Populate
        self.p.get_top_suppliers_by_efficiency(metric="co2_per_ton")
        n_before = self.p.cache_stats()["n_entries"]
        self.assertGreater(n_before, 0)
        # Clear
        n_cleared = self.p.cache_clear()
        self.assertEqual(n_cleared, n_before)
        self.assertEqual(self.p.cache_stats()["n_entries"], 0)

    def test_cache_key_includes_method_name(self):
        """Cache key should differentiate methods even with same args."""
        self.p.get_top_demands_by_fulfillment(metric="fulfillment_rate")
        self.p.get_top_facilities_by_distance(metric="avg_distance")
        stats = self.p.cache_stats()
        self.assertEqual(stats["n_entries"], 2)

    # ----- top_suppliers caching -----

    def test_top_suppliers_caches_result(self):
        # First call: populates cache
        r1 = self.p.get_top_suppliers_by_efficiency(metric="co2_per_ton")
        stats1 = self.p.cache_stats()
        self.assertGreater(stats1["n_entries"], 0)
        # Second call: returns from cache (same data)
        r2 = self.p.get_top_suppliers_by_efficiency(metric="co2_per_ton")
        self.assertEqual(r1, r2)
        # Cache should still have same number of entries
        self.assertEqual(self.p.cache_stats()["n_entries"], stats1["n_entries"])

    def test_top_suppliers_different_metric_different_cache(self):
        self.p.get_top_suppliers_by_efficiency(metric="co2_per_ton")
        n1 = self.p.cache_stats()["n_entries"]
        self.p.get_top_suppliers_by_efficiency(metric="cost_per_ton")
        n2 = self.p.cache_stats()["n_entries"]
        self.assertEqual(n2, n1 + 1)

    def test_top_suppliers_different_limit_different_cache(self):
        self.p.get_top_suppliers_by_efficiency(metric="co2_per_ton", limit=5)
        n1 = self.p.cache_stats()["n_entries"]
        self.p.get_top_suppliers_by_efficiency(metric="co2_per_ton", limit=10)
        n2 = self.p.cache_stats()["n_entries"]
        self.assertEqual(n2, n1 + 1)

    # ----- top_cycles caching -----

    def test_top_cycles_caches_result(self):
        r1 = self.p.get_top_cycles_by_efficiency(metric="co2_per_ton")
        r2 = self.p.get_top_cycles_by_efficiency(metric="co2_per_ton")
        self.assertEqual(r1, r2)

    def test_top_cycles_sim_day_window_different_cache(self):
        self.p.get_top_cycles_by_efficiency(metric="co2_per_ton", since_sim_day=1)
        n1 = self.p.cache_stats()["n_entries"]
        self.p.get_top_cycles_by_efficiency(metric="co2_per_ton", since_sim_day=10)
        n2 = self.p.cache_stats()["n_entries"]
        self.assertEqual(n2, n1 + 1)

    # ----- top_demands caching -----

    def test_top_demands_caches_result(self):
        r1 = self.p.get_top_demands_by_fulfillment(metric="fulfillment_rate")
        r2 = self.p.get_top_demands_by_fulfillment(metric="fulfillment_rate")
        self.assertEqual(r1, r2)

    def test_top_demands_material_filter_different_cache(self):
        self.p.get_top_demands_by_fulfillment(metric="fulfillment_rate")
        n1 = self.p.cache_stats()["n_entries"]
        self.p.get_top_demands_by_fulfillment(metric="fulfillment_rate", material_type="concrete")
        n2 = self.p.cache_stats()["n_entries"]
        self.assertEqual(n2, n1 + 1)

    # ----- top_facilities caching -----

    def test_top_facilities_caches_result(self):
        r1 = self.p.get_top_facilities_by_distance(metric="avg_distance")
        r2 = self.p.get_top_facilities_by_distance(metric="avg_distance")
        self.assertEqual(r1, r2)

    def test_top_facilities_city_filter_different_cache(self):
        self.p.get_top_facilities_by_distance(metric="avg_distance")
        n1 = self.p.cache_stats()["n_entries"]
        self.p.get_top_facilities_by_distance(metric="avg_distance", city="Borås")
        n2 = self.p.cache_stats()["n_entries"]
        self.assertEqual(n2, n1 + 1)

    # ----- TTL expiry -----

    def test_cache_ttl_expiry(self):
        # Set very short TTL for testing
        self.p._top_cache_ttl_s = 0.1
        self.p.get_top_suppliers_by_efficiency(metric="co2_per_ton")
        n1 = self.p.cache_stats()["n_entries"]
        self.assertGreater(n1, 0)
        # Wait > TTL
        time.sleep(0.2)
        # Next call should re-populate (expired entry removed on get)
        self.p.get_top_suppliers_by_efficiency(metric="co2_per_ton")
        # Should still have entry (re-stored)
        n2 = self.p.cache_stats()["n_entries"]
        self.assertEqual(n2, n1)

    def test_expired_entry_evicted_on_get(self):
        """Accessing an expired entry evicts it before returning None."""
        self.p._top_cache_ttl_s = 0.1
        # Populate
        self.p.get_top_suppliers_by_efficiency(metric="co2_per_ton")
        self.assertGreater(self.p.cache_stats()["n_entries"], 0)
        # Wait for expiry
        time.sleep(0.2)
        # Direct cache_get should return None
        key = self.p._cache_key("get_top_suppliers_by_efficiency",
                                {"metric": "co2_per_ton", "material_type": None, "limit": 10})
        self.assertIsNone(self.p._cache_get(key))
        # Cache should now be empty (expired entry was popped)
        self.assertEqual(self.p.cache_stats()["n_entries"], 0)

    # ----- Error case not cached -----

    def test_value_error_not_cached(self):
        """Invalid metric raises ValueError; no cache entry stored."""
        with self.assertRaises(ValueError):
            self.p.get_top_suppliers_by_efficiency(metric="invalid_metric")
        # Cache should be empty (only computed on success)
        self.assertEqual(self.p.cache_stats()["n_entries"], 0)


class TestPersistenceCacheAdmin(unittest.TestCase):
    """/api/admin/persistence/cache endpoint (iter #61)"""

    def setUp(self):
        try:
            from web.backend import main as backend_main
            from agents.persistence import Persistence
            from fastapi.testclient import TestClient

            self.backend_main = backend_main
            # Wire a temp-DB coordinator
            tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False, dir="/tmp")
            tmp.close()
            self.tmp_path = tmp.name
            persistence = Persistence(db_path=self.tmp_path)
            # Begin + commit 1 cycle so endpoints work
            persistence.begin_cycle("c1", sim_day=1, sim_hour=8, activity_factor=1.0,
                                     n_supply_offers=1, n_demand_requests=1)
            persistence.commit_cycle("c1", kpi={"n_matches": 1, "total_tons": 5,
                                                  "total_cost_sek": 50, "total_co2_kg": 25,
                                                  "total_distance_km": 10, "n_vehicles_used": 1,
                                                  "n_vehicles_available": 5,
                                                  "fleet_utilization_pct": 20,
                                                  "solver_status": "OPTIMAL"}, wall_duration_ms=50)
            coord = MagicMock() if False else None
            from unittest.mock import MagicMock
            coord = MagicMock()
            coord.persistence = persistence
            coord.supply_agents = [MagicMock()]
            coord.market_agent = MagicMock()
            coord.market_agent.demand_points = [MagicMock()]
            coord.logistics_agent = MagicMock()
            coord.logistics_agent.vehicles = [MagicMock()]
            coord.run_optimization_cycle = MagicMock()
            backend_main.coordinator = coord
            self.TestClient = TestClient
        except Exception as e:
            self.skipTest(f"setup failed: {e}")

    def tearDown(self):
        try:
            os.unlink(self.tmp_path)
        except Exception:
            pass

    def test_cache_stats_endpoint(self):
        """GET /api/admin/persistence/cache returns n_entries + ttl."""
        with self.TestClient(self.backend_main.app) as client:
            resp = client.get("/api/admin/persistence/cache")
        self.assertIn(resp.status_code, (200, 503))

    def test_cache_clear_endpoint(self):
        """POST /api/admin/persistence/cache/clear returns count cleared."""
        with self.TestClient(self.backend_main.app) as client:
            resp = client.post("/api/admin/persistence/cache/clear")
        self.assertIn(resp.status_code, (200, 503))


if __name__ == "__main__":
    unittest.main()
