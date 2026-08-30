"""
LLM token tracking tests (iter #22).

测试覆盖:
- LLMTracker.record() — usage, error, cost estimation
- by_caller / by_model 分组
- get_stats() 聚合
- get_recent() 时间倒序
- reset()
- 线程安全 (basic test)
- call_gemini 集成 (用 monkeypatch mock genai)
- /api/admin/llm-stats endpoint (FastAPI TestClient)
- /api/admin/llm-stats/reset endpoint
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from agents.llm_tracker import (
    LLMCallRecord,
    LLMTracker,
    get_llm_tracker,
    COST_PER_1M_PROMPT_TOKENS_USD,
    COST_PER_1M_CANDIDATE_TOKENS_USD,
)


# ============================================================
# LLMTracker unit tests
# ============================================================

class TestLLMTrackerRecord:
    def test_record_success_basic(self):
        """基础成功调用 → tokens + cost 累加。"""
        t = LLMTracker()
        rec = t.record(
            caller="test.caller",
            model="gemini-2.5-flash",
            usage={"prompt_token_count": 100, "candidates_token_count": 50, "total_token_count": 150},
            duration_ms=200,
            success=True,
        )
        assert rec.success is True
        assert rec.prompt_tokens == 100
        assert rec.candidate_tokens == 50
        assert rec.total_tokens == 150
        assert rec.cost_usd > 0
        # cost = (100/1M * 0.075) + (50/1M * 0.30) = 7.5e-06 + 1.5e-05 = 2.25e-05 USD
        assert abs(rec.cost_usd - 2.25e-05) < 1e-9

    def test_record_no_usage(self):
        """usage=None (call failed or metadata 缺失) → tokens=0, cost=0。"""
        t = LLMTracker()
        rec = t.record(
            caller="test.caller",
            model="gemini-2.5-flash",
            usage=None,
            duration_ms=10,
            success=False,
            error_type="rate_limit",
        )
        assert rec.success is False
        assert rec.prompt_tokens == 0
        assert rec.cost_usd == 0.0
        assert rec.error_type == "rate_limit"

    def test_record_partial_usage(self):
        """partial usage (e.g., prompt 但无 candidate) → 不 crash。"""
        t = LLMTracker()
        rec = t.record(
            caller="x",
            model="gemini-2.5-flash",
            usage={"prompt_token_count": 100},  # candidates missing
            duration_ms=10,
            success=True,
        )
        assert rec.prompt_tokens == 100
        assert rec.candidate_tokens == 0
        assert rec.total_tokens == 100  # fallback to prompt+candidate = 100

    def test_record_error_no_cost(self):
        """Failed call → cost=0 even with usage。"""
        t = LLMTracker()
        rec = t.record(
            caller="x",
            model="gemini-2.5-flash",
            usage={"prompt_token_count": 1000, "candidates_token_count": 500, "total_token_count": 1500},
            duration_ms=100,
            success=False,
            error_type="server_error",
        )
        assert rec.cost_usd == 0.0
        assert rec.total_tokens == 1500  # tokens 仍然记录

    def test_cost_gemini_2_0_uses_different_pricing(self):
        """Gemini 2.0 用更贵的 pricing tier。"""
        t = LLMTracker()
        rec_25 = t.record(
            caller="x", model="gemini-2.5-flash",
            usage={"prompt_token_count": 1_000_000, "candidates_token_count": 1_000_000, "total_token_count": 2_000_000},
            duration_ms=100, success=True,
        )
        # 2.5-flash: 0.075 + 0.30 = 0.375
        assert abs(rec_25.cost_usd - 0.375) < 1e-6

        t2 = LLMTracker()
        rec_20 = t2.record(
            caller="x", model="gemini-2.0-flash",
            usage={"prompt_token_count": 1_000_000, "candidates_token_count": 1_000_000, "total_token_count": 2_000_000},
            duration_ms=100, success=True,
        )
        # 2.0-flash: 0.10 + 0.40 = 0.50
        assert abs(rec_20.cost_usd - 0.50) < 1e-6


class TestLLMTrackerAggregation:
    def test_total_aggregation(self):
        """多次 call → total_* 累加。"""
        t = LLMTracker()
        for _ in range(5):
            t.record(
                caller="a.b",
                model="gemini-2.5-flash",
                usage={"prompt_token_count": 10, "candidates_token_count": 20, "total_token_count": 30},
                duration_ms=10, success=True,
            )
        s = t.get_stats()
        assert s["total_calls"] == 5
        assert s["total_prompt_tokens"] == 50
        assert s["total_candidate_tokens"] == 100
        assert s["total_tokens"] == 150
        assert s["total_errors"] == 0
        assert s["error_rate_pct"] == 0.0
        assert s["avg_tokens_per_call"] == 30.0

    def test_error_rate(self):
        """混合 success/error → error_rate 正确。"""
        t = LLMTracker()
        for i in range(10):
            t.record(
                caller="x",
                model="gemini-2.5-flash",
                usage={"prompt_token_count": 10, "candidates_token_count": 10, "total_token_count": 20} if i % 3 != 0 else None,
                duration_ms=10,
                success=(i % 3 != 0),
            )
        s = t.get_stats()
        # i % 3 == 0: i=0,3,6,9 → 4 errors
        assert s["total_calls"] == 10
        assert s["total_errors"] == 4
        assert s["error_rate_pct"] == 40.0

    def test_by_caller_split(self):
        """不同 caller 各自聚合。"""
        t = LLMTracker()
        t.record("supply.predict", "gemini-2.5-flash",
                 {"prompt_token_count": 100, "candidates_token_count": 50, "total_token_count": 150},
                 10, True)
        t.record("supply.predict", "gemini-2.5-flash",
                 {"prompt_token_count": 100, "candidates_token_count": 50, "total_token_count": 150},
                 10, True)
        t.record("market.predict", "gemini-2.5-flash",
                 {"prompt_token_count": 200, "candidates_token_count": 100, "total_token_count": 300},
                 10, True)
        s = t.get_stats()
        assert s["by_caller"]["supply.predict"]["calls"] == 2
        assert s["by_caller"]["supply.predict"]["total_tokens"] == 300
        assert s["by_caller"]["market.predict"]["calls"] == 1
        assert s["by_caller"]["market.predict"]["total_tokens"] == 300

    def test_by_model_split(self):
        """不同 model 各自聚合。"""
        t = LLMTracker()
        t.record("x", "gemini-2.5-flash",
                 {"prompt_token_count": 100, "candidates_token_count": 0, "total_token_count": 100},
                 10, True)
        t.record("x", "gemini-2.0-flash",
                 {"prompt_token_count": 200, "candidates_token_count": 0, "total_token_count": 200},
                 10, True)
        s = t.get_stats()
        assert s["by_model"]["gemini-2.5-flash"]["calls"] == 1
        assert s["by_model"]["gemini-2.0-flash"]["calls"] == 1

    def test_buffer_maxlen_eviction(self):
        """超过 maxlen → 旧 record 被 evict。"""
        t = LLMTracker(maxlen=3)
        for i in range(10):
            t.record("x", "gemini-2.5-flash",
                     {"prompt_token_count": i, "candidates_token_count": 0, "total_token_count": i},
                     10, True)
        s = t.get_stats()
        assert s["buffer_size"] == 3
        assert s["buffer_max"] == 3
        # total_calls 不受 maxlen 影响 (counter 永久累加)
        assert s["total_calls"] == 10
        # 但 recent 只返回最后 3 条
        recent = t.get_recent(10)
        assert len(recent) == 3
        # 时间倒序: 最新的 i=9 在前
        assert recent[0]["prompt_tokens"] == 9
        assert recent[-1]["prompt_tokens"] == 7


class TestLLMTrackerRecent:
    def test_recent_chronological_desc(self):
        """get_recent 返回时间倒序。"""
        t = LLMTracker()
        for i in range(5):
            t.record(f"c{i}", "gemini-2.5-flash",
                     {"prompt_token_count": i, "candidates_token_count": 0, "total_token_count": i},
                     10, True)
            time.sleep(0.001)  # ensure timestamp difference
        recent = t.get_recent(3)
        assert len(recent) == 3
        # newest first
        assert recent[0]["caller"] == "c4"
        assert recent[1]["caller"] == "c3"
        assert recent[2]["caller"] == "c2"

    def test_recent_empty(self):
        """空 tracker → empty list。"""
        t = LLMTracker()
        assert t.get_recent(10) == []

    def test_recent_n_smaller_than_buffer(self):
        """N 小于 buffer → 只返 N 条。"""
        t = LLMTracker()
        for i in range(10):
            t.record("x", "gemini-2.5-flash",
                     {"prompt_token_count": 1, "candidates_token_count": 0, "total_token_count": 1},
                     10, True)
        assert len(t.get_recent(3)) == 3


class TestLLMTrackerReset:
    def test_reset_clears_everything(self):
        t = LLMTracker()
        t.record("x", "gemini-2.5-flash",
                 {"prompt_token_count": 100, "candidates_token_count": 50, "total_token_count": 150},
                 10, True)
        t.reset()
        s = t.get_stats()
        assert s["total_calls"] == 0
        assert s["total_tokens"] == 0
        assert s["total_errors"] == 0
        assert s["by_caller"] == {}
        assert s["by_model"] == {}
        assert s["buffer_size"] == 0


class TestLLMTrackerThreadSafety:
    def test_concurrent_record(self):
        """100 threads × 10 records → total_calls = 1000。"""
        t = LLMTracker()
        threads = []

        def worker(n: int):
            for _ in range(n):
                t.record("concurrent", "gemini-2.5-flash",
                         {"prompt_token_count": 1, "candidates_token_count": 1, "total_token_count": 2},
                         1, True)

        for _ in range(100):
            th = threading.Thread(target=worker, args=(10,))
            threads.append(th)
            th.start()
        for th in threads:
            th.join()
        s = t.get_stats()
        assert s["total_calls"] == 1000
        assert s["total_tokens"] == 2000


class TestLLMTrackerSingleton:
    def test_singleton_returns_same_instance(self):
        a = get_llm_tracker()
        b = get_llm_tracker()
        assert a is b

    def test_singleton_after_reset_preserves_state(self):
        """reset 不应该重建 singleton。"""
        a = get_llm_tracker()
        a.record("singleton.test", "gemini-2.5-flash",
                 {"prompt_token_count": 100, "candidates_token_count": 0, "total_token_count": 100},
                 10, True)
        b = get_llm_tracker()
        # same instance, after reset is empty
        a.reset()
        assert b.get_stats()["total_calls"] == 0


# ============================================================
# call_gemini integration test (mocked)
# ============================================================

class TestCallGeminiIntegration:
    def test_call_gemini_records_success(self, monkeypatch):
        """成功调用 call_gemini → tracker 记录 tokens + success。"""
        from agents import llm_tracker as tracker_module
        tracker_module._TRACKER = None  # reset singleton
        tracker = get_llm_tracker()

        # Mock genai response
        mock_resp = MagicMock()
        mock_resp.text = "OK response"
        mock_um = MagicMock()
        mock_um.prompt_token_count = 200
        mock_um.candidates_token_count = 100
        mock_um.total_token_count = 300
        mock_resp.usage_metadata = mock_um

        mock_model = MagicMock()
        mock_model.generate_content.return_value = mock_resp

        # Patch GOOGLE_API_KEY
        monkeypatch.setenv("GOOGLE_API_KEY", "test-key-12345")

        with patch("google.generativeai.GenerativeModel", return_value=mock_model):
            with patch("google.generativeai.configure"):
                from agents.llm_caller import call_gemini
                result = call_gemini("test prompt", caller="integration.test")

        assert result == "OK response"
        s = tracker.get_stats()
        assert s["total_calls"] == 1
        assert s["total_tokens"] == 300
        assert s["by_caller"]["integration.test"]["total_tokens"] == 300
        assert s["total_errors"] == 0

    def test_call_gemini_records_failure(self, monkeypatch):
        """失败调用 → tracker 记录 error。"""
        from agents import llm_tracker as tracker_module
        tracker_module._TRACKER = None
        tracker = get_llm_tracker()

        # Mock genai to raise 429
        mock_model = MagicMock()
        mock_model.generate_content.side_effect = Exception("429 RESOURCE_EXHAUSTED")

        monkeypatch.setenv("GOOGLE_API_KEY", "test-key-12345")

        # Disable retries via direct patch (config is cached)
        monkeypatch.setattr("agents.llm_caller.MAX_RETRIES", 0)
        # Also patch get_llm_config to return 0 retries
        monkeypatch.setattr(
            "agents.llm_caller.get_llm_config",
            lambda: {
                "model": "gemini-2.5-flash", "temperature": 0.5,
                "max_tokens": 1024, "max_retries": 0, "timeout_s": 30,
            },
        )

        with patch("google.generativeai.GenerativeModel", return_value=mock_model):
            with patch("google.generativeai.configure"):
                from agents.llm_caller import call_gemini, GeminiAPIError
                with pytest.raises(GeminiAPIError):
                    call_gemini("test prompt", caller="integration.fail")

        s = tracker.get_stats()
        assert s["total_calls"] == 1
        assert s["total_errors"] == 1
        assert s["by_caller"]["integration.fail"]["errors"] == 1


# ============================================================
# FastAPI endpoint tests
# ============================================================

class TestLLMStatsEndpoint:
    def test_get_llm_stats_empty(self):
        """空 tracker → stats endpoints 仍可访问。"""
        from fastapi.testclient import TestClient
        # Reset singleton
        from agents import llm_tracker as tracker_module
        tracker_module._TRACKER = None

        from web.backend.main import app
        client = TestClient(app)
        resp = client.get("/api/admin/llm-stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_calls"] == 0
        assert "by_caller" in data
        assert "by_model" in data
        assert "recent" in data

    def test_get_llm_stats_with_data(self):
        """tracker 有数据 → stats 反映。"""
        from agents import llm_tracker as tracker_module
        tracker_module._TRACKER = None
        tracker = get_llm_tracker()
        tracker.record("test.endpoint", "gemini-2.5-flash",
                       {"prompt_token_count": 100, "candidates_token_count": 50, "total_token_count": 150},
                       100, True)

        from fastapi.testclient import TestClient
        from web.backend.main import app
        client = TestClient(app)
        resp = client.get("/api/admin/llm-stats?recent=5")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_calls"] == 1
        assert data["total_tokens"] == 150
        assert "test.endpoint" in data["by_caller"]
        assert len(data["recent"]) == 1

    def test_reset_llm_stats(self):
        """reset endpoint → 清空 tracker。"""
        from agents import llm_tracker as tracker_module
        tracker_module._TRACKER = None
        tracker = get_llm_tracker()
        tracker.record("x", "gemini-2.5-flash",
                       {"prompt_token_count": 100, "candidates_token_count": 0, "total_token_count": 100},
                       10, True)

        from fastapi.testclient import TestClient
        from web.backend.main import app
        client = TestClient(app)
        resp = client.post("/api/admin/llm-stats/reset")
        assert resp.status_code == 200
        assert resp.json() == {"reset": True}
        # verify empty
        s2 = client.get("/api/admin/llm-stats").json()
        assert s2["total_calls"] == 0

    def test_recent_param_clamping(self):
        """recent 参数过大/过小 → clamp 到 [0, 500]。"""
        from agents import llm_tracker as tracker_module
        tracker_module._TRACKER = None

        from fastapi.testclient import TestClient
        from web.backend.main import app
        client = TestClient(app)
        resp = client.get("/api/admin/llm-stats?recent=10000")
        assert resp.status_code == 200
        # recent 可空 (因为没有 calls), clamp 不会破坏
        assert "recent" in resp.json()

        resp2 = client.get("/api/admin/llm-stats?recent=-5")
        assert resp2.status_code == 200


class TestLLMCallRecordToDict:
    def test_to_dict_roundtrip(self):
        """LLMCallRecord.to_dict() — 可序列化。"""
        rec = LLMCallRecord(
            timestamp=1234567890.0,
            caller="x", model="gemini-2.5-flash",
            prompt_tokens=10, candidate_tokens=20, total_tokens=30,
            duration_ms=100.0, success=True, cost_usd=0.001,
        )
        d = rec.to_dict()
        assert d["caller"] == "x"
        assert d["total_tokens"] == 30
        assert d["cost_usd"] == 0.001
        # JSON serializable
        import json
        json.dumps(d)
