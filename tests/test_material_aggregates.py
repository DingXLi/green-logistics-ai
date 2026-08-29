"""
Tests for material_type aggregates endpoint + VACUUM + cycle-kpi-summary (iter #16)

Covers:
- get_material_aggregates() — per-material-type aggregate KPIs
- get_cycle_kpi_summary() — overall KPI rollup + best/worst/last cycle
- vacuum() — VACUUM + ANALYZE wrapper
- API endpoints /api/persistence/material-aggregates, /api/persistence/cycle-kpi-summary,
  /api/admin/db-maintenance
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path

import pytest

from agents.persistence import Persistence


@pytest.fixture
def persistence(tmp_path) -> Persistence:
    """Fresh in-memory-ish DB for each test."""
    db_path = tmp_path / "test_material.db"
    return Persistence(str(db_path))


def _record_basic_cycle(p: Persistence, cycle_id: str, day: int = 1,
                        supply_tons: dict = None, matches: list = None) -> None:
    """Helper: record a minimal cycle with supplies + matches.

    Args:
        supply_tons: {supply_id: (material_type, available_tons, quality_score)}
        matches: [(supply_id, demand_id, material_type, tons, distance_km)]
    """
    supply_tons = supply_tons or {}
    matches = matches or []
    p.begin_cycle(
        cycle_id,
        sim_day=day,
        sim_hour=10,
        activity_factor=1.0,
        n_supply_offers=len(supply_tons),
        n_demand_requests=2,
    )
    for sid, (mat, tons, qual) in supply_tons.items():
        p.record_supply(cycle_id, {
            "supply_id": sid,
            "location": {"lat": 57.7, "lon": 14.1},
            "material_type": mat,
            "available_tons": tons,
            "moisture_percent": 20.0,
            "quality_score": qual,
        })
    for supply_id, demand_id, mat, tons, dist in matches:
        p.record_match(cycle_id, {
            "supply_id": supply_id,
            "demand_id": demand_id,
            "material_type": mat,
            "tons": tons,
            "distance_km": dist,
            "estimated_profit_sek": 100.0,
        })
    p.commit_cycle(cycle_id, kpi={
        "n_supply_offers": len(supply_tons),
        "n_demand_requests": 2,
        "n_matches": len(matches),
        "total_tons": sum(m[3] for m in matches),
        "total_cost_sek": 50.0 * len(matches),
        "total_co2_kg": 5.0 * len(matches),
        "total_distance_km": sum(m[4] for m in matches),
        "n_vehicles_used": len(matches),
        "n_vehicles_available": 10,
        "fleet_utilization_pct": 30.0,
        "solver_status": "feasible",
    }, wall_duration_ms=100)


class TestMaterialAggregates:
    """Tests for Persistence.get_material_aggregates()."""

    def test_empty_db_returns_empty_list(self, persistence):
        """No cycles → empty list (no materials)."""
        result = persistence.get_material_aggregates()
        assert result == []

    def test_single_cycle_single_material(self, persistence):
        """1 cycle, 1 material → 1 material aggregate."""
        _record_basic_cycle(
            persistence, "OPT0001",
            supply_tons={"SUP001": ("wood", 10.0, 85.0)},
            matches=[("SUP001", "DEM001", "wood", 8.0, 25.0)],
        )
        result = persistence.get_material_aggregates()
        assert len(result) == 1
        agg = result[0]
        assert agg["material_type"] == "wood"
        assert agg["n_supply_offers"] == 1
        assert agg["n_cycles_with_material"] == 1
        assert agg["n_distinct_supplies"] == 1
        assert agg["total_available_tons"] == 10.0
        assert agg["total_matched_tons"] == 8.0
        assert agg["avg_quality_score"] == 85.0
        assert agg["n_matches"] == 1
        assert agg["avg_match_distance_km"] == 25.0
        assert agg["max_match_distance_km"] == 25.0
        assert agg["match_rate_pct"] == 80.0  # 8/10 = 80%

    def test_multiple_materials_sorted_by_total_available(self, persistence):
        """Multiple materials → sorted by total_available DESC."""
        _record_basic_cycle(
            persistence, "OPT0001",
            supply_tons={
                "SUP001": ("wood", 10.0, 80.0),
                "SUP002": ("metal", 50.0, 95.0),
                "SUP003": ("concrete", 5.0, 70.0),
            },
            matches=[
                ("SUP001", "DEM001", "wood", 8.0, 20.0),
                ("SUP002", "DEM002", "metal", 40.0, 50.0),
            ],
        )
        result = persistence.get_material_aggregates()
        assert len(result) == 3
        # metal (50) > wood (10) > concrete (5)
        assert result[0]["material_type"] == "metal"
        assert result[0]["total_available_tons"] == 50.0
        assert result[1]["material_type"] == "wood"
        assert result[2]["material_type"] == "concrete"
        # materials without matches should have match_rate 0
        assert result[2]["match_rate_pct"] == 0.0
        assert result[2]["n_matches"] == 0

    def test_filter_by_material_type(self, persistence):
        """material_type filter narrows to 1 result."""
        _record_basic_cycle(
            persistence, "OPT0001",
            supply_tons={
                "SUP001": ("wood", 10.0, 80.0),
                "SUP002": ("metal", 50.0, 95.0),
            },
            matches=[("SUP001", "DEM001", "wood", 8.0, 20.0)],
        )
        result = persistence.get_material_aggregates(material_type="wood")
        assert len(result) == 1
        assert result[0]["material_type"] == "wood"

    def test_filter_returns_empty_if_no_match(self, persistence):
        """material_type filter with no matching data → []."""
        _record_basic_cycle(
            persistence, "OPT0001",
            supply_tons={"SUP001": ("wood", 10.0, 80.0)},
        )
        result = persistence.get_material_aggregates(material_type="metal")
        assert result == []

    def test_multiple_cycles_same_material_aggregate(self, persistence):
        """2 cycles with wood → 1 aggregate row summing both."""
        _record_basic_cycle(
            persistence, "OPT0001",
            day=1,
            supply_tons={"SUP001": ("wood", 10.0, 80.0)},
            matches=[("SUP001", "DEM001", "wood", 8.0, 20.0)],
        )
        _record_basic_cycle(
            persistence, "OPT0002",
            day=2,
            supply_tons={"SUP002": ("wood", 15.0, 90.0)},
            matches=[("SUP002", "DEM002", "wood", 12.0, 30.0)],
        )
        result = persistence.get_material_aggregates()
        assert len(result) == 1
        agg = result[0]
        assert agg["material_type"] == "wood"
        assert agg["n_supply_offers"] == 2
        assert agg["n_cycles_with_material"] == 2
        assert agg["n_distinct_supplies"] == 2  # SUP001 + SUP002
        assert agg["total_available_tons"] == 25.0  # 10+15
        assert agg["total_matched_tons"] == 20.0  # 8+12
        assert agg["avg_match_distance_km"] == 25.0  # (20+30)/2
        assert agg["max_match_distance_km"] == 30.0

    def test_match_rate_pct_correct(self, persistence):
        """match_rate = total_matched / total_available * 100."""
        _record_basic_cycle(
            persistence, "OPT0001",
            supply_tons={"SUP001": ("wood", 100.0, 80.0)},
            matches=[("SUP001", "DEM001", "wood", 75.0, 20.0)],
        )
        result = persistence.get_material_aggregates()
        assert result[0]["match_rate_pct"] == 75.0  # 75/100 = 75%

    def test_limit_param_caps_results(self, persistence):
        """limit param caps number of returned materials."""
        # 5 different materials
        supply_tons = {f"SUP{i:03d}": (f"mat{i}", 10.0, 80.0) for i in range(5)}
        _record_basic_cycle(persistence, "OPT0001", supply_tons=supply_tons)
        result = persistence.get_material_aggregates(limit=3)
        assert len(result) == 3


class TestCycleKpiSummary:
    """Tests for Persistence.get_cycle_kpi_summary()."""

    def test_empty_db_returns_zeros(self, persistence):
        """No cycles → all zero, no best/worst/last."""
        result = persistence.get_cycle_kpi_summary()
        assert result["total_cycles"] == 0
        assert result["n_cycles_with_matches"] == 0
        assert result["total_tons_matched"] == 0
        assert result["total_cost_sek"] == 0
        assert result["avg_tons_per_cycle"] == 0
        assert result["avg_cost_per_ton_sek"] is None
        assert result["avg_co2_per_ton_kg"] is None
        assert result["best_cycle"] is None
        assert result["worst_cycle"] is None
        assert result["last_cycle"] is None

    def test_single_cycle_summary(self, persistence):
        """1 cycle → totals match inputs, best=worst=last=that cycle."""
        _record_basic_cycle(
            persistence, "OPT0001", day=1,
            supply_tons={"SUP001": ("wood", 10.0, 80.0)},
            matches=[("SUP001", "DEM001", "wood", 8.0, 25.0)],
        )
        result = persistence.get_cycle_kpi_summary()
        assert result["total_cycles"] == 1
        assert result["n_cycles_with_matches"] == 1
        assert result["total_tons_matched"] == 8.0
        assert result["total_distance_km"] == 25.0
        assert result["total_cost_sek"] == 50.0
        assert result["avg_tons_per_cycle"] == 8.0
        assert result["avg_cost_per_ton_sek"] == 6.25  # 50/8
        assert result["avg_co2_per_ton_kg"] == 0.62  # 5/8 rounded
        assert result["fleet_utilization_avg_pct"] == 30.0
        # All three point to OPT0001
        assert result["best_cycle"]["cycle_id"] == "OPT0001"
        assert result["worst_cycle"]["cycle_id"] == "OPT0001"
        assert result["last_cycle"]["cycle_id"] == "OPT0001"

    def test_multiple_cycles_best_worst_last(self, persistence):
        """3 cycles with different totals → best/worst/last distinguished."""
        _record_basic_cycle(
            persistence, "OPT0001", day=1,
            matches=[("SUP001", "DEM001", "wood", 5.0, 10.0)],
        )
        _record_basic_cycle(
            persistence, "OPT0002", day=2,
            matches=[
                ("SUP002", "DEM001", "wood", 20.0, 30.0),
                ("SUP003", "DEM002", "wood", 15.0, 25.0),
            ],
        )
        _record_basic_cycle(
            persistence, "OPT0003", day=3,
            matches=[("SUP004", "DEM003", "wood", 8.0, 20.0)],
        )
        result = persistence.get_cycle_kpi_summary()
        assert result["total_cycles"] == 3
        # Best = OPT0002 (35 tons), Worst = OPT0001 (5 tons), Last = OPT0003
        assert result["best_cycle"]["cycle_id"] == "OPT0002"
        assert result["best_cycle"]["total_tons"] == 35.0
        assert result["worst_cycle"]["cycle_id"] == "OPT0001"
        assert result["worst_cycle"]["total_tons"] == 5.0
        assert result["last_cycle"]["cycle_id"] == "OPT0003"
        # Day range
        assert result["sim_day_range"]["min"] == 1
        assert result["sim_day_range"]["max"] == 3

    def test_filter_echoed_in_response(self, persistence):
        """Filter params echoed back in result['filter']."""
        result = persistence.get_cycle_kpi_summary(
            last_n=7, since_sim_day=20, until_sim_day=30
        )
        assert result["filter"]["last_n"] == 7
        assert result["filter"]["since_sim_day"] == 20
        assert result["filter"]["until_sim_day"] == 30

    def test_last_n_filter_limits_to_recent_n(self, persistence):
        """last_n=2 returns stats from only the 2 most recent cycles."""
        # 5 cycles, increasing tons
        for i in range(1, 6):
            _record_basic_cycle(
                persistence, f"OPT{i:04d}", day=i,
                matches=[("SUP001", "DEM001", "wood", float(i * 10), 10.0)],
            )
        result = persistence.get_cycle_kpi_summary(last_n=2)
        assert result["total_cycles"] == 2
        # Day range should be 4-5 (the last 2)
        assert result["sim_day_range"]["min"] == 4
        assert result["sim_day_range"]["max"] == 5
        # Last cycle = OPT0005 (day 5)
        assert result["last_cycle"]["cycle_id"] == "OPT0005"
        # Best cycle within last 2 = OPT0005 (50 tons) > OPT0004 (40 tons)
        assert result["best_cycle"]["cycle_id"] == "OPT0005"

    def test_since_until_filter_by_sim_day(self, persistence):
        """since_sim_day + until_sim_day limits to day range."""
        for i in range(1, 6):
            _record_basic_cycle(
                persistence, f"OPT{i:04d}", day=i,
                matches=[("SUP001", "DEM001", "wood", float(i * 10), 10.0)],
            )
        # Day 2-4 only (3 cycles)
        result = persistence.get_cycle_kpi_summary(since_sim_day=2, until_sim_day=4)
        assert result["total_cycles"] == 3
        assert result["sim_day_range"]["min"] == 2
        assert result["sim_day_range"]["max"] == 4

    def test_since_only_inclusive(self, persistence):
        """since_sim_day=N includes day N (>=)."""
        for i in range(1, 4):
            _record_basic_cycle(
                persistence, f"OPT{i:04d}", day=i,
                matches=[("SUP001", "DEM001", "wood", float(i), 1.0)],
            )
        result = persistence.get_cycle_kpi_summary(since_sim_day=2)
        assert result["total_cycles"] == 2  # day 2 + day 3

    def test_until_only_inclusive(self, persistence):
        """until_sim_day=N includes day N (<=)."""
        for i in range(1, 4):
            _record_basic_cycle(
                persistence, f"OPT{i:04d}", day=i,
                matches=[("SUP001", "DEM001", "wood", float(i), 1.0)],
            )
        result = persistence.get_cycle_kpi_summary(until_sim_day=2)
        assert result["total_cycles"] == 2  # day 1 + day 2

    def test_filter_no_match_returns_zero(self, persistence):
        """Filter excludes all cycles → 0 cycles, None best/worst/last."""
        for i in range(1, 4):
            _record_basic_cycle(
                persistence, f"OPT{i:04d}", day=i,
                matches=[("SUP001", "DEM001", "wood", float(i), 1.0)],
            )
        result = persistence.get_cycle_kpi_summary(since_sim_day=100)
        assert result["total_cycles"] == 0
        assert result["best_cycle"] is None
        assert result["last_cycle"] is None

    def test_combined_last_n_and_day_range(self, persistence):
        """last_n + since/until: both filters apply (AND)."""
        for i in range(1, 11):
            _record_basic_cycle(
                persistence, f"OPT{i:04d}", day=i,
                matches=[("SUP001", "DEM001", "wood", float(i * 10), 10.0)],
            )
        # last_n=3, but also since_sim_day=8 → intersection = day 8,9,10 = 3 cycles
        result = persistence.get_cycle_kpi_summary(last_n=3, since_sim_day=8)
        assert result["total_cycles"] == 3
        assert result["sim_day_range"]["min"] == 8
        assert result["sim_day_range"]["max"] == 10


class TestVacuum:
    """Tests for Persistence.vacuum()."""

    def test_vacuum_returns_success(self, persistence):
        """VACUUM + ANALYZE on a fresh DB returns success."""
        _record_basic_cycle(
            persistence, "OPT0001",
            supply_tons={"SUP001": ("wood", 10.0, 80.0)},
        )
        result = persistence.vacuum()
        assert result["action"] == "vacuum_analyze"
        assert result["success"] is True
        assert "size_before_bytes" in result
        assert "size_after_bytes" in result
        assert result["size_after_bytes"] >= 0

    def test_vacuum_on_empty_db(self, persistence):
        """VACUUM on empty DB succeeds."""
        result = persistence.vacuum()
        assert result["success"] is True
        assert result["size_before_bytes"] >= 0
        assert result["size_after_bytes"] >= 0

    def test_vacuum_preserves_data(self, persistence):
        """After VACUUM, data still readable."""
        _record_basic_cycle(
            persistence, "OPT0001",
            supply_tons={"SUP001": ("wood", 10.0, 80.0)},
            matches=[("SUP001", "DEM001", "wood", 8.0, 25.0)],
        )
        persistence.vacuum()
        # Verify data still there
        result = persistence.get_cycle_kpi_summary()
        assert result["total_cycles"] == 1
        assert result["total_tons_matched"] == 8.0


class TestAPIMaterialAggregates:
    """API endpoint tests via TestClient."""

    @pytest.fixture(autouse=True)
    def setup_fake_coordinator(self, tmp_path):
        """Inject a fake coordinator with seeded persistence into web.backend.main."""
        from unittest.mock import MagicMock
        from web.backend import main as backend_main
        from fastapi.testclient import TestClient

        db_path = tmp_path / "api_test.db"
        persistence = Persistence(str(db_path))
        _record_basic_cycle(
            persistence, "API-OPT0001", day=1,
            supply_tons={
                "SUP001": ("wood", 10.0, 80.0),
                "SUP002": ("metal", 30.0, 90.0),
            },
            matches=[
                ("SUP001", "DEM001", "wood", 8.0, 20.0),
                ("SUP002", "DEM002", "metal", 25.0, 40.0),
            ],
        )
        fake_coord = MagicMock()
        fake_coord.persistence = persistence
        backend_main.coordinator = fake_coord
        self.client = TestClient(backend_main.app)

    def test_material_aggregates_endpoint(self):
        """GET /api/persistence/material-aggregates returns 200 with list."""
        response = self.client.get("/api/persistence/material-aggregates")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 2
        # Sorted by total_available DESC: metal (30) > wood (10)
        assert data[0]["material_type"] == "metal"
        assert data[1]["material_type"] == "wood"

    def test_material_aggregates_with_filter(self):
        """GET with material_type query filter returns 200 with 1 material."""
        response = self.client.get("/api/persistence/material-aggregates?material_type=wood")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["material_type"] == "wood"

    def test_material_aggregates_limit_query(self):
        """GET with limit=1 returns only 1 material (the top one)."""
        response = self.client.get("/api/persistence/material-aggregates?limit=1")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["material_type"] == "metal"  # top by total_available

    def test_cycle_kpi_summary_endpoint(self):
        """GET /api/persistence/cycle-kpi-summary returns 200 with KPIs."""
        response = self.client.get("/api/persistence/cycle-kpi-summary")
        assert response.status_code == 200
        data = response.json()
        assert "total_cycles" in data
        assert data["total_cycles"] == 1
        assert "total_tons_matched" in data
        assert "avg_cost_per_ton_sek" in data
        assert "best_cycle" in data
        assert data["best_cycle"]["cycle_id"] == "API-OPT0001"

    def test_db_maintenance_endpoint(self):
        """POST /api/admin/db-maintenance runs VACUUM + ANALYZE."""
        response = self.client.post("/api/admin/db-maintenance")
        assert response.status_code == 200
        data = response.json()
        assert data["action"] == "vacuum_analyze"
        assert data["success"] is True
        assert "size_before_bytes" in data
        assert "size_after_bytes" in data

    def test_db_maintenance_returns_503_if_no_persistence(self):
        """POST without coordinator → 503 (handled gracefully)."""
        from web.backend import main as backend_main
        old_coord = backend_main.coordinator
        backend_main.coordinator = None
        try:
            response = self.client.post("/api/admin/db-maintenance")
            assert response.status_code == 503
        finally:
            backend_main.coordinator = old_coord

    def test_cycle_kpi_summary_with_last_n_filter(self):
        """GET ?last_n=7 returns 200 with filter echoed."""
        response = self.client.get("/api/persistence/cycle-kpi-summary?last_n=7")
        assert response.status_code == 200
        data = response.json()
        assert data["filter"]["last_n"] == 7

    def test_cycle_kpi_summary_with_since_until(self):
        """GET ?since_sim_day=1&until_sim_day=10 returns 200 with filter echoed."""
        response = self.client.get("/api/persistence/cycle-kpi-summary?since_sim_day=1&until_sim_day=10")
        assert response.status_code == 200
        data = response.json()
        assert data["filter"]["since_sim_day"] == 1
        assert data["filter"]["until_sim_day"] == 10

    def test_cycle_kpi_summary_invalid_last_n(self):
        """GET ?last_n=0 returns 400 (last_n must be >= 1)."""
        response = self.client.get("/api/persistence/cycle-kpi-summary?last_n=0")
        assert response.status_code == 400
        assert "last_n" in response.json()["detail"]

    def test_cycle_kpi_summary_invalid_day_range(self):
        """GET ?since_sim_day=20&until_sim_day=10 returns 400 (since > until)."""
        response = self.client.get(
            "/api/persistence/cycle-kpi-summary?since_sim_day=20&until_sim_day=10"
        )
        assert response.status_code == 400
        assert "since_sim_day" in response.json()["detail"]

    def test_cycle_kpi_summary_last_n_too_large(self):
        """GET ?last_n=99999 returns 400 (max 10000)."""
        response = self.client.get("/api/persistence/cycle-kpi-summary?last_n=99999")
        assert response.status_code == 400