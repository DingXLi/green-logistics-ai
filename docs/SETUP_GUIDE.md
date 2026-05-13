# 🖥️ 开发环境搭建指南

在另一台电脑上从 GitHub 克隆并运行项目。

---

## 前置要求

- **Python 3.10+** ([下载](https://www.python.org/downloads/))
- **Node.js 18+** ([下载](https://nodejs.org/))
- **Git** ([下载](https://git-scm.com/download/win))

---

## 1️⃣ 克隆项目

```bash
git clone git@github.com:DingXLi/green-logistics-ai.git
cd green-logistics-ai
```

---

## 2️⃣ 配置 API Key

在项目根目录创建 `.env` 文件：

```bash
# Windows (CMD)
echo GOOGLE_API_KEY=your_google_api_key > .env

# Linux/Mac/Windows PowerShell
echo "GOOGLE_API_KEY=your_google_api_key" > .env
```

> ⚠️ Google API Key 申请地址：https://makersuite.google.com/app/apikey

---

## 3️⃣ 安装后端依赖

```bash
# 创建虚拟环境
python -m venv venv

# 激活虚拟环境
# Linux/Mac:
source venv/bin/activate
# Windows CMD:
venv\Scripts\activate.bat
# Windows PowerShell:
venv\Scripts\Activate.ps1

# 安装依赖
pip install -r requirements.txt
```

---

## 4️⃣ 安装前端依赖

```bash
cd web/frontend
npm install
cd ../..
```

---

## 5️⃣ 启动后端

```bash
# 确保在项目根目录，且虚拟环境已激活
python -m web.backend.main
```

后端运行在：**http://localhost:8000**

API 文档：**http://localhost:8000/docs**

---

## 6️⃣ 启动前端（新终端窗口）

```bash
cd web/frontend
npm run dev
```

前端运行在：**http://localhost:3000**

---

## 7️⃣ 访问测试

打开浏览器访问：**http://localhost:3000**

你应该能看到：
- 🗺️ 瑞典地图
- 📍 供应点/需求点标记
- 🚛 车辆位置
- 📊 状态栏

---

## 🔧 常见问题

### 1. Python 找不到模块
```bash
# 确保虚拟环境已激活
source venv/bin/activate  # Linux/Mac
venv\Scripts\activate     # Windows
```

### 2. npm install 失败
```bash
# 清理缓存重试
npm cache clean --force
npm install
```

### 3. 前端无法连接后端
确保后端运行在 `http://localhost:8000`，且没有被防火墙阻止。

### 4. 地图不显示
检查浏览器控制台是否有跨域错误。后端 CORS 已配置为允许所有来源（开发环境）。

### 5. Google API Key 无效
- 检查 `.env` 文件中的 key 是否正确
- 确保 API Key 已启用 Gemini API

---

## 📁 项目结构

```
green-logistics-ai/
├── agents/              # 多智能体系统
├── optimization/        # VRP 优化求解器
├── synthetic/           # 合成数据生成
├── web/
│   ├── backend/         # FastAPI 后端
│   └── frontend/         # React 前端
├── requirements.txt     # Python 依赖
└── docker-compose.yml    # Docker 部署
```

---

## 🐳 可选：Docker 部署

如果安装了 Docker，可以一键启动：

```bash
docker-compose up -d
```

- 前端：http://localhost:3000
- 后端：http://localhost:8000

---

## 📝 快速命令汇总

```bash
# 克隆
git clone git@github.com:DingXLi/green-logistics-ai.git
cd green-logistics-ai

# 环境配置
cp .env.example .env  # 编辑添加 API Key
python -m venv venv
source venv/bin/activate  # 或 venv\Scripts\activate
pip install -r requirements.txt

# 启动后端（终端 1）
python -m web.backend.main

# 启动前端（终端 2）
cd web/frontend && npm install && npm run dev
```

---

_有问题随时问！_ 🦞
