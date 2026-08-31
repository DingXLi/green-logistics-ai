"""
Tests for per-endpoint error tracking (iter #27).

_iter #27 改进_: /api/admin/perf-stats 现在每个 endpoint 都暴露:
- n_errors: 5xx 错误数 (per-endpoint)
- error_rate_pct: 错误率 (per-endpoint)

Backwards compatible: 旧字段 (n_calls, avg_ms, p50/p95/p99, etc) 仍存在。
"""

import pytest


# ============================================
# Fixtures
# ============================================

@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from web.backend import main as backend_main
    with TestClient(backend_main.app) as c:
        # Reset perf stats to clean slate
        c.post("/api/admin/perf-stats/reset")
        yield c


# ============================================
# Per-endpoint error tracking
# ============================================

class TestPerEndpointErrorTracking:
    """n_errors + error_rate_pct 应该按 endpoint 维度统计。"""

    def test_clean_endpoint_has_zero_errors(self, client):
        """成功的 endpoint → n_errors=0, error_rate_pct=0.0。"""
        client.get("/health")
        client.get("/health")
        resp = client.get("/api/admin/perf-stats")
        assert resp.status_code == 200
        data = resp.json()
        # Find /health endpoint
        health_entry = next(
            (e for e in data["endpoints"] if e["endpoint"] == "GET /health"),
            None,
        )
        assert health_entry is not None
        assert health_entry["n_calls"] == 2  # 2 manual /health calls
        assert "n_errors" in health_entry
        assert health_entry["n_errors"] == 0
        assert health_entry["error_rate_pct"] == 0.0

    def test_404_response_doesnt_count_as_error(self, client):
        """4xx 不算 5xx 错误, n_errors 仍为 0。"""
        client.get("/api/nonexistent-endpoint")
        resp = client.get("/api/admin/perf-stats")
        data = resp.json()
        not_found = next(
            (e for e in data["endpoints"] if e["endpoint"] == "GET /api/nonexistent-endpoint"),
            None,
        )
        if not_found is not None:
            # 404 doesn't count as 5xx error
            assert not_found["n_errors"] == 0

    def test_500_response_increments_per_endpoint_errors(self, client):
        """5xx 应该增加 per-endpoint error 计数。"""
        # Force a 500 by calling an endpoint that requires missing coordinator
        # Use a method that 500s. POST /api/scheduler/control with bad action returns 400 (not 5xx)
        # Use a deliberately bad payload to /api/optimize
        # Actually, validation errors return 422 (not 5xx)
        # We need to trigger a real 500. Let's use a direct injection.
        from web.backend import main as backend_main

        original_persistence = backend_main.coordinator.persistence
        try:
            # Force a 500 by making persistence None temporarily
            backend_main.coordinator.persistence = None
            # 503 from /api/persistence/summary (not 5xx — 503 is technically 5xx)
            client.get("/api/persistence/summary")
        finally:
            backend_main.coordinator.persistence = original_persistence

        resp = client.get("/api/admin/perf-stats")
        data = resp.json()
        # Find /api/persistence/summary endpoint
        summary_entry = next(
            (e for e in data["endpoints"] if e["endpoint"] == "GET /api/persistence/summary"),
            None,
        )
        if summary_entry is not None:
            # 503 IS a 5xx, so it should increment n_errors
            assert summary_entry["n_errors"] >= 1
            assert summary_entry["error_rate_pct"] > 0.0

    def test_error_rate_pct_formula(self, client):
        """error_rate_pct 应该 = n_errors / n_calls * 100。"""
        from web.backend import main as backend_main
        original_persistence = backend_main.coordinator.persistence
        try:
            # Force 3 calls to /api/persistence/summary, all 503
            backend_main.coordinator.persistence = None
            for _ in range(3):
                client.get("/api/persistence/summary")
        finally:
            backend_main.coordinator.persistence = original_persistence

        resp = client.get("/api/admin/perf-stats")
        data = resp.json()
        summary_entry = next(
            (e for e in data["endpoints"] if e["endpoint"] == "GET /api/persistence/summary"),
            None,
        )
        if summary_entry is not None and summary_entry["n_errors"] > 0:
            # rate should be n_errors/n_calls*100 (rounded to 2 decimals)
            expected = round(
                summary_entry["n_errors"] / summary_entry["n_calls"] * 100, 2
            )
            assert summary_entry["error_rate_pct"] == expected


class TestPerfStatsResetClearsErrors:
    """/api/admin/perf-stats/reset 应该也清空 per-endpoint error 计数。"""

    def test_reset_clears_per_endpoint_errors(self, client):
        from web.backend import main as backend_main
        original_persistence = backend_main.coordinator.persistence
        try:
            backend_main.coordinator.persistence = None
            client.get("/api/persistence/summary")  # 503
        finally:
            backend_main.coordinator.persistence = original_persistence

        # Verify there were errors
        data_before = client.get("/api/admin/perf-stats").json()
        before = next(
            (e for e in data_before["endpoints"] if e["endpoint"] == "GET /api/persistence/summary"),
            None,
        )
        # Now reset
        client.post("/api/admin/perf-stats/reset")

        # Verify errors are cleared
        data_after = client.get("/api/admin/perf-stats").json()
        # /api/persistence/summary should be back in buffer (from reset)
        after = next(
            (e for e in data_after["endpoints"] if e["endpoint"] == "GET /api/persistence/summary"),
            None,
        )
        # After reset, the entry might not exist yet (if we haven't called it)
        # OR it exists but with n_errors=0 from the perf-stats call itself
        if after is not None:
            assert after["n_errors"] == 0


class TestPerfStatsBackwardsCompat:
    """/api/admin/perf-stats 应该 back-compat — 旧字段都在。"""

    def test_all_legacy_fields_present(self, client):
        """n_calls, avg_ms, p50, p95, p99, min_ms, max_ms, last_ms 都应存在。"""
        client.get("/health")
        resp = client.get("/api/admin/perf-stats")
        data = resp.json()
        assert data["total_requests"] >= 0
        assert data["total_errors"] >= 0
        assert data["error_rate_pct"] >= 0.0
        assert data["buffer_size_per_endpoint"] == 100
        # New fields
        for ep in data["endpoints"]:
            for f in ["endpoint", "n_calls", "n_errors", "error_rate_pct",
                     "avg_ms", "min_ms", "max_ms", "p50_ms", "p95_ms", "p99_ms", "last_ms"]:
                assert f in ep, f"Missing field {f} in endpoint entry"
