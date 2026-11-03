# ADR-0009: 持久化层拆分 —— 9,949 行单文件 → 16 个领域模块

- **状态**：已接受（解掉 ADR-0005 里记的那条 9,949 行技术债）
- **记录日期**：2026-11-03
- **相关代码**：`agents/persistence/`（原 `agents/persistence.py`）,
  `tests/test_persistence*.py`, `tests/test_*`（1,600+ 用例覆盖公开 API）
- **关联**：[ADR-0005](0005-persistence-sqlite.md)（持久化与 schema）·
  [ADR-0007](0007-frontend-and-deployment.md)（后端与部署）

## 背景

ADR-0005 写下这条债时是这么记的：

> ⚠️ `agents/persistence.py` 已经长到 **9,949 行 / 423 KB**，
> 114 个方法全在一个类里 —— 这是当前仓库里最大的单点维护风险。

这条债当时的判断是「功能正常、CI 全绿，先不动」，和 ADR-0003 那条 SDK 债
一个理由。但它的成本比 SDK 债更直接：

1. **评审者读不动**：`Persistence` 一个类 114 个方法，从
   `begin_cycle` 一路排到 `get_solver_duration_by_season_status`，
   跨越周期写入、LLM 成本、KPI 统计、CSV 导出、留存分析、扰动规则、
   预测校准、对比分析 8 个互不相干的领域。
2. **改一处要读全文件**：加一个统计口径就要在 1 万行里定位；
   找不到就复制粘贴，最后长出第二套相似但不一致的 SQL。
3. **冲突成本高**：所有人改同一个文件 → 合并冲突密集 → 越改越不敢动。
4. **FDE 现场会被问**：「这 1 万行怎么维护的？」
   —— 面试官点开仓库看到 `agents/persistence.py` 9,949 行，
   哪怕功能都对，也会直接判「代码不可维护」。

触发这次重构的时机和 ADR-0008 一样：**要把它写进 portfolio 讲工程能力的时候，
一条自己写在自己 ADR 里的债，要么解掉，要么解释清楚为什么留着。**

## 决策

**把 `agents/persistence.py` 拆成一个包 `agents/persistence/`，
按领域切成 16 个模块；公开 API 一个字都不改。**

### 结构

```
agents/persistence/
├── __init__.py        # 组合类 Persistence + 公开 API
├── _base.py           # 存储核心：连接、schema 初始化/迁移、TTL 读缓存
├── _contract.py       # 跨模块共享查询契约（get_cycle_detail 等 3 个）
├── _schema.py         # SCHEMA_SQL（11 张表）+ _BucketSpec
├── _forecast_math.py  # 预测拟合数学：linear / moving_average / exp_smoothing
├── _cycles.py         # 周期写入与周期查询
├── _llm.py            # LLM 决策落库与成本分析
├── _analytics.py      # KPI / 时间序列 / 预测趋势
├── _exports.py        # CSV / 行式导出
├── _stats.py          # 运营统计与聚合（车辆、材料、留存、碳排）
├── _anomalies.py      # 异常周期检测
├── _maintenance.py    # VACUUM / ANALYZE 与维护日志
├── _config.py         # 运行时配置覆盖、method 偏好
├── _forecasting.py    # 预测校准（预测 vs 实际）
├── _perturbation.py   # 季节扰动规则与影响分析
├── _comparisons.py    # 周期 / 材料 / 路线对比
└── _solver.py         # 求解器耗时分析
```

### 关键设计

1. **Mixin 组合，而不是把类拆成多个类**：
   `Persistence` 仍然是一个类，只是由 12 个领域 mixin 组合而成：

   ```python
   class Persistence(
       CycleMixin, LlmMixin, AnalyticsMixin, ExportMixin, StatsMixin,
       AnomalyMixin, MaintenanceMixin, ConfigMixin, ForecastMixin,
       PerturbationMixin, ComparisonMixin, SolverMixin,
   ):
       ...
   ```

   **公开 API 完全不变** —— 114 个方法、参数名、默认值、返回类型逐个比对一致，
   `from agents.persistence import Persistence` 不用改一行。
   仓库里 100+ 处调用点、web/backend 的 141 个端点、1,600+ 个测试用例
   全部不用动 —— 这是「重构不改变行为」的硬证据。
2. **存储核心只有一份**（`_base.py`）：
   SQLite 连接、schema 初始化、兼容迁移、TTL 读缓存全部收在 `PersistenceBase`，
   所有领域 mixin 继承它 —— 全仓库**仍然只有一个 schema 来源**
   （`_schema.py` 的 `SCHEMA_SQL`），不存在「两个地方建表」的可能。
3. **跨模块依赖显式声明**（`_contract.py`）：
   拆分后有 3 个方法被多个领域复用（`get_cycle_history` / `get_cycle_detail` /
   `get_summary`）。它们被声明成 `SharedQueryMixin` 的签名契约，
   实现仍在各自领域模块里 —— 既不复制代码，也不让类型检查器失去跨模块可见性。
4. **预测数学单独成模块**（`_forecast_math.py`）：
   `_fit_linear` / `_fit_moving_average` / `_fit_exponential_smoothing`
   同时被 analytics 和 llm 两个领域用到，抽成 `ForecastMathMixin` 共享，
   避免「为了拆文件而复制一份算法」。
5. **类级常量跟着领域走**：
   `SEASON_MONTHS` / `SEASON_EMOJI` / `SEASON_NAMES` / `CO2_FACTORS_KG_PER_TON_KM`
   是类属性（不是方法），随 `StatsMixin` 一起搬走 ——
   第一版拆分漏掉它们，测试立刻炸了（`AttributeError: 'Persistence' object has no
   attribute 'SEASON_MONTHS'`），补上后全绿。**这条是这次重构里最值钱的一课：
   测试替我抓到了「人眼审 diff 一定会漏」的那类错误。**

### 拆分方法（怎么保证不引入行为变化）

没有手写搬运，用 AST 机械切分：

- 用 `ast` 解析原文件，逐个方法取**原始行区间**（含装饰器与前置注释横幅），
  原样搬到新模块的类体里 —— 不做任何重排版（`textwrap.dedent` 差点把
  docstring 里缩进两格的列表项搞坏，这次踩过，改成「原样切片」）。
- 每个模块的 import 按 **AST 名字解析**算出来（Load 出来的全局名 −
  方法内绑定的局部名 − 内置名），不是正则猜的。
- 切完做**双向 API 比对**：老类的 114 个方法 vs 新类的 114 个方法，
  方法名、参数名、参数默认值逐个对比 → 0 差异。

## 备选方案

- **不动它，只是把文件拆成几段（物理切分，类还是一个类）** —— 否掉：
  一个 114 方法的类跨几个文件仍然要跨文件跳转，问题没解决；
  而且 Python 不支持把类分段写进多个文件，除非用 mixin，那不如一次做对。
- **拆成多个独立的类（`CycleStore` / `StatsStore` / `ExportStore`），
  由 `Persistence` 转发** —— 否掉：会引入一层转发，
  调用点从 `db.get_cycle_detail(...)` 变成 `db.cycles.get_cycle_detail(...)`，
  **公开 API 变了** —— 141 个后端端点和 1,600+ 测试全要改，
  「重构」变成「重写」，风险与收益完全不成比例。
- **用代码生成器拼出一个大文件（单一真源 + 生成物）** —— 否掉：
  仓库里会同时存在「源」和「生成物」，评审者不知道该读哪个；
  而且单文件 1 万行这个事实没变，只是换了个生成方式。
- **保留 `agents/persistence.py`，另开新包，两边并存一段时间** —— 否掉：
  两份 schema、两份连接逻辑，迟早出现「读 A 写 B」的诡异 bug；
  而且迁移期是纯负担 —— 本次 API 比对证明**一次性切换**是安全的。
- **推到以后再说（和 ADR-0005 的原判断一样）** —— 否掉：
  债不会自己消失，只会在下一个迭代里被复制粘贴放大；
  而这次成本实测只有半天（其中 3.5 分钟是跑测试）。

## 后果

**正面**

- **最大文件从 9,949 行降到 1,271 行**（`_solver.py`），
  平均每个模块 400-600 行，最大的一块（`_stats.py` 3,879 行 / 28 个方法）
  仍是同类聚合、可读可改。
- **公开 API 零变化**：114 个方法、参数、默认值全等；调用点一行没改。
- **类型检查面变大**：mypy 从 16 个文件涨到 32 个文件，
  跨模块调用有了显式契约 —— 以前 `self.get_cycle_detail()` 这种跨域调用
  藏在 1 万行里没人看得见，现在写在 `_contract.py` 里。
- **schema 单一来源更明确**：建表语句集中在 `_schema.py`，
  领域模块想加表必须回到这里 —— 顺带解决了「一个文件里建表 + 迁移混在一起」的问题。
- **仓库的「工程能力」叙事闭环**：ADR-0003（SDK 债）→ ADR-0005（持久化债）
  → ADR-0008（SDK 债已还）→ ADR-0009（持久化债已还），
  **ADR 里写的债，一条条被还掉了** —— 这本身就是 FDE 最想看到的东西：
  「这个人的工程判断是有闭环的」。

**负面 / 技术债**

- **MRO 变深了**：`Persistence` 的 MRO 现在是 17 个类
  （12 个领域 mixin + PersistenceBase + ForecastMathMixin + SharedQueryMixin + object）。
  好处是职责清晰，代价是「这个方法到底从哪来」需要看一眼 MRO。
  缓解手段：`_contract.py` 把跨域契约集中写出来，不靠翻文件猜。
- **Mixin 不是万能解药**：如果将来某个领域要独立部署或独立测试，
  mixin 就不合适了，得换成真正的组合（持有 `CycleStore` 实例）。
  现在还没有这个需求，所以不做 —— **别为假想的未来提前付复杂度成本**。
- **`_stats.py` 还有 3,879 行**，是剩下最大的一块：28 个统计方法挤在一起。
  下一步可以按「车辆 / 材料 / 留存 / 碳排」再切一刀，但收益递减，
  本次先不做（避免为了拆分而拆分）。
- 迁移期间的 `git blame` 会因为整体搬移而失效（历史仍然在，
  但逐行追溯要配合 `git log --follow`）；**这是所有大搬移重构的共同代价**，
  一次付清比反复搬多次好。

---

## 验证记录（2026-11-03）

- **公开 API 双向比对**：老类 114 个方法 vs 新类 114 个方法
  → 方法名 0 缺失 0 多余，参数名/默认值 0 差异
- **本地全量 fast 套件**：**1649 passed / 7 skipped / 122 deselected(slow)**
  → **0 failed**（含 `tests/test_mypy_clean.py` 的 mypy 零错误门禁）
- **flake8 严格子集**（CI 同款 `--select=E9,F63,F7,F82`）：**0 error**
- **flake8 全量风格统计与拆分前逐项对齐**（C901 27 / E127 9 / E128 4 / E131 3 /
  E261 1 / E301 1 / F541 16 / F841 2）→ **新增 0**，全部是原文件自带的历史问题
- **mypy**（CI 同款参数）：**Success: no issues found in 32 source files**（拆分前 16）
- 真实导入冒烟：`Persistence('/tmp/x.db')` 正常初始化，
  `SEASON_MONTHS` / `CO2_FACTORS_KG_PER_TON_KM` 等类级常量取用正常

---

_相关：[ADR-0005](0005-persistence-sqlite.md) 持久化与 schema · [ADR-0007](0007-frontend-and-deployment.md) 后端与部署_
