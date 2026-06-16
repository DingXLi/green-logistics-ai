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
├── agents/                 # Multi-agent system
│   ├── supply_agent.py     # Supply agent
│   ├── market_agent.py     # Market agent
│   ├── logistics_agent.py  # Logistics agent
│   └── coordinator.py      # Coordinator
├── optimization/           # Optimization engine
│   └── vrp_solver.py       # VRP solver
├── data/                   # Data processing
│   └── osm_loader.py       # OSM data loader
├── synthetic/              # Synthetic data
│   └── data_generator.py   # Data generator
├── web/                    # Web application
│   ├── frontend/           # React frontend
│   └── backend/           # FastAPI backend
├── tests/                  # Tests
├── requirements.txt        # Dependencies
└── docker-compose.yml      # Docker config
```

### Core Features

1. **Geospatial Modeling** - OSM-based Swedish logistics network
2. **Synthetic Data Engine** - Supply, demand, IoT telemetry simulation
3. **VRP Baseline** - OR-Tools powered vehicle routing
4. **Multi-Agent Coordination** - Supply-Market-Logistics agent communication

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
├── agents/                 # 多智能体系统
│   ├── supply_agent.py     # 供应智能体
│   ├── market_agent.py     # 市场智能体
│   ├── logistics_agent.py  # 物流智能体
│   └── coordinator.py      # 协调器
├── optimization/           # 优化求解
│   └── vrp_solver.py       # VRP 求解器
├── data/                   # 数据处理
│   └── osm_loader.py       # OSM 数据加载
├── synthetic/              # 合成数据
│   └── data_generator.py   # 数据生成器
├── web/                    # Web 应用
│   ├── frontend/           # React 前端
│   └── backend/            # FastAPI 后端
├── tests/                  # 测试
├── requirements.txt        # Python 依赖
└── docker-compose.yml      # Docker 配置
```

### 核心功能

1. **地理空间建模** - 基于 OSM 的瑞典物流网络
2. **合成数据引擎** - 供应、需求、IoT 遥测模拟
3. **VRP 基线求解** - OR-Tools 驱动的车辆路径优化
4. **多智能体协调** - 供应-市场-物流智能体通信

### 参考资料

- [Google ADK](https://github.com/google/adk-python)
- [NVIDIA DataDesigner](https://github.com/NVIDIA-NeMo/DataDesigner)
- [Google OR-Tools VRP](https://developers.google.com/optimization/routing/vrp)
- [OpenStreetMap](https://www.openstreetmap.org)

---

_优化物流，减少碳排放，构建可持续的未来_ 🌱