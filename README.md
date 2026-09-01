---
title: Green Logistics AI
emoji: ♻️
colorFrom: green
colorTo: blue
sdk: docker
app_port: 8000
pinned: true
license: mit
short_description: Multi-agent AI for Swedish circular waste logistics
---

# ♻️ Multi-Agent AI System for Green Logistics Optimization
# 多智能体 AI 系统 - 绿色物流优化

---

## 🌍 English Version

### Overview

A multi-agent AI system for optimizing waste material logistics in the Swedish circular economy.

**Goals:**
- ✅ Maximize profitability
- ✅ Minimize carbon emissions
- ✅ Reduce empty transport runs

### Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Web Dashboard (React)                     │
│              Map Visualization + KPI Display + Sim Control    │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                   Backend API (FastAPI)                      │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                  Multi-Agent System (ADK)                    │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐       │
│  │ Supply Agent │  │ Market Agent │  │Logistics Agent│       │
│  └──────────────┘  └──────────────┘  └──────────────┘       │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│              Optimization Engine (OR-Tools + GPU)             │
│                    VRP Solver + Multi-Objective               │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│              Data Layer (Synthetic + OSM)                    │
│         Synthetic Data Engine + OSM + IoT Telemetry          │
└─────────────────────────────────────────────────────────────┘
```

### Tech Stack

| Category | Technology |
|----------|------------|
| **Agent Framework** | Google ADK |
| **AI Model** | Google Gemini |
| **Optimization** | Google OR-Tools |
| **Web Backend** | FastAPI |
| **Web Frontend** | React + Vite |
| **Maps** | Leaflet/Mapbox |
| **Geospatial** | OSMnx, NetworkX, Geopandas |
| **Synthetic Data** | NVIDIA DataDesigner |
| **Containerization** | Docker, Docker Compose |

### Quick Start

```bash
# Clone
git clone git@github.com:DingXLi/green-logistics-ai.git
cd green-logistics-ai

# Setup venv
python -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Configure API keys
export GOOGLE_API_KEY="your-key-here"

# Run
python -m agents.coordinator
# or: docker-compose up -d
```

### Project Structure

```
green-logistics-ai/
├── agents/                       # Multi-agent system
│   ├── supply_agent.py           # Supply agent (LLM-driven prediction)
│   ├── market_agent.py           # Market agent (LLM-driven demand)
│   ├── logistics_agent.py        # Logistics agent (VRP optimization)
│   ├── coordinator.py            # Cycle coordinator (orchestrates agents)
│   ├── persistence.py            # SQLite persistence + aggregation queries
│   ├── world_builder.py          # Real-facility world builder
│   ├── llm_caller.py             # Gemini wrapper with retry + fallback
│   ├── llm_config.py             # Centralized model config
│   └── clock.py                  # SimClock (sim_day / sim_hour)
├── optimization/                 # Optimization engine
│   ├── vrp_solver.py             # OR-Tools multi-objective VRP + Pareto
│   └── real_distance.py          # OSM via osmnx, Haversine fallback
├── data/                         # Data sources
│   ├── osm_loader.py             # OSM road network loader
│   ├── swedish_waste_stats.py    # SCB / Avfall Sverige baselines
│   ├── real_sweden_facilities.py # 13 real recycling facilities
│   ├── seasonal_adjuster.py      # 12-month seasonal factors
│   ├── external_signals.py       # Eurostat economic indices
│   └── weather_smhi.py           # SMHI weather API
├── synthetic/                    # Synthetic data
│   └── data_generator.py         # IoT telemetry + rush-hour logic
├── web/                          # Web application
│   ├── frontend/                 # React + Vite (code-split tabs)
│   │   └── src/components/Dashboard/
│   │       ├── Dashboard.jsx          # Main dashboard with 4 tabs
│   │       ├── KPISummary.jsx
│   │       ├── KPITimeseries.jsx
│   │       ├── ParetoChart.jsx
│   │       ├── MonthlyEfficiencyChart.jsx
│   │       ├── FleetUtilizationChart.jsx
│   │       ├── MaterialsOverview.jsx
│   │       ├── SeasonalHeatmap.jsx
│   │       ├── SeasonalComparison.jsx
│   │       ├── CarbonScenarios.jsx
│   │       ├── FacilitiesList.jsx
│   │       ├── CycleHistory.jsx       # iter #11: past cycles + detail
│   │       └── SchedulerControl.jsx   # iter #10
│   └── backend/                   # FastAPI + WebSocket
│       └── main.py                    # 1500+ lines, 30+ endpoints
├── tests/                        # 250+ tests
├── requirements.txt              # Python dependencies
├── Dockerfile.backend            # Docker image
├── docker-compose.yml
└── .github/workflows/ci-cd.yml   # CI/CD pipeline
```

### Core Features

1. **Multi-Agent Coordination** - Supply-Market-Logistics agents (Google ADK) for demand prediction, supply offer matching, route optimization
2. **Multi-Objective VRP** - OR-Tools driven Pareto front (cost vs CO₂ vs empty-runs), Pareto scan across alpha weights
3. **Real Swedish Data** - 13 real recycling facilities (Renova, Ragn-Sells, Stena, etc.), population baselines from SCB / Avfall Sverige / Eurostat
4. **OSM Real-Road Distance** - osmnx network distances with automatic Haversine fallback when offline
5. **Seasonal Modeling** - 12-month seasonal factors (summer-peak concrete, winter-peak mixed_waste) injected into every cycle
6. **Carbon Tax Scenarios** - Recompute Pareto under 4 carbon-price scenarios (0 / 500 / 1500 / 3000 SEK/tCO₂)
7. **SMHI Weather Integration** - Real Swedish weather data via SMHI's open API
8. **External Economic Signals** - Eurostat construction / industrial production indices affect cycle KPIs
9. **Realistic IoT Telemetry** - GPS paths, rush-hour speed, load-emission correlation, moisture sensors
10. **WebSocket Live Updates** - Dashboard streams cycle_update events with fleet / efficiency / distance_source metrics. Optional **Origin allowlist** via `GL_WS_ALLOWED_ORIGINS` env var (iter #27 security). **Max-client guard** via `GL_WS_MAX_CLIENTS` (default 50) + per-IP limit `GL_WS_MAX_PER_IP` (default 10) (iter #32). **Admin auth** for `/api/ws/stats*` and all `/api/admin/*` + `/api/debug/*` via `GL_ADMIN_TOKEN` (iter #33, iter #34) — supports `Authorization: Bearer <token>` or `X-Admin-Token: <token>`, with timing-safe compare.
11. **LLM-Driven Decisions** - Optional Gemini integration for demand/supply prediction; deterministic fallback when API key absent
12. **Background Scheduler** - Cron-style scheduler (start/stop/restart via API); warmup cycle on first start
13. **Persistence + Analytics** - SQLite-backed cycle storage; 10+ aggregation endpoints (KPI / fleet / monthly / seasonal / Pareto history)
14. **Code-Split Frontend** - Lazy-loaded dashboard tabs; main bundle ~34 kB
15. **CI/CD** - GitHub Actions pytest + flake8 + Docker build on every push; HuggingFace Spaces deployment

### API Endpoints (30+)

| Method | Path | Description |
|--------|------|-------------|
| GET    | `/health` | Service health + production metadata |
| POST   | `/api/optimize` | Single optimization cycle |
| POST   | `/api/optimize/pareto` | Multi-objective Pareto scan (cost vs CO₂) |
| POST   | `/api/optimize/carbon-scenarios` | Recompute under 4 carbon prices |
| GET    | `/api/optimize/last` | Last cycle result (cached on coordinator) |
| GET    | `/api/facilities` | Real Swedish recycling facilities |
| GET    | `/api/materials` | Material metadata (kt/year, seasonal pattern) |
| GET    | `/api/fleet` | Live fleet status (vehicles, util, distance) |
| GET    | `/api/seasonal-factors` | 12-month seasonal factor table |
| GET    | `/api/persistence/recent-cycles` | Last N cycles (raw) |
| GET    | `/api/persistence/summary` | Global stats |
| GET    | `/api/persistence/efficiency-metrics` | cost_per_ton / co2_per_ton aggregates |
| GET    | `/api/persistence/kpi-timeseries` | Per-sim_day KPI series |
| GET    | `/api/persistence/fleet-timeseries` | Per-sim_day fleet series |
| GET    | `/api/persistence/seasonal-timeseries` | Per-month seasonal series |
| GET    | `/api/persistence/llm-cost-timeseries` | Per-sim_day LLM usage series (iter #28) |
| GET    | `/api/persistence/llm-cost-forecast` | LLM usage/cost forecast with 3 methods (iter #29) |
| GET    | `/api/persistence/forecast?method=linear\|moving_average\|exponential_smoothing` | KPI forecast with method (iter #28) |
| GET    | `/api/persistence/forecast/multi?methods=linear,ma,es` | Multi-method forecast comparison (iter #28) |
| GET    | `/api/persistence/forecast-confidence` | Ensemble forecast confidence + method dispersion (iter #30) |
| GET    | `/api/persistence/monthly-efficiency-trend` | Per-month efficiency trend |
| GET    | `/api/persistence/cycle-history` | Past cycles with filters (iter #11) |
| GET    | `/api/persistence/cycle-detail/{id}` | Single cycle full breakdown (iter #11) |
| GET    | `/api/persistence/match-distance-stats` | Match distance buckets + avg/median (iter #15) |
| GET    | `/api/persistence/supply-aggregates` | Per-supply cumulative KPIs (iter #15) |
| GET    | `/api/persistence/material-aggregates` | Per-material-type cumulative KPIs (iter #16) |
| GET    | `/api/persistence/cycle-kpi-summary` | Overall KPI rollup + best/worst/last cycle (iter #16 + iter #17 time window filters) |
| GET    | `/api/persistence/supply-cohort-retention` | Supply retention analysis: one-time vs repeating (iter #17) |
| GET    | `/api/persistence/export/cycles.csv` | Download cycles as CSV (iter #11) |
| GET    | `/api/persistence/export/supplies.csv` | Download supplies as CSV (iter #17) |
| GET    | `/api/persistence/export/matches.csv` | Download matches as CSV (iter #17) |
| GET    | `/api/persistence/export/routes.csv` | Download routes as CSV (iter #17) |
| GET    | `/api/persistence/export/cycles.parquet` | Download cycles as Apache Parquet (iter #27) |
| GET    | `/api/persistence/export/supplies.parquet` | Download supplies as Apache Parquet (iter #27) |
| GET    | `/api/persistence/export/matches.parquet` | Download matches as Apache Parquet (iter #27) |
| GET    | `/api/persistence/export/routes.parquet` | Download routes as Apache Parquet (iter #27) |
| GET    | `/api/admin/db-stats` | SQLite DB size, table counts, indexes (iter #15). Admin auth via `GL_ADMIN_TOKEN` (iter #34). |
| POST   | `/api/admin/db-maintenance` | VACUUM + ANALYZE (iter #16). Admin auth via `GL_ADMIN_TOKEN` (iter #34). |
| GET    | `/api/facilities/distance-matrix` | N×N facility distance matrix (iter #15) |
| POST   | `/api/optimize/batch` | Parallel multi-scenario optimization (iter #13) |
| GET    | `/api/health/deep` | Multi-subsystem health check (iter #14) |
| GET    | `/api/scheduler/status` | Background scheduler status |
| POST   | `/api/scheduler/control` | start / stop / restart scheduler |
| WS     | `/ws` | WebSocket: cycle_update + fleet metrics (iter #27 origin allowlist) |
| GET    | `/api/ws/stats` | WebSocket connection stats (peak / accepted / rejected / IP distribution / avg duration) + allowlist metadata (iter #27, iter #32). Admin auth via `GL_ADMIN_TOKEN` (iter #33). |
| GET    | `/docs` | Swagger UI |

### Deployment

```bash
# HuggingFace Spaces (Docker)
git push hf main
# App: https://huggingface.co/spaces/lidingx/Green-logistics

# Local Docker
docker build -f Dockerfile.backend -t green-logistics-backend .
docker run -p 8000:8000 green-logistics-backend
```

### References

- [Google ADK](https://github.com/google/adk-python)
- [NVIDIA DataDesigner](https://github.com/NVIDIA-NeMo/DataDesigner)
- [Google OR-Tools VRP](https://developers.google.com/optimization/routing/vrp)
- [OpenStreetMap](https://www.openstreetmap.org)

---

## 🇨🇳 中文版

### 项目概述

用于优化瑞典循环经济中废料物流的多智能体 AI 系统。

**核心目标：**
- ✅ 最大化利润
- ✅ 最小化碳排放
- ✅ 减少空驶运输

### 系统架构

```
┌─────────────────────────────────────────────────────────────┐
│                    Web Dashboard (React)                     │
│                  地图可视化 + KPI 展示 + 模拟控制              │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                   Backend API (FastAPI)                      │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                  Multi-Agent System (ADK)                   │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐       │
│  │ 供应智能体    │  │  市场智能体   │  │  物流智能体   │       │
│  └──────────────┘  └──────────────┘  └──────────────┘       │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│              Optimization Engine (OR-Tools + GPU)           │
│                    VRP 求解器 + 多目标优化                    │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│              Data Layer (Synthetic + OSM)                   │
│         合成数据引擎 + OpenStreetMap + IoT 遥测              │
└─────────────────────────────────────────────────────────────┘
```

### 技术栈

| 类别 | 技术 |
|------|------|
| **智能体框架** | Google ADK |
| **AI 模型** | Google Gemini |
| **优化求解** | Google OR-Tools |
| **Web 后端** | FastAPI |
| **Web 前端** | React + Vite |
| **地图可视化** | Leaflet/Mapbox |
| **地理空间** | OSMnx, NetworkX, Geopandas |
| **合成数据** | NVIDIA DataDesigner |
| **容器化** | Docker, Docker Compose |

### 快速开始

```bash
# 克隆项目
git clone git@github.com:DingXLi/green-logistics-ai.git
cd green-logistics-ai

# 创建虚拟环境
python -m venv venv
source venv/bin/activate  # Linux/Mac
# 或: venv\Scripts\activate  # Windows

# 安装依赖
pip install -r requirements.txt

# 配置 API Keys
export GOOGLE_API_KEY="your-key-here"

# 运行
python -m agents.coordinator
# 或: docker-compose up -d
```

### 目录结构

```
green-logistics-ai/
├── agents/                       # 多智能体系统
│   ├── supply_agent.py           # 供应智能体 (LLM 预测)
│   ├── market_agent.py           # 市场智能体 (LLM 需求)
│   ├── logistics_agent.py        # 物流智能体 (VRP 优化)
│   ├── coordinator.py            # 协调器
│   ├── persistence.py            # SQLite 持久化 + 聚合查询
│   ├── world_builder.py          # 真实设施世界构建
│   ├── llm_caller.py             # Gemini wrapper + fallback
│   ├── llm_config.py             # 模型配置中心
│   └── clock.py                  # SimClock
├── optimization/                 # 优化求解
│   ├── vrp_solver.py             # OR-Tools 多目标 VRP + Pareto
│   └── real_distance.py          # OSM + Haversine fallback
├── data/                         # 数据源
│   ├── osm_loader.py             # OSM 路网加载
│   ├── swedish_waste_stats.py    # SCB / Avfall Sverige 基线
│   ├── real_sweden_facilities.py # 13 个真实废料设施
│   ├── seasonal_adjuster.py      # 12 月 seasonal factor
│   ├── external_signals.py       # Eurostat 经济指数
│   └── weather_smhi.py           # SMHI 天气 API
├── synthetic/                    # 合成数据
│   └── data_generator.py         # IoT 遥测 + 高峰时段逻辑
├── web/                          # Web 应用
│   ├── frontend/                 # React + Vite (代码分割)
│   │   └── src/components/Dashboard/
│   │       ├── Dashboard.jsx          # 主 Dashboard (4 tabs)
│   │       ├── KPISummary.jsx
│   │       ├── KPITimeseries.jsx
│   │       ├── ParetoChart.jsx
│   │       ├── MonthlyEfficiencyChart.jsx
│   │       ├── FleetUtilizationChart.jsx
│   │       ├── MaterialsOverview.jsx
│   │       ├── SeasonalHeatmap.jsx
│   │       ├── SeasonalComparison.jsx
│   │       ├── CarbonScenarios.jsx
│   │       ├── FacilitiesList.jsx
│   │       ├── CycleHistory.jsx       # iter #11: 周期历史
│   │       └── SchedulerControl.jsx   # iter #10
│   └── backend/                   # FastAPI + WebSocket
│       └── main.py                    # 1500+ 行, 30+ endpoints
├── tests/                        # 250+ 测试
├── requirements.txt              # Python 依赖
├── Dockerfile.backend            # Docker 镜像
├── docker-compose.yml
└── .github/workflows/ci-cd.yml   # CI/CD 流水线
```

### 核心功能

1. **多智能体协调** - Supply / Market / Logistics 智能体 (Google ADK) 处理需求预测、供应匹配、路径优化
2. **多目标 VRP 求解** - OR-Tools Pareto 前沿 (cost / CO₂ / 空驶), alpha 权重扫描
3. **真实瑞典数据** - 13 个真实废料设施 (Renova / Ragn-Sells / Stena 等), SCB / Avfall Sverige / Eurostat 人均基线
4. **OSM 真实道路距离** - osmnx 路网距离 + 离线时自动回退 Haversine
5. **季节性建模** - 12 个月 seasonal factor (夏季高峰混凝土 / 冬季高峰混合废料) 注入每个 cycle
6. **碳税情景** - 在 4 个碳价下 (0 / 500 / 1500 / 3000 SEK/tCO₂) 重算 Pareto
7. **SMHI 天气集成** - 通过 SMHI 开放 API 读取真实瑞典天气
8. **外部经济信号** - Eurostat 建筑 / 工业生产指数影响 cycle KPI
9. **真实 IoT 遥测** - GPS 路径、高峰时段速度、载荷-排放相关性、湿度传感器
10. **WebSocket 实时推送** - Dashboard 流式接收 cycle_update (fleet / efficiency / distance_source)
11. **LLM 驱动决策** - 可选 Gemini 集成 (需求/供应预测); 无 API key 时降级到确定性算法
12. **后台调度器** - Cron 风格调度器 (API start/stop/restart); 首次启动预热 1 cycle
13. **持久化 + 分析** - SQLite cycle 存储; 10+ 聚合 endpoint (KPI / fleet / 月度 / 季节 / Pareto 历史)
14. **代码分割前端** - Dashboard tab 懒加载; 主 bundle ~34 kB
15. **CI/CD** - GitHub Actions pytest + flake8 + Docker build; HuggingFace Spaces 部署

### 部署

```bash
# HuggingFace Spaces (Docker)
git push hf main
# 应用: https://huggingface.co/spaces/lidingx/Green-logistics

# 本地 Docker
docker build -f Dockerfile.backend -t green-logistics-backend .
docker run -p 8000:8000 green-logistics-backend
```

### 参考资料

- [Google ADK](https://github.com/google/adk-python)
- [NVIDIA DataDesigner](https://github.com/NVIDIA-NeMo/DataDesigner)
- [Google OR-Tools VRP](https://developers.google.com/optimization/routing/vrp)
- [OpenStreetMap](https://www.openstreetmap.org)

---

_优化物流，减少碳排放，构建可持续的未来_ 🌱