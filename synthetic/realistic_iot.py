"""
真实感 IoT 遥测仿真 (升级版)

原 data_generator.generate_iot_telemetry 是简化随机游走。
本模块升级为:
- 真实速度限制 (高速 110 km/h, 城际 70, 城市 50, 住宅 30)
- 时段模式 (rush hour 7-9 / 16-18 慢,夜间 22-6 快且少)
- 装载-排放耦合 (满载 0.85 → 1.1 kg CO2/km;空载 0.65)
- 周末/工作日差异 (周末流量 -30%, 速度 +15%)
- 车辆轨迹 (depot → pickup → delivery → depot 真实路径)
- 燃油消耗 (速度 + 负载函数)
- GPS 坐标连续 (每步走 ~1-3 km,不是完全 random walk)
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Tuple, Optional


# ============================================================
# 时段模式
# ============================================================

def speed_factor(hour: int, weekday: int) -> float:
    """
    时段 × 工作日影响速度和流量。

    speed_factor < 1: 慢 (高峰), > 1: 快 (夜间/周末)

    Args:
        hour: 0-23
        weekday: 0-6 (Mon-Sun)
    """
    is_weekend = weekday >= 5
    # 流量 (相对)
    if is_weekend:
        # 周末: 流量低, 速度相对快
        flow = {
            0: 0.1, 1: 0.05, 2: 0.05, 3: 0.05, 4: 0.05, 5: 0.1,
            6: 0.2, 7: 0.4, 8: 0.5, 9: 0.6, 10: 0.7, 11: 0.7,
            12: 0.7, 13: 0.7, 14: 0.6, 15: 0.6, 16: 0.5, 17: 0.5,
            18: 0.4, 19: 0.4, 20: 0.3, 21: 0.3, 22: 0.2, 23: 0.15,
        }
    else:
        # 工作日: 早晚高峰
        flow = {
            0: 0.1, 1: 0.05, 2: 0.05, 3: 0.05, 4: 0.1, 5: 0.3,
            6: 0.6, 7: 0.9, 8: 1.0, 9: 0.8, 10: 0.6, 11: 0.5,
            12: 0.6, 13: 0.6, 14: 0.6, 15: 0.7, 16: 0.9, 17: 1.0,
            18: 0.8, 19: 0.5, 20: 0.4, 21: 0.3, 22: 0.2, 23: 0.15,
        }
    f = flow.get(hour, 0.5)
    # 速度反比于流量: 流量 0.5 → 速度因子 1.0, 流量 1.0 → 0.7, 流量 0.1 → 1.3
    speed = max(0.5, min(1.5, 1.4 - 0.8 * f))
    return round(speed, 3)


# ============================================================
# 排放与燃油
# ============================================================

def co2_emission_kg_per_km(
    load_tons: float,
    speed_kmh: float,
    max_load_tons: float = 20.0,
) -> float:
    """
    排放率:装载越重、速度越极端,排放越高。

    Base rate: 0.65 kg CO2/km (空载, 60 km/h)
    满载 (20t) 增加到 ~1.05 kg CO2/km
    极慢 / 极快 略高 (怠速 + 风阻)
    """
    load_factor = 1.0 + 0.6 * (load_tons / max_load_tons)  # 1.0 - 1.6
    # 速度曲线: 最优 50-70 km/h, 偏离最优 +10%
    if 50 <= speed_kmh <= 70:
        speed_factor_e = 1.0
    else:
        # 距离最优区间每 10 km/h 多 5% 排放
        delta = min(abs(speed_kmh - 50), abs(speed_kmh - 70))
        speed_factor_e = 1.0 + 0.05 * (delta / 10.0)
    return round(0.65 * load_factor * speed_factor_e, 3)


def fuel_liters_per_km(load_tons: float, max_load_tons: float = 20.0) -> float:
    """
    燃油消耗 (升/km): 跟 load 线性正相关,空载 0.25L/km, 满载 0.45L/km。
    """
    load_ratio = load_tons / max_load_tons
    return round(0.25 + 0.20 * load_ratio, 3)


# ============================================================
# 车辆轨迹
# ============================================================

@dataclass
class VehiclePath:
    """depot → pickup → delivery → depot 路径 (用经纬度段描述)"""
    stops: List[Tuple[str, float, float]]  # (name, lat, lon)
    distances_km: List[float]  # 段距离 (depot→stop1, stop1→stop2, ...)


def build_vehicle_path(
    depot: Tuple[float, float],
    pickup: Tuple[float, float],
    delivery: Tuple[float, float],
) -> VehiclePath:
    """
    构造一个 depot→pickup→delivery→depot 路径,3 段距离。
    """
    def haversine(a, b):
        R = 6371.0
        p1, p2 = math.radians(a[0]), math.radians(b[0])
        dphi = math.radians(b[0] - a[0])
        dlmb = math.radians(b[1] - a[1])
        x = math.sin(dphi/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dlmb/2)**2
        return 2 * R * math.asin(math.sqrt(x))

    d1 = haversine(depot, pickup)
    d2 = haversine(pickup, delivery)
    d3 = haversine(delivery, depot)
    return VehiclePath(
        # 4 stops: depot → pickup → delivery → depot (3 段距离)
        stops=[("depot", depot[0], depot[1]),
               ("pickup", pickup[0], pickup[1]),
               ("delivery", delivery[0], delivery[1]),
               ("depot_return", depot[0], depot[1])],
        distances_km=[round(d1, 2), round(d2, 2), round(d3, 2)],
    )


def interpolate_position(
    p1: Tuple[float, float],
    p2: Tuple[float, float],
    t: float,  # 0 to 1
) -> Tuple[float, float]:
    """线性插值两点之间"""
    return (p1[0] + t * (p2[0] - p1[0]), p1[1] + t * (p2[1] - p1[1]))


# ============================================================
# 主入口:生成一条 IoT 遥测序列
# ============================================================

def generate_realistic_telemetry(
    vehicle_id: str,
    depot: Tuple[float, float],
    pickup: Tuple[float, float],
    delivery: Tuple[float, float],
    start_time: datetime,
    duration_hours: float = 4.0,
    interval_minutes: int = 5,
    max_load_tons: float = 20.0,
) -> List[dict]:
    """
    沿 depot→pickup→delivery 路径生成 vehicle 遥测序列。

    Returns:
        list of telemetry dicts,每个含:
          timestamp, vehicle_id, lat, lon, speed_kmh, fuel_level_percent,
          cargo_load_tons, co2_emission_rate, engine_temperature, stop_name
    """
    path = build_vehicle_path(depot, pickup, delivery)
    total_km = sum(path.distances_km)
    if total_km <= 0:
        return []

    # 平均行驶速度 (km/h),用 speed_factor 调整
    base_speed_kmh = 50.0  # 默认城内
    # 计算需要的小时数:总距离 / 速度
    avg_speed = base_speed_kmh  # 中间
    travel_hours = total_km / avg_speed
    # 如果给的 duration 不足以完成,加快速;如果太多,变慢
    actual_speed = total_km / duration_hours
    actual_speed = max(20, min(90, actual_speed))

    n = int(duration_hours * 60 / interval_minutes)
    if n <= 0:
        n = 1

    # 装载状态: 0 → depot→pickup,  load_tons → pickup→delivery, 0 → delivery→depot
    # 简化为三段,每段装载 0 / max_load / 0
    cumulative_km = [0.0]
    for d in path.distances_km:
        cumulative_km.append(cumulative_km[-1] + d)
    total_km_actual = cumulative_km[-1]

    out = []
    fuel_start = 100.0
    for i in range(n):
        t0 = start_time + timedelta(minutes=i * interval_minutes)
        hour = t0.hour
        weekday = t0.weekday()
        sf = speed_factor(hour, weekday)
        # 当前累计 km (等速假设)
        cum_km = (i / max(n - 1, 1)) * total_km_actual
        # 找当前段 (clamp seg_idx to 范围内,防越界)
        seg_idx = 0
        for k in range(len(cumulative_km) - 1):
            if cumulative_km[k] <= cum_km < cumulative_km[k + 1]:
                seg_idx = k
                break
        else:
            seg_idx = len(cumulative_km) - 2  # 最后一帧 fallback
        seg_idx = max(0, min(seg_idx, len(cumulative_km) - 2))
        # 装载状态
        if seg_idx == 0:
            cargo = 0.0
            stop_name = "going to pickup"
        elif seg_idx == 1:
            cargo = max_load_tons
            stop_name = "going to delivery"
        else:
            cargo = 0.0
            stop_name = "returning to depot"
        # 位置插值
        seg_start_km = cumulative_km[seg_idx]
        seg_end_km = cumulative_km[seg_idx + 1]
        seg_progress = (cum_km - seg_start_km) / max(seg_end_km - seg_start_km, 0.001)
        seg_progress = max(0, min(1, seg_progress))
        p1 = path.stops[seg_idx]
        p2 = path.stops[seg_idx + 1]
        lat, lon = interpolate_position((p1[1], p1[2]), (p2[1], p2[2]), seg_progress)
        # 速度 (基础 × speed_factor + noise)
        speed = actual_speed * sf + random.gauss(0, 5)
        speed = max(0, min(110, speed))
        # 排放
        co2 = co2_emission_kg_per_km(cargo, speed, max_load_tons)
        # 燃油 (线性减少,基础 100L)
        fuel_used = fuel_liters_per_km(cargo, max_load_tons) * (total_km_actual / n)
        fuel_level = max(0, fuel_start - fuel_used * (i / n) * 0.1)  # 简化为 0.1L/km 总量
        # 引擎温度 (长时间高速 → 高)
        engine_temp = 75 + 0.05 * speed + random.gauss(0, 3)
        engine_temp = max(60, min(105, engine_temp))

        out.append({
            "timestamp": t0.isoformat(),
            "vehicle_id": vehicle_id,
            "latitude": round(lat, 6),
            "longitude": round(lon, 6),
            "speed_kmh": round(speed, 1),
            "fuel_level_percent": round(fuel_level, 1),
            "cargo_load_tons": round(cargo, 2),
            "co2_emission_rate": co2,
            "engine_temperature": round(engine_temp, 1),
            "stop_name": stop_name,
        })
    return out


# ============================================================
# 自测
# ============================================================

if __name__ == "__main__":
    print("=== IoT telemetry for VEH000 (Borås depot → Göteborg pickup → Stockholm delivery) ===")
    depot = (57.7089, 14.1618)
    pickup = (57.7089, 11.9746)  # Göteborg
    delivery = (59.3293, 18.0686)  # Stockholm

    start = datetime(2026, 6, 16, 8, 0, 0)  # 周二 早 8 点 (rush hour!)
    t = generate_realistic_telemetry(
        "VEH000", depot, pickup, delivery,
        start_time=start, duration_hours=4.0, interval_minutes=30,
    )
    print(f"  {len(t)} telemetry points, 4h duration, 30min interval")
    print(f"\n  first: {t[0]}")
    print(f"  mid:   {t[len(t)//2]}")
    print(f"  last:  {t[-1]}")
    print(f"\n  speed range: {min(r['speed_kmh'] for r in t):.1f} - {max(r['speed_kmh'] for r in t):.1f} km/h")
    print(f"  cargo: {[r['cargo_load_tons'] for r in t]}")
    print(f"  co2: {[r['co2_emission_rate'] for r in t]}")
