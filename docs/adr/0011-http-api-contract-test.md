# ADR-0011: HTTP API 契约测试 —— 把「客户调用的那层接口」变成 CI 里的常驻不变量

- **状态**：已接受
- **记录日期**：2026-11-05
- **相关代码**：`scripts/gen_http_contract.py`,
  `tests/test_http_api_contract.py`,
  `tests/http_api_contract.json`,
  `web/backend/main.py`（被契约约束的 HTTP 面 + `_get_protected_endpoints()`）
- **关联**：[ADR-0010](0010-public-api-contract-test.md)（公开 API 契约测试）·
  [ADR-0009](0009-persistence-package-split.md)（持久化层拆分）·
  [ADR-0006](0006-ci-strategy-and-deterministic-fixture.md)（CI 策略）·
  [ADR-0007](0007-frontend-and-deployment.md)（前端与部署）

## 背景

ADR-0010 把 `agents/persistence` 的**公开方法签名**冻成了 CI 常驻不变量。
写完那一条我意识到一件事：

> **客户不调用 Python 方法。客户调 HTTP。**

`web/backend/main.py` 现在是一个 8,273 行 / 136 个 HTTP 端点 / 1 个 WebSocket 的单体，
真正对外交付给客户的是这一层。而这一层的风险结构和 Python 那一层**完全不同**：

### 1. OpenAPI 看不见真正的响应体

136 个端点里，绝大多数的响应类型是 `Dict[str, Any]`：

```
GET /api/dashboard-summary  →  responses: {"200": {"schema": {}}}   # 空 schema
GET /api/persistence/forecast → responses: {"200": {"schema": {}}}
```

也就是说：**OpenAPI 里没有 response_model，「响应体字段变了」在规范层面完全不可见。**
只有 6 个具名模型（`FleetStatusResponse` / `OptimizationRequest` / `OptimizationResponse` /
`SupplyPoint` / `BatchOptimizeRequest` / `HTTPValidationError`）是有形状的。

这意味着：只要没人改那 6 个模型，OpenAPI 快照对「响应体退化」是**完全瞎的**。
而这类退化恰恰最伤客户 —— 前端不会报错，只会在页面上默默少一块。

### 2. 鉴权面没有契约，只有「注释里的一句提醒」

`require_admin` 依赖一共出现在 30 处， protects 29 个路由。
但「哪些端点受保护」这件事，在 iter #36 之前是靠**一份手写字面量清单**维护的：

```python
def _get_protected_endpoints() -> list:
    # NOTE: keep in sync with all ``Depends(require_admin)`` usages above.
    return ["/api/ws/stats", "/api/admin/db-stats", ...]   # 19 条
```

而 `/api/admin/auth/status` 是**公开**的发现端点，专门用来告诉客户端
「哪些端点要 token、token 怎么传」。**一份靠人肉同步、已经漂了的清单，
正在对外发布安全信息。**

### 3. 路由面本身没有基线

`web/backend/main.py` 里 136 个端点，路径、参数、状态码全部只存在于源码里。
改一个路径，没有任何一个测试会失败 —— 直到客户的 curl 开始 404。

## 决策

**把 HTTP 面也变成仓库里的常驻不变量，并且按「破坏性」分层断言。**

### 决策 1：机械抽取，不手写

`scripts/gen_http_contract.py` 从 `app.openapi()` + `route.dependant` 抽出契约：

| 抽取内容 | 来源 | 为什么这么抽 |
|---|---|---|
| 路径 / 方法 / 状态码 / 参数 / 请求体 | `app.openapi()` | 规范化的机器可读视图 |
| 响应 schema 摘要 | OpenAPI 的 `$ref` / `type` / `properties` | 只冻结**客户端能感知**的结构 |
| **是否需要 admin token** | `route.dependant.dependencies` | 判据是**真实依赖树**，不是 grep 源码 |
| WebSocket 路由 | `app.routes` 里的 `APIWebSocketRoute` | 前端实时通道也要冻结 |

136 条路由手写是抄错的，而且抄错了也没人能核对。机械抽取还顺带解决了
「grep 源码找 `Depends(require_admin)`」的经典误判：函数体里出现这串字、
或者依赖被换写法，grep 都会误判/漏判，而依赖树不会。

### 决策 2：分层断言（红 / 黄）

**这是整套设计里最关键的一条。** 契约门禁一旦「什么变化都报红」，
团队就会开始无脑 `--write` 刷基线 —— 那它就废了。所以：

| 变化 | 判定 | 理由 |
|---|---|---|
| 端点被删 / 路径改名 | 🔴 红 | 客户端直接 404 |
| 状态码被删 | 🔴 红 | 客户端按状态码分支的逻辑会走错分支 |
| 参数被删 / 改名 / 变必填 / 变类型 | 🔴 红 | 老客户端要么 422 要么丢参数 |
| 请求体 schema 变了 / 变必填 | 🔴 红 | 请求被拒 |
| response model 换人 | 🔴 红 | 字段全变 |
| 响应模型字段被删 / 变必填 | 🔴 红 | 客户端拿不到值 |
| **鉴权依赖被摘掉** | 🔴 红（**安全回归**） | 这不是「破坏」，这是**漏洞** |
| 新增端点 | 🟡 黄 | 兼容性变更，打印提示 |
| 新增可选参数 / 新增状态码 | 🟡 黄 | 同上 |
| 新增响应字段 | 🟡 黄 | 同上 |
| 给端点**补上**鉴权 | 🟡 黄 | 收紧是好事，但要让人知道 |
| 标记 deprecated | 🟡 黄 | 承诺变更，要让人知道 |

默认值**故意不冻结**：在 Python 那一层默认值是签名的一部分，
但在 HTTP 这一层它属于「服务端行为」，改了不会让老客户端报错，
冻结它只会制造噪声（ADR-0010 的教训直接搬过来用）。

### 决策 3：契约测试顺带变成安全回归测试

除了「和基线一致」，测试里还冻结了三条**不变量**，它们跟基线无关，
跟「现在的代码对不对」有关：

1. `/api/admin/*` 下除了公开的 `/api/admin/auth/status`，**必须**都挂着 `require_admin`
   → 以后新加 admin 端点忘了加依赖，CI 当场变红，而不是等它在生产上裸奔；
2. `/api/admin/auth/status` 公布的清单，**必须等于**实际受保护的端点集合
   → 「清单」和「行为」不许漂；
3. 基线的规模下限（≥130 路由 / ≥25 个受保护端点）+ 关键端点抽查
   → 防「有人为了让 CI 变绿而手工削掉一半基线」。

### 决策 4：把漂移的根因修掉，而不是只加断言

不变量 2 一上来就红了 —— 因为 iter #36 那份手写清单**已经漂了**：

| | 实际 | 清单里 |
|---|---|---|
| 受保护路由数 | 29 | 19 条 |

漏掉的是这几条路径（按路由表推导出来的）：

- `/api/admin/persistence/cache`、`/api/admin/persistence/cache/clear`
- `/api/admin/runtime-config/reset`
- `/api/admin/simulate/batch`、`/api/admin/simulate/batch/{task_id}`

**修法不是把漏的几条补进字面量**（那只是把漂移推迟到下一次），
而是把 `_get_protected_endpoints()` 改成**从 `app.routes` 推导**：
遍历路由表、看依赖树里有没有 `require_admin`。
推导用的判据和运行时鉴权**是同一份依赖声明** ——
所以「清单」和「行为」在**结构上**就不可能再漂。

> 「单一事实来源」这句话，只有当那个来源是**推导出来的**才是真的。
> 手抄一份清单再声称它是 single source of truth，是自欺欺人。

## 备选方案（为什么否掉）

| 方案 | 为什么否掉 |
|---|---|
| **A. 只快照 OpenAPI JSON**（业界常见做法） | 本项目 136 个端点里绝大多数是 `Dict[str, Any]`，快照出来 99% 是空 schema —— 对「响应体退化」完全瞎，等于花力气冻了个寂寞。而且 OpenAPI 快照**噪音极大**：改个 description 就红，团队必然开始无脑刷基线 |
| **B. 用 Schemathesis / Dredd 做黑盒属性测试** | 需要跑起服务 + 造数据；对 136 个端点全量跑要接 DB fixture、LLM key，成本高且不稳定。**更关键**：它验证「契约和实现一致」，但**不验证「契约和上个版本一致」** —— 而客户要的是后者 |
| **C. 手写一份 `api.md` 接口清单** | 一定会和代码漂，而且没有任何东西会告诉你它漂了。ADR-0010 的教训：**没有机器校验的文档等于没有文档** |
| **D. 直接把所有端点拆到 `routers/` 包，拆完再补契约** | 顺序反了。契约测试的真正价值是**给重构当安全网** —— 先有网，再动刀。Day 75+ 拆 `main.py` 时，它就是那张网 |
| **E. 只冻结路径，不冻结参数/状态码/鉴权** | 冻结得越少，漏得越多。参数改名和鉴权脱落是**最常见**的两类生产事故 |

## 后果

**好的**：

- 136 个 HTTP 端点 + 1 个 WebSocket 的面被冻住，PR 里改接口会当场看到红；
- 鉴权面第一次有「机器说了算」的守卫，而不是注释里的一句提醒；
- `_get_protected_endpoints()` 的漂移被修掉，公开发现端点开始说真话；
- 给 Day 75+ 的 `web/backend/main.py` 拆包准备了安全网；
- 多了一个可以在面试里讲的**分层门禁**设计（红/黄），而不是「我加了个测试」。

**坏的 / 代价**：

- 多一份 63 KB 的基线 JSON 进仓库（可接受 —— 比 8,273 行单文件小两个数量级）；
- 有意新增端点时要多一步 `--write` + PR 说明；
- OpenAPI 看不见的**响应体字段退化**，这套契约**仍然看不见** ——
  这是已知边界，要治得靠运行时 payload 快照（下一个角度）。

## 验证记录

### 1. 契约与基线一致（当前状态）

```
$ python scripts/gen_http_contract.py
OK: HTTP API matches baseline (136 http routes, 1 ws routes, 6 models)
```

### 2. 故意注入 5 种破坏 → 确认门禁真的会响

这一步是「**测试测试**」—— 很多契约测试其实从没验证过自己会不会响。

| 注入的破坏 | 门禁抓到了吗 | 报出来的信息 |
|---|---|---|
| 路径改名 `/api/dashboard-summary` → `-v2` | ✅ | `REMOVED route: GET /api/dashboard-summary` |
| 删掉查询参数 `metrics` | ✅ | `REMOVED query param 'metrics': GET /api/persistence/forecast` |
| 删掉 `Depends(require_admin)` | ✅ | `SECURITY REGRESSION: admin auth removed: GET /api/admin/db-stats` + 连带 `REMOVED header param 'authorization'` / `'X-Admin-Token'` + `REMOVED status code 422` |
| 响应模型删字段 `en_route` | ✅ | `REMOVED field 'en_route' from model FleetStatusResponse` |
| 删掉带参端点的参数 → 连带少一个 422 | ✅ | `REMOVED status code 422: ...` |

5 次全部被捕获，脚本退出码 **1**；随后完整还原（`git diff` 为空），再跑一次回到 `OK`。

> 附带发现：删掉鉴权依赖时，门禁**同时**报出了
> `REMOVED header param 'authorization'` 和 `'X-Admin-Token'` ——
> 因为这两个 header 本来就是 `require_admin` 函数签名带来的。
> 也就是说「客户端还能不能传 token」这件事也被契约管住了。

### 3. 不变量测试

`pytest tests/test_http_api_contract.py` → **12 passed**
（外加 `tests/test_auth_status_endpoint.py` 18 passed，一并验证清单改动没打破旧行为）

### 4. 全量 fast 套件

`pytest tests/ -m "not slow"` → 见下方「CI」一节。

## CI 接入

`.github/workflows/ci-cd.yml` 的 `fast-tests` job 增加一步：

```yaml
- name: HTTP API contract gate (iter #79)
  run: |
    # HTTP 面契约: 端点增删 / 路径改名 / 状态码 / 参数 / 鉴权依赖 任何破坏性变化都会红。
    # 故意改 API 时: python scripts/gen_http_contract.py --write + 在 PR 里说明。
    python scripts/gen_http_contract.py
```

故意加在 **pytest 之后、coverage 上传之前**：这样「测试挂了」和「契约挂了」
在 CI 日志里是两条独立的失败原因，不会互相掩盖。

## 面试怎么讲（30 秒版）

> 「我的后端有 136 个端点，但响应大部分是自由 dict —— OpenAPI 快照对响应体退化
> 是瞎的。所以我自己抽了一份 HTTP 契约：路径、状态码、参数、还有**哪些端点需要
> admin token**。关键是**分层**：破坏性变更让 CI 红，兼容性变更只打印 ——
> 否则大家就会无脑刷基线，那门禁就废了。
> 写完第一天就抓到真 bug：对外公布「哪些端点要 token」的那份清单，
> 靠人手维护，已经漏了 4 个受保护端点。我没去补清单，而是改成**从路由表推导** ——
> 推导用的判据和运行时鉴权是同一份依赖声明，所以它结构上就不可能再漂。」

追问预案：

- **「为什么不直接用 OpenAPI 快照？」** → 因为 136 个端点里绝大多数响应是
  `Dict[str, Any]`，快照对响应体退化完全瞎；而且 description 一改就红，噪音大到没人看。
- **「参数默认值为什么没冻？」** → HTTP 层默认值是服务端行为，改了不会让老客户端报错；
  冻结它只会制造噪声。Python 层默认值是签名的一部分，所以那里冻了 —— 两层不一样。
- **「契约测试会不会变成负担？」** → 破坏性变更红、兼容性变更黄，加上 `--write` 需要
  PR 说明，改接口的成本是「写一段说明」，不是「跟门禁吵架」。
- **「怎么保证基线本身不被削掉？」** → 测试里断言基线规模下限 + 关键端点抽查，
  跟 ADR-0010 对 persistence 基线的做法一致。
