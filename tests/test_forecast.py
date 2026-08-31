"""
Forecast tests (iter #26).

测试覆盖:
- Persistence.forecast_next_n_sim_days()
  - 基础: < 2 history → empty
  - 线性回归: perfect linear data → 完美拟合 + accurate prediction
  - 4 个 metric (cost_sek / co2_kg / util_pct / matches)
  - trend detection (up / down / flat)
  - R² 拟合优度
  - 95% CI 宽度 (residual_std 影响)
  - horizon / history_n 验证
  - 非法 metric 跳过

- /api/persistence/forecast endpoint
  - 200 OK + correct shape
  - 400 on invalid horizon / history_n / metric
  - 503 if no persistence
"""

from __future__ import annotations

import pytest

from agents.persistence import Persistence


# ============================================================
# Persistence.forecast_next_n_sim_days unit tests
# ============================================================

class TestForecastNextNSimDays:
    """Tests for Persistence.forecast_next_n_sim_days() (iter #26)."""

    def _seed_cycle(self, p: Persistence, day: int, **kpi_kwargs) -> None:
        """Seed a cycle with given KPI for forecasting tests."""
        defaults = {
            "n_supply_offers": 5, "n_demand_requests": 5, "n_matches": 4,
            "total_tons": 40.0, "total_cost_sek": 1000.0, "total_co2_kg": 50.0,
            "total_distance_km": 100.0, "n_vehicles_used": 3,
            "n_vehicles_available": 5, "fleet_utilization_pct": 60.0,
            "solver_status": "feasible",
        }
        defaults.update(kpi_kwargs)
        cid = f"FCST-{day:03d}"
        p.begin_cycle(
            cid, sim_day=day, sim_hour=10, activity_factor=1.0,
            n_supply_offers=defaults["n_supply_offers"],
            n_demand_requests=defaults["n_demand_requests"],
        )
        p.commit_cycle(cid, kpi=defaults, wall_duration_ms=100)

    def test_empty_db_returns_empty_metrics(self, tmp_path):
        """Empty DB → empty metrics dict + note。"""
        p = Persistence(str(tmp_path / "empty.db"))
        result = p.forecast_next_n_sim_days()
        assert result["metrics"] == {}
        assert "note" in result
        assert "need at least 2" in result["note"]

    def test_single_sim_day_returns_empty(self, tmp_path):
        """1 cycle → 不够 (需要 ≥ 2) → empty metrics。"""
        p = Persistence(str(tmp_path / "single.db"))
        self._seed_cycle(p, 1, total_cost_sek=1000)
        result = p.forecast_next_n_sim_days()
        assert result["metrics"] == {}

    def test_perfect_linear_data_accurate_forecast(self, tmp_path):
        """完美线性数据 (cost_sek = 100 * sim_day) → 预测精确。"""
        p = Persistence(str(tmp_path / "linear.db"))
        for day in range(1, 11):
            # cost = 100 * day
            self._seed_cycle(p, day, total_cost_sek=100.0 * day)

        result = p.forecast_next_n_sim_days(horizon=3, metrics=["cost_sek"])
        cost = result["metrics"]["cost_sek"]
        # Expected: day 11 = 1100, day 12 = 1200, day 13 = 1300
        for i, sd in enumerate([11, 12, 13]):
            assert cost["forecast"][i]["sim_day"] == sd
            assert abs(cost["forecast"][i]["value"] - 100.0 * sd) < 0.1
        # R² should be 1.0 (perfect fit)
        assert abs(cost["r_squared"] - 1.0) < 0.001

    def test_increasing_trend_detected(self, tmp_path):
        """上升趋势 → trend='up', slope > 0。"""
        p = Persistence(str(tmp_path / "up.db"))
        for day in range(1, 11):
            self._seed_cycle(p, day, total_cost_sek=100.0 * day)
        result = p.forecast_next_n_sim_days(horizon=5, metrics=["cost_sek"])
        cost = result["metrics"]["cost_sek"]
        assert cost["trend"] == "up"
        assert cost["slope_per_day"] > 0

    def test_decreasing_trend_detected(self, tmp_path):
        """下降趋势 → trend='down', slope < 0。"""
        p = Persistence(str(tmp_path / "down.db"))
        for day in range(1, 11):
            self._seed_cycle(p, day, total_cost_sek=2000.0 - 100.0 * day)
        result = p.forecast_next_n_sim_days(horizon=5, metrics=["cost_sek"])
        cost = result["metrics"]["cost_sek"]
        assert cost["trend"] == "down"
        assert cost["slope_per_day"] < 0

    def test_flat_trend_detected(self, tmp_path):
        """平稳趋势 (stddev 小) → trend='flat'。"""
        p = Persistence(str(tmp_path / "flat.db"))
        for day in range(1, 11):
            # Same cost with tiny noise
            self._seed_cycle(p, day, total_cost_sek=1000.0 + (day % 3))
        result = p.forecast_next_n_sim_days(horizon=5, metrics=["cost_sek"])
        cost = result["metrics"]["cost_sek"]
        # Slope should be near 0 (no clear trend)
        assert abs(cost["slope_per_day"]) < 10
        assert cost["trend"] == "flat"

    def test_all_4_metrics_forecast(self, tmp_path):
        """4 个 metric 都被预测。"""
        p = Persistence(str(tmp_path / "metrics.db"))
        for day in range(1, 6):
            self._seed_cycle(
                p, day,
                total_cost_sek=100 * day,
                total_co2_kg=50 + 5 * day,
                fleet_utilization_pct=50 + day,
                n_matches=4,
            )
        result = p.forecast_next_n_sim_days(horizon=3)
        assert "cost_sek" in result["metrics"]
        assert "co2_kg" in result["metrics"]
        assert "util_pct" in result["metrics"]
        assert "matches" in result["metrics"]

    def test_horizon_validation(self, tmp_path):
        """horizon 范围 1-30。"""
        p = Persistence(str(tmp_path / "h.db"))
        for day in range(1, 5):
            self._seed_cycle(p, day)
        with pytest.raises(ValueError, match="horizon must be 1-30"):
            p.forecast_next_n_sim_days(horizon=0)
        with pytest.raises(ValueError, match="horizon must be 1-30"):
            p.forecast_next_n_sim_days(horizon=31)

    def test_history_n_validation(self, tmp_path):
        """history_n 范围 2-90。"""
        p = Persistence(str(tmp_path / "h.db"))
        for day in range(1, 5):
            self._seed_cycle(p, day)
        with pytest.raises(ValueError, match="history_n must be 2-90"):
            p.forecast_next_n_sim_days(history_n=1)
        with pytest.raises(ValueError, match="history_n must be 2-90"):
            p.forecast_next_n_sim_days(history_n=91)

    def test_invalid_metric_silently_skipped(self, tmp_path):
        """非法 metric → 静默跳过 (only valid in result)。"""
        p = Persistence(str(tmp_path / "m.db"))
        for day in range(1, 5):
            self._seed_cycle(p, day)
        result = p.forecast_next_n_sim_days(metrics=["cost_sek", "invalid_metric"])
        assert "cost_sek" in result["metrics"]
        assert "invalid_metric" not in result["metrics"]

    def test_95_ci_includes_predicted_value(self, tmp_path):
        """CI bounds 应包含 predicted value (lower ≤ value ≤ upper)。"""
        p = Persistence(str(tmp_path / "ci.db"))
        for day in range(1, 11):
            self._seed_cycle(p, day, total_cost_sek=1000 + day * 10)
        result = p.forecast_next_n_sim_days(horizon=5, metrics=["cost_sek"])
        cost = result["metrics"]["cost_sek"]
        for fcst in cost["forecast"]:
            assert fcst["lower_95"] <= fcst["value"] <= fcst["upper_95"]

    def test_95_ci_wider_with_noisy_data(self, tmp_path):
        """更 noisy 数据 → 更大的 CI (residual_std 更大)。"""
        # Clean data
        p_clean = Persistence(str(tmp_path / "clean.db"))
        for day in range(1, 11):
            p_clean.begin_cycle(
                f"C-{day}", sim_day=day, sim_hour=10, activity_factor=1.0,
                n_supply_offers=2, n_demand_requests=2,
            )
            p_clean.commit_cycle(
                f"C-{day}", kpi={
                    "n_supply_offers": 2, "n_demand_requests": 2, "n_matches": 2,
                    "total_tons": 20, "total_cost_sek": 1000 + day * 10,  # perfect linear
                    "total_co2_kg": 50, "total_distance_km": 100, "n_vehicles_used": 2,
                    "n_vehicles_available": 5, "fleet_utilization_pct": 60,
                    "solver_status": "feasible",
                }, wall_duration_ms=0)

        # Noisy data
        p_noisy = Persistence(str(tmp_path / "noisy.db"))
        for day in range(1, 11):
            noise = (day * 137) % 50 - 25  # -25 to 24 noise
            p_noisy.begin_cycle(
                f"N-{day}", sim_day=day, sim_hour=10, activity_factor=1.0,
                n_supply_offers=2, n_demand_requests=2,
            )
            p_noisy.commit_cycle(
                f"N-{day}", kpi={
                    "n_supply_offers": 2, "n_demand_requests": 2, "n_matches": 2,
                    "total_tons": 20, "total_cost_sek": 1000 + day * 10 + noise,
                    "total_co2_kg": 50, "total_distance_km": 100, "n_vehicles_used": 2,
                    "n_vehicles_available": 5, "fleet_utilization_pct": 60,
                    "solver_status": "feasible",
                }, wall_duration_ms=0)

        clean = p_clean.forecast_next_n_sim_days(metrics=["cost_sek"])["metrics"]["cost_sek"]
        noisy = p_noisy.forecast_next_n_sim_days(metrics=["cost_sek"])["metrics"]["cost_sek"]
        # Noisy data should have wider CI (larger residual_std)
        assert noisy["residual_std"] > clean["residual_std"]
        # And lower R²
        assert noisy["r_squared"] < clean["r_squared"]

    def test_forecast_sim_days_continuous(self, tmp_path):
        """forecast_sim_days = [last+1, last+2, ..., last+horizon]。"""
        p = Persistence(str(tmp_path / "sd.db"))
        for day in range(1, 11):
            self._seed_cycle(p, day)
        result = p.forecast_next_n_sim_days(horizon=5)
        # Last sim_day is 10
        assert result["last_sim_day"] == 10
        assert result["forecast_sim_days"] == [11, 12, 13, 14, 15]
        # Forecast length matches horizon
        for metric_data in result["metrics"].values():
            assert len(metric_data["forecast"]) == 5

    def test_history_window_respects_history_n(self, tmp_path):
        """history 输出长度 = min(actual history, history_n)。"""
        p = Persistence(str(tmp_path / "hw.db"))
        for day in range(1, 21):  # 20 days
            self._seed_cycle(p, day)
        result = p.forecast_next_n_sim_days(history_n=5, metrics=["cost_sek"])
        # History length should be 5 (latest 5)
        assert len(result["metrics"]["cost_sek"]["history"]) == 5
        # And starts from day 16 (20 - 5 + 1)
        assert result["metrics"]["cost_sek"]["history"][0]["sim_day"] == 16


# ============================================================
# API endpoint tests
# ============================================================

class TestForecastEndpoint:
    """Tests for /api/persistence/forecast (iter #26)."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        from unittest.mock import MagicMock
        from web.backend import main as backend_main
        from fastapi.testclient import TestClient

        db_path = tmp_path / "api_forecast.db"
        p = Persistence(str(db_path))
        for day in range(1, 11):
            cid = f"API-FCST-{day:03d}"
            p.begin_cycle(
                cid, sim_day=day, sim_hour=10, activity_factor=1.0,
                n_supply_offers=5, n_demand_requests=5,
            )
            p.commit_cycle(cid, kpi={
                "n_supply_offers": 5, "n_demand_requests": 5, "n_matches": 4,
                "total_tons": 40.0 + day, "total_cost_sek": 1000.0 + 50 * day,
                "total_co2_kg": 50.0 + day, "total_distance_km": 100.0,
                "n_vehicles_used": 3, "n_vehicles_available": 5,
                "fleet_utilization_pct": 60.0 + day * 0.5,
                "solver_status": "feasible",
            }, wall_duration_ms=100)

        fake = MagicMock()
        fake.persistence = p
        backend_main.coordinator = fake
        self.client = TestClient(backend_main.app)
        self.persistence = p

    def test_endpoint_default_params(self):
        """默认参数 (horizon=7, history_n=14, all metrics)。"""
        resp = self.client.get("/api/persistence/forecast")
        assert resp.status_code == 200
        data = resp.json()
        assert data["horizon"] == 7
        assert data["history_n"] == 14
        assert data["last_sim_day"] == 10
        assert data["forecast_sim_days"] == [11, 12, 13, 14, 15, 16, 17]
        # 4 default metrics
        assert "cost_sek" in data["metrics"]
        assert "co2_kg" in data["metrics"]
        assert "util_pct" in data["metrics"]
        assert "matches" in data["metrics"]

    def test_endpoint_custom_horizon(self):
        """?horizon=3 → 3 个 forecast points。"""
        resp = self.client.get("/api/persistence/forecast?horizon=3")
        assert resp.status_code == 200
        data = resp.json()
        assert data["horizon"] == 3
        assert len(data["forecast_sim_days"]) == 3
        for metric_data in data["metrics"].values():
            assert len(metric_data["forecast"]) == 3

    def test_endpoint_custom_metrics(self):
        """?metrics=cost_sek,co2_kg → 只返这 2 个。"""
        resp = self.client.get("/api/persistence/forecast?metrics=cost_sek,co2_kg")
        assert resp.status_code == 200
        data = resp.json()
        assert set(data["metrics"].keys()) == {"cost_sek", "co2_kg"}

    def test_endpoint_invalid_horizon_400(self):
        """?horizon=0 或 31 → 400。"""
        resp = self.client.get("/api/persistence/forecast?horizon=0")
        assert resp.status_code == 400
        assert "horizon" in resp.json()["detail"]
        resp = self.client.get("/api/persistence/forecast?horizon=31")
        assert resp.status_code == 400

    def test_endpoint_invalid_history_n_400(self):
        """?history_n=1 或 91 → 400。"""
        resp = self.client.get("/api/persistence/forecast?history_n=1")
        assert resp.status_code == 400
        resp = self.client.get("/api/persistence/forecast?history_n=91")
        assert resp.status_code == 400

    def test_endpoint_invalid_metric_400(self):
        """?metrics=bogus → 400 with valid options。"""
        resp = self.client.get("/api/persistence/forecast?metrics=bogus")
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert "bogus" in detail
        # Should list valid options
        for m in ["cost_sek", "co2_kg", "util_pct", "matches"]:
            assert m in detail

    def test_endpoint_returns_503_if_no_persistence(self):
        """coordinator=None → 503。"""
        from web.backend import main as backend_main
        old_coord = backend_main.coordinator
        backend_main.coordinator = None
        try:
            resp = self.client.get("/api/persistence/forecast")
            assert resp.status_code == 503
        finally:
            backend_main.coordinator = old_coord

    def test_endpoint_response_shape(self):
        """Response 字段 contract 完整。"""
        resp = self.client.get("/api/persistence/forecast?metrics=cost_sek&horizon=2")
        assert resp.status_code == 200
        data = resp.json()
        assert "horizon" in data
        assert "history_n" in data
        assert "last_sim_day" in data
        assert "forecast_sim_days" in data
        assert "metrics" in data
        cost = data["metrics"]["cost_sek"]
        # Required fields per metric
        assert "history" in cost
        assert "forecast" in cost
        assert "trend" in cost
        assert cost["trend"] in ("up", "down", "flat")
        assert "slope_per_day" in cost
        assert "r_squared" in cost
        assert "residual_std" in cost
        assert "mean_value" in cost
        # History points
        for h in cost["history"]:
            assert "sim_day" in h
            assert "value" in h
            assert h["is_forecast"] is False
        # Forecast points
        for f in cost["forecast"]:
            assert "sim_day" in f
            assert "value" in f
            assert f["is_forecast"] is True
            assert "lower_95" in f
            assert "upper_95" in f
