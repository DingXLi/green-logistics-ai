"""
Tests for /api/persistence/forecast/multi endpoint (iter #28).

新 endpoint: 同时跑多个 forecast method, 返回 comparison structure.
"""

import pytest


# ============================================
# Fixtures
# ============================================

@pytest.fixture
def client_with_cycles():
    from fastapi.testclient import TestClient
    from web.backend import main as backend_main
    from web.backend.main import coordinator as coord
    with TestClient(backend_main.app) as client:
        if coord is not None and coord.persistence is not None:
            summary = coord.persistence.get_summary() or {}
            if summary.get("n_cycles", 0) == 0:
                try:
                    client.post(
                        "/api/optimize",
                        json={"use_real_roads": False, "time_limit_seconds": 1},
                    )
                except Exception:
                    pass
        yield client


# ============================================
# Basic endpoint tests
# ============================================

class TestForecastMultiEndpoint:
    """/api/persistence/forecast/multi 端点。"""

    def test_endpoint_returns_200(self, client_with_cycles):
        resp = client_with_cycles.get("/api/persistence/forecast/multi?horizon=3")
        assert resp.status_code == 200

    def test_default_methods(self, client_with_cycles):
        """不传 methods → default = 3 个 method。"""
        resp = client_with_cycles.get("/api/persistence/forecast/multi?horizon=3")
        data = resp.json()
        assert set(data["methods"]) == {
            "linear", "moving_average", "exponential_smoothing"
        }

    def test_custom_methods_subset(self, client_with_cycles):
        resp = client_with_cycles.get(
            "/api/persistence/forecast/multi?horizon=3&methods=linear,moving_average"
        )
        data = resp.json()
        assert set(data["methods"]) == {"linear", "moving_average"}
        assert "exponential_smoothing" not in data["methods"]

    def test_invalid_method_returns_400(self, client_with_cycles):
        resp = client_with_cycles.get(
            "/api/persistence/forecast/multi?horizon=3&methods=invalid_method"
        )
        assert resp.status_code == 400

    def test_invalid_horizon_returns_400(self, client_with_cycles):
        resp = client_with_cycles.get("/api/persistence/forecast/multi?horizon=0")
        assert resp.status_code == 400

    def test_invalid_history_n_returns_400(self, client_with_cycles):
        resp = client_with_cycles.get("/api/persistence/forecast/multi?history_n=1")
        assert resp.status_code == 400


# ============================================
# Comparison structure tests
# ============================================

class TestComparisonStructure:
    """返回 comparison 结构应该完整。"""

    def test_comparison_has_each_metric(self, client_with_cycles):
        resp = client_with_cycles.get("/api/persistence/forecast/multi?horizon=3&metrics=cost_sek,co2_kg")
        data = resp.json()
        assert "cost_sek" in data["comparison"]
        assert "co2_kg" in data["comparison"]
        assert "util_pct" not in data["comparison"]
        assert "matches" not in data["comparison"]

    def test_comparison_metric_has_all_4_fields(self, client_with_cycles):
        """comparison[metric] 应有 history / forecasts / final_values / change_from_mean_pct。"""
        resp = client_with_cycles.get("/api/persistence/forecast/multi?horizon=3&metrics=cost_sek")
        data = resp.json()
        cost = data["comparison"]["cost_sek"]
        for field in ["history", "forecasts", "final_values", "change_from_mean_pct"]:
            assert field in cost, f"Missing field {field}"

    def test_forecasts_has_each_method(self, client_with_cycles):
        resp = client_with_cycles.get(
            "/api/persistence/forecast/multi?horizon=3&metrics=cost_sek&methods=linear,moving_average"
        )
        data = resp.json()
        cost = data["comparison"]["cost_sek"]
        assert "linear" in cost["forecasts"]
        assert "moving_average" in cost["forecasts"]

    def test_final_values_match_last_forecast(self, client_with_cycles):
        """final_values[method] 应该 = forecasts[method][-1].value。"""
        resp = client_with_cycles.get(
            "/api/persistence/forecast/multi?horizon=5&metrics=cost_sek&methods=linear"
        )
        data = resp.json()
        cost = data["comparison"]["cost_sek"]
        last_forecast = cost["forecasts"]["linear"][-1]["value"]
        assert cost["final_values"]["linear"] == last_forecast

    def test_change_from_mean_pct_is_pct(self, client_with_cycles):
        """change_from_mean_pct[method] 是百分比 (float, 可能正负)。"""
        resp = client_with_cycles.get(
            "/api/persistence/forecast/multi?horizon=3&metrics=cost_sek&methods=linear"
        )
        data = resp.json()
        cost = data["comparison"]["cost_sek"]
        for method, pct in cost["change_from_mean_pct"].items():
            assert isinstance(pct, (int, float))

    def test_history_preserved_in_comparison(self, client_with_cycles):
        """comparison[metric].history 应该 = 该 metric 的历史。"""
        resp = client_with_cycles.get(
            "/api/persistence/forecast/multi?horizon=3&metrics=cost_sek&methods=linear"
        )
        data = resp.json()
        cost = data["comparison"]["cost_sek"]
        assert len(cost["history"]) > 0
        # Each history point should have sim_day, value, is_forecast=false
        for h in cost["history"][:3]:
            assert "sim_day" in h
            assert "value" in h
            assert h["is_forecast"] is False

    def test_forecast_sim_days_array(self, client_with_cycles):
        """forecast_sim_days 应该是 horizon 个连续 sim_day。"""
        resp = client_with_cycles.get("/api/persistence/forecast/multi?horizon=5")
        data = resp.json()
        assert len(data["forecast_sim_days"]) == 5
        # Should be consecutive
        for i in range(1, len(data["forecast_sim_days"])):
            assert data["forecast_sim_days"][i] == data["forecast_sim_days"][i-1] + 1


# ============================================
# Method behavior tests in multi context
# ============================================

class TestMultiMethodBehavior:
    """在 multi endpoint 里, 不同 method 应该产生不同 forecast。"""

    def test_moving_average_forecasts_are_flat(self, client_with_cycles):
        """moving_average 预测应该是平的 (所有 horizon 步 = 同值)。"""
        resp = client_with_cycles.get(
            "/api/persistence/forecast/multi?horizon=5&metrics=cost_sek&methods=moving_average"
        )
        data = resp.json()
        ma_forecasts = data["comparison"]["cost_sek"]["forecasts"]["moving_average"]
        if ma_forecasts:
            first_val = ma_forecasts[0]["value"]
            for f in ma_forecasts:
                assert f["value"] == first_val, "MA should produce flat forecast"

    def test_exponential_smoothing_forecasts_are_flat(self, client_with_cycles):
        """ES 预测也是平的 (final_level constant for all future steps)."""
        resp = client_with_cycles.get(
            "/api/persistence/forecast/multi?horizon=5&metrics=cost_sek&methods=exponential_smoothing"
        )
        data = resp.json()
        es_forecasts = data["comparison"]["cost_sek"]["forecasts"]["exponential_smoothing"]
        if es_forecasts:
            first_val = es_forecasts[0]["value"]
            for f in es_forecasts:
                assert f["value"] == first_val, "ES should produce flat forecast"

    def test_linear_can_vary_across_horizon(self, client_with_cycles):
        """linear 预测可能变化 (如果有 trend)。"""
        resp = client_with_cycles.get(
            "/api/persistence/forecast/multi?horizon=5&metrics=cost_sek&methods=linear"
        )
        data = resp.json()
        lin_forecasts = data["comparison"]["cost_sek"]["forecasts"]["linear"]
        if lin_forecasts:
            # Linear should at least have 5 different sim_days
            sim_days = [f["sim_day"] for f in lin_forecasts]
            assert len(set(sim_days)) == 5  # All unique
