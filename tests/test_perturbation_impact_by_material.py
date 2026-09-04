"""
iter #46: tests for /api/persistence/perturbation-impact-by-material endpoint.

Covers:
1. Persistence.get_perturbation_impact_by_material() — basic aggregation
2. Sorted by n_perturbed DESC, NULL material excluded
3. Window filter (since/until sim_day)
4. Endpoint exposes the new method via /api/persistence/perturbation-impact-by-material
5. Endpoint validates sim_day range
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
        db_path = os.path.join(tmpdir, "test_pert_mat.db")
        p = Persistence(db_path)
        yield p


def _insert_cycle(p, cycle_id, sim_day, perturbation_count=0,
                  total_multiplier=1.0, base_seasonal=1.0,
                  seasonal_factor=1.0):
    with p._conn() as conn:
        conn.execute(
            """INSERT INTO optimization_cycles
               (cycle_id, sim_day, sim_hour, activity_factor, wall_timestamp,
                n_supply_offers, n_demand_requests, n_matches,
                total_tons, total_cost_sek, total_co2_kg, total_distance_km,
                n_vehicles_used, n_vehicles_available, fleet_utilization_pct,
                solver_status, wall_duration_ms, seasonal_factor_avg, seasonal_month,
                base_seasonal_factor_avg, perturbation_count,
                perturbation_total_multiplier)
               VALUES (?, ?, 0, 1.0, '2026-09-03T00:00:00',
                       1, 1, 1, 5.0, 100.0, 50.0, 20.0,
                       1, 1, 50.0, 'OPTIMAL', 100, ?, 1, ?, ?, ?)""",
            (cycle_id, sim_day, seasonal_factor, base_seasonal,
             perturbation_count, total_multiplier),
        )


def _insert_supply(p, cycle_id, supply_id, material_type,
                   perturbed=0, seasonal_mult=1.0, base_mult=1.0):
    with p._conn() as conn:
        conn.execute(
            """INSERT INTO supply_offers
               (cycle_id, supply_id, material_type, available_tons,
                moisture_percent, quality_score,
                base_seasonal_multiplier, seasonal_multiplier,
                perturbation_applied)
               VALUES (?, ?, ?, 10.0, 5.0, 0.8, ?, ?, ?)""",
            (cycle_id, supply_id, material_type, base_mult, seasonal_mult,
             perturbed),
        )


# ============================================
# Persistence method tests
# ============================================


def test_empty(persistence):
    result = persistence.get_perturbation_impact_by_material()
    assert result["by_material"] == []
    assert result["summary"]["n_materials"] == 0
    assert result["summary"]["n_perturbed_total"] == 0
    assert result["summary"]["n_supply_offers_total"] == 0
    assert result["summary"]["overall_perturbation_rate_pct"] == 0.0


def test_single_material_perturbed(persistence):
    _insert_cycle(persistence, "c1", sim_day=10, perturbation_count=2)
    _insert_supply(persistence, "c1", "s1", "concrete",
                   perturbed=1, seasonal_mult=1.5, base_mult=1.0)
    _insert_supply(persistence, "c1", "s2", "concrete", perturbed=0)
    result = persistence.get_perturbation_impact_by_material()
    assert len(result["by_material"]) == 1
    row = result["by_material"][0]
    assert row["material_type"] == "concrete"
    assert row["n_perturbed"] == 1
    assert row["n_total"] == 2
    assert row["perturbation_rate_pct"] == 50.0
    assert row["avg_effective_multiplier"] == 1.5
    assert row["avg_base_multiplier"] == 1.0
    assert row["avg_ratio"] == 1.5


def test_multiple_materials_sorted_by_perturbed(persistence):
    _insert_cycle(persistence, "c1", sim_day=10, perturbation_count=5)
    # concrete: 3 perturbed, 1 normal = 4 total
    _insert_supply(persistence, "c1", "s1", "concrete", perturbed=1, seasonal_mult=1.5)
    _insert_supply(persistence, "c1", "s2", "concrete", perturbed=1, seasonal_mult=1.4)
    _insert_supply(persistence, "c1", "s3", "concrete", perturbed=1, seasonal_mult=1.6)
    _insert_supply(persistence, "c1", "s4", "concrete", perturbed=0)
    # metal_scrap: 1 perturbed, 2 normal = 3 total
    _insert_supply(persistence, "c1", "s5", "metal_scrap", perturbed=1, seasonal_mult=1.1)
    _insert_supply(persistence, "c1", "s6", "metal_scrap", perturbed=0)
    _insert_supply(persistence, "c1", "s7", "metal_scrap", perturbed=0)
    # wood_waste: 0 perturbed, 1 normal
    _insert_supply(persistence, "c1", "s8", "wood_waste", perturbed=0)

    result = persistence.get_perturbation_impact_by_material()
    assert result["summary"]["n_materials"] == 3
    assert result["summary"]["n_perturbed_total"] == 4
    assert result["summary"]["n_supply_offers_total"] == 8
    assert result["summary"]["overall_perturbation_rate_pct"] == 50.0
    # Sorted by n_perturbed DESC
    materials = [r["material_type"] for r in result["by_material"]]
    assert materials == ["concrete", "metal_scrap", "wood_waste"]
    # First row (concrete) has perturbation_rate 75%
    assert result["by_material"][0]["perturbation_rate_pct"] == 75.0
    # wood_waste has 0% perturbation, avg_effective should be None
    assert result["by_material"][2]["perturbation_rate_pct"] == 0.0
    assert result["by_material"][2]["avg_effective_multiplier"] is None
    assert result["by_material"][2]["avg_ratio"] is None


def test_excludes_null_material(persistence):
    _insert_cycle(persistence, "c1", sim_day=10)
    _insert_supply(persistence, "c1", "s1", "concrete", perturbed=1)
    with persistence._conn() as conn:
        conn.execute(
            """INSERT INTO supply_offers
               (cycle_id, supply_id, material_type, available_tons,
                moisture_percent, quality_score,
                base_seasonal_multiplier, seasonal_multiplier,
                perturbation_applied)
               VALUES (?, ?, NULL, 5.0, 5.0, 0.8, 1.0, 1.0, 1)""",
            ("c1", "s_null",),
        )
    result = persistence.get_perturbation_impact_by_material()
    assert result["summary"]["n_materials"] == 1
    assert result["by_material"][0]["material_type"] == "concrete"


def test_window_filter(persistence):
    # Cycle 1 at sim_day 10 with concrete perturbed
    _insert_cycle(persistence, "c1", sim_day=10)
    _insert_supply(persistence, "c1", "s1", "concrete", perturbed=1)
    # Cycle 2 at sim_day 100 with metal_scrap perturbed
    _insert_cycle(persistence, "c2", sim_day=100)
    _insert_supply(persistence, "c2", "s2", "metal_scrap", perturbed=1)
    # Cycle 3 at sim_day 200 with wood_waste perturbed
    _insert_cycle(persistence, "c3", sim_day=200)
    _insert_supply(persistence, "c3", "s3", "wood_waste", perturbed=1)

    # Window: only 50-150
    result = persistence.get_perturbation_impact_by_material(
        since_sim_day=50, until_sim_day=150,
    )
    assert result["summary"]["n_materials"] == 1
    assert result["by_material"][0]["material_type"] == "metal_scrap"
    assert result["window"]["since_sim_day"] == 50
    assert result["window"]["until_sim_day"] == 150

    # Window: only 5-50 (concrete only)
    result2 = persistence.get_perturbation_impact_by_material(
        since_sim_day=5, until_sim_day=50,
    )
    assert result2["summary"]["n_materials"] == 1
    assert result2["by_material"][0]["material_type"] == "concrete"

    # No window
    result3 = persistence.get_perturbation_impact_by_material()
    assert result3["summary"]["n_materials"] == 3


# ============================================
# API endpoint tests
# ============================================


def test_endpoint_returns_200_with_data(monkeypatch):
    from web.backend import main as backend_main
    from fastapi.testclient import TestClient

    class _FakePersistence:
        def get_perturbation_impact_by_material(self, since_sim_day=None, until_sim_day=None):
            return {
                "by_material": [
                    {
                        "material_type": "concrete", "n_perturbed": 3, "n_total": 4,
                        "perturbation_rate_pct": 75.0,
                        "avg_effective_multiplier": 1.5,
                        "avg_base_multiplier": 1.0,
                        "avg_ratio": 1.5,
                    },
                ],
                "summary": {
                    "n_materials": 1, "n_perturbed_total": 3,
                    "n_supply_offers_total": 4,
                    "overall_perturbation_rate_pct": 75.0,
                },
                "window": {"since_sim_day": None, "until_sim_day": None},
            }

    class _FakeCoord:
        persistence = _FakePersistence()

    monkeypatch.setattr(backend_main, "coordinator", _FakeCoord())
    client = TestClient(backend_main.app)
    resp = client.get("/api/persistence/perturbation-impact-by-material")
    assert resp.status_code == 200
    data = resp.json()
    assert data["summary"]["n_materials"] == 1
    assert data["by_material"][0]["material_type"] == "concrete"


def test_endpoint_rejects_inverted_window(monkeypatch):
    from web.backend import main as backend_main
    from fastapi.testclient import TestClient

    class _FakePersistence:
        def get_perturbation_impact_by_material(self, since_sim_day=None, until_sim_day=None):
            return {"by_material": [], "summary": {}, "window": {}}

    class _FakeCoord:
        persistence = _FakePersistence()

    monkeypatch.setattr(backend_main, "coordinator", _FakeCoord())
    client = TestClient(backend_main.app)
    resp = client.get(
        "/api/persistence/perturbation-impact-by-material?since_sim_day=100&until_sim_day=50"
    )
    assert resp.status_code == 400


def test_endpoint_returns_503_when_no_coordinator(monkeypatch):
    from web.backend import main as backend_main
    from fastapi.testclient import TestClient

    monkeypatch.setattr(backend_main, "coordinator", None)
    client = TestClient(backend_main.app)
    resp = client.get("/api/persistence/perturbation-impact-by-material")
    assert resp.status_code == 503
