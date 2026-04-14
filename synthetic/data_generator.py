"""
合成数据生成器

生成：
- 每日废料供应量
- 需求点需求
- IoT 设备遥测数据
- 车辆位置和状态
"""

from typing import List, Dict, Any
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict
import random
import numpy as np
from loguru import logger

try:
    from faker import Faker
    faker = Faker('sv_SE')  # 瑞典语环境
    FAKER_AVAILABLE = True
except ImportError:
    FAKER_AVAILABLE = False


@dataclass
class SupplyReading:
    """供应点读数"""
    timestamp: str
    location_id: str
    material_type: str
    weight_tons: float
    moisture_percent: float
    quality_score: float  # 0-100


@dataclass
class IoTTelemetry:
    """IoT 遥测数据"""
    timestamp: str
    vehicle_id: str
    latitude: float
    longitude: float
    speed_kmh: float
    fuel_level_percent: float
    cargo_load_tons: float
    co2_emission_rate: float
    engine_temperature: float


@dataclass
class DemandReading:
    """需求点读数"""
    timestamp: str
    location_id: str
    material_type: str
    required_tons: float
    priority: str  # low, normal, high, urgent
    deadline: str


class SyntheticDataGenerator:
    """
    合成数据生成器
    
    生成逼真的物流系统数据用于测试和模拟
    """
    
    def __init__(self, seed: int = None):
        if seed is not None:
            random.seed(seed)
            np.random.seed(seed)
        
        self.material_types = [
            "mixed_waste",
            "metal_scrap",
            "wood_waste",
            "plastic",
            "concrete",
            "paper_cardboard"
        ]
        
        self.priorities = ["low", "normal", "high", "urgent"]
    
    def generate_supply_reading(
        self,
        location_id: str,
        timestamp: datetime = None
    ) -> SupplyReading:
        """生成单个供应点读数"""
        if timestamp is None:
            timestamp = datetime.now()
        
        material = random.choice(self.material_types)
        
        # 基于材料类型的典型重量范围
        weight_ranges = {
            "mixed_waste": (5, 30),
            "metal_scrap": (2, 15),
            "wood_waste": (10, 50),
            "plastic": (3, 20),
            "concrete": (15, 60),
            "paper_cardboard": (5, 25)
        }
        
        min_w, max_w = weight_ranges.get(material, (5, 30))
        weight = round(random.uniform(min_w, max_w), 2)
        
        return SupplyReading(
            timestamp=timestamp.isoformat(),
            location_id=location_id,
            material_type=material,
            weight_tons=weight,
            moisture_percent=round(random.uniform(10, 40), 1),
            quality_score=round(random.uniform(60, 95), 1)
        )
    
    def generate_daily_supply(
        self,
        location_id: str,
        date: datetime = None,
        intervals_per_day: int = 24
    ) -> List[SupplyReading]:
        """
        生成一天的供应数据
        
        Args:
            location_id: 位置 ID
            date: 日期
            intervals_per_day: 每天的数据点数量
        
        Returns:
            供应读数列表
        """
        if date is None:
            date = datetime.now()
        
        readings = []
        interval_hours = 24 / intervals_per_day
        
        for i in range(intervals_per_day):
            timestamp = date + timedelta(hours=i * interval_hours)
            
            # 模拟日间活动模式（白天更多）
            hour = timestamp.hour
            if 6 <= hour <= 18:
                activity_factor = 1.5
            else:
                activity_factor = 0.5
            
            reading = self.generate_supply_reading(location_id, timestamp)
            
            # 应用活动因子
            if FAKER_AVAILABLE:
                adjusted_weight = reading.weight_tons * activity_factor
                readings.append(SupplyReading(
                    timestamp=reading.timestamp,
                    location_id=reading.location_id,
                    material_type=reading.material_type,
                    weight_tons=round(adjusted_weight, 2),
                    moisture_percent=reading.moisture_percent,
                    quality_score=reading.quality_score
                ))
        
        logger.debug(f"生成 {len(readings)} 条供应数据：{location_id}")
        return readings
    
    def generate_iot_telemetry(
        self,
        vehicle_id: str,
        start_location: tuple = None,
        duration_hours: int = 8,
        interval_minutes: int = 5
    ) -> List[IoTTelemetry]:
        """
        生成车辆 IoT 遥测数据
        
        Args:
            vehicle_id: 车辆 ID
            start_location: 起始位置 (lat, lon)
            duration_hours: 持续小时数
            interval_minutes: 数据间隔（分钟）
        
        Returns:
            IoT 遥测数据列表
        """
        if start_location is None:
            start_location = (57.7089, 14.1618)  # Borås
        
        readings = []
        current_lat, current_lon = start_location
        current_time = datetime.now()
        current_load = random.uniform(5, 15)  # 初始负载
        
        num_readings = int(duration_hours * 60 / interval_minutes)
        
        for i in range(num_readings):
            timestamp = current_time + timedelta(minutes=i * interval_minutes)
            
            # 模拟移动
            if random.random() > 0.3:  # 70% 时间在移动
                speed = random.uniform(30, 80)
                # 简单模拟位置变化
                current_lat += random.uniform(-0.01, 0.01)
                current_lon += random.uniform(-0.01, 0.01)
            else:
                speed = 0
            
            # 模拟负载变化（卸货）
            if random.random() > 0.8 and current_load > 2:
                current_load -= random.uniform(2, 5)
            
            # 模拟燃油消耗
            fuel_consumption = speed * 0.01 if speed > 0 else 0.05
            fuel_level = max(0, 100 - fuel_consumption * i * interval_minutes / 60)
            
            readings.append(IoTTelemetry(
                timestamp=timestamp.isoformat(),
                vehicle_id=vehicle_id,
                latitude=round(current_lat, 6),
                longitude=round(current_lon, 6),
                speed_kmh=round(speed, 1),
                fuel_level_percent=round(fuel_level, 1),
                cargo_load_tons=round(max(0, current_load), 2),
                co2_emission_rate=round(0.85 + random.uniform(-0.1, 0.1), 3),
                engine_temperature=round(random.uniform(75, 95), 1)
            ))
        
        logger.debug(f"生成 {len(readings)} 条 IoT 数据：{vehicle_id}")
        return readings
    
    def generate_demand_reading(
        self,
        location_id: str,
        timestamp: datetime = None
    ) -> DemandReading:
        """生成需求点读数"""
        if timestamp is None:
            timestamp = datetime.now()
        
        material = random.choice(self.material_types)
        required = round(random.uniform(10, 100), 2)
        priority = random.choices(
            self.priorities,
            weights=[10, 50, 30, 10]  # 权重分布
        )[0]
        
        # 基于优先级设置截止时间
        deadline_hours = {
            "low": 72,
            "normal": 48,
            "high": 24,
            "urgent": 12
        }
        deadline = timestamp + timedelta(hours=deadline_hours[priority])
        
        return DemandReading(
            timestamp=timestamp.isoformat(),
            location_id=location_id,
            material_type=material,
            required_tons=required,
            priority=priority,
            deadline=deadline.isoformat()
        )
    
    def generate_fleet_snapshot(
        self,
        vehicle_ids: List[str],
        region_center: tuple = (57.7089, 14.1618)
    ) -> List[Dict[str, Any]]:
        """
        生成车队实时快照
        
        Returns:
            车辆状态列表
        """
        statuses = ["available", "en_route", "loading", "unloading", "maintenance"]
        status_weights = [30, 50, 10, 5, 5]
        
        snapshot = []
        for vid in vehicle_ids:
            status = random.choices(statuses, weights=status_weights)[0]
            
            snapshot.append({
                "vehicle_id": vid,
                "status": status,
                "location": {
                    "lat": round(region_center[0] + random.uniform(-0.5, 0.5), 6),
                    "lon": round(region_center[1] + random.uniform(-0.5, 0.5), 6)
                },
                "fuel_level": round(random.uniform(20, 100), 1),
                "current_load_tons": round(random.uniform(0, 20), 2),
                "last_update": datetime.now().isoformat()
            })
        
        return snapshot
    
    def export_to_json(self, data: Any, filepath: str) -> bool:
        """导出数据为 JSON"""
        import json
        
        try:
            # 处理 dataclass 对象
            if hasattr(data, '__dataclass_fields__'):
                data = asdict(data)
            elif isinstance(data, list) and len(data) > 0:
                if hasattr(data[0], '__dataclass_fields__'):
                    data = [asdict(item) for item in data]
            
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            
            logger.info(f"数据导出完成：{filepath}")
            return True
        except Exception as e:
            logger.error(f"导出失败：{e}")
            return False


# ============================================
# 使用示例
# ============================================
if __name__ == "__main__":
    # 创建生成器
    generator = SyntheticDataGenerator(seed=42)
    
    # 生成供应数据
    supply_data = generator.generate_daily_supply(
        location_id="SUP001",
        date=datetime.now(),
        intervals_per_day=12
    )
    
    print("\n" + "="*60)
    print("供应数据示例")
    print("="*60)
    for reading in supply_data[:5]:
        print(f"{reading.timestamp}: {reading.weight_tons}t {reading.material_type}")
    
    # 生成 IoT 数据
    iot_data = generator.generate_iot_telemetry(
        vehicle_id="VEH001",
        duration_hours=4,
        interval_minutes=10
    )
    
    print("\n" + "="*60)
    print("IoT 遥测示例")
    print("="*60)
    for telemetry in iot_data[:5]:
        print(f"{telemetry.timestamp}: {telemetry.speed_kmh}km/h, {telemetry.cargo_load_tons}t")
    
    # 生成车队快照
    fleet = generator.generate_fleet_snapshot(
        vehicle_ids=[f"VEH{i:03d}" for i in range(10)]
    )
    
    print("\n" + "="*60)
    print("车队快照")
    print("="*60)
    for vehicle in fleet[:5]:
        print(f"{vehicle['vehicle_id']}: {vehicle['status']} @ {vehicle['location']}")
    
    # 导出示例
    generator.export_to_json(supply_data, "synthetic/sample_supply_data.json")
