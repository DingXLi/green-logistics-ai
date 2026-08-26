"""
Tests for real_sweden_facilities dataset + WorldBuilder integration.

未来工作 #4 (替换 jittered 合成 → 真实瑞典城市数据) — 用真实设施坐标。
"""

import pytest

from data.real_sweden_facilities import (
    ALL_FACILITIES,
    GOTEBORG_FACILITIES,
    BORAS_FACILITIES,
    STOCKHOLM_FACILITIES,
    get_facilities_by_city,
    get_facilities_by_type,
    get_facility_count,
    FACILITY_TYPE_COUNTS,
)


class TestFacilityDataset:
    """数据集完整性"""

    def test_minimum_10_facilities(self):
        """应该至少有 10 个真实设施覆盖 3 个城市"""
        assert get_facility_count() >= 10

    def test_all_three_cities_represented(self):
        for city in ["Borås", "Göteborg", "Stockholm"]:
            assert any(f["city"] == city for f in ALL_FACILITIES), f"missing {city}"

    def test_all_facilities_have_required_fields(self):
        """每个设施必须有 id/name/city/lat/lon/preferred_materials"""
        required = {"id", "name", "city", "lat", "lon", "preferred_materials"}
        for f in ALL_FACILITIES:
            missing = required - set(f.keys())
            assert not missing, f"{f.get('id', '???')} missing {missing}"

    def test_coordinates_in_sweden(self):
        """lat/lon 应该在瑞典范围内 (大致 55-69°N, 10-24°E)"""
        for f in ALL_FACILITIES:
            assert 55.0 <= f["lat"] <= 69.5, f"{f['id']}: lat {f['lat']} out of Sweden"
            assert 10.0 <= f["lon"] <= 24.5, f"{f['id']}: lon {f['lon']} out of Sweden"

    def test_city_specific_datasets(self):
        assert len(BORAS_FACILITIES) >= 2, "Borås should have ≥2 facilities"
        assert len(GOTEBORG_FACILITIES) >= 2, "Göteborg should have ≥2 facilities"
        assert len(STOCKHOLM_FACILITIES) >= 2, "Stockholm should have ≥2 facilities"


class TestFacilityFilters:
    """查询辅助函数"""

    def test_get_facilities_by_city(self):
        for city in ["Borås", "Göteborg", "Stockholm"]:
            fs = get_facilities_by_city(city)
            assert len(fs) >= 1
            for f in fs:
                assert f["city"] == city

    def test_get_facilities_by_type(self):
        metal_recovery = get_facilities_by_type("metal_recovery")
        assert len(metal_recovery) >= 1
        for f in metal_recovery:
            assert f["facility_type"] == "metal_recovery"

    def test_unknown_type_returns_empty(self):
        assert get_facilities_by_type("unicorn_factory") == []

    def test_facility_type_counts(self):
        # 至少有 3 种 facility_type
        assert len(FACILITY_TYPE_COUNTS) >= 3
        # 总和 = ALL_FACILITIES 长度
        assert sum(FACILITY_TYPE_COUNTS.values()) == len(ALL_FACILITIES)


class TestWorldBuilderIntegration:
    """WorldBuilder.build_demand_points() 应该用真实设施"""

    def test_default_uses_real_facilities(self):
        """默认 WorldConfig.use_real_facilities = True"""
        from agents.world_builder import WorldConfig
        assert WorldConfig().use_real_facilities is True

    def test_build_demand_points_with_real(self):
        """build_demand_points(10) 应该返回 10 个真实设施"""
        from agents.world_builder import WorldBuilder, WorldConfig
        config = WorldConfig(n_demand_points=10, seed=42)
        builder = WorldBuilder(config)
        demands = builder.build_demand_points()
        assert len(demands) == 10
        # 至少 80% 应该是真实设施 (10/13 = 76%, 取整 8)
        real_count = sum(1 for d in demands if d.get("data_source") == "real_sweden_facilities")
        assert real_count >= 8, f"only {real_count}/10 real, expected ≥8"

    def test_build_demand_points_with_synthetic_fallback(self):
        """use_real_facilities=False 应该全用虚构 DEMAND_FACILITIES"""
        from agents.world_builder import WorldBuilder, WorldConfig
        config = WorldConfig(n_demand_points=10, use_real_facilities=False)
        builder = WorldBuilder(config)
        demands = builder.build_demand_points()
        # 全部应该是 synthetic (data_source 字段不存在或 = synthetic)
        assert all(d.get("data_source") == "synthetic" for d in demands)

    def test_build_demand_points_exceeding_real_count_falls_back(self):
        """n_demand_points=20 应该使用所有 13 个真实设施 (可能重复) + 余下虚构"""
        from agents.world_builder import WorldBuilder, WorldConfig
        config = WorldConfig(n_demand_points=20, use_real_facilities=True)
        builder = WorldBuilder(config)
        demands = builder.build_demand_points()
        assert len(demands) == 20
        real_count = sum(1 for d in demands if d.get("data_source") == "real_sweden_facilities")
        # 应该包含所有 13 个 unique 真实设施 (按 i % 13 循环)
        unique_real_ids = set(
            d["id"] for d in demands if d.get("data_source") == "real_sweden_facilities"
        )
        assert len(unique_real_ids) == get_facility_count(), (
            f"all {get_facility_count()} unique real facilities should be used, "
            f"got {len(unique_real_ids)}"
        )
        # 而且 real_count >= 13 (可能重复使用)
        assert real_count >= get_facility_count()

    def test_real_facilities_carry_metadata(self):
        """真实设施 demand 应携带 facility_type + operator"""
        from agents.world_builder import WorldBuilder, WorldConfig
        config = WorldConfig(n_demand_points=3, seed=42)
        builder = WorldBuilder(config)
        demands = builder.build_demand_points()
        real = [d for d in demands if d.get("data_source") == "real_sweden_facilities"]
        assert real, "expected at least 1 real facility"
        for d in real:
            assert "facility_type" in d
            assert "operator" in d
            assert d["operator"] != "", "real facility must have operator"
            # facility_type 应该是已知枚举
            valid_types = {
                "recycling_center", "harbor_cargo", "metal_recovery",
                "paper_mill", "textile_recycling", "concrete_recycling",
                "waste_to_energy", "plastic_recycling",
            }
            assert d["facility_type"] in valid_types

    def test_real_facility_base_demand_reflects_capacity(self):
        """真实设施 base_demand 应该在 capacity × [0.4, 0.8] 范围内"""
        from agents.world_builder import WorldBuilder, WorldConfig
        config = WorldConfig(n_demand_points=5, seed=42)
        builder = WorldBuilder(config)
        demands = builder.build_demand_points()
        real = [d for d in demands if d.get("data_source") == "real_sweden_facilities"]
        for d in real:
            cap = next(
                f["processing_capacity_tons_per_day"]
                for f in ALL_FACILITIES
                if f["id"] == d["id"]
            )
            assert cap * 0.4 <= d["base_demand_tons"] <= cap * 0.8, (
                f"{d['id']}: base {d['base_demand_tons']} not in [{cap*0.4}, {cap*0.8}]"
            )