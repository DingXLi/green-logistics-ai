# ADR-0005: 持久化 —— SQLite + 11 张表（单一建表来源），并承认 persistence.py 已经太大

- **状态**：已接受（原技术债已于 2026-11-03 由 [ADR-0009](0009-persistence-package-split.md) 还清）
- **记录日期**：2026-11-01
- **相关代码**：`agents/persistence.py`, `.gitignore`, `scripts/seed_ci_fixture.py`

## 背景

每个仿真 cycle 都会产生一批数据：KPI、供应报价、需求请求、供需匹配、车辆路线、
LLM 决策、预测结果、季节性扰动、运行时配置…… 这些数据要满足：

1. **可回溯** —— 客户拿着上周的报表问「这条路线为什么这么排」，要能翻出当时的输入与决策；
2. **可聚合** —— 要能按天/按区域/按方法算趋势（成本、CO₂、利用率、命中率的走势）；
3. **可复现** —— 干净环境重跑要得到同样的库（这条直接催生了 CI 的 fixture 方案，见 ADR-0006）。

## 决策

**用标准库 SQLite（`sqlite3`）单文件存 `data/simulation.db`，表结构集中在 `Persistence._init_schema()` 一处。**

### 11 张表

| 表 | 内容 |
|---|---|
| `optimization_cycles` | 每个 cycle 的 KPI（成本 / CO₂ / 利用率 / 命中数） |
| `supply_offers` | 供应点报价与容量 |
| `demand_requests` | 需求请求 |
| `matches` | 供需匹配结果 |
| `routes` | 车辆路线与里程 |
| `llm_decisions` | LLM 判断与 fallback（含 raw_json、confidence、source） |
| `forecast_method_prefs` | 各指标下「当前最佳预测方法」的持久化 |
| `seasonal_perturbations` | 季节性扰动配置 |
| `forecast_predictions` | 预测点（供 r² / 置信度拟合） |
| `db_maintenance_log` | 维护操作日志（VACUUM / 清理） |
| `runtime_config` | 运行时可改配置 |

### 三条硬规矩

1. **schema 单一来源** —— 建表 SQL 只写在 `Persistence._init_schema()` 里，
   任何地方都不许抄第二份 `CREATE TABLE`（连 CI fixture 也复用它建表，见 ADR-0006）。
2. **运行数据不进仓库** —— `data/*.db` 进 `.gitignore`；仓库里只留 schema 与代码。
3. **每张表按查询维度建索引** —— 例如 `llm_decisions` 建了 `cycle_id` / `sim_day` / `decision_type` 三个索引，
   因为这三个是 dashboard 最常用的筛选条件。

## 备选方案

- **PostgreSQL** —— 并发与规模都更好。否掉的原因：要额外起服务、要运维权限，
  对单机仿真 + 单人 demo 是净增成本；FDE 交付现场「clone 就能跑」比「能扛百万 QPS」重要。
- **Parquet + DuckDB** —— 分析型查询更快。否掉的原因：不适合「每 cycle 写一行 + 事务更新 +
  按条件查最近 N 条」这种 OLTP 式写法，而后者才是这个场景的主流操作。
- **只写 JSON / JSONL 文件** —— 零依赖、最直观。否掉的原因：没法做聚合查询，
  KPI 趋势只能全量读进内存算，文件多了还会并发写坏。

## 后果

**正面**

- 零外部服务：`python -m agents.coordinator` 就能跑完并留下完整数据。
- SQLite 是事务安全的 —— 上千 cycle 跑到一半断电，库不会写坏。
- 可以直接用 SQL 算 KPI 趋势（dashboard 和评估报告都吃这份数据）。

**负面 / 技术债**

- ~~⚠️ **`agents/persistence.py` 已经涨到 9,949 行 / 423 KB 单文件**~~
  → **已解决（2026-11-03，见 ADR-0009）**：已拆成 `agents/persistence/` 包，
  按领域分 16 个模块（`_base` / `_schema` / `_contract` / `_forecast_math` + 12 个领域 mixin），
  最大文件降到 1,271 行；**公开 API 114 个方法零变化**，schema 仍是单一来源
  （`_schema.py` 的 `SCHEMA_SQL`）。当时的顾虑「不适合在赶 deadline 的冲刺期里做」
  被证明是对的 —— 拆分本身半天，但**必须有 1,600+ 测试兜底**才敢动。
- 单文件 SQLite 的写入是串行的，并发写会排队（当前用单进程顺序跑 cycle，尚可）。
- 没有连接池与迁移工具（alembic 之类），schema 变更靠 `CREATE TABLE IF NOT EXISTS` + 手工 ALTER。

---

_相关：[ADR-0004](0004-llm-resilience-and-observability.md) 决策落库 · [ADR-0006](0006-ci-strategy-and-deterministic-fixture.md) CI fixture · [ADR-0009](0009-persistence-package-split.md) 持久化层拆分_
