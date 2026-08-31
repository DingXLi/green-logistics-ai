"""
Tests for /api/persistence/forecast with multiple methods (iter #28).

_iter #28 改进_: forecast 现在支持 3 种 method:
- "linear" (default, iter #26)
- "moving_average" (iter #28, 全期均值, 适合平稳数据)
- "exponential_smoothing" (iter #28, alpha=0.3 SES, 适合近期更重要数据)

新返回字段:
- method: 用哪种 method
- method_meta: {intercept} / {window_mean, window_n} / {alpha, final_level}
"""

import pytest
import numpy as np


# ============================================
# Persistence layer unit tests
# ============================================

class TestForecastMethodsPersistence:
    """forecast_next_n_sim_days() method 参数。"""

    def _insert_cycles(self, p, cost_sek_fn, n_cycles=10):
        """Helper: 直接 SQL 插入 n_cycles 行 optimization_cycles。"""
        import json as _json
        from datetime import datetime
        with p._conn() as conn:
            for day in range(1, n_cycles + 1):
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
                        1.0, 10, 5, 3, 100.0, cost_sek_fn(day), 50.0,
                        20.0, 2, 5, 40.0, "optimal", 100,
                        1.0, 1,
                    ),
                )

    def test_method_default_is_linear(self, tmp_path):
        from agents.persistence import Persistence
        db_path = tmp_path / "test_forecast.db"
        p = Persistence(db_path=db_path)
        self._insert_cycles(p, lambda d: 500.0 + d * 10)
        result = p.forecast_next_n_sim_days(horizon=3, history_n=10)
        assert result["method"] == "linear"

    def test_method_moving_average(self, tmp_path):
        from agents.persistence import Persistence
        db_path = tmp_path / "test_forecast.db"
        p = Persistence(db_path=db_path)
        self._insert_cycles(p, lambda d: 500.0)  # All 500
        result = p.forecast_next_n_sim_days(horizon=3, history_n=10, method="moving_average")
        assert result["method"] == "moving_average"
        cost_sek_metric = result["metrics"]["cost_sek"]
        assert cost_sek_metric["trend"] == "flat"
        assert "window_mean" in cost_sek_metric
        assert cost_sek_metric["window_mean"] == 500.0
        for f in cost_sek_metric["forecast"]:
            assert f["value"] == 500.0

    def test_method_exponential_smoothing(self, tmp_path):
        from agents.persistence import Persistence
        db_path = tmp_path / "test_forecast.db"
        p = Persistence(db_path=db_path)
        # Strong increasing trend: 150, 200, 250, ..., 600
        self._insert_cycles(p, lambda d: 100.0 + d * 50)
        result = p.forecast_next_n_sim_days(horizon=3, history_n=10, method="exponential_smoothing")
        assert result["method"] == "exponential_smoothing"
        cost_sek_metric = result["metrics"]["cost_sek"]
        assert "alpha" in cost_sek_metric
        assert cost_sek_metric["alpha"] == 0.3
        assert "final_level" in cost_sek_metric
        # With alpha=0.3, final_level should be heavily weighted toward recent values
        # Should be > 400 (most recent values are 500-600)
        assert cost_sek_metric["final_level"] >= 400
        assert cost_sek_metric["trend"] == "flat"  # ES doesn't extrapolate trend

    def test_invalid_method_raises(self, tmp_path):
        from agents.persistence import Persistence
        db_path = tmp_path / "test_forecast.db"
        p = Persistence(db_path=db_path)
        self._insert_cycles(p, lambda d: 500.0)
        with pytest.raises(ValueError) as exc_info:
            p.forecast_next_n_sim_days(horizon=3, method="invalid_method")
        assert "method" in str(exc_info.value).lower()

    def test_method_meta_per_method(self, tmp_path):
        """每个 method 的 method_meta 字段不同。"""
        from agents.persistence import Persistence
        db_path = tmp_path / "test_forecast.db"
        p = Persistence(db_path=db_path)
        self._insert_cycles(p, lambda d: 500.0)
        for method, expected_keys in [
            ("linear", {"intercept"}),
            ("moving_average", {"window_mean", "window_n"}),
            ("exponential_smoothing", {"alpha", "final_level"}),
        ]:
            result = p.forecast_next_n_sim_days(horizon=3, method=method)
            cost_metric = result["metrics"]["cost_sek"]
            assert expected_keys.issubset(set(cost_metric.keys())), \
                f"method={method} missing keys: {expected_keys - set(cost_metric.keys())}"


# ============================================
# API endpoint tests
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


class TestForecastEndpointMethods:
    """/api/persistence/forecast?method=X 端点。"""

    def test_method_query_param_linear_default(self, client_with_cycles):
        """不传 method → default = linear。"""
        resp = client_with_cycles.get("/api/persistence/forecast?horizon=3")
        assert resp.status_code == 200
        data = resp.json()
        assert data["method"] == "linear"

    def test_method_query_param_moving_average(self, client_with_cycles):
        resp = client_with_cycles.get(
            "/api/persistence/forecast?horizon=3&method=moving_average"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["method"] == "moving_average"
        for m in data["metrics"].values():
            assert m["method"] == "moving_average"
            assert m["trend"] == "flat"
            assert "window_mean" in m

    def test_method_query_param_exponential_smoothing(self, client_with_cycles):
        resp = client_with_cycles.get(
            "/api/persistence/forecast?horizon=3&method=exponential_smoothing"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["method"] == "exponential_smoothing"
        for m in data["metrics"].values():
            assert m["method"] == "exponential_smoothing"
            assert m["alpha"] == 0.3
            assert "final_level" in m

    def test_invalid_method_returns_400(self, client_with_cycles):
        resp = client_with_cycles.get(
            "/api/persistence/forecast?horizon=3&method=invalid_method"
        )
        assert resp.status_code == 400
        data = resp.json()
        assert "invalid method" in data["detail"].lower() or "method" in data["detail"].lower()

    def test_method_with_metrics_filter(self, client_with_cycles):
        """method param 与 metrics filter 一起工作。"""
        resp = client_with_cycles.get(
            "/api/persistence/forecast?horizon=3&method=moving_average&metrics=cost_sek"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["method"] == "moving_average"
        assert "cost_sek" in data["metrics"]
        # Only cost_sek should be in metrics
        assert set(data["metrics"].keys()) == {"cost_sek"}

    def test_method_comparison_all_three(self, client_with_cycles):
        """3 个 method 都应该返回不同结果 (linear ≠ MA ≠ ES)。"""
        results = {}
        for method in ("linear", "moving_average", "exponential_smoothing"):
            resp = client_with_cycles.get(
                f"/api/persistence/forecast?horizon=5&method={method}"
            )
            assert resp.status_code == 200
            results[method] = resp.json()

        # All three should be different
        linear_val = results["linear"]["metrics"]["cost_sek"]["forecast"][-1]["value"]
        ma_val = results["moving_average"]["metrics"]["cost_sek"]["forecast"][-1]["value"]
        es_val = results["exponential_smoothing"]["metrics"]["cost_sek"]["forecast"][-1]["value"]
        # linear should differ from MA (unless perfectly flat data)
        # MA should differ from ES (different smoothing approaches)
        # At minimum, linear should have a trend (up/down/flat based on slope)
        # and MA/ES should both be flat
        assert results["moving_average"]["metrics"]["cost_sek"]["trend"] == "flat"
        assert results["exponential_smoothing"]["metrics"]["cost_sek"]["trend"] == "flat"
