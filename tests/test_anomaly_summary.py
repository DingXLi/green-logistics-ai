"""
iter #66: Anomaly summary aggregation tests.

Tests the ``get_anomaly_summary`` method which wraps
``detect_anomalous_cycles`` with per-metric counts, severity distribution,
and multi-anomaly cycle detection.
"""
from __future__ import annotations

import os
import tempfile

import pytest


# ----- Helpers --------------------------------------------------------


def _insert_cycle(p, cycle_id, sim_day, cost=100.0, co2=50.0, util=50.0,
                  distance=20.0, tons=10.0):
    """Insert a cycle row with KPI values (matches existing schema)."""
    with p._conn() as conn:
        conn.execute(
            """INSERT INTO optimization_cycles
               (cycle_id, sim_day, sim_hour, wall_timestamp,
                activity_factor, n_supply_offers, n_demand_requests, n_matches,
                total_tons, total_cost_sek, total_co2_kg, total_distance_km,
                n_vehicles_used, n_vehicles_available, fleet_utilization_pct,
                solver_status, wall_duration_ms)
               VALUES (?, ?, 0, '2026-09-04T00:00:00',
                       1.0, 1, 1, 1, ?, ?, ?, ?,
                       1, 1, ?, 'OPTIMAL', 100)""",
            (cycle_id, sim_day, tons, cost, co2, distance, util),
        )


def _populate_normal_cycles(p, n_cycles: int):
    """Insert n_cycles rows with slowly-escalating cost (no clear outliers)."""
    for i in range(1, n_cycles + 1):
        cost = 100.0 + i * 5.0
        co2 = 50.0 + i * 0.5
        util = 70.0 + (i % 5)
        distance = 10.0 + (i % 3)
        tons = 5.0 + (i % 4) * 0.5
        _insert_cycle(p, f"OPT{i:04d}", sim_day=i, cost=cost, co2=co2,
                      util=util, distance=distance, tons=tons)


def _populate_with_cost_outlier(p, n_cycles: int):
    """Same as _populate_normal_cycles but last cycle has huge cost+co2 spike."""
    _populate_normal_cycles(p, n_cycles)
    last = f"OPT{n_cycles:04d}"
    with p._conn() as conn:
        conn.execute(
            """UPDATE optimization_cycles
               SET total_cost_sek = ?, total_co2_kg = ?
               WHERE cycle_id = ?""",
            (10000.0, 5000.0, last),
        )


# ----- Persistence-level tests ----------------------------------------


def test_anomaly_summary_empty_db_returns_zeros(persistence):
    """No cycles => zero anomalies, but consistent shape."""
    summary = persistence.get_anomaly_summary()
    assert summary["n_anomalous_cycles"] == 0
    assert summary["n_total_cycles"] == 0
    assert summary["total_anomaly_events"] == 0
    # All metric counts present (consistent shape even with no data)
    assert set(summary["per_metric_counts"].keys()) == {
        "total_cost_sek", "total_co2_kg", "fleet_utilization_pct",
        "total_distance_km", "total_tons",
    }
    assert summary["top_anomalous_metrics"] == []
    assert summary["most_common_metric"] is None
    assert summary["most_common_severity"] is None
    assert summary["insufficient_history"] is True
    assert summary["anomaly_rate_pct"] == 0.0
    assert summary["multi_anomaly_rate_pct"] == 0.0


def test_anomaly_summary_insufficient_history_flag(persistence):
    """Fewer cycles than min_history => insufficient_history=True."""
    _populate_normal_cycles(persistence, n_cycles=3)
    summary = persistence.get_anomaly_summary(min_history=5)
    assert summary["insufficient_history"] is True
    assert summary["n_anomalous_cycles"] == 0


def test_anomaly_summary_normal_data_no_anomalies(persistence):
    """Smooth data with no clear outliers => no anomalies."""
    _populate_normal_cycles(persistence, n_cycles=20)
    summary = persistence.get_anomaly_summary()
    assert summary["n_anomalous_cycles"] == 0
    assert summary["total_anomaly_events"] == 0
    assert summary["top_anomalous_metrics"] == []


def test_anomaly_summary_detects_cost_outlier(persistence):
    """One cost outlier in 20 cycles => 1 anomalous cycle, cost metric flagged."""
    _populate_with_cost_outlier(persistence, n_cycles=20)
    summary = persistence.get_anomaly_summary(z_threshold=2.0)
    assert summary["n_anomalous_cycles"] >= 1
    # Cost should be flagged (huge spike)
    assert summary["per_metric_counts"]["total_cost_sek"] >= 1
    # n_total should reflect actual rows
    assert summary["n_total_cycles"] == 20
    # most_common_metric should be one of the known metrics
    assert summary["most_common_metric"] in summary["per_metric_counts"]
    # Top metrics list excludes zero-count metrics
    for entry in summary["top_anomalous_metrics"]:
        assert entry["count"] > 0
        assert 0.0 <= entry["pct_of_cycles"] <= 100.0


def test_anomaly_summary_multi_metric_anomaly(persistence):
    """Single cycle with multiple metric anomalies => counted in multi_anomaly."""
    _populate_with_cost_outlier(persistence, n_cycles=20)
    summary = persistence.get_anomaly_summary(z_threshold=2.0)
    # Last cycle has both cost AND co2 spikes => multi-anomaly cycle
    assert summary["cycles_with_multiple_anomalies"] >= 1
    assert summary["multi_anomaly_rate_pct"] > 0.0
    # total_anomaly_events should be >= 2 (cost + co2 at minimum)
    assert summary["total_anomaly_events"] >= 2


def test_anomaly_summary_severity_distribution(persistence):
    """Severity distribution sums to total anomaly events."""
    _populate_with_cost_outlier(persistence, n_cycles=30)
    summary = persistence.get_anomaly_summary(z_threshold=2.0)
    sev_sum = (
        summary["per_severity_counts"]["high"]
        + summary["per_severity_counts"]["medium"]
        + summary["per_severity_counts"]["low"]
    )
    assert sev_sum == summary["total_anomaly_events"]


def test_anomaly_summary_z_threshold_filtering(persistence):
    """Higher z_threshold => fewer anomalies (sensitivity control)."""
    _populate_with_cost_outlier(persistence, n_cycles=30)
    low = persistence.get_anomaly_summary(z_threshold=1.5)
    high = persistence.get_anomaly_summary(z_threshold=3.5)
    assert low["n_anomalous_cycles"] >= high["n_anomalous_cycles"]


def test_anomaly_summary_top_metrics_sorted_desc(persistence):
    """top_anomalous_metrics is sorted by count DESC."""
    _populate_with_cost_outlier(persistence, n_cycles=30)
    summary = persistence.get_anomaly_summary(z_threshold=2.0)
    counts = [e["count"] for e in summary["top_anomalous_metrics"]]
    assert counts == sorted(counts, reverse=True)


def test_anomaly_summary_per_metric_pct_bounded(persistence):
    """Per-metric percentages are individually bounded [0, 100]."""
    _populate_with_cost_outlier(persistence, n_cycles=30)
    summary = persistence.get_anomaly_summary(z_threshold=2.0)
    for m, pct in summary["per_metric_pct"].items():
        assert 0.0 <= pct <= 100.0


def test_anomaly_summary_anomaly_rate_pct(persistence):
    """anomaly_rate_pct = n_anomalous / n_total * 100."""
    _populate_with_cost_outlier(persistence, n_cycles=30)
    summary = persistence.get_anomaly_summary(z_threshold=2.0)
    expected = round(
        100 * summary["n_anomalous_cycles"] / summary["n_total_cycles"], 2)
    assert summary["anomaly_rate_pct"] == expected


def test_anomaly_summary_consistent_keys_with_or_without_data(persistence):
    """Shape is consistent regardless of anomaly presence."""
    # Empty
    empty = persistence.get_anomaly_summary()
    # With data
    _populate_with_cost_outlier(persistence, n_cycles=20)
    full = persistence.get_anomaly_summary()

    for key in [
        "n_anomalous_cycles", "n_total_cycles", "anomaly_rate_pct",
        "z_threshold", "min_history", "total_anomaly_events",
        "per_metric_counts", "per_metric_pct", "per_severity_counts",
        "top_anomalous_metrics", "cycles_with_multiple_anomalies",
        "multi_anomaly_rate_pct", "most_common_metric",
        "most_common_severity", "insufficient_history",
    ]:
        assert key in empty, f"missing from empty: {key}"
        assert key in full, f"missing from full: {key}"

    # per_metric_counts / per_metric_pct / per_severity_counts keys
    # always present even when empty
    for m in ["total_cost_sek", "total_co2_kg", "fleet_utilization_pct",
              "total_distance_km", "total_tons"]:
        assert m in empty["per_metric_counts"]
        assert m in full["per_metric_counts"]
        assert m in empty["per_metric_pct"]
        assert m in full["per_metric_pct"]
    for s in ["high", "medium", "low"]:
        assert s in empty["per_severity_counts"]
        assert s in full["per_severity_counts"]


# ----- Endpoint-level tests -------------------------------------------


def test_anomaly_summary_endpoint_returns_200(monkeypatch):
    """Endpoint returns 200 with summary structure."""
    from web.backend import main as backend_main
    from fastapi.testclient import TestClient

    class _FakePersistence:
        def get_anomaly_summary(self, **kwargs):
            return {
                "n_anomalous_cycles": 0,
                "n_total_cycles": 0,
                "anomaly_rate_pct": 0.0,
                "z_threshold": 2.0,
                "min_history": 5,
                "total_anomaly_events": 0,
                "per_metric_counts": {
                    "total_cost_sek": 0, "total_co2_kg": 0,
                    "fleet_utilization_pct": 0,
                    "total_distance_km": 0, "total_tons": 0,
                },
                "per_metric_pct": {
                    "total_cost_sek": 0.0, "total_co2_kg": 0.0,
                    "fleet_utilization_pct": 0.0,
                    "total_distance_km": 0.0, "total_tons": 0.0,
                },
                "per_severity_counts": {"high": 0, "medium": 0, "low": 0},
                "top_anomalous_metrics": [],
                "cycles_with_multiple_anomalies": 0,
                "multi_anomaly_rate_pct": 0.0,
                "most_common_metric": None,
                "most_common_severity": None,
                "insufficient_history": True,
            }

    class _FakeCoord:
        persistence = _FakePersistence()

    monkeypatch.setattr(backend_main, "coordinator", _FakeCoord())
    client = TestClient(backend_main.app)
    resp = client.get("/api/persistence/anomaly-summary")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    # Required keys
    for key in [
        "n_anomalous_cycles", "n_total_cycles", "anomaly_rate_pct",
        "z_threshold", "min_history", "total_anomaly_events",
        "per_metric_counts", "per_metric_pct", "per_severity_counts",
        "top_anomalous_metrics", "cycles_with_multiple_anomalies",
        "multi_anomaly_rate_pct", "most_common_metric",
        "most_common_severity", "insufficient_history",
    ]:
        assert key in data, f"missing key: {key}"


def test_anomaly_summary_endpoint_with_params(monkeypatch):
    """Endpoint accepts z_threshold and min_history query params."""
    from web.backend import main as backend_main
    from fastapi.testclient import TestClient

    received_kwargs: dict = {}

    class _FakePersistence:
        def get_anomaly_summary(self, **kwargs):
            received_kwargs.update(kwargs)
            return {
                "n_anomalous_cycles": 0,
                "n_total_cycles": 0,
                "anomaly_rate_pct": 0.0,
                "z_threshold": kwargs.get("z_threshold", 2.0),
                "min_history": kwargs.get("min_history", 5),
                "total_anomaly_events": 0,
                "per_metric_counts": {},
                "per_metric_pct": {},
                "per_severity_counts": {"high": 0, "medium": 0, "low": 0},
                "top_anomalous_metrics": [],
                "cycles_with_multiple_anomalies": 0,
                "multi_anomaly_rate_pct": 0.0,
                "most_common_metric": None,
                "most_common_severity": None,
                "insufficient_history": True,
            }

    class _FakeCoord:
        persistence = _FakePersistence()

    monkeypatch.setattr(backend_main, "coordinator", _FakeCoord())
    client = TestClient(backend_main.app)
    resp = client.get(
        "/api/persistence/anomaly-summary?z_threshold=2.5&min_history=3"
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["z_threshold"] == 2.5
    assert data["min_history"] == 3
    assert received_kwargs["z_threshold"] == 2.5
    assert received_kwargs["min_history"] == 3


def test_anomaly_summary_endpoint_503_when_no_coordinator(monkeypatch):
    """Endpoint returns 503 when coordinator is not initialized."""
    from web.backend import main as backend_main
    from fastapi.testclient import TestClient

    monkeypatch.setattr(backend_main, "coordinator", None)
    client = TestClient(backend_main.app)
    resp = client.get("/api/persistence/anomaly-summary")
    assert resp.status_code == 503


# ----- Fixtures --------------------------------------------------------


@pytest.fixture
def persistence():
    """Fresh Persistence for each test."""
    from agents.persistence import Persistence
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_anom_summary.db")
        p = Persistence(db_path)
        yield p
