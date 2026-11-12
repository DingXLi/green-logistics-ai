# ADR-0017: 测试顺序无关性门禁 —— 让「CI 绿灯」从运气变成证据

- **状态**：已接受
- **记录日期**：2026-11-12
- **相关代码**：`scripts/test_order_gate.py`, `scripts/order_shuffle.py`, `.github/workflows/ci-cd.yml`
- **相关 ADR**：ADR-0016（复杂度棘轮门禁，本 ADR 是它的同源延续）

---

## 背景

iter #84（ADR-0016）给「圈复杂度棘轮门禁」装上牙齿的**当天**，门禁就抓到了两个真 bug。
其中第二个（`set` 迭代顺序导致 `method=auto` 在不同进程自动选不同 method）很直接，
但第一个更值得记住，因为它攻击的是**我们整个 CI 的可信度**：

```bash
pytest tests/ -k forecast   # → 2 failed
pytest tests/               # → 全绿
```

根因在 `web/backend/main.py` 的 `startup_event`：

```python
global coordinator, data_generator
coordinator = MultiAgentCoordinator(...)   # 每次起 TestClient 整个换掉
```

于是 `tests/test_forecast_method_prefs.py` 的 6 个测试：
- 把 pref 写进**旧** coordinator 的 DB；
- 而那个 DB 已经被 `unlink` 过 → 测试断言的其实是一个**没人会读的 DB**；
- 全量跑时 ambient coordinator 恰好绑同一个 `data/simulation.db` → **绿灯**。

修完之后，根因其实**没有消失**，只是被绕过去了：
`main.py` 仍然有 `global coordinator`，仍然有 25 个测试文件直接写 ambient 全局单例。
换句话说，**「测试结果依赖执行顺序」这个失效模式还完整地躺在仓库里**。

这暴露了一个我们一直没正面回答的问题：

> **CI 的绿灯，到底证明了什么？**

我们当时的 CI 只证明了「**按这个顺序跑**是绿的」。
它没有证明「换个顺序还是绿的」。
而一个只在特定顺序下才通过的测试，和一个随机通过的测试，
对交付质量来说是**同一回事**——都是不可靠的证据。

对一个要交给客户的 FDE 来说，这个差别是本质的：
客户不会告诉你「先跑 A 再跑 B」，他只会跑一次，然后问「为什么昨天能跑今天不能」。

---

## 决策

**我们把「测试顺序无关性」变成一条 CI 常驻不变量**，
并且只用一个可复现的扰动去检验它 —— 不追求随机性，追求**可复现的扰动**。

具体做法分两步：

### 1. 静态圈定「嫌疑队列」（AST）

用 AST 扫 `tests/` 下所有测试文件，找出**直接给 ambient 全局单例赋值**的地方：

```python
backend_main.coordinator = fake_coord    # ← 命中
backend_main.some_local = x              # ← 不命中(不是进程级共享)
```

命中的测试文件就是「谁在污染全局状态」的嫌疑人，
也就是**顺序依赖最可能藏身的地方**。本仓库当前命中 **25 个文件 / 46 处写入**。

只测嫌疑队列、不测全仓是刻意的取舍：
全仓 × 3 个 seed ≈ 15 分钟 CI，成本不可接受；
而嫌疑队列命中率高得多，成本降到分钟级。

### 2. 动态扰动验证（可复现 seed）

用 N 个**固定 seed** 把嫌疑队列的测试顺序打乱，每个 seed 跑一次，
产出 JUnit XML，然后**逐个 testcase 比对 outcome**（passed / failed / skipped / error）。

- 任何 testcase 在不同 seed 下 outcome 不一致 → **红**。
- 全部一致 → **绿**。

### 3. Fail-closed

一个 testcase 都没收集到 → **红**。

这条是刻意的：实现过程中第一版就踩了这个坑——
`pytest` 只传了文件名（`test_foo.py`）而不是路径（`tests/test_foo.py`），
pytest 静默地「0 tests ran」，而我最初的门禁判定是**绿的**。
一个什么都没验证的扰动报绿灯，等于没有门禁。

> 这和 iter #81（ADR-0013）把 endpoint policy 从 fail-open 换成 fail-closed 是同一条原则：
> **沉默的缺口必须是红的。**

---

## 备选方案

### 否掉：直接用 `pytest-randomly` 等第三方插件

- **否掉原因**：为了 100 行代码引入一个外部依赖不划算；
  更重要的是第三方插件**默认就会打乱顺序**，意味着所有现有 CI 语义都被改变。
  我们要的是**默认零行为**——不设 env 时插件一个字节都不改，
  这样现有 CI 语义 100% 不变，扰动是**显式开启**的。

### 否掉：全仓随机顺序（每 PR 都跑全仓 × N seed）

- **否掉原因**：CI 成本 ≈ 15 分钟，且**信噪比更差**。
  全仓里有大量与 ambient 状态无关的纯函数测试，打乱它们只是在浪费 runner。
  圈定嫌疑队列后，同样的时间投在**真正可能出问题**的地方。

### 否掉：只用 `-p no:randomly` 之外的 `--lf` / 分片跑

- **否掉原因**：这些是**选子集**的机制，不是**扰动顺序**的机制。
  「只跑失败过的测试」恰恰是在**缩小**顺序空间，方向反了。

### 否掉：靠人 review 发现顺序依赖

- **否掉原因**：**人 review 看不到顺序依赖**。
  这类 bug 的签名就是「在 CI 上是绿的」——它躲过了所有基于「当前结果正确」的检查。
  要发现它，必须**主动制造一个不同的执行环境**。

### 否掉：只修 `global coordinator` 这一处，不做门禁

- **否掉原因**：这才是真正要否掉的。
  修一个 bug 是**点修复**，失效模式还在。
  我们要的是让**同类 bug 再也藏不住**——这是 ADR-0010 以来一直坚持的立场：
  **门禁比修复更重要，因为门禁防的是一整类问题。**

---

## 后果

### 正面

1. **「CI 绿灯」的含义被抬高了**：
   从「按这个顺序跑是绿的」升级为「**换个顺序还是绿的**」。
   这是从「运气」到「证据」的跃迁。

2. **失效模式被整类防住**：
   不是修了 `global coordinator`，而是让**任何**「测试写 ambient 单例」
   的新代码都会立刻被圈进嫌疑队列、被扰动检验。

3. **失败即定位**：
   报告直接给出「哪个 testcase、在哪个 seed 下翻转了」，
   不是一句「有 flaky」让人自己猜。

4. **可复现**：
   同一个 flaky 每次都在同一处炸，修完就不会复发。
   随机测试的价值 90% 在于**可复现**，剩下 10% 在于覆盖面。

### 负面 / 技术债

1. **CI 多了一个 job**：嫌疑队列 × 3 seed ≈ 2-4 分钟（不含全仓 fast-tests）。
   已用独立 job 隔离，不阻塞主测试反馈环。

2. **队列是「当前已知嫌疑人」**：AST 只认**直接赋值**这一种模式。
   如果有人通过 `setattr()` / `globals()` / `exec()` 改 ambient 状态，门禁看不见。
   → **这是明确的覆盖边界**，已写进脚本 docstring，不假装覆盖。

3. **需要 DB fixture 干净**：
   扰动跑会写 `data/simulation.db`。CI 里必须先跑 `seed_ci_fixture.py`
   造确定性 fixture，否则「换一个顺序」会把「fixture 不确定」误判成「顺序依赖」。
   → 这是本 ADR 与 ADR-0006（CI 确定性 fixture）的耦合点。

4. **不是所有顺序依赖都能靠扰动抓到**：
   扰动能抓到的是「**换序后结果翻转**」这一类。
   更隐蔽的「换序后仍然绿，但写到了错误的 DB」抓不到——
   那类要靠断言本身是否指向了真实对象。

---

## 与 ADR-0016 的关系

这两条 ADR 是同一条线索的两端：

| | ADR-0016 复杂度棘轮 | ADR-0017 顺序无关性 |
|---|---|---|
| 攻击的对象 | **代码**的复杂度 | **测试**的可信度 |
| 核心命题 | 债务只许降，不许涨 | 绿灯必须是证据，不是运气 |
| 门禁形态 | AST + 基线棘轮 | AST 圈嫌疑 + 动态扰动 |
| 抓到的真 bug | 2 个（顺序依赖 + set 迭代顺序） | 门禁本身抓 1 个（0 testcase 静默绿灯） |

**同一个故事的两幕**：
ADR-0016 的门禁**当天**抓到了一个攻击 CI 可信度的 bug，
而那个 bug 揭示的失效模式**比我们以为的更大**，
于是有了 ADR-0017 把「CI 绿灯」重新定义了一遍。

这正是 FDE 角色最值钱的能力：
**不是把 bug 修掉，是把「怎么知道没有 bug」这件事本身变得可信。**

---

## 门禁上线当天抓到的真 bug

第一次跑 3 个 seed 就有收获 —— 嫌疑队列 400 个 testcase 里翻出 **1 个真顺序依赖**：

```
🔴 tests.test_persistence_efficiency.TestEfficiencyMetricsAPI::test_endpoint_503_when_no_persistence
     seed [202, 303] → failed | seed [101] → passed
```

失败信息：`sqlite3.OperationalError: no such table: optimization_cycles`。

### 根因：这个测试**从来没装过** fake coordinator

```python
def test_endpoint_503_when_no_persistence(self, monkeypatch):
    monkeypatch.setenv("GL_DB_PATH", "/tmp/_no_exist.db")   # ← 没人读它
    orig_coord = backend_main.coordinator
    try:
        client = TestClient(backend_main.app)               # ← 不进 with
        r = client.get("/api/persistence/efficiency-metrics")
        assert r.status_code in (200, 503)                   # ← 断言松到能蒙
```

三个错误叠在一起：

1. **`GL_DB_PATH` 是死代码** —— `TestClient(app)` 不以 context manager 方式进入时
   **不触发 lifespan**，`startup_event` 根本没跑 → 那个环境变量从头到尾没人读过。
2. **断言松到能蒙** —— `in (200, 503)` 同时接受两种结果，等于**不验证任何东西**。
   端点真正该返回 503。
3. **依赖 ambient coordinator** —— 端点读到的是**上一轮遗留的** coordinator：
   - 上一轮留了个真 coordinator（DB 已被 unlink）→ **500**（no such table）
   - 上一轮留了个 `persistence=None` 的 fake → **503**（蒙对）

→ 结果取决于「谁先跑」。**seed 101 恰好蒙对，所以这个测试一直是绿的。**

### 修法：跟它两个兄弟测试一样

同一个文件里另外两个 503 测试（第 338 行 / 第 463 行）**写法是对的**：
自己装一个 `persistence=None` 的 fake coordinator，断言**精确的** 503。
本次把这个测试改成一样的写法 → 3 个 seed 全绿（原本 101 绿 / 202、303 红）。

> 这是一个很好的教训：
> **一个「断言松到能蒙」的测试，等于一个把 bug 藏起来的测试。**
> 它不只是顺序依赖，它是**顺序依赖能够长期存活**的原因。

---

## 门禁自证：它真的会红吗？

一个只报绿的门禁没有价值。所以仓库里放了一个**故意顺序依赖**的靶子
（`selftest/test_order_gate_selftest.py`，**故意放在 `tests/` 外面**，
不污染默认 collection）：

```bash
python scripts/test_order_gate.py --cohort selftest/test_order_gate_selftest.py --seeds 11,22,33,44,55
# → ❌ 发现 1 个顺序敏感的测试
#   🔴 test_zz_b_depends_on_a  seed [22,33,55] → failed | seed [11,44] → passed
# → exit 1
```

**门禁有牙齿，实测确认。**

---

## 门禁长什么样

CI 里新增独立 job：

```yaml
  test-order:
    name: Test order independence (iter #85: ADR-0017)
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -r requirements.txt pytest pytest-asyncio pytest-timeout
      - run: python scripts/seed_ci_fixture.py     # 确定性 fixture (ADR-0006)
      - name: Order independence gate (ADR-0017)
        run: python scripts/test_order_gate.py --seeds 101,202,303
```

故意改测试(比如让一个测试依赖另一个测试先跑)时，这一步会红。

---

_最后更新：2026-11-12（AI FDE 突击 · Day 81，ADR-0017 测试顺序无关性门禁：让「CI 绿灯」从运气变成证据）_
