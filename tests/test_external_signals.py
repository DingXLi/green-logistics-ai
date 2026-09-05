"""
Tests for external economic signals (Eurostat integration).
"""

import pytest

from data.external_signals import (
    construction_demand_multiplier,
    industrial_supply_multiplier,
    business_confidence_multiplier,
    get_construction_index,
    get_industrial_index,
    get_business_confidence,
    CONSTRUCTION_INDEX_FALLBACK,
    INDUSTRIAL_INDEX_FALLBACK,
    BUSINESS_CONFIDENCE_FALLBACK,
)


class TestMultipliers:
    def test_construction_baseline_1_0(self):
        # baseline=110 → multiplier=1.0
        assert construction_demand_multiplier(110.0) == 1.0

    def test_construction_high_above_baseline(self):
        # 130 (18% 高) → ~1.18
        assert construction_demand_multiplier(130.0, baseline=110.0) == pytest.approx(1.18, abs=0.01)

    def test_construction_low_below_baseline(self):
        # 90 (低 18%) → clamp 到下限 0.85
        assert construction_demand_multiplier(90.0, baseline=110.0) == 0.85

    def test_construction_clamped(self):
        # 极端高 / 极端低
        assert construction_demand_multiplier(200.0) == 1.20  # clamp 上限
        assert construction_demand_multiplier(50.0) == 0.85  # clamp 下限

    def test_construction_zero_safe(self):
        assert construction_demand_multiplier(None) == 1.0
        assert construction_demand_multiplier(0) == 1.0

    def test_industrial_baseline(self):
        assert industrial_supply_multiplier(111.0) == 1.0

    def test_industrial_above(self):
        # 120 (高 8%) → ~1.08
        assert industrial_supply_multiplier(120.0, baseline=111.0) == pytest.approx(1.08, abs=0.01)

    def test_industrial_clamped(self):
        assert industrial_supply_multiplier(200.0) == 1.20
        assert industrial_supply_multiplier(50.0) == 0.85

    # ============================================
    # iter #51: business confidence multiplier
    # ============================================

    def test_confidence_neutral_1_0(self):
        # balance=0 (中性) → multiplier=1.0
        assert business_confidence_multiplier(0.0) == 1.0

    def test_confidence_positive_above(self):
        # +5 balance → 1.0 + 5*0.02 = 1.10
        assert business_confidence_multiplier(5.0) == pytest.approx(1.10, abs=0.01)

    def test_confidence_negative_below(self):
        # -8.2 balance (fallback 1月) → 1.0 - 8.2*0.02 = 0.836 → clamp 到 0.85
        assert business_confidence_multiplier(-8.2) == 0.85

    def test_confidence_clamped_high(self):
        # +20 → 1.0 + 15*0.02 = 1.30 → clamp 到 1.20
        assert business_confidence_multiplier(20.0) == 1.20

    def test_confidence_clamped_low(self):
        # -50 → clamp 到 -15 → 1.0 - 15*0.02 = 0.70 → clamp 到 0.85
        assert business_confidence_multiplier(-50.0) == 0.85

    def test_confidence_none_safe(self):
        assert business_confidence_multiplier(None) == 1.0

    def test_confidence_zero_safe(self):
        assert business_confidence_multiplier(0.0) == 1.0


class TestFallbacks:
    def test_construction_fallback_has_data(self):
        # 至少 12 月 (2024)
        assert len(CONSTRUCTION_INDEX_FALLBACK) >= 12
        # 全部在合理范围
        for t, v in CONSTRUCTION_INDEX_FALLBACK.items():
            assert 80 < v < 150  # 2015=100 基础, ±50% 范围

    def test_construction_seasonal_pattern(self):
        # 6月 应当 > 1月 (夏季建筑活跃)
        jun = CONSTRUCTION_INDEX_FALLBACK["2024-06"]
        jan = CONSTRUCTION_INDEX_FALLBACK["2024-01"]
        assert jun > jan

    def test_industrial_fallback_has_data(self):
        assert len(INDUSTRIAL_INDEX_FALLBACK) >= 12
        for t, v in INDUSTRIAL_INDEX_FALLBACK.items():
            assert 80 < v < 150

    def test_business_confidence_fallback_has_data(self):
        # 至少 12 个月 (2024)
        assert len(BUSINESS_CONFIDENCE_FALLBACK) >= 12
        # balance 范围: 信心指数极少超过 [-30, +30]
        for t, v in BUSINESS_CONFIDENCE_FALLBACK.items():
            assert -30 <= v <= 30, f"{t}={v} outside balance range"

    def test_business_confidence_fallback_negative_during_2024(self):
        # 2024 年瑞典建筑业信心实际低迷 — 多数 fallback 值为负
        neg_count = sum(1 for v in BUSINESS_CONFIDENCE_FALLBACK.values() if v < 0)
        assert neg_count >= 10  # 至少 10/12 月份为负


class TestGetIndicators:
    """实时 API 测试,网络异常时 fallback。"""

    def test_get_construction_returns_valid_structure(self):
        c = get_construction_index("SE")
        assert "latest_value" in c
        assert "source" in c
        assert c["source"] in ("eurostat", "fallback", "cache")
        assert c["latest_value"] is not None

    def test_get_industrial_returns_valid_structure(self):
        i = get_industrial_index("SE")
        assert "latest_value" in i
        assert "source" in i
        assert i["source"] in ("eurostat", "fallback", "cache")
        assert i["latest_value"] is not None

    def test_get_business_confidence_returns_valid_structure(self):
        # iter #51: 新增 ei_bcs endpoint
        b = get_business_confidence("SE")
        assert "latest_value" in b
        assert "source" in b
        assert b["source"] in ("eurostat", "fallback", "cache")
        assert b["latest_value"] is not None
        # balance 范围 (relaxed): [-30, +30]
        assert -30 <= b["latest_value"] <= 30
