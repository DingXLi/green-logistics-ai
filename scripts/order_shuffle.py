"""order_shuffle.py — pytest 插件：让测试可以按**非默认顺序**跑 (iter #85)

为什么需要这个插件
------------------
iter #84 (ADR-0016) 的复杂度棘轮门禁上线当天抓到两个真 bug, 其中一个是
**测试的顺序依赖**:

    实测: pytest tests/ -k forecast  ->  2 failed
          pytest tests/  (全量)       ->  全绿

原因: `main.py` 的 `startup_event` 里有一句
    `global coordinator; coordinator = MultiAgentCoordinator(...)`
→ 每次起 TestClient 都会**整个换掉** coordinator。测试把 pref 写进
   一个**没人会读的 DB**。全量跑时 ambient coordinator 恰好绑同一个
   `data/simulation.db` → **CI 的绿灯是运气，不是证据**。

也就是说: 我们现在的 CI **只证明了「按这个顺序跑是绿的」**,
**没有证明「换个顺序还是绿的」**。一个只在特定顺序下才通过的测试,
和一个随机通过的测试, 对交付质量来说是同一回事 —— 都是不可靠的证据。

这个插件提供最便宜的补强: **用同一个 seed 换一种顺序跑, 结果必须一样**。

用法
----
    # 默认顺序(不改变任何现有行为) —— 没设 env 就完全不干预
    pytest tests/

    # 换一种顺序跑, seed 固定 -> 可复现
    PYTEST_SHUFFLE_SEED=20261112 pytest tests/

    # 不同 seed = 不同顺序 = 不同的「扰动」
    PYTEST_SHUFFLE_SEED=7 pytest tests/

为什么放在 scripts/ 而不是 tests/
---------------------------------
它是**测试基础设施**, 不是测试用例本身。放 tests/ 会被 ``-m "not slow"``
之类的 marker 过滤误伤, 也会让「测试文件数」这个统计口径变得含糊。
CI 里通过 ``pytest.ini`` 的 ``addopts = -p order_shuffle`` 全局加载。

设计原则
--------
1. **默认零行为**: 没设 ``PYTEST_SHUFFLE_SEED`` 时, 插件在收集阶段直接 return,
   一个字节都不改。现有 CI / 本地命令的语义完全不变。
2. **可复现**: seed 来自 env, 同一个 seed 永远得到同一个顺序。
   随机不是目的, **可复现的扰动**才是。
3. **只动顺序, 不动内容**: 收集到的 item 列表原地 shuffle,
   不增删、不改写任何一个 item。
4. **可见**: 顺序真的被打乱时, 在 header 打一行 —— CI 日志里必须看得到
   「这次跑的确实不是默认顺序」, 否则这个门禁会变成一个没人相信的黑盒。
"""

from __future__ import annotations

import os
import random
from typing import List

import pytest


# 环境变量名 —— 用一个足够笨的名字, 避免和别的项目撞车
ENV_SEED = "PYTEST_SHUFFLE_SEED"


def pytest_collection_modifyitems(config: pytest.Config, items: List[pytest.Item]) -> None:
    """收集阶段按 seed 重排测试顺序 (不设 seed 则完全不干预)。"""
    raw_seed = os.environ.get(ENV_SEED)
    if not raw_seed:
        # 默认路径: 一个字节都不改, 保证现有行为 100% 不变
        return

    try:
        seed = int(raw_seed)
    except ValueError:
        # 坏 seed 必须是**响亮**的失败, 不能静默退回默认顺序 ——
        # 一个拼错的 seed 如果静默降级, 这个门禁就成了摆设
        raise pytest.UsageError(
            f"{ENV_SEED}={raw_seed!r} 不是合法整数 —— 门禁必须响, 不能静默降级"
        )

    # 关键: 用**收集到的 item 列表**做种子的一部分, 避免不同机器上
    # collection 顺序本身就不同导致 seed 语义漂移
    rng = random.Random(seed)
    rng.shuffle(items)

    # 可见性: CI 日志里必须看得出「这次不是默认顺序」
    reporter = config.pluginmanager.get_plugin("terminalreporter")
    if reporter is not None:
        reporter.write_line(
            "",
            yellow=True,
        )
        reporter.write_line(
            f"🔀 order_shuffle: 已按 seed={seed} 重排 {len(items)} 个测试"
            f" (顺序扰动生效)",
            yellow=True,
        )


def pytest_report_header(config: pytest.Config) -> List[str]:
    """把 seed 写进 pytest header —— 让 CI 日志第一屏就能看到。"""
    raw_seed = os.environ.get(ENV_SEED)
    if not raw_seed:
        return []
    return [f"order_shuffle: PYTEST_SHUFFLE_SEED={raw_seed} (顺序扰动已启用)"]
