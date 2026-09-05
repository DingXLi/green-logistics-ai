"""
Tests for /api/signals/external endpoint (iter #51).

Verifies the new aggregated Eurostat signals endpoint:
- Returns construction / industrial / business confidence indicators
- Each indicator has multiplier in [0.85, 1.20]
- Composite multipliers are within reasonable range
- Endpoint is graceful under API failures (always returns fallback)
"""

import pytest


class TestExternalSignalsEndpoint:
    """Test /api/signals/external API endpoint."""

    def test_endpoint_returns_full_payload(self):
        from fastapi.testclient import TestClient
        from web.backend.main import app

        client = TestClient(app)
        resp = client.get("/api/signals/external")
        assert resp.status_code == 200, resp.text

        data = resp.json()
        # Top-level keys
        assert "country" in data
        assert data["country"] == "SE"
        assert "fetched_at" in data
        assert "composite_demand_multiplier" in data
        assert "composite_supply_multiplier" in data

    def test_each_indicator_has_required_fields(self):
        from fastapi.testclient import TestClient
        from web.backend.main import app

        client = TestClient(app)
        resp = client.get("/api/signals/external")
        data = resp.json()

        for key in ("construction", "industrial", "business_confidence"):
            indicator = data[key]
            assert "latest_time" in indicator, f"{key} missing latest_time"
            assert "latest_value" in indicator, f"{key} missing latest_value"
            assert "source" in indicator, f"{key} missing source"
            assert "multiplier" in indicator, f"{key} missing multiplier"
            assert indicator["source"] in ("eurostat", "fallback", "cache")

    def test_multipliers_in_valid_range(self):
        from fastapi.testclient import TestClient
        from web.backend.main import app

        client = TestClient(app)
        resp = client.get("/api/signals/external")
        data = resp.json()

        # 每个 multiplier 必须在 [0.85, 1.20] 区间
        for key in ("construction", "industrial", "business_confidence"):
            m = data[key]["multiplier"]
            assert 0.85 <= m <= 1.20, f"{key} multiplier {m} out of range"

        # composite 是两个 multiplier 的乘积, 应该在 [0.7225, 1.44]
        cd = data["composite_demand_multiplier"]
        cs = data["composite_supply_multiplier"]
        assert 0.70 <= cd <= 1.45, f"composite_demand {cd} out of range"
        assert 0.70 <= cs <= 1.45, f"composite_supply {cs} out of range"

    def test_country_query_param(self):
        from fastapi.testclient import TestClient
        from web.backend.main import app

        client = TestClient(app)
        resp = client.get("/api/signals/external?country=SE")
        assert resp.status_code == 200
        assert resp.json()["country"] == "SE"

    def test_use_cache_false_still_works(self):
        # iter #51: 即使跳过 cache 也要能拿到 fallback
        from fastapi.testclient import TestClient
        from web.backend.main import app

        client = TestClient(app)
        resp = client.get("/api/signals/external?use_cache=false")
        assert resp.status_code == 200
        data = resp.json()
        # 数据源 可能是 eurostat 或 fallback (API 不可达时)
        assert data["construction"]["source"] in ("eurostat", "fallback", "cache")

    def test_business_confidence_present_with_balance_value(self):
        """iter #51: business confidence 在 -30 到 +30 之间."""
        from fastapi.testclient import TestClient
        from web.backend.main import app

        client = TestClient(app)
        resp = client.get("/api/signals/external")
        data = resp.json()

        bc = data["business_confidence"]
        # balance 范围 [-30, +30]
        assert -30 <= bc["latest_value"] <= 30, \
            f"business confidence {bc['latest_value']} outside balance range"