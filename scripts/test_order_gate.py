#!/usr/bin/env python3
"""
test_order_gate.py — **测试顺序无关性门禁** (iter #85, ADR-0017)

这个门禁要证明的命题
--------------------
    「CI 绿灯」不等于「测试可靠」。

iter #84 (ADR-0016) 的复杂度棘轮门禁上线当天抓到一个真 bug:
``pytest tests/ -k forecast`` 跑出 2 failed, 但 ``pytest tests/`` 全量跑是绿的。
根因是 ``main.py`` 的 ``startup_event`` 里有
``global coordinator; coordinator = MultiAgentCoordinator(...)``
→ 每次起 TestClient 都会**整个换掉** coordinator, 测试把 pref 写进了
一个**没人会读的 DB**。全量跑时 ambient coordinator 恰好绑同一个
``data/simulation.db``, 所以绿灯了。

翻译成一句工程语言:
    **我们当时只证明了「按这个顺序跑是绿的」, 没有证明「换个顺序还是绿的」。**

一个只在特定顺序下才通过的测试, 和一个随机通过的测试, 对交付质量是同一回事。

本模块做什么
------------
1. **静态圈定「顺序敏感队列」** —— 用 AST 找出所有**写 ambient 全局单例**
   的测试文件(比如 ``backend_main.coordinator = ...``)。这些文件就是
   「谁在污染全局状态」的嫌疑人, 也就是顺序依赖最可能藏身的地方。
   → 只测嫌疑队列, 不测全仓(全仓 × N 个 seed 的 CI 成本不可接受)。

2. **动态扰动验证** —— 用 N 个**可复现的 seed** 把嫌疑队列的测试顺序打乱,
   每个 seed 跑一次, 产出 JUnit XML, 然后**逐个 testcase 比对结果**。

   - 任何 testcase 在不同 seed 下的 outcome 不一致(过 ↔ 失败 / 过 ↔ 跳过)
     → **红**。这条测试的结果依赖了「谁先跑」, 它不是可靠的证据。
   - 全部 seed 结果一致 → **绿**。

3. **失败即定位** —— 报告直接给出「哪个测试、在哪个 seed 下翻转了」,
   而不是只说「有 flaky」。

设计取舍(为什么不做得更花哨)
---------------------------
- **不用 pytest-randomly 之类的第三方插件** —— 自己写一个 100 行的
  ``order_shuffle.py`` 就够了, 而且默认**零行为**(不设 env 就完全不干预),
  不会污染任何现有 CI 语义。
- **不打乱全仓** —— 全仓 × 3 个 seed ≈ 15 分钟 CI, 性价比不划算;
  圈定嫌疑队列后成本降到可接受, 而命中率更高。
- **要 JUnit XML 不用解析 stdout** —— stdout 解析会随 pytest 版本漂移,
  JUnit XML 是稳定契约, 能拿到 testcase 的**精确 id + outcome**。
- **seed 固定** —— 随机不是目的, **可复现的扰动**才是。CI 每次跑同一组 seed,
  同一个 flaky 每次都在同一处炸, 修完就不会复发。

跑法
----
    python scripts/test_order_gate.py                    # 默认 3 个 seed
    python scripts/test_order_gate.py --seeds 1,2,3,4,5  # 更多扰动
    python scripts/test_order_gate.py --all-files        # 不圈嫌疑队列, 全仓跑
    python scripts/test_order_gate.py --json             # 机器可读输出
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from typing import Dict, List, Set, Tuple

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

_TESTS_DIR = os.path.join(_REPO_ROOT, "tests")


# ====================================================================
# 1. 静态圈定嫌疑队列: 谁在写 ambient 全局单例?
# ====================================================================

# 已知的 ambient 单例 —— 「模块级全局状态」的完整清单。
# 这些名字在生产代码里是**进程级共享**的: 改一个 = 改所有。
# 一旦测试文件直接写它们, 测试之间就开始互相影响。
AMBIENT_SINGLETONS: Set[str] = {
    "coordinator",
    "data_generator",
    "ws_broadcaster",
}

# 写 ambient 单例的「合法载体」前缀 —— 写到这些模块的属性上算命中。
# 例: backend_main.coordinator = fake  →  命中
#     backend_main.other_thing = x     →  不命中(不是共享单例)
_ASSIGN_TARGETS = ("backend_main", "main", "web_backend_main")


def _is_singleton_attr(attr: str) -> bool:
    return attr in AMBIENT_SINGLETONS


def scan_ambient_writers() -> Dict[str, List[str]]:
    """找出所有给 ambient 单例赋值的测试文件。

    返回: {文件名: [命中的源码行, ...]}
    """
    hits: Dict[str, List[str]] = {}

    if not os.path.isdir(_TESTS_DIR):
        return hits

    for fname in sorted(os.listdir(_TESTS_DIR)):
        if not fname.startswith("test_") or not fname.endswith(".py"):
            continue

        path = os.path.join(_TESTS_DIR, fname)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                source = fh.read()
            tree = ast.parse(source, filename=path)
        except (OSError, SyntaxError):
            continue

        found: List[str] = []
        for node in ast.walk(tree):
            # 形如  backend_main.coordinator = X
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for tgt in targets:
                if not isinstance(tgt, ast.Attribute):
                    continue
                if not _is_singleton_attr(tgt.attr):
                    continue
                # 根对象必须是已知的 backend_main / main 之一
                root = tgt.value
                root_name = None
                if isinstance(root, ast.Name):
                    root_name = root.id
                elif isinstance(root, ast.Attribute):
                    # backend_main.xxx.coordinator = ... (深一层)
                    root_name = getattr(root, "attr", None)
                if root_name not in _ASSIGN_TARGETS:
                    continue
                found.append(f"L{node.lineno}: {ast.unparse(node)[:110]}")

        if found:
            hits[fname] = found

    return hits


def build_cohort(writers: Dict[str, List[str]], all_files: bool) -> List[str]:
    """决定这次要跑哪些测试文件。

    返回的是**相对仓库根的路径**(``tests/xxx.py``) —— pytest 必须拿到
    能定位到文件的路径, 只给文件名会静默地「0 tests ran」。
    """
    if all_files:
        names = sorted(
            f for f in os.listdir(_TESTS_DIR)
            if f.startswith("test_") and f.endswith(".py")
        )
    else:
        names = sorted(writers.keys())
    return [os.path.join("tests", n) for n in names]


# ====================================================================
# 2. 动态扰动: 跑 N 个 seed, 收集每个 testcase 的 outcome
# ====================================================================

def _junit_outcomes(xml_path: str) -> Dict[str, str]:
    """从 JUnit XML 里抽出 {testcase id: outcome}。

    outcome 归一化成 4 个值: passed / failed / skipped / error
    """
    if not os.path.exists(xml_path):
        return {}
    try:
        tree = ET.parse(xml_path)
    except ET.ParseError:
        return {}

    outcomes: Dict[str, str] = {}
    for case in tree.getroot().iter("testcase"):
        cid = case.get("classname", "") + "::" + case.get("name", "")
        if case.find("failure") is not None:
            outcomes[cid] = "failed"
        elif case.find("error") is not None:
            outcomes[cid] = "error"
        elif case.find("skipped") is not None:
            outcomes[cid] = "skipped"
        else:
            outcomes[cid] = "passed"
    return outcomes


def run_with_seed(files: List[str], seed: int, timeout: int) -> Tuple[Dict[str, str], str]:
    """用指定 seed 跑一遍, 返回 (outcomes, 摘要文本)。"""
    out_xml = tempfile.NamedTemporaryFile(
        prefix=f"order_gate_seed{seed}_", suffix=".xml", delete=False
    )
    out_xml.close()

    env = dict(os.environ)
    env["PYTEST_SHUFFLE_SEED"] = str(seed)
    # 让 pytest 能 import scripts/order_shuffle.py
    env["PYTHONPATH"] = os.pathsep.join(
        p for p in [os.path.join(_REPO_ROOT, "scripts"), env.get("PYTHONPATH", "")] if p
    )

    cmd = [
        sys.executable, "-m", "pytest",
        *files,
        "-p", "order_shuffle",
        "--junitxml", out_xml.name,
        "-q", "--tb=no", "-p", "no:cacheprovider",
        "--timeout=180", "--timeout-method=thread",
    ]

    try:
        proc = subprocess.run(
            cmd, cwd=_REPO_ROOT, env=env, timeout=timeout,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        summary = (proc.stdout or "").strip().splitlines()[-1] if proc.stdout else ""
        rc = proc.returncode
    except subprocess.TimeoutExpired:
        summary = f"TIMEOUT after {timeout}s"
        rc = -1
    finally:
        outcomes = _junit_outcomes(out_xml.name)
        try:
            os.unlink(out_xml.name)
        except OSError:
            pass

    outcomes["__exit_code__"] = str(rc)
    outcomes["__summary__"] = summary
    return outcomes, summary


# ====================================================================
# 3. 比对
# ====================================================================

def compare_runs(runs: Dict[int, Dict[str, str]]) -> Dict[str, List[str]]:
    """找出在不同 seed 下 outcome 不一致的 testcase。

    返回: {testcase id: [描述, ...]}
    """
    all_ids: Set[str] = set()
    for out in runs.values():
        all_ids.update(k for k in out if not k.startswith("__"))

    unstable: Dict[str, List[str]] = {}
    for cid in sorted(all_ids):
        seen: Dict[str, List[int]] = {}
        for seed, out in runs.items():
            outcome = out.get(cid)
            if outcome is None:
                continue
            seen.setdefault(outcome, []).append(seed)
        if len(seen) > 1:
            detail = " | ".join(
                f"seed {seeds} → {outcome}" for outcome, seeds in sorted(seen.items())
            )
            unstable[cid] = [detail]
    return unstable


def main() -> int:
    ap = argparse.ArgumentParser(description="测试顺序无关性门禁 (ADR-0017)")
    ap.add_argument("--seeds", default="101,202,303", help="逗号分隔的 seed 列表")
    ap.add_argument("--all-files", action="store_true", help="不圈嫌疑队列, 跑全仓")
    ap.add_argument(
        "--cohort", default=None,
        help="显式指定要跑的文件(逗号分隔, 相对仓库根)。"
             "用来做**门禁自证**: 注入一组故意顺序依赖的测试, 验证门禁真的会红。",
    )
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    ap.add_argument("--timeout", type=int, default=1200, help="单次 run 的超时秒数")
    args = ap.parse_args()

    seeds = [int(s) for s in args.seeds.split(",") if s.strip()]

    writers = scan_ambient_writers()
    cohort = build_cohort(writers, args.all_files)
    if args.cohort:
        # 显式覆盖: 自证用(注入故意的顺序依赖 -> 必须判红)
        cohort = [p.strip() for p in args.cohort.split(",") if p.strip()]
        writers = {}  # 显式队列下不适用 AST 统计

    report: Dict[str, object] = {
        "cohort": cohort,
        "cohort_size": len(cohort),
        "ambient_writers": {k: len(v) for k, v in writers.items()},
        "seeds": seeds,
        "runs": {},
        "unstable": {},
        "ok": True,
    }

    if not cohort:
        print("✅ 嫌疑队列为空 —— 没有测试文件直接写 ambient 全局单例")
        print("   (这本身是个好消息: 没有测试在直接污染进程级共享状态)")
        print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else "")
        return 0

    print("=" * 74)
    print("🔀 测试顺序无关性门禁 (ADR-0017)")
    print("=" * 74)
    print(f"嫌疑队列: {len(cohort)} 个测试文件 (写 ambient 全局单例)")
    for f in cohort:
        # cohort 里是相对路径(tests/xxx.py), writers 的键是纯文件名 → 取 basename
        n = len(writers.get(os.path.basename(f), []))
        print(f"   - {f}  ({n} 处写入)")
    print(f"扰动 seed: {seeds}")
    print()

    runs: Dict[int, Dict[str, str]] = {}
    for seed in seeds:
        print(f"▶ 跑 seed={seed} ...", flush=True)
        outcomes, summary = run_with_seed(cohort, seed, args.timeout)
        runs[seed] = outcomes
        n_cases = len([k for k in outcomes if not k.startswith("__")])
        report["runs"][str(seed)] = {
            "summary": summary,
            "exit_code": outcomes.get("__exit_code__"),
            "testcases": n_cases,
        }
        print(f"  → {summary}  ({n_cases} 个 testcase)")
        # Fail-closed: 一个 testcase 都没收集到 = 这轮扰动**什么都没验证**,
        # 必须当红处理。静默绿灯 = 门禁作废(这正是 iter #83 之前的老毛病)。
        if n_cases == 0:
            print(f"❌ seed={seed} 收集到 0 个 testcase —— 本轮扰动无效, 门禁判红")
            report["ok"] = False
            report["fatal"] = f"seed={seed} collected 0 testcases"
            if args.json:
                print(json.dumps(report, ensure_ascii=False, indent=2))
            return 1

    unstable = compare_runs(runs)
    report["unstable"] = unstable

    print()
    print("=" * 74)
    if unstable:
        report["ok"] = False
        print(f"❌ 发现 {len(unstable)} 个**顺序敏感**的测试 (结果随执行顺序翻转)")
        print("=" * 74)
        for cid, details in unstable.items():
            print(f"  🔴 {cid}")
            for d in details:
                print(f"       {d}")
        print()
        print("  含义: 这些测试的结果依赖「谁先跑」—— 它们不是可靠的证据。")
        print("  修法: 测试自己持有状态, 别共享 ambient 单例;")
        print("        或用 fixture 在 setup/teardown 里显式换掉并还原。")
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1

    print("✅ 全部 seed 下结果一致 —— 嫌疑队列的测试结果是**顺序无关**的")
    print("=" * 74)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
