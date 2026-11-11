# ADR-0016: 复杂度棘轮门禁 —— 把「从来没红过的 lint 规则」变成只许单向变好的 CI 常驻不变量

- **状态**：已接受
- **记录日期**：2026-11-11
- **相关代码**：scripts/gen_complexity_baseline.py, tests/complexity_baseline.json, web/backend/routers/persistence.py, tests/test_forecast_method_prefs.py, .github/workflows/ci-cd.yml

## 背景

`setup.cfg` 里写着 `max-complexity = 10`。CI 的 lint job 里也跑着：

```yaml
flake8 agents/ optimization/ synthetic/ data/ web/backend/ \
  --count --exit-zero --max-complexity=10 --max-line-length=127 --statistics
```

`--exit-zero` —— **这条规则从来没有真正拦住过任何东西**。

复杂度 36 的 `health_check_deep`、32 的 `get_forecast_confidence`、
31 的 `MultiAgentCoordinator.run_optimization_cycle` 就那么躺在主干上，
每次 lint 都在报，每次都当没看见。

这不是「没时间改」的问题，是**「门禁没有牙齿」**的问题：
一个永远不会红的门禁，和没有门禁是同一个东西 —— 而且更糟，
它让人误以为复杂度是被管着的。

### 为什么不能一刀切改严

最直觉的做法是把 CI 里的 `--exit-zero` 去掉，让 `max-complexity=10` 真的生效。
**不能这么做**，理由有三个：

1. **一次性 49 个函数全部爆红** —— 这个 PR 会长到无法评审。
   而「无法评审的重构」在实践中只有两种结局：全靠 `# noqa` 逃避，或者永远不合。
2. **改严的那一刻，规则就成了噪音** —— 团队的第一反应不是改代码，是改门禁。
3. **还债和写新功能会互相阻塞** —— 老代码在降复杂度，新功能在加复杂度，
   总量原地踏步。一刀切的门禁会让「加新功能」这件事被无限期推迟。

真正可行的形状是**棘轮（ratchet）**：债务**只许降，不许涨**。
像数据库 migration 一样，一格一格往前爬，每一格都可评审、可回滚、可测量。

### 借谁的尺子

复杂度必须用 **flake8 自己的 C901（mccabe）**算，不能自己实现一套。
理由：开发者本地跑 `flake8` 看到的数字，必须和门禁看到的数字**一模一样**。
自己写一个「差不多的复杂度」= 门禁和开发者对不上账 = 门禁必被无视。

## 决策

**我们建立一条圈复杂度棘轮门禁：已有函数不许变难，新函数必须够简单。**

具体落地是 `scripts/gen_complexity_baseline.py`（+ `tests/complexity_baseline.json` 基线）：

### 三层规则

| 层 | 判据 | 级别 | 为什么 |
|---|---|---|---|
| 1 | 同一个函数的复杂度比基线**更高** | 🔴 红 | 门禁主体：已经很难的代码不许再变难 |
| 2 | 基线里没有的**新函数**复杂度 > 10 | 🔴 红 | 债不许以「新写的代码」的形式偷偷进场 |
| 3 | 基线里的函数**消失**了（删了/改名了） | 🔴 红 | 沉默的缺口必须是红的（与 ADR-0013「陈旧豁免 → 红」同源） |
| — | 复杂度**变低**了 | 🟡 黄 | 进步，提示 `--write` 记录进展 |
| — | 新函数且复杂度 ≤ 10 | 🟡 黄 | 合规，提示记进基线 |
| — | 基线里的函数不见了（删除/改名） | 🟡 黄 | 提示同步基线 |

第 3 层和第 1/2 层分开的理由：**「债务变多」和「债务变少」不是一回事**。
新代码更复杂 → 直接阻断；旧代码被删掉 → 只是基线要同步，不该阻断。
（删函数本身不是坏事，不该被门禁拦住。）

### 为什么不强制每次都刷基线

因为**基线的价值在于「相对上一次」，不在于「永远是最新的」**。
每次改动都强制刷基线 → 每个 PR 都要动基线文件 → 评审噪音 → 门禁被无视。

这和 ADR-0011 的立场完全一致：

> 「门禁一旦『什么变化都报红』，团队就会开始无脑刷基线 —— 那它就废了。」

所以只在**债务真的动了**（降 / 增 / 删）的时候提示刷新。

### 键怎么设计

基线键是 `文件路径::类名.函数名`（限定名），**不是行号**。

- 行号会漂 —— 上面加一行注释，下面所有函数的行号全变。
  按行号冻结 = 每改一次代码就得刷一次基线 = 门禁作废。
- 限定名还顺带解决了同名歧义：`Foo.run` 和 `Bar.run` 根本不是同一个函数。

实现上还有一个坑：**mccabe 不只给函数建图**，还给语句块建图
（`If 686` / `Loop 12` / `TryExcept 30`）。这些图的名字里**嵌着行号**
（不能当基线键），而且它们的复杂度是**包含在**外层函数里的（重复计数）。
门禁因此只用 AST 的 `FunctionDef` 行号做过滤，把块级条目挡在外面
（当前跳过 17 条，数量记在快照的 `skipped_block_graphs` 里，不是静默丢弃）。

### 第一轮还债：persistence router 包

门禁建好之后，立刻用它自己照了一遍 `web/backend/routers/persistence.py`，
发现**整个模块 4 个端点全部超阈值**：

| 函数 | 之前 | 之后 |
|---|---|---|
| `get_forecast_confidence` | **32** | 7 |
| `get_forecast_multi` | **19** | 6 |
| `get_llm_cost_forecast` | 11 | 7 |
| `get_forecast` | 11 | 5 |
| `get_prediction_accuracy_by_day` | 11 | 5 |

做法只有一条原则：**只搬代码，不改行为**。
复杂度是「门禁关心的东西」，端点契约（ADR-0011 / 0012 / 0014）才是
「客户关心的东西」—— 后者必须一个字节都不变，由那三层门禁当场证明。

搬出来的不是「为了拆而拆的碎片」，是**本来就应该独立的三件事**：

1. **参数解析 + 白名单校验**（`_parse_forecast_methods` / `_parse_forecast_metrics` /
   `_validate_forecast_window` / `_validate_forecast_method`）
   —— 四个 forecast 端点原来各写一遍**看起来一样**的校验代码，
   其中 `/forecast` 那个还漏过一个分支（见下面「抓到的 bug 2」）。
2. **计算**（`_build_ensemble` / `_pick_best_method` / `_build_metric_confidence` /
   `_build_metric_comparison`）
3. **副作用**（`_save_method_prefs` / `_record_forecast_predictions` /
   `_backfill_forecast_actuals`）

拆完之后，「落库失败不能影响返回」这条业务规则，从**第 180 行的一个
try/except** 变成了**一个函数的名字** —— 这是拆分真正的收益，
不是行数变少了。

第二个端点 `get_forecast_multi` **复用**了第一个端点拆出来的 helper，
一行代码都没抄 —— 参数白名单逻辑现在全仓库只有一份。

## 备选方案

- **一刀切把 `--exit-zero` 去掉**：见「背景」。49 个函数一次爆红 = 巨型重构 + 规则被无视。
- **给复杂函数加 `# noqa: C901`**：把债藏起来，还债变成无限期。
  债还在，但它连「被看见」的机会都没有了 —— 比现在更糟。
- **自己实现一套复杂度算法**：和 flake8 对不上账，开发者本地看到的数字
  和 CI 报的不一样 → 门禁必被无视。
- **换 radon / 其他工具**：多一个依赖、多一套数字，而且和仓库里
  开发者已经在用的 `flake8 --max-complexity` **不是同一个尺子**。
- **只在 lint job 里打印警告**（= 现状）：什么都没变。
- **用「代码行数」当代理指标**：行数和复杂度不是一回事
  （一个 200 行的纯数据表 vs 一个 40 行的状态机），而且行数更容易被
  「拆成很多小函数」刷 —— **指标可被 gaming 的门禁不是门禁**。

## 后果

**正面**

- 复杂度从「装饰性配置」变成 **CI 常驻不变量**，第一次有了牙齿。
- 第一轮还债：`web/backend/routers/persistence.py` **整个模块**回到阈值内。
- 仓库超阈值函数数：**54 → 49**（486 个函数中）。
- 门禁上线**当天**就抓到两个真 bug（见下）。

**负面 / 技术债**

- **49 个函数仍然超阈值**（最高的 `health_check_deep` 36、
  `MultiAgentCoordinator.run_optimization_cycle` 31）。这条债还很大，
  门禁只是让它**不再变大**，并让还债变成可测量的、可评审的日常。
- 基线会漂：函数改名/删除后必须 `--write`。第三层规则会提醒，
  但**提醒不等于强制** —— 这是刻意的取舍（删函数不该被拦住）。
- 复杂度只是**一个**维度：它测不出「长函数但很简单」的问题，
  也不测得出「短函数但有隐蔽副作用」的问题。别把它当银弹。
- `web/backend/main.py` 里有一批 iter #83 拆 router 包时留下的
  **未使用的 import**（`csv` / `io` / `gzip` / `Header` / `Query` /
  `VRPSolver` / `_check_admin_token` 等）—— F401 不在 CI 严格子集里，
  所以没人报。这次没顺手清，因为「清 import」和「改复杂度」混在一个 PR 里
  会让评审者分不清哪个改动引入了问题 —— 和 ADR-0015 里
  「一次只拆一个包」是同一条原则。

### 门禁上线当天抓到的两个真 bug

**bug 1：测试的顺序依赖 —— CI 的绿灯是运气，不是证据**

`tests/test_forecast_method_prefs.py` 里有 6 个测试这样写：

```python
if backend_main.coordinator and backend_main.coordinator.persistence:
    backend_main.coordinator.persistence.save_method_pref(...)   # ← 写在**旧** coordinator 上
with TestClient(backend_main.app) as client:                    # ← startup 把 coordinator 换掉了
    resp = client.get(...)
```

`main.py` 的 `startup_event` 里有：

```python
global coordinator, data_generator
coordinator = MultiAgentCoordinator(config=world_config, db_path=db_path)
```

**每次起 TestClient，coordinator 都会被整个换掉**，换成一个绑在
`GL_DB_PATH`（默认 `data/simulation.db`）上的新实例。
所以那行 setup 写进了一个**没人会读的 DB**。

为什么以前没被发现？因为**全量跑的时候恰好是对的** ——
上一个跑过的测试留下的 ambient coordinator 绑的就是 `data/simulation.db`，
跟 startup 新建的那个**指向同一个文件**，看起来一切正常。
一旦单独跑这个文件（或换个测试顺序），ambient coordinator 绑的是别的文件，
setup 就静默丢了。

实测：`pytest tests/ -k forecast` → **2 failed**；
全量跑 → 全绿。**同一份代码，两种结果。**

修法：setup 一律放进 `with TestClient(...)` 里面，并在块内重新读
`backend_main.coordinator` —— 也就是**端点真正会用的那个** coordinator。
修完 `-k forecast` 从 2 failed 变成 **127 passed**。

这类测试比没有测试更危险：它看起来在验证「pref 被读到了」，
实际验证的是「ambient 状态碰巧对了」。

**bug 2：set 的迭代顺序 —— 同一请求在不同进程里可能选不同的 method**

`get_forecast` 里，`method=auto` 且没传 metrics 时的逻辑是：

```python
valid_metrics = {"cost_sek", "co2_kg", "util_pct", "matches"}   # ← set
targets = metrics_list or list(valid_metrics)
method = coordinator.persistence.get_best_method(targets[0]) or "linear"
```

`valid_metrics` 是 **set**，而字符串的 set 迭代顺序跟 `PYTHONHASHSEED` 走 ——
**每个进程可能不一样**。于是 `targets[0]` 到底是哪个 metric 是不确定的，
同一个请求在两个进程里可能**自动选了不同的 method**。

改成 `sorted(_VALID_FORECAST_METRICS)` → 确定性（`cost_sek` 优先）。

**没有报错、没有告警、CI 全绿** —— 这种 bug 才是最难在 review 里发现的，
而「复杂度门禁 + 契约门禁 + 确定性的测试顺序」三样一起上，
才第一次让它浮出水面。

## 度量

| 指标 | 之前 | 之后 |
|---|---|---|
| 复杂度规则在 CI 里**真的会红**吗 | ❌（`--exit-zero`） | ✅ |
| 仓库超阈值函数（486 个函数中） | 54 | **49** |
| `web/backend/routers/persistence.py` 超阈值端点 | 4 | **0** |
| 门禁上线当天抓到的真 bug | — | **2** |
| `pytest -k forecast` 结果 | 2 failed（顺序依赖） | **127 passed** |

---

_最后更新：2026-11-11（AI FDE 突击 · Day 80 补 ADR-0016，复杂度棘轮门禁 + persistence router 模块首次全绿）_
