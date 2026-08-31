"""
Tests for /api/persistence/llm-cost-timeseries (iter #28).

新 endpoint: 按 sim_day 聚合 llm_decisions 表, 返回 LLM 使用趋势.
"""

import pytest
from datetime import datetime


# ============================================
# Persistence layer unit tests
# ============================================

class TestLlmCostTimeseriesPersistence:
    """Persistence.get_llm_cost_timeseries()"""

    def _insert_llm_decisions(self, p, n_per_day=2, n_days=5):
        """Helper: insert n_per_day llm_decisions for n_days sim_days.

        First inserts a placeholder optimization_cycles row for each sim_day
        (llm_decisions.cycle_id has FK to optimization_cycles.cycle_id).
        """
        with p._conn() as conn:
            for day in range(1, n_days + 1):
                # First insert a cycle (FK requirement)
                conn.execute(
                    """INSERT INTO optimization_cycles
                       (cycle_id, sim_day, sim_hour, wall_timestamp,
                        activity_factor, n_supply_offers, n_demand_requests,
                        n_matches, total_tons, total_cost_sek, total_co2_kg,
                        total_distance_km, n_vehicles_used, n_vehicles_available,
                        fleet_utilization_pct, solver_status, wall_duration_ms,
                        seasonal_factor_avg, seasonal_month)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        f"c{day}", day, 12, datetime.now().isoformat(),
                        1.0, 10, 5, 3, 100.0, 500.0, 50.0,
                        20.0, 2, 5, 40.0, "optimal", 100,
                        1.0, 1,
                    ),
                )
                for n in range(n_per_day):
                    source = "llm" if n % 2 == 0 else "fallback"
                    conn.execute(
                        """INSERT INTO llm_decisions
                           (cycle_id, sim_day, sim_hour, decision_type,
                            target_id, target_type, multiplier, trend, confidence,
                            reason, source, raw_json, wall_timestamp)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            f"c{day}", day, 12, "demand_prediction",
                            f"DEM{n:03d}", "demand_point",
                            1.0 + n * 0.1,
                            "stable", 0.8, "test reason",
                            source, "{}", datetime.now().isoformat(),
                        ),
                    )

    def test_empty_db_returns_empty(self, tmp_path):
        from agents.persistence import Persistence
        p = Persistence(db_path=tmp_path / "test.db")
        rows = p.get_llm_cost_timeseries()
        assert rows == []

    def test_aggregates_by_sim_day(self, tmp_path):
        from agents.persistence import Persistence
        p = Persistence(db_path=tmp_path / "test.db")
        self._insert_llm_decisions(p, n_per_day=3, n_days=3)
        rows = p.get_llm_cost_timeseries()
        # 3 distinct sim_days
        assert len(rows) == 3
        # Each day should have 3 decisions
        for r in rows:
            assert r["n_decisions"] == 3

    def test_aggregates_llm_vs_fallback(self, tmp_path):
        from agents.persistence import Persistence
        p = Persistence(db_path=tmp_path / "test.db")
        self._insert_llm_decisions(p, n_per_day=4, n_days=2)
        rows = p.get_llm_cost_timeseries()
        # Each day: 2 llm + 2 fallback (alternating)
        for r in rows:
            assert r["llm_n"] == 2
            assert r["fallback_n"] == 2
            assert r["llm_success_rate_pct"] == 50.0

    def test_since_sim_day_filter(self, tmp_path):
        from agents.persistence import Persistence
        p = Persistence(db_path=tmp_path / "test.db")
        self._insert_llm_decisions(p, n_per_day=1, n_days=5)
        rows = p.get_llm_cost_timeseries(since_sim_day=3)
        # Only days 3, 4, 5
        assert len(rows) == 3
        for r in rows:
            assert r["sim_day"] >= 3

    def test_until_sim_day_filter(self, tmp_path):
        from agents.persistence import Persistence
        p = Persistence(db_path=tmp_path / "test.db")
        self._insert_llm_decisions(p, n_per_day=1, n_days=5)
        rows = p.get_llm_cost_timeseries(until_sim_day=2)
        assert len(rows) == 2
        for r in rows:
            assert r["sim_day"] <= 2

    def test_avg_confidence_and_multiplier(self, tmp_path):
        from agents.persistence import Persistence
        p = Persistence(db_path=tmp_path / "test.db")
        with p._conn() as conn:
            # First insert a cycle (FK requirement)
            conn.execute(
                """INSERT INTO optimization_cycles
                   (cycle_id, sim_day, sim_hour, wall_timestamp,
                    activity_factor, n_supply_offers, n_demand_requests,
                    n_matches, total_tons, total_cost_sek, total_co2_kg,
                    total_distance_km, n_vehicles_used, n_vehicles_available,
                    fleet_utilization_pct, solver_status, wall_duration_ms,
                    seasonal_factor_avg, seasonal_month)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    "c1", 1, 12, datetime.now().isoformat(),
                    1.0, 10, 5, 3, 100.0, 500.0, 50.0,
                    20.0, 2, 5, 40.0, "optimal", 100,
                    1.0, 1,
                ),
            )
            conn.execute(
                """INSERT INTO llm_decisions
                   (cycle_id, sim_day, sim_hour, decision_type,
                    target_id, target_type, multiplier, trend, confidence,
                    reason, source, raw_json, wall_timestamp)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    "c1", 1, 12, "demand_prediction", "DEM001",
                    "demand_point", 1.5, "stable", 0.9,
                    "test", "llm", "{}", datetime.now().isoformat(),
                ),
            )
        rows = p.get_llm_cost_timeseries()
        assert len(rows) == 1
        assert rows[0]["avg_multiplier"] == 1.5
        assert rows[0]["avg_confidence"] == 0.9


# ============================================
# API endpoint tests
# ============================================

@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from web.backend import main as backend_main
    with TestClient(backend_main.app) as c:
        yield c


class TestLlmCostTimeseriesEndpoint:
    """/api/persistence/llm-cost-timeseries"""

    def test_endpoint_returns_200(self, client):
        resp = client.get("/api/persistence/llm-cost-timeseries")
        assert resp.status_code == 200

    def test_response_structure(self, client):
        resp = client.get("/api/persistence/llm-cost-timeseries")
        data = resp.json()
        assert "since_sim_day" in data
        assert "until_sim_day" in data
        assert "rows" in data
        assert isinstance(data["rows"], list)

    def test_since_sim_day_query_param(self, client):
        resp = client.get("/api/persistence/llm-cost-timeseries?since_sim_day=0")
        assert resp.status_code == 200
        data = resp.json()
        assert data["since_sim_day"] == 0

    def test_until_sim_day_query_param(self, client):
        resp = client.get("/api/persistence/llm-cost-timeseries?until_sim_day=10")
        assert resp.status_code == 200
        data = resp.json()
        assert data["until_sim_day"] == 10

    def test_negative_since_returns_400(self, client):
        resp = client.get("/api/persistence/llm-cost-timeseries?since_sim_day=-1")
        assert resp.status_code == 400

    def test_negative_until_returns_400(self, client):
        resp = client.get("/api/persistence/llm-cost-timeseries?until_sim_day=-1")
        assert resp.status_code == 400

    def test_since_greater_than_until_returns_400(self, client):
        resp = client.get(
            "/api/persistence/llm-cost-timeseries?since_sim_day=10&until_sim_day=5"
        )
        assert resp.status_code == 400
        assert "since_sim_day" in client.get(
            "/api/persistence/llm-cost-timeseries?since_sim_day=10&until_sim_day=5"
        ).json()["detail"]

    def test_filter_validation_in_response(self, client):
        """Passing valid range works."""
        resp = client.get(
            "/api/persistence/llm-cost-timeseries?since_sim_day=0&until_sim_day=100"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["since_sim_day"] == 0
        assert data["until_sim_day"] == 100
