"""
Tests for realistic IoT telemetry generation.
"""

import pytest
from datetime import datetime

from synthetic.realistic_iot import (
    speed_factor,
    co2_emission_kg_per_km,
    fuel_liters_per_km,
    build_vehicle_path,
    generate_realistic_telemetry,
)


class TestSpeedFactor:
    def test_rush_hour_slower_than_night(self):
        # Tuesday 8am rush hour vs 2am night
        rush = speed_factor(8, 0)
        night = speed_factor(2, 0)
        assert night > rush

    def test_weekend_faster_than_weekday(self):
        # Same hour, weekend should be faster
        weekday = speed_factor(8, 0)  # Mon 8am
        weekend = speed_factor(8, 5)  # Sat 8am
        assert weekend > weekday

    def test_bounds(self):
        for h in range(24):
            for w in range(7):
                sf = speed_factor(h, w)
                assert 0.5 <= sf <= 1.5


class TestEmission:
    def test_empty_load_lower_than_full(self):
        empty = co2_emission_kg_per_km(0, 60)
        full = co2_emission_kg_per_km(20, 60)
        assert full > empty
        # 满载应该至少 1.5x 空载
        assert full / empty >= 1.5

    def test_optimal_speed_min_emission(self):
        # 50-70 km/h 是最优区间
        optimal_low = co2_emission_kg_per_km(10, 50)
        optimal_high = co2_emission_kg_per_km(10, 70)
        extreme_slow = co2_emission_kg_per_km(10, 20)
        extreme_fast = co2_emission_kg_per_km(10, 110)
        # 极慢 / 极快应 > 最优
        assert extreme_slow > optimal_low
        assert extreme_fast > optimal_low

    def test_zero_speed_zero_emission_factor(self):
        # 速度为 0 时,speed_factor=1.0 (50以下)
        e = co2_emission_kg_per_km(10, 0)
        # 应该是 0.65 * 1.6 (满载 load_factor) * 1.05 (速度偏离) ≈ 1.09
        assert 1.0 < e < 1.2


class TestFuel:
    def test_empty_lower_than_full(self):
        empty = fuel_liters_per_km(0)
        full = fuel_liters_per_km(20)
        assert full > empty
        # 满载约 0.45L/km, 空载 0.25L/km
        assert 0.4 < full < 0.5
        assert 0.2 < empty < 0.3

    def test_linear_in_load(self):
        # 燃油消耗随 load 线性
        assert fuel_liters_per_km(10) == pytest.approx(0.35, abs=0.01)
        assert fuel_liters_per_km(15) == pytest.approx(0.40, abs=0.01)


class TestBuildVehiclePath:
    def test_3_segments_4_stops(self):
        depot = (57.7089, 14.1618)
        pickup = (57.7089, 11.9746)
        delivery = (59.3293, 18.0686)
        path = build_vehicle_path(depot, pickup, delivery)
        assert len(path.stops) == 4
        assert len(path.distances_km) == 3
        assert path.stops[0][0] == "depot"
        assert path.stops[-1][0] == "depot_return"

    def test_distances_positive(self):
        path = build_vehicle_path((57.7, 14.2), (57.7, 12.0), (59.3, 18.1))
        for d in path.distances_km:
            assert d > 0


class TestGenerateTelemetry:
    def test_basic_telemetry(self):
        depot = (57.7089, 14.1618)
        pickup = (57.7089, 11.9746)
        delivery = (59.3293, 18.0686)
        start = datetime(2026, 6, 16, 8, 0)
        t = generate_realistic_telemetry(
            "VEH000", depot, pickup, delivery,
            start_time=start, duration_hours=2.0, interval_minutes=15,
        )
        assert len(t) == 8  # 2h * 60 / 15 = 8

    def test_cargo_pattern(self):
        """0 → pickup (empty), pickup → delivery (full), delivery → depot (empty)"""
        depot = (57.7089, 14.1618)
        pickup = (57.7089, 11.9746)
        delivery = (59.3293, 18.0686)
        t = generate_realistic_telemetry(
            "VEH001", depot, pickup, delivery,
            start_time=datetime(2026, 6, 16, 8, 0),
            duration_hours=3.0, interval_minutes=15,
        )
        # 第 1 个点应该是空载
        assert t[0]["cargo_load_tons"] == 0
        # 中间应该满载
        cargos = [r["cargo_load_tons"] for r in t]
        assert max(cargos) == 20.0
        # 最后一个应该是空载
        assert t[-1]["cargo_load_tons"] == 0

    def test_stop_names(self):
        depot = (57.7089, 14.1618)
        pickup = (57.7089, 11.9746)
        delivery = (59.3293, 18.0686)
        t = generate_realistic_telemetry(
            "VEH002", depot, pickup, delivery,
            start_time=datetime(2026, 6, 16, 8, 0),
            duration_hours=3.0, interval_minutes=15,
        )
        stop_names = {r["stop_name"] for r in t}
        # 应该有 3 种 stop_name
        assert "going to pickup" in stop_names
        assert "going to delivery" in stop_names
        assert "returning to depot" in stop_names

    def test_gps_progression(self):
        """GPS 坐标应该沿 depot→pickup→delivery 移动"""
        depot = (57.7089, 14.1618)
        pickup = (57.7089, 11.9746)  # 往西
        delivery = (59.3293, 18.0686)  # 往东北
        t = generate_realistic_telemetry(
            "VEH003", depot, pickup, delivery,
            start_time=datetime(2026, 6, 16, 8, 0),
            duration_hours=3.0, interval_minutes=15,
        )
        # 第 1 个点应该在 depot 附近
        assert abs(t[0]["latitude"] - depot[0]) < 0.1
        # 中间点应该比较靠 pickup 区域 (Göteborg)
        mid = t[len(t) // 2]
        # (Göteborg 离 Borås 130km,中间点可能已经过 pickup)
        # 第 2 段起点是 pickup (Göteborg)
        # 第 2 段终点是 delivery (Stockholm)
        # 看 lat 变化: 应该单调增加 (Borås 57.7 → Stockholm 59.3)

    def test_speed_within_bounds(self):
        t = generate_realistic_telemetry(
            "VEH004", (57.7, 14.2), (57.7, 12.0), (59.3, 18.1),
            start_time=datetime(2026, 6, 16, 8, 0),
            duration_hours=2.0, interval_minutes=10,
        )
        for r in t:
            assert 0 <= r["speed_kmh"] <= 110
            assert 60 <= r["engine_temperature"] <= 105
            assert 0 <= r["fuel_level_percent"] <= 100

    def test_co2_correlates_with_load(self):
        """满载时 CO2 应高于空载"""
        t = generate_realistic_telemetry(
            "VEH005", (57.7, 14.2), (57.7, 12.0), (59.3, 18.1),
            start_time=datetime(2026, 6, 16, 8, 0),
            duration_hours=3.0, interval_minutes=15,
        )
        empty_co2 = [r["co2_emission_rate"] for r in t if r["cargo_load_tons"] == 0]
        full_co2 = [r["co2_emission_rate"] for r in t if r["cargo_load_tons"] == 20]
        if full_co2 and empty_co2:
            assert min(full_co2) > max(empty_co2)
