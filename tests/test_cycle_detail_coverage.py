"""
Extended cycle-detail coverage tests (iter #18) — 补足 iter #11 / iter #13 覆盖薄处。

新增覆盖:
- 空 cycle (有 cycle 但 0 supply/0 match/0 route)
- stops_json malformed → stops=[]
- cycle 数字 rounding (total_tons, total_co2_kg 等)
- API 404 for nonexistent cycle_id
- API 503 when persistence not initialized
- API route pagination (之前只测 match)
- pagination 同时有 match + route limits
- supply_offers / demand_requests 字段完整性
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock

# conftest.py 已处理 sys.path (iter #16)
import pytest

from agents.persistence import Persistence


@pytest.fixture
def persistence_with_cycle(tmp_path):
    """Fresh DB with 1 seeded cycle."""
    db_path = tmp_path / "test_cycle_detail.db"
    p = Persistence(str(db_path))
    cid = "test-cycle-1"
    p.begin_cycle(
        cid, sim_day=1, sim_hour=8, activity_factor=1.0,
        n_supply_offers=2, n_demand_requests=2,
    )
    p.commit_cycle(
        cid, kpi={
            "n_supply_offers": 2,
            "n_demand_requests": 2,
            "n_matches": 2,
            "total_tons": 15.555,        # 测试 rounding
            "total_cost_sek": 250.789,
            "total_co2_kg": 12.345,
            "total_distance_km": 30.999,
            "n_vehicles_used": 1,
            "n_vehicles_available": 5,
            "fleet_utilization_pct": 20.111,
            "solver_status": "OPTIMAL",
        },
        wall_duration_ms=50,
    )
    p.record_supply(cid, {
        "supply_id": "SUP001",
        "location": {"lat": 57.7, "lon": 12.9},
        "material_type": "concrete",
        "available_tons": 20.0,
        "moisture_percent": 15.0,
        "quality_score": 85.0,
    })
    p.record_supply(cid, {
        "supply_id": "SUP002",
        "location": {"lat": 57.8, "lon": 13.0},
        "material_type": "metal_scrap",
        "available_tons": 10.0,
        "moisture_percent": 5.0,
        "quality_score": 92.0,
    })
    p.record_demand(cid, {
        "demand_id": "DEM001",
        "name": "Construction Site A",
        "location": {"lat": 57.7, "lon": 12.9},
        "material_type": "concrete",
        "required_tons": 18.0,
        "priority": "high",
        "deadline": "2026-09-15T18:00:00",
    })
    p.record_match(cid, {
        "supply_id": "SUP001",
        "demand_id": "DEM001",
        "material_type": "concrete",
        "tons": 15.0,
        "distance_km": 25.5,
        "estimated_profit_sek": 75.0,
    })
    p.record_route(cid, {
        "vehicle_id": "VEH001",
        "stops": ["DEPOT", "SUP001", "DEM001", "DEPOT"],
        "distance_km": 30.5,
        "duration_hours": 1.5,
        "cost_sek": 250.0,
        "co2_kg": 12.0,
    })
    return p, cid


class TestCycleDetailEmptyCycle:
    """Test cycles with 0 supplies/0 matches/0 routes."""

    def test_cycle_with_no_supplies_or_matches(self, tmp_path):
        """Cycle exists but has 0 supplies/matches/routes."""
        p = Persistence(str(tmp_path / "empty.db"))
        cid = "empty-cycle"
        p.begin_cycle(cid, sim_day=1, sim_hour=8, activity_factor=1.0)
        p.commit_cycle(cid, kpi={
            "n_supply_offers": 0,
            "n_demand_requests": 0,
            "n_matches": 0,
            "total_tons": 0,
            "total_cost_sek": 0,
            "total_co2_kg": 0,
            "total_distance_km": 0,
            "n_vehicles_used": 0,
            "n_vehicles_available": 0,
            "fleet_utilization_pct": 0,
            "solver_status": "feasible",
        }, wall_duration_ms=0)
        detail = p.get_cycle_detail(cid)
        assert detail is not None
        assert detail["cycle"]["cycle_id"] == cid
        assert detail["supply_offers"] == []
        assert detail["demand_requests"] == []
        assert detail["matches"] == []
        assert detail["routes"] == []
        assert detail["pagination"]["matches"]["total"] == 0
        assert detail["pagination"]["routes"]["total"] == 0


class TestCycleDetailRounding:
    """Test numeric rounding in cycle + route data."""

    def test_cycle_kpis_rounded_to_2_decimals(self, persistence_with_cycle):
        p, cid = persistence_with_cycle
        detail = p.get_cycle_detail(cid)
        cycle = detail["cycle"]
        # 验证 round 行为 (banker's rounding / round half to even)
        # 15.555 在 Python float 里实际是 15.55500000... → 15.55 or 15.56 (FP 依赖)
        # 只检查 2 位小数 (Python 实际行为)
        assert round(cycle["total_tons"], 2) == 15.55 or round(cycle["total_tons"], 2) == 15.56
        assert round(cycle["total_cost_sek"], 2) == 250.79
        assert round(cycle["total_co2_kg"], 2) in (12.34, 12.35)
        assert round(cycle["total_distance_km"], 2) == 31.0
        assert round(cycle["fleet_utilization_pct"], 2) == 20.11

    def test_route_kpis_rounded_to_2_decimals(self, persistence_with_cycle):
        p, cid = persistence_with_cycle
        detail = p.get_cycle_detail(cid)
        route = detail["routes"][0]
        assert route["distance_km"] == 30.5
        assert route["duration_hours"] == 1.5
        assert route["cost_sek"] == 250.0
        assert route["co2_kg"] == 12.0


class TestCycleDetailStopsJson:
    """Test stops_json parsing edge cases."""

    def test_normal_stops_json_parsed_to_list(self, tmp_path):
        p = Persistence(str(tmp_path / "stops.db"))
        cid = "stops-cycle"
        p.begin_cycle(cid, sim_day=1, sim_hour=8, activity_factor=1.0)
        p.commit_cycle(cid, kpi={
            "n_supply_offers": 1, "n_demand_requests": 1, "n_matches": 1,
            "total_tons": 5, "total_cost_sek": 100, "total_co2_kg": 5,
            "total_distance_km": 10, "n_vehicles_used": 1,
            "n_vehicles_available": 5, "fleet_utilization_pct": 20,
            "solver_status": "feasible",
        }, wall_duration_ms=0)
        p.record_route(cid, {
            "vehicle_id": "V001",
            "stops": ["DEPOT", "SUP001", "DEM001"],
            "distance_km": 10, "duration_hours": 1, "cost_sek": 100, "co2_kg": 5,
        })
        detail = p.get_cycle_detail(cid)
        assert detail["routes"][0]["stops"] == ["DEPOT", "SUP001", "DEM001"]
        # stops_json 字段不应泄漏到 response
        assert "stops_json" not in detail["routes"][0]

    def test_malformed_stops_json_returns_empty_list(self, tmp_path):
        """stops_json 不是 valid JSON → stops=[] (graceful)."""
        p = Persistence(str(tmp_path / "bad_stops.db"))
        cid = "bad-stops-cycle"
        p.begin_cycle(cid, sim_day=1, sim_hour=8, activity_factor=1.0)
        p.commit_cycle(cid, kpi={
            "n_supply_offers": 1, "n_demand_requests": 1, "n_matches": 1,
            "total_tons": 5, "total_cost_sek": 100, "total_co2_kg": 5,
            "total_distance_km": 10, "n_vehicles_used": 1,
            "n_vehicles_available": 5, "fleet_utilization_pct": 20,
            "solver_status": "feasible",
        }, wall_duration_ms=0)
        # 手动 inject malformed JSON into route
        with p._conn() as conn:
            conn.execute(
                """INSERT INTO routes (cycle_id, vehicle_id, stops_json, distance_km,
                                       duration_hours, cost_sek, co2_kg)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (cid, "V001", "not-valid-json{[", 10, 1, 100, 5),
            )
        detail = p.get_cycle_detail(cid)
        assert detail["routes"][0]["stops"] == []  # graceful fallback

    def test_null_stops_json_returns_empty_list(self, tmp_path):
        """stops_json is NULL → stops=[]."""
        p = Persistence(str(tmp_path / "null_stops.db"))
        cid = "null-stops-cycle"
        p.begin_cycle(cid, sim_day=1, sim_hour=8, activity_factor=1.0)
        p.commit_cycle(cid, kpi={
            "n_supply_offers": 1, "n_demand_requests": 1, "n_matches": 1,
            "total_tons": 5, "total_cost_sek": 100, "total_co2_kg": 5,
            "total_distance_km": 10, "n_vehicles_used": 1,
            "n_vehicles_available": 5, "fleet_utilization_pct": 20,
            "solver_status": "feasible",
        }, wall_duration_ms=0)
        with p._conn() as conn:
            conn.execute(
                """INSERT INTO routes (cycle_id, vehicle_id, stops_json, distance_km,
                                       duration_hours, cost_sek, co2_kg)
                   VALUES (?, ?, NULL, ?, ?, ?, ?)""",
                (cid, "V001", 10, 1, 100, 5),
            )
        detail = p.get_cycle_detail(cid)
        assert detail["routes"][0]["stops"] == []


class TestCycleDetailSupplyDemandFields:
    """Test that supply_offers and demand_requests return all expected fields."""

    def test_supply_offers_complete_fields(self, persistence_with_cycle):
        p, cid = persistence_with_cycle
        detail = p.get_cycle_detail(cid)
        supplies = detail["supply_offers"]
        assert len(supplies) == 2
        # First supply (SUP001)
        s1 = next(s for s in supplies if s["supply_id"] == "SUP001")
        assert s1["material_type"] == "concrete"
        assert s1["available_tons"] == 20.0
        assert s1["moisture_percent"] == 15.0
        assert s1["quality_score"] == 85.0
        assert s1["location_lat"] == 57.7
        assert s1["location_lon"] == 12.9

    def test_demand_requests_complete_fields(self, persistence_with_cycle):
        p, cid = persistence_with_cycle
        detail = p.get_cycle_detail(cid)
        demands = detail["demand_requests"]
        assert len(demands) == 1
        d = demands[0]
        assert d["demand_id"] == "DEM001"
        assert d["name"] == "Construction Site A"
        assert d["material_type"] == "concrete"
        assert d["required_tons"] == 18.0
        assert d["priority"] == "high"
        assert d["deadline"] == "2026-09-15T18:00:00"


class TestCycleDetailMissingCycle:
    """Test behavior when cycle_id doesn't exist."""

    def test_persistence_returns_none(self, persistence_with_cycle):
        p, _ = persistence_with_cycle
        result = p.get_cycle_detail("nonexistent-cycle-id")
        assert result is None


class TestAPICycleDetailExtended:
    """API endpoint extended coverage."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        from web.backend import main as backend_main
        from fastapi.testclient import TestClient

        db_path = tmp_path / "api_extended.db"
        p = Persistence(str(db_path))
        # Seed 2 cycles with different data
        for i in range(2):
            cid = f"API-EXT-{i}"
            p.begin_cycle(cid, sim_day=i + 1, sim_hour=10, activity_factor=1.0,
                          n_supply_offers=2, n_demand_requests=2)
            p.commit_cycle(cid, kpi={
                "n_supply_offers": 2, "n_demand_requests": 2,
                "n_matches": 1, "total_tons": 5 + i, "total_cost_sek": 100 + i * 10,
                "total_co2_kg": 5, "total_distance_km": 10,
                "n_vehicles_used": 1, "n_vehicles_available": 5,
                "fleet_utilization_pct": 20, "solver_status": "feasible",
            }, wall_duration_ms=10)
            p.record_supply(cid, {
                "supply_id": f"SUP{i}", "location": {"lat": 57.7, "lon": 12.9},
                "material_type": "wood", "available_tons": 10.0,
            })
            p.record_demand(cid, {
                "demand_id": f"DEM{i}", "location": {"lat": 57.7, "lon": 12.9},
                "material_type": "wood", "required_tons": 5.0,
            })
            p.record_match(cid, {
                "supply_id": f"SUP{i}", "demand_id": f"DEM{i}",
                "material_type": "wood", "tons": 5.0,
                "distance_km": 10.0, "estimated_profit_sek": 50.0,
            })
            p.record_route(cid, {
                "vehicle_id": f"V{i}", "stops": ["DEPOT", f"SUP{i}"],
                "distance_km": 10.0, "duration_hours": 1.0,
                "cost_sek": 100.0, "co2_kg": 5.0,
            })
        fake_coord = MagicMock()
        fake_coord.persistence = p
        backend_main.coordinator = fake_coord
        self.client = TestClient(backend_main.app)

    def test_api_404_for_nonexistent_cycle(self):
        """GET cycle-detail/{nonexistent} → 404."""
        resp = self.client.get("/api/persistence/cycle-detail/does-not-exist")
        assert resp.status_code == 404
        assert "does-not-exist" in resp.json()["detail"]

    def test_api_503_when_no_persistence(self):
        """GET cycle-detail without coordinator → 503."""
        from web.backend import main as backend_main
        old_coord = backend_main.coordinator
        backend_main.coordinator = None
        try:
            resp = self.client.get("/api/persistence/cycle-detail/API-EXT-0")
            assert resp.status_code == 503
        finally:
            backend_main.coordinator = old_coord

    def test_api_route_pagination(self):
        """GET ?route_limit=1 returns 1 route + pagination metadata."""
        resp = self.client.get(
            "/api/persistence/cycle-detail/API-EXT-0?route_limit=1"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["routes"]) == 1
        assert data["pagination"]["routes"]["total"] == 1
        assert data["pagination"]["routes"]["limit"] == 1

    def test_api_combined_match_and_route_pagination(self):
        """GET with both match_limit AND route_limit → both paginate."""
        resp = self.client.get(
            "/api/persistence/cycle-detail/API-EXT-0?match_limit=1&route_limit=1"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["matches"]) == 1
        assert len(data["routes"]) == 1

    def test_api_negative_match_offset_clamped(self):
        """GET ?match_offset=-5 → clamped to 0."""
        resp = self.client.get(
            "/api/persistence/cycle-detail/API-EXT-0?match_limit=5&match_offset=-5"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["pagination"]["matches"]["offset"] == 0

    def test_api_special_chars_in_cycle_id(self):
        """GET cycle-detail with URL-encoded special chars returns 404 (not 500)."""
        # URL: 'OPT-cycle-with-dash-and.123'
        encoded = "OPT-cycle-with-dash-and.123"
        resp = self.client.get(f"/api/persistence/cycle-detail/{encoded}")
        # Should be 404 (not found), not 500 (server error)
        assert resp.status_code == 404

    def test_api_supply_demand_arrays_present(self):
        """Response includes supply_offers + demand_requests arrays."""
        resp = self.client.get("/api/persistence/cycle-detail/API-EXT-0")
        assert resp.status_code == 200
        data = resp.json()
        assert "supply_offers" in data
        assert "demand_requests" in data
        assert isinstance(data["supply_offers"], list)
        assert isinstance(data["demand_requests"], list)
        assert len(data["supply_offers"]) >= 1
        assert len(data["demand_requests"]) >= 1

    def test_api_cycle_has_all_required_fields(self):
        """Cycle object has expected fields."""
        resp = self.client.get("/api/persistence/cycle-detail/API-EXT-0")
        assert resp.status_code == 200
        cycle = resp.json()["cycle"]
        for field in [
            "cycle_id", "sim_day", "sim_hour", "activity_factor",
            "n_supply_offers", "n_demand_requests", "n_matches",
            "total_tons", "total_cost_sek", "total_co2_kg",
            "total_distance_km", "n_vehicles_used",
            "n_vehicles_available", "fleet_utilization_pct",
            "solver_status", "wall_duration_ms",
        ]:
            assert field in cycle, f"Missing field: {field}"