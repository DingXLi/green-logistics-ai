"""
Tests for /api/persistence/llm-cost-forecast (iter #29).

预测 llm_decisions 聚合 usage 序列:
- n_decisions / llm_n / fallback_n / avg_multiplier / avg_confidence
- 3 methods: linear / moving_average / exponential_smoothing
- filters since_sim_day / until_sim_day
"""

import pytest
from datetime import datetime


# ============================================
# Persistence layer unit tests
# ============================================

class TestLlmCostForecastPersistence:
    """Persistence.forecast_llm_cost()"""

    def _insert_cycles_and_llm(self, p, n_days=10, per_day=3):
        """插入 n_days cycles + per_day llm_decisions。"""
        with p._conn() as conn:
            for day in range(1, n_days + 1):
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
                for n in range(per_day):
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
                            1.0 + n * 0.05, "stable", 0.6 + n * 0.1,
                            "test", source, "{}", datetime.now().isoformat(),
                        ),
                    )

    def test_not_enough_data_returns_note(self, tmp_path):
        from agents.persistence import Persistence
        p = Persistence(db_path=tmp_path / "test.db")
        result = p.forecast_llm_cost()
        assert result["metrics"] == {}
        assert "need at least 2" in result["note"]

    def test_default_linear_forecast(self, tmp_path):
        from agents.persistence import Persistence
        p = Persistence(db_path=tmp_path / "test.db")
        self._insert_cycles_and_llm(p)
        result = p.forecast_llm_cost(horizon=3, history_n=5)
        assert result["method"] == "linear"
        assert len(result["forecast_sim_days"]) == 3
        assert set(result["metrics"]) == {
            "n_decisions", "llm_n", "fallback_n", "avg_multiplier", "avg_confidence"
        }
        assert len(result["metrics"]["n_decisions"]["forecast"]) == 3

    def test_moving_average_forecast(self, tmp_path):
        from agents.persistence import Persistence
        p = Persistence(db_path=tmp_path / "test.db")
        self._insert_cycles_and_llm(p)
        result = p.forecast_llm_cost(horizon=3, method="moving_average")
        n_dec = result["metrics"]["n_decisions"]
        assert n_dec["trend"] == "flat"
        assert all(f["value"] == n_dec["forecast"][0]["value"] for f in n_dec["forecast"])
        assert "window_mean" in n_dec

    def test_exponential_smoothing_forecast(self, tmp_path):
        from agents.persistence import Persistence
        p = Persistence(db_path=tmp_path / "test.db")
        self._insert_cycles_and_llm(p)
        result = p.forecast_llm_cost(horizon=3, method="exponential_smoothing")
        n_dec = result["metrics"]["n_decisions"]
        assert n_dec["alpha"] == 0.3
        assert n_dec["trend"] == "flat"
        assert len(n_dec["forecast"]) == 3

    def test_since_until_filters(self, tmp_path):
        from agents.persistence import Persistence
        p = Persistence(db_path=tmp_path / "test.db")
        self._insert_cycles_and_llm(p, n_days=8, per_day=2)
        result = p.forecast_llm_cost(
            horizon=2, history_n=3, since_sim_day=4, until_sim_day=6
        )
        assert result["last_sim_day"] == 6
        assert all(row["sim_day"] >= 4 for row in result["metrics"]["n_decisions"]["history"])

    def test_invalid_method(self, tmp_path):
        from agents.persistence import Persistence
        p = Persistence(db_path=tmp_path / "test.db")
        self._insert_cycles_and_llm(p)
        with pytest.raises(ValueError):
            p.forecast_llm_cost(method="unknown")

    def test_horizon_validation(self, tmp_path):
        from agents.persistence import Persistence
        p = Persistence(db_path=tmp_path / "test.db")
        with pytest.raises(ValueError):
            p.forecast_llm_cost(horizon=0)
        with pytest.raises(ValueError):
            p.forecast_llm_cost(horizon=31)


# ============================================
# API endpoint tests
# ============================================

@pytest.fixture
def client_with_llm_data():
    from fastapi.testclient import TestClient
    from web.backend import main as backend_main
    with TestClient(backend_main.app) as client:
        yield client


class TestLlmCostForecastEndpoint:
    """/api/persistence/llm-cost-forecast"""

    def test_endpoint_default_200(self, client_with_llm_data):
        resp = client_with_llm_data.get("/api/persistence/llm-cost-forecast")
        assert resp.status_code in (200, 503)

    def test_method_moving_average_200(self, client_with_llm_data):
        resp = client_with_llm_data.get(
            "/api/persistence/llm-cost-forecast?horizon=3&method=moving_average"
        )
        assert resp.status_code in (200, 503)

    def test_method_exponential_smoothing_200(self, client_with_llm_data):
        resp = client_with_llm_data.get(
            "/api/persistence/llm-cost-forecast?horizon=3&method=exponential_smoothing"
        )
        assert resp.status_code in (200, 503)

    def test_invalid_method_400(self, client_with_llm_data):
        resp = client_with_llm_data.get(
            "/api/persistence/llm-cost-forecast?method=unknown"
        )
        assert resp.status_code in (400, 503)

    def test_invalid_horizon_400(self, client_with_llm_data):
        resp = client_with_llm_data.get(
            "/api/persistence/llm-cost-forecast?horizon=0"
        )
        assert resp.status_code in (400, 503)

    def test_invalid_since_400(self, client_with_llm_data):
        resp = client_with_llm_data.get(
            "/api/persistence/llm-cost-forecast?since_sim_day=-1"
        )
        assert resp.status_code in (400, 503)

    def test_invalid_range_400(self, client_with_llm_data):
        resp = client_with_llm_data.get(
            "/api/persistence/llm-cost-forecast?since_sim_day=10&until_sim_day=5"
        )
        assert resp.status_code in (400, 503)

    def test_filter_params_preserved(self, client_with_llm_data):
        resp = client_with_llm_data.get(
            "/api/persistence/llm-cost-forecast?since_sim_day=0&until_sim_day=10"
        )
        assert resp.status_code in (200, 503)
