# 🔧 调试指南 / Debugging Guide

本文档介绍如何调试 Green Logistics AI 项目。
This document explains how to debug the Green Logistics AI project.

## 🌐 English Summary

The project ships a **debug script** (`debug_script.py`) that runs a full diagnostic in <30s — DB schema, world-builder bootstrap, end-to-end multi-agent cycle, KPI capture, and OSMnx availability check. The recommended local debug workflow is: (1) start a virtualenv and `pip install -r requirements.txt`; (2) `python debug_script.py` for a quick smoke test; (3) `pytest tests/test_agents.py` for the test suite; (4) for visual debugging, run the FastAPI backend (`uvicorn web.backend.main:app --reload`) and the Vite dev server (`cd web/frontend && npm run dev`). Common pitfalls are listed in the troubleshooting section below — solver timeouts on small-ton cycles, missing OSMnx system deps, and Gemini API key typos (the key must start with `AIzaSy`, not `AlzaSy`). GitHub Actions CI runs on every push to `main` and `pull_request`; check the Actions tab for the latest run.

For the full Chinese walkthrough including Docker, continue below.

## 📋 目录 / Table of contents

1. [本地调试 / Local debugging](#本地调试)
2. [GitHub Actions CI/CD](#github-actions-cicd)
3. [Docker 调试 / Docker debugging](#docker-调试)
4. [常见问题排查 / Troubleshooting](#常见问题排查)

---

## 本地调试

### 快速诊断

使用调试脚本快速检查系统状态：

```bash
# 进入项目目录
cd green-logistics-ai

# 激活虚拟环境
source venv/bin/activate

# 运行完整诊断
python debug_script.py

# 或只测试特定模块
python debug_script.py --env     # 检查环境配置
python debug_script.py --api     # 测试 API 连接
python debug_script.py --agent   # 测试多智能体系统
python debug_script.py --vrp     # 测试 VRP 求解器
```

### 运行测试套件

```bash
# 安装测试依赖
pip install pytest pytest-cov pytest-asyncio

# 运行所有测试
pytest tests/ -v

# 运行特定测试
pytest tests/test_agents.py::TestSupplyAgent -v

# 生成覆盖率报告
pytest tests/ -v --cov=agents --cov=optimization --cov-report=html

# 查看覆盖率报告
open htmlcov/index.html  # macOS
xdg-open htmlcov/index.html  # Linux
```

### 详细日志

```bash
# 设置日志级别
export LOG_LEVEL=DEBUG

# 运行并查看详细输出
python -m agents.coordinator 2>&1 | tee debug.log

# 查看日志文件
tail -f logs/green_logistics.log
```

---

## GitHub Actions CI/CD

### 自动测试

每次推送到 GitHub 时，会自动运行：

1. ✅ Python 代码检查 (flake8)
2. ✅ 依赖安装测试
3. ✅ Docker 镜像构建
4. ⚠️ 单元测试（需要添加测试文件）

### 查看测试结果

1. 访问：https://github.com/DingXLi/green-logistics-ai/actions
2. 点击最近的 workflow run
3. 查看详细日志

### 手动触发测试

1. 访问：https://github.com/DingXLi/green-logistics-ai/actions/workflows/ci-cd.yml
2. 点击 "Run workflow"
3. 选择分支
4. 点击 "Run workflow"

---

## Docker 调试

### 本地构建测试

```bash
# 构建后端镜像
docker build -f Dockerfile.backend -t green-logistics:test .

# 运行容器
docker run --rm -it \
  -e GOOGLE_API_KEY=your-key \
  -v $(pwd):/app \
  green-logistics:test \
  python debug_script.py
```

### Docker Compose 调试

```bash
# 启动服务
docker-compose up -d

# 查看日志
docker-compose logs -f backend

# 进入容器调试
docker-compose exec backend bash

# 在容器内运行测试
docker-compose exec backend python debug_script.py

# 停止服务
docker-compose down
```

---

## 常见问题排查

### 1. API Key 问题

**症状**: `401 Unauthorized` 或 `API_KEY_INVALID`

**解决**:
```bash
# 检查 API Key 是否设置
echo $GOOGLE_API_KEY

# 检查 Key 格式（应该以 AIza 或 Alza 开头）
# 重新获取 Key: https://aistudio.google.com/apikey

# 设置正确的 Key
export GOOGLE_API_KEY="AIzaSy..."
```

### 2. 依赖安装失败

**症状**: `pip install` 报错

**解决**:
```bash
# 升级 pip
pip install --upgrade pip

# 清除缓存重试
pip cache purge
pip install -r requirements.txt

# 或逐个安装
pip install google-adk fastapi ortools osmnx
```

### 3. OR-Tools 导入错误

**症状**: `ImportError: No module named ortools`

**解决**:
```bash
# 重新安装
pip uninstall ortools
pip install ortools==9.12.4544

# 检查安装
python -c "from ortools.constraint_solver import pywrapcp; print('OK')"
```

### 4. OSMnx 安装失败

**症状**: GDAL 相关错误

**解决**:
```bash
# Ubuntu/Debian
sudo apt-get install libgdal-dev libgeos-dev libproj-dev

# macOS
brew install gdal

# 然后重新安装
pip install osmnx
```

### 5. 智能体通信错误

**症状**: `KeyError` 或属性错误

**解决**:
```bash
# 运行调试脚本
python debug_script.py --agent

# 查看详细错误
python -m agents.coordinator 2>&1 | less

# 检查代码版本
git log --oneline -5
git status
```

### 6. VRP 求解失败

**症状**: `No solution found`

**解决**:
```bash
# 测试 VRP 求解器
python debug_script.py --vrp

# 检查距离矩阵
python -c "
from optimization.vrp_solver import VRPSolver
solver = VRPSolver()
# ... 添加节点和车辆
result = solver.solve()
print(result)
"

# 增加求解时间
# 在代码中设置 time_limit_seconds=60
```

---

## 远程调试

### SSH 到服务器

```bash
# 如果有远程服务器
ssh user@server.com

# 克隆代码
git clone git@github.com:DingXLi/green-logistics-ai.git
cd green-logistics-ai

# 安装并测试
pip install -r requirements.txt
python debug_script.py
```

### 使用 VS Code Remote

1. 安装 VS Code Remote-SSH 扩展
2. 连接到远程服务器
3. 打开项目文件夹
4. 使用集成终端调试

---

## 性能调试

### 分析执行时间

```bash
# 使用 cProfile
python -m cProfile -o profile.stats -m agents.coordinator

# 查看分析结果
python -m pstats profile.stats
```

### 内存分析

```bash
# 安装 memory_profiler
pip install memory_profiler

# 运行分析
python -m memory_profiler debug_script.py
```

---

## 获取帮助

### 查看日志文件

```bash
# 应用日志
tail -f logs/green_logistics.log

# Docker 日志
docker-compose logs -f backend

# GitHub Actions 日志
# 访问：https://github.com/DingXLi/green-logistics-ai/actions
```

### 提交 Issue

如果遇到问题：
1. 运行 `python debug_script.py` 收集信息
2. 查看相关日志
3. 在 GitHub 创建 Issue: https://github.com/DingXLi/green-logistics-ai/issues

---

## 调试检查清单

- [ ] API Key 已正确设置
- [ ] 所有依赖已安装
- [ ] 虚拟环境已激活
- [ ] 代码是最新版本
- [ ] 运行调试脚本通过
- [ ] 测试套件通过
- [ ] Docker 构建成功（如使用）

---

_祝调试顺利！_ / _Happy debugging!_
