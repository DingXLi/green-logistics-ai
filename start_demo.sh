#!/bin/bash
# ============================================================
# Green Logistics AI — One-command demo launcher
# ============================================================
# 启动 FastAPI backend (8000) + Vite frontend (3000)
# 用法:  bash start_demo.sh
# 停止:  Ctrl+C  (会一起停 backend + frontend)
# ============================================================
set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

PORT_BACKEND=8000
PORT_FRONTEND=3000

# 颜色
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

cleanup() {
  echo
  echo -e "${YELLOW}🛑 停止 backend (pid=$BACKEND_PID) + frontend (pid=$FRONTEND_PID)${NC}"
  kill $BACKEND_PID $FRONTEND_PID 2>/dev/null || true
  pkill -f "uvicorn.*main:app" 2>/dev/null || true
  pkill -f "vite" 2>/dev/null || true
  wait 2>/dev/null || true
  exit 0
}
trap cleanup INT TERM

echo -e "${BLUE}============================================================${NC}"
echo -e "${BLUE}♻️  Green Logistics AI — Demo Launcher${NC}"
echo -e "${BLUE}============================================================${NC}"
echo

# --- 0. 前置检查 ---
echo -e "${YELLOW}[0/4] 前置检查...${NC}"

# venv
if [ ! -d "venv" ]; then
  echo -e "${RED}❌ venv/ 不存在, 请先: python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt${NC}"
  exit 1
fi
echo "  ✅ venv/"

# node_modules
if [ ! -d "web/frontend/node_modules" ]; then
  echo -e "${YELLOW}  ⚠️  node_modules 不存在, 自动 npm install...${NC}"
  (cd web/frontend && npm install)
fi
echo "  ✅ web/frontend/node_modules"

# .env
if [ ! -f ".env" ]; then
  echo -e "${YELLOW}  ⚠️  .env 不存在, 复制 .env.example (如果存在) 或继续${NC}"
fi

# 端口占用
if lsof -Pi :$PORT_BACKEND -sTCP:LISTEN -t >/dev/null 2>&1; then
  echo -e "${YELLOW}  ⚠️  端口 $PORT_BACKEND 被占用, 尝试 kill 占用进程...${NC}"
  lsof -Pi :$PORT_BACKEND -sTCP:LISTEN -t | xargs -r kill -9 2>/dev/null || true
  sleep 1
fi
if lsof -Pi :$PORT_FRONTEND -sTCP:LISTEN -t >/dev/null 2>&1; then
  echo -e "${YELLOW}  ⚠️  端口 $PORT_FRONTEND 被占用, 尝试 kill 占用进程...${NC}"
  lsof -Pi :$PORT_FRONTEND -sTCP:LISTEN -t | xargs -r kill -9 2>/dev/null || true
  sleep 1
fi
echo

# --- 1. Backend ---
echo -e "${YELLOW}[1/4] 启动 backend (FastAPI :$PORT_BACKEND)...${NC}"
source venv/bin/activate
mkdir -p logs
nohup python -m uvicorn web.backend.main:app --host 0.0.0.0 --port $PORT_BACKEND > logs/backend.log 2>&1 &
BACKEND_PID=$!
echo "  backend pid=$BACKEND_PID, 日志: logs/backend.log"

# 等 health
for i in {1..30}; do
  if curl -s --max-time 1 "http://localhost:$PORT_BACKEND/health" >/dev/null 2>&1; then
    echo -e "  ${GREEN}✅ backend 健康${NC}"
    break
  fi
  sleep 1
  if [ $i -eq 30 ]; then
    echo -e "${RED}❌ backend 30s 内未就绪, 查看 logs/backend.log${NC}"
    tail -30 logs/backend.log
    cleanup
  fi
done
echo

# --- 2. Frontend ---
echo -e "${YELLOW}[2/4] 启动 frontend (Vite :$PORT_FRONTEND)...${NC}"
(cd web/frontend && nohup npm run dev -- --host 0.0.0.0 --port $PORT_FRONTEND > ../../logs/frontend.log 2>&1 &)
FRONTEND_PID=$!
echo "  frontend pid=$FRONTEND_PID, 日志: logs/frontend.log"

# 等 Vite
for i in {1..30}; do
  if curl -s --max-time 1 "http://localhost:$PORT_FRONTEND" >/dev/null 2>&1; then
    echo -e "  ${GREEN}✅ frontend 健康${NC}"
    break
  fi
  sleep 1
  if [ $i -eq 30 ]; then
    echo -e "${RED}❌ frontend 30s 内未就绪, 查看 logs/frontend.log${NC}"
    tail -30 logs/frontend.log
    cleanup
  fi
done
echo

# --- 3. 数据初始化 ---
echo -e "${YELLOW}[3/4] 数据初始化...${NC}"
if [ -f "data/month_simulation.db" ]; then
  N_CYCLES=$(sqlite3 data/month_simulation.db "SELECT COUNT(DISTINCT sim_day) FROM optimization_cycles" 2>/dev/null || echo "?")
  echo "  ✅ 发现 30 天仿真数据库 ($N_CYCLES 天, data/month_simulation.db)"

  # 自动预填 simulation.db (backend 默认 db)，让 Dashboard 立即有数据
  if [ -f "data/simulation.db" ]; then
    cp data/simulation.db data/simulation.db.bak 2>/dev/null || true
    echo "  📦 备份旧 simulation.db -> simulation.db.bak"
  fi
  cp data/month_simulation.db data/simulation.db
  N_NEW=$(sqlite3 data/simulation.db "SELECT COUNT(DISTINCT sim_day) FROM optimization_cycles" 2>/dev/null || echo "?")
  echo "  ✅ 已预填 simulation.db ($N_NEW 天, Dashboard 立即可用)"
else
  echo -e "  ${YELLOW}⚠️  没有 30 天仿真数据, Dashboard 图表将为空${NC}"
fi
echo

# --- 4. 打开浏览器 ---
echo -e "${YELLOW}[4/4] 打开浏览器...${NC}"
sleep 2
URL="http://localhost:$PORT_FRONTEND"
if command -v xdg-open >/dev/null 2>&1; then
  xdg-open "$URL" 2>/dev/null &
elif command -v open >/dev/null 2>&1; then
  open "$URL" 2>/dev/null &
fi

echo
echo -e "${GREEN}============================================================${NC}"
echo -e "${GREEN}✅ Demo 已就绪!${NC}"
echo -e "${GREEN}============================================================${NC}"
echo
echo -e "  📊 Dashboard:  ${BLUE}$URL${NC}"
echo -e "  🔌 Backend API: ${BLUE}http://localhost:$PORT_BACKEND/docs${NC}"
echo -e "  🗺️  Map 视图:   浏览器自动打开"
echo
echo -e "${YELLOW}按 Ctrl+C 停止所有服务${NC}"
echo

# 等子进程
wait $BACKEND_PID 2>/dev/null
wait $FRONTEND_PID 2>/dev/null
