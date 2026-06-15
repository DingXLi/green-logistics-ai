# 🚀 快速开始指南 / Quick Start Guide

## 🌐 English Summary

This is a 5-minute quickstart for the **Green Logistics AI** project — a multi-agent system (Google ADK + Gemini Flash) that solves a multi-objective Vehicle Routing Problem (OR-Tools) for Swedish circular-economy waste recycling. It exposes a FastAPI backend and a React/Leaflet frontend. To get up and running: install Python deps in a virtualenv, install npm deps in `web/frontend/`, set your `GOOGLE_API_KEY` in `.env`, run `python -m agents.coordinator` to see the multi-agent demo, then `uvicorn web.backend.main:app` for the API and `npm run dev` in `web/frontend/` for the UI. A 30-day simulation lives at `python scripts/run_month.py`. The data-quality checker (`python scripts/data_quality.py`) reports KPI variability, schema completeness, and solver health.

For full step-by-step instructions including troubleshooting, continue in Chinese below.

## 1. 环境准备 / Environment

### 系统要求 / System requirements
- Python 3.11+
- Node.js 18+ (前端开发 / frontend dev)
- Docker & Docker Compose (可选 / optional, for containerised deployment)

### 安装依赖

```bash
# 进入项目目录
cd green-logistics-ai

# 创建 Python 虚拟环境
python -m venv venv

# 激活虚拟环境
source venv/bin/activate  # Linux/Mac
# 或：venv\Scripts\activate  # Windows

# 安装 Python 依赖
pip install -r requirements.txt

# 安装前端依赖 (可选)
cd web/frontend
npm install
cd ../..
```

## 2. 配置 API Keys

### Google Gemini (必需 - 免费)

1. 访问 [Google AI Studio](https://aistudio.google.com/app/prompts/new_chat)
2. 点击 "Get API key"
3. 复制 API key

```bash
export GOOGLE_API_KEY="your-google-api-key-here"
```

### NVIDIA (可选 - 用于 DataDesigner)

```bash
export NVIDIA_API_KEY="your-nvidia-api-key-here"
```

## 3. 运行方式

### 方式 A: Docker (推荐)

```bash
# 启动所有服务
docker-compose up -d

# 查看日志
docker-compose logs -f

# 停止服务
docker-compose down
```

访问：
- 前端：http://localhost:3000
- 后端 API: http://localhost:8000
- API 文档：http://localhost:8000/docs

### 方式 B: 本地运行

```bash
# 终端 1: 启动后端
python -m web.backend.main

# 终端 2: 启动前端 (可选)
cd web/frontend
npm run dev
```

### 方式 C: 测试智能体系统

```bash
# 运行多智能体协调器测试
python -m agents.coordinator

# 运行 VRP 求解器测试
python -m optimization.vrp_solver

# 运行数据生成器测试
python -m synthetic.data_generator

# 运行 OSM 加载器测试
python -m data.osm_loader
```

## 4. 验证安装

### 检查后端健康状态

```bash
curl http://localhost:8000/health
```

预期响应：
```json
{
  "status": "healthy",
  "timestamp": "2026-04-13T..."
}
```

### 测试 API 端点

```bash
# 获取系统状态
curl http://localhost:8000/api/status

# 获取车队状态
curl http://localhost:8000/api/fleet

# 运行优化
curl -X POST http://localhost:8000/api/optimize \
  -H "Content-Type: application/json" \
  -d '{"run_simulation": false}'
```

## 5. 项目结构

```
green-logistics-ai/
├── agents/              # 多智能体系统
│   ├── supply_agent.py     # 供应智能体
│   ├── market_agent.py     # 市场智能体
│   ├── logistics_agent.py  # 物流智能体
│   └── coordinator.py      # 协调器 ⭐
├── optimization/        # 优化求解
│   └── vrp_solver.py       # VRP 求解器 ⭐
├── data/              # 地理空间数据
│   └── osm_loader.py       # OSM 数据加载
├── synthetic/         # 合成数据
│   └── data_generator.py   # 数据生成器
├── web/               # Web 应用
│   ├── backend/       # FastAPI 后端
│   └── frontend/      # React 前端
├── config/            # 配置文件
└── requirements.txt   # Python 依赖
```

## 6. 下一步

### 立即可以做的：
1. ✅ 运行 `python -m agents.coordinator` 测试多智能体系统
2. ✅ 访问 http://localhost:8000/docs 查看 API 文档
3. ✅ 修改 `config/settings.yaml` 自定义配置

### 接下来开发：
1. 🗺️ 集成 Leaflet 地图组件（前端）
2. 📊 实现实时数据可视化（图表）
3. 🔌 连接真实的 OSM 路网数据
4. ⚡ 优化 VRP 求解器性能
5. 📱 添加移动端支持

## 7. 常见问题

### Q: OR-Tools 安装失败？
```bash
# 尝试安装特定版本
pip install ortools==9.10.4067
```

### Q: OSMnx 安装失败？
```bash
# 需要先安装 GDAL
# Ubuntu/Debian:
sudo apt-get install libgdal-dev

# macOS:
brew install gdal
```

### Q: 前端无法连接后端？
检查 CORS 配置和 API 地址：
- 后端默认运行在 `http://localhost:8000`
- 前端代理配置在 `web/frontend/vite.config.js`

## 8. 资源链接

- [Google ADK 文档](https://github.com/google/adk-python)
- [OR-Tools VRP 教程](https://developers.google.com/optimization/routing/vrp)
- [OpenStreetMap](https://www.openstreetmap.org)
- [React Leaflet](https://react-leaflet.js.org)

---

有问题随时在 GitHub Issues 提问！
Questions? Please open a GitHub issue.
