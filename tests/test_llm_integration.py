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


def _api_error(code: int, status: str, message: str) -> Exception:
    """构造 google-genai 新 SDK 的 APIError(带 code → 走新的 code 优先分类逻辑)。"""
    from google.genai.errors import ClientError, ServerError  # type: ignore
    payload = {"error": {"code": code, "status": status, "message": message}}
    return ClientError(code, payload) if code < 500 else ServerError(code, payload)


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

    def test_thinking_budget_defaults_to_zero(self, monkeypatch):
        """新 SDK 对 Gemini 2.5 默认开思考，思考 token 计入 max_tokens → 默认关掉。"""
        monkeypatch.delenv("GEMINI_THINKING_BUDGET", raising=False)
        reload()
        assert get_llm_config()["thinking_budget"] == 0

    def test_thinking_budget_env_override(self, monkeypatch):
        monkeypatch.setenv("GEMINI_THINKING_BUDGET", "1024")
        reload()
        assert get_llm_config()["thinking_budget"] == 1024
        monkeypatch.delenv("GEMINI_THINKING_BUDGET", raising=False)
        reload()


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
        mock_genai.Client.return_value.models.generate_content.return_value = self._make_fake_response("hi")
        text = call_gemini("hello")
        assert text == "hi"
        # 1 次成功就不应重试
        assert mock_genai.Client.return_value.models.generate_content.call_count == 1

    @patch("agents.llm_caller.genai")
    @patch.dict(os.environ, {"GOOGLE_API_KEY": "AIzaTestFakeKeyForUnitTest_x"})
    def test_retries_on_429_then_succeeds(self, mock_genai):
        # 前两次 429,第三次成功
        side_effects = [
            _api_error(429, "RESOURCE_EXHAUSTED", "quota exceeded"),
            _api_error(429, "RESOURCE_EXHAUSTED", "quota exceeded again"),
            self._make_fake_response("recovered"),
        ]
        mock_genai.Client.return_value.models.generate_content.side_effect = side_effects
        text = call_gemini("hello")
        assert text == "recovered"
        assert mock_genai.Client.return_value.models.generate_content.call_count == 3

    @patch("agents.llm_caller.genai")
    @patch.dict(os.environ, {"GOOGLE_API_KEY": "AIzaTestFakeKeyForUnitTest_x"})
    def test_does_not_retry_on_4xx(self, mock_genai):
        # 400 应该直接抛 GeminiClientError,不重试
        mock_genai.Client.return_value.models.generate_content.side_effect = _api_error(400, "INVALID_ARGUMENT", "bad request")
        with pytest.raises(GeminiClientError):
            call_gemini("hello")
        # 1 次就不重试
        assert mock_genai.Client.return_value.models.generate_content.call_count == 1

    @patch("agents.llm_caller.genai")
    @patch.dict(os.environ, {"GOOGLE_API_KEY": "AIzaTestFakeKeyForUnitTest_x"})
    def test_safe_returns_none_on_failure(self, mock_genai):
        # 5 次 429 — 超过 max_retries+1
        mock_genai.Client.return_value.models.generate_content.side_effect = _api_error(429, "RESOURCE_EXHAUSTED", "quota exceeded")
        text = call_gemini_safe("hello")
        assert text is None

    @patch("agents.llm_caller.genai")
    @patch.dict(os.environ, {})  # no key
    def test_raises_client_error_when_no_key(self, mock_genai):
        with pytest.raises(GeminiClientError, match="GOOGLE_API_KEY"):
            call_gemini("hello")

    @patch("agents.llm_caller.genai")
    @patch.dict(os.environ, {"GOOGLE_API_KEY": "AIzaTestFakeKeyForUnitTest_x"})
    def test_retry_survives_braces_in_error_payload(self, mock_genai):
        """iter #76 回归测试：错误消息里带 dict payload 时，重试路径不能被日志炸掉。

        背景：新 SDK 的错误消息永远带大括号（{'error': {...}}），
        而 loguru 会把消息当 format string 处理 → 以前会抛 KeyError，
        tenacity 的 before_sleep_log 一炸，整个重试逻辑就挂了。
        """
        side_effects = [
            _api_error(429, "RESOURCE_EXHAUSTED", "quota exceeded"),
            self._make_fake_response("recovered"),
        ]
        mock_genai.Client.return_value.models.generate_content.side_effect = side_effects
        text = call_gemini("hello")
        assert text == "recovered"
        assert mock_genai.Client.return_value.models.generate_content.call_count == 2

    @patch("agents.llm_caller.genai")
    @patch.dict(os.environ, {"GOOGLE_API_KEY": "AIzaTestFakeKeyForUnitTest_x"})
    def test_thinking_budget_is_passed_to_sdk(self, mock_genai):
        """思考预算要显式传给 SDK(默认 0 = 关闭思考)。"""
        mock_genai.Client.return_value.models.generate_content.return_value = self._make_fake_response("ok")
        call_gemini("hello")
        cfg = mock_genai.Client.return_value.models.generate_content.call_args.kwargs["config"]
        assert cfg.thinking_config.thinking_budget == 0
        assert cfg.max_output_tokens == 4096

    @patch("agents.llm_caller.genai")
    @patch.dict(os.environ, {"GOOGLE_API_KEY": "AIzaTestFakeKeyForUnitTest_x"})
    def test_max_tokens_raised_when_thinking_budget_too_big(self, mock_genai):
        """开了思考且输出预算 <= 思考预算时,自动抬高输出预算(否则 JSON 会被截断)。"""
        monkey_budget = {"model": "gemini-2.5-flash", "temperature": 0.5,
                         "max_tokens": 100, "max_retries": 0, "timeout_s": 30,
                         "thinking_budget": 2048}
        mock_genai.Client.return_value.models.generate_content.return_value = self._make_fake_response("ok")
        with patch("agents.llm_caller.get_llm_config", lambda: monkey_budget):
            call_gemini("hello")
        cfg = mock_genai.Client.return_value.models.generate_content.call_args.kwargs["config"]
        assert cfg.thinking_config.thinking_budget == 2048
        assert cfg.max_output_tokens > 2048


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
        mock_genai.Client.return_value.models.generate_content.return_value = resp

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
        mock_genai.Client.return_value.models.generate_content.return_value = resp

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
        mock_genai.Client.return_value.models.generate_content.return_value = resp

        result = await market.predict_demand(sim_day=1, weekday=0)
        assert result["source"] == "llm"
        assert len(result["predictions"]) == 1

    @pytest.mark.asyncio
    @patch("agents.llm_caller.genai")
    @patch.dict(os.environ, {"GOOGLE_API_KEY": "AIzaTestFakeKeyForUnitTest_x"})
    async def test_fallback_on_429(self, mock_genai, market):
        mock_genai.Client.return_value.models.generate_content.side_effect = _api_error(429, "RESOURCE_EXHAUSTED", "quota exceeded")

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
        mock_genai.Client.return_value.models.generate_content.return_value = resp

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
        mock_genai.Client.return_value.models.generate_content.return_value = resp

        result = await market.predict_demand(sim_day=1, weekday=0)
        assert result["source"] == "llm"
        # 只有 DEM001 被采纳
        assert len(result["predictions"]) == 1
        assert result["predictions"][0]["id"] == "DEM001"


# ============================================================
# SupplyAgent.predict_supply_batch (LLM 驱动) 测试
# ============================================================

class TestPredictSupplyBatchLLM:
    @pytest.fixture
    def two_agents(self):
        from agents.supply_agent import SupplyAgent
        a1 = SupplyAgent("SUP000", {"lat": 57.7, "lon": 14.2})
        a1.set_inventory(current_stock=5.0, daily_capacity=10.0, material_type="metal_scrap")
        a2 = SupplyAgent("SUP001", {"lat": 59.3, "lon": 18.1})
        a2.set_inventory(current_stock=2.0, daily_capacity=8.0, material_type="concrete")
        return [a1, a2]

    def _import_sa(self):
        from agents.supply_agent import SupplyAgent
        return SupplyAgent

    @pytest.mark.asyncio
    @patch("agents.llm_caller.genai")
    @patch.dict(os.environ, {"GOOGLE_API_KEY": "AIzaTestFakeKeyForUnitTest_x"})
    async def test_returns_per_agent_multiplier(self, mock_genai, two_agents):
        SupplyAgent = self._import_sa()
        llm_json = (
            '[{"id":"SUP000","multiplier":1.2,"trend":"rising","confidence":0.8,"reason":"Construction peak"},'
            '{"id":"SUP001","multiplier":0.6,"trend":"falling","confidence":0.7,"reason":"Plant holiday"}]'
        )
        resp = MagicMock()
        resp.text = llm_json
        mock_genai.Client.return_value.models.generate_content.return_value = resp

        result = await SupplyAgent.predict_supply_batch(two_agents, days=1, sim_day=2, weekday=1)
        assert set(result.keys()) == {"SUP000", "SUP001"}
        assert result["SUP000"]["multiplier"] == 1.2
        assert result["SUP000"]["source"] == "llm"
        assert result["SUP001"]["trend"] == "falling"
        assert "Plant holiday" in result["SUP001"]["reason"]

    @pytest.mark.asyncio
    @patch("agents.llm_caller.genai")
    @patch.dict(os.environ, {"GOOGLE_API_KEY": "AIzaTestFakeKeyForUnitTest_x"})
    async def test_clamps_extreme_values(self, mock_genai, two_agents):
        SupplyAgent = self._import_sa()
        llm_json = '[{"id":"SUP000","multiplier":10.0,"trend":"rising","confidence":0.5,"reason":"moon"},' \
                    '{"id":"SUP001","multiplier":-1.0,"trend":"falling","confidence":0.5,"reason":"neg"}]'
        resp = MagicMock()
        resp.text = llm_json
        mock_genai.Client.return_value.models.generate_content.return_value = resp

        result = await SupplyAgent.predict_supply_batch(two_agents, sim_day=1, weekday=0)
        # supply clamp [0.1, 2.0]
        assert result["SUP000"]["multiplier"] == 2.0
        assert result["SUP001"]["multiplier"] == 0.1

    @pytest.mark.asyncio
    @patch("agents.llm_caller.genai")
    @patch.dict(os.environ, {"GOOGLE_API_KEY": "AIzaTestFakeKeyForUnitTest_x"})
    async def test_fallback_on_429(self, mock_genai, two_agents):
        SupplyAgent = self._import_sa()
        mock_genai.Client.return_value.models.generate_content.side_effect = _api_error(429, "RESOURCE_EXHAUSTED", "quota exceeded")

        result = await SupplyAgent.predict_supply_batch(two_agents, sim_day=1, weekday=0)
        assert all(p["source"] == "fallback" for p in result.values())
        assert all(p["multiplier"] == 1.0 for p in result.values())

    @pytest.mark.asyncio
    @patch("agents.llm_caller.genai")
    @patch.dict(os.environ, {"GOOGLE_API_KEY": "AIzaTestFakeKeyForUnitTest_x"})
    async def test_fallback_on_malformed_json(self, mock_genai, two_agents):
        SupplyAgent = self._import_sa()
        resp = MagicMock()
        resp.text = "not a json"
        mock_genai.Client.return_value.models.generate_content.return_value = resp

        result = await SupplyAgent.predict_supply_batch(two_agents, sim_day=1, weekday=0)
        assert all(p["source"] == "fallback" for p in result.values())

    @pytest.mark.asyncio
    async def test_empty_agents_returns_empty_dict(self):
        SupplyAgent = self._import_sa()
        result = await SupplyAgent.predict_supply_batch([], sim_day=1, weekday=0)
        assert result == {}


# ============================================================
# Persistence.llm_decisions 测试
# ============================================================

class TestLLMDecisionsPersistence:
    @pytest.fixture
    def p(self, tmp_path):
        from agents.persistence import Persistence
        return Persistence(str(tmp_path / "llm.db"))

    def test_schema_has_llm_decisions_table(self, p):
        with p._conn() as con:
            cur = con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='llm_decisions'"
            )
            assert cur.fetchone() is not None

    def test_record_single_and_get(self, p):
        p.begin_cycle("OPT0001", sim_day=1, sim_hour=0, activity_factor=1.0)
        p.record_llm_decision(
            cycle_id="OPT0001", decision_type="demand_prediction",
            target_id="DEM001", target_type="demand_point",
            multiplier=1.2, trend="rising", confidence=0.8,
            reason="Test", source="llm", sim_day=1, sim_hour=0,
        )
        rows = p.get_llm_decisions(decision_type="demand_prediction")
        assert len(rows) == 1
        assert rows[0]["target_id"] == "DEM001"
        assert rows[0]["multiplier"] == 1.2

    def test_record_batch_and_timeseries(self, p):
        p.begin_cycle("OPT0001", sim_day=1, sim_hour=0, activity_factor=1.0)
        n = p.record_llm_decisions_batch(
            cycle_id="OPT0001",
            decision_type="demand_prediction",
            target_type="demand_point",
            predictions=[
                {"id": "DEM001", "multiplier": 1.1, "trend": "rising", "confidence": 0.8, "reason": "a", "source": "llm"},
                {"id": "DEM002", "multiplier": 0.9, "trend": "stable", "confidence": 0.7, "reason": "b", "source": "llm"},
            ],
            sim_day=1, sim_hour=0,
        )
        assert n == 2
        ts = p.get_llm_timeseries("demand_prediction")
        assert len(ts) == 1
        assert ts[0]["n"] == 2
        assert ts[0]["llm_n"] == 2
        assert ts[0]["fb_n"] == 0

    def test_get_summary_includes_llm(self, p):
        p.begin_cycle("OPT0001", sim_day=1, sim_hour=0, activity_factor=1.0)
        p.record_llm_decisions_batch(
            cycle_id="OPT0001", decision_type="supply_prediction",
            target_type="supply_point",
            predictions=[
                {"id": "SUP000", "multiplier": 1.5, "trend": "rising", "confidence": 0.7, "reason": "x", "source": "llm"},
                {"id": "SUP001", "multiplier": 1.0, "trend": "stable", "confidence": 0.4, "reason": "y", "source": "fallback"},
            ],
            sim_day=1, sim_hour=0,
        )
        summary = p.get_summary()
        assert "llm_decisions" in summary
        assert summary["llm_decisions"]["n_total"] == 2
        assert summary["llm_decisions"]["n_real_llm"] == 1
        assert summary["llm_decisions"]["n_fallback"] == 1
