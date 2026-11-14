"""
门禁自证: 松断言门禁必须真的会红 (iter #86 自测, 不进默认 testpaths)

这个文件**故意**是「松断言门禁」的靶子, 但它自己**不含**任何松断言 ——
所有坏代码都写在字符串里, 落到 tmp_path 的临时文件上再扫。

它验证三件事:
1. **有牙齿** —— 两条规则(MULTI_STATUS_IN / MULTI_STATUS_OR)都能抓到松断言。
2. **不误伤** —— 精确断言 / 区间断言 / 值域断言 / 单元素容器都不能被当成松断言。
3. **fail-closed** —— 登记册陈旧、理由不在封闭枚举里、一个文件都没扫到 → 全部判红。

一个只报绿的门禁没有价值。
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, List

import pytest

_SCRIPTS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"
)
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import loose_assert_lint as gate  # noqa: E402


def _scan_source(tmp_path, source: str, name: str = "test_target.py") -> List[Dict[str, Any]]:
    """把一段源码写到临时文件里, 用门禁扫它, 返回命中列表。"""
    path = tmp_path / name
    path.write_text(source, encoding="utf-8")
    return gate.scan_file(str(path), name)


# ====================================================================
# 1. 有牙齿: 两条规则都要能抓到
# ====================================================================

def test_detects_multi_status_in(tmp_path):
    """R1: assert x in (200, 503) —— 同时接受两个状态码。"""
    hits = _scan_source(
        tmp_path,
        "def test_x():\n"
        "    resp = client.get('/x')\n"
        "    assert resp.status_code in (200, 503)\n",
    )
    assert len(hits) == 1
    assert hits[0]["rule"] == "MULTI_STATUS_IN"
    assert hits[0]["function"] == "test_x"
    assert set(hits[0]["values"]) == {"200", "503"}


def test_detects_multi_status_or(tmp_path):
    """R2: assert x == 200 or x == 503 —— 同一表达式的或链。"""
    hits = _scan_source(
        tmp_path,
        "def test_x():\n"
        "    resp = client.get('/x')\n"
        "    assert resp.status_code == 200 or resp.status_code == 503\n",
    )
    assert len(hits) == 1
    assert hits[0]["rule"] == "MULTI_STATUS_OR"


def test_detects_three_way_loose_assert(tmp_path):
    """最恶劣的形态: 同时接受 200 / 400 / 500。"""
    hits = _scan_source(
        tmp_path,
        "def test_x():\n"
        "    resp = client.get('/x')\n"
        "    assert resp.status_code in (200, 400, 500)\n",
    )
    assert len(hits) == 1
    assert len(hits[0]["values"]) == 3


def test_detects_inside_method(tmp_path):
    """方法里也要能抓到, 且限定名带类名。"""
    hits = _scan_source(
        tmp_path,
        "class TestFoo:\n"
        "    def test_bar(self):\n"
        "        resp = client.get('/x')\n"
        "        assert resp.status_code in (200, 503)\n",
    )
    assert len(hits) == 1
    assert "TestFoo" in hits[0]["function"]


def test_detects_conditional_assert(tmp_path):
    """R3: 断言写在 ``if resp.status_code == 200:`` 里 → 可能根本不执行。"""
    hits = _scan_source(
        tmp_path,
        "def test_x():\n"
        "    resp = client.get('/x')\n"
        "    if resp.status_code == 200:\n"
        "        assert 'a' in resp.json()\n",
    )
    assert len(hits) == 1
    assert hits[0]["rule"] == "CONDITIONAL_ASSERT"
    assert "status_code" in hits[0]["expr"]


def test_detects_conditional_assert_in_loop(tmp_path):
    """R3: 守卫里套循环 → 循环里的断言同样可能不执行。"""
    hits = _scan_source(
        tmp_path,
        "def test_x():\n"
        "    resp = client.get('/x')\n"
        "    if resp.status_code == 200:\n"
        "        for line in resp.text.split('\\n'):\n"
        "            assert line\n",
    )
    assert len(hits) == 1
    assert hits[0]["rule"] == "CONDITIONAL_ASSERT"


def test_no_false_positive_for_guard_without_asserts(tmp_path):
    """只是取数据、没有断言的守卫 → 不算违规(噪音会让人无视门禁)。"""
    hits = _scan_source(
        tmp_path,
        "def test_x():\n"
        "    resp = client.get('/x')\n"
        "    assert resp.status_code == 200\n"
        "    if resp.status_code == 200:\n"
        "        data = resp.json()\n"
        "    assert data is not None\n",
    )
    assert hits == [], f"误报: {hits}"


def test_no_false_positive_for_unrelated_guard(tmp_path):
    """和 status_code 无关的守卫(比如按数据内容分支)→ 不算违规。"""
    hits = _scan_source(
        tmp_path,
        "def test_x():\n"
        "    rows = load()\n"
        "    if len(rows) > 0:\n"
        "        assert rows[0]['id'] == 1\n",
    )
    assert hits == [], f"误报: {hits}"


# ====================================================================
# 2. 不误伤: 精确断言 / 区间断言 / 值域断言都放行
# ====================================================================

@pytest.mark.parametrize(
    "source",
    [
        # 精确断言
        "def test_x():\n    assert resp.status_code == 200\n",
        # 区间断言(两种写法)
        "def test_x():\n    assert 200 <= resp.status_code < 300\n",
        "def test_x():\n    assert resp.status_code in range(200, 300)\n",
        # 值域断言(不是「多个可接受结果」)
        "def test_x():\n    assert role in ('admin', 'analyst')\n",
        # 单元素容器 = 精确
        "def test_x():\n    assert resp.status_code in (200,)\n",
        # 非状态码数字的多值断言(不在 100..599)
        "def test_x():\n    assert x in (1, 2, 3)\n",
        # 不同表达式的 or 链(不是「同一个表达式的多个结果」)
        "def test_x():\n    assert a == 200 or b == 503\n",
    ],
)
def test_no_false_positives(tmp_path, source):
    """这些都不该被当成松断言 —— 门禁必须精准, 否则会被无视。"""
    hits = _scan_source(tmp_path, source)
    assert hits == [], f"误报: {hits}"


# ====================================================================
# 3. fail-closed: 登记册 / 空扫描
# ====================================================================

def test_unregistered_violation_is_a_violation(tmp_path):
    """没登记的松断言 = 违规。"""
    scan = {
        "findings": [{
            "rule": "MULTI_STATUS_IN", "path": "tests/a.py", "line": 3,
            "function": "test_x", "source": "assert resp.status_code in (200, 503)",
            "expr": "resp.status_code", "values": ["200", "503"],
            "key": "tests/a.py::test_x::assert resp.status_code in (200, 503)",
        }],
        "files_scanned": 1,
        "errors": [],
    }
    result = gate.evaluate(scan, {"version": 1, "exemptions": []})
    assert len(result["violations"]) == 1
    assert result["errors"] == []


def test_registered_violation_becomes_exempted(tmp_path):
    """登记过的 → 变成豁免, 不再是违规。"""
    entry = {
        "path": "tests/a.py",
        "function": "test_x",
        "source": "assert resp.status_code in (200, 503)",
        "reason": "FLAKY_ENV_DEPENDENT",
        "note": "外部 OSM 抖动",
    }
    scan = {
        "findings": [{
            "rule": "MULTI_STATUS_IN", "path": "tests/a.py", "line": 3,
            "function": "test_x", "source": "assert resp.status_code in (200, 503)",
            "expr": "resp.status_code", "values": ["200", "503"],
            "key": gate.registry_key(entry),
        }],
        "files_scanned": 1,
        "errors": [],
    }
    result = gate.evaluate(scan, {"version": 1, "exemptions": [entry]})
    assert result["violations"] == []
    assert len(result["exempted"]) == 1


def test_stale_exemption_is_red(tmp_path):
    """登记册里的豁免对不上任何现存松断言 → 红(死豁免会误导人)。"""
    entry = {
        "path": "tests/gone.py",
        "function": "test_x",
        "source": "assert resp.status_code in (200, 503)",
        "reason": "PENDING_REWRITE",
        "note": "已经改好了, 忘了删登记",
    }
    scan = {"findings": [], "files_scanned": 1, "errors": []}
    result = gate.evaluate(scan, {"version": 1, "exemptions": [entry]})
    assert result["stale_exemptions"] == [gate.registry_key(entry)]
    assert any("陈旧" in e for e in result["errors"])


def test_reason_outside_closed_enum_is_red():
    """理由必须是封闭枚举里的一个 —— 不能自由发挥。"""
    errors = gate.validate_registry({
        "version": 1,
        "exemptions": [{
            "path": "tests/a.py", "function": "test_x",
            "source": "assert x in (1, 2)", "reason": "I_FEEL_LIKE_IT",
            "note": "n/a",
        }],
    })
    assert any("封闭枚举" in e for e in errors)


def test_missing_fields_are_red():
    """必填字段缺失 → 红。"""
    errors = gate.validate_registry({
        "version": 1,
        "exemptions": [{"path": "tests/a.py", "function": "test_x",
                        "source": "assert x in (1, 2)"}],
    })
    assert any("reason" in e for e in errors)


def test_duplicate_exemptions_are_red():
    """重复登记 → 红。"""
    entry = {
        "path": "tests/a.py", "function": "test_x",
        "source": "assert x in (1, 2)", "reason": "PENDING_REWRITE", "note": "n/a",
    }
    errors = gate.validate_registry({"version": 1, "exemptions": [entry, dict(entry)]})
    assert any("重复" in e for e in errors)


def test_zero_files_scanned_is_red(tmp_path):
    """一个文件都没扫到 → 门禁自己瞎了 → 红。"""
    empty = tmp_path / "empty_dir"
    empty.mkdir()
    scan = gate.scan_paths([])
    result = gate.evaluate({"findings": [], "files_scanned": 0, "errors": []},
                            {"version": 1, "exemptions": []})
    assert any("fail-closed" in e for e in result["errors"])


def test_syntax_error_is_reported_not_swallowed(tmp_path):
    """语法错误的文件不能被静默跳过 —— 报成 PARSE_ERROR。"""
    path = tmp_path / "test_broken.py"
    path.write_text("def test_x(:\n    pass\n", encoding="utf-8")
    hits = gate.scan_file(str(path), "test_broken.py")
    assert len(hits) == 1
    assert hits[0]["rule"] == "PARSE_ERROR"


# ====================================================================
# 6. iter #87 (ADR-0019): 第二种方言 —— unittest
# ====================================================================
#
# ADR-0018 的门禁只认 ``assert`` **语句**。本仓库 4957 条断言里, 1384 条
# (28%) 是 unittest 的 ``self.assertX(...)`` —— 门禁对它们**完全失明**。
#
# 后果不是理论上的: 78 条 ``self.assertIn(resp.status_code, (200, 503))``
# 在门禁眼皮底下活了一个星期。
#
# 下面 7 个用例锁住「门禁现在读得懂 unittest」+「门禁会说自己看不懂」。


def test_detects_unittest_multi_status_in(tmp_path):
    """unittest 方言的 R1: self.assertIn(resp.status_code, (200, 503))。"""
    hits = _scan_source(
        tmp_path,
        "class TestFoo(unittest.TestCase):\n"
        "    def test_x(self):\n"
        "        resp = client.get('/x')\n"
        "        self.assertIn(resp.status_code, (200, 503))\n",
    )
    assert len(hits) == 1, f"没抓到: {hits}"
    assert hits[0]["rule"] == "MULTI_STATUS_IN"
    assert set(hits[0]["values"]) == {"200", "503"}
    assert "TestFoo" in hits[0]["function"]


def test_detects_unittest_multi_status_or(tmp_path):
    """unittest 方言的 R2: self.assertTrue(x == 200 or x == 503)。"""
    hits = _scan_source(
        tmp_path,
        "class TestFoo(unittest.TestCase):\n"
        "    def test_x(self):\n"
        "        resp = client.get('/x')\n"
        "        self.assertTrue(resp.status_code == 200 or resp.status_code == 503)\n",
    )
    assert len(hits) == 1, f"没抓到: {hits}"
    assert hits[0]["rule"] == "MULTI_STATUS_OR"


def test_detects_unittest_conditional_assert(tmp_path):
    """R3 也要看得见 unittest 断言: 守卫里那句 assertEqual 可能根本不跑。"""
    hits = _scan_source(
        tmp_path,
        "class TestFoo(unittest.TestCase):\n"
        "    def test_x(self):\n"
        "        resp = client.get('/x')\n"
        "        if resp.status_code == 200:\n"
        "            self.assertEqual(resp.json()['n'], 1)\n",
    )
    rules = {h["rule"] for h in hits}
    assert "CONDITIONAL_ASSERT" in rules, f"没抓到: {hits}"


def test_detects_multi_status_guard(tmp_path):
    """R4 (新增): 守卫本身在蒙 —— if resp.status_code in (200, 503):"""
    hits = _scan_source(
        tmp_path,
        "def test_x():\n"
        "    resp = client.get('/x')\n"
        "    if resp.status_code in (200, 503):\n"
        "        data = resp.json()\n"
        "    assert data is not None\n",
    )
    assert len(hits) == 1, f"没抓到: {hits}"
    assert hits[0]["rule"] == "MULTI_STATUS_GUARD"


def test_no_false_positive_for_unittest_precise(tmp_path):
    """精确的 unittest 断言不该被当成松断言。"""
    hits = _scan_source(
        tmp_path,
        "class TestFoo(unittest.TestCase):\n"
        "    def test_x(self):\n"
        "        resp = client.get('/x')\n"
        "        self.assertEqual(resp.status_code, 200)\n"
        "        self.assertIn('cycle_id', resp.json())\n"
        "        self.assertIn(role, ('admin', 'analyst'))\n",
    )
    assert hits == [], f"误报: {hits}"


def test_unknown_dialect_is_reported(tmp_path):
    """门禁看不懂的方言必须**说出来** —— 视野自证(fail-closed 第四代)。"""
    path = tmp_path / "test_unknown.py"
    path.write_text(
        "class TestFoo(unittest.TestCase):\n"
        "    def test_x(self):\n"
        "        self.assertThat(resp.status_code, matches(200))\n",
        encoding="utf-8",
    )
    census = gate.census_dialects(str(path))
    assert census["counts"].get("UNKNOWN") == 1, census
    assert census["unknown"][0]["name"] == "assertThat"


def test_repo_tests_are_clean():
    """端到端: 改完之后, tests/ 目录里一条松断言都没有。

    这条用例是 ADR-0019 的**验收线** —— 以后谁再写一条 ``assertIn(status, (200, 503))``,
    CI 会红。
    """
    scan = gate.scan_paths(["tests"])
    result = gate.evaluate(scan, gate.load_registry())
    assert result["violations"] == [], \
        f"还有 {len(result['violations'])} 条松断言: {result['violations'][:3]}"
    assert result["errors"] == [], f"fail-closed 校验没过: {result['errors']}"
    # 视野必须 100%
    assert scan["assertions_total"] > 4000, scan["assertions_total"]
    assert scan["assertions_modeled"] == scan["assertions_total"], (
        f"门禁视野 {scan['assertions_modeled']}/{scan['assertions_total']} < 100%"
    )
