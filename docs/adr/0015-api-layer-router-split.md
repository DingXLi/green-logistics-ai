# ADR-0015: API 层拆包 —— 8,303 行 main.py 瘦身成「薄壳 + router 包」

- **状态**：已接受
- **记录日期**：2026-11-10
- **相关代码**：web/backend/main.py, web/backend/routers/persistence.py, web/backend/_auth.py, web/backend/_export.py, web/backend/routers/__init__.py, scripts/split_persistence_router.py

## 背景

前面五轮契约（ADR-0010 ~ ADR-0014）解决的都是**「怎么证明没改坏」**。
这一轮要解决的是**「怎么改得动」**。

改造之前的 `web/backend/main.py`：

| 指标 | 数值 |
|---|---|
| 行数 | **8,303 行** |
| HTTP 端点 | 136 个（119 个 GET） |
| 顶层函数 | ~180 个 |
| 内聚的领域 | 至少 6 个（persistence 查询 / 导出 / admin / 调度 / 模拟 / WebSocket） |

单文件 8,303 行的真实代价，不是「看着长」：

1. **改一行要读 8,000 行** —— 想加一个 `/api/persistence/*` 端点，得先确认
   136 个端点里没有重名路径。
2. **agent 和新人都不敢碰** —— 这是最实际的成本：这个仓库 90% 的改动是
   AI agent 做的，agent 面对 8,000 行单文件时的默认行为是**绕开**，
   于是新端点继续往同一个文件里堆。
3. **「加个 helper」和「加个端点」没有边界** —— 导出 helper（CSV/Parquet/gzip）、
   admin 鉴权 helper、端点，全在同一个模块的同一个命名空间里。
4. **循环依赖是隐形的** —— 只要有人想在 router 里 `import main` 里的东西，
   就会立刻撞上 `main → router → main`。

ADR-0009 已经把 persistence **数据层**拆成了 16 个领域模块（9,949 行 → 包）。
但 **API 层**一直没动 —— 那次拆的是「存数据的」，不是「对外暴露的」。

### 为什么以前不敢拆

因为没有东西能证明「拆完 API 一样」。

「重构不改 API」如果只是口头承诺，那它就是一句无法验证的话 ——
而这次要动的是**客户调的那一层**。

## 决策

**把 `main.py` 从「所有东西」改成「装配 + 跨领域逻辑」，HTTP 端点按领域拆包；
第一个包是 `persistence`（82 个只读查询端点）。**

```
web/backend/
├── main.py                  4,720 行  ← 装配 + 跨领域逻辑（admin / 调度 / 模拟 / WS）
├── _auth.py                    92 行  ← admin token 校验 + require_admin 依赖
├── _export.py                 130 行  ← CSV / Parquet / gzip 编解码 helper
└── routers/
    ├── __init__.py
    └── persistence.py       3,571 行  ← 82 个 /api/persistence/* 端点
```

依赖方向是**单向**的，没有环：

```
main.py ──▶ _auth.py
main.py ──▶ _export.py
main.py ──▶ routers/persistence.py
routers/persistence.py ──▶ _auth.py
routers/persistence.py ──▶ _export.py
```

`main.py` 只剩 4,720 行（**-43%**），`routers/persistence.py` 3,571 行。

### 关键取舍 1：`coordinator` 怎么拿

搬走的端点都要用 `coordinator`。三种做法：

| 方案 | 问题 |
|---|---|
| ① router 模块自己存一份 `coordinator` | 两份状态，必然漂移 |
| ② 放 `app.state.coordinator` | 既有测试全要改 —— 见下 |
| ③ **router 每次请求时读 `web.backend.main.coordinator`** | 选定 |

选 ③ 的理由很具体：**既有测试直接写模块属性**。

```python
# tests/test_forecast.py:271
backend_main.coordinator = fake
# tests/test_llm_cost_by_type.py:160
monkeypatch.setattr(backend_main, "coordinator", _FakeCoord())
```

那是**模块属性**，不是 `app.state`。改成 `app.state` 要动所有这些测试
+ 破坏一个已经形成的使用约定。所以 router 里写了一个惰性读取：

```python
def _coordinator() -> Optional[Any]:
    main_module = sys.modules.get("web.backend.main")
    if main_module is None:
        return None
    return getattr(main_module, "coordinator", None)
```

走 `sys.modules` 而不是 `import` —— `import` 会形成循环依赖
（main 要 `include_router`，router 又要 main 的 coordinator）。

**代价**：每个端点函数体第一行多一句 `coordinator = _coordinator()`。
**收益**：零同步代码、零测试改动、`main.coordinator` 仍是唯一事实来源。
测试怎么改都立刻生效。

配套还加了一句，让 `python web/backend/main.py` 直接跑时也能对上：

```python
sys.modules.setdefault("web.backend.main", sys.modules[__name__])
```

### 关键取舍 2：迁移怎么保证「一个字节都没改」

**不用正则，用 AST。** 端点是用装饰器声明的（`@app.get("/api/persistence/xxx")`），
正则会翻车：把 docstring 里的字符串当成路径、漏掉跨多行的装饰器、
改一个字符就悄悄搬错函数。

`scripts/split_persistence_router.py` 用 `ast` 定位「顶层函数 + 它的装饰器路径」，
再按行搬源码。两个要点：

1. **迁移是闭包的** —— 被搬走的端点如果调用了 main.py 里的私有 helper
   （`_build_parquet_response` / `_build_json_response` / `_build_ndjson_response`），
   helper 必须一起搬，否则 router 模块 import 时就 NameError。
   脚本沿「函数引用」做传递闭包。
2. **注释块跟着函数走** —— 端点上面那几行解释性注释（`iter #27: Parquet endpoints`）
   是函数的一部分，拆散了就没意义了。脚本按「紧邻（或隔一个空行）」认领注释块。

脚本还留了两个**精确字符串替换**（`CLEANUPS`）：把随代码搬过来的两处历史风格债
（`E741` 的变量名 `l`、`F841` 的死变量 `n_routes`）顺手清掉。
写成精确替换而不是就地手改，是为了让脚本**可复现** —— 重跑得到同样的结果。

### 关键取舍 3：为什么不一次全拆

136 个端点一次全搬，等于同时改 136 处 —— 出问题时分不清是「搬错了」
还是「本来就这样」。

一次一个包，每次都跑完四层契约门禁 + 全量测试，**每一步都能单独归因**。
这是 ADR-0013 那条「先建门禁，再动刀」原则的延续：门禁不是拆完再补的，
是拆之前就有的。

## 验证

**四层契约门禁全绿（这就是「API 没变」的证明）**：

| 门禁 | 结果 |
|---|---|
| `scripts/gen_http_contract.py`（ADR-0011） | `OK: HTTP API matches baseline (136 http routes, 1 ws routes, 6 models)` |
| `scripts/gen_payload_shapes.py`（ADR-0012） | `OK: 运行时响应体形状与基线一致` |
| `scripts/endpoint_policy.py`（ADR-0013） | `✅ 校验通过：119 个 GET 端点，采集 100 + 显式豁免 19，覆盖率 84.0%，没有沉默的缺口` |
| `scripts/gen_export_contracts.py`（ADR-0014） | `OK: 导出列契约与基线一致` |

**端点数逐个对齐**：

```
HTTP 端点: 136 (拆分前 136)   WebSocket: 1   总 routes: 141
persistence 端点: 82   admin 端点: 25
```

**静态检查**：

- flake8（CI 严格子集 `E9,F63,F7,F82`）→ **0 error**
- flake8（全量风格，新文件）→ 只剩 5 条 `C901`（**继承的历史复杂度债**，见下）
- mypy（CI 同款参数）→ `Success: no issues found in 36 source files`

**测试**：`pytest -m "not slow"` 全量通过（1726 passed / 7 skipped / 0 failed）。

## 已知债（这次没还）

搬过来的代码里有 5 个函数复杂度超阈值（`C901`）：

| 函数 | 复杂度 |
|---|---|
| `get_forecast_confidence` | 32 |
| `get_forecast_multi` | 19 |
| `get_llm_cost_forecast` | 11 |
| `get_forecast` | 11 |
| `get_prediction_accuracy_by_day` | 11 |

这是**搬过来之前就有的债**，不是这次引入的 —— CI 的严格子集不含 `C901`，
所以它一直绿着。

**没有顺手改**，理由：一次改动同时做「搬家」和「重写」，出问题时分不清
是搬错了还是改错了；而且 `get_forecast_confidence` 复杂度 32 的函数要拆，
需要先想清楚怎么拆（那是**下一个** ADR 的事）。

**下一个包该挑哪个**：`/api/admin/*`（25 个端点）依赖 `require_admin`，
已经在 `_auth.py` 里就位，拆起来最省事。

## 对外影响

**零。** 路径、方法、参数、状态码、response_model、admin 鉴权、
响应体运行时形状、导出列契约 —— 全部由上面四层门禁证明没变。

唯一的行为差异在**内部实现**：端点现在住在 `routers/persistence.py`，
`web.backend.main.coordinator` 仍是唯一事实来源。

`from web.backend.main import require_admin` / `_rows_to_csv` / `_csv_to_rows` /
`_rows_to_parquet_bytes` 这些**内部**导入路径也仍然可用（main.py 转手 import 回来），
所以既有测试一行没改。
