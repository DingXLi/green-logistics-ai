"""门禁自证: 故意制造一个「换顺序就翻转」的结果 (iter #85 自测, 不进 CI)

这个文件**故意**是顺序依赖的:
- ``test_zz_b_depends_on_a`` 断言一个由 ``test_zz_a_sets_state`` 写下的模块级状态
- 如果 B 先跑 -> 断言失败; 如果 A 先跑 -> 通过

它是 ``scripts/test_order_gate.py --cohort`` 的**验证靶子**:
    python scripts/test_order_gate.py --cohort tests/test_zz_order_gate_selftest.py

必须报红。如果报绿, 门禁就是假的。

注意: 本文件不在 AST 嫌疑队列里(它不写 ambient 单例),
所以正常跑全仓时它会被 ``--ignore`` 排除, 不会污染 CI。
"""

from __future__ import annotations

import pytest

# 故意用模块级状态来制造顺序依赖
_SELFTEST_STATE = {"set_by_a": False}


def test_zz_a_sets_state():
    """先跑它 -> 后面的依赖者会通过"""
    _SELFTEST_STATE["set_by_a"] = True
    assert _SELFTEST_STATE["set_by_a"] is True


def test_zz_b_depends_on_a():
    """故意依赖 A 先跑 —— 换顺序就会翻转"""
    assert _SELFTEST_STATE["set_by_a"] is True, (
        "B 先跑了: 这是**故意**的顺序依赖, 用来验证门禁真的会红"
    )
