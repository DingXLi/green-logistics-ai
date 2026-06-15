"""
Tests for the centralised LLM config and retry-wrapped caller.

这些测试:
- 不依赖真实 GOOGLE_API_KEY (用 monkeypatch / mock)
- 覆盖优先级 (env > yaml > default)
- 覆盖 retry 行为 (用 mock 抛出 429 / 5xx 验证重试,client 错误不重试)
- 覆盖分类函数 (4xx / 5xx / 429 怎么分)
"""

import os
from unittest.mock import patch, MagicMock

import pytest

from agents.llm_config import (
    get_llm_config, reload, MODEL, MAX_RETRIES,
    DEFAULT_MODEL, DEFAULT_MAX_RETRIES,
)
from agents.llm_caller import (
    call_gemini, call_gemini_safe,
    GeminiAPIError, GeminiRateLimitError, GeminiServerError, GeminiClientError,
    _classify_http_error,
)


# ============================================================
# llm_config 优先级测试
# ============================================================

class TestLLMConfigPriority:
    def test_default_model_when_nothing_set(self, monkeypatch):
        # 清掉 env 和 yaml 里的覆盖,确认 fallback
        monkeypatch.delenv("GEMINI_MODEL", raising=False)
        reload()
        cfg = get_llm_config()
        assert cfg["model"] == DEFAULT_MODEL
        assert cfg["model"] == "gemini-2.5-flash"

    def test_env_overrides_default(self, monkeypatch):
        monkeypatch.setenv("GEMINI_MODEL", "gemini-2.5-flash-lite")
        reload()
        cfg = get_llm_config()
        assert cfg["model"] == "gemini-2.5-flash-lite"

    def test_env_overrides_yaml(self, monkeypatch):
        # yaml 里 ai.model = gemini-2.5-flash
        # env 设为 gemini-2.5-flash-lite
        # env 应该胜
        monkeypatch.setenv("GEMINI_MODEL", "gemini-2.5-flash-lite")
        reload()
        cfg = get_llm_config()
        assert cfg["model"] == "gemini-2.5-flash-lite"

    def test_max_retries_parsing(self, monkeypatch):
        monkeypatch.setenv("GEMINI_MAX_RETRIES", "7")
        reload()
        assert get_llm_config()["max_retries"] == 7

    def test_max_retries_falls_back_to_default_on_garbage(self, monkeypatch):
        monkeypatch.setenv("GEMINI_MAX_RETRIES", "not-a-number")
        reload()
        assert get_llm_config()["max_retries"] == DEFAULT_MAX_RETRIES


# ============================================================
# _classify_http_error 单元测试
# ============================================================

class TestClassifyError:
    def test_429_is_rate_limit(self):
        exc = Exception("429 RESOURCE_EXHAUSTED quota exceeded")
        out = _classify_http_error(exc)
        assert isinstance(out, GeminiRateLimitError)

    def test_quota_keyword_is_rate_limit(self):
        exc = Exception("You exceeded your current quota, please check your plan")
        out = _classify_http_error(exc)
        assert isinstance(out, GeminiRateLimitError)

    def test_500_is_server_error(self):
        for code in ("500", "502", "503", "504"):
            exc = Exception(f"HTTP {code} Internal Server Error")
            out = _classify_http_error(exc)
            assert isinstance(out, GeminiServerError), f"code {code} should be ServerError"

    def test_unavailable_is_server_error(self):
        exc = Exception("UNAVAILABLE: Service temporarily down")
        out = _classify_http_error(exc)
        assert isinstance(out, GeminiServerError)

    def test_other_4xx_is_client_error(self):
        exc = Exception("400 Bad Request: invalid argument")
        out = _classify_http_error(exc)
        assert isinstance(out, GeminiClientError)


# ============================================================
# Retry 行为测试 (用 mock 模拟 API 错误)
# ============================================================

class TestCallGeminiRetry:
    def _make_fake_response(self, text="Hello back"):
        resp = MagicMock()
        resp.text = text
        return resp

    @patch("agents.llm_caller.genai")
    @patch.dict(os.environ, {"GOOGLE_API_KEY": "AIzaTestFakeKeyForUnitTest_x" * 1})  # any non-empty
    def test_succeeds_first_try(self, mock_genai):
        mock_genai.GenerativeModel.return_value.generate_content.return_value = self._make_fake_response("hi")
        text = call_gemini("hello")
        assert text == "hi"
        # 1 次成功就不应重试
        assert mock_genai.GenerativeModel.return_value.generate_content.call_count == 1

    @patch("agents.llm_caller.genai")
    @patch.dict(os.environ, {"GOOGLE_API_KEY": "AIzaTestFakeKeyForUnitTest_x"})
    def test_retries_on_429_then_succeeds(self, mock_genai):
        # 前两次 429,第三次成功
        from google.api_core.exceptions import ResourceExhausted  # type: ignore
        side_effects = [
            ResourceExhausted("429 quota"),
            ResourceExhausted("429 quota again"),
            self._make_fake_response("recovered"),
        ]
        mock_genai.GenerativeModel.return_value.generate_content.side_effect = side_effects
        text = call_gemini("hello")
        assert text == "recovered"
        assert mock_genai.GenerativeModel.return_value.generate_content.call_count == 3

    @patch("agents.llm_caller.genai")
    @patch.dict(os.environ, {"GOOGLE_API_KEY": "AIzaTestFakeKeyForUnitTest_x"})
    def test_does_not_retry_on_4xx(self, mock_genai):
        # 400 应该直接抛 GeminiClientError,不重试
        from google.api_core.exceptions import InvalidArgument  # type: ignore
        mock_genai.GenerativeModel.return_value.generate_content.side_effect = InvalidArgument("400 bad request")
        with pytest.raises(GeminiClientError):
            call_gemini("hello")
        # 1 次就不重试
        assert mock_genai.GenerativeModel.return_value.generate_content.call_count == 1

    @patch("agents.llm_caller.genai")
    @patch.dict(os.environ, {"GOOGLE_API_KEY": "AIzaTestFakeKeyForUnitTest_x"})
    def test_safe_returns_none_on_failure(self, mock_genai):
        from google.api_core.exceptions import ResourceExhausted  # type: ignore
        # 5 次 429 — 超过 max_retries+1
        mock_genai.GenerativeModel.return_value.generate_content.side_effect = ResourceExhausted("429")
        text = call_gemini_safe("hello")
        assert text is None

    @patch("agents.llm_caller.genai")
    @patch.dict(os.environ, {})  # no key
    def test_raises_client_error_when_no_key(self, mock_genai):
        with pytest.raises(GeminiClientError, match="GOOGLE_API_KEY"):
            call_gemini("hello")


# ============================================================
# 集成测试 (需要真实 key, 默认 skip)
# ============================================================

@pytest.mark.skipif(
    not os.environ.get("GOOGLE_API_KEY"),
    reason="GOOGLE_API_KEY not set; real API integration test skipped"
)
class TestRealGeminiIntegration:
    """要真跑:  export GOOGLE_API_KEY=...; pytest tests/test_llm_integration.py -v -k Real"""

    def test_real_call_returns_text(self):
        text = call_gemini("用一句话说 hi", max_tokens=50)
        assert isinstance(text, str)
        assert len(text) > 0


# ============================================================
# MarketAgent.predict_demand (LLM 驱动) 测试
# ============================================================

class TestPredictDemandLLM:
    """用 mock 验证 LLM 驱动 predict_demand 的所有路径。"""

    @pytest.fixture
    def market(self):
        from agents.market_agent import MarketAgent
        m = MarketAgent()
        m.demand_points = [
            {"id": "DEM001", "name": "Borås Plant", "city": "Borås",
             "preferred_materials": ["mixed_waste", "metal_scrap"],
             "base_demand_tons": 50.0, "current_demand_tons": 50.0,
             "daily_capacity_tons": 75.0},
            {"id": "DEM002", "name": "Gbg Harbor", "city": "Göteborg",
             "preferred_materials": ["concrete"],
             "base_demand_tons": 80.0, "current_demand_tons": 80.0,
             "daily_capacity_tons": 120.0},
        ]
        return m

    @pytest.mark.asyncio
    @patch("agents.llm_caller.genai")
    @patch.dict(os.environ, {"GOOGLE_API_KEY": "AIzaTestFakeKeyForUnitTest_x"})
    async def test_llm_path_returns_per_point_multipliers(self, mock_genai, market):
        # 构造 LLM 返回 (像真实 API 的 JSON 数组)
        llm_json = (
            '[{"id":"DEM001","multiplier":1.2,"trend":"rising","confidence":0.8,'
            '"reason":"Construction season peak in Borås"},'
            '{"id":"DEM002","multiplier":0.7,"trend":"falling","confidence":0.6,'
            '"reason":"Plant maintenance scheduled"}]'
        )
        resp = MagicMock()
        resp.text = llm_json
        mock_genai.GenerativeModel.return_value.generate_content.return_value = resp

        result = await market.predict_demand(days=1, sim_day=5, weekday=2)
        assert result["source"] == "llm"
        assert len(result["predictions"]) == 2
        by_id = {p["id"]: p for p in result["predictions"]}
        assert by_id["DEM001"]["multiplier"] == 1.2
        assert by_id["DEM002"]["trend"] == "falling"
        assert "Construction season" in by_id["DEM001"]["reason"]

    @pytest.mark.asyncio
    @patch("agents.llm_caller.genai")
    @patch.dict(os.environ, {"GOOGLE_API_KEY": "AIzaTestFakeKeyForUnitTest_x"})
    async def test_clamps_extreme_multipliers(self, mock_genai, market):
        # LLM 返回超出 [0.3, 1.8] 范围的值 — 应该被 clamp
        llm_json = '[{"id":"DEM001","multiplier":5.0,"trend":"rising","confidence":0.5,"reason":"hype"},' \
                    '{"id":"DEM002","multiplier":0.05,"trend":"falling","confidence":0.5,"reason":"closed"}]'
        resp = MagicMock()
        resp.text = llm_json
        mock_genai.GenerativeModel.return_value.generate_content.return_value = resp

        result = await market.predict_demand(sim_day=1, weekday=0)
        by_id = {p["id"]: p for p in result["predictions"]}
        assert by_id["DEM001"]["multiplier"] == 1.8  # clamped to upper
        assert by_id["DEM002"]["multiplier"] == 0.3  # clamped to lower

    @pytest.mark.asyncio
    @patch("agents.llm_caller.genai")
    @patch.dict(os.environ, {"GOOGLE_API_KEY": "AIzaTestFakeKeyForUnitTest_x"})
    async def test_strips_code_fences(self, mock_genai, market):
        # LLM 把 JSON 包在 ```json ... ``` 里
        llm_text = "```json\n[{\"id\":\"DEM001\",\"multiplier\":1.0,\"trend\":\"stable\",\"confidence\":0.5,\"reason\":\"ok\"}]\n```"
        resp = MagicMock()
        resp.text = llm_text
        mock_genai.GenerativeModel.return_value.generate_content.return_value = resp

        result = await market.predict_demand(sim_day=1, weekday=0)
        assert result["source"] == "llm"
        assert len(result["predictions"]) == 1

    @pytest.mark.asyncio
    @patch("agents.llm_caller.genai")
    @patch.dict(os.environ, {"GOOGLE_API_KEY": "AIzaTestFakeKeyForUnitTest_x"})
    async def test_fallback_on_429(self, mock_genai, market):
        from google.api_core.exceptions import ResourceExhausted  # type: ignore
        mock_genai.GenerativeModel.return_value.generate_content.side_effect = ResourceExhausted("429")

        result = await market.predict_demand(sim_day=1, weekday=5)  # Sat
        assert result["source"] == "fallback"
        # weekend fallback = 0.85
        for p in result["predictions"]:
            assert p["multiplier"] == 0.85
            assert "fallback" in p["reason"].lower()

    @pytest.mark.asyncio
    @patch("agents.llm_caller.genai")
    @patch.dict(os.environ, {"GOOGLE_API_KEY": "AIzaTestFakeKeyForUnitTest_x"})
    async def test_fallback_on_invalid_json(self, mock_genai, market):
        resp = MagicMock()
        resp.text = "sorry, I cannot predict demand"  # not JSON
        mock_genai.GenerativeModel.return_value.generate_content.return_value = resp

        result = await market.predict_demand(sim_day=1, weekday=0)
        assert result["source"] == "fallback"
        assert all(p["multiplier"] == 1.0 for p in result["predictions"])  # weekday non-weekend

    @pytest.mark.asyncio
    @patch("agents.llm_caller.genai")
    @patch.dict(os.environ, {"GOOGLE_API_KEY": "AIzaTestFakeKeyForUnitTest_x"})
    async def test_skips_malformed_entries(self, mock_genai, market):
        # 一条缺 id,一条缺 multiplier — 都该被跳过
        llm_json = '[{"id":"DEM001","multiplier":1.0,"trend":"stable","confidence":0.5,"reason":"ok"},' \
                    '{"name":"oops"},{"id":"DEM002"}]'
        resp = MagicMock()
        resp.text = llm_json
        mock_genai.GenerativeModel.return_value.generate_content.return_value = resp

        result = await market.predict_demand(sim_day=1, weekday=0)
        assert result["source"] == "llm"
        # 只有 DEM001 被采纳
        assert len(result["predictions"]) == 1
        assert result["predictions"][0]["id"] == "DEM001"
