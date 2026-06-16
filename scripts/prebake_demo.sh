#!/bin/bash
# ============================================================
# prebake_demo.sh — 把 30 天仿真数据预填到 backend 默认 db
# ============================================================
# 默认 backend 启动后用 data/simulation.db。
# 这个脚本把 data/month_simulation.db (30 天) 复制为 simulation.db，
# 让 demo 启动后 Dashboard 立即有数据。
# ============================================================
set -e
cd "$(dirname "$0")/.."

YELLOW='\033[1;33m'
GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

if [ ! -f "data/month_simulation.db" ]; then
  echo -e "${RED}❌ data/month_simulation.db 不存在. 先跑:${NC}"
  echo "  source venv/bin/activate && python scripts/run_month.py"
  exit 1
fi

# 备份现有
if [ -f "data/simulation.db" ]; then
  cp data/simulation.db data/simulation.db.bak
  echo -e "${YELLOW}📦 备份: data/simulation.db.bak${NC}"
fi

# 复制
cp data/month_simulation.db data/simulation.db

# 用 Python 查统计 (避免 sqlite3 CLI 依赖)
STATS=$(python3 -c "
import sqlite3
conn = sqlite3.connect('data/simulation.db')
n_days = conn.execute('SELECT COUNT(DISTINCT sim_day) FROM optimization_cycles').fetchone()[0]
row = conn.execute('SELECT ROUND(SUM(total_cost_sek)), ROUND(SUM(total_co2_kg)), ROUND(SUM(total_tons)) FROM optimization_cycles').fetchone()
try:
    n_llm = conn.execute('SELECT COUNT(*) FROM llm_decisions').fetchone()[0]
except Exception:
    n_llm = 0
print(f'{n_days}|{row[0]}|{row[1]}|{row[2]}|{n_llm}')
conn.close()
")

IFS='|' read -r N_DAYS TOTAL_COST TOTAL_CO2 TOTAL_TONS N_LLM <<< "$STATS"

echo
echo -e "${GREEN}✅ Demo 数据预填完成:${NC}"
echo "  Sim days:     $N_DAYS"
echo "  Total cost:   ${TOTAL_COST} SEK"
echo "  Total CO₂:    ${TOTAL_CO2} kg"
echo "  Total tons:   ${TOTAL_TONS} t"
echo "  LLM decisions: $N_LLM"
echo
echo "现在跑: bash start_demo.sh"
