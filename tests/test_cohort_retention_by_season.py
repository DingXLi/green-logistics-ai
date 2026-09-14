"""
iter #70: tests for /api/persistence/cohort-retention-by-season endpoint.

Covers:
1. Persistence.get_cohort_retention_by_season() pure logic (no API):
   - empty DB returns consistent shape (n_seasons_with_data=0, totals=0)
   - season assignment correctness (12→winter, 6→summer)
   - per-season retention_rate_pct / one_time_pct computation
   - best/worst_season tracking
   - worst_vs_best_pct calculation
   - material_type filter
   - total_supply_ids dedup across seasons
   - n_cycles_in_season / total_supply_offers counts
2. /api/persistence/cohort-retention-by-season HTTP endpoint:
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
        db_path = os.path.join(tmpdir, "test_cohort_by_season.db")
        p = Persistence(db_path)
        yield p


def _insert_cycle(p, cycle_id: str, sim_day: int, seasonal_month: int):
    """Insert an optimization_cycles row with given seasonal_month."""
    with p._conn() as conn:
        conn.execute(
            """INSERT INTO optimization_cycles
               (cycle_id, sim_day, sim_hour, wall_timestamp,
                activity_factor, n_supply_offers, n_demand_requests, n_matches,
                total_tons, total_cost_sek, total_co2_kg, total_distance_km,
                n_vehicles_used, n_vehicles_available, fleet_utilization_pct,
                solver_status, wall_duration_ms, seasonal_factor_avg, seasonal_month)
               VALUES (?, ?, 0, '2026-09-14T00:00:00',
                       1.0, 1, 1, 1,
                       5.0, 100.0, 50.0, 20.0,
                       1, 1, 100.0,
                       'OPTIMAL', 100, 1.0, ?)""",
            (cycle_id, sim_day, seasonal_month),
        )


def _insert_supply(p, cycle_id: str, supply_id: str, material_type: str = "concrete"):
    """Insert a supply_offers row."""
    with p._conn() as conn:
        conn.execute(
            """INSERT INTO supply_offers
               (cycle_id, supply_id, material_type, available_tons,
                moisture_percent, quality_score,
                base_seasonal_multiplier, seasonal_multiplier, perturbation_applied)
               VALUES (?, ?, ?, 10.0, 0.5, 0.8, 1.0, 1.0, 0)""",
            (cycle_id, supply_id, material_type),
        )


# ---------------------------------------------------------------------------
# Persistence.get_cohort_retention_by_season() unit tests
# ---------------------------------------------------------------------------


def test_get_cohort_retention_by_season_empty(persistence):
    """Empty DB returns consistent shape with all 4 seasons present (n_supply_ids=0)."""
    result = persistence.get_cohort_retention_by_season()
    assert result["n_seasons_with_data"] == 0
    assert result["total_supply_ids"] == 0
    assert result["best_season"] is None
    assert result["worst_season"] is None
    assert result["best_season_pct"] == 0.0
    assert result["worst_season_pct"] == 0.0
    assert result["worst_vs_best_pct"] == 0.0
    assert result["material_type_filter"] is None
    # All 4 seasons always present in the array (consistent shape)
    assert len(result["seasons"]) == 4
    season_keys = {s["season"] for s in result["seasons"]}
    assert season_keys == {"winter", "spring", "summer", "fall"}


def test_season_assignment_correctness(persistence):
    """Months 12, 1, 2 → winter; 3,4,5 → spring; 6,7,8 → summer; 9,10,11 → fall."""
    months = [
        (1, "winter"),
        (12, "winter"),
        (2, "winter"),
        (3, "spring"),
        (4, "spring"),
        (5, "spring"),
        (6, "summer"),
        (7, "summer"),
        (8, "summer"),
        (9, "fall"),
        (10, "fall"),
        (11, "fall"),
    ]
    for i, (month, expected_season) in enumerate(months):
        cid = f"OPT{i+1:04d}"
        _insert_cycle(persistence, cid, i + 1, month)
        _insert_supply(persistence, cid, f"SUP_{cid}")

    result = persistence.get_cohort_retention_by_season()
    assert result["n_seasons_with_data"] == 4
    # Each season has exactly 3 supplies (one per month)
    for s in result["seasons"]:
        assert s["n_supply_ids"] == 3, f"season {s['season']} should have 3 ids, got {s['n_supply_ids']}"


def test_season_months_mapping_correctness(persistence):
    """Verify SEASON_MONTHS contains correct month arrays."""
    assert set(persistence.SEASON_MONTHS.keys()) == {"winter", "spring", "summer", "fall"}
    assert persistence.SEASON_MONTHS["winter"] == [12, 1, 2]
    assert persistence.SEASON_MONTHS["spring"] == [3, 4, 5]
    assert persistence.SEASON_MONTHS["summer"] == [6, 7, 8]
    assert persistence.SEASON_MONTHS["fall"] == [9, 10, 11]


def test_repeating_supply_in_one_season(persistence):
    """SUP_A appears 3 times in winter → winter retention_rate_pct = 100."""
    for i in range(3):
        cid = f"OPT{i+1:04d}"
        _insert_cycle(persistence, cid, i + 1, 1)  # all winter
        _insert_supply(persistence, cid, "SUP_A")

    result = persistence.get_cohort_retention_by_season()
    winter = next(s for s in result["seasons"] if s["season"] == "winter")
    assert winter["n_supply_ids"] == 1  # dedup to 1 unique
    assert winter["n_repeating"] == 1
    assert winter["n_one_time"] == 0
    assert winter["retention_rate_pct"] == 100.0
    assert winter["one_time_pct"] == 0.0


def test_one_time_supply_in_one_season(persistence):
    """SUP_B appears 1 time → one_time_pct = 100, retention = 0."""
    _insert_cycle(persistence, "OPT0001", 1, 1)
    _insert_supply(persistence, "OPT0001", "SUP_B")

    result = persistence.get_cohort_retention_by_season()
    winter = next(s for s in result["seasons"] if s["season"] == "winter")
    assert winter["n_supply_ids"] == 1
    assert winter["n_repeating"] == 0
    assert winter["n_one_time"] == 1
    assert winter["retention_rate_pct"] == 0.0
    assert winter["one_time_pct"] == 100.0


def test_mixed_retention_rates_across_seasons(persistence):
    """Winter: 2/3 repeating (66.7%), Summer: 1/2 repeating (50%), others: empty."""
    # Winter: 3 cycles, SUP_A repeats (3 cycles), SUP_B repeats (2), SUP_C one-time (1)
    for i in range(3):
        cid = f"OPT_W{i+1:04d}"
        _insert_cycle(persistence, cid, i + 1, 1)
        _insert_supply(persistence, cid, "SUP_A")  # always present (3 times)
        if i < 2:
            _insert_supply(persistence, cid, "SUP_B")  # 2 times
    _insert_supply(persistence, "OPT_W0001", "SUP_C")  # only cycle 1

    # Summer: 2 cycles, SUP_D repeats (2 times), SUP_E one-time (1)
    for i in range(2):
        cid = f"OPT_S{i+1:04d}"
        _insert_cycle(persistence, cid, 100 + i + 1, 7)
        _insert_supply(persistence, cid, "SUP_D")
    _insert_supply(persistence, "OPT_S0001", "SUP_E")  # only summer cycle 1

    result = persistence.get_cohort_retention_by_season()

    winter = next(s for s in result["seasons"] if s["season"] == "winter")
    summer = next(s for s in result["seasons"] if s["season"] == "summer")
    spring = next(s for s in result["seasons"] if s["season"] == "spring")
    fall = next(s for s in result["seasons"] if s["season"] == "fall")

    # Winter: SUP_A=3, SUP_B=2, SUP_C=1 → 3 unique, 2 repeating (A, B)
    assert winter["n_supply_ids"] == 3
    assert winter["n_repeating"] == 2
    assert winter["n_one_time"] == 1
    assert winter["retention_rate_pct"] == pytest.approx(66.7, abs=0.1)

    # Summer: SUP_D=2, SUP_E=1 → 2 unique, 1 repeating (D)
    assert summer["n_supply_ids"] == 2
    assert summer["n_repeating"] == 1
    assert summer["n_one_time"] == 1
    assert summer["retention_rate_pct"] == 50.0

    # Spring + Fall: empty
    assert spring["n_supply_ids"] == 0
    assert fall["n_supply_ids"] == 0

    # Best/worst season by retention%
    assert result["best_season"] == "winter"  # 66.7%
    assert result["best_season_pct"] == pytest.approx(66.7, abs=0.1)
    assert result["worst_season"] == "summer"  # 50.0%
    assert result["worst_season_pct"] == 50.0
    # (worst - best) / best * 100 = (50 - 66.7) / 66.7 * 100 = -25.0%
    assert result["worst_vs_best_pct"] == pytest.approx(-25.0, abs=0.1)

    # Total supply_ids dedup across seasons: SUP_A,B,C,D,E = 5
    assert result["total_supply_ids"] == 5
    assert result["n_seasons_with_data"] == 2


def test_total_supply_ids_dedup_across_seasons(persistence):
    """SUP_X in both winter + summer → counted once in total_supply_ids."""
    _insert_cycle(persistence, "OPT0001", 1, 1)
    _insert_supply(persistence, "OPT0001", "SUP_X")

    _insert_cycle(persistence, "OPT0002", 100, 7)
    _insert_supply(persistence, "OPT0002", "SUP_X")  # same id, different season

    result = persistence.get_cohort_retention_by_season()
    # SUP_X appears in both winter (1 time) and summer (1 time)
    winter = next(s for s in result["seasons"] if s["season"] == "winter")
    summer = next(s for s in result["seasons"] if s["season"] == "summer")
    # Each season has SUP_X (one-time within that season)
    assert winter["n_supply_ids"] == 1
    assert winter["retention_rate_pct"] == 0.0  # 1 time only
    assert summer["n_supply_ids"] == 1
    assert summer["retention_rate_pct"] == 0.0
    # But total_supply_ids dedup → SUP_X counted once
    assert result["total_supply_ids"] == 1


def test_material_type_filter(persistence):
    """material_type='concrete' excludes wood_waste supplies."""
    _insert_cycle(persistence, "OPT0001", 1, 1)
    _insert_supply(persistence, "OPT0001", "SUP_C1", material_type="concrete")
    _insert_supply(persistence, "OPT0001", "SUP_W1", material_type="wood_waste")

    result = persistence.get_cohort_retention_by_season(material_type="concrete")
    winter = next(s for s in result["seasons"] if s["season"] == "winter")
    # Only SUP_C1 counted
    assert winter["n_supply_ids"] == 1
    assert result["material_type_filter"] == "concrete"
    assert result["total_supply_ids"] == 1


def test_material_type_filter_unknown_returns_empty(persistence):
    """material_type='unknown_material' → all seasons have n_supply_ids=0."""
    _insert_cycle(persistence, "OPT0001", 1, 1)
    _insert_supply(persistence, "OPT0001", "SUP_C1", material_type="concrete")

    result = persistence.get_cohort_retention_by_season(material_type="unknown_material")
    assert result["n_seasons_with_data"] == 0
    assert result["total_supply_ids"] == 0
    for s in result["seasons"]:
        assert s["n_supply_ids"] == 0
    assert result["material_type_filter"] == "unknown_material"


def test_n_cycles_in_season_and_total_offers(persistence):
    """Verify n_cycles_in_season + total_supply_offers counts are accurate."""
    # 3 cycles in winter, each with 2 supply_offers
    for i in range(3):
        cid = f"OPT{i+1:04d}"
        _insert_cycle(persistence, cid, i + 1, 1)
        _insert_supply(persistence, cid, "SUP_A")
        _insert_supply(persistence, cid, "SUP_B")

    # 1 cycle in summer, with 1 offer
    _insert_cycle(persistence, "OPT_S01", 100, 7)
    _insert_supply(persistence, "OPT_S01", "SUP_X")

    result = persistence.get_cohort_retention_by_season()
    winter = next(s for s in result["seasons"] if s["season"] == "winter")
    summer = next(s for s in result["seasons"] if s["season"] == "summer")

    assert winter["n_cycles_in_season"] == 3
    assert winter["total_supply_offers"] == 6  # 3 cycles × 2 offers

    assert summer["n_cycles_in_season"] == 1
    assert summer["total_supply_offers"] == 1


def test_season_emojis_and_names(persistence):
    """Verify each season row has emoji + name + months array."""
    _insert_cycle(persistence, "OPT0001", 1, 1)
    _insert_supply(persistence, "OPT0001", "SUP_A")

    result = persistence.get_cohort_retention_by_season()
    season_meta = {s["season"]: (s["season_emoji"], s["season_name"], s["months"]) for s in result["seasons"]}
    assert season_meta["winter"][0] == "❄️"
    assert season_meta["winter"][1] == "Winter"
    assert season_meta["spring"][0] == "🌱"
    assert season_meta["summer"][0] == "☀️"
    assert season_meta["fall"][0] == "🍂"


def test_single_season_only(persistence):
    """When only one season has data, best == worst == that season."""
    for i in range(3):
        cid = f"OPT{i+1:04d}"
        _insert_cycle(persistence, cid, i + 1, 7)  # all summer
        _insert_supply(persistence, cid, "SUP_A")

    result = persistence.get_cohort_retention_by_season()
    assert result["best_season"] == "summer"
    assert result["worst_season"] == "summer"
    assert result["best_season_pct"] == result["worst_season_pct"]
    assert result["worst_vs_best_pct"] == 0.0
    assert result["n_seasons_with_data"] == 1


def test_all_seasons_zero_pct_when_only_one_time_supplies(persistence):
    """All 4 seasons have only one-time supplies → all retention = 0, worst_vs_best = 0."""
    seasons_months = [("OPT0001", 1), ("OPT0002", 4), ("OPT0003", 7), ("OPT0004", 10)]
    for cid, month in seasons_months:
        _insert_cycle(persistence, cid, int(month), month)
        _insert_supply(persistence, cid, f"SUP_{cid}")

    result = persistence.get_cohort_retention_by_season()
    # All 4 seasons have one-time supplies → retention = 0
    assert result["n_seasons_with_data"] == 4
    for s in result["seasons"]:
        assert s["n_supply_ids"] == 1
        assert s["retention_rate_pct"] == 0.0
    # All tied at 0 → best_season = first non-empty (winter, since first)
    assert result["best_season"] == "winter"
    assert result["worst_season"] == "winter"
    assert result["worst_vs_best_pct"] == 0.0


# ---------------------------------------------------------------------------
# HTTP endpoint tests
# ---------------------------------------------------------------------------


def test_endpoint_returns_full_envelope(monkeypatch):
    from web.backend import main as backend_main

    fake_data = {
        "n_seasons_with_data": 2,
        "total_supply_ids": 5,
        "seasons": [
            {"season": "winter", "season_name": "Winter", "season_emoji": "❄️",
             "months": [12, 1, 2], "n_supply_ids": 3, "n_one_time": 1,
             "n_repeating": 2, "retention_rate_pct": 66.7, "one_time_pct": 33.3,
             "total_supply_offers": 6, "n_cycles_in_season": 3},
            {"season": "spring", "season_name": "Spring", "season_emoji": "🌱",
             "months": [3, 4, 5], "n_supply_ids": 0, "n_one_time": 0,
             "n_repeating": 0, "retention_rate_pct": 0.0, "one_time_pct": 0.0,
             "total_supply_offers": 0, "n_cycles_in_season": 0},
            {"season": "summer", "season_name": "Summer", "season_emoji": "☀️",
             "months": [6, 7, 8], "n_supply_ids": 2, "n_one_time": 1,
             "n_repeating": 1, "retention_rate_pct": 50.0, "one_time_pct": 50.0,
             "total_supply_offers": 3, "n_cycles_in_season": 2},
            {"season": "fall", "season_name": "Fall", "season_emoji": "🍂",
             "months": [9, 10, 11], "n_supply_ids": 0, "n_one_time": 0,
             "n_repeating": 0, "retention_rate_pct": 0.0, "one_time_pct": 0.0,
             "total_supply_offers": 0, "n_cycles_in_season": 0},
        ],
        "best_season": "winter", "worst_season": "summer",
        "best_season_pct": 66.7, "worst_season_pct": 50.0,
        "worst_vs_best_pct": -25.0,
        "material_type_filter": None,
    }

    class _FakePersistence:
        def get_cohort_retention_by_season(self, material_type=None):
            fake_data["material_type_filter"] = material_type
            return fake_data

    class _FakeCoord:
        persistence = _FakePersistence()

    monkeypatch.setattr(backend_main, "coordinator", _FakeCoord())
    from fastapi.testclient import TestClient
    client = TestClient(backend_main.app)
    resp = client.get("/api/persistence/cohort-retention-by-season")
    assert resp.status_code == 200
    data = resp.json()
    assert data["n_seasons_with_data"] == 2
    assert data["total_supply_ids"] == 5
    assert len(data["seasons"]) == 4  # always 4 (consistent shape)
    # Check schema
    required_envelope = {"n_seasons_with_data", "total_supply_ids", "seasons",
                         "best_season", "worst_season", "best_season_pct",
                         "worst_season_pct", "worst_vs_best_pct",
                         "material_type_filter"}
    assert required_envelope.issubset(data.keys())
    required_season = {"season", "season_name", "season_emoji", "months",
                       "n_supply_ids", "n_one_time", "n_repeating",
                       "retention_rate_pct", "one_time_pct",
                       "total_supply_offers", "n_cycles_in_season"}
    for s in data["seasons"]:
        assert required_season.issubset(s.keys())


def test_endpoint_with_material_type_query_param(monkeypatch):
    from web.backend import main as backend_main

    captured = {}

    class _FakePersistence:
        def get_cohort_retention_by_season(self, material_type=None):
            captured["material_type"] = material_type
            return {
                "n_seasons_with_data": 1, "total_supply_ids": 1,
                "seasons": [
                    {"season": "winter", "season_name": "Winter", "season_emoji": "❄️",
                     "months": [12, 1, 2], "n_supply_ids": 1, "n_one_time": 0,
                     "n_repeating": 1, "retention_rate_pct": 100.0, "one_time_pct": 0.0,
                     "total_supply_offers": 1, "n_cycles_in_season": 1},
                    {"season": "spring", "season_name": "Spring", "season_emoji": "🌱",
                     "months": [3, 4, 5], "n_supply_ids": 0, "n_one_time": 0,
                     "n_repeating": 0, "retention_rate_pct": 0.0, "one_time_pct": 0.0,
                     "total_supply_offers": 0, "n_cycles_in_season": 0},
                    {"season": "summer", "season_name": "Summer", "season_emoji": "☀️",
                     "months": [6, 7, 8], "n_supply_ids": 0, "n_one_time": 0,
                     "n_repeating": 0, "retention_rate_pct": 0.0, "one_time_pct": 0.0,
                     "total_supply_offers": 0, "n_cycles_in_season": 0},
                    {"season": "fall", "season_name": "Fall", "season_emoji": "🍂",
                     "months": [9, 10, 11], "n_supply_ids": 0, "n_one_time": 0,
                     "n_repeating": 0, "retention_rate_pct": 0.0, "one_time_pct": 0.0,
                     "total_supply_offers": 0, "n_cycles_in_season": 0},
                ],
                "best_season": "winter", "worst_season": "winter",
                "best_season_pct": 100.0, "worst_season_pct": 100.0,
                "worst_vs_best_pct": 0.0,
                "material_type_filter": None,
            }

    class _FakeCoord:
        persistence = _FakePersistence()

    monkeypatch.setattr(backend_main, "coordinator", _FakeCoord())
    from fastapi.testclient import TestClient
    client = TestClient(backend_main.app)
    resp = client.get("/api/persistence/cohort-retention-by-season?material_type=concrete")
    assert resp.status_code == 200
    assert captured["material_type"] == "concrete"


def test_endpoint_503_when_no_coordinator(monkeypatch):
    from web.backend import main as backend_main
    monkeypatch.setattr(backend_main, "coordinator", None)
    from fastapi.testclient import TestClient
    client = TestClient(backend_main.app)
    resp = client.get("/api/persistence/cohort-retention-by-season")
    assert resp.status_code == 503


def test_endpoint_empty_db(monkeypatch):
    from web.backend import main as backend_main

    class _FakePersistence:
        def get_cohort_retention_by_season(self, material_type=None):
            return {
                "n_seasons_with_data": 0, "total_supply_ids": 0,
                "seasons": [
                    {"season": s, "season_name": s.title(), "season_emoji": "?",
                     "months": [], "n_supply_ids": 0, "n_one_time": 0,
                     "n_repeating": 0, "retention_rate_pct": 0.0, "one_time_pct": 0.0,
                     "total_supply_offers": 0, "n_cycles_in_season": 0}
                    for s in ["winter", "spring", "summer", "fall"]
                ],
                "best_season": None, "worst_season": None,
                "best_season_pct": 0.0, "worst_season_pct": 0.0,
                "worst_vs_best_pct": 0.0,
                "material_type_filter": None,
            }

    class _FakeCoord:
        persistence = _FakePersistence()

    monkeypatch.setattr(backend_main, "coordinator", _FakeCoord())
    from fastapi.testclient import TestClient
    client = TestClient(backend_main.app)
    resp = client.get("/api/persistence/cohort-retention-by-season")
    assert resp.status_code == 200
    data = resp.json()
    assert data["n_seasons_with_data"] == 0
    assert len(data["seasons"]) == 4
    assert data["best_season"] is None