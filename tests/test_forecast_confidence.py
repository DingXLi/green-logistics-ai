"""
Tests for /api/persistence/forecast-confidence (iter #30).
"""

import pytest


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from web.backend import main as backend_main
    with TestClient(backend_main.app) as c:
        yield c


class TestForecastConfidenceEndpoint:
    """/api/persistence/forecast-confidence ensemble endpoint。"""

    def test_endpoint_returns_200(self, client):
        resp = client.get("/api/persistence/forecast-confidence?horizon=3")
        assert resp.status_code == 200

    def test_default_methods(self, client):
        data = client.get("/api/persistence/forecast-confidence?horizon=3").json()
        assert set(data["methods"]) == {
            "linear", "moving_average", "exponential_smoothing"
        }

    def test_custom_methods_subset(self, client):
        data = client.get(
            "/api/persistence/forecast-confidence?horizon=3&methods=linear,moving_average"
        ).json()
        assert set(data["methods"]) == {"linear", "moving_average"}

    def test_invalid_method_400(self, client):
        resp = client.get(
            "/api/persistence/forecast-confidence?horizon=3&methods=invalid"
        )
        assert resp.status_code == 400

    def test_invalid_horizon_400(self, client):
        resp = client.get("/api/persistence/forecast-confidence?horizon=0")
        assert resp.status_code == 400

    def test_invalid_history_400(self, client):
        resp = client.get("/api/persistence/forecast-confidence?history_n=1")
        assert resp.status_code == 400

    def test_metrics_filter(self, client):
        data = client.get(
            "/api/persistence/forecast-confidence?horizon=3&metrics=cost_sek"
        ).json()
        assert set(data["confidence"].keys()) == {"cost_sek"}

    def test_confidence_structure(self, client):
        data = client.get(
            "/api/persistence/forecast-confidence?horizon=3&metrics=cost_sek"
        ).json()
        item = data["confidence"]["cost_sek"]
        assert "history" in item
        assert "forecast" in item
        assert "per_method_quality" in item
        assert "best_method" in item
        assert "n_methods" in item

    def test_ensemble_point_fields(self, client):
        data = client.get(
            "/api/persistence/forecast-confidence?horizon=3&metrics=cost_sek"
        ).json()
        points = data["confidence"]["cost_sek"]["forecast"]
        assert len(points) == 3
        for point in points:
            for field in ["sim_day", "mean", "stddev", "lower_95", "upper_95",
                          "dispersion_pct", "n_methods"]:
                assert field in point
            assert point["lower_95"] <= point["mean"] <= point["upper_95"]

    def test_per_method_quality_fields(self, client):
        data = client.get(
            "/api/persistence/forecast-confidence?horizon=3&metrics=cost_sek"
        ).json()
        quality = data["confidence"]["cost_sek"]["per_method_quality"]
        assert set(quality) == {"linear", "moving_average", "exponential_smoothing"}
        for method, scores in quality.items():
            assert "r_squared" in scores
            assert "residual_std" in scores

    def test_best_method_is_valid(self, client):
        data = client.get(
            "/api/persistence/forecast-confidence?horizon=3&metrics=cost_sek"
        ).json()
        best = data["confidence"]["cost_sek"]["best_method"]
        assert best in {"linear", "moving_average", "exponential_smoothing"}

    def test_moving_average_dispersion_with_linear_only(self, client):
        """Only one method → stddev/dispersion = 0, n_methods=1。"""
        data = client.get(
            "/api/persistence/forecast-confidence?horizon=3&methods=linear&metrics=cost_sek"
        ).json()
        points = data["confidence"]["cost_sek"]["forecast"]
        assert all(p["stddev"] == 0.0 for p in points)
        assert all(p["dispersion_pct"] == 0.0 for p in points)
        assert all(p["n_methods"] == 1 for p in points)

    def test_forecast_sim_days_preserved(self, client):
        data = client.get("/api/persistence/forecast-confidence?horizon=4").json()
        assert len(data["forecast_sim_days"]) == 4
        assert len(data["confidence"]) >= 1
