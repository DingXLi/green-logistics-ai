"""SQLite 持久化层 (Persistence Layer)

将每次优化周期的结果落到 SQLite，方便：
1. 实验复现（同一 seed + 历史数据 → 同一结果）
2. 论文 figure 绘制（直接 SQL 拉 KPI 时间序列）
3. 失败回放（crash 后从断点继续）

Schema:
- optimization_cycles: 1 行 / 周期（KPI）
- supply_offers: 1 行 / 供应点 / 周期
- demand_requests: 1 行 / 需求点 / 周期
- matches: 1 行 / 匹配 / 周期
- routes: 1 行 / 车辆路径 / 周期

公开 API 不变：`from agents.persistence import Persistence`。

结构：
  _base           存储核心（连接 / schema / 缓存）
  _contract       跨模块共享查询契约
  _schema         SCHEMA_SQL 与共享类型
  _forecast_math  预测拟合数学
  领域 mixin       _cycles / _llm / _analytics / _exports / _stats /
                  _anomalies / _maintenance / _config / _forecasting /
                  _perturbation / _comparisons / _solver
"""

from ._analytics import AnalyticsMixin
from ._anomalies import AnomalyMixin
from ._comparisons import ComparisonMixin
from ._config import ConfigMixin
from ._cycles import CycleMixin
from ._exports import ExportMixin
from ._forecasting import ForecastMixin
from ._llm import LlmMixin
from ._maintenance import MaintenanceMixin
from ._perturbation import PerturbationMixin
from ._schema import SCHEMA_SQL
from ._solver import SolverMixin
from ._stats import StatsMixin


class Persistence(
    CycleMixin,
    LlmMixin,
    AnalyticsMixin,
    ExportMixin,
    StatsMixin,
    AnomalyMixin,
    MaintenanceMixin,
    ConfigMixin,
    ForecastMixin,
    PerturbationMixin,
    ComparisonMixin,
    SolverMixin,
):
    """SQLite 持久化封装

    Usage
    -----
    >>> db = Persistence("data/simulation.db")
    >>> db.init_schema()
    >>> db.begin_cycle("OPT0001", day=0, hour=0)
    >>> db.record_supply("OPT0001", {...})
    >>> db.record_match("OPT0001", {...})
    >>> db.commit_cycle("OPT0001", kpi={...})
    """


__all__ = ["Persistence", "SCHEMA_SQL"]
