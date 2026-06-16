"""
Tests for Swedish waste statistics reference data.
"""

import pytest

from data.swedish_waste_stats import (
    SWEDEN_WASTE_BASELINES,
    CITY_DEMAND_PROFILES,
    SEASONAL_FACTORS,
    get_baseline_demand_tons_per_day,
    get_realistic_range,
)


class TestBaselines:
    def test_all_6_materials_present(self):
        expected = {"concrete", "metal_scrap", "wood_waste", "mixed_waste", "plastic", "paper_cardboard"}
        assert set(SWEDEN_WASTE_BASELINES.keys()) == expected

    def test_baselines_have_source(self):
        for mat, info in SWEDEN_WASTE_BASELINES.items():
            assert "source" in info, f"{mat} missing source"
            assert "total_kt_per_year" in info
            assert "per_capita_kg" in info

    def test_baseline_values_sane(self):
        for mat, info in SWEDEN_WASTE_BASELINES.items():
            assert info["total_kt_per_year"] > 0
            # Sanity: Sweden total should be in [100, 50000] kt/yr
            assert 100 < info["total_kt_per_year"] < 50000


class TestCityProfiles:
    def test_3_cities(self):
        assert set(CITY_DEMAND_PROFILES.keys()) == {"Borås", "Göteborg", "Stockholm"}

    def test_population_ordering(self):
        pops = {city: p["population"] for city, p in CITY_DEMAND_PROFILES.items()}
        assert pops["Borås"] < pops["Göteborg"] < pops["Stockholm"]


class TestSeasonal:
    def test_all_6_materials_have_12_months(self):
        for mat, factors in SEASONAL_FACTORS.items():
            assert len(factors) == 12, f"{mat} has {len(factors)} months"
            for m in range(1, 13):
                assert m in factors

    def test_concrete_peaks_summer(self):
        # 混凝土夏季 (5-7月) 应高,冬季 (1, 12月) 应低
        assert SEASONAL_FACTORS["concrete"][6] > SEASONAL_FACTORS["concrete"][1]
        assert SEASONAL_FACTORS["concrete"][7] > SEASONAL_FACTORS["concrete"][12]
        # 至少 2x 差异
        assert SEASONAL_FACTORS["concrete"][6] >= 2 * SEASONAL_FACTORS["concrete"][1]


class TestGetBaseline:
    def test_raises_unknown_material(self):
        with pytest.raises(KeyError):
            get_baseline_demand_tons_per_day("unknown", "Borås", 6)

    def test_raises_unknown_city(self):
        with pytest.raises(KeyError):
            get_baseline_demand_tons_per_day("concrete", "Malmö", 6)

    def test_raises_invalid_month(self):
        with pytest.raises(ValueError):
            get_baseline_demand_tons_per_day("concrete", "Borås", 13)

    def test_borås_concrete_summer(self):
        # Borås 6月混凝土 ~265 t/day (从输出看)
        b = get_baseline_demand_tons_per_day("concrete", "Borås", 6)
        assert 250 < b < 280

    def test_borås_concrete_winter_lower(self):
        # Borås 1月混凝土应明显低 (季节因子 0.4)
        b_summer = get_baseline_demand_tons_per_day("concrete", "Borås", 6)
        b_winter = get_baseline_demand_tons_per_day("concrete", "Borås", 1)
        # 夏季 ≈ 1.4/0.4 = 3.5x 冬季
        assert b_summer > 2.5 * b_winter

    def test_stockholm_larger_than_borås(self):
        # Stockholm > Borås in demand
        s = get_baseline_demand_tons_per_day("mixed_waste", "Stockholm", 6)
        b = get_baseline_demand_tons_per_day("mixed_waste", "Borås", 6)
        assert s > b
        # Stockholm ~13.5x Borås (人口比)
        assert 10 < s / b < 20

    def test_realistic_range(self):
        lo, hi = get_realistic_range("concrete", "Borås", 6)
        base = get_baseline_demand_tons_per_day("concrete", "Borås", 6)
        assert lo < base < hi
        assert (hi - lo) / base < 0.5  # ±20% jitter

    def test_custom_jitter(self):
        lo50, hi50 = get_realistic_range("concrete", "Borås", 6, jitter_pct=0.5)
        lo10, hi10 = get_realistic_range("concrete", "Borås", 6, jitter_pct=0.1)
        # 50% jitter 的 range 应比 10% 大
        assert (hi50 - lo50) > (hi10 - lo10)
