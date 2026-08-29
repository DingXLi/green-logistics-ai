"""
Tests for performance monitoring middleware (iter #21).

Covers:
- perf_middleware adds X-Perf-Time-Ms header to every response
- /api/admin/perf-stats endpoint aggregates by endpoint
- Top N slowest endpoints
- Reset endpoint
- 503 graceful when no coordinator
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def reset_perf():
    """Reset perf stats before each test."""
    from web.backend import main as backend_main
    backend_main._PERF_BUFFER.clear()
    backend_main._PERF_TOTAL = 0
    backend_main._PERF_ERRORS = 0
    yield
    backend_main._PERF_BUFFER.clear()
    backend_main._PERF_TOTAL = 0
    backend_main._PERF_ERRORS = 0


class TestPerfMiddleware:
    """Test the perf_middleware adds header + tracks timing."""

    def test_response_has_perf_header(self):
        """Every response includes X-Perf-Time-Ms header."""
        from fastapi.testclient import TestClient
        from web.backend.main import app
        client = TestClient(app)
        resp = client.get("/health")
        assert resp.status_code == 200
        assert "X-Perf-Time-Ms" in resp.headers
        # Should be a number with .1 decimal
        ms = float(resp.headers["X-Perf-Time-Ms"])
        assert ms > 0
        assert ms < 5000  # sane range

    def test_endpoint_records_in_buffer(self):
        """Each call to /health records in the buffer."""
        from fastapi.testclient import TestClient
        from web.backend import main as backend_main
        from web.backend.main import app
        client = TestClient(app)
        client.get("/health")
        client.get("/health")
        client.get("/health")
        # Buffer should have entry
        assert "GET /health" in backend_main._PERF_BUFFER
        buf = backend_main._PERF_BUFFER["GET /health"]
        assert len(buf) == 3
        assert all(ms > 0 for ms in buf)


class TestPerfStatsEndpoint:
    """Test /api/admin/perf-stats aggregation."""

    def test_empty_perf_stats(self):
        """No requests → empty stats."""
        from fastapi.testclient import TestClient
        from web.backend.main import app
        client = TestClient(app)
        resp = client.get("/api/admin/perf-stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_requests"] == 0
        assert data["total_errors"] == 0
        assert data["error_rate_pct"] == 0.0
        assert data["endpoints"] == []

    def test_total_requests_counted(self):
        """Each request increments total_requests."""
        from fastapi.testclient import TestClient
        from web.backend.main import app
        client = TestClient(app)
        for _ in range(5):
            client.get("/health")
        resp = client.get("/api/admin/perf-stats")
        data = resp.json()
        # /perf-stats endpoint reads BEFORE its own middleware increment,
        # so it sees only the 5 /health calls
        assert data["total_requests"] == 5

    def test_total_requests_increments_after_perf_stats(self):
        """After perf-stats call, total_requests includes its own increment."""
        from fastapi.testclient import TestClient
        from web.backend import main as backend_main
        from web.backend.main import app
        client = TestClient(app)
        client.get("/health")
        client.get("/api/admin/perf-stats")
        # Now total should be 2 (both calls)
        assert backend_main._PERF_TOTAL == 2

    def test_endpoints_sorted_by_avg_ms(self):
        """Endpoints sorted by avg_ms DESC (slowest first)."""
        from fastapi.testclient import TestClient
        from web.backend import main as backend_main
        from web.backend.main import app
        client = TestClient(app)
        # Make multiple requests to populate stats
        for _ in range(3):
            client.get("/health")
            client.get("/api/admin/db-stats")
        resp = client.get("/api/admin/perf-stats")
        data = resp.json()
        assert len(data["endpoints"]) >= 2
        # Verify sort: avg_ms should be non-increasing
        avgs = [e["avg_ms"] for e in data["endpoints"]]
        assert avgs == sorted(avgs, reverse=True)

    def test_percentiles_calculated(self):
        """p50/p95/p99 are calculated correctly."""
        from fastapi.testclient import TestClient
        from web.backend.main import app
        client = TestClient(app)
        # Generate 20 calls
        for _ in range(20):
            client.get("/health")
        resp = client.get("/api/admin/perf-stats")
        data = resp.json()
        # Find /health endpoint
        health_stats = next(e for e in data["endpoints"] if e["endpoint"] == "GET /health")
        assert health_stats["n_calls"] == 20
        assert health_stats["p50_ms"] > 0
        assert health_stats["p95_ms"] > 0
        assert health_stats["p99_ms"] > 0
        # p95 should be >= p50 (monotonic)
        assert health_stats["p95_ms"] >= health_stats["p50_ms"]

    def test_top_n_param(self):
        """?top=N limits the number of endpoints returned."""
        from fastapi.testclient import TestClient
        from web.backend.main import app
        client = TestClient(app)
        # Hit multiple endpoints
        client.get("/health")
        client.get("/api/admin/db-stats")
        client.get("/api/admin/db-info")
        resp = client.get("/api/admin/perf-stats?top=2")
        data = resp.json()
        assert len(data["endpoints"]) <= 2

    def test_reset_endpoint(self):
        """POST /api/admin/perf-stats/reset clears buffer."""
        from fastapi.testclient import TestClient
        from web.backend import main as backend_main
        from web.backend.main import app
        client = TestClient(app)
        client.get("/health")
        client.get("/health")
        assert len(backend_main._PERF_BUFFER) > 0

        # Snapshot BEFORE reset (only /health should be tracked)
        before = dict(backend_main._PERF_BUFFER)
        assert "GET /health" in before

        resp = client.post("/api/admin/perf-stats/reset")
        assert resp.status_code == 200
        assert resp.json()["reset"] is True
        # Old entries cleared (but /reset itself added a new entry after)
        for k in before:
            if k == "POST /api/admin/perf-stats/reset":
                continue
            assert k not in backend_main._PERF_BUFFER

    def test_buffer_caps_at_100(self):
        """Buffer for each endpoint caps at 100 entries."""
        from fastapi.testclient import TestClient
        from web.backend import main as backend_main
        from web.backend.main import app
        client = TestClient(app)
        # Generate 150 requests to /health
        for _ in range(150):
            client.get("/health")
        # Buffer should cap at 100
        assert len(backend_main._PERF_BUFFER["GET /health"]) == 100


class TestPerfStatsIntegration:
    """Integration tests with other endpoints."""

    def test_health_endpoint_appears_in_perf(self):
        """/health is tracked in perf-stats."""
        from fastapi.testclient import TestClient
        from web.backend.main import app
        client = TestClient(app)
        client.get("/health")
        resp = client.get("/api/admin/perf-stats")
        endpoints = [e["endpoint"] for e in resp.json()["endpoints"]]
        assert "GET /health" in endpoints

    def test_multiple_endpoints_tracked(self):
        """Multiple endpoints each get their own entry."""
        from fastapi.testclient import TestClient
        from web.backend.main import app
        client = TestClient(app)
        client.get("/health")
        client.get("/api/admin/db-stats")
        client.get("/api/admin/db-info")
        resp = client.get("/api/admin/perf-stats")
        endpoints = {e["endpoint"] for e in resp.json()["endpoints"]}
        # 3 called endpoints should appear (perf-stats itself isn't tracked yet)
        assert "GET /health" in endpoints
        assert "GET /api/admin/db-stats" in endpoints
        assert "GET /api/admin/db-info" in endpoints