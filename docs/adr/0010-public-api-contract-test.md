# ADR-0010: 公开 API 契约测试 —— 把「重构不改 API」变成 CI 里的常驻不变量

- **状态**：已接受
- **记录日期**：2026-11-04
- **相关代码**：`scripts/gen_api_contract.py`,
  `tests/test_persistence_api_contract.py`,
  `tests/persistence_api_contract.json`,
  `agents/persistence/`（被契约约束的公开面）
- **关联**：[ADR-0009](0009-persistence-package-split.md)（持久化层拆分）·
  [ADR-0006](0006-ci-strategy-and-deterministic-fixture.md)（CI 策略）·
  [ADR-0005](0005-persistence-sqlite.md)（持久化与 schema）

## 背景

ADR-0009 把 `agents/persistence.py`（9,949 行 / 423 KB）拆成 `agents/persistence/` 包，
当时用来证明「公开 API 一个字没改」的手段是：

> **一次性人工双向比对**：老类 114 个方法 vs 新类 114 个方法，
> 方法名、参数名、参数默认值逐个对比 → 0 差异。

这个比对本身是**正确的**，但它有两个结构性缺陷：

1. **它只在那天成立**。
   拆完之后，任何一个后续 PR 都可以：
   - 把 `get_summary(limit=50)` 的默认值改成 `limit=100`；
   - 把 `record_supply` 的参数 `available_tons` 改名成 `tons`；
   - 直接删掉一个「看起来没人用」的方法。

   这些改动**都不会让任何测试失败** —— 因为 1,600+ 个测试用例测的是**行为**，
   不是**签名**。参数改名只在调用点没同步时才会炸（而且是运行期 `TypeError`，
   不是 CI 期）。默认值改成 100 更糟：所有测试照样绿，只有生产数据变了。

2. **它的结论没有留在仓库里**。
   「我们比对过，0 差异」这句话写在 ADR 里，但**无法被第三方复现**。
   面试官问「你怎么保证重构没改 API」，最好的答案是指着一个 CI job 说
   「这个每次 push 都跑」—— 而不是指着一段一次性脚本说「当时我们跑过」。

第 2 条对 FDE 岗特别重要：这个岗位考的就是「**你交付的东西能不能被别人接手**」。
一个只存在于对话历史里的验证过程，等于不存在。

### 一个更隐蔽的问题：mixin 架构的 stub 抢跑

ADR-0009 用了 mixin 组合，其中 `_contract.py` 声明了 3 个**签名契约**
（`get_cycle_history` / `get_cycle_detail` / `get_summary`），
函数体是 `...`（返回 `None`），真实现分别在 `_cycles.py` / `_analytics.py`。

Python 的 MRO 决定谁生效。当前顺序是：

```
Persistence → CycleMixin → ... → PersistenceBase → ForecastMathMixin → SharedQueryMixin → object
```

`CycleMixin` 在 `SharedQueryMixin` **前面**，所以真实现赢。

但这个正确性**只由「基类书写顺序」隐式保证** —— 没有任何东西在守护它。
如果哪天有人调整 mixin 顺序，或者把某个方法不小心写进了 `SharedQueryMixin`，
那么 `db.get_summary()` 会**静默返回 `None`** 而不是抛 `AttributeError`：

- 不是 `AttributeError`（因为 stub 确实定义了同名方法）；
- 不是 `TypeError`（签名对得上，stub 的签名就是照着实现抄的）；
- 就是 `None`，然后在下游 `for row in result` 处炸成 `TypeError: 'NoneType' is not iterable`。

**这种 bug 靠人眼 review 几乎不可能发现** —— diff 里只是少了一个方法名，
多了一行 `...`。

## 决策

**把「公开 API 不变」从一次性人工比对，升级为仓库里的常驻不变量：**

1. **机械抽取**：`scripts/gen_api_contract.py` 用 `inspect` 从类对象抽出
   公开 API 的完整签名（方法名、参数名、参数默认值、返回注解、公开常量的键与长度），
   写成 `tests/persistence_api_contract.json`。
   **不手写** —— 手抄 104 个签名一定会抄错，而且没人能核对。
2. **每次 CI 比对**：`tests/test_persistence_api_contract.py` 读基线 JSON，
   与当前类对象逐字比对，任何差异都让 CI 失败，并打印出**哪几个方法变了、变前变后是什么**。
3. **基线本身也被守卫**：断言基线里仍然是 104 个方法 + 4 个常量，
   防止有人为了让 CI 变绿而手工削掉一半基线。
4. **mixin 骨架单独守卫**：
   - 12 个领域 mixin + 3 个基础 mixin 必须都在 MRO 里；
   - **mixin 顺序**被逐字冻结（顺序变了 → 方法解析顺序变了）；
   - `_contract.py` 的 3 个签名契约必须**解析到真实现**，不能解析到 stub。
5. **结构变更不算破坏**：内部重新组织（方法搬到另一个模块）**不**让 CI 变红，
   但会被打印成 informational 提示 —— 评审时能一眼看见方法被挪了窝。

### 关键设计：断言什么 / 不断言什么

| 维度 | 是否断言 | 理由 |
|---|---|---|
| 方法增删 | ✅ 断言 | 调用方直接依赖 |
| 参数名 / 默认值 | ✅ 断言 | 改默认值 = 改行为，且测试测不出来 |
| 返回注解 | ✅ 断言 | 影响下游类型与可读性 |
| 公开常量的键与长度 | ✅ 断言 | 业务语义（四季 / 5 种碳排因子） |
| mixin 组成与顺序 | ✅ 断言 | 决定「哪个实现生效」 |
| 方法归属哪个模块 | ❌ 不断言 | 那是**重构自由**，不是 API 破坏 |
| 私有成员 `_foo` | ❌ 不断言 | 不是公开面 |

最后两行是这条 ADR 最重要的判断：
**把「内部怎么组织」和「外部看到什么」分开，才能让契约测试既守住底线、又不阻碍重构。**
一个连内部重排都报错的契约测试，团队很快就会开始 `--write` 刷基线 —— 那它就废了。

### 变更流程（故意改 API 时）

```bash
python scripts/gen_api_contract.py --write   # 刷新基线
# 在 PR 描述里写清楚：哪些方法变了 / 为什么 / 下游调用点怎么改
```

这条流程本身就是治理：**API 变更必须是显式的、可 review 的、有说明的**，
而不是「顺手改了个默认值，CI 没报错所以就过去了」。

## 备选方案

- **不改默认值也测一下**（写一个跑真实数据的方法级快照测试） —— 否掉：
  运行期快照对「方法增删」完全不敏感（少一个方法，快照照样过），
  而且要为 104 个方法各造一份 fixture，代价极高；
  签名比对是 O(1) 成本覆盖全部方法，成本收益比完全不同。
- **只在 `__init__.py` 里写 `__all__` 声明来约束公开面** —— 否掉：
  `__all__` 只影响 `from x import *`，**不约束属性访问**
  （`db.get_summary()` 照样能调）；而且它不记录签名 ——
  参数默认值改了、`__all__` 一个字都不用动。
- **用 `mypy --strict` 或 `pyright` 的 `--reportIncompatibleMethodOverride` 代替** —— 否掉：
  这些工具管的是**类型**兼容性，管不了「默认值从 50 改成 100」
  （类型没变）和「删了一个没人调的方法」（没人调 = 类型检查器不报）。
  契约测试补的是它们管不到的**签名形状**。
- **只做一次性比对，把结论写进 ADR 就够了**（ADR-0009 的做法） —— 否掉：
  就是这次要解决的问题 —— 结论只在那一天成立，无法复现。
- **把契约快照放进 `agents/persistence/` 而不是 `tests/`** —— 否掉：
  这是一份**测试资产**，不是运行时依赖。
  放进包目录会让「运行时代码」和「验证运行时代码的东西」耦合在一起，
  而且打包时容易漏掉或反过来 —— 放在 `tests/` 下，
  `pytest tests/` 天然会跑它，不需要额外配置。

## 后果

**正面**

- **「公开 API 不变」从声明变成了不变量**。
  下一次任何人动签名，CI 立刻红，并告诉他**哪个方法、变前变后**。
- **契约可复现、可审阅**：`tests/persistence_api_contract.json` 就在仓库里，
  任何人 `git diff` 就能看到基线；面试时可以直接指这个文件。
- **补上了 mixin 架构最隐蔽的漏洞**：3 个签名契约 stub 抢跑导致的
  「静默返回 None」，现在有专门的断言守着（`test_shared_contract_methods_resolve_to_real_implementations`）。
- **基线防削**：契约测试自己也被守卫 —— 不能为了让 CI 绿而削基线。
- **零运行期成本**：契约只在测试期比对，不进生产路径；
  基线文件 ~40 KB，一次 JSON 解析，测试耗时 <3 秒。
- **和 ADR-0009 形成闭环**：
  ADR-0009 说「公开 API 零变化」，ADR-0010 说「**而且以后一直会是这样**」。

**负面 / 技术债**

- **基线文件会漂**：如果未来真的要做破坏性 API 变更，
  基线必须 `--write` 更新 —— 这**正是设计意图**（变更要显式），
  但如果团队习惯性无脑 `--write`，这条 ADR 就退化成摆设。
  缓解手段：基线更新必须走 PR review，diff 里能直接看到签名变化。
- **104 个方法的签名是「快照」而不是「语义」**：
  它能证明「签名没变」，但不能证明「实现是对的」——
  后者是 1,600+ 行为测试的职责。两者互补，不互相替代。
- **默认值的类型敏感**：契约只比对默认值的 `repr`。
  极端情况（比如默认值是个自定义对象）会退化成 `<TypeName>`，
  失去区分度 —— 当前的 104 个方法默认全是 `int/float/bool/str/None`，
  够用；真出现复杂默认值时再升级抽取逻辑。
- **`_contract.py` 的设计本身仍有债**：签名契约（stub）只给类型检查器看，
  运行期它就是个 `None` 陷阱。
  更好的设计可能是把 3 个共享查询**真的抽成一个基类**（实现 + 契约合一），
  而不是「契约一份、实现一份」。今天先用断言把风险守住，
  真正的解法留给下一次重构 —— **不为假想的未来提前付复杂度成本**。

---

## 验证记录（2026-11-04）

**基线与重构前单文件版本对比**（机械抽取，非人工核对）：

```
$ python scripts/gen_api_contract.py --compare-legacy /tmp/legacy_persistence.py
OK: public API identical to legacy baseline (104 methods, 4 constants)
```

→ 与 ADR-0009 拆分前的 `agents/persistence.py`（9,949 行）**逐字一致**：
104 个方法、4 个公开常量，方法名 / 参数名 / 默认值 / 返回注解全部相同。
**「拆分没改公开 API」这句话，从此有了机器可复现的证据。**

**漂移检测验证**（故意改坏 → 确认 CI 会红）：

| 注入的破坏 | 捕获它的断言 | 报错 |
|---|---|---|
| `begin_cycle` 的 `n_supply_offers` 默认值 0 → 1 | `test_public_api_matches_committed_baseline` + `test_generator_reports_no_drift` | `CHANGED method: begin_cycle` |
| `should_auto_vacuum` 改名为 `_should_auto_vacuum` | 同上 | `REMOVED method: should_auto_vacuum` |
| 从类基类列表里删掉 `SolverMixin` | `test_all_domain_mixins_present_in_mro` + 契约比对 | `MRO 里少了领域 mixin: ['SolverMixin']` + 5 个方法 REMOVED |

三次注入全部被捕获，随后**完整还原**并确认 `git diff` 为空。

**本仓库自身**：
- 契约测试：**11 passed**
- 契约文件：`tests/persistence_api_contract.json`（104 methods / 4 constants）
- 生成器：`scripts/gen_api_contract.py`（默认 diff 模式 / `--write` 刷新 / `--legacy` 抽历史基线 / `--compare-legacy` 对比历史版本）

---

_相关：[ADR-0009](0009-persistence-package-split.md) 持久化层拆分 ·
[ADR-0006](0006-ci-strategy-and-deterministic-fixture.md) CI 策略_
