"""
iter #46: tests for /api/persistence/seasonal-timeseries-by-material endpoint.

Covers:
1. Persistence.get_seasonal_timeseries_by_material() — basic cross-tab
2. Returns sorted materials and 12 month labels
3. Material-specific seasonal_multiplier aggregation
4. Endpoint exposes the new method via /api/persistence/seasonal-timeseries-by-material
5. Empty state when no cycles exist
"""
import os
import sys
import tempfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def persistence():
    from agents.persistence import Persistence
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_seasonal_mat.db")
        p = Persistence(db_path)
        yield p


def _insert_cycle(p, cycle_id, sim_day, seasonal_month, seasonal_factor=1.0):
    """Insert a cycle row with a given seasonal_month."""
    with p._conn() as conn:
        conn.execute(
            """INSERT INTO optimization_cycles
               (cycle_id, sim_day, sim_hour, wall_timestamp,
                activity_factor, n_supply_offers, n_demand_requests, n_matches,
                total_tons, total_cost_sek, total_co2_kg, total_distance_km,
                n_vehicles_used, n_vehicles_available, fleet_utilization_pct,
                solver_status, wall_duration_ms, seasonal_factor_avg, seasonal_month)
               VALUES (?, ?, 0, '2026-09-03T00:00:00',
                       1.0, 1, 1, 1, 5.0, 100.0, 50.0, 20.0,
                       1, 1, 50.0, 'OPTIMAL', 100, ?, ?)""",
            (cycle_id, sim_day, seasonal_factor, seasonal_month),
        )


def _insert_supply(p, cycle_id, supply_id, material_type, tons=10.0,
                   seasonal_mult=1.0, base_mult=1.0):
    with p._conn() as conn:
        conn.execute(
            """INSERT INTO supply_offers
               (cycle_id, supply_id, material_type, available_tons,
                moisture_percent, quality_score,
                base_seasonal_multiplier, seasonal_multiplier, perturbation_applied)
               VALUES (?, ?, ?, ?, 5.0, 0.8, ?, ?, 0)""",
            (cycle_id, supply_id, material_type, tons, base_mult, seasonal_mult),
        )


# ============================================
# Persistence method tests
# ============================================


def test_get_seasonal_timeseries_by_material_empty(persistence):
    """No cycles → empty matrix + zero counts."""
    result = persistence.get_seasonal_timeseries_by_material()
    assert result["n_materials"] == 0
    assert result["n_months"] == 0
    assert result["materials"] == []
    assert result["month_labels"] == ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                                       "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    assert result["matrix"] == []


def test_single_material_single_month(persistence):
    """One cycle in July, all concrete → one row in matrix."""
    _insert_cycle(persistence, "c1", sim_day=200, seasonal_month=7)
    _insert_supply(persistence, "c1", "s1", "concrete", tons=15.0,
                   seasonal_mult=1.3, base_mult=1.2)
    result = persistence.get_seasonal_timeseries_by_material()
    assert result["n_materials"] == 1
    assert result["n_months"] == 1
    assert result["materials"] == ["concrete"]
    assert len(result["matrix"]) == 1
    row = result["matrix"][0]
    assert row["material_type"] == "concrete"
    assert row["seasonal_month"] == 7
    assert row["month_name"] == "Jul"
    assert row["total_tons"] == 15.0
    assert row["avg_seasonal_multiplier"] == 1.3
    assert row["avg_base_multiplier"] == 1.2
    assert row["n_supply_offers"] == 1


def test_multiple_materials_multiple_months(persistence):
    """Multiple materials across different months."""
    # January cycle with concrete + metal_scrap
    _insert_cycle(persistence, "c1", sim_day=10, seasonal_month=1)
    _insert_supply(persistence, "c1", "s1", "concrete", tons=20.0, seasonal_mult=0.8)
    _insert_supply(persistence, "c1", "s2", "metal_scrap", tons=5.0, seasonal_mult=0.95)
    # July cycle with concrete + wood_waste
    _insert_cycle(persistence, "c2", sim_day=200, seasonal_month=7)
    _insert_supply(persistence, "c2", "s3", "concrete", tons=30.0, seasonal_mult=1.4)
    _insert_supply(persistence, "c2", "s4", "wood_waste", tons=10.0, seasonal_mult=1.3)

    result = persistence.get_seasonal_timeseries_by_material()
    assert result["n_materials"] == 3
    assert result["n_months"] == 2
    assert result["materials"] == ["concrete", "metal_scrap", "wood_waste"]
    assert len(result["matrix"]) == 4  # (c, 1), (c, 7), (m, 1), (w, 7)
    # Check ordering: material asc, month asc
    types = [r["material_type"] for r in result["matrix"]]
    months = [r["seasonal_month"] for r in result["matrix"]]
    assert types == ["concrete", "concrete", "metal_scrap", "wood_waste"]
    assert months == [1, 7, 1, 7]


def test_aggregates_across_cycles(persistence):
    """Multiple cycles in same (material, month) should aggregate."""
    _insert_cycle(persistence, "c1", sim_day=10, seasonal_month=1)
    _insert_supply(persistence, "c1", "s1", "concrete", tons=10.0, seasonal_mult=1.0)
    _insert_cycle(persistence, "c2", sim_day=11, seasonal_month=1)
    _insert_supply(persistence, "c2", "s2", "concrete", tons=20.0, seasonal_mult=1.2)
    result = persistence.get_seasonal_timeseries_by_material()
    row = result["matrix"][0]
    assert row["n_supply_offers"] == 2
    assert row["total_tons"] == 30.0
    # avg(1.0, 1.2) = 1.1
    assert abs(row["avg_seasonal_multiplier"] - 1.1) < 0.01


def test_excludes_null_material(persistence):
    """supply_offers with material_type=NULL should not appear."""
    _insert_cycle(persistence, "c1", sim_day=10, seasonal_month=1)
    _insert_supply(persistence, "c1", "s1", "concrete", tons=10.0)
    # Insert NULL material
    with persistence._conn() as conn:
        conn.execute(
            """INSERT INTO supply_offers
               (cycle_id, supply_id, material_type, available_tons,
                moisture_percent, quality_score)
               VALUES (?, ?, NULL, 5.0, 5.0, 0.8)""",
            ("c1", "s_null",),
        )
    result = persistence.get_seasonal_timeseries_by_material()
    assert result["n_materials"] == 1
    assert result["materials"] == ["concrete"]
    assert len(result["matrix"]) == 1


def test_excludes_cycles_with_null_month(persistence):
    """Cycles with seasonal_month NULL/0 should be excluded."""
    _insert_cycle(persistence, "c1", sim_day=10, seasonal_month=1)
    _insert_supply(persistence, "c1", "s1", "concrete", tons=10.0)
    # Cycle with seasonal_month=NULL (default)
    with persistence._conn() as conn:
        conn.execute(
            """INSERT INTO optimization_cycles
               (cycle_id, sim_day, sim_hour, wall_timestamp,
                activity_factor, n_supply_offers, n_demand_requests, n_matches,
                total_tons, total_cost_sek, total_co2_kg, total_distance_km,
                n_vehicles_used, n_vehicles_available, fleet_utilization_pct,
                solver_status, wall_duration_ms, seasonal_factor_avg, seasonal_month)
               VALUES (?, ?, 0, '2026-09-03T00:00:00',
                       1.0, 1, 1, 1, 5.0, 100.0, 50.0, 20.0,
                       1, 1, 50.0, 'OPTIMAL', 100, 1.0, NULL)""",
            ("c_null", 20),
        )
    result = persistence.get_seasonal_timeseries_by_material()
    # Only the one with seasonal_month=1
    assert len(result["matrix"]) == 1
    assert result["matrix"][0]["seasonal_month"] == 1


# ============================================
# API endpoint tests
# ============================================


def test_endpoint_returns_200_with_data(monkeypatch):
    from web.backend import main as backend_main
    from fastapi.testclient import TestClient

    class _FakePersistence:
        def get_seasonal_timeseries_by_material(self):
            return {
                "n_materials": 2,
                "n_months": 3,
                "materials": ["concrete", "metal_scrap"],
                "month_labels": ["Jan", "Feb", "Mar"],
                "matrix": [
                    {
                        "material_type": "concrete", "seasonal_month": 1,
                        "month_name": "Jan", "n_supply_offers": 5,
                        "total_tons": 50.0, "avg_seasonal_multiplier": 0.8,
                        "avg_base_multiplier": 0.8,
                    },
                ],
            }

    class _FakeCoord:
        persistence = _FakePersistence()

    monkeypatch.setattr(backend_main, "coordinator", _FakeCoord())
    client = TestClient(backend_main.app)
    resp = client.get("/api/persistence/seasonal-timeseries-by-material")
    assert resp.status_code == 200
    data = resp.json()
    assert data["n_materials"] == 2
    assert data["materials"] == ["concrete", "metal_scrap"]
    assert len(data["matrix"]) == 1


def test_endpoint_returns_503_when_no_coordinator(monkeypatch):
    from web.backend import main as backend_main
    from fastapi.testclient import TestClient

    monkeypatch.setattr(backend_main, "coordinator", None)
    client = TestClient(backend_main.app)
    resp = client.get("/api/persistence/seasonal-timeseries-by-material")
    assert resp.status_code == 503


def test_endpoint_returns_503_when_persistence_none(monkeypatch):
    from web.backend import main as backend_main
    from fastapi.testclient import TestClient

    class _FakeCoord:
        persistence = None

    monkeypatch.setattr(backend_main, "coordinator", _FakeCoord())
    client = TestClient(backend_main.app)
    resp = client.get("/api/persistence/seasonal-timeseries-by-material")
    assert resp.status_code == 503
