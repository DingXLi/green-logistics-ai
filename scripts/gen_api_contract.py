#!/usr/bin/env python3
"""
gen_api_contract.py — 从「类对象」机械地抽出一份公开 API 契约快照 (iter #78)

为什么需要这个脚本
------------------
ADR-0009 把 ``agents/persistence.py``(9,949 行)拆成 ``agents/persistence/`` 包时，
验证手段是「一次性人工双向比对老类 vs 新类的 114 个方法」。

一次性比对的问题：
- 结论只在那一天成立 —— 下一个 PR 加一个方法、删一个参数、改一个默认值，
  **没有任何东西会报警**；
- 比对结果没有留在仓库里 —— 面试时无法指着文件说「这是机器校验过的」。

所以把「公开 API 不变」从**一次性声明**变成**仓库里的常驻不变量**：
把契约快照存成 JSON，交给 ``tests/test_persistence_api_contract.py`` 每次 CI 都比对。

设计约束
--------
1. **机械抽取，不手写**：签名、参数默认值、返回注解全部从 ``inspect`` 拿。
   手写 104 个方法签名 = 一定会抄错，且没人能核对。
2. **可复现**：同一份代码跑两次，输出字节级一致（排序 + 无时间戳）。
3. **可对比**：默认输出 unified diff（和 git diff 一样），直接贴进 PR 描述。
4. **能当基线用**：``--legacy`` 模式从重构前的单文件版本抽契约，
   用来证明「拆分没改公开 API」这件事本身也是机器校验过的。

用法
----
    # 看当前公开 API 与仓库里基线的差异（CI 用的就是这条）
    python scripts/gen_api_contract.py

    # 刷新基线（只有在你**故意**改了公开 API 时才做）
    python scripts/gen_api_contract.py --write

    # 从重构前的单文件版本抽一份（证明拆分零变化）
    python scripts/gen_api_contract.py --legacy /tmp/legacy_persistence.py
"""

from __future__ import annotations

import argparse
import importlib.util
import inspect
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

CONTRACT_PATH = _REPO_ROOT / "tests" / "persistence_api_contract.json"


def _public_names(cls: type) -> List[str]:
    return sorted(n for n in dir(cls) if not n.startswith("_"))


def _format_default(value: Any) -> str:
    """把参数默认值渲染成稳定的字符串。

    只支持「简单不可变类型」——契约快照不该被 repr 一个巨大对象污染。
    遇到复杂默认值时降级成类型名 + 长度，保证仍然可比。
    """
    if value is inspect.Parameter.empty:
        return "<empty>"
    if value is None or isinstance(value, (bool, int, float, str, bytes)):
        return repr(value)
    if isinstance(value, (tuple, frozenset)):
        return f"{type(value).__name__}({', '.join(repr(v) for v in value)})"
    if isinstance(value, list):
        return f"list({len(value)})"
    if isinstance(value, dict):
        return f"dict({len(value)})"
    return f"<{type(value).__name__}>"


def _format_annotation(annotation: Any) -> str:
    """注解渲染 —— 去掉模块前缀，避免 ``typing.Optional[int]`` vs ``Optional[int]``。"""
    if annotation is inspect.Parameter.empty:
        return ""
    text = str(annotation)
    for prefix in ("typing.", "agents.persistence.", "collections.abc."):
        text = text.replace(prefix, "")
    return text


def _describe_method(func: Any, kind: str) -> Dict[str, Any]:
    """把一个可调用成员渲染成契约条目（签名 / 参数 / 返回注解）。"""
    try:
        sig = inspect.signature(func)
    except (TypeError, ValueError):
        return {"kind": kind, "signature": "<unavailable>"}

    params = []
    for pname, param in sig.parameters.items():
        params.append(
            {
                "name": pname,
                "kind": str(param.kind),
                "annotation": _format_annotation(param.annotation),
                "default": _format_default(param.default),
            }
        )

    return {
        "kind": kind,
        "params": params,
        "returns": _format_annotation(sig.return_annotation),
    }


def _describe_constant(value: Any) -> Dict[str, Any]:
    """把一个公开常量渲染成契约条目（形状，不含内容）。"""
    if isinstance(value, dict):
        return {
            "type": "dict",
            "keys": sorted(str(k) for k in value.keys()),
            "length": len(value),
        }
    if isinstance(value, (list, tuple)):
        return {"type": type(value).__name__, "length": len(value)}
    return {"type": type(value).__name__, "value": repr(value)}


def extract_contract(cls: type) -> Dict[str, Any]:
    """从类对象抽出完整公开 API 契约。

    分两层：
    - **public**（会被断言）：方法名 / 签名 / 参数默认值 / 返回注解 / 公开常量。
      这是调用方（141 个后端端点、100+ 调用点）真正依赖的东西。
    - **structure**（仅供参考，不触发失败）：MRO 与「哪个 mixin 提供哪个方法」。
      内部重新组织（把方法搬到另一个模块）**不应该**让 CI 变红 ——
      那不是 API 破坏，是重构。但它值得被看见（评审时能立刻发现方法被挪了窝）。
    """
    methods: Dict[str, Any] = {}
    constants: Dict[str, Any] = {}
    owners: Dict[str, List[str]] = {}

    for name in _public_names(cls):
        static = inspect.getattr_static(cls, name)
        if isinstance(static, property):
            methods[name] = {"kind": "property"}
            continue
        if isinstance(static, (classmethod, staticmethod)):
            func = static.__func__
            kind = "classmethod" if isinstance(static, classmethod) else "staticmethod"
        elif callable(static):
            func = getattr(cls, name)
            kind = "method"
        else:
            value = getattr(cls, name)
            constants[name] = _describe_constant(value)
            continue

        methods[name] = _describe_method(func, kind)

    # 谁提供了这个方法（ informational —— 换模块不算 API 破坏）
    for klass in cls.__mro__:
        for attr, value in vars(klass).items():
            if attr.startswith("_") or not callable(value):
                continue
            owners.setdefault(klass.__name__, []).append(attr)
    owners = {k: sorted(v) for k, v in sorted(owners.items())}

    return {
        "class": cls.__name__,
        "method_count": len(methods),
        "constant_count": len(constants),
        "public": {
            "methods": methods,
            "constants": constants,
        },
        "structure": {
            "mro": [c.__name__ for c in cls.__mro__],
            "owners": owners,
        },
    }


def load_class_from_file(path: str, class_name: str = "Persistence") -> type:
    """从任意 .py 文件动态加载一个类（用于抽重构前的基线）。"""
    spec = importlib.util.spec_from_file_location("_legacy_persistence", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["_legacy_persistence"] = module
    spec.loader.exec_module(module)
    return getattr(module, class_name)


def diff_contracts(old: Dict[str, Any], new: Dict[str, Any]) -> List[str]:
    """两个契约之间**会被断言**的差异（公开 API 破坏）。

    只看 public 层：方法增删、签名/默认值/返回注解变化、常量增删或形状变化。
    structure 层（MRO / 方法归属）不在这里 —— 那是重构自由，不是 API 破坏。
    """
    lines: List[str] = []
    old_p = old["public"]
    new_p = new["public"]
    old_m, new_m = old_p["methods"], new_p["methods"]

    for name in sorted(set(old_m) - set(new_m)):
        lines.append(f"  REMOVED method: {name}")
    for name in sorted(set(new_m) - set(old_m)):
        lines.append(f"  ADDED   method: {name}")
    for name in sorted(set(old_m) & set(new_m)):
        if old_m[name] != new_m[name]:
            lines.append(f"  CHANGED method: {name}")
            lines.append(f"      before: {json.dumps(old_m[name], sort_keys=True)}")
            lines.append(f"      after:  {json.dumps(new_m[name], sort_keys=True)}")

    old_c, new_c = old_p["constants"], new_p["constants"]
    for name in sorted(set(old_c) - set(new_c)):
        lines.append(f"  REMOVED constant: {name}")
    for name in sorted(set(new_c) - set(old_c)):
        lines.append(f"  ADDED   constant: {name}")
    for name in sorted(set(old_c) & set(new_c)):
        if old_c[name] != new_c[name]:
            lines.append(f"  CHANGED constant: {name}")
            lines.append(f"      before: {json.dumps(old_c[name], sort_keys=True)}")
            lines.append(f"      after:  {json.dumps(new_c[name], sort_keys=True)}")

    return lines


def diff_structure(old: Dict[str, Any], new: Dict[str, Any]) -> List[str]:
    """结构性差异 —— **不触发失败**，只是让人看见「方法被挪到哪个 mixin 了」。"""
    lines: List[str] = []
    old_s = old.get("structure", {})
    new_s = new.get("structure", {})
    old_o = old_s.get("owners", {})
    new_o = new_s.get("owners", {})

    moved: Dict[str, List[str]] = {}
    for owner, names in new_o.items():
        for n in names:
            previous = [o for o, ns in old_o.items() if n in ns]
            if previous and owner not in previous:
                moved.setdefault(n, []).append(f"{previous[0]} -> {owner}")
    if moved:
        lines.append("  (informational) 方法换了所属 mixin（不是 API 破坏）：")
        for n in sorted(moved):
            lines.append(f"      {n}: {', '.join(moved[n])}")

    if old_s.get("mro") != new_s.get("mro"):
        lines.append("  (informational) MRO 变了：")
        lines.append(f"      before: {old_s.get('mro')}")
        lines.append(f"      after:  {new_s.get('mro')}")

    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write",
        action="store_true",
        help="把当前公开 API 写回基线文件（只在故意改 API 时用）",
    )
    parser.add_argument(
        "--legacy",
        metavar="PATH",
        help="从指定的 .py 文件抽契约（例如重构前的单文件版本）",
    )
    parser.add_argument(
        "--compare-legacy",
        metavar="PATH",
        help="把指定 .py 文件的契约与仓库基线比对",
    )
    args = parser.parse_args()

    from agents.persistence import Persistence

    if args.legacy:
        contract = extract_contract(load_class_from_file(args.legacy))
        print(json.dumps(contract, indent=2, sort_keys=True, ensure_ascii=False))
        return 0

    current = extract_contract(Persistence)

    if args.compare_legacy:
        legacy = extract_contract(load_class_from_file(args.compare_legacy))
        lines = diff_contracts(legacy, current)
        if lines:
            print("PUBLIC API DRIFT vs legacy baseline:")
            print("\n".join(lines))
            return 1
        print(f"OK: public API identical to legacy baseline "
              f"({current['method_count']} methods, {current['constant_count']} constants)")
        for note in diff_structure(legacy, current):
            print(note)
        return 0

    if args.write:
        CONTRACT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(CONTRACT_PATH, "w", encoding="utf-8") as fh:
            json.dump(current, fh, indent=2, sort_keys=True, ensure_ascii=False)
            fh.write("\n")
        print(f"wrote {CONTRACT_PATH} "
              f"({current['method_count']} methods, {current['constant_count']} constants)")
        return 0

    # 默认：diff 模式
    if not CONTRACT_PATH.exists():
        print(f"baseline not found: {CONTRACT_PATH}")
        print("run: python scripts/gen_api_contract.py --write")
        return 1
    with open(CONTRACT_PATH, encoding="utf-8") as fh:
        baseline = json.load(fh)

    lines = diff_contracts(baseline, current)
    if lines:
        print("PUBLIC API DRIFT vs committed baseline:")
        print("\n".join(lines))
        print()
        print("如果这是**故意**的 API 变更，请跑：")
        print("    python scripts/gen_api_contract.py --write")
        print("并在 PR 描述里说明「哪些方法变了、为什么、下游调用点怎么改」。")
        return 1

    for note in diff_structure(baseline, current):
        print(note)
    print(f"OK: public API matches baseline "
          f"({current['method_count']} methods, {current['constant_count']} constants)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
