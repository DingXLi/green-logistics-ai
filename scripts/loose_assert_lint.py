#!/usr/bin/env python3
"""
loose_assert_lint.py — **松断言门禁** (iter #86 ADR-0018 / iter #87 ADR-0019)

这个门禁要证明的命题
--------------------
    「一个断言同时接受多个结果, 就等于没有断言。」

iter #85 (ADR-0017) 的测试顺序无关性门禁上线当天抓到 1 个真 bug,
根因之一是:

    断言写成了 ``assert resp.status_code in (200, 503)`` —— 同时接受「成功」
    和「服务不可用」两种结果。**这等于没有断言**: 端点整个挂掉, 测试照样绿。

当时的修法是「把断言改精确」, 但那只修了**一处**。
本门禁要修的是**整类**: 让「断言松到能蒙」这件事在 CI 里**不可能悄悄发生**。

iter #87 (ADR-0019): 这个门禁**自己**漏了 28%
--------------------------------------------------
ADR-0018 上线那天, 门禁只认 ``assert`` **语句**。而本仓库 4957 条断言里:

    assert 语句      3573 条  (72%)  ← 门禁看得见
    unittest 方法    1384 条  (28%)  ← **门禁完全看不见**

后果是实打实的: **78 条** ``self.assertIn(resp.status_code, (200, 503))``
在门禁眼皮底下活了一个星期 —— 端点整个 503, 测试照样绿。
ADR-0018 宣布「把整类消灭了」, 实际只消灭了**自己会读的那一半**。

所以 iter #87 给门禁加了两样东西:

1. **会读两种方言** —— ``assert`` 语句 + unittest 的 ``self.assertX(...)``。
2. **视野自证 (fail-closed 第四代)** —— 门禁普查仓库里出现的**每一种断言方言**,
   只要出现一种**它不认识的**方言 → **红**。

   以前是「一个文件都没扫到 → 红」(ADR-0017)。现在是:
   「有一类断言我没读懂 → 红」。因为**门禁的覆盖面 = 门禁的价值上限**:
   一道只看得见 72% 断言的门禁, 它的绿灯只值 72%。

什么叫「松断言」
----------------
四条规则, 都是可机器判定的:

**R1 ``MULTI_STATUS_IN``** —— 一个断言同时接受多个 HTTP 状态码:
    ``assert resp.status_code in (200, 503)``
    ``self.assertIn(resp.status_code, (200, 503))``
    判定: 断言是 ``<expr> in <container>``, container 是 tuple/list/set,
    元素里**至少有一个是 100..599 的整数**, 且**元素总数 >= 2**。

**R2 ``MULTI_STATUS_OR``** —— 同一个表达式的「等于 A 或等于 B」:
    ``assert resp.status_code == 200 or resp.status_code == 503``
    ``self.assertTrue(resp.status_code == 200 or resp.status_code == 503)``
    判定: 断言是 ``<expr> == <v> or <expr> == <v> ...`` 的 or 链,
    且出现的值里至少有一个是 100..599 的整数, 且**取值个数 >= 2**。

**R3 ``CONDITIONAL_ASSERT``** —— 断言写在「结果守卫」里, 可能根本不执行:
    ``if resp.status_code == 200:``
    ``    assert "cycle_id" in data``   ← 端点返回 500 时, 这条断言**不会跑**
    判定: 断言语句位于某个 ``if`` 的 body 内, 且该 ``if`` 的条件里出现
    ``status_code``。也就是说: **被断言的东西, 同时决定了断言跑不跑。**

**R4 ``MULTI_STATUS_GUARD``** —— 守卫本身在蒙 (iter #87 新增):
    ``if resp.status_code in (200, 503):``
    判定: ``if`` 的条件是 ``<expr> in <container>``, 容器里含多个状态码。
    为什么单列一条: R3 只管「守卫里有没有断言」, 管不了「守卫自己在选哪条路」。
    一个多值守卫 = **测试有两条通向绿的路**, 端点坏掉时走哪条都行。

**刻意不拦的情况**(避免门禁变成噪音机器):
- ``assert resp.status_code == 200`` —— 精确, 拦什么拦。
- ``assert 200 <= resp.status_code < 300`` —— 区间断言, 语义明确。
- ``assert resp.status_code in range(200, 300)`` —— 区间断言的另一种写法。
- ``assert role in ("admin", "analyst")`` —— 值域检查, 不是「多个可接受结果」。
- ``assert x in (100, 200)`` 且 x 与 status_code 无关 —— 本门禁只看
  **看起来像状态码的数字**(100..599), 判定精度够用, 且误报面极小。

为什么是 fail-closed
-------------------
和 ADR-0010 ~ ADR-0018 一脉相承的四条:

- **不漏**: 任何一条松断言, 除非在登记册里显式登记, 否则一律红。
- **不腐**: 登记册里的豁免如果已经对不上任何现存松断言(改好了 / 删了),
  → **红**。死豁免会让人误以为「那里还有问题被管着」。
- **不空**: 豁免理由必须来自封闭枚举, 且必须写清「客户少了哪层保护, 换来了什么」。
- **不静默**: 一个文件都没扫到 → **红**; **有一种断言方言不认识 → 红**(ADR-0019)。

豁免登记册
----------
``config/loose_assert_exemptions.json``:

    {
      "version": 1,
      "exemptions": [
        {
          "path": "tests/xxx.py",
          "function": "test_yyy",
          "source": "assert resp.status_code in (200, 503)",
          "reason": "FLAKY_ENV_DEPENDENT",
          "note": "人话: 这条断言在 CI 上真的会因为外部环境抖动而有两种结果。"
        }
      ]
    }

key = ``path::function::规范化后的断言源码``。
用**限定名 + 源码**而不是行号 —— 行号会漂, 按行号冻结就等于每改一次代码就得刷一次登记册
(ADR-0016 的同一条教训)。

跑法
----
    python scripts/loose_assert_lint.py              # 门禁(CI 跑的就是这条)
    python scripts/loose_assert_lint.py --json       # 机器可读
    python scripts/loose_assert_lint.py --path tests/selftest_dir  # 指定目录

默认扫 ``tests/`` + ``selftest/`` —— **门禁连自己的自测文件一起管**
(自测文件里如果出现松断言, 那就说明门禁的靶子写错了)。
"""

from __future__ import annotations

import argparse
import ast
import json
import os
from typing import Any, Dict, List, Optional, Set, Tuple

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DEFAULT_SCAN_PATHS = ("tests", "selftest")
REGISTRY_PATH = os.path.join(_REPO_ROOT, "config", "loose_assert_exemptions.json")

# HTTP 状态码区间 —— 「看起来像状态码」的判定范围
_STATUS_MIN = 100
_STATUS_MAX = 599


# ====================================================================
# 豁免理由: 封闭枚举
# ====================================================================
EXEMPTION_REASONS: Dict[str, str] = {
    "FLAKY_ENV_DEPENDENT": (
        "结果真的取决于外部环境(网络 / 真实 OSM / 真实 LLM), 同一次提交在不同环境"
        "会得到不同状态码。**这恰恰是最该被消灭的** —— 请把它缩到只剩「非确定性」"
        "这一小块, 而不是整个端点。"
    ),
    "PENDING_REWRITE": (
        "这个测试正在被重写, 现状暂时保留。请在 note 里写清重写之后会变成什么。"
    ),
    "NON_STATUS_SEMANTICS": (
        "tuple 里的数字只是**值域**, 不是「多个可接受的结果」(例如金额 / 分桶 / "
        "档位)。这条豁免保护的是语义, 不是「蒙混过关」。"
    ),
}


# ====================================================================
# 0. 方言模型 (iter #87 新增)
# ====================================================================
#
# 门禁必须**读得懂**仓库里出现的每一种断言写法 —— 读不懂的, 要**说出来**。
#
# 这里列的是「门禁认识哪些断言方法」。只要仓库里出现一个不在表里的
# ``xxx.assertYyy(...)`` / ``assertYyy(...)``, 门禁就判红:
# 「有一类断言我没读懂」→ 它的绿灯对那一类**没有约束力**。

# unittest.TestCase 的断言 API + 项目里用到的 mock 断言
KNOWN_ASSERT_METHODS: Set[str] = {
    # unittest.TestCase
    "assertEqual", "assertNotEqual", "assertIn", "assertNotIn", "assertTrue",
    "assertFalse", "assertIs", "assertIsNot", "assertIsNone", "assertIsNotNone",
    "assertAlmostEqual", "assertNotAlmostEqual", "assertRaises",
    "assertRaisesRegex", "assertGreater", "assertGreaterEqual", "assertLess",
    "assertLessEqual", "assertIsInstance", "assertNotIsInstance",
    "assertCountEqual", "assertDictEqual", "assertListEqual", "assertTupleEqual",
    "assertSetEqual", "assertMultiLineEqual", "assertSequenceEqual",
    "assertRegex", "assertNotRegex",
    # unittest.mock
    "assert_called", "assert_called_once", "assert_not_called", "assert_any_call",
    "assert_has_calls",
}

# 非 unittest 的断言入口(点名才算, 不靠前缀猜)
KNOWN_ASSERT_CALLS: Set[str] = {"pytest.raises"}


# ====================================================================
# 1. 扫描: 找出所有松断言
# ====================================================================

def _is_status_like_value(node: ast.AST) -> bool:
    """这个节点是不是一个「像 HTTP 状态码的整数」。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, int) \
            and not isinstance(node.value, bool):
        return _STATUS_MIN <= node.value <= _STATUS_MAX
    return False


def _dumps(node: ast.AST) -> str:
    """把 AST 节点还原成稳定的源码文本(用于 key 和报告)。"""
    try:
        return ast.unparse(node)
    except Exception:  # pragma: no cover - ast.unparse 在 3.8 上不存在
        return "<unparseable>"


def _normalize_source(source: str) -> str:
    """规范化断言源码: 折叠空白, 让 key 对格式变化不敏感。"""
    return " ".join(source.split())


def _container_elements(node: ast.AST) -> Optional[List[ast.AST]]:
    """tuple / list / set → 元素列表; 其他 → None。"""
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return list(node.elts)
    return None


def _call_name(node: ast.Call) -> str:
    """给一个 Call 起个稳定的名字(用于方言判定)。"""
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _is_known_assert_call(node: ast.Call) -> bool:
    """这个 Call 是不是「门禁认识的断言」。"""
    name = _call_name(node)
    if name in KNOWN_ASSERT_METHODS:
        return True
    if name == "raises":
        # pytest.raises(...) —— 只认 pytest.raises, 不认别的 .raises
        func = node.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            return func.value.id == "pytest"
    return False


def _is_assert_like_call(node: ast.Call) -> bool:
    """这个 Call 长得像断言吗(名字以 assert 开头)? —— 不认识的那种要报出来。"""
    name = _call_name(node)
    return name.startswith("assert") and name not in KNOWN_ASSERT_METHODS


def _find_loose_in_container(expr: ast.AST, container: ast.AST) -> Optional[List[str]]:
    """``<expr> in <container>`` 且容器里有多个状态码 → 返回取值列表。"""
    elements = _container_elements(container)
    if elements is None or len(elements) < 2:
        return None
    if not any(_is_status_like_value(e) for e in elements):
        return None
    return [_dumps(e) for e in elements]


def _find_loose_or_chain(test: ast.AST) -> Optional[Tuple[str, List[str]]]:
    """``<expr> == a or <expr> == b ...`` → (表达式, 取值列表)。"""
    if not isinstance(test, ast.BoolOp) or not isinstance(test.op, ast.Or):
        return None

    lefts: Set[str] = set()
    values: List[str] = []
    for value in test.values:
        if not isinstance(value, ast.Compare):
            return None
        if len(value.ops) != 1 or not isinstance(value.ops[0], ast.Eq):
            return None
        if len(value.comparators) != 1:
            return None
        lefts.add(_dumps(value.left))
        values.append(_dumps(value.comparators[0]))

    if len(lefts) != 1:
        # 不是「同一个表达式的或」→ 不属于这一类
        return None
    if not any(_is_status_like_value(v) for v in value.comparators):
        return None
    if len(set(values)) < 2:
        return None
    return next(iter(lefts)), values


def _find_loose_in_assert(node: ast.Assert) -> Optional[Dict[str, Any]]:
    """R1: ``assert <expr> in (a, b, ...)``"""
    test = node.test
    if not isinstance(test, ast.Compare):
        return None
    if len(test.ops) != 1 or not isinstance(test.ops[0], ast.In):
        return None
    values = _find_loose_in_container(test.left, test.comparators[0])
    if values is None:
        return None
    return {
        "rule": "MULTI_STATUS_IN",
        "expr": _dumps(test.left),
        "values": values,
        "source": _normalize_source(_dumps(node)),
    }


def _find_loose_or_assert(node: ast.Assert) -> Optional[Dict[str, Any]]:
    """R2: ``assert <expr> == a or <expr> == b``"""
    found = _find_loose_or_chain(node.test)
    if found is None:
        return None
    expr, values = found
    return {
        "rule": "MULTI_STATUS_OR",
        "expr": expr,
        "values": values,
        "source": _normalize_source(_dumps(node)),
    }


def _find_loose_in_unittest(node: ast.Call) -> Optional[Dict[str, Any]]:
    """R1 (unittest 方言): ``self.assertIn(resp.status_code, (200, 503))``"""
    if _call_name(node) != "assertIn" or len(node.args) < 2:
        return None
    values = _find_loose_in_container(node.args[0], node.args[1])
    if values is None:
        return None
    return {
        "rule": "MULTI_STATUS_IN",
        "expr": _dumps(node.args[0]),
        "values": values,
        "source": _normalize_source(_dumps(node)),
    }


def _find_loose_or_unittest(node: ast.Call) -> Optional[Dict[str, Any]]:
    """R2 (unittest 方言): ``self.assertTrue(x == 200 or x == 503)``"""
    if _call_name(node) != "assertTrue" or not node.args:
        return None
    found = _find_loose_or_chain(node.args[0])
    if found is None:
        return None
    expr, values = found
    return {
        "rule": "MULTI_STATUS_OR",
        "expr": expr,
        "values": values,
        "source": _normalize_source(_dumps(node)),
    }


def _build_function_index(tree: ast.AST) -> List[Tuple[ast.AST, str, int]]:
    """返回 [(函数节点, 限定名, 嵌套深度), ...]。

    限定名带上类名(``TestFoo.test_bar``) —— key 要稳定且可读,
    嵌套深度用来在多个候选函数里挑**最内层**那个。
    """
    result: List[Tuple[ast.AST, str, int]] = []

    def walk(node: ast.AST, prefix: str, depth: int) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qual = f"{prefix}.{child.name}" if prefix else child.name
                result.append((child, qual, depth))
                walk(child, qual, depth + 1)
            elif isinstance(child, ast.ClassDef):
                qual = f"{prefix}.{child.name}" if prefix else child.name
                walk(child, qual, depth)

    walk(tree, "", 0)
    return result


def _iter_asserts_without_nested_functions(body: List[ast.stmt]):
    """遍历一段语句里的所有断言, 但**不进入**嵌套的函数定义。"""
    stack: List[ast.AST] = list(body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        if isinstance(node, ast.Assert):
            yield node
        if isinstance(node, ast.Call) and _is_known_assert_call(node):
            yield node
        stack.extend(ast.iter_child_nodes(node))


def _find_conditional_asserts(tree: ast.AST) -> List[Dict[str, Any]]:
    """R3: 断言写在「看结果再决定要不要断言」的守卫里。"""
    hits: List[Dict[str, Any]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        guard = _dumps(node.test)
        if "status_code" not in guard:
            continue
        for stmt in node.body:
            for assert_node in _iter_asserts_without_nested_functions([stmt]):
                hits.append({
                    "rule": "CONDITIONAL_ASSERT",
                    "expr": guard,
                    "values": [],
                    "source": _normalize_source(_dumps(assert_node)),
                    "_assert_node": assert_node,
                    "_guard": guard,
                })
    return hits


def _find_multi_status_guards(tree: ast.AST) -> List[Dict[str, Any]]:
    """R4 (iter #87): 守卫本身在蒙 —— ``if x in (200, 503):``"""
    hits: List[Dict[str, Any]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if not isinstance(test, ast.Compare):
            continue
        if len(test.ops) != 1 or not isinstance(test.ops[0], ast.In):
            continue
        values = _find_loose_in_container(test.left, test.comparators[0])
        if values is None:
            continue
        hits.append({
            "rule": "MULTI_STATUS_GUARD",
            "expr": _dumps(test.left),
            "values": values,
            "source": _normalize_source(f"if {guard_source(test)}:"),
            "_guard_node": node,
            "_guard": _dumps(test),
        })
    return hits


def guard_source(test: ast.AST) -> str:
    return _dumps(test)


def _qualify(node: ast.AST, functions: List[Tuple[ast.AST, str, int]]) -> str:
    """找到这个节点所属的**最内层**函数限定名。"""
    qual = "<module>"
    best_depth = -1
    for fn_node, fn_qual, fn_depth in functions:
        for child in ast.walk(fn_node):
            if child is node:
                if fn_depth > best_depth:
                    best_depth = fn_depth
                    qual = fn_qual
                break
    return qual


def scan_file(path: str, rel_path: str) -> List[Dict[str, Any]]:
    """扫描单个文件, 返回松断言列表。"""
    with open(path, "r", encoding="utf-8") as fh:
        source = fh.read()
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as exc:
        return [{
            "rule": "PARSE_ERROR",
            "path": rel_path,
            "line": exc.lineno or 0,
            "source": f"<syntax error: {exc.msg}>",
            "expr": "",
            "values": [],
            "function": "<module>",
            "key": f"{rel_path}::<module>::<parse error>",
        }]

    functions = _build_function_index(tree)
    findings: List[Dict[str, Any]] = []

    for node in ast.walk(tree):
        hit = None
        if isinstance(node, ast.Assert):
            hit = _find_loose_in_assert(node) or _find_loose_or_assert(node)
        elif isinstance(node, ast.Call) and _is_known_assert_call(node):
            hit = _find_loose_in_unittest(node) or _find_loose_or_unittest(node)
        if hit is None:
            continue

        qual = _qualify(node, functions)
        entry = {
            "rule": hit["rule"],
            "path": rel_path,
            "line": node.lineno,
            "function": qual,
            "source": hit["source"],
            "expr": hit["expr"],
            "values": hit["values"],
        }
        entry["key"] = f"{rel_path}::{qual}::{hit['source']}"
        findings.append(entry)

    # R3: 条件断言(断言写在看结果的守卫里, 可能根本不执行)
    for cond in _find_conditional_asserts(tree):
        assert_node = cond.pop("_assert_node")
        cond.pop("_guard")
        qual = _qualify(assert_node, functions)
        entry = {
            "rule": cond["rule"],
            "path": rel_path,
            "line": assert_node.lineno,
            "function": qual,
            "source": cond["source"],
            "expr": cond["expr"],
            "values": [],
        }
        entry["key"] = f"{rel_path}::{qual}::{cond['source']}"
        findings.append(entry)

    # R4: 多值守卫
    for guard_hit in _find_multi_status_guards(tree):
        guard_node = guard_hit.pop("_guard_node")
        guard_hit.pop("_guard")
        qual = _qualify(guard_node, functions)
        entry = {
            "rule": guard_hit["rule"],
            "path": rel_path,
            "line": guard_node.lineno,
            "function": qual,
            "source": guard_hit["source"],
            "expr": guard_hit["expr"],
            "values": guard_hit["values"],
        }
        entry["key"] = f"{rel_path}::{qual}::{guard_hit['source']}"
        findings.append(entry)

    return _dedupe_keys(findings)


def _dedupe_keys(findings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """同一个函数里可能有多条一模一样的断言 → key 加序号, 避免互相覆盖。"""
    seen: Dict[str, int] = {}
    for f in findings:
        key = f["key"]
        seen[key] = seen.get(key, 0) + 1
        if seen[key] > 1:
            f["key"] = f"{key}#{seen[key]}"
    return findings


# ====================================================================
# 1b. 方言普查 (iter #87 新增 —— 门禁的视野自证)
# ====================================================================

def census_dialects(path: str) -> Dict[str, Any]:
    """普查一个文件里的断言方言。

    返回 ``{"counts": {方言: 条数}, "unknown": [{...}], "total": n}``

    **unknown 非空 = 门禁看不懂这一类断言** → 它的绿灯对这一类没有约束力
    → 必须判红(见 ``evaluate``)。
    """
    counts: Dict[str, int] = {}
    unknown: List[Dict[str, Any]] = []

    with open(path, "r", encoding="utf-8") as fh:
        source = fh.read()
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError:
        return {"counts": {}, "unknown": [], "total": 0}

    for node in ast.walk(tree):
        if isinstance(node, ast.Assert):
            counts["ASSERT_STMT"] = counts.get("ASSERT_STMT", 0) + 1
        elif isinstance(node, ast.Call):
            name = _call_name(node)
            if _is_known_assert_call(node):
                dialect = "UNITTEST_METHOD" if name in KNOWN_ASSERT_METHODS \
                    else "PYTEST_RAISES"
                counts[dialect] = counts.get(dialect, 0) + 1
            elif _is_assert_like_call(node):
                counts["UNKNOWN"] = counts.get("UNKNOWN", 0) + 1
                unknown.append({
                    "name": name,
                    "line": node.lineno,
                    "source": _normalize_source(_dumps(node)),
                })

    total = sum(counts.values())
    return {"counts": counts, "unknown": unknown, "total": total}


def scan_paths(paths: List[str]) -> Dict[str, Any]:
    """扫描若干目录, 返回 {findings, files_scanned, errors, dialects}。"""
    findings: List[Dict[str, Any]] = []
    files_scanned = 0
    errors: List[str] = []
    dialect_counts: Dict[str, int] = {}
    unknown_dialects: List[Dict[str, Any]] = []

    for rel_dir in paths:
        abs_dir = os.path.join(_REPO_ROOT, rel_dir)
        if not os.path.isdir(abs_dir):
            errors.append(f"扫描目录不存在: {rel_dir}")
            continue
        for root, _dirs, files in os.walk(abs_dir):
            for fname in sorted(files):
                if not fname.endswith(".py"):
                    continue
                if fname.startswith("conftest"):
                    continue
                abs_path = os.path.join(root, fname)
                rel_path = os.path.relpath(abs_path, _REPO_ROOT)
                files_scanned += 1
                findings.extend(scan_file(abs_path, rel_path))

                census = census_dialects(abs_path)
                for dialect, count in census["counts"].items():
                    dialect_counts[dialect] = dialect_counts.get(dialect, 0) + count
                for item in census["unknown"]:
                    item = dict(item)
                    item["path"] = rel_path
                    unknown_dialects.append(item)

    total_assertions = sum(dialect_counts.values())
    modeled = sum(c for d, c in dialect_counts.items() if d != "UNKNOWN")

    return {
        "findings": findings,
        "files_scanned": files_scanned,
        "errors": errors,
        "dialects": dialect_counts,
        "unknown_dialects": unknown_dialects,
        "assertions_total": total_assertions,
        "assertions_modeled": modeled,
    }


# ====================================================================
# 2. 登记册
# ====================================================================

def load_registry(path: str = REGISTRY_PATH) -> Dict[str, Any]:
    if not os.path.exists(path):
        return {"version": 1, "exemptions": []}
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    data.setdefault("version", 1)
    data.setdefault("exemptions", [])
    return data


def registry_key(entry: Dict[str, Any]) -> str:
    source = _normalize_source(entry.get("source", ""))
    return f"{entry.get('path', '')}::{entry.get('function', '')}::{source}"


def validate_registry(registry: Dict[str, Any]) -> List[str]:
    """校验登记册本身的合法性。"""
    errors: List[str] = []
    seen: Set[str] = set()

    for i, entry in enumerate(registry.get("exemptions", [])):
        for required in ("path", "function", "source", "reason", "note"):
            if required not in entry or not str(entry[required]).strip():
                errors.append(f"登记册第 {i + 1} 条缺少必填字段: {required}")
        reason = entry.get("reason")
        if reason and reason not in EXEMPTION_REASONS:
            errors.append(
                f"登记册第 {i + 1} 条的 reason '{reason}' 不在封闭枚举里 "
                f"(允许: {', '.join(sorted(EXEMPTION_REASONS))})"
            )
        key = registry_key(entry)
        if key in seen:
            errors.append(f"登记册里有重复条目: {key}")
        seen.add(key)

    return errors


# ====================================================================
# 3. 判定
# ====================================================================

def evaluate(scan: Dict[str, Any], registry: Dict[str, Any]) -> Dict[str, Any]:
    """把扫描结果和登记册对上, 得出最终的通过/失败。"""
    errors: List[str] = list(scan.get("errors", []))

    if scan["files_scanned"] == 0:
        errors.append(
            "一个文件都没扫到 → 门禁自己瞎了(fail-closed)。"
            "检查扫描目录是否写错 / 是否被 .gitignore 掉了。"
        )

    # ADR-0019: 视野自证 —— 有一种断言方言不认识 → 门禁的绿灯没有约束力
    unknown = scan.get("unknown_dialects", [])
    if unknown:
        names = sorted({u["name"] for u in unknown})
        errors.append(
            f"有 {len(unknown)} 处断言用了门禁**不认识**的写法 "
            f"({', '.join(names)}) → 门禁的绿灯对这一类断言没有约束力。\n"
            f"    → 要么在 loose_assert_lint.KNOWN_ASSERT_METHODS 里补上它,\n"
            f"    → 要么改用门禁认识的写法。\n"
            f"    位置: {', '.join(u['path'] + ':' + str(u['line']) for u in unknown[:10])}"
            + (" ..." if len(unknown) > 10 else "")
        )

    exemptions = {registry_key(e): e for e in registry.get("exemptions", [])}
    violations: List[Dict[str, Any]] = []
    exempted: List[Dict[str, Any]] = []

    for finding in scan["findings"]:
        key = finding["key"]
        if key in exemptions:
            exempted.append({**finding, "reason": exemptions[key].get("reason", "?")})
        else:
            violations.append(finding)

    # 陈旧豁免: 登记册里有, 但扫不到了 → 红(死豁免 = 误导)
    found_keys = {f["key"] for f in scan["findings"]}
    stale = [k for k in exemptions if k not in found_keys]

    for key in stale:
        errors.append(
            f"登记册里的豁免已经对不上任何现存松断言(陈旧): {key}\n"
            f"    → 如果你已经把它改精确了, 请删掉这条登记; "
            f"如果代码被删了, 也请删掉这条登记。"
        )

    return {
        "violations": violations,
        "exempted": exempted,
        "stale_exemptions": stale,
        "errors": errors,
        "files_scanned": scan["files_scanned"],
        "dialects": scan.get("dialects", {}),
        "assertions_total": scan.get("assertions_total", 0),
        "assertions_modeled": scan.get("assertions_modeled", 0),
    }


# ====================================================================
# 4. 报告
# ====================================================================

def build_report(result: Dict[str, Any]) -> Dict[str, Any]:
    by_rule: Dict[str, int] = {}
    for v in result["violations"]:
        by_rule[v["rule"]] = by_rule.get(v["rule"], 0) + 1
    total = result.get("assertions_total", 0)
    modeled = result.get("assertions_modeled", 0)
    coverage = (modeled / total * 100.0) if total else 0.0
    return {
        "files_scanned": result["files_scanned"],
        "violations": len(result["violations"]),
        "exempted": len(result["exempted"]),
        "stale_exemptions": len(result["stale_exemptions"]),
        "violations_by_rule": by_rule,
        "errors": result["errors"],
        "violation_details": result["violations"],
        "exempted_details": result["exempted"],
        "dialects": result.get("dialects", {}),
        "assertions_total": total,
        "assertions_modeled": modeled,
        "assertion_coverage_pct": round(coverage, 2),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="松断言门禁: 一个断言同时接受多个结果 = 没有断言")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--path", action="append", default=None,
                        help="扫描目录(相对仓库根), 可重复。默认: tests")
    args = parser.parse_args()

    scan = scan_paths(args.path if args.path else list(DEFAULT_SCAN_PATHS))
    registry = load_registry()
    registry_errors = validate_registry(registry)

    result = evaluate(scan, registry)
    result["errors"] = registry_errors + result["errors"]
    report = build_report(result)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1 if report["errors"] or report["violations"] else 0

    print(f"扫描文件: {report['files_scanned']}")
    print(f"松断言: {report['violations']}  {report['violations_by_rule'] or ''}")
    print(f"已登记豁免: {report['exempted']}")
    print(f"陈旧豁免: {report['stale_exemptions']}")

    dialects = report.get("dialects", {})
    if dialects:
        print("\n断言方言普查(门禁的视野):")
        for dialect, count in sorted(dialects.items(), key=lambda x: -x[1]):
            mark = "❌ 不认识" if dialect == "UNKNOWN" else "✅ 已建模"
            print(f"  {dialect:20s} {count:6d}  {mark}")
        print(f"  {'合计':20s} {report['assertions_total']:6d}  "
              f"视野覆盖 {report['assertion_coverage_pct']}%")

    if report["violations"]:
        print("\n❌ 松断言(一个断言接受多个结果 = 没断言):")
        for v in report["violation_details"]:
            print(f"  - {v['path']}:{v['line']}  [{v['rule']}]  {v['source']}")
            print(f"      接受的结果: {', '.join(v['values'])}")
            print(f"      → 改成精确断言: assert {v['expr']} == <唯一正确的值>")
            print("      → 或把测试拆成两条: 一条断成功路径, 一条断明确的失败路径")

    if report["errors"]:
        print("\n❌ 校验失败(fail-closed):")
        for e in report["errors"]:
            print(f"  - {e}")
        return 1

    if report["violations"]:
        return 1

    print("\n✅ 校验通过: 没有「同时接受多个结果」的断言, "
          "每条断言都在证明唯一一件事。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
