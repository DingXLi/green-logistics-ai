"""
iter #47: tests for /api/persistence/export/perturbed-supplies.csv endpoint.

Covers:
1. Persistence.export_perturbed_supplies_csv() — basic CSV with perturbation cols
2. only_perturbed filter: only include rows with perturbation_applied=1
3. multiplier_ratio + was_perturbed derived columns
4. Endpoint exposes the new method via /api/persistence/export/perturbed-supplies.csv
5. Endpoint validates only_perturbed query param
"""
import csv
import io
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
        db_path = os.path.join(tmpdir, "test_pert_exp.db")
        p = Persistence(db_path)
        yield p


def _insert_cycle(p, cycle_id, sim_day):
    """Insert a cycle row."""
    with p._conn() as conn:
        conn.execute(
            """INSERT INTO optimization_cycles
               (cycle_id, sim_day, sim_hour, wall_timestamp,
                activity_factor, n_supply_offers, n_demand_requests, n_matches,
                total_tons, total_cost_sek, total_co2_kg, total_distance_km,
                n_vehicles_used, n_vehicles_available, fleet_utilization_pct,
                solver_status, wall_duration_ms)
               VALUES (?, ?, 0, '2026-09-04T00:00:00',
                       1.0, 1, 1, 1, 5.0, 100.0, 50.0, 20.0,
                       1, 1, 50.0, 'OPTIMAL', 100)""",
            (cycle_id, sim_day),
        )


def _insert_supply(p, cycle_id, supply_id, material_type="concrete",
                   perturbed=0, seasonal_mult=1.0, base_mult=1.0, tons=10.0):
    with p._conn() as conn:
        conn.execute(
            """INSERT INTO supply_offers
               (cycle_id, supply_id, material_type, available_tons,
                moisture_percent, quality_score,
                base_seasonal_multiplier, seasonal_multiplier, perturbation_applied)
               VALUES (?, ?, ?, ?, 5.0, 0.8, ?, ?, ?)""",
            (cycle_id, supply_id, material_type, tons, base_mult, seasonal_mult, perturbed),
        )


# ============================================
# Persistence method tests
# ============================================


def test_export_perturbed_supplies_empty(persistence):
    csv_str = persistence.export_perturbed_supplies_csv(limit=10)
    # Should have header + 0 data rows; metadata is in # lines
    data_lines = [l for l in csv_str.split("\n") if l and not l.startswith("#")]
    assert len(data_lines) == 1  # only header
    assert "cycle_id" in data_lines[0]
    assert "base_seasonal_multiplier" in data_lines[0]
    assert "seasonal_multiplier" in data_lines[0]
    assert "perturbation_applied" in data_lines[0]
    assert "multiplier_ratio" in data_lines[0]
    assert "was_perturbed" in data_lines[0]


def test_export_perturbed_supplies_basic(persistence):
    _insert_cycle(persistence, "c1", sim_day=10)
    _insert_supply(persistence, "c1", "s1", "concrete", perturbed=1,
                   seasonal_mult=1.5, base_mult=1.0)
    _insert_supply(persistence, "c1", "s2", "metal_scrap", perturbed=0,
                   seasonal_mult=1.0, base_mult=1.0)
    csv_str = persistence.export_perturbed_supplies_csv(limit=10)
    # Parse CSV (skip metadata lines)
    data_lines = [l for l in csv_str.split("\n") if l and not l.startswith("#")]
    reader = csv.DictReader(io.StringIO("\n".join(data_lines)))
    rows = list(reader)
    assert len(rows) == 2
    # Find perturbed row
    perturbed_row = next(r for r in rows if r["supply_id"] == "s1")
    assert perturbed_row["perturbation_applied"] == "1"
    assert perturbed_row["was_perturbed"] == "True"
    assert float(perturbed_row["base_seasonal_multiplier"]) == 1.0
    assert float(perturbed_row["seasonal_multiplier"]) == 1.5
    assert float(perturbed_row["multiplier_ratio"]) == 1.5
    # Non-perturbed row
    normal_row = next(r for r in rows if r["supply_id"] == "s2")
    assert normal_row["perturbation_applied"] == "0"
    assert normal_row["was_perturbed"] == "False"
    assert normal_row["multiplier_ratio"] == "1.0"


def test_export_perturbed_supplies_only_perturbed_filter(persistence):
    _insert_cycle(persistence, "c1", sim_day=10)
    _insert_supply(persistence, "c1", "s1", "concrete", perturbed=1, seasonal_mult=1.5)
    _insert_supply(persistence, "c1", "s2", "metal_scrap", perturbed=0)
    _insert_supply(persistence, "c1", "s3", "wood_waste", perturbed=1, seasonal_mult=1.3)
    csv_str = persistence.export_perturbed_supplies_csv(limit=10, only_perturbed=True)
    data_lines = [l for l in csv_str.split("\n") if l and not l.startswith("#")]
    reader = csv.DictReader(io.StringIO("\n".join(data_lines)))
    rows = list(reader)
    assert len(rows) == 2
    assert all(r["was_perturbed"] == "True" for r in rows)
    supply_ids = {r["supply_id"] for r in rows}
    assert supply_ids == {"s1", "s3"}


def test_export_perturbed_supplies_metadata(persistence):
    csv_str = persistence.export_perturbed_supplies_csv(limit=10, include_metadata=True)
    lines = csv_str.split("\n")
    # First 6 lines are metadata
    assert lines[0] == "# Green Logistics AI CSV export"
    assert "# generated_at:" in lines[1]
    assert "# table: perturbed_supplies" in lines[4]
    assert "# only_perturbed: False" in lines[6]


def test_export_perturbed_supplies_no_metadata(persistence):
    csv_str = persistence.export_perturbed_supplies_csv(limit=10, include_metadata=False)
    assert not csv_str.startswith("#")
    # First line is the header
    assert csv_str.split("\n")[0].startswith("cycle_id")


def test_export_perturbed_supplies_handles_null_base(persistence):
    """When base_seasonal_multiplier is 0 or NULL, multiplier_ratio should be None."""
    _insert_cycle(persistence, "c1", sim_day=10)
    with persistence._conn() as conn:
        conn.execute(
            """INSERT INTO supply_offers
               (cycle_id, supply_id, material_type, available_tons,
                moisture_percent, quality_score,
                base_seasonal_multiplier, seasonal_multiplier, perturbation_applied)
               VALUES (?, ?, ?, 10.0, 5.0, 0.8, 0.0, 1.5, 1)""",
            ("c1", "s_zero", "concrete"),
        )
    csv_str = persistence.export_perturbed_supplies_csv(limit=10, include_metadata=False)
    data_lines = [l for l in csv_str.split("\n") if l]
    reader = csv.DictReader(io.StringIO("\n".join(data_lines)))
    rows = list(reader)
    assert len(rows) == 1
    assert rows[0]["multiplier_ratio"] == ""  # None serialized as empty string


# ============================================
# API endpoint tests
# ============================================


def test_endpoint_returns_200_with_csv(monkeypatch):
    from web.backend import main as backend_main
    from fastapi.testclient import TestClient

    class _FakePersistence:
        def export_perturbed_supplies_csv(self, limit=10000, include_metadata=True, only_perturbed=False):
            return "cycle_id,supply_id\nc1,s1\n"

    class _FakeCoord:
        persistence = _FakePersistence()

    monkeypatch.setattr(backend_main, "coordinator", _FakeCoord())
    client = TestClient(backend_main.app)
    resp = client.get("/api/persistence/export/perturbed-supplies.csv?limit=100")
    assert resp.status_code == 200
    assert "text/csv" in resp.headers.get("content-type", "")
    assert "perturbed_supplies_100.csv" in resp.headers.get("content-disposition", "")


def test_endpoint_only_perturbed_filename_suffix(monkeypatch):
    from web.backend import main as backend_main
    from fastapi.testclient import TestClient

    class _FakePersistence:
        def export_perturbed_supplies_csv(self, limit=10000, include_metadata=True, only_perturbed=False):
            return "cycle_id,supply_id\n"

    class _FakeCoord:
        persistence = _FakePersistence()

    monkeypatch.setattr(backend_main, "coordinator", _FakeCoord())
    client = TestClient(backend_main.app)
    resp = client.get("/api/persistence/export/perturbed-supplies.csv?only_perturbed=true")
    assert resp.status_code == 200
    assert "perturbed_supplies_perturbed_" in resp.headers.get("content-disposition", "")


def test_endpoint_returns_503_when_no_coordinator(monkeypatch):
    from web.backend import main as backend_main
    from fastapi.testclient import TestClient

    monkeypatch.setattr(backend_main, "coordinator", None)
    client = TestClient(backend_main.app)
    resp = client.get("/api/persistence/export/perturbed-supplies.csv")
    assert resp.status_code == 503
