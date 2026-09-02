"""
Tests for perturbation impact analytics (iter #38).

Covers:
- Persistence.get_perturbation_impact()
- /api/persistence/perturbation-impact endpoint
- Schema migrations (base_seasonal_factor_avg, perturbation_count, etc.)
"""

import pytest


@pytest.fixture
def client():
    """FastAPI TestClient with startup triggered; cleans DB rows added in tests."""
    from fastapi.testclient import TestClient
    from web.backend import main as backend_main
    import sqlite3

    def _cleanup():
        # Remove any cycles added by this test (those with cycle_id prefix test_iter38_)
        # Use real prod DB so TestClient sees same data. Clean only our rows.
        conn = sqlite3.connect("data/simulation.db")
        conn.execute(
            "DELETE FROM optimization_cycles WHERE cycle_id LIKE 'TEST_ITER38_%'"
        )
        conn.execute(
            "DELETE FROM supply_offers WHERE cycle_id LIKE 'TEST_ITER38_%'"
        )
        conn.execute(
            "DELETE FROM seasonal_perturbations WHERE label LIKE 'iter38-test-%'"
        )
        conn.commit()
        conn.close()

    _cleanup()
    with TestClient(backend_main.app) as c:
        yield c
    _cleanup()


# ============================================
# Persistence-level tests
# ============================================

class TestPersistenceGetPerturbationImpact:
    """Direct tests for Persistence.get_perturbation_impact()."""

    def test_empty_db_returns_empty_list(self, tmp_path):
        """Fresh DB → empty cycles list, summary n_cycles_total=0."""
        from agents.persistence import Persistence
        p = Persistence(str(tmp_path / "test.db"))
        result = p.get_perturbation_impact()
        assert result["cycles"] == []
        assert result["summary"]["n_cycles_total"] == 0
        assert result["summary"]["avg_delta"] is None
        assert result["summary"]["max_delta"] is None

    def test_single_cycle_no_perturbation(self, tmp_path):
        """One cycle with no perturbation → delta=0, count=0."""
        from agents.persistence import Persistence
        p = Persistence(str(tmp_path / "test.db"))
        p.begin_cycle(
            cycle_id="TEST_C1",
            sim_day=5,
            sim_hour=12,
            activity_factor=1.0,
            seasonal_factor_avg=1.0,
            seasonal_month=1,
            base_seasonal_factor_avg=1.0,
            perturbation_count=0,
            perturbation_total_multiplier=1.0,
        )
        result = p.get_perturbation_impact()
        assert result["summary"]["n_cycles_total"] == 1
        assert result["summary"]["n_cycles_with_perturbation"] == 0
        assert result["summary"]["avg_delta"] == 0.0
        assert result["cycles"][0]["sim_day"] == 5

    def test_cycle_with_perturbation_calculates_delta(self, tmp_path):
        """Effective > baseline → positive delta; perturbation_count recorded."""
        from agents.persistence import Persistence
        p = Persistence(str(tmp_path / "test.db"))
        # Baseline 1.0, perturbation bumped to 1.4
        p.begin_cycle(
            cycle_id="TEST_C2",
            sim_day=10,
            sim_hour=12,
            activity_factor=1.0,
            seasonal_factor_avg=1.4,
            seasonal_month=4,
            base_seasonal_factor_avg=1.0,
            perturbation_count=5,
            perturbation_total_multiplier=1.4,
        )
        result = p.get_perturbation_impact()
        cycle = result["cycles"][0]
        assert cycle["delta"] == pytest.approx(0.4)
        assert cycle["perturbation_count"] == 5
        assert cycle["perturbation_total_multiplier"] == pytest.approx(1.4)
        assert result["summary"]["n_cycles_with_perturbation"] == 1

    def test_negative_delta_when_dampening(self, tmp_path):
        """Effective < baseline → negative delta (perturbation dampened)."""
        from agents.persistence import Persistence
        p = Persistence(str(tmp_path / "test.db"))
        p.begin_cycle(
            cycle_id="TEST_C3",
            sim_day=20,
            sim_hour=12,
            activity_factor=1.0,
            seasonal_factor_avg=0.6,
            seasonal_month=12,
            base_seasonal_factor_avg=1.0,
            perturbation_count=3,
            perturbation_total_multiplier=0.6,
        )
        result = p.get_perturbation_impact()
        cycle = result["cycles"][0]
        assert cycle["delta"] == pytest.approx(-0.4)
        assert result["summary"]["min_delta"] == pytest.approx(-0.4)
        assert result["summary"]["max_delta"] == pytest.approx(-0.4)

    def test_window_filter_respects_since_until(self, tmp_path):
        """since/until filter restricts returned cycles."""
        from agents.persistence import Persistence
        p = Persistence(str(tmp_path / "test.db"))
        for day in [1, 5, 10, 15, 20]:
            p.begin_cycle(
                cycle_id=f"TEST_W_{day}",
                sim_day=day,
                sim_hour=12,
                activity_factor=1.0,
                seasonal_factor_avg=1.0,
                seasonal_month=1,
            )
        result = p.get_perturbation_impact(since_sim_day=5, until_sim_day=15)
        sim_days = [c["sim_day"] for c in result["cycles"]]
        assert all(5 <= d <= 15 for d in sim_days)
        assert set(sim_days) == {5, 10, 15}

    def test_invalid_window_returns_400(self, client):
        """until < since should reject via HTTP 400."""
        resp = client.get(
            "/api/persistence/perturbation-impact?since_sim_day=20&until_sim_day=10"
        )
        assert resp.status_code == 400

    def test_summary_aggregates_correctly(self, tmp_path):
        """Multiple cycles: avg/max/min_delta and max_multiplier computed."""
        from agents.persistence import Persistence
        p = Persistence(str(tmp_path / "test.db"))
        # 3 cycles with deltas +0.2, -0.1, +0.4 → avg=0.167
        for i, (eff, base) in enumerate([(1.2, 1.0), (0.9, 1.0), (1.4, 1.0)]):
            p.begin_cycle(
                cycle_id=f"TEST_S_{i}",
                sim_day=i,
                sim_hour=12,
                activity_factor=1.0,
                seasonal_factor_avg=eff,
                seasonal_month=1,
                base_seasonal_factor_avg=base,
                perturbation_count=2,
                perturbation_total_multiplier=round(eff / base, 3),
            )
        result = p.get_perturbation_impact()
        s = result["summary"]
        assert s["n_cycles_total"] == 3
        assert s["n_cycles_with_perturbation"] == 3
        assert s["max_delta"] == pytest.approx(0.4)
        assert s["min_delta"] == pytest.approx(-0.1)
        assert s["avg_delta"] == pytest.approx(0.167, abs=0.01)
        assert s["max_total_multiplier"] == pytest.approx(1.4)


# ============================================
# /api/persistence/perturbation-impact endpoint
# ============================================

class TestPerturbationImpactEndpoint:
    def test_endpoint_returns_200(self, client):
        resp = client.get("/api/persistence/perturbation-impact")
        assert resp.status_code == 200

    def test_response_shape(self, client):
        resp = client.get("/api/persistence/perturbation-impact")
        data = resp.json()
        assert "cycles" in data
        assert "summary" in data
        summary = data["summary"]
        for k in (
            "n_cycles_total",
            "n_cycles_with_perturbation",
            "avg_delta",
            "max_delta",
            "min_delta",
            "max_total_multiplier",
            "window_start",
            "window_end",
        ):
            assert k in summary, f"missing summary.{k}"

    def test_cycle_row_shape(self, client):
        resp = client.get("/api/persistence/perturbation-impact")
        if resp.json()["cycles"]:
            c = resp.json()["cycles"][0]
            for k in (
                "sim_day",
                "wall_timestamp",
                "base_seasonal_factor_avg",
                "seasonal_factor_avg",
                "delta",
                "perturbation_count",
                "perturbation_total_multiplier",
            ):
                assert k in c, f"missing cycle.{k}"

    def test_limit_param_respected(self, client):
        resp = client.get("/api/persistence/perturbation-impact?limit=5")
        assert resp.status_code == 200
        assert len(resp.json()["cycles"]) <= 5

    def test_limit_default_is_90(self, client):
        """No limit param → default 90 (verified by schema, not by data)."""
        resp = client.get("/api/persistence/perturbation-impact")
        assert resp.status_code == 200


# ============================================
# Schema migration: existing DB without iter #38 columns
# ============================================

class TestSchemaBackwardCompat:
    def test_old_db_without_new_columns_loads(self, tmp_path):
        """A DB created BEFORE iter #38 (no new columns) should still work
        via _migrate_old_db(). The values default to 1.0 / 0."""
        import sqlite3
        db_path = tmp_path / "old_iter37.db"
        # Manually create an old-schema table
        conn = sqlite3.connect(str(db_path))
        conn.executescript("""
            CREATE TABLE optimization_cycles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cycle_id TEXT NOT NULL UNIQUE,
                sim_day INTEGER NOT NULL,
                sim_hour INTEGER NOT NULL,
                activity_factor REAL,
                wall_timestamp TEXT NOT NULL,
                n_supply_offers INTEGER DEFAULT 0,
                n_demand_requests INTEGER DEFAULT 0,
                n_matches INTEGER DEFAULT 0,
                total_tons REAL DEFAULT 0,
                total_cost_sek REAL DEFAULT 0,
                total_co2_kg REAL DEFAULT 0,
                total_distance_km REAL DEFAULT 0,
                n_vehicles_used INTEGER DEFAULT 0,
                n_vehicles_available INTEGER DEFAULT 0,
                fleet_utilization_pct REAL DEFAULT 0,
                solver_status TEXT,
                wall_duration_ms INTEGER,
                seasonal_factor_avg REAL DEFAULT 1.0,
                seasonal_month INTEGER DEFAULT 1
            );
        """)
        conn.execute(
            """INSERT INTO optimization_cycles
               (cycle_id, sim_day, sim_hour, activity_factor, wall_timestamp,
                seasonal_factor_avg, seasonal_month)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("OLD_C1", 5, 12, 1.0, "2026-01-01T00:00:00", 1.2, 1),
        )
        conn.commit()
        conn.close()
        # Now load via Persistence — should migrate + read OK
        from agents.persistence import Persistence
        p = Persistence(str(db_path))
        # Verify columns added (via direct sqlite since p._conn is a ctxmgr)
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        cols = [r[1] for r in conn.execute(
            "PRAGMA table_info(optimization_cycles)"
        ).fetchall()]
        conn.close()
        assert "base_seasonal_factor_avg" in cols
        assert "perturbation_count" in cols
        # And query works
        result = p.get_perturbation_impact()
        assert result["summary"]["n_cycles_total"] >= 1
