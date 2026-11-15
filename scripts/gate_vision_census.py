#!/usr/bin/env python3
"""
gate_vision_census.py — **门禁视野普查** (iter #88 / ADR-0020)

这个脚本要证明的命题
--------------------
    **「门禁的覆盖面 = 门禁的价值上限。」**
    —— 一道只看得见 70% 代码的门禁, 它的绿灯只值 70%。

iter #87 (ADR-0019) 的教训
--------------------------
ADR-0019 给松断言门禁加了「视野自证」: 门禁普查仓库里出现的**每一种断言方言**,
只要出现一种它不认识的 → 红。那天抓到的教训被写进了 ADR-0019:

    「我消灭了一整类」这句话必须能用 ``命中数 / 总数`` 证明,
    且分母是**全部**, 不是「我看得见的那部分」。

但那天只做了一件门禁: 松断言门禁的视野自证。
**其他 5 道门禁呢?** 它们各自的视野是多少? 没人算过。

这就是本脚本要补的洞
--------------------
**门禁的视野不是靠「我扫了哪些目录」这种注释来保证的, 是靠「算」出来的。**

具体做法: 对仓库里每一道门禁, 本脚本做三件事:

1. **算它的关注面(universe)** —— 「这类问题应该存在在哪」的全集。
   例如复杂度门禁的关注面 = 「所有非测试 Python 文件」;
   松断言门禁的关注面 = 「所有含断言的 Python 文件」。

2. **读它自己声明的扫描范围(scope)** —— **从门禁自己的源码里读**,
   不是从登记册里抄。用 AST 把 ``SCAN_PATHS`` / ``DEFAULT_SCAN_PATHS``
   这种模块级常量解析出来; CI 里的门禁则从 workflow YAML 的 run 段里
   把路径 token 抠出来。

   **为什么必须读源码而不是抄登记册**: 抄登记册 = 登记册和代码可以各说各话,
   而「登记册说自己管 A, 代码实际扫 B」正是 ADR-0017 那类漂移的翻版。
   从源码里读 → 登记册和代码不一致时, **门禁自己会红**。

3. **算覆盖率, 并和门禁自己声明的门槛比** —— 覆盖率低于门槛 → 红。

外加一条**镜像校验(mirror claim)**
----------------------------------
iter #84 的复杂度棘轮门禁, 源码注释里写着一句话:

    「扫描范围: 与 CI lint job 的 flake8 目标完全一致
      (少扫一个目录 = 那个目录的复杂度债根本不在门禁视野里)」

**这句话从来没被验证过。** 两处各自写着「agents/ optimization/ synthetic/
data/ web/backend/」, 哪天有人只改了一边, 注释就变成了谎言 ——
而谎言不会让 CI 变红。

本脚本的 mirror 段就是来验证这句话的: 两道门禁声明为镜像 → 范围必须**逐项相等**,
不等 → 红。

为什么是 fail-closed
---------------------
和 ADR-0010 ~ ADR-0019 一脉相承:

- **不漏**: 关注面里有文件不在任何门禁的视野里 → 红。
- **不空**: 关注面算出来是空的 → 红(说明这个门禁的 universe 定义写错了)。
- **不瞎**: 门禁的扫描范围解析不出来 → 红(说明门禁改了实现, 登记册没跟上)。
- **不静默**: 镜像声明对不上 → 红。

跑法
----
    python scripts/gate_vision_census.py            # 普查(CI 跑的就是这条)
    python scripts/gate_vision_census.py --json     # 机器可读
    python scripts/gate_vision_census.py --write    # 刷新 config/gate_vision.json 里的实测值

登记册
------
``config/gate_vision.json``:

    {
      "version": 1,
      "gates": [
        {
          "id": "complexity-ratchet",
          "title": "复杂度棘轮门禁",
          "concern": "PRODUCTION_PY",
          "scope_source": {
            "kind": "module_const",
            "path": "scripts/gen_complexity_baseline.py",
            "names": ["SCAN_PATHS"]
          },
          "min_coverage_pct": 100.0
        }
      ],
      "mirror_claims": [
        {"a": "complexity-ratchet", "b": "flake8-lint",
         "why": "源码注释声称「与 CI lint job 的 flake8 目标完全一致」"}
      ]
    }
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REGISTRY_PATH = os.path.join(_REPO_ROOT, "config", "gate_vision.json")

# 任何 Python 文件普查都要躲开的目录(虚拟环境 / 前端依赖 / 构建产物)
GLOBAL_EXCLUDES = {
    "venv", ".venv", "node_modules", ".git", "__pycache__",
    "build", "dist", ".mypy_cache", ".pytest_cache",
}

# 关注面类型
CONCERN_KINDS = {
    # 所有非测试 Python 文件 —— 复杂度 / 静态分析这类「代码质量」门禁的关注面
    "PRODUCTION_PY": "所有非测试 Python 文件",
    # 所有 Python 文件(含测试) —— 覆盖式门禁的关注面
    "ALL_PY": "所有 Python 文件(含测试)",
    # 含断言的 Python 文件 —— 断言类门禁的关注面
    "PY_WITH_ASSERTS": "所有含断言的 Python 文件",
}

# unittest / mock 的断言方法名(用于判定「这个文件里有没有断言」)
_ASSERT_METHODS = {
    "assertEqual", "assertNotEqual", "assertIn", "assertNotIn", "assertTrue",
    "assertFalse", "assertIs", "assertIsNot", "assertIsNone", "assertIsNotNone",
    "assertAlmostEqual", "assertNotAlmostEqual", "assertRaises",
    "assertRaisesRegex", "assertGreater", "assertGreaterEqual", "assertLess",
    "assertLessEqual", "assertIsInstance", "assertNotIsInstance",
    "assertCountEqual", "assertDictEqual", "assertListEqual", "assertTupleEqual",
    "assertSetEqual", "assertMultiLineEqual", "assertSequenceEqual",
    "assertRegex", "assertNotRegex",
    "assert_called", "assert_called_once", "assert_not_called",
    "assert_any_call", "assert_has_calls",
}

# 「像路径」的 token: 目录(以 / 结尾) 或 .py 文件
_PATH_TOKEN_RE = re.compile(r"(?<![\w./-])([A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*/?|\S+\.py)")


# ====================================================================
# 1. 仓库遍历
# ====================================================================

def _excluded(rel_dir: str) -> bool:
    return any(part in GLOBAL_EXCLUDES for part in rel_dir.split(os.sep))


def iter_python_files(include_tests: bool = True) -> List[str]:
    """仓库里所有 Python 文件(仓库根相对路径, 已排序)。

    ``include_tests=False`` 时排除 ``tests/`` 和 ``selftest/``。
    """
    result: List[str] = []
    for root, dirs, files in os.walk(_REPO_ROOT):
        dirs[:] = sorted(d for d in dirs if d not in GLOBAL_EXCLUDES)
        rel_root = os.path.relpath(root, _REPO_ROOT)
        if rel_root == ".":
            rel_root = ""
        if not include_tests:
            top = rel_root.split(os.sep)[0] if rel_root else ""
            if top in ("tests", "selftest"):
                dirs[:] = []
                continue
        for fname in sorted(files):
            if not fname.endswith(".py"):
                continue
            rel = os.path.join(rel_root, fname) if rel_root else fname
            result.append(rel.replace(os.sep, "/"))
    return sorted(result)


def _file_has_assertion(abs_path: str) -> bool:
    """这个文件里**代码上**有没有断言(用 AST, 不用 grep —— 免得把 docstring 算进去)。"""
    try:
        with open(abs_path, "r", encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=abs_path)
    except (SyntaxError, OSError):
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.Assert):
            return True
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name in _ASSERT_METHODS:
                return True
    return False


def build_universe(kind: str) -> List[str]:
    """算出某个关注面的全集(仓库根相对路径)。"""
    if kind not in CONCERN_KINDS:
        raise ValueError(f"未知的关注面类型: {kind}")

    files = iter_python_files(include_tests=True)
    if kind == "ALL_PY":
        return files
    if kind == "PRODUCTION_PY":
        return [f for f in files
                if f.split("/")[0] not in ("tests", "selftest")]
    # PY_WITH_ASSERTS
    result = []
    for rel in files:
        if _file_has_assertion(os.path.join(_REPO_ROOT, rel)):
            result.append(rel)
    return result


# ====================================================================
# 2. 读门禁自己声明的扫描范围
# ====================================================================

def _assigned_names(node: ast.AST) -> List[str]:
    """这个赋值语句把值绑定给了哪些名字。"""
    if isinstance(node, ast.Assign):
        return [t.id for t in node.targets if isinstance(t, ast.Name)]
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return [node.target.id]
    return []


def _string_list(value: Optional[ast.AST]) -> List[str]:
    """把 List / Tuple / Set / 单个字符串常量还原成字符串列表。"""
    if isinstance(value, (ast.List, ast.Tuple, ast.Set)):
        return [e.value for e in value.elts
                if isinstance(e, ast.Constant) and isinstance(e.value, str)]
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        return [value.value]
    return []


def _module_constants(path: str, names: Sequence[str]) -> Dict[str, List[str]]:
    """从模块源码里解析模块级常量(AST, 不 import —— 免得执行门禁代码)。"""
    with open(path, "r", encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=path)

    wanted = set(names)
    found: Dict[str, List[str]] = {}

    for node in tree.body:
        value = node.value if isinstance(node, (ast.Assign, ast.AnnAssign)) else None
        for name in _assigned_names(node):
            if name not in wanted:
                continue
            items = _string_list(value)
            if items:
                found[name] = items
    return found


def _find_step_block(lines: List[str], step_name: str) -> List[str]:
    """定位某个 step 的块(从 ``- name:`` 往后到下一个同级 step)。"""
    start = None
    for i, line in enumerate(lines):
        if line.strip().startswith("- name:") and step_name in line:
            start = i
            break
    if start is None:
        return []

    block: List[str] = []
    base_indent = len(lines[start]) - len(lines[start].lstrip())
    for line in lines[start + 1:]:
        stripped = line.strip()
        if not stripped:
            block.append("")
            continue
        indent = len(line) - len(line.lstrip())
        if stripped.startswith("- name:") and indent <= base_indent:
            break
        if indent == 0 and stripped and not stripped.startswith("#"):
            break
        block.append(line)
    return block


def _run_lines(block: List[str]) -> List[str]:
    """取出 step 里 ``run:`` 段的命令行。

    GitHub Actions 里绝大多数 step 写的是 ``run: |`` + 下一行缩进的多行命令 ——
    命令**不在** ``run:`` 那一行, 所以 block scalar 的内容要一起收进来。
    """
    commands: List[str] = []
    run_indent: Optional[int] = None

    for line in block:
        stripped = line.strip()
        if run_indent is None:
            if stripped.startswith("run:"):
                run_indent = len(line) - len(line.lstrip())
                first = stripped[len("run:"):].strip()
                if first and first != "|":
                    commands.append(first)
            continue
        if not stripped:
            continue
        indent = len(line) - len(line.lstrip())
        if indent <= run_indent:
            break
        commands.append(line)
    return commands


def _path_tokens(text: str) -> List[str]:
    """从一行命令里抠出像路径的 token(目录或 .py 文件)。"""
    tokens: List[str] = []
    for match in _PATH_TOKEN_RE.finditer(text):
        token = match.group(1)
        if token.startswith("-"):
            continue
        if token.endswith("/") or token.endswith(".py"):
            tokens.append(token.rstrip("/"))
    return tokens


def _dedupe(items: List[str]) -> List[str]:
    """去重, 保序。"""
    seen: Set[str] = set()
    ordered: List[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            ordered.append(item)
    return ordered


def _workflow_step_paths(path: str, step_name: str) -> List[str]:
    """从 workflow YAML 里抠出某个 step 的 run 段里的路径 token。

    故意**不**依赖 PyYAML —— 这份脚本要在任何 checkout 上都能跑,
    少一个依赖就少一个「本地能跑、CI 装不上」的理由。
    """
    with open(path, "r", encoding="utf-8") as fh:
        lines = fh.read().splitlines()

    block = _find_step_block(lines, step_name)
    if not block:
        return []

    tokens: List[str] = []
    for command in _run_lines(block):
        tokens.extend(_path_tokens(command))
    return _dedupe(tokens)


def resolve_scope(scope_source: Dict[str, Any]) -> Tuple[List[str], Optional[str]]:
    """解析一道门禁的扫描范围 → (范围列表, 错误信息)。"""
    kind = scope_source.get("kind")
    path = scope_source.get("path", "")

    if kind == "module_const":
        abs_path = os.path.join(_REPO_ROOT, path)
        if not os.path.exists(abs_path):
            return [], f"门禁实现文件不存在: {path}"
        names = scope_source.get("names") or []
        try:
            consts = _module_constants(abs_path, names)
        except SyntaxError as exc:
            return [], f"门禁实现文件语法错误: {path} ({exc.msg})"
        missing = [n for n in names if n not in consts]
        if missing:
            return [], (f"在 {path} 里找不到模块级常量: {', '.join(missing)} "
                       f"(门禁改了实现? 登记册没跟上?)")
        scope: List[str] = []
        for name in names:
            scope.extend(consts[name])
        return scope, None

    if kind == "workflow_paths":
        abs_path = os.path.join(_REPO_ROOT, path)
        if not os.path.exists(abs_path):
            return [], f"workflow 文件不存在: {path}"
        step = scope_source.get("step", "")
        tokens = _workflow_step_paths(abs_path, step)
        if not tokens:
            return [], f"在 {path} 里找不到 step '{step}' 的路径 token"
        return tokens, None

    return [], f"未知的 scope_source.kind: {kind}"


# ====================================================================
# 3. 覆盖率计算
# ====================================================================

def _covers(scope_path: str, rel_file: str) -> bool:
    """rel_file 有没有被 scope_path 这个范围覆盖到。"""
    scope_path = scope_path.rstrip("/")
    if not scope_path:
        return True
    if scope_path.endswith(".py"):
        return rel_file == scope_path
    return rel_file == scope_path or rel_file.startswith(scope_path + "/")


def compute_coverage(universe: List[str], scope: List[str]) -> Dict[str, Any]:
    """关注面里有多少文件落在 scope 里。"""
    covered = [f for f in universe if any(_covers(s, f) for s in scope)]
    uncovered = [f for f in universe if f not in set(covered)]
    total = len(universe)
    pct = (len(covered) / total * 100.0) if total else 0.0
    return {
        "total": total,
        "covered": len(covered),
        "uncovered": uncovered,
        "coverage_pct": round(pct, 2),
    }


# ====================================================================
# 4. 登记册
# ====================================================================

def load_registry(path: str = REGISTRY_PATH) -> Dict[str, Any]:
    if not os.path.exists(path):
        return {"version": 1, "gates": [], "mirror_claims": []}
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    data.setdefault("version", 1)
    data.setdefault("gates", [])
    data.setdefault("mirror_claims", [])
    data.setdefault("known_gaps", [])
    return data


def _validate_gate(gate: Dict[str, Any], index: int) -> List[str]:
    """单道门禁的登记合法性。"""
    errors: List[str] = []
    gid = gate.get("id", "")

    if not gid:
        return [f"登记册第 {index + 1} 道门禁没有 id"]

    for required in ("title", "concern", "scope_source", "min_coverage_pct"):
        if required not in gate:
            errors.append(f"门禁 {gid} 缺少必填字段: {required}")

    concern = gate.get("concern")
    if concern and concern not in CONCERN_KINDS:
        errors.append(
            f"门禁 {gid} 的关注面 '{concern}' 不认识 "
            f"(允许: {', '.join(sorted(CONCERN_KINDS))})"
        )

    threshold = gate.get("min_coverage_pct")
    if threshold is not None and not (0 < float(threshold) <= 100):
        errors.append(f"门禁 {gid} 的 min_coverage_pct 必须在 (0, 100] 区间: {threshold}")

    scope_source = gate.get("scope_source") or {}
    if scope_source and not scope_source.get("kind"):
        errors.append(f"门禁 {gid} 的 scope_source 缺少 kind")

    return errors


def _validate_mirrors(registry: Dict[str, Any]) -> List[str]:
    """镜像声明的合法性(引用悬空 / 缺 why)。"""
    errors: List[str] = []
    ids = {g.get("id") for g in registry.get("gates", [])}
    for i, claim in enumerate(registry.get("mirror_claims", [])):
        for side in ("a", "b"):
            if claim.get(side) not in ids:
                errors.append(
                    f"镜像声明第 {i + 1} 条引用了不存在的门禁: "
                    f"{side}={claim.get(side)}"
                )
        if not claim.get("why"):
            errors.append(f"镜像声明第 {i + 1} 条没有写 why(为什么这两道门禁必须一致)")
    return errors


def _validate_gaps(registry: Dict[str, Any]) -> List[str]:
    """已知缺口的登记完整性。"""
    errors: List[str] = []
    for i, gap in enumerate(registry.get("known_gaps", [])):
        gid = gap.get("id", "")
        if not gid:
            errors.append(f"已知缺口第 {i + 1} 条没有 id")
        for required in ("title", "why", "scope_source"):
            if required not in gap:
                errors.append(f"已知缺口 {gid or i + 1} 缺少必填字段: {required}")
    return errors


def validate_registry(registry: Dict[str, Any]) -> List[str]:
    """登记册自身的合法性(缺字段 / 未知关注面 / 门槛非法 / 镜像引用悬空)。"""
    errors: List[str] = []
    seen_ids: Set[str] = set()

    for i, gate in enumerate(registry.get("gates", [])):
        gid = gate.get("id", "")
        if gid and gid in seen_ids:
            errors.append(f"登记册里有重复的门禁 id: {gid}")
        if gid:
            seen_ids.add(gid)
        errors.extend(_validate_gate(gate, i))

    errors.extend(_validate_mirrors(registry))
    errors.extend(_validate_gaps(registry))
    return errors


# ====================================================================
# 5. 普查
# ====================================================================

def _census_one(gate: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """普查单道门禁 → (结果条目, 实际扫描范围)。"""
    gid = gate.get("id", "?")
    concern = gate.get("concern", "")
    threshold = float(gate.get("min_coverage_pct", 0) or 0)

    entry: Dict[str, Any] = {
        "id": gid,
        "title": gate.get("title", gid),
        "concern": concern,
        "concern_desc": CONCERN_KINDS.get(concern, "?"),
        "min_coverage_pct": threshold,
        "scope": [],
        "errors": [],
    }

    try:
        universe = build_universe(concern)
    except ValueError as exc:
        entry["errors"].append(str(exc))
        return entry, []

    scope, err = resolve_scope(gate.get("scope_source") or {})
    entry["scope"] = scope
    if err:
        entry["errors"].append(err)
        return entry, scope

    cov = compute_coverage(universe, scope)
    entry.update(cov)

    if cov["total"] == 0:
        entry["errors"].append(
            f"关注面 {concern} 算出来是空的 → 门禁的 universe 定义写错了"
        )
    elif cov["coverage_pct"] < threshold:
        entry["errors"].append(
            f"视野只有 {cov['coverage_pct']}% "
            f"({cov['covered']}/{cov['total']}), 低于自报的门槛 {threshold}%"
        )

    return entry, scope


def _census_gap(gap: Dict[str, Any]) -> Dict[str, Any]:
    """普查一个「已知缺口」(登记在案, 不阻断)。"""
    gid = gap.get("id", "?")
    concern = gap.get("concern", "PRODUCTION_PY")
    entry = {
        "id": gid,
        "title": gap.get("title", gid),
        "why": gap.get("why", ""),
        "concern": concern,
        "scope": [],
        "errors": [],
    }
    try:
        universe = build_universe(concern)
    except ValueError as exc:
        entry["errors"].append(str(exc))
        return entry
    scope, err = resolve_scope(gap.get("scope_source") or {})
    entry["scope"] = scope
    if err:
        entry["errors"].append(err)
        return entry
    entry.update(compute_coverage(universe, scope))
    return entry


def census(registry: Dict[str, Any]) -> Dict[str, Any]:
    """普查每一道门禁的视野。"""
    gates_out: List[Dict[str, Any]] = []
    scope_by_id: Dict[str, List[str]] = {}

    for gate in registry.get("gates", []):
        entry, scope = _census_one(gate)
        gates_out.append(entry)
        scope_by_id[entry["id"]] = scope

    # ---- 已知缺口: 登记在案, **不**阻断 CI ----
    # 有些门禁的视野天生就是有意收窄的(比如 mypy 只管有类型标注的代码)。
    # 把它藏起来 = 又一次「我只看得见我看得见的那部分」；
    # 把它写成红灯 = 门禁变成永远红的噪音机, 大家就会去改门槛。
    # 第三条路: **登记在案 + 持续测量** → 缺口不会变大, 也不会被忘掉。
    gaps_out = [_census_gap(gap) for gap in registry.get("known_gaps", [])]

    # ---- 镜像校验: 两道门禁声明一致, 范围就必须逐项相等 ----
    mirror_results: List[Dict[str, Any]] = []
    for claim in registry.get("mirror_claims", []):
        a, b = claim.get("a"), claim.get("b")
        scope_a = set(scope_by_id.get(a, []))
        scope_b = set(scope_by_id.get(b, []))
        only_a = sorted(scope_a - scope_b)
        only_b = sorted(scope_b - scope_a)
        entry = {
            "a": a,
            "b": b,
            "why": claim.get("why", ""),
            "only_in_a": only_a,
            "only_in_b": only_b,
            "equal": not only_a and not only_b,
            "errors": [],
        }
        if not only_a and not only_b:
            entry["errors"].append(
                f"镜像声明 {a} ≡ {b} 成立 —— 注释里的那句话目前是真的"
            )
        else:
            msg = (f"镜像声明 {a} ≡ {b} 不成立: "
                   f"只在 {a} 里的: {only_a or '无'}; 只在 {b} 里的: {only_b or '无'}")
            entry["errors"].append(msg)
        mirror_results.append(entry)

    return {"gates": gates_out, "mirrors": mirror_results, "known_gaps": gaps_out}


# ====================================================================
# 6. 判定 + 报告
# ====================================================================

def build_report(registry: Dict[str, Any],
                 result: Dict[str, Any],
                 registry_errors: List[str]) -> Dict[str, Any]:
    errors: List[str] = list(registry_errors)
    violations: List[Dict[str, Any]] = []

    for gate in result["gates"]:
        for err in gate["errors"]:
            if "低于自报的门槛" in err:
                violations.append({"gate": gate["id"], "message": err})
            else:
                errors.append(f"[{gate['id']}] {err}")

    for mirror in result["mirrors"]:
        if not mirror["equal"]:
            errors.append(f"[mirror:{mirror['a']}≡{mirror['b']}] {mirror['errors'][0]}")

    gates_summary = []
    for gate in result["gates"]:
        gates_summary.append({
            "id": gate["id"],
            "title": gate["title"],
            "concern": gate["concern"],
            "concern_desc": gate["concern_desc"],
            "scope": gate["scope"],
            "total": gate.get("total", 0),
            "covered": gate.get("covered", 0),
            "coverage_pct": gate.get("coverage_pct", 0.0),
            "min_coverage_pct": gate["min_coverage_pct"],
            "uncovered": gate.get("uncovered", []),
            "errors": gate["errors"],
        })

    return {
        "gates": gates_summary,
        "mirrors": result["mirrors"],
        "known_gaps": result.get("known_gaps", []),
        "violations": violations,
        "errors": errors,
    }


def _print_gate(gate: Dict[str, Any]) -> None:
    """单道门禁的视野。"""
    flag = "❌" if any("低于自报的门槛" in e for e in gate["errors"]) else "✅"
    print(f"\n{flag} {gate['title']}  [{gate['id']}]")
    print(f"   关注面: {gate['concern_desc']} ({gate['concern']})")
    print(f"   实际扫描: {', '.join(gate['scope']) or '(解析失败)'}")
    if gate["total"]:
        print(f"   视野: {gate['covered']}/{gate['total']} = {gate['coverage_pct']}%"
              f"   (自报门槛 {gate['min_coverage_pct']}%)")
        if gate["uncovered"]:
            print(f"   ⚠ 视野外: {', '.join(gate['uncovered'][:10])}"
                  + (" ..." if len(gate["uncovered"]) > 10 else ""))
    for err in gate["errors"]:
        print(f"   → {err}")


def _print_mirrors(mirrors: List[Dict[str, Any]]) -> None:
    """镜像校验。"""
    if not mirrors:
        return
    print("\n镜像校验(「A 和 B 完全一致」这类注释里的承诺):")
    for mirror in mirrors:
        mark = "✅" if mirror["equal"] else "❌"
        print(f"  {mark} {mirror['a']} ≡ {mirror['b']}")
        print(f"     理由: {mirror['why']}")
        if not mirror["equal"]:
            print(f"     只在 {mirror['a']}: {mirror['only_in_a']}")
            print(f"     只在 {mirror['b']}: {mirror['only_in_b']}")


def _print_gaps(gaps: List[Dict[str, Any]]) -> None:
    """已知视野缺口: 登记在案, 持续测量, 不阻断 CI。"""
    if not gaps:
        return
    print("\n已知视野缺口(登记在案 · 持续测量 · 不阻断 CI):")
    for gap in gaps:
        scope = ", ".join(gap.get("scope", [])) or "(解析失败)"
        print(f"  • {gap['title']}  [{gap['id']}]")
        print(f"    视野: {gap.get('covered', 0)}/{gap.get('total', 0)}"
              f" = {gap.get('coverage_pct', 0.0)}%   扫描: {scope}")
        if gap.get("why"):
            print(f"    为什么: {gap['why']}")


def print_report(report: Dict[str, Any]) -> None:
    print("门禁视野普查 (ADR-0020) —「门禁的覆盖面 = 门禁的价值上限」")
    print("=" * 68)
    for gate in report["gates"]:
        _print_gate(gate)

    _print_mirrors(report["mirrors"])
    _print_gaps(report.get("known_gaps", []))

    if report["violations"]:
        print("\n❌ 视野不足(fail-closed):")
        for v in report["violations"]:
            print(f"  - [{v['gate']}] {v['message']}")

    if report["errors"]:
        print("\n❌ 校验失败(fail-closed):")
        for e in report["errors"]:
            print(f"  - {e}")
        return

    if report["violations"]:
        return

    print("\n✅ 每道门禁的视野都覆盖了它该管的全集 —— "
          "绿灯是全覆盖的绿灯, 不是「我看得见的那部分」的绿灯。")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="门禁视野普查: 算清每道门禁到底看得见多少")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args()

    registry = load_registry()
    registry_errors = validate_registry(registry)
    result = census(registry)
    report = build_report(registry, result, registry_errors)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1 if report["errors"] or report["violations"] else 0

    print_report(report)
    if report["errors"] or report["violations"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
