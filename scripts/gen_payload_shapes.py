#!/usr/bin/env python3
"""
gen_payload_shapes.py — 把「响应体到底长什么样」冻成运行时快照 (iter #80)

为什么需要这个脚本
------------------
iter #79 (ADR-0011) 把 HTTP 契约冻到了「路径 / 参数 / 状态码 / response_model /
鉴权依赖」这一层。但 ADR-0011 自己写下的**已知边界**是：

    「136 个端点里绝大多数响应是 Dict[str, Any]，
      OpenAPI 里没有 response_model，所以响应体字段在 schema 层面根本看不见。」

这就是本脚本要补的那一块 —— **运行时响应体的结构形状**。

为什么 schema 看不见的东西真的会把客户打挂
--------------------------------------------
一个 ``Dict[str, Any]`` 的端点, schema 层面对「字段还在不在 / 类型变没变」是瞎的,
但下面这些全是真实发生过的客户侧事故:

1. **字段变 null** —— schema 说 ``integer``, 运行时某天起变成 ``None``,
   客户 JS 端 ``data.x.toFixed(0)`` 直接 TypeError。
2. **类型悄悄变了** —— ``total_cost_sek`` 从 float 变成 str(为了塞货币符号),
   客户的算术全错, 而且**不报错**(静默算错比报错更贵)。
3. **字段整个没了** —— 有人重构时把 ``co2_saved`` 删了,
   客户拿到的值变成 ``undefined``, 报表里那一列静默变成空白。
4. **多出字段** —— 通常无害(兼容), 但有时会**泄漏内部信息**
   (比如把 SQL / 绝对路径 / 内部 id 带出来)。

这四类, 全部**不在** OpenAPI 契约里, 全部**会**伤到客户。

怎么冻
------
1. 用**确定性 fixture DB** 起服务(复用 scripts/seed_ci_fixture.py, 14 天合成 cycle),
   保证每次跑出来的数据一模一样 —— 快照必须可复现, 否则门禁会变成随机噪音。
2. 真实调一遍端点(TestClient), 把响应 JSON 递归压成「结构形状」:
   字段名 + 类型集合(不做值比较 —— 值会变, 结构才是契约)。
3. 快照写进 ``tests/http_api_payload_shapes.json``, 交给
   ``tests/test_payload_shapes.py`` 每次 CI 比对。

分层断言(和 ADR-0010 / ADR-0011 一脉相承)
-----------------------------------------
- **破坏性变更 → 红**:
  - 端点没了 / 状态码不再是 200
  - **字段被删** —— 客户拿不到值了
  - **类型集合被加宽** —— 原来是 integer, 现在可能是 null 了(经典事故 #1)
  - **类型集合被换掉**(既不是加宽也不是收窄, 例如 integer → string)
- **兼容性变更 → 黄(只打印, 不让 CI 变红)**:
  - 新增字段 / 新增可选参数 / 状态码新增
  - **类型集合被收窄**(原来可能是 null, 现在固定有值) —— 收窄是安全的
- 理由和 ADR-0011 一模一样: **门禁一旦「什么变化都报红」, 团队就会开始无脑刷基线
  —— 那它就废了。**

「unknown」的特殊处理
--------------------
空数组的 item 形状是 ``unknown``(没有样本可观测)。
unknown ↔ 具体类型之间的变化一律算**黄**: 数据出现了不是破坏,
但**必须让人知道**(这往往意味着「这个端点以前一直返回空, 现在开始有数据了」)。

用法
----
    # 看当前运行时形状与仓库基线的差异 (CI 用的就是这条)
    python scripts/gen_payload_shapes.py

    # 刷新基线 (只有在你**故意**改了响应体结构时才做)
    python scripts/gen_payload_shapes.py --write

    # 打印当前快照 (调试用, stdout 是纯 JSON)
    python scripts/gen_payload_shapes.py --json
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

BASELINE_PATH = _REPO_ROOT / "tests" / "http_api_payload_shapes.json"

SCHEMA_VERSION = 1
MAX_DEPTH = 3          # 嵌套超过 3 层就只记类型, 不再展开字段
FIXTURE_DAYS = 14      # 合成历史 cycle 天数 (与 seed_ci_fixture 默认一致)
ADMIN_TOKEN = "payload-shape-snapshot-token"

# ---------------------------------------------------------------- 被冻结的端点
# 选取原则:
#   1. 只读 GET —— 快照过程不能有副作用
#   2. 不打外网(天气 / 外部信号)、不调 LLM —— 否则门禁会随机红
#   3. 覆盖客户真正会调的三类面:
#      概览面(/api/dashboard-*) · 持久化分析面(/api/persistence/*) · 运维面(/api/admin/*)
# 每个条目: (method, path, query_params)
ENDPOINTS: List[Tuple[str, str, Dict[str, Any]]] = [
    ("GET", "/health", {}),
    ("GET", "/api/status", {}),
    ("GET", "/api/dashboard-summary", {}),
    ("GET", "/api/dashboard-top-summary", {}),
    ("GET", "/api/fleet", {}),
    ("GET", "/api/supply-points", {}),
    ("GET", "/api/facilities", {}),
    ("GET", "/api/materials", {}),
    ("GET", "/api/regions", {}),
    ("GET", "/api/seasonal-factors", {}),
    ("GET", "/api/scheduler/status", {}),
    ("GET", "/api/persistence/summary", {}),
    ("GET", "/api/persistence/recent-cycles", {}),
    ("GET", "/api/persistence/cycle-history", {}),
    ("GET", "/api/persistence/forecast", {}),
    ("GET", "/api/persistence/efficiency-metrics", {}),
    ("GET", "/api/persistence/cycle-duration-stats", {}),
    ("GET", "/api/persistence/fleet-utilization-summary", {}),
    ("GET", "/api/persistence/kpi-timeseries", {}),
    ("GET", "/api/persistence/top-suppliers", {}),
    ("GET", "/api/persistence/top-vehicles", {}),
    ("GET", "/api/persistence/carbon-savings", {}),
    ("GET", "/api/persistence/demand-aggregates", {}),
    ("GET", "/api/persistence/material-aggregates", {}),
    ("GET", "/api/persistence/anomaly-summary", {}),
    ("GET", "/api/persistence/anomalous-cycles", {}),
    ("GET", "/api/persistence/cycle-kpi-summary", {}),
    ("GET", "/api/persistence/llm-cost-timeseries", {}),
    ("GET", "/api/persistence/llm-decision-targets", {}),
    ("GET", "/api/persistence/forecast-confidence", {}),
    ("GET", "/api/persistence/perturbation-impact", {}),
    ("GET", "/api/persistence/match-distance-stats", {}),
    ("GET", "/api/persistence/cycle-detail/ci-fixture-cycle-001", {}),
    ("GET", "/api/persistence/compare-cycles", {"cycle_id_a": "ci-fixture-cycle-001", "cycle_id_b": "ci-fixture-cycle-002"}),
    # 运维 / 客户诊断面 —— 都需要 admin token
    ("GET", "/api/admin/auth/status", {}),
    ("GET", "/api/admin/db-stats", {}),
    ("GET", "/api/admin/db-info", {}),
    ("GET", "/api/admin/runtime-config", {}),
    ("GET", "/api/admin/runtime-config/overrides", {}),
    ("GET", "/api/admin/llm-stats", {}),
    ("GET", "/api/admin/perf-stats", {}),
    ("GET", "/api/admin/persistence/cache", {}),
    ("GET", "/api/admin/db-maintenance/recommendation", {}),
    ("GET", "/api/admin/db-maintenance/log", {}),
    ("GET", "/api/admin/seasonal-perturbations", {}),
]


# ---------------------------------------------------------------- 形状计算

def _node(types: List[str], fields: Optional[Dict[str, Any]] = None,
          items: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """构造一个形状节点。字段顺序固定, 保证输出可复现。"""
    node: Dict[str, Any] = {"types": sorted(set(types))}
    if fields:
        node["fields"] = {k: fields[k] for k in sorted(fields)}
    if items is not None:
        node["items"] = items
    return node


def _shape_scalar(value: Any) -> Optional[Dict[str, Any]]:
    """标量 → 形状节点；非标量返回 None。

    注意 bool 必须先于 int 判断 —— bool 是 int 的子类，
    顺序错了 True 会被记成 integer(客户端 ``=== true`` 的判断就废了)。
    """
    if value is None:
        return _node(["null"])
    if isinstance(value, bool):
        return _node(["boolean"])
    if isinstance(value, int):
        return _node(["integer"])
    if isinstance(value, float):
        return _node(["number"])
    if isinstance(value, str):
        return _node(["string"])
    return None


def _shape_object(value: Dict[str, Any], depth: int) -> Dict[str, Any]:
    """对象 → 形状节点(字段递归)。"""
    if depth >= MAX_DEPTH:
        return _node(["object"])
    fields = {k: shape_of(v, depth + 1) for k, v in value.items()}
    return _node(["object"], fields=fields)


def _shape_array(value: Any, depth: int) -> Dict[str, Any]:
    """数组 → 形状节点(item 形状合并)。"""
    if depth >= MAX_DEPTH:
        return _node(["array"])
    merged: Optional[Dict[str, Any]] = None
    for item in value:
        current = shape_of(item, depth + 1)
        merged = current if merged is None else merge_shapes(merged, current)
    return _node(["array"], items=merged or _node(["unknown"]))


def shape_of(value: Any, depth: int = 0) -> Dict[str, Any]:
    """把一个 JSON 值压成结构形状。

    形状 = 类型集合(可并集) + 对象字段(递归) + 数组 item 形状(合并)。
    **不比较值** —— 值每天都在变, 结构才是客户依赖的契约。
    """
    scalar = _shape_scalar(value)
    if scalar is not None:
        return scalar
    if isinstance(value, dict):
        return _shape_object(value, depth)
    if isinstance(value, (list, tuple)):
        return _shape_array(value, depth)
    return _node(["other"])


def merge_shapes(left: Dict[str, Any], right: Dict[str, Any]) -> Dict[str, Any]:
    """合并两个形状节点(数组里多个 item 的形状并成一个)。

    合并规则: 类型集合取并集; 对象字段递归合并; 数组 item 递归合并。
    → 于是「有的 item 有这个字段、有的没有」会表现为**并集**, 而不是被忽略。
    """
    merged = _node(list(left["types"]) + list(right["types"]))
    left_fields = left.get("fields") or {}
    right_fields = right.get("fields") or {}
    if left_fields or right_fields:
        fields: Dict[str, Any] = dict(left_fields)
        for key, shape in right_fields.items():
            if key in fields:
                fields[key] = merge_shapes(fields[key], shape)
            else:
                fields[key] = shape
        merged["fields"] = {k: fields[k] for k in sorted(fields)}
    if "items" in left and "items" in right:
        merged["items"] = merge_shapes(left["items"], right["items"])
    elif "items" in left:
        merged["items"] = left["items"]
    elif "items" in right:
        merged["items"] = right["items"]
    return merged


# ---------------------------------------------------------------- 差异比对

def _diff_node(path: str, before: Dict[str, Any], after: Dict[str, Any],
               breaking: List[str], compatible: List[str]) -> None:
    """比对两个形状节点, 就地往 breaking / compatible 里塞结论。"""
    before_types = set(before.get("types") or [])
    after_types = set(after.get("types") or [])

    if "unknown" in before_types or "unknown" in after_types:
        # 空数组没有样本可观测 —— 与 unknown 相关的变化一律算「提示」
        if before_types != after_types:
            compatible.append(
                f"{path}: 类型集合 {sorted(before_types)} → {sorted(after_types)} (含 unknown, 仅提示)"
            )
        return

    added = after_types - before_types
    removed = before_types - after_types

    if added and removed:
        # 类型被换掉(既不是加宽也不是收窄) —— 客户的类型假设直接失效
        breaking.append(
            f"{path}: 类型被替换 {sorted(before_types)} → {sorted(after_types)}"
        )
    elif added:
        # 类型被加宽 —— 原来一定有值, 现在可能是 null/别的类型了
        breaking.append(
            f"{path}: 类型被加宽 {sorted(before_types)} → {sorted(after_types)} "
            f"(新增 {sorted(added)}) —— 客户原来的类型假设可能失效"
        )
    elif removed:
        # 类型被收窄 —— 原来是「integer 或 null」, 现在固定是 integer, 对客户是安全方向
        compatible.append(
            f"{path}: 类型被收窄 {sorted(before_types)} → {sorted(after_types)}"
        )

    before_fields = before.get("fields") or {}
    after_fields = after.get("fields") or {}
    for key in sorted(set(before_fields) - set(after_fields)):
        breaking.append(f"{path}.{key}: 字段被删 —— 客户拿不到这个值了")
    for key in sorted(set(after_fields) - set(before_fields)):
        compatible.append(f"{path}.{key}: 新增字段 (兼容)")
    for key in sorted(set(before_fields) & set(after_fields)):
        _diff_node(f"{path}.{key}", before_fields[key], after_fields[key], breaking, compatible)

    before_items = before.get("items")
    after_items = after.get("items")
    if before_items and after_items:
        _diff_node(f"{path}[]", before_items, after_items, breaking, compatible)


def diff_shapes(baseline: Dict[str, Any], current: Dict[str, Any]
                ) -> Tuple[List[str], List[str]]:
    """比对两份快照 → (破坏性变更列表, 兼容性变更列表)。"""
    breaking: List[str] = []
    compatible: List[str] = []
    before_eps = baseline.get("endpoints") or {}
    after_eps = current.get("endpoints") or {}

    for key in sorted(set(before_eps) - set(after_eps)):
        breaking.append(f"{key}: 端点不在快照里了 (被删 / 改名 / 不再被采集)")

    for key in sorted(set(after_eps) - set(before_eps)):
        compatible.append(f"{key}: 新增端点 (兼容)")

    for key in sorted(set(before_eps) & set(after_eps)):
        before_ep = before_eps[key]
        after_ep = after_eps[key]
        if before_ep.get("status") != after_ep.get("status"):
            breaking.append(
                f"{key}: 状态码 {before_ep.get('status')} → {after_ep.get('status')}"
            )
        _diff_node(key, before_ep.get("shape") or {}, after_ep.get("shape") or {},
                   breaking, compatible)

    return breaking, compatible


# ---------------------------------------------------------------- 运行时采集

def _seed_fixture(db_path: Path) -> None:
    """用确定性合成数据铺一份历史 cycle(不碰开发者本地 DB)。"""
    sys.path.insert(0, str(_REPO_ROOT / "scripts"))
    from seed_ci_fixture import seed

    # seed() 会往 stdout 打印一行进度 —— 这里挪到 stderr, 保证 --json 的 stdout 是纯 JSON
    with contextlib.redirect_stdout(sys.stderr):
        seed(db_path, FIXTURE_DAYS, force=True)


def collect_shapes() -> Dict[str, Any]:
    """真起一次服务, 把每个端点的响应压成形状。"""
    tmp_dir = Path(tempfile.mkdtemp(prefix="payload-shapes-"))
    db_path = tmp_dir / "snapshot.db"
    try:
        _seed_fixture(db_path)
        os.environ["GL_DB_PATH"] = str(db_path)
        os.environ["GL_ADMIN_TOKEN"] = ADMIN_TOKEN

        from fastapi.testclient import TestClient
        from web.backend import main as backend_main

        endpoints: Dict[str, Any] = {}
        with TestClient(backend_main.app) as client:
            for method, path, params in ENDPOINTS:
                key = f"{method} {path}"
                # admin 端点需要 token; 非 admin 端点带这个 header 也无害
                response = client.request(
                    method, path, params=params, headers={"X-Admin-Token": ADMIN_TOKEN}
                )
                if response.status_code != 200:
                    endpoints[key] = {
                        "status": response.status_code,
                        "shape": _node(["non-json"]),
                    }
                    continue
                try:
                    payload = response.json()
                except ValueError:
                    endpoints[key] = {
                        "status": response.status_code,
                        "shape": _node(["non-json"]),
                    }
                    continue
                endpoints[key] = {
                    "status": response.status_code,
                    "shape": shape_of(payload),
                }
        return {
            "generated_by": "scripts/gen_payload_shapes.py",
            "schema_version": SCHEMA_VERSION,
            "max_depth": MAX_DEPTH,
            "fixture": {"days": FIXTURE_DAYS, "admin_token_configured": True},
            "endpoints": endpoints,
        }
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def load_baseline() -> Dict[str, Any]:
    with open(BASELINE_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def write_baseline(snapshot: Dict[str, Any]) -> None:
    BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(BASELINE_PATH, "w", encoding="utf-8") as fh:
        json.dump(snapshot, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")


def _print_report(breaking: List[str], compatible: List[str]) -> None:
    if compatible:
        print("[兼容变更（仅提示，不阻断 CI）]")
        for line in compatible:
            print(f"  {line}")
    if breaking:
        print("[破坏性变更（真实的接口破坏）]")
        for line in breaking:
            print(f"  {line}")
    else:
        print("OK: 运行时响应体形状与基线一致")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="把 HTTP 响应体的运行时结构冻结成 CI 常驻不变量")
    parser.add_argument("--write", action="store_true", help="刷新基线快照")
    parser.add_argument("--json", action="store_true", help="把当前快照打到 stdout (纯 JSON)")
    args = parser.parse_args()

    current = collect_shapes()

    if args.json:
        print(json.dumps(current, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    if args.write:
        write_baseline(current)
        print(f"✅ 基线已刷新: {BASELINE_PATH.relative_to(_REPO_ROOT)} "
              f"({len(current['endpoints'])} 个端点)")
        return 0

    breaking, compatible = diff_shapes(load_baseline(), current)
    _print_report(breaking, compatible)
    if breaking:
        print("\n如果这是**故意**的变更，请跑：")
        print("    python scripts/gen_payload_shapes.py --write")
        print("并在 PR 描述里说明：哪些端点的响应变了 / 客户端怎么改。")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
