# ADR-0006: CI 策略 —— 分层 + 多版本矩阵 + fail-fast=false + 确定性 DB fixture

- **状态**：已接受
- **记录日期**：2026-11-01（决策形成于 iter #36 与 iter #74）
- **相关代码**：`.github/workflows/ci-cd.yml`, `scripts/seed_ci_fixture.py`,
  `pytest.ini`, `tests/`

## 背景

这个仓库对 FDE 岗的意义不只是「代码」—— 面试官会点开 GitHub Actions 看灯是红是绿，
点开 README 看有没有架构说明。**一个连挂 5 次的 Actions 是负资产**，
比没有 CI 更减分（说明作者不懂交付纪律，或者不敢开 CI）。

同时真实约束是：CI 要跑 OR-Tools（慢）、要跑 LLM 真机调用（要 key）、
而 `data/*.db` 被 gitignore 排除（见 [ADR-0005](0005-persistence-sqlite.md)），
导致 **fresh checkout 里根本没有数据库**。

## 决策

### 1. 六个 job 分层

| job | 内容 | 说明 |
|---|---|---|
| `lint` | flake8 + mypy + 安全扫描 | 三个检查并行跑，任一失败即红 |
| `fast-tests` | `pytest -m "not slow"`，矩阵 **3.11 / 3.12 / 3.13** | 主力档 |
| `slow-tests` | `pytest -m slow`（OR-Tools / coordinator / perf），**只跑 3.12** | 慢，矩阵不翻倍 |
| `llm-tests` | 需要 `GOOGLE_API_KEY` 的真机调用 | 单独隔离，缺 key 不影响主流程 |
| `docker-build` | 构建镜像 + 冒烟测试 | 依赖前三个 job 全绿 |
| `deploy-preview` | 预览环境通知 | `needs: [lint, fast-tests, slow-tests, docker-build]` |

### 2. `fail-fast: false`

矩阵默认 fail-fast：一档挂了，其余档会被 **cancel**，结果页只看到一个红。
显式关掉之后，**每一档独立报结果**，能直接看到「3.11 挂了但 3.12/3.13 是绿的」。

### 3. 覆盖率门槛 `--cov-fail-under=35%`

门槛设在 35% 而不是拍脑袋的高值：它防的是「覆盖率归零式重构」，
而当前实测 **81.21%**（iter #74 修复后）——门槛只做地板，不做天花板。

### 4. 干净 checkout 必须能跑绿：确定性 DB fixture（iter #74）

根因（实测复现，非推测）：`data/*.db` 被 gitignore → CI 里没有 `simulation.db`
→ 依赖历史的测试（`need >= 2 cycles`、`confidence` 需要历史点拟合）全挂，
**实测 26 failed / 1588 passed**。本地几个月攒的真实数据让同样的测试全绿 —— 典型的
「本地能跑 ≠ 交付能跑」。

解法：新增 `scripts/seed_ci_fixture.py`（153 行）+ `fast-tests` job 里加 seeding 步骤，四条硬约束：

1. **幂等** —— 库里已有 ≥ 2 个 `sim_day` 就直接跳过，**绝不覆盖开发者的本地真实数据**；
2. **确定性** —— 纯 SQL insert，不跑 OR-Tools、不用随机数、不依赖网络；
3. **快** —— < 1 秒；
4. **schema 单一来源** —— 复用 `Persistence` 建表，不抄第二份 `CREATE TABLE`。

数据形状故意做成「轻微上升 + 确定性锯齿」，让三种预测方法都有可拟合的形状
（全常数序列算出来的 r² 没有意义）。

### 5. CI 绿灯是交付标准，不是加分项

修复链条：run #75~#79 连挂 5 次 → run #80 一次全绿（8 个 job 全 success，
其中 `docker-build` 与 `deploy-preview` 是**历史上第一次真正跑到**）。
链接：`https://github.com/DingXLi/green-logistics-ai/actions/runs/41183662099`

## 备选方案

- **只测 Python 3.11** —— 省一半 CI 时间。否掉：用户机器与客户环境版本并不统一，
  3.13 上出的问题（我在本地就撞到过）只有矩阵能提前暴露。
- **把 `simulation.db` 提交进仓库** —— 简单。否掉：二进制文件进 git 会膨胀历史、
  而且开发者会误改它导致 CI 结果不可信。
- **CI 里用 docker 起数据库** —— 更接近生产。否掉：为了 26 个测试引入容器编排开销不划算；
  SQLite 单文件本来就该能在干净环境里凭空造出来。
- **只在本地跑测试，不上 CI** —— 最省事。否掉：交付物必须自带可验证证据。

## 后果

**正面**

- Actions 绿灯 + badge 可直接作为面试证据（「我的仓库 CI 是绿的」比任何形容词都硬）。
- 「干净环境可复现」这条原则**不只服务 CI**：它就是交付给客户时的要求 ——
  客户拿到你给的那一份，本地绿不算数。
- 覆盖率从门槛到实测 81.21%，回归有网兜着。

**负面 / 技术债**

- 每次 push 跑 3 档 fast + 1 档 slow + LLM 档，**总 CI 时长十几分钟**（fast 单档约 5 分钟）。
  可优化方向：按路径过滤（`paths:`）、给 fast 档做依赖缓存、或把 3.11 降级为 nightly。
- `llm-tests` 需要 secret key，fork 出去的 PR 拿不到 key → 该档会失败，
  对开源贡献者不友好（当前是靠仓库私有规避）。
- fixture 的数据形状是**为测试设计**的，与真实世界分布不同 —— 它保证「绿」，不保证「像」。

---

_相关：[ADR-0005](0005-persistence-sqlite.md) 数据与 gitignore · [ADR-0007](0007-frontend-and-deployment.md) 部署_
