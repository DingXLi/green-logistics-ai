#!/usr/bin/env python3
"""
loose_assert_lint.py — **松断言门禁** (iter #86, ADR-0018)

这个门禁要证明的命题
--------------------
    「一个断言同时接受多个结果, 就等于没有断言。」

iter #85 (ADR-0017) 的测试顺序无关性门禁上线当天抓到 1 个真 bug,
根因是三件事叠加, 其中一件是:

    断言写成了 ``assert resp.status_code in (200, 503)`` —— 同时接受「成功」
    和「服务不可用」两种结果。**这等于没有断言**: 端点整个挂掉, 测试照样绿。

当时的修法是「把断言改精确」, 但那只修了**一处**。
本门禁要修的是**整类**: 让「断言松到能蒙」这件事在 CI 里**不可能悄悄发生**。

什么叫「松断言」
----------------
两条规则, 都是可机器判定的:

**R1 ``MULTI_STATUS_IN``** —— 一个断言同时接受多个 HTTP 状态码:
    ``assert resp.status_code in (200, 503)``
    ``assert r.status_code in (200, 400, 500)``
    判定: 断言是 ``<expr> in <container>``, container 是 tuple/list/set,
    元素里**至少有一个是 100..599 的整数**, 且**元素总数 >= 2**。

**R2 ``MULTI_STATUS_OR``** —— 同一个表达式的「等于 A 或等于 B」:
    ``assert resp.status_code == 200 or resp.status_code == 503``
    判定: 断言是 ``<expr> == <v> or <expr> == <v> ...`` 的 or 链,
    且出现的值里至少有一个是 100..599 的整数, 且**取值个数 >= 2**。

**R3 ``CONDITIONAL_ASSERT``** —— 断言写在「结果守卫」里, 可能根本不执行:
    ``if resp.status_code == 200:``
    ``    assert "cycle_id" in data``   ← 端点返回 500 时, 这条断言**不会跑**
    判定: 断言语句位于某个 ``if`` 的 body 内, 且该 ``if`` 的条件里出现
    ``status_code``。也就是说: **被断言的东西, 同时决定了断言跑不跑。**

**刻意不拦的情况**(避免门禁变成噪音机器):
- ``assert resp.status_code == 200`` —— 精确, 拦什么拦。
- ``assert 200 <= resp.status_code < 300`` —— 区间断言, 语义明确。
- ``assert resp.status_code in range(200, 300)`` —— 区间断言的另一种写法。
- ``assert role in ("admin", "analyst")`` —— 值域检查, 不是「多个可接受结果」。
- ``assert x in (100, 200)`` 且 x 与 status_code 无关 —— 本门禁只看
  **看起来像状态码的数字**(100..599), 判定精度够用, 且误报面极小。

为什么是 fail-closed
-------------------
和 ADR-0010 ~ ADR-0017 一脉相承的三条:

- **不漏**: 任何一条松断言, 除非在登记册里显式登记, 否则一律红。
- **不腐**: 登记册里的豁免如果已经对不上任何现存松断言(改好了 / 删了),
  → **红**。死豁免会让人误以为「那里还有问题被管着」。
- **不空**: 豁免理由必须来自封闭枚举, 且必须写清「客户少了哪层保护, 换来了什么」。

还有一条 ADR-0017 教给我们的:

- **不静默**: 一个文件都没扫到 → **红**。
  「0 个 testcase 收集到 → 判红」, 因为「门禁自己瞎了」比「门禁通过」危险得多。

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
"""

from __future__ import annotations

import argparse
import ast
import json
import os
from typing import Any, Dict, List, Optional, Set, Tuple

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DEFAULT_SCAN_PATHS = ("tests",)
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


def _find_loose_in_assert(node: ast.Assert, source: str) -> Optional[Dict[str, Any]]:
    """R1: ``assert <expr> in (a, b, ...)``"""
    test = node.test
    if not isinstance(test, ast.Compare):
        return None
    if len(test.ops) != 1 or not isinstance(test.ops[0], ast.In):
        return None
    elements = _container_elements(test.comparators[0])
    if elements is None or len(elements) < 2:
        return None
    if not any(_is_status_like_value(e) for e in elements):
        return None
    return {
        "rule": "MULTI_STATUS_IN",
        "expr": _dumps(test.left),
        "values": [_dumps(e) for e in elements],
        "source": _normalize_source(_dumps(node)),
    }


def _find_loose_or_assert(node: ast.Assert, source: str) -> Optional[Dict[str, Any]]:
    """R2: ``assert <expr> == a or <expr> == b``"""
    test = node.test
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
    return {
        "rule": "MULTI_STATUS_OR",
        "expr": next(iter(lefts)),
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
    """遍历一段语句里的所有 assert, 但**不进入**嵌套的函数定义。"""
    stack: List[ast.AST] = list(body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        if isinstance(node, ast.Assert):
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
        if not isinstance(node, ast.Assert):
            continue
        hit = _find_loose_in_assert(node, source) or _find_loose_or_assert(node, source)
        if hit is None:
            continue

        qual = "<module>"
        best_depth = -1
        for fn_node, fn_qual, fn_depth in functions:
            for child in ast.walk(fn_node):
                if child is node:
                    if fn_depth > best_depth:
                        best_depth = fn_depth
                        qual = fn_qual
                    break

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
        guard = cond.pop("_guard")
        qual = "<module>"
        best_depth = -1
        for fn_node, fn_qual, fn_depth in functions:
            for child in ast.walk(fn_node):
                if child is assert_node:
                    if fn_depth > best_depth:
                        best_depth = fn_depth
                        qual = fn_qual
                    break
        entry = {
            "rule": cond["rule"],
            "path": rel_path,
            "line": assert_node.lineno,
            "function": qual,
            "source": cond["source"],
            "expr": guard,
            "values": [],
        }
        entry["key"] = f"{rel_path}::{qual}::{cond['source']}"
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


def scan_paths(paths: List[str]) -> Dict[str, Any]:
    """扫描若干目录, 返回 {findings, files_scanned, errors}。"""
    findings: List[Dict[str, Any]] = []
    files_scanned = 0
    errors: List[str] = []

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

    return {"findings": findings, "files_scanned": files_scanned, "errors": errors}


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
    }


# ====================================================================
# 4. 报告
# ====================================================================

def build_report(result: Dict[str, Any]) -> Dict[str, Any]:
    by_rule: Dict[str, int] = {}
    for v in result["violations"]:
        by_rule[v["rule"]] = by_rule.get(v["rule"], 0) + 1
    return {
        "files_scanned": result["files_scanned"],
        "violations": len(result["violations"]),
        "exempted": len(result["exempted"]),
        "stale_exemptions": len(result["stale_exemptions"]),
        "violations_by_rule": by_rule,
        "errors": result["errors"],
        "violation_details": result["violations"],
        "exempted_details": result["exempted"],
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

    if report["violations"]:
        print("\n❌ 松断言(一个断言接受多个结果 = 没断言):")
        for v in report["violation_details"]:
            print(f"  - {v['path']}:{v['line']}  [{v['rule']}]  {v['source']}")
            print(f"      接受的结果: {', '.join(v['values'])}")
            print(f"      → 改成精确断言: assert {v['expr']} == <唯一正确的值>")
            print(f"      → 或把测试拆成两条: 一条断成功路径, 一条断明确的失败路径")

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
