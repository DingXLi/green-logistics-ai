#!/usr/bin/env python3
"""scripts/split_persistence_router.py

把 ``web/backend/main.py`` 从「8,300 行大杂烩」拆成「薄壳 + router 包」的第一步：
把 82 个 ``/api/persistence/*`` 只读查询端点搬进
``web/backend/routers/persistence.py``，同时把两组**跨模块复用**的 helper
外置成独立模块：

- ``web/backend/_auth.py``   —— admin token 校验 + ``require_admin`` 依赖
- ``web/backend/_export.py`` —— CSV / Parquet / gzip 编解码 helper

为什么用 AST 而不是正则
----------------------
端点是用装饰器声明的（``@app.get("/api/persistence/xxx")``），正则容易翻车：

- 把函数体 / docstring 里的字符串当成路径
- 漏掉跨多行的装饰器
- 改一个字符就悄悄搬错函数

所以这里用 ``ast`` 定位「顶层函数 + 它的装饰器路径」，再按行搬源码。

迁移是**闭包**的
--------------
被搬走的端点如果调用了 main.py 里的私有 helper（例如
``_build_parquet_response``），helper 必须一起搬 —— 否则 router 模块
import 时就 NameError。脚本沿「函数引用」做传递闭包。

``coordinator`` 怎么办
---------------------
端点里的 coordinator 换成 ``_coordinator()``（每次请求时读
``web.backend.main.coordinator`` 这个**模块属性**）。

为什么不放 ``app.state``：既有测试（tests/test_forecast.py 等）直接写
``backend_main.coordinator = fake``，那是**模块属性**而不是 app.state。
改成 app.state 要改所有测试 + 破坏既有约定。保持「main.coordinator 是
唯一事实来源」，router 每次读一次，测试改完立刻生效。

用法::

    python scripts/split_persistence_router.py           # 执行拆分
    python scripts/split_persistence_router.py --check   # 只体检，不落盘
"""

import argparse
import ast
import sys
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

ROOT = Path(__file__).resolve().parent.parent
MAIN_PATH = ROOT / "web" / "backend" / "main.py"
AUTH_PATH = ROOT / "web" / "backend" / "_auth.py"
EXPORT_PATH = ROOT / "web" / "backend" / "_export.py"
ROUTERS_DIR = ROOT / "web" / "backend" / "routers"
ROUTERS_INIT = ROUTERS_DIR / "__init__.py"
ROUTER_PATH = ROUTERS_DIR / "persistence.py"

PREFIX = "/api/persistence"
VERBS = {"get", "post", "put", "delete", "patch"}

AUTH_NAMES = {
    "_get_admin_token",
    "_extract_admin_token",
    "_check_admin_token",
    "require_admin",
}
EXPORT_NAMES = {
    "_csv_to_rows",
    "_rows_to_csv",
    "PARQUET_MIMETYPE",
    "_rows_to_parquet_bytes",
    "_maybe_gzip",
}
EXTERNALIZED = AUTH_NAMES | EXPORT_NAMES

# 迁移后收尾：两处**历史**风格债（随代码搬过来的），顺手清掉。
# 写成「精确字符串替换」而不是就地手改 —— 这样重跑脚本得到的结果一样。
CLEANUPS: List[Tuple[Path, str, str]] = [
    (
        EXPORT_PATH,
        '    lines = [l for l in csv_str.split("\\n") if not l.startswith("#")]',
        '    lines = [line for line in csv_str.split("\\n") if not line.startswith("#")]',
    ),
    (
        ROUTER_PATH,
        '        n_routes = v.get("n_routes") or 0\n'
        '        # Estimate total tons as avg_tons * n_routes (we don\'t have total_tons in routes)\n'
        '        # Approximation: use fleet utilization as proxy for "effective tonnage"\n',
        '        # Estimate total tons: routes 表里没有 total_tons，\n'
        '        # 用车队利用率当「有效运力」的代理指标。\n',
    ),
]


# ---------------------------------------------------------------- 解析


def read_lines(path: Path) -> List[str]:
    return path.read_text(encoding="utf-8").splitlines(keepends=True)


def route_path(node: ast.AST) -> Optional[str]:
    """顶层函数上 ``@app.<verb>("/path")`` 的路径；不是路由则返回 None。"""
    for dec in getattr(node, "decorator_list", []):
        if not isinstance(dec, ast.Call):
            continue
        func = dec.func
        if not isinstance(func, ast.Attribute):
            continue
        if not (isinstance(func.value, ast.Name) and func.value.id == "app"):
            continue
        if func.attr not in VERBS:
            continue
        if (
            dec.args
            and isinstance(dec.args[0], ast.Constant)
            and isinstance(dec.args[0].value, str)
        ):
            return dec.args[0].value
    return None


def top_level_defs(tree: ast.Module) -> Dict[str, ast.AST]:
    """顶层函数 + 顶层常量赋值（PARQUET_MIMETYPE 这种）。"""
    out: Dict[str, ast.AST] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out[node.name] = node
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    out.setdefault(target.id, node)
    return out


def body_nodes(func: ast.AST) -> List[ast.AST]:
    """函数**体**里的所有节点 —— 不含装饰器。

    装饰器里必然出现 ``app``（``@app.get(...)``），把它当成「函数体引用的
    全局名」会让闭包把 ``app = FastAPI(...)`` 一起搬走 —— 那就本末倒置了。
    """
    out: List[ast.AST] = []
    for stmt in func.body:  # type: ignore[attr-defined]
        out.extend(ast.walk(stmt))
    return out


def local_names(func: ast.AST) -> Set[str]:
    names: Set[str] = set()
    args = func.args  # type: ignore[attr-defined]
    for arg in list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs):
        names.add(arg.arg)
    if args.vararg:
        names.add(args.vararg.arg)
    if args.kwarg:
        names.add(args.kwarg.arg)
    for node in body_nodes(func):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
    return names


def referenced_globals(func: ast.AST, candidates: Set[str]) -> Set[str]:
    """函数体里引用到的、属于 candidates 的全局名字。"""
    local = local_names(func)
    refs: Set[str] = set()
    for node in body_nodes(func):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            if node.id not in local and node.id in candidates:
                refs.add(node.id)
    return refs


def closure(seed: Set[str], defs: Dict[str, ast.AST]) -> Set[str]:
    """沿「函数引用」做传递闭包 —— 被搬走的函数需要的私有 helper 一起搬。"""
    followable = set(defs) - EXTERNALIZED
    moved = set(seed)
    frontier = set(seed)
    while frontier:
        name = frontier.pop()
        node = defs.get(name)
        if node is None or not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for ref in referenced_globals(node, followable):
            if ref not in moved:
                moved.add(ref)
                frontier.add(ref)
    return moved


# ---------------------------------------------------------------- 源码切块


def block_start(node: ast.AST) -> int:
    """函数块起点（0-indexed，含装饰器）。"""
    decorators = getattr(node, "decorator_list", [])
    if decorators:
        return min(d.lineno for d in decorators) - 1
    return node.lineno - 1  # type: ignore[attr-defined]


def block_end(node: ast.AST) -> int:
    """函数块终点（0-indexed，exclusive）。"""
    return node.end_lineno  # type: ignore[attr-defined]


def attach_comments(
    lines: List[str], start_idx: int, claimed: Set[int]
) -> Tuple[List[int], List[str]]:
    """往上找紧邻（或隔一个空行）的注释块，一并搬走。"""
    j = start_idx - 1
    if j >= 0 and not lines[j].strip():
        j -= 1
    block: List[int] = []
    while j >= 0 and lines[j].lstrip().startswith("#") and j not in claimed:
        block.append(j)
        j -= 1
    if not block:
        return [], []
    block.reverse()
    return block, [lines[k] for k in block]


def insertion_point(node: ast.AST) -> int:
    """函数体第一条语句之后（0-indexed）的插入位置；有 docstring 就插在 docstring 后面。"""
    body = node.body  # type: ignore[attr-defined]
    first = body[0]
    if (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str)
    ):
        return first.end_lineno  # type: ignore[attr-defined]
    return first.lineno - 1


def indentation_of(lines: List[str], idx: int) -> str:
    line = lines[idx]
    return line[: len(line) - len(line.lstrip())]


# ---------------------------------------------------------------- 生成


ROUTER_HEADER = '''"""Green Logistics AI —— 持久化查询路由包（iter #83）。

这是 ``web/backend/main.py`` 拆出来的**第一个 router 包**。

背景
----
``main.py`` 曾经是 8,303 行 / 136 个 HTTP 端点的单文件：改一行要读 8,000 行，
改坏了只能靠人眼比对；新人和 agent 都不敢碰。本包把 82 个
``/api/persistence/*`` 只读查询端点搬进来，``main.py`` 瘦身到 ~4,700 行。

**公开 HTTP API 一个字没变** —— 这是 ADR-0011 / 0012 / 0013 / 0014 那四层
契约门禁证明的：路径、方法、参数、状态码、response_model、admin 鉴权全不变。

先建门禁，后动刀
----------------
这次拆分之所以敢做，是因为 ADR-0010 ~ ADR-0014 那四层契约**先**存在：

- ADR-0011 冻结 HTTP 面（路径 / 参数 / 状态码 / 鉴权）
- ADR-0012 冻结响应体运行时形状（字段名 + 类型）
- ADR-0013 冻结「我们到底有哪些端点」
- ADR-0014 冻结导出端点的列契约

没有这四层，「重构不改 API」只是一句口号；有了它，这句话是一个 CI 门禁。

``coordinator`` 从哪来
----------------------
端点里的 coordinator 来自 ``_coordinator()`` —— 它每次请求时读
``web.backend.main.coordinator`` 这个**模块属性**。

为什么不放 ``app.state``：既有测试（tests/test_forecast.py 等）直接写
``backend_main.coordinator = fake``，那是模块属性不是 app.state。改成
app.state 要改所有测试 + 破坏既有约定。保持「main.coordinator 是唯一事实
来源」，router 每次读一次 —— 测试怎么改都立刻生效，零额外同步代码。

新增端点请写在这里，别再往 main.py 里塞。
"""

import json
import sys
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response as FastAPIResponse, JSONResponse
from loguru import logger

from web.backend._auth import require_admin
from web.backend._export import PARQUET_MIMETYPE, _rows_to_parquet_bytes

router = APIRouter()


def _coordinator() -> Optional[Any]:
    """读 ``web.backend.main.coordinator``（模块属性，唯一事实来源）。

    走 sys.modules 而不是 import —— import 会和 main.py 形成循环依赖
    （main 要 include_router，router 又要 main 的 coordinator）。
    """
    main_module = sys.modules.get("web.backend.main")
    if main_module is None:
        return None
    return getattr(main_module, "coordinator", None)


'''

AUTH_HEADER = '''"""Green Logistics AI —— admin 鉴权（iter #83 从 main.py 外置）。

为什么外置
----------
``require_admin`` 这个 FastAPI dependency 同时被 main.py 的 admin 端点和
``routers/persistence.py`` 里的 ``/api/persistence/forecast-method-prefs``
端点使用。留在 main.py 里的话，router 就得 ``from web.backend.main import
require_admin`` —— 而 main.py 又要 import router → 循环依赖。

外置之后依赖方向是单向的：

    main.py ──▶ _auth.py
    routers/persistence.py ──▶ _auth.py

对外行为不变：``from web.backend.main import require_admin`` 仍然可用
（main.py 转手 import 回来），tests/test_admin_auth.py 照常通过。
"""

import os
from typing import Optional

from fastapi import Header, HTTPException


'''

EXPORT_HEADER = '''"""Green Logistics AI —— 导出编解码 helper（iter #83 从 main.py 外置）。

这些 helper 有两个用户：main.py 的 ``/api/admin/db-export`` 和
``routers/persistence.py`` 的 20 个 ``/api/persistence/export/*`` 端点。
留在 main.py 里的话，router 又得反向 import main → 循环依赖。

外置之后依赖方向单向：

    main.py ──▶ _export.py
    routers/persistence.py ──▶ _export.py

对外行为不变：``from web.backend.main import _rows_to_csv`` 仍然可用。
"""

import csv
import gzip
import io
from typing import Any, Dict, List

from fastapi.responses import Response as FastAPIResponse


'''


class MoveUnit:
    """一次搬迁：1 个顶层节点 + 它上面的注释块。"""

    def __init__(self, node: ast.AST, comments: List[int], lines: List[str]):
        self.node = node
        self.comment_idx = comments
        self.comment_lines = [lines[i] for i in comments]
        self.start = block_start(node)
        self.end = block_end(node)
        self.block_lines = lines[self.start : self.end]

    @property
    def name(self) -> str:
        return getattr(self.node, "name", "<assign>")

    def remove_range(self) -> Tuple[int, int]:
        """要删的行段（含注释块）。"""
        if not self.comment_idx:
            return self.start, self.end
        return min(self.comment_idx[0], self.start), self.end

    def source_lines(self) -> List[str]:
        return self.block_lines


def collect_units(
    lines: List[str], nodes: List[ast.AST]
) -> Tuple[List[MoveUnit], Set[int]]:
    """把节点变成 MoveUnit，注释块只认领一次。"""
    units: List[MoveUnit] = []
    claimed: Set[int] = set()
    for node in nodes:
        comments, _ = attach_comments(lines, block_start(node), claimed)
        claimed.update(comments)
        units.append(MoveUnit(node, comments, lines))
    return units, claimed


def build_router_source(
    lines: List[str], units: List[MoveUnit]
) -> str:
    out: List[str] = [ROUTER_HEADER]
    for unit in units:
        node = unit.node
        start = unit.start
        end = unit.end
        block = lines[start:end]

        # 装饰器：@app.x(...) → @router.x(...)（装饰器只有首行带 @app.）
        rendered: List[str] = []
        for offset, line in enumerate(block):
            if offset == 0:
                rendered.append(line.replace("@app.", "@router.", 1))
            else:
                rendered.append(line)

        # coordinator = _coordinator()
        if "coordinator" in referenced_globals(node, {"coordinator"}):
            at = insertion_point(node)
            indent = indentation_of(lines, at)
            rendered.insert(at - start, f"{indent}coordinator = _coordinator()\n")

        out.extend(unit.comment_lines)
        out.extend(rendered)
        out.append("\n\n")
    return "".join(out)


def build_module_source(units: List[MoveUnit], header: str) -> str:
    out: List[str] = [header]
    for unit in units:
        out.extend(unit.comment_lines)
        out.extend(unit.source_lines())
        out.append("\n\n")
    return "".join(out)


def rewrite_main(
    lines: List[str],
    remove_ranges: List[Tuple[int, int]],
    insert_imports_at: int,
    include_at: int,
) -> str:
    drop = set()
    for start, end in remove_ranges:
        drop.update(range(start, end))

    out: List[str] = []
    for idx, line in enumerate(lines):
        if idx in drop:
            continue
        if idx == insert_imports_at:
            out.append(
                "\n"
                "# iter #83: main.py 瘦身 —— 下面这些从 main.py 外置/拆包出去\n"
                "#   _auth.py         : admin token 校验 + require_admin 依赖\n"
                "#   _export.py       : CSV / Parquet / gzip 编解码 helper\n"
                "#   routers/         : HTTP 端点按领域拆包（第一个包：persistence）\n"
                "from web.backend._auth import (\n"
                "    _check_admin_token,\n"
                "    _extract_admin_token,\n"
                "    _get_admin_token,\n"
                "    require_admin,\n"
                ")\n"
                "from web.backend._export import (\n"
                "    PARQUET_MIMETYPE,\n"
                "    _csv_to_rows,\n"
                "    _maybe_gzip,\n"
                "    _rows_to_csv,\n"
                "    _rows_to_parquet_bytes,\n"
                ")\n"
                "from web.backend.routers.persistence import router as persistence_router\n"
            )
        if idx == include_at:
            out.append(
                "\n"
                "# iter #83: 持久化查询端点拆到 router 包（公开 HTTP API 不变，\n"
                "# 由 ADR-0011/0012/0013/0014 四层契约门禁保证）\n"
                "app.include_router(persistence_router)\n"
                "\n"
                "# 让 `python web/backend/main.py` 直接跑时，router 也能找回同一个模块对象\n"
                "sys.modules.setdefault(\"web.backend.main\", sys.modules[__name__])\n"
            )
        out.append(line)

    text = "".join(out)
    # 删块之后留下的连续空行压掉（最多留 2 行）
    while "\n\n\n\n" in text:
        text = text.replace("\n\n\n\n", "\n\n\n")
    return text


# ---------------------------------------------------------------- 主流程


def write_text(path: Path, text: str) -> None:
    """落盘：末尾只留一个换行（不要 W391）。"""
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def apply_cleanups() -> None:
    for path, old, new in CLEANUPS:
        text = path.read_text(encoding="utf-8")
        if old not in text:
            raise SystemExit(
                f"清理项在 {path.name} 里找不到原文（源码变了？）：{old[:60]!r}"
            )
        path.write_text(text.replace(old, new, 1), encoding="utf-8")


def find_insert_points(tree: ast.Module) -> Tuple[int, int]:
    """(import 插入点, include_router 插入点) —— 都是 0-indexed 行号。"""
    insert_imports_at = 0
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "synthetic.data_generator":
            insert_imports_at = node.end_lineno
    if not insert_imports_at:
        raise SystemExit("找不到 synthetic.data_generator import，插入点判定失败")

    include_at = 0
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "app":
                    include_at = node.end_lineno
    if not include_at:
        raise SystemExit("找不到 app = FastAPI(...) 赋值，插入点判定失败")
    return insert_imports_at, include_at


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="只体检：打印迁移计划，不写文件",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    lines = read_lines(MAIN_PATH)
    tree = ast.parse("".join(lines))
    defs = top_level_defs(tree)

    # 1) 持久化端点
    endpoints = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        path = route_path(node)
        if path and path.startswith(PREFIX):
            endpoints.append(node)

    if not endpoints:
        print("main.py 里已经没有 /api/persistence/* 端点了 —— 拆过了？")
        return 0

    # 2) 闭包：端点用到的私有 helper 一起搬
    seed = {n.name for n in endpoints}
    moved_names = closure(seed, defs)
    helper_names = moved_names - seed

    moved_nodes = [
        n
        for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in moved_names
    ]

    auth_nodes = [
        n
        for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in AUTH_NAMES
    ]
    export_nodes = [
        n
        for n in tree.body
        if (isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in EXPORT_NAMES)
        or (
            isinstance(n, ast.Assign)
            and any(
                isinstance(t, ast.Name) and t.id == "PARQUET_MIMETYPE" for t in n.targets
            )
        )
    ]

    print(f"持久化端点: {len(endpoints)}")
    print(f"连带搬走的私有 helper: {sorted(helper_names)}")
    print(f"外置到 _auth.py: {[n.name for n in auth_nodes]}")
    print(f"外置到 _export.py: {[getattr(n, 'name', 'PARQUET_MIMETYPE') for n in export_nodes]}")

    # 3) 收集要删的行段（注释块随函数一起搬）
    auth_units, _ = collect_units(lines, auth_nodes)
    export_units, _ = collect_units(lines, export_nodes)
    router_units, _ = collect_units(lines, moved_nodes)
    remove_ranges = [
        unit.remove_range() for unit in (auth_units + export_units + router_units)
    ]

    # 4) 插入点
    insert_imports_at, include_at = find_insert_points(tree)

    if args.check:
        print(f"\n插入点: import @{insert_imports_at}, include_router @{include_at}")
        print(f"将删除 {len(remove_ranges)} 个源码块")
        print(f"main.py: {len(lines)} 行 → 预计 {len(lines) - sum(e - s for s, e in remove_ranges)} 行")
        return 0

    # 5) 落盘
    ROUTERS_DIR.mkdir(parents=True, exist_ok=True)
    write_text(
        ROUTERS_INIT,
        '"""Green Logistics AI —— HTTP router 包。\n\n'
        "每个包一个领域；main.py 只留 app 装配 + 跨领域逻辑。\n"
        "第一个包：``persistence``（iter #83）。\n"
        '"""\n',
    )
    write_text(AUTH_PATH, build_module_source(auth_units, AUTH_HEADER))
    write_text(EXPORT_PATH, build_module_source(export_units, EXPORT_HEADER))
    write_text(ROUTER_PATH, build_router_source(lines, router_units))
    write_text(
        MAIN_PATH,
        rewrite_main(lines, remove_ranges, insert_imports_at, include_at),
    )
    apply_cleanups()

    print("\n拆分完成：")
    for path in (AUTH_PATH, EXPORT_PATH, ROUTER_PATH, MAIN_PATH):
        count = len(read_lines(path))
        print(f"  {path.relative_to(ROOT)}: {count} 行")
    return 0


if __name__ == "__main__":
    sys.exit(main())
