#!/usr/bin/env python3
"""
gen_complexity_baseline.py — 把「圈复杂度债」变成一条只能单向变好的棘轮门禁 (iter #84)

为什么需要这个脚本
------------------
仓库的 ``setup.cfg`` 里写着 ``max-complexity = 10``, CI 的 lint job 里也跑着
``flake8 --max-complexity=10 --exit-zero`` —— 但**它是 ``--exit-zero``**。

也就是说: 那条规则**从来没有真正拦住过任何东西**。
复杂度 36 的 ``health_check_deep``、复杂度 32 的 ``get_forecast_confidence`` 就那么
躺在主干上, 每次 lint 都在报, 每次都当没看见。

**这不是「没时间改」的问题, 是「门禁没有牙齿」的问题。**
一个永远不会红的门禁, 和没有门禁是同一个东西 —— 而且更糟:
它让人误以为复杂度是被管着的。

直接改成「复杂度 > 10 就红」行不行? **不行** —— 一次性还掉 20 个函数的债,
会让这个 PR 变成一个巨大的、无法评审的重构, 而且每一步都可能被改错。
「一刀切改严」在实践中几乎总是失败: 要么没人敢合, 要么全靠 ``# noqa`` 逃避。

所以本脚本做的是**棘轮(ratchet)**: 债务只许降, 不许涨。
像数据库 migration 一样 —— 一格一格地往前爬, 每格都可评审、可回滚。

三层规则(和 ADR-0010 ~ ADR-0015 一脉相承)
-----------------------------------------
**第 1 层: 复杂度只许降, 不许升(红)**
    同一个函数的圈复杂度比基线**更高** → 红灯。
    这是门禁的主体: 已经很难的代码, 不许再变得更难。

**第 2 层: 新函数必须够简单(红)**
    基线里没有的新函数, 复杂度 > 阈值(10) → 红灯。
    债不能以「新写的代码」的形式偷偷溜进来 —— 否则还债永远还不完:
    老代码在降, 新代码在涨, 总量没动。

**第 3 层: 结构性不变量(红)**
    基线里的函数**消失**了 → 红灯。
    函数被删掉或改名, 而基线没同步 → 说明这次改动**没有被人真正看一眼**。
    (和 ADR-0013「陈旧豁免 → 红」是同一条哲学: **沉默的缺口必须是红的**。)

只打印、不阻断的部分(黄)
-------------------------
- **复杂度变低了** → 进步, 但基线还记着旧数字 → 提示可以 ``--write`` 记录进展
- **新增函数且复杂度 ≤ 阈值** → 合规, 提示记进基线
  (不阻断: 写一个普通的小函数不该被门禁拦住)

为什么不把「变低」也做成红灯(强制每次都刷基线)?
-------------------------------------------------
因为**基线的价值在于「相对上一次」**, 而不在于「永远是最新的」。
每次都强制刷基线 → 每次 PR 都要改基线文件 → 评审噪音 → 门禁被无视。
只在**债务真的动了**(降 / 增 / 删)的时候提示刷新, 才是合适的粒度。

这和 ADR-0011 的立场完全一致:
> 「门禁一旦『什么变化都报红』, 团队就会开始无脑刷基线 —— 那它就废了。」

复杂度是怎么算的
----------------
**直接用 flake8 自己的 C901 实现**(mccabe) —— 不用自己实现一套。
理由: 开发者本地跑 ``flake8`` 看到的数字, 和门禁看到的数字必须**一模一样**。
自己写一个「差不多的复杂度」= 门禁和开发者对不上账 = 门禁必被无视。

键(key)为什么是「路径 + 类名 + 函数名」而不是行号?
--------------------------------------------------
行号会漂 —— 上面加一行注释, 下面所有函数的行号全变。
按行号冻结基线 = 每改一次代码就得刷一次基线 = 门禁作废。
所以键用**限定名**(qualified name): ``文件路径::类名.函数名``。
行号只作为**给人看的定位信息**存在, 不参与判定。

用法
----
    # 跑门禁 (CI 用的就是这条)
    python scripts/gen_complexity_baseline.py

    # 刷新基线 (还了债 / 删了函数 之后)
    python scripts/gen_complexity_baseline.py --write

    # 看当前复杂度全景 (调试用, stdout 是纯 JSON)
    python scripts/gen_complexity_baseline.py --json
"""

from __future__ import annotations

import argparse
import ast
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

BASELINE_PATH = _REPO_ROOT / "tests" / "complexity_baseline.json"

SCHEMA_VERSION = 1

# 阈值: 与 setup.cfg 的 max-complexity 保持一致
THRESHOLD = 10

# 扫描范围: 与 CI lint job 的 flake8 目标完全一致
# (少扫一个目录 = 那个目录的复杂度债根本不在门禁视野里)
SCAN_PATHS = ["agents", "optimization", "synthetic", "data", "web/backend"]


# ---------------------------------------------------------------- 复杂度采集

def _run_flake8(paths: List[str]) -> Tuple[List[Tuple[str, int, str, int]], List[str]]:
    """跑一次 flake8 C901 → ([(文件, 行号, 函数名, 复杂度)], 错误行)。

    ``--max-complexity=0`` 的作用: 让 flake8 把**每一个**函数都报出来
    (复杂度 1 的平凡函数也报), 这样「新增函数」才能被识别出来 ——
    否则新写的简单函数根本不会出现在报告里, 基线就会漏记。
    """
    cmd = [
        sys.executable, "-m", "flake8",
        "--select=C901",
        "--max-complexity=0",
        "--format=%(path)s\t%(row)d\t%(text)s",
        *paths,
    ]
    proc = subprocess.run(cmd, cwd=str(_REPO_ROOT), capture_output=True, text=True)
    if proc.returncode not in (0, 1):
        raise RuntimeError(
            f"flake8 执行失败 (rc={proc.returncode}):\n{proc.stderr.strip()[:2000]}"
        )

    entries: List[Tuple[str, int, str, int]] = []
    errors: List[str] = []
    for line in proc.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            if line.strip():
                errors.append(line.strip())
            continue
        path, row, text = parts
        # text 形如: C901 'func_name' is too complex (17)
        if "'" not in text or "(" not in text:
            errors.append(line.strip())
            continue
        name = text.split("'")[1]
        try:
            complexity = int(text.rsplit("(", 1)[1].rstrip(")"))
        except ValueError:
            errors.append(line.strip())
            continue
        entries.append((path, int(row), name, complexity))

    return entries, errors


def _qualified_names(path: Path) -> Dict[int, str]:
    """行号 → 限定名(``类名.函数名``)。用来消除同名函数的歧义。

    flake8 的 C901 消息里只有**裸函数名**, 所以 ``Foo.run`` 和 ``Bar.run``
    在报告里长得一模一样 —— 而它们根本不是同一个函数。
    按行号回 AST 里取「最近的祖先类」补上类名, 键才是稳定的。
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return {}

    mapping: Dict[int, str] = {}

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                visit(child, f"{prefix}{child.name}.")
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                mapping[child.lineno] = f"{prefix}{child.name}"
                visit(child, f"{prefix}{child.name}.")  # 支持嵌套函数
            else:
                visit(child, prefix)

    visit(tree, "")
    return mapping


# mccabe 除了函数, 还会给**语句块**建图: ``If <行号>`` / ``Loop <行号>`` /
# ``TryExcept <行号>`` / ``With <行号>``。
#
# 这些图**不是函数**, 而且名字里**嵌着行号** —— 拿它们当基线键 = 基线每天都在漂。
# 更重要的是: 它们的复杂度是**包含在**外层函数复杂度里的(重复计数),
# 而且块级复杂度 > 阈值并不意味着「这个函数难」, 只意味着「这个 if 难」。
#
# 所以门禁只认**真正的函数**: 用 AST 的 FunctionDef 行号做过滤。
# 被过滤掉的块级条目数量会被记进快照(``skipped_block_graphs``), 不是静默丢弃。
BLOCK_ENTITY_PREFIXES = ("If ", "Loop ", "TryExcept ", "With ", "For ")


def collect() -> Dict[str, Any]:
    """采集当前代码库的复杂度全景。"""
    entries, errors = _run_flake8(SCAN_PATHS)

    functions: Dict[str, Dict[str, Any]] = {}
    qual_cache: Dict[str, Dict[int, str]] = {}
    skipped_blocks = 0

    for path, lineno, name, complexity in entries:
        rel = Path(path).resolve().relative_to(_REPO_ROOT).as_posix()
        if rel not in qual_cache:
            qual_cache[rel] = _qualified_names(_REPO_ROOT / rel)
        qual = qual_cache[rel].get(lineno)
        if qual is None:
            # 不是函数 → mccabe 的语句块图(``If 686`` 那种)。
            # 名字里嵌着行号, 不能当基线键; 也不代表「某个函数变难了」。
            if name.startswith(BLOCK_ENTITY_PREFIXES) or " " in name:
                skipped_blocks += 1
            else:
                errors.append(
                    f"{rel}:{lineno}: C901 条目 `{name}` 在 AST 里找不到对应函数定义"
                )
            continue
        key = f"{rel}::{qual}"
        # 同一个限定名只应出现一次; 真出现重复说明 AST 与 flake8 视图不一致 → 记下来
        prev = functions.get(key)
        if prev is not None and prev.get("complexity") != complexity:
            errors.append(
                f"重复的限定名 {key} (复杂度 {prev.get('complexity')} vs {complexity})"
            )
            continue
        functions[key] = {"complexity": complexity, "lineno": lineno}

    over = sorted(
        ((k, v["complexity"]) for k, v in functions.items() if v["complexity"] > THRESHOLD),
        key=lambda kv: (-kv[1], kv[0]),
    )

    return {
        "generated_by": "scripts/gen_complexity_baseline.py",
        "schema_version": SCHEMA_VERSION,
        "threshold": THRESHOLD,
        "scan_paths": SCAN_PATHS,
        "total_functions": len(functions),
        "skipped_block_graphs": skipped_blocks,
        "over_threshold": [{"function": k, "complexity": c} for k, c in over],
        "functions": functions,
        "_errors": errors,
    }


# ---------------------------------------------------------------- 差异比对

def diff(baseline: Dict[str, Any], current: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    """比对基线与当前 → (阻断项, 提示项)。"""
    breaking: List[str] = []
    notes: List[str] = []

    before = baseline.get("functions") or {}
    after = current.get("functions") or {}

    for key in sorted(set(before) & set(after)):
        old = int(before[key].get("complexity", 0))
        new = int(after[key].get("complexity", 0))
        if new > old:
            breaking.append(
                f"{key}: 复杂度 {old} → {new} —— 棘轮不许往回爬 "
                f"(要么拆函数, 要么在 PR 里说明并显式 --write 记录)"
            )
        elif new < old:
            notes.append(f"{key}: 复杂度 {old} → {new} (还债 {old - new}, 建议 --write 记录进展)")

    for key in sorted(set(after) - set(before)):
        new = int(after[key].get("complexity", 0))
        if new > THRESHOLD:
            breaking.append(
                f"{key}: 新函数复杂度 {new} > 阈值 {THRESHOLD} —— "
                f"新债不许进场 (拆成几个小函数, 或用 --write 显式认领这笔债)"
            )
        else:
            notes.append(f"{key}: 新函数, 复杂度 {new} ≤ {THRESHOLD} (合规, 建议 --write 记进基线)")

    for key in sorted(set(before) - set(after)):
        notes.append(
            f"{key}: 基线里的函数不存在了 (已删除 / 改名) —— "
            f"建议 --write 同步基线 (别让基线静默烂掉)"
        )

    return breaking, notes


# ---------------------------------------------------------------- 基线读写

def load_baseline() -> Dict[str, Any]:
    with open(BASELINE_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def write_baseline(snapshot: Dict[str, Any]) -> None:
    BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {k: v for k, v in snapshot.items() if not k.startswith("_")}
    with open(BASELINE_PATH, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")


def _print_report(breaking: List[str], notes: List[str], current: Dict[str, Any]) -> None:
    over = current.get("over_threshold") or []
    if notes:
        print("[提示（不阻断 CI）]")
        for line in notes:
            print(f"  {line}")
    if breaking:
        print("[阻断项（复杂度棘轮被反向转动）]")
        for line in breaking:
            print(f"  {line}")
    else:
        print("OK: 复杂度棘轮正常 —— 没有函数变得更难, 也没有新债进场")
    print(f"\n当前: {current.get('total_functions')} 个函数, "
          f"{len(over)} 个超阈值 (阈值 {current.get('threshold')})")
    for item in over[:10]:
        print(f"  {item['complexity']:>3}  {item['function']}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="把圈复杂度债变成只许单向变好的棘轮门禁")
    parser.add_argument("--write", action="store_true", help="刷新基线快照")
    parser.add_argument("--json", action="store_true", help="把当前快照打到 stdout (纯 JSON)")
    args = parser.parse_args()

    current = collect()

    if current.get("_errors"):
        print("[采集告警]")
        for line in current["_errors"][:20]:
            print(f"  {line}")

    if args.json:
        print(json.dumps({k: v for k, v in current.items() if not k.startswith("_")},
                         ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    if args.write:
        write_baseline(current)
        over = current.get("over_threshold") or []
        print(f"✅ 基线已刷新: {BASELINE_PATH.relative_to(_REPO_ROOT)} "
              f"({current['total_functions']} 个函数, {len(over)} 个仍超阈值)")
        return 0

    breaking, notes = diff(load_baseline(), current)
    _print_report(breaking, notes, current)
    if breaking:
        print("\n如果这是**故意**的复杂度变更，请跑：")
        print("    python scripts/gen_complexity_baseline.py --write")
        print("并在 PR 描述里说明：为什么这个函数值得变得更难。")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
