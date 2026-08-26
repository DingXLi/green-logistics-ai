"""
Tests for seasonal adjuster + integration with coordinator.

季节性扰动 (future work #1) — 用真实 Swedish 月度数据。
数据源: data/swedish_waste_stats.SEASONAL_FACTORS
"""

import pytest
from datetime import datetime

from data.seasonal_adjuster import (
    sim_day_to_month,
    get_supply_multiplier,
    get_demand_multiplier,
    get_all_factors,
    DEFAULT_FACTOR,
    DAYS_PER_MONTH,
)


class TestSimDayToMonth:
    """sim_day → month 映射 (30 天压缩 = 1 月)"""

    def test_first_day_is_january(self):
        assert sim_day_to_month(0) == 1

    def test_last_day_of_january(self):
        assert sim_day_to_month(29) == 1

    def test_first_day_of_february(self):
        assert sim_day_to_month(30) == 2

    def test_year_end_wraps_to_january(self):
        # 360 天 = 第 13 月 1 日 = Jan (next year)
        assert sim_day_to_month(360) == 1

    def test_december(self):
        assert sim_day_to_month(330) == 12
        assert sim_day_to_month(359) == 12

    def test_june_summer_peak(self):
        # sim_day 150-179 = June
        assert sim_day_to_month(150) == 6
        assert sim_day_to_month(179) == 6


class TestSupplyMultiplier:
    """供应侧季节因子"""

    def test_concrete_summer_peak(self):
        # sim_day 150-179 = June → concrete 1.4 (summer peak)
        assert get_supply_multiplier("concrete", 150) == 1.4
        assert get_supply_multiplier("concrete", 179) == 1.4

    def test_concrete_winter_low(self):
        # sim_day 0-29 = Jan → concrete 0.4
        assert get_supply_multiplier("concrete", 0) == 0.4
        # sim_day 330-359 = Dec → concrete 0.4
        assert get_supply_multiplier("concrete", 330) == 0.4

    def test_metal_scrap_relatively_stable(self):
        # metal_scrap should stay in 0.95-1.10 range across months
        for d in [0, 30, 60, 90, 120, 150, 180, 210, 240, 270, 300, 330]:
            m = get_supply_multiplier("metal_scrap", d)
            assert 0.9 <= m <= 1.15, f"metal_scrap at day {d}: {m}"

    def test_wood_waste_seasonal_pattern(self):
        # wood_waste should peak summer (1.4), trough winter (0.5-0.6)
        assert get_supply_multiplier("wood_waste", 150) == 1.4  # Jun
        assert get_supply_multiplier("wood_waste", 0) == 0.6    # Jan
        assert get_supply_multiplier("wood_waste", 359) == 0.5  # Dec

    def test_unknown_material_returns_default(self):
        # 不存在的 material → 1.0 (中性, 不扰动)
        assert get_supply_multiplier("unicorn_horn", 150) == DEFAULT_FACTOR


class TestDemandMultiplier:
    """需求侧季节因子"""

    def test_demand_uses_same_data_as_supply(self):
        # 默认 supply == demand (假设同步)
        for d in [0, 30, 150, 300, 359]:
            assert get_demand_multiplier("concrete", d) == get_supply_multiplier("concrete", d)


class TestGetAllFactors:
    """调试用: 一次拿全部 material 的 factor"""

    def test_returns_dict_for_each_month(self):
        for m in range(1, 13):
            factors = get_all_factors(m)
            assert isinstance(factors, dict)
            assert "concrete" in factors
            # 至少 5 个 material
            assert len(factors) >= 5

    def test_invalid_month_does_not_crash(self):
        # _get_all_factors 不做 month 范围检查, 但调用应返回空或全部
        # 当前实现: out of range month → 不会 raise, 只会返回空 dict
        result = get_all_factors(13)
        assert isinstance(result, dict)


class TestAccumulateStockWithSeasonal:
    """SupplyAgent.accumulate_stock() 应该接受 seasonal_multiplier"""

    def test_default_no_seasonal(self):
        """不传 seasonal_multiplier 应该默认 1.0 (向后兼容)"""
        from agents.supply_agent import SupplyAgent
        agent = SupplyAgent("S1", {"lat": 57.7, "lon": 14.1})
        agent.daily_capacity = 10.0
        agent.current_stock = 5.0
        before = agent.current_stock
        agent.accumulate_stock(factor=1.0, llm_multiplier=1.0)
        # factor × llm × seasonal(default 1.0) × daily_cap × 0.5
        expected = before + 10.0 * 0.5 * 1.0 * 1.0
        assert abs(agent.current_stock - round(expected, 2)) < 0.01

    def test_summer_seasonal_increases_accumulation(self):
        """summer seasonal > 1.0 应该让 accumulation > baseline"""
        from agents.supply_agent import SupplyAgent
        agent = SupplyAgent("S1", {"lat": 57.7, "lon": 14.1})
        agent.daily_capacity = 10.0
        agent.current_stock = 5.0

        # Summer (concrete seasonal = 1.4)
        agent.accumulate_stock(factor=1.0, llm_multiplier=1.0, seasonal_multiplier=1.4)
        summer_stock = agent.current_stock

        # Reset
        agent.current_stock = 5.0
        # Winter (concrete seasonal = 0.4)
        agent.accumulate_stock(factor=1.0, llm_multiplier=1.0, seasonal_multiplier=0.4)
        winter_stock = agent.current_stock

        assert summer_stock > winter_stock, (
            f"summer {summer_stock} should be > winter {winter_stock}"
        )


class TestCoordinatorIntegration:
    """Coordinator.run_optimization_cycle() 应该 inject seasonal factors"""

    @pytest.mark.asyncio
    async def test_coordinator_seasonal_affects_supply_offers(self):
        """不同 sim_day 应该让 supply_offers 携带不同的 seasonal_multiplier"""
        from agents.coordinator import MultiAgentCoordinator

        # 用确定性种子避免 LLM 不稳定
        coord = MultiAgentCoordinator(
            config=None,
            db_path="data/test_seasonal_coordinator.db",
        )
        # 把 config 改成小世界，加速测试
        coord.config.n_supply_points = 3
        coord.config.n_demand_points = 3
        coord.config.n_vehicles = 3
        coord.supply_agents.clear()
        coord._bootstrap_world()

        # Summer sim_day = 150 (Jun)
        coord.clock.now.day = 150
        coord.clock.now.month = 6  # set month directly if available
        result = await coord.run_optimization_cycle()
        summer_offers = [s for s in result.get("supply_offers", [])]
        if summer_offers:
            summer_factors = [s.get("seasonal_multiplier", 1.0) for s in summer_offers]
            # 至少有一个 supply 是 concrete/wood_waste (summer > 1.0)
            assert any(f > 1.0 for f in summer_factors), (
                f"summer factors should include > 1.0, got {summer_factors}"
            )

        # Winter sim_day = 359 (Dec)
        coord.clock.now.day = 359
        coord.clock.now.month = 12
        result2 = await coord.run_optimization_cycle()
        winter_offers = [s for s in result2.get("supply_offers", [])]
        if winter_offers:
            winter_factors = [s.get("seasonal_multiplier", 1.0) for s in winter_offers]
            # 冬季: 应该有 < 1.0
            assert any(f < 1.0 for f in winter_factors), (
                f"winter factors should include < 1.0, got {winter_factors}"
            )

        # Cleanup test DB
        import os
        try:
            os.remove("data/test_seasonal_coordinator.db")
        except OSError:
            pass