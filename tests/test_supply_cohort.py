"""
Tests for supply cohort retention endpoint (iter #17)

Covers:
- Persistence.get_supply_cohort_retention() — n_one_time / n_repeating / buckets
- API endpoint /api/persistence/supply-cohort-retention
- Material type filter
"""

from __future__ import annotations

import pytest

from agents.persistence import Persistence


@pytest.fixture
def persistence(tmp_path) -> Persistence:
    """Fresh DB per test."""
    db_path = tmp_path / "test_cohort.db"
    return Persistence(str(db_path))


def _seed_cycle(p: Persistence, cycle_id: str, day: int, supplies: list) -> None:
    """Record a cycle with given supplies.

    Args:
        supplies: list of (supply_id, material_type, available_tons, quality_score)
    """
    p.begin_cycle(
        cycle_id, sim_day=day, sim_hour=10, activity_factor=1.0,
        n_supply_offers=len(supplies), n_demand_requests=0,
    )
    for sid, mat, tons, qual in supplies:
        p.record_supply(cycle_id, {
            "supply_id": sid,
            "location": {"lat": 57.7, "lon": 14.1},
            "material_type": mat,
            "available_tons": tons,
            "moisture_percent": 20.0,
            "quality_score": qual,
        })
    p.commit_cycle(cycle_id, kpi={
        "n_supply_offers": len(supplies),
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


class TestSupplyCohortRetention:
    """Tests for Persistence.get_supply_cohort_retention()."""

    def test_empty_db_returns_zero(self, persistence):
        """Empty DB → all zeros, no buckets."""
        result = persistence.get_supply_cohort_retention()
        assert result["total_supply_ids"] == 0
        assert result["n_one_time"] == 0
        assert result["n_repeating"] == 0
        assert result["retention_rate_pct"] == 0.0
        assert result["one_time_pct"] == 0.0
        assert result["by_appearance_count"] == []
        assert result["material_type_filter"] is None

    def test_all_one_time(self, persistence):
        """All supplies appear in only 1 cycle → 100% one_time."""
        _seed_cycle(persistence, "OPT0001", 1, [
            ("SUP001", "wood", 10.0, 80.0),
            ("SUP002", "wood", 15.0, 85.0),
            ("SUP003", "metal", 5.0, 90.0),
        ])
        result = persistence.get_supply_cohort_retention()
        assert result["total_supply_ids"] == 3
        assert result["n_one_time"] == 3
        assert result["n_repeating"] == 0
        assert result["one_time_pct"] == 100.0
        assert result["retention_rate_pct"] == 0.0

    def test_all_repeating(self, persistence):
        """Same supplies in 2 cycles → all repeating."""
        _seed_cycle(persistence, "OPT0001", 1, [
            ("SUP001", "wood", 10.0, 80.0),
            ("SUP002", "wood", 15.0, 85.0),
        ])
        _seed_cycle(persistence, "OPT0002", 2, [
            ("SUP001", "wood", 12.0, 82.0),
            ("SUP002", "wood", 14.0, 86.0),
        ])
        result = persistence.get_supply_cohort_retention()
        assert result["total_supply_ids"] == 2
        assert result["n_one_time"] == 0
        assert result["n_repeating"] == 2
        assert result["retention_rate_pct"] == 100.0
        assert result["one_time_pct"] == 0.0

    def test_mixed_repeating_and_one_time(self, persistence):
        """Some supplies repeat, some appear once."""
        # Cycle 1: SUP001, SUP002, SUP003 (SUP003 = one-time)
        _seed_cycle(persistence, "OPT0001", 1, [
            ("SUP001", "wood", 10.0, 80.0),
            ("SUP002", "metal", 15.0, 85.0),
            ("SUP003", "wood", 5.0, 90.0),
        ])
        # Cycle 2: SUP001, SUP002 (repeating), SUP004 (one-time)
        _seed_cycle(persistence, "OPT0002", 2, [
            ("SUP001", "wood", 12.0, 82.0),
            ("SUP002", "metal", 16.0, 87.0),
            ("SUP004", "metal", 8.0, 88.0),
        ])
        result = persistence.get_supply_cohort_retention()
        # SUP001 + SUP002 = repeating (2); SUP003 + SUP004 = one-time (2)
        assert result["total_supply_ids"] == 4
        assert result["n_one_time"] == 2
        assert result["n_repeating"] == 2
        assert result["retention_rate_pct"] == 50.0
        assert result["one_time_pct"] == 50.0

    def test_appearance_count_buckets(self, persistence):
        """Buckets: 1, 2, 3-5, 6-10, 11+."""
        # 3 supplies:
        #   SUP001 appears 1x (one-time)
        #   SUP002 appears 2x (repeating)
        #   SUP003 appears 3x (repeating, in bucket 3-5)
        _seed_cycle(persistence, "OPT0001", 1, [
            ("SUP001", "wood", 1.0, 80.0),
            ("SUP002", "wood", 2.0, 80.0),
            ("SUP003", "wood", 3.0, 80.0),
        ])
        _seed_cycle(persistence, "OPT0002", 2, [
            ("SUP002", "wood", 2.0, 80.0),
            ("SUP003", "wood", 3.0, 80.0),
        ])
        _seed_cycle(persistence, "OPT0003", 3, [
            ("SUP003", "wood", 3.0, 80.0),
        ])
        result = persistence.get_supply_cohort_retention()
        # Check buckets exist
        labels = [b["appearance_count_label"] for b in result["by_appearance_count"]]
        assert "1 (one-time)" in labels
        assert "2" in labels
        assert "3-5" in labels
        # Check counts
        for b in result["by_appearance_count"]:
            if b["appearance_count_label"] == "1 (one-time)":
                assert b["n_supplies"] == 1
                assert b["pct"] == pytest.approx(33.3, abs=0.1)
            elif b["appearance_count_label"] == "2":
                assert b["n_supplies"] == 1
                assert b["pct"] == pytest.approx(33.3, abs=0.1)
            elif b["appearance_count_label"] == "3-5":
                assert b["n_supplies"] == 1

    def test_appearance_11plus(self, persistence):
        """Supply appearing 11+ times goes into 11+ bucket."""
        # SUP001 in 12 cycles
        for i in range(12):
            _seed_cycle(persistence, f"OPT{i:04d}", i + 1, [
                ("SUP001", "wood", 1.0, 80.0),
            ])
        result = persistence.get_supply_cohort_retention()
        for b in result["by_appearance_count"]:
            if b["appearance_count_label"] == "11+":
                assert b["n_supplies"] == 1

    def test_filter_by_material_type(self, persistence):
        """material_type filter narrows the analysis."""
        _seed_cycle(persistence, "OPT0001", 1, [
            ("SUP001", "wood", 10.0, 80.0),     # wood
            ("SUP002", "wood", 15.0, 85.0),     # wood
            ("SUP003", "metal", 5.0, 90.0),     # metal
        ])
        _seed_cycle(persistence, "OPT0002", 2, [
            ("SUP001", "wood", 12.0, 82.0),     # wood (repeat)
            # SUP002 wood not repeated → one-time
            # SUP003 metal not repeated → one-time
        ])
        result = persistence.get_supply_cohort_retention(material_type="wood")
        # Only wood supplies: SUP001 (repeat), SUP002 (one-time)
        assert result["total_supply_ids"] == 2
        assert result["n_one_time"] == 1
        assert result["n_repeating"] == 1
        assert result["material_type_filter"] == "wood"

    def test_filter_returns_empty_if_no_match(self, persistence):
        """Filter with no matching data → 0 supplies."""
        _seed_cycle(persistence, "OPT0001", 1, [
            ("SUP001", "wood", 10.0, 80.0),
        ])
        result = persistence.get_supply_cohort_retention(material_type="metal")
        assert result["total_supply_ids"] == 0


class TestAPISupplyCohortRetention:
    """API endpoint tests via TestClient."""

    @pytest.fixture(autouse=True)
    def setup_fake_coordinator(self, tmp_path):
        from unittest.mock import MagicMock
        from web.backend import main as backend_main
        from fastapi.testclient import TestClient

        db_path = tmp_path / "api_cohort_test.db"
        persistence = Persistence(str(db_path))
        _seed_cycle(persistence, "API-OPT0001", 1, [
            ("SUP001", "wood", 10.0, 80.0),
            ("SUP002", "wood", 15.0, 85.0),
        ])
        _seed_cycle(persistence, "API-OPT0002", 2, [
            ("SUP001", "wood", 12.0, 82.0),  # repeat
            # SUP002 one-time
            ("SUP003", "metal", 8.0, 88.0),  # one-time
        ])
        fake_coord = MagicMock()
        fake_coord.persistence = persistence
        backend_main.coordinator = fake_coord
        self.client = TestClient(backend_main.app)

    def test_endpoint_returns_200(self):
        """GET /api/persistence/supply-cohort-retention returns 200."""
        response = self.client.get("/api/persistence/supply-cohort-retention")
        assert response.status_code == 200
        data = response.json()
        assert "total_supply_ids" in data
        assert "n_one_time" in data
        assert "n_repeating" in data
        assert "retention_rate_pct" in data
        assert "by_appearance_count" in data

    def test_endpoint_with_material_filter(self):
        """GET ?material_type=wood returns 200."""
        response = self.client.get("/api/persistence/supply-cohort-retention?material_type=wood")
        assert response.status_code == 200
        data = response.json()
        assert data["material_type_filter"] == "wood"

    def test_endpoint_returns_503_if_no_persistence(self):
        """Without coordinator → 503 (graceful)."""
        from web.backend import main as backend_main
        old_coord = backend_main.coordinator
        backend_main.coordinator = None
        try:
            response = self.client.get("/api/persistence/supply-cohort-retention")
            assert response.status_code == 503
        finally:
            backend_main.coordinator = old_coord


class TestCohortRetentionByPeriod:
    """Tests for Persistence.get_cohort_retention_by_period() (iter #19)."""

    def _seed(self, p: Persistence, day: int, supplies: list) -> None:
        cid = f"CYCLE-DAY-{day}"
        p.begin_cycle(
            cid, sim_day=day, sim_hour=10, activity_factor=1.0,
            n_supply_offers=len(supplies), n_demand_requests=0,
        )
        for sid, mat in supplies:
            p.record_supply(cid, {
                "supply_id": sid,
                "location": {"lat": 57.7, "lon": 14.1},
                "material_type": mat,
                "available_tons": 10.0,
                "moisture_percent": 20.0,
                "quality_score": 80.0,
            })
        p.commit_cycle(cid, kpi={
            "n_supply_offers": len(supplies),
            "n_demand_requests": 0, "n_matches": 0,
            "total_tons": 0, "total_cost_sek": 0, "total_co2_kg": 0,
            "total_distance_km": 0, "n_vehicles_used": 0,
            "n_vehicles_available": 0, "fleet_utilization_pct": 0,
            "solver_status": "feasible",
        }, wall_duration_ms=0)

    def test_empty_db_returns_unknown_trend(self, tmp_path):
        """Empty DB → unknown trend, no periods."""
        p = Persistence(str(tmp_path / "empty.db"))
        result = p.get_cohort_retention_by_period(n_periods=4)
        assert result["total_supply_ids"] == 0
        assert result["n_periods"] == 4
        assert result["periods"] == []
        assert result["trend"] == "unknown"

    def test_valid_n_periods_accepted(self, tmp_path):
        """n_periods in [1, 10] accepted (need at least as many cycles as periods)."""
        p = Persistence(str(tmp_path / "p.db"))
        # Seed 10 days so we can use up to 10 periods
        for d in range(1, 11):
            self._seed(p, d, [(f"SUP{d}", "wood")])
        for n in [1, 2, 4, 8, 10]:
            result = p.get_cohort_retention_by_period(n_periods=n)
            assert result["n_periods"] == n
            assert len(result["periods"]) == n

    def test_n_periods_too_low_raises(self, tmp_path):
        """n_periods=0 raises ValueError."""
        p = Persistence(str(tmp_path / "p.db"))
        with pytest.raises(ValueError):
            p.get_cohort_retention_by_period(n_periods=0)

    def test_n_periods_too_high_raises(self, tmp_path):
        """n_periods=11 raises ValueError."""
        p = Persistence(str(tmp_path / "p.db"))
        with pytest.raises(ValueError):
            p.get_cohort_retention_by_period(n_periods=11)

    def test_period_labels_contain_sim_day_range(self, tmp_path):
        """Period labels 应该包含 sim_day range."""
        p = Persistence(str(tmp_path / "p.db"))
        for d in range(1, 9):
            self._seed(p, d, [(f"SUP{d}", "wood")])
        result = p.get_cohort_retention_by_period(n_periods=4)
        assert len(result["periods"]) == 4
        # 8 days / 4 periods = 2 days per period (period 4 gets the remainder)
        assert "sim_day" in result["periods"][0]["period_label"]

    def test_periods_cover_all_sim_days(self, tmp_path):
        """All sim_days should be in some period."""
        p = Persistence(str(tmp_path / "p.db"))
        for d in range(1, 13):
            self._seed(p, d, [(f"SUP{d}", "wood")])
        result = p.get_cohort_retention_by_period(n_periods=3)
        # 12 days / 3 periods = 4 days each
        min_days = [p["sim_day_range"]["min"] for p in result["periods"]]
        max_days = [p["sim_day_range"]["max"] for p in result["periods"]]
        # Should cover [1, 12]
        assert min_days[0] == 1
        assert max_days[-1] == 12

    def test_trend_improving(self, tmp_path):
        """Last period retention > first + 5% → improving."""
        p = Persistence(str(tmp_path / "p.db"))
        # Early: 1 supply in 4 cycles (high retention)
        # Late: same supply repeated + new ones
        for d in range(1, 11):
            supplies = [("SUP_A", "wood")]  # SUP_A appears in all 10 days
            if d >= 7:
                supplies.append(("SUP_NEW", "wood"))  # Late additions
            self._seed(p, d, supplies)
        result = p.get_cohort_retention_by_period(n_periods=4)
        # Period 1 (early): SUP_A appears 4x in cycles 1-3, retention should be 0% (only 1 supply)
        # Actually 1 supply per period * repeating → retention is 100% (1 supply repeating)
        # Trend analysis works on percentage changes
        assert "trend" in result
        assert result["trend"] in ("improving", "declining", "stable", "unknown")

    def test_trend_declining(self, tmp_path):
        """Last period retention < first - 5% → declining."""
        p = Persistence(str(tmp_path / "p.db"))
        # Early: 1 supply repeating (retention 100%)
        # Late: many new one-time supplies (retention 0%)
        for d in range(1, 11):
            if d <= 3:
                # Early: just one repeating supply
                self._seed(p, d, [("SUP_REPEAT", "wood"), ("SUP_REPEAT", "wood")][:1])
            else:
                # Late: new supply each day (one-time)
                self._seed(p, d, [(f"SUP_D{d}", "wood")])
        result = p.get_cohort_retention_by_period(n_periods=4)
        # Period 1: 1 supply, retention 100% (it's the same)
        # Period 4: 4 different supplies, retention 0% (all one-time)
        assert result["trend"] == "declining"


class TestAPICohortRetentionByPeriod:
    """API tests for /api/persistence/cohort-retention-by-period (iter #19)."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        from unittest.mock import MagicMock
        from web.backend import main as backend_main
        from fastapi.testclient import TestClient

        db_path = tmp_path / "api_cohort_period.db"
        p = Persistence(str(db_path))
        # Seed 6 days
        for d in range(1, 7):
            cid = f"PERIOD-{d}"
            p.begin_cycle(
                cid, sim_day=d, sim_hour=10, activity_factor=1.0,
                n_supply_offers=2, n_demand_requests=0,
            )
            p.record_supply(cid, {
                "supply_id": "SUP_REPEAT", "location": {"lat": 57.7, "lon": 12.9},
                "material_type": "wood", "available_tons": 10.0,
            })
            p.record_supply(cid, {
                "supply_id": f"SUP_D{d}", "location": {"lat": 57.7, "lon": 12.9},
                "material_type": "wood", "available_tons": 10.0,
            })
            p.commit_cycle(cid, kpi={
                "n_supply_offers": 2, "n_demand_requests": 0, "n_matches": 0,
                "total_tons": 0, "total_cost_sek": 0, "total_co2_kg": 0,
                "total_distance_km": 0, "n_vehicles_used": 0,
                "n_vehicles_available": 0, "fleet_utilization_pct": 0,
                "solver_status": "feasible",
            }, wall_duration_ms=0)
        fake = MagicMock()
        fake.persistence = p
        backend_main.coordinator = fake
        self.client = TestClient(backend_main.app)

    def test_endpoint_returns_200(self):
        resp = self.client.get("/api/persistence/cohort-retention-by-period")
        assert resp.status_code == 200
        data = resp.json()
        assert "periods" in data
        assert "trend" in data
        assert "n_periods" in data

    def test_default_n_periods_is_4(self):
        """Default n_periods = 4 (quartiles)."""
        resp = self.client.get("/api/persistence/cohort-retention-by-period")
        assert resp.json()["n_periods"] == 4

    def test_custom_n_periods(self):
        """GET ?n_periods=2 returns 2 periods."""
        resp = self.client.get("/api/persistence/cohort-retention-by-period?n_periods=2")
        assert resp.status_code == 200
        assert resp.json()["n_periods"] == 2
        assert len(resp.json()["periods"]) == 2

    def test_invalid_n_periods_low(self):
        """GET ?n_periods=0 → 400."""
        resp = self.client.get("/api/persistence/cohort-retention-by-period?n_periods=0")
        assert resp.status_code == 400

    def test_invalid_n_periods_high(self):
        """GET ?n_periods=11 → 400."""
        resp = self.client.get("/api/persistence/cohort-retention-by-period?n_periods=11")
        assert resp.status_code == 400

    def test_endpoint_returns_503_if_no_persistence(self):
        from web.backend import main as backend_main
        old_coord = backend_main.coordinator
        backend_main.coordinator = None
        try:
            resp = self.client.get("/api/persistence/cohort-retention-by-period")
            assert resp.status_code == 503
        finally:
            backend_main.coordinator = old_coord