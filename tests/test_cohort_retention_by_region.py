"""
iter #73: tests for /api/persistence/cohort-retention-by-region endpoint.

Covers:
1. Persistence.get_cohort_retention_by_region() pure logic (no API):
   - empty DB returns consistent shape (n_regions_with_data=0, 4 regions)
   - city assignment via haversine (GBG / BOR / STO coords)
   - per-region retention_rate_pct / one_time_pct computation
   - best/worst_region tracking
   - worst_vs_best_pct calculation
   - material_type filter
   - total_supply_ids dedup across regions
   - 'unknown' bucket for NULL coords
   - city_assignment_method metadata
2. /api/persistence/cohort-retention-by-region HTTP endpoint:
   - 503 when no coordinator
   - 200 happy path with valid data
   - schema validation
   - material_type query param passthrough
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
    """Build a temporary Persistence instance."""
    from agents.persistence import Persistence

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_cohort_by_region.db")
        p = Persistence(db_path)
        yield p


# Real Sweden facility coordinates (from real_sweden_facilities.py)
GBG_LAT, GBG_LON = 57.7321, 12.0123
BOR_LAT, BOR_LON = 57.7198, 14.1581
STO_LAT, STO_LON = 59.2621, 18.0413


def _insert_cycle(p, cycle_id: str, sim_day: int = 1):
    """Insert an optimization_cycles row."""
    with p._conn() as conn:
        conn.execute(
            """INSERT INTO optimization_cycles
               (cycle_id, sim_day, sim_hour, wall_timestamp,
                activity_factor, n_supply_offers, n_demand_requests, n_matches,
                total_tons, total_cost_sek, total_co2_kg, total_distance_km,
                n_vehicles_used, n_vehicles_available, fleet_utilization_pct,
                solver_status, wall_duration_ms, seasonal_factor_avg, seasonal_month)
               VALUES (?, ?, 0, '2026-09-17T00:00:00',
                       1.0, 1, 1, 1,
                       5.0, 100.0, 50.0, 20.0,
                       1, 1, 100.0,
                       'OPTIMAL', 100, 1.0, 6)""",
            (cycle_id, sim_day),
        )


def _insert_supply(p, cycle_id: str, supply_id: str, lat, lon,
                   material_type: str = "concrete"):
    """Insert a supply_offers row with given coords + material."""
    with p._conn() as conn:
        conn.execute(
            """INSERT INTO supply_offers
               (cycle_id, supply_id, location_lat, location_lon, material_type,
                available_tons, moisture_percent, quality_score,
                base_seasonal_multiplier, seasonal_multiplier, perturbation_applied)
               VALUES (?, ?, ?, ?, ?, 10.0, 0.5, 0.8, 1.0, 1.0, 0)""",
            (cycle_id, supply_id, lat, lon, material_type),
        )


# ---------------------------------------------------------------------------
# Persistence.get_cohort_retention_by_region() unit tests
# ---------------------------------------------------------------------------


def test_get_cohort_retention_by_region_empty(persistence):
    """Empty DB returns consistent shape with all 4 regions present (n_supply_ids=0)."""
    result = persistence.get_cohort_retention_by_region()
    assert result["n_regions_with_data"] == 0
    assert result["n_unknown_with_data"] == 0
    assert result["total_supply_ids"] == 0
    assert result["best_region"] is None
    assert result["worst_region"] is None
    assert result["best_region_pct"] == 0.0
    assert result["worst_region_pct"] == 0.0
    assert result["worst_vs_best_pct"] == 0.0
    assert result["material_type_filter"] is None
    assert result["city_assignment_method"] == "haversine_nearest_facility"
    # Always 4 regions (3 cities + unknown)
    assert len(result["regions"]) == 4
    region_keys = {r["region"] for r in result["regions"]}
    assert region_keys == {"Göteborg", "Borås", "Stockholm", "unknown"}


def test_city_assignment_correctness(persistence):
    """Coords near each city should map to that city."""
    # 1 supply near each real city
    _insert_cycle(persistence, "OPT000")
    _insert_supply(persistence, "OPT000", "SGBG", GBG_LAT, GBG_LON)
    _insert_cycle(persistence, "OPT001")
    _insert_supply(persistence, "OPT001", "SBOR", BOR_LAT, BOR_LON)
    _insert_cycle(persistence, "OPT002")
    _insert_supply(persistence, "OPT002", "SSTO", STO_LAT, STO_LON)

    result = persistence.get_cohort_retention_by_region()

    gbg = next(r for r in result["regions"] if r["region"] == "Göteborg")
    bor = next(r for r in result["regions"] if r["region"] == "Borås")
    sto = next(r for r in result["regions"] if r["region"] == "Stockholm")

    assert gbg["n_supply_ids"] == 1
    assert bor["n_supply_ids"] == 1
    assert sto["n_supply_ids"] == 1
    # Each is one-time (single appearance)
    assert gbg["n_one_time"] == 1
    assert bor["n_one_time"] == 1
    assert sto["n_one_time"] == 1
    assert gbg["retention_rate_pct"] == 0.0
    assert bor["retention_rate_pct"] == 0.0
    assert sto["retention_rate_pct"] == 0.0


def test_per_region_retention_computation(persistence):
    """Per-region retention: repeating/total * 100."""
    # Göteborg: 3 unique supplies, 2 repeating, 1 one-time → 66.7%
    for cid in ["OPT000", "OPT001", "OPT002"]:
        _insert_cycle(persistence, cid)
    _insert_supply(persistence, "OPT000", "SGBG_1", GBG_LAT, GBG_LON)
    _insert_supply(persistence, "OPT001", "SGBG_1", GBG_LAT, GBG_LON)
    _insert_supply(persistence, "OPT002", "SGBG_1", GBG_LAT, GBG_LON)
    _insert_supply(persistence, "OPT000", "SGBG_2", GBG_LAT, GBG_LON)
    _insert_supply(persistence, "OPT001", "SGBG_2", GBG_LAT, GBG_LON)
    _insert_supply(persistence, "OPT000", "SGBG_3", GBG_LAT, GBG_LON)

    # Borås: 2 unique, both repeating → 100%
    _insert_supply(persistence, "OPT000", "SBOR_1", BOR_LAT, BOR_LON)
    _insert_supply(persistence, "OPT001", "SBOR_1", BOR_LAT, BOR_LON)
    _insert_supply(persistence, "OPT000", "SBOR_2", BOR_LAT, BOR_LON)
    _insert_supply(persistence, "OPT001", "SBOR_2", BOR_LAT, BOR_LON)

    # Stockholm: 1 unique, one-time → 0%
    _insert_supply(persistence, "OPT000", "SSTO_1", STO_LAT, STO_LON)

    result = persistence.get_cohort_retention_by_region()

    gbg = next(r for r in result["regions"] if r["region"] == "Göteborg")
    bor = next(r for r in result["regions"] if r["region"] == "Borås")
    sto = next(r for r in result["regions"] if r["region"] == "Stockholm")

    assert gbg["n_supply_ids"] == 3
    assert gbg["n_one_time"] == 1
    assert gbg["n_repeating"] == 2
    assert gbg["retention_rate_pct"] == 66.7

    assert bor["n_supply_ids"] == 2
    assert bor["n_one_time"] == 0
    assert bor["n_repeating"] == 2
    assert bor["retention_rate_pct"] == 100.0

    assert sto["n_supply_ids"] == 1
    assert sto["n_one_time"] == 1
    assert sto["n_repeating"] == 0
    assert sto["retention_rate_pct"] == 0.0


def test_best_worst_region_tracking(persistence):
    """best/worst_region picked correctly from retention rates."""
    for cid in ["OPT000", "OPT001"]:
        _insert_cycle(persistence, cid)

    # Göteborg: 100% (1 repeating)
    _insert_supply(persistence, "OPT000", "SGBG_1", GBG_LAT, GBG_LON)
    _insert_supply(persistence, "OPT001", "SGBG_1", GBG_LAT, GBG_LON)

    # Borås: 50% (1 repeating, 1 one-time)
    _insert_supply(persistence, "OPT000", "SBOR_1", BOR_LAT, BOR_LON)
    _insert_supply(persistence, "OPT001", "SBOR_1", BOR_LAT, BOR_LON)
    _insert_supply(persistence, "OPT000", "SBOR_2", BOR_LAT, BOR_LON)

    # Stockholm: 0% (1 one-time)
    _insert_supply(persistence, "OPT000", "SSTO_1", STO_LAT, STO_LON)

    result = persistence.get_cohort_retention_by_region()
    assert result["best_region"] == "Göteborg"
    assert result["best_region_pct"] == 100.0
    assert result["worst_region"] == "Stockholm"
    assert result["worst_region_pct"] == 0.0
    # (0 - 100) / 100 * 100 = -100.0
    assert result["worst_vs_best_pct"] == -100.0


def test_best_worst_when_only_one_region_has_data(persistence):
    """Single-region dataset has best == worst."""
    _insert_cycle(persistence, "OPT000")
    _insert_supply(persistence, "OPT000", "SGBG_1", GBG_LAT, GBG_LON)
    _insert_cycle(persistence, "OPT001")
    _insert_supply(persistence, "OPT001", "SGBG_1", GBG_LAT, GBG_LON)

    result = persistence.get_cohort_retention_by_region()
    assert result["best_region"] == "Göteborg"
    assert result["worst_region"] == "Göteborg"
    assert result["best_region_pct"] == 100.0
    assert result["worst_region_pct"] == 100.0
    assert result["worst_vs_best_pct"] == 0.0


def test_unknown_bucket_for_null_coords(persistence):
    """Supply with NULL lat/lon goes to 'unknown' bucket."""
    _insert_cycle(persistence, "OPT000")
    # NULL lat/lon
    with persistence._conn() as conn:
        conn.execute(
            """INSERT INTO supply_offers
               (cycle_id, supply_id, location_lat, location_lon, material_type,
                available_tons, moisture_percent, quality_score,
                base_seasonal_multiplier, seasonal_multiplier, perturbation_applied)
               VALUES (?, ?, NULL, NULL, ?, 10.0, 0.5, 0.8, 1.0, 1.0, 0)""",
            ("OPT000", "S_UNKNOWN", "concrete"),
        )

    result = persistence.get_cohort_retention_by_region()
    unknown = next(r for r in result["regions"] if r["region"] == "unknown")
    assert unknown["n_supply_ids"] == 1
    assert result["n_unknown_with_data"] == 1
    # n_regions_with_data counts only real cities (Göteborg/Borås/Stockholm)
    assert result["n_regions_with_data"] == 0


def test_material_type_filter(persistence):
    """material_type filter excludes rows from other materials."""
    for cid in ["OPT000", "OPT001"]:
        _insert_cycle(persistence, cid)

    # 1 GBG concrete + 1 GBG wood
    _insert_supply(persistence, "OPT000", "SGBG_CONC", GBG_LAT, GBG_LON, "concrete")
    _insert_supply(persistence, "OPT001", "SGBG_CONC", GBG_LAT, GBG_LON, "concrete")
    _insert_supply(persistence, "OPT000", "SGBG_WOOD", GBG_LAT, GBG_LON, "wood_waste")

    # With concrete filter: 1 unique (SGBG_CONC, repeating) → 100% retention
    result_conc = persistence.get_cohort_retention_by_region(material_type="concrete")
    gbg_conc = next(r for r in result_conc["regions"] if r["region"] == "Göteborg")
    assert gbg_conc["n_supply_ids"] == 1
    assert gbg_conc["retention_rate_pct"] == 100.0
    assert result_conc["material_type_filter"] == "concrete"

    # With wood_waste filter: 1 unique, one-time → 0% retention
    result_wood = persistence.get_cohort_retention_by_region(material_type="wood_waste")
    gbg_wood = next(r for r in result_wood["regions"] if r["region"] == "Göteborg")
    assert gbg_wood["n_supply_ids"] == 1
    assert gbg_wood["retention_rate_pct"] == 0.0


def test_total_supply_ids_dedup_across_regions(persistence):
    """total_supply_ids deduped across all regions."""
    _insert_cycle(persistence, "OPT000")
    _insert_supply(persistence, "OPT000", "SGBG", GBG_LAT, GBG_LON)
    _insert_supply(persistence, "OPT000", "SBOR", BOR_LAT, BOR_LON)
    _insert_supply(persistence, "OPT000", "SSTO", STO_LAT, STO_LON)

    result = persistence.get_cohort_retention_by_region()
    # 3 unique IDs in 3 cities, no overlap
    assert result["total_supply_ids"] == 3


def test_n_cycles_in_region_field_present(persistence):
    """Each region dict has all 8 required keys."""
    _insert_cycle(persistence, "OPT000")
    _insert_supply(persistence, "OPT000", "SGBG", GBG_LAT, GBG_LON)

    result = persistence.get_cohort_retention_by_region()
    required = {
        "region", "region_name", "region_emoji",
        "n_supply_ids", "n_one_time", "n_repeating",
        "retention_rate_pct", "one_time_pct",
        "total_supply_offers", "n_cycles_in_region",
    }
    for r in result["regions"]:
        assert required.issubset(r.keys())


def test_all_three_cities_appear_in_correct_order(persistence):
    """Regions array order: Göteborg → Borås → Stockholm → unknown."""
    result = persistence.get_cohort_retention_by_region()
    keys = [r["region"] for r in result["regions"]]
    assert keys == ["Göteborg", "Borås", "Stockholm", "unknown"]


def test_region_emoji_set(persistence):
    """Each city has a distinct emoji for visual identification."""
    result = persistence.get_cohort_retention_by_region()
    emoji_map = {r["region"]: r["region_emoji"] for r in result["regions"]}
    assert emoji_map["Göteborg"] == "⚓"
    assert emoji_map["Borås"] == "🧵"
    assert emoji_map["Stockholm"] == "🏛️"
    assert emoji_map["unknown"] == "❓"


def test_haversine_does_not_throw_for_extreme_coords(persistence):
    """Extreme coords still classify (falls into nearest city by haversine)."""
    _insert_cycle(persistence, "OPT000")
    # Coord deep in north Sweden (near Kiruna), far from all 3 cities.
    # Should classify to whichever is closest (probably Stockholm or unknown).
    _insert_supply(persistence, "OPT000", "S_NORTH", 67.8558, 20.2253, "concrete")

    result = persistence.get_cohort_retention_by_region()
    total = sum(r["n_supply_ids"] for r in result["regions"])
    assert total == 1  # exactly 1 supply classified


# ---------------------------------------------------------------------------
# /api/persistence/cohort-retention-by-region HTTP endpoint tests
# ---------------------------------------------------------------------------


def test_endpoint_503_when_no_coordinator(monkeypatch):
    from web.backend import main as backend_main
    monkeypatch.setattr(backend_main, "coordinator", None)
    from fastapi.testclient import TestClient
    client = TestClient(backend_main.app)
    resp = client.get("/api/persistence/cohort-retention-by-region")
    assert resp.status_code == 503


def test_endpoint_happy_path(monkeypatch):
    from web.backend import main as backend_main

    captured = {}

    class _FakePersistence:
        def get_cohort_retention_by_region(self, material_type=None):
            captured["material_type"] = material_type
            return {
                "n_regions_with_data": 2, "n_unknown_with_data": 0,
                "total_supply_ids": 5,
                "regions": [
                    {"region": "Göteborg", "region_name": "Göteborg",
                     "region_emoji": "⚓", "n_supply_ids": 3,
                     "n_one_time": 1, "n_repeating": 2,
                     "retention_rate_pct": 66.7, "one_time_pct": 33.3,
                     "total_supply_offers": 6, "n_cycles_in_region": 0},
                    {"region": "Borås", "region_name": "Borås",
                     "region_emoji": "🧵", "n_supply_ids": 2,
                     "n_one_time": 0, "n_repeating": 2,
                     "retention_rate_pct": 100.0, "one_time_pct": 0.0,
                     "total_supply_offers": 4, "n_cycles_in_region": 0},
                    {"region": "Stockholm", "region_name": "Stockholm",
                     "region_emoji": "🏛️", "n_supply_ids": 0,
                     "n_one_time": 0, "n_repeating": 0,
                     "retention_rate_pct": 0.0, "one_time_pct": 0.0,
                     "total_supply_offers": 0, "n_cycles_in_region": 0},
                    {"region": "unknown", "region_name": "unknown",
                     "region_emoji": "❓", "n_supply_ids": 0,
                     "n_one_time": 0, "n_repeating": 0,
                     "retention_rate_pct": 0.0, "one_time_pct": 0.0,
                     "total_supply_offers": 0, "n_cycles_in_region": 0},
                ],
                "best_region": "Borås", "worst_region": "Göteborg",
                "best_region_pct": 100.0, "worst_region_pct": 66.7,
                "worst_vs_best_pct": -33.3,
                "material_type_filter": None,
                "city_assignment_method": "haversine_nearest_facility",
            }

    class _FakeCoord:
        persistence = _FakePersistence()

    monkeypatch.setattr(backend_main, "coordinator", _FakeCoord())
    from fastapi.testclient import TestClient
    client = TestClient(backend_main.app)
    resp = client.get("/api/persistence/cohort-retention-by-region")
    assert resp.status_code == 200
    data = resp.json()
    assert data["n_regions_with_data"] == 2
    assert data["total_supply_ids"] == 5
    assert len(data["regions"]) == 4  # always 4 (consistent shape)
    # Check envelope
    required_envelope = {"n_regions_with_data", "n_unknown_with_data",
                         "total_supply_ids", "regions",
                         "best_region", "worst_region",
                         "best_region_pct", "worst_region_pct",
                         "worst_vs_best_pct",
                         "material_type_filter", "city_assignment_method"}
    assert required_envelope.issubset(data.keys())
    required_region = {"region", "region_name", "region_emoji",
                       "n_supply_ids", "n_one_time", "n_repeating",
                       "retention_rate_pct", "one_time_pct",
                       "total_supply_offers", "n_cycles_in_region"}
    for r in data["regions"]:
        assert required_region.issubset(r.keys())


def test_endpoint_with_material_type_query_param(monkeypatch):
    from web.backend import main as backend_main

    captured = {}

    class _FakePersistence:
        def get_cohort_retention_by_region(self, material_type=None):
            captured["material_type"] = material_type
            return {
                "n_regions_with_data": 1, "n_unknown_with_data": 0,
                "total_supply_ids": 1,
                "regions": [
                    {"region": "Borås", "region_name": "Borås",
                     "region_emoji": "🧵", "n_supply_ids": 1,
                     "n_one_time": 0, "n_repeating": 1,
                     "retention_rate_pct": 100.0, "one_time_pct": 0.0,
                     "total_supply_offers": 2, "n_cycles_in_region": 0},
                    {"region": "Göteborg", "region_name": "Göteborg",
                     "region_emoji": "⚓", "n_supply_ids": 0,
                     "n_one_time": 0, "n_repeating": 0,
                     "retention_rate_pct": 0.0, "one_time_pct": 0.0,
                     "total_supply_offers": 0, "n_cycles_in_region": 0},
                    {"region": "Stockholm", "region_name": "Stockholm",
                     "region_emoji": "🏛️", "n_supply_ids": 0,
                     "n_one_time": 0, "n_repeating": 0,
                     "retention_rate_pct": 0.0, "one_time_pct": 0.0,
                     "total_supply_offers": 0, "n_cycles_in_region": 0},
                    {"region": "unknown", "region_name": "unknown",
                     "region_emoji": "❓", "n_supply_ids": 0,
                     "n_one_time": 0, "n_repeating": 0,
                     "retention_rate_pct": 0.0, "one_time_pct": 0.0,
                     "total_supply_offers": 0, "n_cycles_in_region": 0},
                ],
                "best_region": "Borås", "worst_region": "Borås",
                "best_region_pct": 100.0, "worst_region_pct": 100.0,
                "worst_vs_best_pct": 0.0,
                "material_type_filter": None,
                "city_assignment_method": "haversine_nearest_facility",
            }

    class _FakeCoord:
        persistence = _FakePersistence()

    monkeypatch.setattr(backend_main, "coordinator", _FakeCoord())
    from fastapi.testclient import TestClient
    client = TestClient(backend_main.app)
    resp = client.get("/api/persistence/cohort-retention-by-region?material_type=concrete")
    assert resp.status_code == 200
    assert captured["material_type"] == "concrete"


def test_endpoint_empty_db(monkeypatch):
    from web.backend import main as backend_main

    class _FakePersistence:
        def get_cohort_retention_by_region(self, material_type=None):
            return {
                "n_regions_with_data": 0, "n_unknown_with_data": 0,
                "total_supply_ids": 0,
                "regions": [
                    {"region": "Göteborg", "region_name": "Göteborg",
                     "region_emoji": "⚓", "n_supply_ids": 0,
                     "n_one_time": 0, "n_repeating": 0,
                     "retention_rate_pct": 0.0, "one_time_pct": 0.0,
                     "total_supply_offers": 0, "n_cycles_in_region": 0},
                    {"region": "Borås", "region_name": "Borås",
                     "region_emoji": "🧵", "n_supply_ids": 0,
                     "n_one_time": 0, "n_repeating": 0,
                     "retention_rate_pct": 0.0, "one_time_pct": 0.0,
                     "total_supply_offers": 0, "n_cycles_in_region": 0},
                    {"region": "Stockholm", "region_name": "Stockholm",
                     "region_emoji": "🏛️", "n_supply_ids": 0,
                     "n_one_time": 0, "n_repeating": 0,
                     "retention_rate_pct": 0.0, "one_time_pct": 0.0,
                     "total_supply_offers": 0, "n_cycles_in_region": 0},
                    {"region": "unknown", "region_name": "unknown",
                     "region_emoji": "❓", "n_supply_ids": 0,
                     "n_one_time": 0, "n_repeating": 0,
                     "retention_rate_pct": 0.0, "one_time_pct": 0.0,
                     "total_supply_offers": 0, "n_cycles_in_region": 0},
                ],
                "best_region": None, "worst_region": None,
                "best_region_pct": 0.0, "worst_region_pct": 0.0,
                "worst_vs_best_pct": 0.0,
                "material_type_filter": None,
                "city_assignment_method": "haversine_nearest_facility",
            }

    class _FakeCoord:
        persistence = _FakePersistence()

    monkeypatch.setattr(backend_main, "coordinator", _FakeCoord())
    from fastapi.testclient import TestClient
    client = TestClient(backend_main.app)
    resp = client.get("/api/persistence/cohort-retention-by-region")
    assert resp.status_code == 200
    data = resp.json()
    assert data["n_regions_with_data"] == 0
    assert len(data["regions"]) == 4