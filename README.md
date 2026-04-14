# 🦞 多智能体 AI 系统 - 绿色物流优化

**Multi-Agent AI System for Green Logistics**

瑞典布罗斯大学 (University of Borås) 实习项目

## 📋 项目概述

开发一个多智能体 AI 系统，优化瑞典循环经济中的废料物流，实现：
- ✅ 最大化利润
- ✅ 最小化碳排放
- ✅ 减少空驶运输

## 🏗️ 系统架构

```
┌─────────────────────────────────────────────────────────────┐
│                    Web Dashboard (React)                     │
│              地图可视化 + KPI 展示 + 模拟控制                  │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                   Backend API (FastAPI)                      │
│              请求路由 + 结果缓存 + 数据服务                    │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                  Multi-Agent System (ADK)                    │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐       │
│  │ Supply Agent │  │ Market Agent │  │Logistics Agent│      │
│  │  供应智能体   │  │  市场智能体   │  │  物流智能体    │      │
│  └──────────────┘  └──────────────┘  └──────────────┘       │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│              Optimization Engine (OR-Tools + GPU)            │
│                    VRP 求解器 + 多目标优化                      │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│              Data Layer (Synthetic + OSM)                    │
│    合成数据引擎 + OpenStreetMap + IoT 遥测模拟                 │
└─────────────────────────────────────────────────────────────┘
```

## 📁 目录结构

```
green-logistics-ai/
├── agents/                 # 多智能体系统
│   ├── supply_agent.py     # 供应智能体
│   ├── market_agent.py     # 市场智能体
│   ├── logistics_agent.py  # 物流智能体
│   └── coordinator.py      # 协调器
├── optimization/           # 优化求解
│   ├── vrp_solver.py       # VRP 求解器
│   ├── cost_matrix.py      # 成本矩阵计算
│   └── multi_objective.py  # 多目标优化
├── data/                   # 数据处理
│   ├── osm_loader.py       # OSM 数据加载
│   ├── distance_matrix.py  # 距离矩阵计算
│   └── nodes.py            # 物流节点定义
├── synthetic/              # 合成数据
│   ├── data_generator.py   # 数据生成器
│   ├── iot_simulator.py    # IoT 遥测模拟
│   └── validators.py       # 数据验证
├── web/                    # Web 应用
│   ├── frontend/           # React 前端
│   └── backend/            # FastAPI 后端
├── config/                 # 配置文件
├── tests/                  # 测试
├── docs/                   # 文档
├── requirements.txt        # Python 依赖
├── docker-compose.yml      # Docker 配置
└── README.md
```

## 🚀 快速开始

### 1. 环境准备

```bash
# 克隆项目
cd green-logistics-ai

# 创建虚拟环境
python -m venv venv
source venv/bin/activate  # Linux/Mac
# or: venv\Scripts\activate  # Windows

# 安装依赖
pip install -r requirements.txt
```

### 2. 配置 API Keys

```bash
# Google Gemini (免费)
export GOOGLE_API_KEY="your-key-here"

# NVIDIA (可选，用于 DataDesigner)
export NVIDIA_API_KEY="your-key-here"
```

### 3. 运行

```bash
# 开发模式
docker-compose up -d

# 或直接运行 Python
python -m agents.coordinator
```

## 🛠️ 技术栈

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

## 📊 核心功能

### 1. 地理空间数据建模
- 使用 OpenStreetMap 定义瑞典物流节点
- 计算全量道路距离矩阵
- 节点类型：供应源、破碎厂、需求点、仓库

### 2. 合成数据引擎
- 每日废料供应量模拟
- 需求点动态生成
- IoT 设备遥测数据（位置、负载、碳排放）

### 3. VRP 基线求解
- OR-Tools 实现基础 VRP
- 性能基准对比
- 多目标成本矩阵（成本/距离/碳排放）

### 4. 多智能体协调
- 供应 - 市场 - 物流智能体通信
- 分布式决策
- 实时优化响应

## 📝 开发日志

- **2026-04-13**: 项目初始化，搭建基础框架

## 📚 参考资料

- [Google ADK](https://github.com/google/adk-python)
- [NVIDIA DataDesigner](https://github.com/NVIDIA-NeMo/DataDesigner)
- [Google OR-Tools VRP](https://developers.google.com/optimization/routing/vrp)
- [OpenStreetMap](https://www.openstreetmap.org)

## 👥 团队

- **实习开发者**: [你的名字]
- **导师**: University of Borås - Industrial Engineering and Management
- **AI 助手**: 代码龙虾 🦞

---

_优化物流，减少碳排放，构建可持续的未来_ 🌱
