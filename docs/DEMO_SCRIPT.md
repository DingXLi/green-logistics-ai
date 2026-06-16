# 🎬 Green Logistics AI — 5-Minute Demo Script

> **场景**: 给导师 / 同事 / 论文 reviewer 现场演示
> **设备**: 笔记本 + 投影 / 屏幕共享
> **预演**: 跑 2 遍确认稳定
> **回退**: 录屏备份 (见 [录屏] 段)

---

## 📋 Demo 前 30 分钟

```bash
# 1. 进入项目
cd green-logistics-ai

# 2. 预填 demo 数据 (一次, 之后数据一直存在)
bash scripts/prebake_demo.sh

# 3. 一键启动 (backend :8000 + frontend :3000)
bash start_demo.sh
```

打开浏览器 http://localhost:3000

---

## 🎯 5 分钟演示流程 (Talk Track)

### 【0:00 - 0:30】开场 — 项目背景

> "我做了一个多智能体 AI 系统,优化瑞典 Borås 地区的废料回收物流。
> 核心问题是:每天 20 个废料供应点、10 个需求点、30 辆车,
> 怎么分配路径才能同时兼顾成本和碳排放?"

**画面**: 项目 logo + 标题

---

### 【0:30 - 1:30】实时地图 — 现场感

切到 **Map 视图**。

> "先看地图。红色点是供应点(废料来源),蓝色是需求点(回收/处理设施),
> 黄色小卡车是 30 辆车。点击 'Run Optimization' 看一次求解。"

**操作**:
1. 点击 `🚀 Run Optimization` 按钮
2. 等 ~2 秒,地图上画出路径

**话术**:
> "OR-Tools 在 2 秒内求出了最优路径。 注意我用了**真实 OSM 路网距离**,
> 不是直线距离,所以路径是实际可行驶的。"

---

### 【1:30 - 3:00】Dashboard — 数字说话

切到 **Dashboard 视图**。

> "现在看真正有意思的部分 —— 30 天仿真结果。"

**逐一点 KPI 卡片**:
- **Total Cost** 300,885 SEK
- **Total CO₂** 98,366 kg (≈ 23 棵树一年的吸收量)
- **Utilization** 66.7% 平均车队利用率
- **Transported** 30,701 t 废料

**点 KPI Trends 折线图**:
> "30 天趋势。看这三条线 —— 成本(红)、碳排(橙)、利用率(绿)。
> 趋势平稳,系统每天都找到了可行解,没有崩溃也没有骤变。"

**点 Matches per Day 柱状图**:
> "每天匹配 50-60 次,代表 30 辆车在 Borås/Göteborg/Stockholm 三地
> 之间的运输任务。"

---

### 【3:00 - 4:00】多目标 — Pareto 前沿

> "传统 VRP 只优化成本。我们做了**多目标**,
> 同时考虑成本和碳排放。"

**点 Pareto Frontier 散点图**:
> "这是 Pareto 前沿 —— 5 个点代表 5 种不同的权重组合 α。
> 左下角是'省钱优先',右上角是'减排优先'。
> 决策者可以根据政策选择不同的权衡点。"

---

### 【4:00 - 5:00】技术亮点 + Q&A 引子

> "最后说一下技术栈:
> - **多智能体**: Google ADK + Gemini Flash LLM 做高层决策
> - **求解器**: OR-Tools (工业级 VRP)
> - **真实数据**: OSM 路网 + SMHI 天气 + Eurostat 经济信号 + SCB 废料统计
> - **前端**: React + Leaflet + Recharts
> - **后端**: FastAPI + SQLite"

**收尾**:
> "整套系统从 4 月 13 日开始,到现在 6 月 16 日,两个月迭代出来。
> 接下来想做的:多目标权重交互式调整、瑞典全境扩展、JWT 鉴权上线。
> 大家有问题的现在可以问。"

---

## ❓ Q&A 准备

| 问题 | 回答要点 |
|------|---------|
| **为什么用 Gemini 不是 GPT?** | 成本低 (Flash 版), 瑞典语支持可, 走 Google ADK 框架 |
| **数据从哪来?** | OSM (路网) + SMHI (天气) + Eurostat (经济) + SCB (废料) + 合成 (兜底) |
| **怎么扩展到其他城市?** | WorldConfig 里改 lat/lon, OSM 自动下载新区域 |
| **OR-Tools 求解时间?** | 单 cycle ~11s,30 天 ~5.5 min (time_limit=10s) |
| **Pareto 怎么生成的?** | α ∈ {0, 0.25, 0.5, 0.75, 1.0} 跑 5 次 VRP, 选 cost×α + co2×(1-α) 最优 |
| **真实部署成本?** | FastAPI 单实例可承载 100 辆车/天, SQLite 换 PostgreSQL 后可扩 |
| **多智能体真的需要吗?** | 简化版本能跑,但分层 (Supply / Market / Logistics) 让 LLM 决策更可解释 |
| **为什么 30 天?** | 覆盖一个完整业务周期, 同时跑完在 10 分钟内 |
| **能耗 vs 碳排 区别?** | 能耗 (kWh) 是输入, 碳排 (kg CO2) 是输出, 我们追踪后者 |
| **怎么验证优化真的有效?** | 跟 baseline (随机分配) 对比, 通常成本降 20-30%, CO2 降 15-25% |

---

## 🎥 录屏 (回退方案)

如果现场机器出问题:

```bash
# 启动后, 浏览器装个录屏插件 (如 Loom)
# 或用 ffmpeg 录整个浏览器
ffmpeg -video_size 1920x1080 -framerate 30 -f x11grab -i :0.0 \
  -t 300 docs/demo_recording_$(date +%Y%m%d).mp4
```

---

## 🐛 故障回退清单

| 现象 | 修复 |
|------|------|
| `bash: venv/bin/activate: No such file or directory` | `python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt` |
| 端口 8000 占用 | `lsof -i:8000 -t \| xargs kill -9` |
| 端口 3000 占用 | `lsof -i:3000 -t \| xargs kill -9` |
| Dashboard 没数据 | `bash scripts/prebake_demo.sh` 然后重启 |
| 地图空白 | 后端 `/api/optimize` 跑一次 |
| LLM 决策为 0 | 当前 db 是 month_simulation.db, 跑新 sim 会带 LLM 数据 |
| 前端 build 报错 | `cd web/frontend && rm -rf node_modules && npm install` |

---

## 📂 演示前后必看

- ✅ 演示前: `git status` 确认 working tree clean
- ✅ 演示前: 跑 2 遍完整流程, 计时
- ✅ 演示后: `git add -A && git commit -m "demo: presentation + screenshots"`
- ✅ 演示后: 收集 Q&A, 更新本文档
