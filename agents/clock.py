"""
加速模拟时钟 (Accelerated Simulation Clock)

1 simulation cycle = 1 simulated day
Internal hour can be advanced within a day for fine-grained activity modeling
(e.g. higher supply rates during 6-18h).

This decouples simulation time from wall-clock time so demos / benchmarks can
run as fast as the compute allows.
"""

from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import Tuple


@dataclass
class SimTime:
    """模拟时间点"""
    day: int          # 模拟第几天（从 0 开始）
    hour: int         # 当日小时 (0-23)
    minute: int = 0   # 当日分钟 (0-59)

    def to_iso(self, base_date: datetime = None) -> str:
        """转 ISO 字符串。base_date 是锚定到现实的起始日（默认今天）。"""
        base = base_date or datetime.now()
        real = base + timedelta(days=self.day, hours=self.hour, minutes=self.minute)
        return real.isoformat()

    def __str__(self) -> str:
        return f"Day {self.day:03d} {self.hour:02d}:{self.minute:02d}"


class SimClock:
    """
    加速时钟：每次 advance() 推进一个 step（默认 1 天），可配 step_hours 细粒度推进。

    Examples
    --------
    >>> clock = SimClock()
    >>> clock.now.hour
    0
    >>> clock.advance_day()
    Day(day=1, hour=0, minute=0)
    >>> clock.activity_factor
    1.5  # 启动时 hour=0，夜间
    """

    # 活动因子：6-18h 是白天 = 1.5x, 其余 = 0.5x
    DAY_HOURS = range(6, 19)
    NIGHT_FACTOR = 0.5
    DAY_FACTOR = 1.5

    def __init__(self, start_day: int = 0, start_hour: int = 0, base_date: datetime = None):
        self.now = SimTime(day=start_day, hour=start_hour)
        self.base_date = base_date or datetime.now()
        self.total_cycles = 0

    # 每天的小时不再固定 0，按一个 7-步的 sequence 轮转，覆盖白天/夜晚，
    # 让 activity_factor 真正随 cycle 变化（白天 6-18h = 1.5x，夜晚 = 0.5x）。
    HOUR_PATTERN = (8, 12, 18, 0, 6, 14, 22)

    @property
    def activity_factor(self) -> float:
        """当前小时对应的活动因子（用于供应/需求建模）"""
        if self.now.hour in self.DAY_HOURS:
            return self.DAY_FACTOR
        return self.NIGHT_FACTOR

    @property
    def iso_now(self) -> str:
        """当前模拟时间对应的 ISO 时间戳（锚定到 base_date）"""
        return self.now.to_iso(self.base_date)

    def advance_day(self) -> SimTime:
        """推进 1 个完整模拟日。
        hour 从 HOUR_PATTERN 里按 total_cycles 选一个，保证可复现又覆盖 24h。
        """
        self.total_cycles += 1
        hour = self.HOUR_PATTERN[(self.total_cycles - 1) % len(self.HOUR_PATTERN)]
        self.now = SimTime(day=self.now.day + 1, hour=hour)
        return self.now

    def advance_hours(self, hours: int) -> SimTime:
        """推进 N 小时，跨日自动进位"""
        total = self.now.hour + hours
        day_offset, new_hour = divmod(total, 24)
        self.now = SimTime(day=self.now.day + day_offset, hour=new_hour)
        if day_offset > 0:
            self.total_cycles += day_offset
        return self.now

    def reset(self) -> None:
        """重置到初始状态（保留 base_date）"""
        self.now = SimTime(day=0, hour=0)
        self.total_cycles = 0

    def state(self) -> dict:
        """导出当前状态（用于持久化）"""
        return {
            "sim_day": self.now.day,
            "sim_hour": self.now.hour,
            "activity_factor": self.activity_factor,
            "total_cycles": self.total_cycles,
        }
