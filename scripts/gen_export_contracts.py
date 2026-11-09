#!/usr/bin/env python3
"""
gen_export_contracts.py — 把「导出文件的列契约」冻成 CI 常驻不变量 (iter #82)

为什么需要这个脚本
------------------
iter #81 (ADR-0013) 把端点清单改成**自动发现** + **显式豁免登记册**,
把 14 个导出端点(CSV / Parquet / NDJSON)登记成了 ``NON_JSON_RESPONSE`` 豁免。

那个豁免的 note 里写着:

    「CSV 全文导出; 字段契约是「列名」, 不是 JSON 字段形状。」

**这句话是对的, 但它同时也是一张欠条。**
「字段契约是列名」= 列名就是契约 = 列名必须有保护。
而 ADR-0013 交付之后, 那 14 个端点的列名**依然零保护** ——
ADR-0012 的响应体形状快照根本不看它们(它们不是 JSON),
ADR-0011 的 HTTP 契约只看路径/参数/状态码(不看响应体),
ADR-0010 的公开 API 契约只看 Python 方法签名(不看返回值内容)。

→ **三层契约都盖不住「导出文件里到底有哪些列」这件事。**

客户视角的三种真实事故
----------------------
1. **列被删了** —— 客户的下游 ETL 报「找不到该列」, 或者更糟:
   ``pd.read_csv()`` 之后 ``df['total_tons']`` 直接 KeyError。
2. **列被改名了** —— 同上, 但更难查(客户端以为是自己写错了)。
3. **列顺序变了 / 列类型变了** —— 最阴的一类:
   - CSV 客户如果用**位置**读(``awk -F, '{print $5}'``), 中间插一列 →
     位置整体右移 → **静默读到别的列** → 报表数字错了但没人报错
   - Parquet 客户如果用 ``df['x'].sum()``, 类型从 double 变成 string →
     **静默算错**(pandas 把字符串拼起来, 而不是报错)

第 3 类正是 ADR-0012 立下的那条原则的延伸:
> 「静默算错比报错更贵」

怎么冻 —— 两层, 缺一不可
-----------------------
本脚本同时做两件事, 因为它们能抓到**完全不同**的 bug:

**第一层: 每端点 vs 基线(冻结)**
    跟 ADR-0012 一模一样的思路: 真起服务、真调端点, 把列契约抽出来,
    写进 ``tests/export_column_contracts.json``, 每次 CI 比对。
    抓的是「**相对上一次**变了什么」。

**第二层: 同表跨格式一致性(推导, 不需要基线)**
    同一张表导出成 CSV / Parquet / NDJSON / JSON, **列集合必须完全一样**。
    这是**结构不变量**, 不是基线 —— 所以它在一份全新的 checkout 上也成立。
    抓的是「**同一个表在不同格式下长得不一样**」。

    只冻基线是不够的: 如果基线本身就是漂移的(19 列的 CSV vs 20 列的 Parquet
    被一起冻进去), 门禁会一直绿 —— **绿得没有意义**。
    跨格式一致性检查不需要任何基线, 它永远是真的。

分层断言(和 ADR-0010 / 0011 / 0012 / 0013 一脉相承)
-----------------------------------------------------
- **破坏性变更 → 红**:
  - 端点没了
  - **列被删** —— 客户读不到这一列了
  - **列被改名** —— 客户的 ``df['old_name']`` 直接炸
  - **CSV / Parquet 列顺序变了** —— 位置型读取器静默读错列
  - **有序格式中间插了一列** —— 后面所有列的位置都右移了
  - **Parquet 列类型变了** —— 客户端的算术静默失效
  - **同表跨格式列集不一致** —— 客户换个格式下载就少列/多列
- **兼容性变更 → 黄(只打印, 不让 CI 变红)**:
  - **新增列**(追加在末尾) —— 不影响已有列的位置
  - 新增端点
- 理由和 ADR-0011 一模一样: **门禁一旦「什么变化都报红」, 团队就会开始无脑刷基线
  —— 那它就废了。**

三种格式的列契约是**不同的东西** —— 所以判据也必须不同
---------------------------------------------------------
| 格式 | 契约是什么 | 顺序重要吗 | 类型重要吗 |
|---|---|---|---|
| CSV | 列名 + 顺序 | **重要**(位置型读取器) | 否(全是字符串) |
| Parquet | 列名 + 顺序 + **类型** | 重要 | **重要**(schema 是自描述的) |
| NDJSON | 键集合 | 否(对象无序) | 弱(JSON 有类型但宽松) |
| JSON | 键集合 | 否 | 弱 |

这不是「同一个检查套四种格式」, 而是**每种格式冻它自己的那部分契约**。

用法
----
    # 看当前列契约与仓库基线的差异 (CI 用的就是这条)
    python scripts/gen_export_contracts.py

    # 刷新基线 (只有在你**故意**改了导出列时才做)
    python scripts/gen_export_contracts.py --write

    # 打印当前快照 (调试用, stdout 是纯 JSON)
    python scripts/gen_export_contracts.py --json
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import io
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

BASELINE_PATH = _REPO_ROOT / "tests" / "export_column_contracts.json"

SCHEMA_VERSION = 1
FIXTURE_DAYS = 14      # 合成历史 cycle 天数 (与 seed_ci_fixture 默认一致)
ADMIN_TOKEN = "export-contract-token"

# 导出端点的「格式」是从路径后缀推出来的 —— 不手写清单。
#
# iter #81 的教训: 手写清单是 fail-open 的。这里反过来:
# **从路由表自动发现**所有「路径以 .csv/.parquet/.ndjson/.json 结尾」的 GET 端点,
# 再按后缀推断格式。新加一个导出端点 → 自动进契约 → 没冻进基线 → 红灯。
EXPORT_SUFFIXES = {
    ".csv": "csv",
    ".parquet": "parquet",
    ".ndjson": "ndjson",
    ".json": "json",
}

# 带 path 参数的导出端点需要绑定值(跟 endpoint_policy.PARAM_BINDINGS 一个思路,
# 但这里只管导出端点)。
PARAM_BINDINGS: Dict[str, Dict[str, Any]] = {
    "/api/persistence/export/cycle-detail/{cycle_id}.csv": {
        "path": {"cycle_id": "ci-fixture-cycle-001"},
    },
}

# 有序格式: 列的位置是契约的一部分(位置型读取器依赖它)
ORDERED_FORMATS = {"csv", "parquet"}

# ---------------------------------------------------------------- db-export 矩阵
#
# ``/api/admin/db-export?table=X&fmt=Y`` 是**参数驱动**的导出端点 ——
# 它的契约取决于 (table, fmt) 两个参数, 而不是路径后缀。
#
# 所以它不能靠「路径后缀自动发现」, 只能显式枚举。但注意:
# **枚举的是端点自带的封闭列表**(下面的 TABLES / FORMATS 跟端点自己校验的
# 那两份完全一致), 不是「我记得有这些」的手写清单 —— 所以新增一个 table
# 必须同时改端点和这里, 漏了就会在这里显式地不一致(而不是悄悄漏掉)。
#
# iter #82: 把它纳入契约, 因为它才是客户**真正**用的那个「统一导出」入口 ——
# 从这儿拿到的列契约, 跟从 /api/persistence/export/* 拿到的必须一样。
DB_EXPORT_PATH = "/api/admin/db-export"
DB_EXPORT_TABLES = ("cycles", "supplies", "matches", "routes", "llm_decisions")
DB_EXPORT_FORMATS = ("csv", "json", "ndjson", "parquet")


# ---------------------------------------------------------------- 发现

def discover_export_routes(app: Any) -> List[Dict[str, str]]:
    """从路由表自动发现所有导出端点 → [{"path":..., "format":...}]。

    判据: GET + 路径以已知导出后缀结尾。
    **不手写清单** —— 手写清单就是 ADR-0013 推翻的那个 fail-open 设计。
    """
    from fastapi.routing import APIRoute

    found: List[Dict[str, str]] = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        methods = {m for m in (route.methods or set()) if m not in ("HEAD", "OPTIONS")}
        if methods != {"GET"}:
            continue
        for suffix, fmt in EXPORT_SUFFIXES.items():
            if route.path.endswith(suffix):
                found.append({"path": route.path, "format": fmt})
                break
    return sorted(found, key=lambda r: r["path"])


def table_of(path: str) -> str:
    """从导出路径推出「逻辑表名」(用于跨格式一致性分组)。

    ``/api/persistence/export/cycles.csv`` → ``cycles``
    ``/api/persistence/export/cycle-detail/{cycle_id}.csv`` → ``cycle-detail``
    """
    tail = path.rsplit("/", 1)[-1]
    for suffix in EXPORT_SUFFIXES:
        if tail.endswith(suffix):
            return tail[: -len(suffix)]
    return tail


def db_export_key(table: str, fmt: str) -> str:
    """db-export 的契约 key —— 把 (table, fmt) 编进 key 里。

    必须编进去: 同一个端点不同参数下的列契约**完全不同**
    (cycles 的 csv 有 20 列, llm_decisions 的 csv 只有 placeholder 的 2 列),
    不编进 key 就分不开了。
    """
    return f"GET {DB_EXPORT_PATH}?table={table}&fmt={fmt}"


def build_request(path: str) -> Tuple[str, Dict[str, Any]]:
    """路由模板 → (真实路径, query 参数)。"""
    binding = PARAM_BINDINGS.get(path, {})
    real_path = path
    for name, value in (binding.get("path") or {}).items():
        real_path = real_path.replace("{" + name + "}", str(value))
    return real_path, dict(binding.get("query") or {})


# ---------------------------------------------------------------- 解析

def parse_csv_contract(text: str) -> Dict[str, Any]:
    """CSV 响应 → 列契约。

    两种形态:
    1. **单表 CSV**(绝大多数): 表头一行 → ``{"columns": [...]}``
    2. **多段 CSV**(cycle-detail): ``# section: NAME`` 分隔, 每段各有表头
       → ``{"sections": {name: [...]}}``

    跳过 ``#`` 开头的 metadata 行(iter #19 加的 ``# generated_at:`` 那些)。
    注意: **列顺序本身就是契约的一部分** —— 位置型读取器依赖它。
    """
    lines = text.split("\n")
    sections: Dict[str, List[str]] = {}
    current_section: Optional[str] = None
    plain_columns: List[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            marker = stripped[1:].strip()
            if marker.startswith("section:"):
                current_section = marker.split(":", 1)[1].strip()
                sections.setdefault(current_section, [])
            continue
        row = next(csv.reader(io.StringIO(line)), [])
        row = [c.strip() for c in row]
        if current_section is not None:
            if not sections[current_section]:
                sections[current_section] = row
        elif not plain_columns:
            plain_columns = row

    if sections:
        return {"sections": sections}
    return {"columns": plain_columns}


def normalize_parquet_type(arrow_type: str) -> str:
    """pyarrow 类型 → **稳定的类型家族**。

    为什么不让基线存原始 pyarrow 类型串: 同样的语义在 pandas 2.x 下是
    ``string``、在 pandas 3.x 下是 ``large_string``。存原始串会让门禁
    在「CI 装的 pandas 版本」和「本地装的」之间假红 —— 而假红一次,
    团队就会开始无脑刷基线, 门禁就废了。

    类型家族保留了真正会伤到客户的那部分区别:
    string / integer / number / boolean —— 「数字变字符串」照样能抓到。
    """
    t = arrow_type.lower()
    if "bool" in t:
        return "boolean"
    if "int" in t:
        return "integer"
    if "float" in t or "double" in t or "decimal" in t:
        return "number"
    if "string" in t:
        return "string"
    if "timestamp" in t or "date" in t:
        return "datetime"
    return "other"


def parse_parquet_contract(content: bytes) -> Dict[str, Any]:
    """Parquet 响应 → ``{"columns": [{"name":..., "type":...}]}``。

    **类型也是契约**: schema 是自描述的, 客户 ``pd.read_parquet()`` 之后
    ``df['x'].sum()`` 的行为完全由列类型决定。类型从 number 变 string,
    客户的算术就静默失效了。
    """
    import pyarrow.parquet as pq

    schema = pq.read_schema(io.BytesIO(content))
    return {
        "columns": [
            {"name": f.name, "type": normalize_parquet_type(str(f.type))}
            for f in schema
        ]
    }


def parse_json_keys(payload: Any) -> List[str]:
    """JSON 响应 → 顶层键集合(排序)。

    JSON 对象是**无序**的, 所以这里不关心顺序, 只关心「有哪些键」。
    列表 → 取所有元素的键并集(不能只看第一个: 元素结构可能不一致)。
    """
    keys: List[str] = []
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                for k in item:
                    if k not in keys:
                        keys.append(k)
    elif isinstance(payload, dict):
        keys = list(payload.keys())
    return sorted(keys)


def parse_ndjson_keys(text: str) -> List[str]:
    """NDJSON 响应 → 所有行的键并集(排序)。

    NDJSON 每行是一个独立 JSON 对象 —— 行的顺序无关, 但**每行的键集合**有关。
    取并集而不是第一行: 免得某一行少一个键就被漏掉。
    """
    keys: List[str] = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict):
            for k in obj:
                if k not in keys:
                    keys.append(k)
    return sorted(keys)


# ---------------------------------------------------------------- 差异比对

def _diff_name_list(path: str, before: List[str], after: List[str],
                    ordered: bool, breaking: List[str], compatible: List[str]) -> None:
    """比一份「名字列表」(CSV/Parquet 的列, JSON/NDJSON 的键)。

    ``ordered=True`` 时顺序也是契约 —— 顺序变了 / 中间插了列,
    位置型读取器会静默读到别的列, 所以算破坏性。
    """
    before_set, after_set = set(before), set(after)

    for name in sorted(before_set - after_set):
        breaking.append(f"{path}: 列/键 `{name}` 被删 —— 客户读不到它了")
    for name in sorted(after_set - before_set):
        compatible.append(f"{path}: 新增列/键 `{name}` (兼容)")

    if not ordered:
        return

    # 位置型读取器(``awk -F, '{print $3}'`` / ``df.iloc[:, 2]``)依赖的是
    # **绝对下标** —— 所以判据是「**共有列的绝对位置有没有变**」,
    # 而不是「共有列的相对顺序有没有变」(后者在中间插一列时是不变的)。
    #
    #   ["a","b"]     → ["a","b","c"]   末尾追加   → 下标不变 → 兼容
    #   ["a","c"]     → ["a","b","c"]   中间插入   → c 从 1 移到 2 → 破坏
    #   ["a","b","c"] → ["c","a","b"]   整体重排   → 全部变了 → 破坏
    before_index = {name: i for i, name in enumerate(before)}
    after_index = {name: i for i, name in enumerate(after)}
    shifted = [
        name for name in sorted(set(before_index) & set(after_index))
        if before_index[name] != after_index[name]
    ]
    if not shifted:
        return

    detail = ", ".join(
        f"{name} {before_index[name]}→{after_index[name]}" for name in shifted
    )
    if before_set == after_set:
        breaking.append(
            f"{path}: 列**顺序**变了 {before} → {after} —— "
            f"按位置读的客户端会静默读到别的列 (位置变化: {detail})"
        )
    else:
        breaking.append(
            f"{path}: 有序格式里**中间插了一列**, 后面所有列的位置都右移了 "
            f"(位置变化: {detail}) —— 按位置读的客户端会静默读错列"
        )


def diff_endpoint_contract(path: str, before: Dict[str, Any], after: Dict[str, Any]
                           ) -> Tuple[List[str], List[str]]:
    """比对单个导出端点的列契约 → (破坏性变更, 兼容性变更)。"""
    breaking: List[str] = []
    compatible: List[str] = []
    fmt = after.get("format") or before.get("format") or "?"

    if before.get("status") != after.get("status"):
        breaking.append(f"{path}: 状态码 {before.get('status')} → {after.get('status')}")

    if fmt == "parquet":
        # Parquet: 列名 + 顺序 + 类型 全是契约
        before_cols = [c["name"] for c in before.get("columns") or []]
        after_cols = [c["name"] for c in after.get("columns") or []]
        _diff_name_list(path, before_cols, after_cols, True, breaking, compatible)

        before_types = {c["name"]: c["type"] for c in before.get("columns") or []}
        after_types = {c["name"]: c["type"] for c in after.get("columns") or []}
        for name in sorted(set(before_types) & set(after_types)):
            if before_types[name] != after_types[name]:
                breaking.append(
                    f"{path}.{name}: 列**类型**变了 "
                    f"{before_types[name]} → {after_types[name]} —— "
                    f"客户端的算术会静默失效(比报错更贵)"
                )
    elif fmt == "csv":
        # CSV: 列名 + 顺序 是契约, 类型不是(全是字符串)
        before_sections = before.get("sections")
        after_sections = after.get("sections")
        if before_sections is not None or after_sections is not None:
            # 多段 CSV: 每段各冻各的
            before_sections = before_sections or {}
            after_sections = after_sections or {}
            for name in sorted(set(before_sections) - set(after_sections)):
                breaking.append(f"{path}[{name}]: 整个 section 没了")
            for name in sorted(set(after_sections) - set(before_sections)):
                compatible.append(f"{path}[{name}]: 新增 section (兼容)")
            for name in sorted(set(before_sections) & set(after_sections)):
                _diff_name_list(
                    f"{path}[{name}]", before_sections[name], after_sections[name],
                    True, breaking, compatible,
                )
        else:
            _diff_name_list(
                path, before.get("columns") or [], after.get("columns") or [],
                True, breaking, compatible,
            )
    else:
        # JSON / NDJSON: 键集合是契约, 顺序不是
        _diff_name_list(
            path, before.get("keys") or [], after.get("keys") or [],
            False, breaking, compatible,
        )

    return breaking, compatible


def diff_contracts(baseline: Dict[str, Any],
                   current: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    """比对两份导出列契约快照 → (破坏性变更, 兼容性变更)。"""
    breaking: List[str] = []
    compatible: List[str] = []

    before_eps = baseline.get("endpoints") or {}
    after_eps = current.get("endpoints") or {}

    for key in sorted(set(before_eps) - set(after_eps)):
        breaking.append(f"{key}: 导出端点不在契约里了 (被删 / 改名)")

    for key in sorted(set(after_eps) - set(before_eps)):
        compatible.append(f"{key}: 新增导出端点 (兼容)")

    for key in sorted(set(before_eps) & set(after_eps)):
        ep_breaking, ep_compatible = diff_endpoint_contract(
            key, before_eps[key], after_eps[key]
        )
        breaking.extend(ep_breaking)
        compatible.extend(ep_compatible)

    return breaking, compatible


# ---------------------------------------------------------------- 跨格式一致性

def columns_of(contract: Dict[str, Any]) -> List[str]:
    """一个端点的列名集合(不管它是什么格式)。"""
    if contract.get("columns") and isinstance(contract["columns"][0], dict):
        return [c["name"] for c in contract["columns"]]
    if contract.get("columns"):
        return list(contract["columns"])
    if contract.get("keys"):
        return list(contract["keys"])
    if contract.get("sections"):
        # 多段 CSV: 各段的列并起来(分段契约本身由基线层管)
        merged: List[str] = []
        for cols in contract["sections"].values():
            for c in cols:
                if c not in merged:
                    merged.append(c)
        return merged
    return []


def check_cross_format_consistency(snapshot: Dict[str, Any]) -> List[str]:
    """同一张表, 不同格式的列集合必须一致 → (破坏性变更列表)。

    **这一层不需要基线** —— 它是结构不变量, 所以在一份全新的 checkout 上也成立。

    为什么必须有它: 只冻基线是不够的 —— 如果基线本身就是漂移的
    (19 列的 CSV vs 20 列的 Parquet 被一起冻进去), 门禁会一直绿,
    **绿得没有意义**。跨格式一致性检查不需要任何基线, 它永远是真的。

    分组用的是**逻辑表名**, 所以它同时覆盖两种来源:
    - ``/api/persistence/export/<table>.<fmt>``
    - ``/api/admin/db-export?table=<table>&fmt=<fmt>``

    → 于是「客户从统一导出入口拿到的表」和「从专用导出入口拿到的表」
    也会被拿来互相比 —— 这正是 iter #82 修掉的两个真 bug 藏身的地方。
    """
    problems: List[str] = []
    endpoints = snapshot.get("endpoints") or {}

    groups: Dict[str, List[str]] = {}
    for key, contract in endpoints.items():
        table = contract.get("table")
        if not table:
            continue
        groups.setdefault(table, []).append(key)

    for table in sorted(groups):
        problems.extend(_compare_table_formats(table, groups[table], endpoints))

    return problems


def _compare_table_formats(table: str, members: List[str],
                           endpoints: Dict[str, Any]) -> List[str]:
    """同一张表在不同格式下的列集合一致性 → 问题列表。

    基准取「列最多的那个」: 少的那几个就是漂移的。
    """
    # 只有一种格式 → 没有可比对象(比如 perturbed-supplies 只有 CSV)
    formats = {(endpoints[k].get("format") or "?") for k in members}
    if len(formats) < 2:
        return []

    sets = {key: set(columns_of(endpoints[key])) for key in members}
    reference_key = max(sets, key=lambda k: len(sets[k]))
    reference = sets[reference_key]
    if not reference:
        return []

    problems: List[str] = []
    for key in sorted(sets):
        if key == reference_key or sets[key] == reference:
            continue
        missing = sorted(reference - sets[key])
        extra = sorted(sets[key] - reference)
        parts = []
        if missing:
            parts.append(f"少了 {missing}")
        if extra:
            parts.append(f"多了 {extra}")
        problems.append(
            f"表 `{table}`: {key} 与 {reference_key} 的列集合不一致 "
            f"({', '.join(parts)}) —— 客户换个格式下载同一张表就少列/多列"
        )
    return problems


# ---------------------------------------------------------------- 运行时采集

def _seed_fixture(db_path: Path) -> None:
    """用确定性合成数据铺一份历史 cycle + 明细行。

    **明细行是必须的**: 空表的 Parquet 导出返回的是一个假的 schema
    (只有一列 ``_empty`` int64), 空表的 NDJSON 一行都没有 ——
    那种情况下冻下来的契约是假的。
    """
    sys.path.insert(0, str(_REPO_ROOT / "scripts"))
    from seed_ci_fixture import seed, seed_detail_tables

    # seed() 会往 stdout 打印一行进度 —— 挪到 stderr, 保证 --json 的 stdout 是纯 JSON
    with contextlib.redirect_stdout(sys.stderr):
        seed(db_path, FIXTURE_DAYS, force=True)
        seed_detail_tables(db_path, FIXTURE_DAYS)


def _contract_from_response(fmt: str, response: Any) -> Dict[str, Any]:
    """按格式把 HTTP 响应解析成列契约。

    每种格式冻它自己的那部分契约:
    - CSV     → 列名 + 顺序(多段 CSV → sections)
    - Parquet → 列名 + 顺序 + **类型**
    - NDJSON  → 键集合
    - JSON    → 键集合
    """
    if fmt == "csv":
        return parse_csv_contract(response.text)
    if fmt == "parquet":
        return parse_parquet_contract(response.content)
    if fmt == "ndjson":
        return {"keys": parse_ndjson_keys(response.text)}
    try:
        payload = response.json()
    except ValueError:
        payload = None
    return {"keys": parse_json_keys(payload)}


def _collect_path_export(client: Any,
                         route: Dict[str, str]) -> Tuple[str, Dict[str, Any]]:
    """采一个「路径后缀型」导出端点的列契约 → (key, contract)。"""
    template = route["path"]
    fmt = route["format"]
    real_path, params = build_request(template)
    response = client.request(
        "GET", real_path, params=params,
        headers={"X-Admin-Token": ADMIN_TOKEN},
    )
    key = f"GET {template}"
    if response.status_code != 200:
        return key, {
            "format": fmt,
            "status": response.status_code,
            "columns": [],
            "keys": [],
        }
    return key, {
        "format": fmt,
        "status": response.status_code,
        "table": table_of(template),
        **_contract_from_response(fmt, response),
    }


def _collect_db_export(client: Any, table: str,
                       fmt: str) -> Tuple[str, Dict[str, Any]]:
    """采一个 db-export 参数矩阵面 (table, fmt) 的列契约 → (key, contract)。"""
    key = db_export_key(table, fmt)
    response = client.request(
        "GET", DB_EXPORT_PATH,
        params={"table": table, "fmt": fmt, "limit": 100},
        headers={"X-Admin-Token": ADMIN_TOKEN},
    )
    if response.status_code != 200:
        return key, {
            "format": fmt,
            "status": response.status_code,
            "table": table,
            "columns": [],
            "keys": [],
        }
    return key, {
        "format": fmt,
        "status": response.status_code,
        "table": table,
        **_contract_from_response(fmt, response),
    }


def collect_contracts() -> Dict[str, Any]:
    """真起一次服务, 把每个导出端点的列契约解析出来。"""
    tmp_dir = Path(tempfile.mkdtemp(prefix="export-contracts-"))
    db_path = tmp_dir / "contracts.db"
    try:
        _seed_fixture(db_path)
        os.environ["GL_DB_PATH"] = str(db_path)
        os.environ["GL_ADMIN_TOKEN"] = ADMIN_TOKEN

        from fastapi.testclient import TestClient
        from web.backend import main as backend_main

        routes = discover_export_routes(backend_main.app)

        endpoints: Dict[str, Any] = {}
        with TestClient(backend_main.app) as client:
            for route in routes:
                key, contract = _collect_path_export(client, route)
                endpoints[key] = contract

            # ---- db-export 矩阵(参数驱动, 显式枚举) ----
            for table in DB_EXPORT_TABLES:
                for fmt in DB_EXPORT_FORMATS:
                    key, contract = _collect_db_export(client, table, fmt)
                    endpoints[key] = contract

        return {
            "generated_by": "scripts/gen_export_contracts.py",
            "schema_version": SCHEMA_VERSION,
            "fixture": {"days": FIXTURE_DAYS, "detail_rows": True},
            "coverage": {
                "total_export_routes": len(routes),
                "covered": len(endpoints),
                "db_export_surfaces": len(DB_EXPORT_TABLES) * len(DB_EXPORT_FORMATS),
            },
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
        print("[破坏性变更（真实的导出契约破坏）]")
        for line in breaking:
            print(f"  {line}")
    else:
        print("OK: 导出列契约与基线一致")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="把导出文件(CSV/Parquet/NDJSON/JSON)的列契约冻结成 CI 常驻不变量")
    parser.add_argument("--write", action="store_true", help="刷新基线快照")
    parser.add_argument("--json", action="store_true", help="把当前快照打到 stdout (纯 JSON)")
    args = parser.parse_args()

    current = collect_contracts()

    if args.json:
        print(json.dumps(current, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    if args.write:
        write_baseline(current)
        cov = current.get("coverage") or {}
        print(f"✅ 基线已刷新: {BASELINE_PATH.relative_to(_REPO_ROOT)} "
              f"({len(current['endpoints'])} 个导出契约: "
              f"{cov.get('total_export_routes')} 个路径导出端点 + "
              f"{cov.get('db_export_surfaces')} 个 db-export 参数矩阵面)")
        return 0

    breaking, compatible = diff_contracts(load_baseline(), current)

    # 第二层: 跨格式一致性(不需要基线的结构不变量)
    cross_problems = check_cross_format_consistency(current)
    if cross_problems:
        breaking.extend(cross_problems)

    _print_report(breaking, compatible)
    if breaking:
        print("\n如果这是**故意**的变更，请跑：")
        print("    python scripts/gen_export_contracts.py --write")
        print("并在 PR 描述里说明：哪些导出的列变了 / 客户端怎么改。")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
