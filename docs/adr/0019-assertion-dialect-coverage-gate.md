# ADR-0019: 断言方言覆盖门禁 —— 「门禁只看得见 72%, 它的绿灯就只值 72%」

- **状态**: 已接受 (2026-11-14)
- **取代/延续**: ADR-0018 (松断言门禁) —— 本 ADR 是它的**自我修正**
- **相关**: ADR-0016 (复杂度棘轮) · ADR-0017 (测试顺序无关性) · ADR-0010~0014 (契约门禁)

---

## 一、背景: 门禁自己瞎了一半

ADR-0018 上线的时候写得很满:

> 让「断言松到能蒙」这件事在 CI 里**不可能悄悄发生**。

上线第二天(iter #87)一做方言普查, 发现这句话只对 72% 成立。

这个仓库的断言有**两种方言**:

| 方言 | 条数 | 占比 | ADR-0018 的门禁看得见吗 |
|---|---:|---:|---|
| `assert` 语句 | 3573 | 72% | ✅ |
| unittest `self.assertX(...)` | 1384 | 28% | ❌ **完全失明** |

原因很朴素: 门禁只解析 `ast.Assert` 节点。unittest 的 `self.assertIn(...)`
在 AST 里是**方法调用**, 不是断言语句 —— 门禁压根没往那儿看。

### 失明的后果有多具体

78 条这样的断言, 在门禁眼皮底下活了一个星期:

```python
self.assertIn(resp.status_code, (200, 503))   # 端点整个 503, 测试照样绿
```

它们分布在 20 个文件里, 覆盖 `/api/persistence/*` 的 82 个只读端点的一半。
**ADR-0018 宣布「把整类消灭了」, 实际只消灭了自己会读的那一半。**

而且更糟的是: 这些测试**看起来是有覆盖的**。测试名、断言行数、覆盖率报告
全都在说「这 82 个端点被测过了」。实际上它们证明的是:
「端点返回 200 **或者** 返回 503」—— 而 503 意味着**持久化没初始化, 端点整个没干活**。

> **端点整个挂掉, 测试照样绿, 而且绿得很整齐。**

---

## 二、决策

### 决策 1: 门禁必须读得懂仓库里出现的**每一种**断言方言

`loose_assert_lint.py` 升级为**双方言**:

- `assert` 语句(原有)
- unittest 的 `self.assertX(...)`(新增)

同一套规则(R1 MULTI_STATUS_IN / R2 MULTI_STATUS_OR / R3 CONDITIONAL_ASSERT)
对两种方言**同时生效**。

### 决策 2: 视野自证 —— 「有一种断言我没读懂」也要判红

这是本 ADR 真正的重点。

ADR-0017 已经有一版 fail-closed:「一个文件都没扫到 → 红」(门禁自己瞎了)。
iter #87 把它升级成:

> **门禁必须普查仓库里出现的每一种断言方言。只要出现一种它不认识的 → 红。**

理由: **门禁的覆盖面 = 门禁的价值上限**。
一道只看得见 72% 断言的门禁, 它的绿灯只值 72% —— 而它**看上去**是 100%。
这比没有门禁更危险: 没有门禁你知道没人管, 有个半盲的门禁你**以为**有人管。

实现: 扫描时统计方言 → 输出「视野覆盖 %」→ 覆盖率 < 100% 判红,
并列出「我不认识的那个写法」在哪个文件哪一行。

### 决策 3: 新增 R4 `MULTI_STATUS_GUARD` —— 守卫本身在蒙

R3 管的是「守卫里有没有断言」。R4 管的是「**守卫自己在选哪条路**」:

```python
if resp.status_code in (200, 503):   # ← 测试有两条通向绿的路
    data = resp.json()
assert data is not None
```

一个多值守卫 = 端点坏掉时, 测试走哪条分支都能绿。
(本仓库当前 0 处 —— 规则先立起来, 防止以后长出来。)

---

## 三、还债: 把 78 条「什么都没证明」的绿变成精确断言

光改门禁不够 —— 门禁要红, 得先把债还了。

### 3.1 为什么这些端点必然 503

`/api/persistence/*` 的 82 个端点, 第一件事都是:

```python
coordinator = _coordinator()
if coordinator is None or coordinator.persistence is None:
    raise HTTPException(status_code=503, detail="Persistence not initialized")
```

而端点测试用 `TestClient(app)`(不走 lifespan → 不跑 `startup_event`)
→ 全局 `coordinator` 永远是 `None` → **端点必然 503**。

也就是说: 那 78 条断言**实际上一直在走 503 分支**, 从来没有验证过任何返回体。

### 3.2 怎么还: 造依赖, 而不是许愿

`tests/fake_world.py`(ADR-0018 建的共享 fake 世界)新增:

```python
class PersistenceCoordinatorMixin:
    def install_persistence_coordinator(self, db_path=None):
        """接上一个指向临时 SQLite 的真 Persistence, 退出时自动还原。"""
```

- 用**真的** `Persistence` 指向临时 SQLite(不是 mock)→ 端点行为完全确定
- 退出时 `addCleanup` 还原旧 coordinator → 不污染别的测试(ADR-0017 的顺序无关性)
- 端点只碰 `.persistence` → fake 面很窄, 不需要起完整 coordinator

> **注意**: 注入 fake 时**必须**用 `TestClient(app)`, 不能用 `with TestClient(app) as client:`
> —— 后者走 lifespan → `startup_event` 把全局 coordinator 整个换掉(ADR-0017 的老坑)。

### 3.3 改写结果

| 原来的写法 | 现在 | 条数 |
|---|---|---:|
| `self.assertIn(resp.status_code, (200, 503))` + `if resp.status_code == 200:` 守卫 | `self.assertEqual(resp.status_code, 200)` + 守卫拆掉, 内容断言**无条件**执行 | 51 |
| `self.assertIn(resp.status_code, (400, 503))` | `self.assertEqual(resp.status_code, 400)` | 22 |
| `self.assertIn(resp.status_code, (422, 503))` | `self.assertEqual(resp.status_code, 422)` | 2 |
| `self.assertIn(resp.status_code, (400, 422))` | `self.assertEqual(resp.status_code, 422)` | 1 |
| `self.assertIn(resp.status_code, (401, 200))` | `self.assertEqual(resp.status_code, 401)` | 1 |
| `self.assertIn(resp.status_code, (200, 401, 503))` | `self.assertEqual(resp.status_code, 200)` | 1 |

**守卫也一并拆掉**: 原来

```python
self.assertIn(resp.status_code, (200, 503))
if resp.status_code == 200:
    data = resp.json()
    self.assertIn("n_cycles", data)
```

里的 `self.assertIn("n_cycles", data)` 在端点 503 时**根本不会执行** ——
R3 说的就是这个。现在它无条件执行。

### 3.4 验证: 同一份代码, 换门禁前后

| | ADR-0018 的门禁 | ADR-0019 的门禁 |
|---|---:|---:|
| 改写前(旧树) | **0 违规** | **166 违规**(78 MULTI_STATUS_IN + 88 CONDITIONAL_ASSERT) |
| 改写后(新树) | 0 违规 | **0 违规** |

「0 → 0」是门禁**最危险的状态**: 它绿灯, 但它根本没在看那 78 条。

---

## 四、门禁的视野(改写后)

```
断言方言普查(门禁的视野):
  ASSERT_STMT            3573  ✅ 已建模
  UNITTEST_METHOD        1384  ✅ 已建模
  PYTEST_RAISES            29  ✅ 已建模
  合计                   4986  视野覆盖 100.0%
```

---

## 五、门禁自证

`selftest/test_loose_assert_selftest.py` 新增 7 个用例(30 passed):

1. unittest 方言的 R1 / R2 / R3 必须抓得到
2. R4 多值守卫必须抓得到
3. 精确的 unittest 断言**不能**误报
4. 不认识的方言(`self.assertThat(...)`)必须被**报出来**
5. 端到端验收线: `tests/` 里一条松断言都不许剩 + 视野必须 100%

并新增 CI 步骤 `Loose assertion gate selftest (iter #87)` ——
**门禁自己的自测必须进 CI**, 否则「有牙齿」只是一句承诺。

---

## 六、教训(写给未来的自己)

1. **「我消灭了一整类」这句话, 要用「分母」来证明, 不能用「感觉」**。
   ADR-0018 说它消灭了「松断言」这一整类 —— 实际上它消灭的是
   「松断言 ∩ assert 语句」。「整类」这个词, 必须能写成
   `命中数 / 总数`, 且分母是**全部**, 不是「我看得见的那部分」。

2. **门禁的覆盖面是门禁的属性, 不是门禁作者的属性**。
   「我以为我管住了」和「我确实管住了」之间, 差一个
   「方言普查 + 视野百分比」。

3. **一个绿色的测试, 可能是「什么都没证明」的绿色**。
   `assertIn(status, (200, 503))` 的信息量是: 「端点活着, 或者端点没活着」。
   这种断言**看起来**比 `assertEqual(status, 200)` 更有兼容性, 实际上更有害 ——
   它把「我不敢确定」写成了「我接受两个答案」。

4. **还债的方向是「造依赖」, 不是「许愿」**。
   「端点返回什么我不知道, 那我两个都接受」= 把不确定性固化进断言。
   正确做法是**把不确定性从环境里拿走**(注入确定性依赖),
   然后断言就可以精确到唯一结果。
