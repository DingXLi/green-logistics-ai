# ADR-0007: 前端与部署 —— React + Vite 单页 + Docker 双镜像 + HF Spaces 公网 demo

- **状态**：已接受（含两条技术债）
- **记录日期**：2026-11-01
- **相关代码**：`web/frontend/package.json`, `web/backend/main.py`, `Dockerfile`,
  `docker-compose.yml`, `DEPLOY_HF.md`, `.github/workflows/ci-cd.yml`

## 背景

FDE 岗的现场是**给客户看东西**：一份 PPT 讲不出「这个系统真的在跑」。
所以交付物必须满足：

1. 任何人 clone 之后 **一条命令就能起**；
2. 必须有一个**公网可访问的 URL**，演示时不用依赖现场网络或云配额；
3. 前端要能表达 KPI 趋势（成本 / CO₂ / 利用率），这是客户真正关心的三个数；
4. 交付形态要能继续演进 —— 不是一次性的 demo 脚本。

## 决策

### 前端：React 18 + Vite 5 + Recharts

- `web/frontend/package.json`：`react ^18.2.0`、`vite ^5.1.0`、`recharts ^2.12.0`。
- 选 Recharts 而不是 D3/ECharts：成本/CO₂ 曲线是标准折线图，Recharts 的 API 直接对应，
  **少写 80% 的胶水代码**，而 FDE 现场真正的时间应该花在「客户要什么图」上，不是造图表库。
- 开发期通过 Vite proxy 把 `/api` 转发到 `http://localhost:8000`（`web/frontend/vite.config.js`），
  前后端分离但本地零配置。

### 后端：FastAPI + uvicorn

- `web/backend/main.py`：**8,273 行 / 141 个 `@app.` 端点**，
  覆盖 cycle 运行、供需、路线、KPI 趋势、LLM 统计、dashboard 数据等。
- 选 FastAPI 的原因不只是快：它自带 **OpenAPI 文档**，接口就是可读的合同，
  客户对接时不用再写接口文档 —— 这是 FDE 交付里省下大把时间的细节。

### 部署：本地 docker-compose + HF Spaces 公网 demo

- `docker-compose.yml` 三个服务：
  - `backend`（`Dockerfile.backend`，8000 端口，带 `/health` 健康检查）
  - `frontend`（`web/frontend/Dockerfile`，3000:80）
  - `redis`（`redis:7-alpine`）
- 正式演示挂在 **HuggingFace Spaces**（Docker Space）：
  remote 是 `git@hf.co:spaces/lidingx/Green-logistics.git`，`DEPLOY_HF.md` 写明流程。
  公网地址：`https://huggingface.co/spaces/lidingx/Green-logistics`
- CI 里的 `docker-build` / `deploy-preview` **只做构建与冒烟，不做真实环境发布**
  —— 避免每次 push 都覆盖线上 demo；发布是手动动作。

## 备选方案

- **Cloud Run + Cloud Build** —— 更接近 GCP 生产形态，也是 FDE 面试的加分项。
  否掉的原因（当下）：需要 GCP 项目、账单与镜像仓库配置，clone 即跑的成本更高；
  但**这是明确的下一步**，见下方技术债。
- **GKE / Cloud Run 多服务 + Cloud SQL** —— 对这个体量过重，成本与运维面不划算。
- **纯静态 demo（导出 HTML）** —— 最省事。否掉：讲不了后端、agent、持久化，
  而这些恰恰是 JD 的加分项所在。

## 后果

**正面**

- `docker compose up` 就能得到完整系统，任何人可复现 —— 这是「交付纪律」最直观的证据。
- 有一个公网 URL，面试现场不需要网络、不需要云配额、不需要 VPN。
- FastAPI 自动出 OpenAPI 文档，接口可读可测。
- dashboard 直接吃 SQLite 里的 KPI（见 ADR-0005），前后端职责清晰。

**负面 / 技术债**

- ⚠️ **HF Spaces 适合 demo，不适合生产**：无鉴权、无多实例、无流量治理、无扩缩容。
  面对真实客户必须走 Cloud Run（走 ADR-0006 的 Docker 流程即可复用）。
- ⚠️ **compose 里的 `redis` 服务目前没有任何 Python 代码在用**（`redis` 在 `web/backend/` 与
  `agents/` 里零命中），属于预留依赖。要么接上缓存，要么从 compose 里拿掉 ——
  **留着不用的服务是技术债，不是架构**。
- 没有前端测试（只有后端 118 个测试文件）—— 图表层的回归目前靠人眼。
- 本地开发要同时起后端与前端（vite dev + uvicorn），没有一条命令的「全栈 dev」脚本。

---

_相关：[ADR-0006](0006-ci-strategy-and-deterministic-fixture.md) CI 与镜像构建 · [ADR-0005](0005-persistence-sqlite.md) 数据来源_
