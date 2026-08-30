# Technical Report — Green Logistics AI
**Date:** 2026-08-30 (iter #16-#21)
**Project:** Multi-Agent AI System for Swedish Circular Waste Logistics
**Repo:** `/home/liding/.openclaw/workspace-coder/green-logistics-ai`
**Deploy:** `https://huggingface.co/spaces/lidingx/Green-logistics`

---

## 1. 系统概述

### 1.1 目标
为瑞典循环物流 (circular waste logistics) 提供**多智能体协同优化**系统, 同时:
- 最大化利润 (profit)
- 最小化碳排放 (CO₂)
- 减少空驶 (empty transport runs)

### 1.2 核心架构
```
┌─────────────────────────────────────────────────────────────┐
│              Web Dashboard (React 18 + Vite)                 │
│      Map (Leaflet) + Recharts + WS client + KPI cards      │
└─────────────────────────────────────────────────────────────┘
                            │ WebSocket + REST
┌─────────────────────────────────────────────────────────────┐
│             FastAPI Backend (port 8000)                    │
│   45+ API endpoints · WS · CORS · perf middleware         │
└─────────────────────────────────────────────────────────────┘
                            │
┌─────────────────────────────────────────────────────────────┐
│       Multi-Agent Coordinator (async Python)               │
│  ┌────────────┐  ┌────────────┐  ┌────────────┐            │
│  │ Supply     │  │ Market     │  │ Logistics  │            │
│  │ Agent      │  │ Agent      │  │ Agent      │            │
│  └────────────┘  └────────────┘  └────────────┘            │
│         ↓ LLM (Gemini 2.5-flash) ↓                          │
│  ┌──────────────────────────────────────────┐              │
│  │ OR-Tools VRP Solver (Pareto + carbon)    │              │
│  └──────────────────────────────────────────┘              │
└─────────────────────────────────────────────────────────────┘
                            │
┌─────────────────────────────────────────────────────────────┐
│        Data Layer (SQLite + 6 tables)                       │
│  optimization_cycles · supply_offers · demand_requests       │
│  matches · routes · llm_decisions                            │
└─────────────────────────────────────────────────────────────┘
                            │
┌─────────────────────────────────────────────────────────────┐
│        External Data Sources                                 │
│  SMHI (weather) · SCB (Sweden stats) · OSM (real roads)     │
│  Eurostat (economic signals) · Avfall Sverige (waste data)  │
└─────────────────────────────────────────────────────────────┘
```

---

## 2. 技术栈

### 2.1 后端 (Python 3.11+ / 3.13)
| 库 | 用途 | 版本 |
|---|---|---|
| **FastAPI** | REST API + WebSocket | ≥0.110 |
| **uvicorn** | ASGI server | ≥0.29 |
| **pydantic** | Data validation | ≥2.6 |
| **google-generativeai** | LLM (Gemini 2.5-flash) | ≥0.8 |
| **google-adk** | Agent Development Kit | ≥0.1 |
| **ortools** | VRP solver (Pareto front + carbon) | ≥9.10 |
| **sqlite3** (stdlib) | Persistence layer | built-in |
| **loguru** | Structured logging | latest |
| **osmnx** | OpenStreetMap data | ≥1.9 |
| **networkx** | Graph algorithms | ≥3.2 |
| **pandas / numpy / scipy** | Data wrangling + numerics | latest |

### 2.2 前端 (React 18)
| 库 | 用途 |
|---|---|
| **React 18** | UI framework |
| **Vite 5** | Build tool (5.4.21) |
| **Leaflet + react-leaflet** | Interactive map |
| **Recharts** | Charts (line, bar, scatter) |
| **React Router 6** | Routing |

### 2.3 DevOps
- **HF Spaces** (Docker) — production deploy
- **GitHub Actions** — pytest + flake8 + mypy + security scan
- **conftest.py** — local pytest path setup (iter #16)

---

## 3. 代码规模

### 3.1 数字 (2026-08-30 01:59 GMT+2)

| 指标 | 数值 |
|---|---|
| Python files | **70** (.py excluding venv) |
| JSX files | **23** (.jsx excluding node_modules) |
| Test files | **40** (test_*.py) |
| **Python LOC** | **21,134** (production code) |
| **JSX LOC** | **3,847** (frontend code) |
| **Test LOC** | **9,113** |
| **API endpoints** | **45+** (45 in main.py + WebSocket + WebSocket routes) |
| **Commits ahead of hf/main** | **130** (main领先 origin 130 vs 28) |

### 3.2 数据库 schema (6 tables)
```sql
optimization_cycles  -- 1 行/周期 (KPI + seasonal)
supply_offers       -- 1 行/供应点/周期
demand_requests      -- 1 行/需求点/周期
matches              -- 1 行/匹配/周期
routes               -- 1 行/车辆路径/周期 (含 stops_json)
llm_decisions        -- 1 行/LLM 决策 (含 raw_json)
```
8 个 custom indexes · 14 columns with seasonal_factor_avg + seasonal_month

---

## 4. 关键功能模块

### 4.1 多智能体协同 (iter #6+)
- **SupplyAgent** — 库存积累 + LLM-driven 预测 (predict_supply_batch)
- **MarketAgent** — 需求分析 + 匹配 (LLM-driven demand_prediction)
- **LogisticsAgent** — 车队调度 + Pareto 多目标
- **MultiAgentCoordinator** — async 调度 + 周期管理

### 4.2 优化求解 (iter #12+)
- **VRPSolver** (OR-Tools) — 多目标 Pareto front (cost vs CO₂)
- **carbon_scenarios** — 4 个碳价 (0 / 1.5 / 3 / 5 SEK/kg) 重新计算
- **batch_optimize** — 并行多场景 (1-8 scenarios)
- **real_distance** (iter #8+) — OSM 真实路网 (osmnx)

### 4.3 持久化 + 分析 (iter #11-#20)
**SQLite-backed cycle 存储**, 45+ aggregate endpoints:
- 时间窗口 filter: last_n / since_sim_day / until_sim_day
- 聚合 KPI: cycle / supply / material / match / route
- 留存分析: cohort retention (total + by_period)
- CSV export: 4 tables × {csv, json, ndjson} × gzip
- DB metadata: size / checksum (md5) / version / vacuum
- Performance: response time tracking (X-Perf-Time-Ms)

### 4.4 实时通信
- `/ws` WebSocket — cycle_update + fleet metrics (广播)
- 跨 tab 共享: BroadcastChannel + leader/follower pattern
- 自动重连 + 状态指示 (WSStatusIndicator)

### 4.5 数据集成
- **SMHI** 天气 API (SNOW1gv1 new API)
- **Eurostat** 经济指标 (construction + industrial production)
- **OSM** 真实路网距离 (via osmnx + fallback to haversine)
- **SCB / Eurostat / Avfall Sverige** 6 种废料 material metadata
- **Seasonal adjuster** 12-month factor (summer_peak / winter_peak / stable)

---

## 5. 性能 / 可观测性

### 5.1 Performance monitoring middleware (iter #21)
每个 response:
- `X-Perf-Time-Ms: <ms>` header
- 内部 ring buffer (最近 100 次 per endpoint)
- Thread-safe (`_PERF_LOCK`)

API: `GET /api/admin/perf-stats?top=N`
- total_requests / total_errors / error_rate_pct
- endpoints: `[{endpoint, n_calls, avg/min/max/p50/p95/p99/last_ms}, ...]`
- 按 avg_ms DESC 排序 (top N 最慢)

实测 (HF Space):
- `/health`: ~5-10ms
- `/api/admin/db-stats`: ~20ms
- `/api/admin/perf-stats`: ~5ms
- `/api/optimize` (单 cycle): 30-35s (LLM call 主导)
- `/api/optimize/pareto?n_points=3`: 8-12s

### 5.2 Health monitoring
- `/health` — basic liveness
- `/api/health/deep` — 6 subsystems (DB / LLM / OSM / WS / cache / etc.)

---

## 6. 测试覆盖

### 6.1 统计
- **187+ tests passing** (across 40 test files)
- **mypy 0 errors** (15 files in CI)
- **flake8 fatal errors: 0** (style warnings allowed)
- **coverage: ~39%** (target 35%)
- **smoke test 24/24 pass** on HF Space

### 6.2 测试金字塔
```
unit tests     ████████████████ 70%   (187 tests)
integration    ████████       20%   (API + WS)
e2e (smoke)    ████            10%   (24 HF endpoint checks)
```

### 6.3 CI (GitHub Actions)
```yaml
1. flake8 (E9/F63/F7/F82 fatal + style max-complexity 10)
2. mypy (15 files, ignore-missing-imports, explicit-package-bases)
3. Security scan (pip-audit + heuristic fallback)
4. pytest + coverage (target 35%, fail under 35%)
```

---

## 7. 安全 / 合规

### 7.1 安全扫描 (iter #16)
- `scripts/security_scan.sh` — pip-audit (preferred) + heuristic fallback
- 6 known CVE patterns (python-multipart, jinja2, pillow, cryptography, urllib3, requests)
- CI 集成, non-fatal warnings

### 7.2 审计 trail
- LLM raw_json 持久化 (llm_decisions.raw_json) — 所有决策可追溯
- DB md5 (前 100KB) — `/api/admin/db-info` 提供 checksum
- CSV metadata header — `include_metadata=true` 默认开启

### 7.3 环境变量
```bash
GOOGLE_API_KEY      # Gemini API key (optional — deterministic fallback)
GL_DB_PATH          # SQLite path (default /data/simulation.db)
GL_SCHEDULER_ENABLED  # Background scheduler (default false)
GEMINI_MODEL        # Override model (default gemini-2.5-flash)
```

---

## 8. 部署

### 8.1 HF Spaces (Docker)
```dockerfile
FROM python:3.11-slim
RUN apt-get install gcc g++ libgdal-dev libgeos-dev libproj-dev curl
COPY requirements.txt . pip install -r requirements.txt
COPY agents/ optimization/ synthetic/ config/ data/ web/backend/ ./
EXPOSE 8000
HEALTHCHECK CMD curl -fsS http://localhost:8000/health
CMD ["uvicorn", "web.backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
```
- 持久化卷 `/data` (DB + cache 跨重启保留)
- Auto-rebuild on `git push hf main`

### 8.2 本地 Docker
```bash
docker build -f Dockerfile.backend -t green-logistics-backend .
docker run -p 8000:8000 green-logistics-backend
```

### 8.3 Git workflow
```
local main (130 commits) ──push──> hf/main (production)
                │
                └─── ahead of origin/main (diverged 130 vs 28)
```

---

## 9. 5-hour 迭代周期 (iter #16-#21)

### 9.1 自动化迭代结果

| iter | 主要功能 | commits | 测试 |
|---|---|---|---|
| #16 | conftest.py + material-aggregates + cycle-kpi-summary + VACUUM + smoke + security scan + 2 前端 | 6 | 48 |
| #17 | cycle-kpi-summary 时间窗口 + 3 CSV export + supply cohort retention + DbStatsBadge | 5 | 44 |
| #18 | cycle-detail 测试 + unified DB export (5×3) + frontend VACUUM + kpi-timeseries 时间窗口 | 8 | 38 |
| #19 | gzip option + cohort retention by period + CSV metadata header | 3 | 29 |
| #20 | CSV metadata 默认 ON + CohortRetentionByPeriod 前端 + db-info + fix _csv_to_rows | 4 | 38 |
| #21 | perf monitoring middleware + X-Perf-Time-Ms + /perf-stats | 1 | 12 |

**合计:** 27 commits, 209+ 新测试

### 9.2 自动化迭代的 trade-offs
- ✅ 7 项 future work 全部完成 (gzip / 时间窗口 / metadata / 多格式 export / 留存分析 / 前端整合 / 性能监控)
- ✅ 测试覆盖率持续增长 (40 个 test files)
- ⚠️ HF Space first-push 偶发 transient infra issue (exit 128), retry always succeeds
- ⚠️ 复杂 feature (5×3 format dispatch) 需要 revert + retry
- ✅ 紧急 commit 用 `git revert --no-commit` + `--abort` workflow

---

## 10. 已知技术债

### 10.1 Type hints
- 一些 untyped functions (notes only, not errors)
- mypy strict 模式未启用 (因 google-generativeai 等第三方库)

### 10.2 Frontend
- React 18 (考虑升级 19? 评估成本)
- 一些 inline styles 未抽取 (Tailwind 没引入, 维护一致性)

### 10.3 Performance
- LLM call 主导 /api/optimize (30s+) — 考虑 streaming response
- WebSocket broadcast 是 in-memory (单 process) — 多 worker 需要 sticky session

### 10.4 Coverage gap (39%)
- LLM integration tests (`test_llm_integration.py`) — require real API key, 跳过
- Pareto front generation — 15s+, CI skip
- Some edge cases in scheduler control

---

## 11. Roadmap (iter #22+)

按 ROI:
1. **/perf-stats dashboard** — Recharts graph (p95 / p99 trends)
2. **CSV export 加 format=parquet** (columnar, smaller, faster analytics)
3. **/api/persistence/cohort-retention/by-time-window** — 高级 cohort 切分
4. **WebSocket auth** (currently open — production 风险)
5. **Seasonal trend forecast** — predict next sim_day based on history
6. **Multi-region OSM caching** (load + fallback for slow OSM responses)
7. **Frontend URL-based state** (deep linking to specific cycle/dashboard tab)

---

**End of technical report — 2026-08-30 01:59 GMT+2 (iter #21)**
**Total commits since iter #16:** 27
**Total new tests:** 209+
**Current main HEAD:** `c073147` (performance monitoring middleware)
**HF Space HEAD:** `c073147` (verified live)