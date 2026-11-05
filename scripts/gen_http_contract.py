#!/usr/bin/env python3
"""
gen_http_contract.py — 从 FastAPI app 机械地抽出一份 HTTP API 契约快照 (iter #79)

为什么需要这个脚本
------------------
iter #78 (ADR-0010) 把 ``agents/persistence`` 的**公开方法签名**冻成了 CI 常驻不变量。
但 FDE 面对的客户**不调用 Python 方法** —— 客户调的是 **HTTP**。

现在 ``web/backend/main.py`` 有 136 个 HTTP 端点 + 1 个 WebSocket，
而且绝大部分端点的响应是 ``Dict[str, Any]``（OpenAPI 里**没有** response_model），
所以「响应体字段」在 OpenAPI 层面根本看不见。真正会伤到客户的破坏有两类：

1. **路由面变了** —— 路径改名 / 端点被删 / 状态码没了 / 参数改名
   → 客户的 curl / 前端 fetch / 客户自己的集成代码直接 404 或 400。
2. **鉴权面变了** —— 某个 ``/api/admin/*`` 端点不小心掉了 ``Depends(require_admin)``
   → 这不是「破坏」，这是**安全漏洞**。

这个脚本把上面两类都变成**机器可复现的契约**，交给
``tests/test_http_api_contract.py`` 每次 CI 都比对。

设计约束（和 ADR-0010 一脉相承）
-------------------------------
1. **机械抽取，不手写**：路由、参数、状态码、response_model 全部从
   ``app.openapi()`` + ``route.dependant`` 拿。手写 136 条路由 = 一定会抄错。
2. **可复现**：同一份代码跑两次，输出字节级一致（排序 + 无时间戳）。
3. **分层断言**（关键）：
   - **破坏性变更 → 红**：端点被删 / 状态码被删 / 参数被删或变必填 /
     response_model 换人 / 响应模型字段被删或变必填 / **鉴权被摘掉**。
   - **兼容性变更 → 黄（只打印，不让 CI 变红）**：新增端点 / 新增可选参数 /
     新增状态码 / 新增响应字段 / 给端点加上鉴权 / 标记 deprecated。
   - 理由：**契约门禁一旦「什么变化都报红」，团队就会开始无脑刷基线 —— 那它就废了。**
     这条是 ADR-0010 里「分层断言」思想在 HTTP 层的延续。
4. **基线自身也被守卫**：测试里断言基线的规模与关键路径，防止有人为了让 CI 变绿
   而手工削掉一半基线。

用法
----
    # 看当前 HTTP 面与仓库基线的差异（CI 用的就是这条）
    python scripts/gen_http_contract.py

    # 刷新基线（只有在你**故意**改了 HTTP 面时才做）
    python scripts/gen_http_contract.py --write

    # 打印当前契约（调试用）
    python scripts/gen_http_contract.py --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

CONTRACT_PATH = _REPO_ROOT / "tests" / "http_api_contract.json"

# 依赖注入里「这个端点需要 admin token」的判据 —— 名字必须和 main.py 里的
# require_admin 一致。改名了这里就会漏判（测试会红，提示同步）。
ADMIN_DEPENDENCY_NAME = "require_admin"


# ---------------------------------------------------------------- schema 摘要

def _schema_summary(schema: Dict[str, Any]) -> str:
    """把一段 JSON Schema 渲染成稳定的短摘要。

    只保留**客户端能感知到**的结构信息：
    - ``ref:ModelName`` —— 指向一个具名模型（模型字段在 models 段里单独冻结）
    - ``array<ref:X>`` / ``array<object>``
    - ``object{field1,field2}`` —— 内联对象的字段名
    - ``object`` —— freeform dict（``Dict[str, Any]`` 这类，OpenAPI 里看不到字段）
    - ``integer`` / ``string`` / ... —— 标量
    """
    if not schema:
        return "any"
    if "$ref" in schema:
        return "ref:" + str(schema["$ref"]).split("/")[-1]

    schema_type = schema.get("type")
    if schema_type == "array":
        return "array<" + _schema_summary(schema.get("items") or {}) + ">"
    if schema_type == "object" or "properties" in schema:
        props = sorted(schema.get("properties", {}).keys())
        if props:
            return "object{" + ",".join(props) + "}"
        if schema.get("additionalProperties") is True:
            return "object"
        return "object"
    if schema_type:
        return str(schema_type)
    if "anyOf" in schema:
        return "anyOf"
    return "any"


def _collect_refs(schema: Any, out: set) -> None:
    """递归收集 schema 里出现的所有 ``$ref`` 名字。"""
    if isinstance(schema, dict):
        ref = schema.get("$ref")
        if isinstance(ref, str):
            out.add(ref.split("/")[-1])
        for value in schema.values():
            _collect_refs(value, out)
    elif isinstance(schema, list):
        for item in schema:
            _collect_refs(item, out)


# ---------------------------------------------------------------- 契约抽取

def _route_lookup(app: Any) -> Dict[Tuple[str, str], Any]:
    """(METHOD, path) → APIRoute 对象。"""
    from fastapi.routing import APIRoute

    lookup: Dict[Tuple[str, str], Any] = {}
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in route.methods or set():
            lookup[(method.upper(), route.path)] = route
    return lookup


def _is_admin_protected(route: Any) -> bool:
    """这个端点是否挂着 ``Depends(require_admin)``。

    判据取自**真实的依赖树**（``route.dependant.dependencies``），
    不是从源码里 grep —— grep 会在函数体里出现 ``Depends(require_admin)``
    时误判，也会在依赖被换名/换写法时漏判。
    """
    dependant = getattr(route, "dependant", None)
    if dependant is None:
        return False
    for dep in getattr(dependant, "dependencies", []) or []:
        call = getattr(dep, "call", None)
        if getattr(call, "__name__", None) == ADMIN_DEPENDENCY_NAME:
            return True
    return False


def _extract_params(op: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    """按位置分组参数：query / path / header / cookie。

    只冻结**名字 + 是否必填 + 类型**：
    - 名字变了 = 破坏（老客户端还在传老名字）
    - 必填变了 = 破坏（老客户端没传 → 422）
    - 类型变了 = 破坏（老客户端传错类型 → 422）
    - **默认值变了不冻结**：默认值属于「服务端行为」，改了不会让老客户端报错，
      冻结它会让契约测试变成噪声源（Day 73 的教训：默认值是签名的一部分，
      但在 HTTP 这一层，它的破坏性弱得多）。
    """
    grouped: Dict[str, List[Dict[str, Any]]] = {
        "query": [], "path": [], "header": [], "cookie": [],
    }
    for param in op.get("parameters", []) or []:
        location = param.get("in")
        if location not in grouped:
            continue
        grouped[location].append(
            {
                "name": param.get("name"),
                "required": bool(param.get("required", False)),
                "type": _schema_summary(param.get("schema") or {}),
            }
        )
    for items in grouped.values():
        items.sort(key=lambda p: str(p["name"]))
    return grouped


def _extract_response_schema(op: Dict[str, Any]) -> Dict[str, str]:
    """每个状态码 → 响应 schema 摘要。"""
    out: Dict[str, str] = {}
    for code, resp in (op.get("responses") or {}).items():
        content = resp.get("content") or {}
        if not content:
            out[str(code)] = "empty"
            continue
        # 多 content type 时取第一个（JSON 优先）
        media_type = "application/json" if "application/json" in content else sorted(content)[0]
        schema = content[media_type].get("schema") or {}
        out[str(code)] = _schema_summary(schema)
    return out


def _extract_models(openapi: Dict[str, Any]) -> Dict[str, Any]:
    """冻结被端点引用到的具名模型的**顶层字段**。

    只冻结顶层字段 —— 嵌套模型通过 ``ref:`` 名字被间接覆盖
    （模型 A 的字段类型是 ``ref:B``，B 自己的字段在 models 段里单独冻结）。
    """
    schemas = (openapi.get("components") or {}).get("schemas") or {}

    referenced: set = set()
    _collect_refs(openapi.get("paths") or {}, referenced)

    models: Dict[str, Any] = {}
    for name in sorted(referenced):
        schema = schemas.get(name)
        if not schema:
            continue
        properties = schema.get("properties") or {}
        required = set(schema.get("required") or [])
        models[name] = {
            "fields": {
                fname: {
                    "type": _schema_summary(fschema),
                    "required": fname in required,
                }
                for fname, fschema in sorted(properties.items())
            },
            "field_count": len(properties),
        }
    return models


def extract_contract(app: Any) -> Dict[str, Any]:
    """从 FastAPI app 抽出完整的 HTTP API 契约。"""
    openapi = app.openapi()
    lookup = _route_lookup(app)

    routes: List[Dict[str, Any]] = []
    for path in sorted(openapi.get("paths", {})):
        operations = openapi["paths"][path]
        for method in sorted(k for k in operations if k != "parameters"):
            op = operations[method]
            method_upper = method.upper()
            route = lookup.get((method_upper, path))

            request_body = op.get("requestBody") or {}
            body_content = request_body.get("content") or {}
            body_schema = ""
            if body_content:
                media_type = (
                    "application/json"
                    if "application/json" in body_content
                    else sorted(body_content)[0]
                )
                body_schema = _schema_summary(body_content[media_type].get("schema") or {})

            routes.append(
                {
                    "method": method_upper,
                    "path": path,
                    "status_codes": sorted(str(c) for c in (op.get("responses") or {})),
                    "params": _extract_params(op),
                    "request_body": {
                        "required": bool(request_body.get("required", False)),
                        "schema": body_schema,
                    },
                    "responses": _extract_response_schema(op),
                    "deprecated": bool(op.get("deprecated", False)),
                    "admin_protected": _is_admin_protected(route) if route else False,
                }
            )

    websockets: List[str] = []
    for route in app.routes:
        if type(route).__name__ == "APIWebSocketRoute":
            websockets.append(str(route.path))
    websockets.sort()

    return {
        "generated_by": "scripts/gen_http_contract.py",
        "openapi_version": openapi.get("openapi"),
        "app_title": (openapi.get("info") or {}).get("title"),
        "http_route_count": len(routes),
        "websocket_routes": websockets,
        "routes": routes,
        "models": _extract_models(openapi),
    }


# ---------------------------------------------------------------- 差异计算

def _route_key(route: Dict[str, Any]) -> str:
    return f"{route['method']} {route['path']}"


def _diff_params(
    o: Dict[str, Any], n: Dict[str, Any], key: str
) -> Tuple[List[str], List[str]]:
    """参数层面的差异：删/改名/变必填/变类型 = 破坏；新增可选参数 = 兼容。"""
    breaking: List[str] = []
    compatible: List[str] = []
    for location in ("query", "path", "header", "cookie"):
        o_params = {p["name"]: p for p in o["params"].get(location, [])}
        n_params = {p["name"]: p for p in n["params"].get(location, [])}
        for name in sorted(set(o_params) - set(n_params)):
            breaking.append(f"REMOVED {location} param '{name}': {key}")
        for name in sorted(set(n_params) - set(o_params)):
            compatible.append(f"ADDED {location} param '{name}': {key}")
        for name in sorted(set(o_params) & set(n_params)):
            breaking.extend(_diff_param_pair(o_params[name], n_params[name], location, key))
            compatible.extend(_diff_param_pair_compat(o_params[name], n_params[name], key))
    return breaking, compatible


def _diff_param_pair(
    o_param: Dict[str, Any], n_param: Dict[str, Any], location: str, key: str
) -> List[str]:
    """同一个参数的破坏性差异：变必填 / 变类型。"""
    lines: List[str] = []
    if not o_param["required"] and n_param["required"]:
        lines.append(
            f"PARAM '{o_param['name']}' became required: {key} "
            f"(老客户端不传 → 422)"
        )
    if o_param["type"] != n_param["type"]:
        lines.append(
            f"PARAM '{o_param['name']}' type changed: {key} "
            f"({o_param['type']} -> {n_param['type']})"
        )
    return lines


def _diff_param_pair_compat(
    o_param: Dict[str, Any], n_param: Dict[str, Any], key: str
) -> List[str]:
    """同一个参数的兼容性差异：变可选。"""
    if o_param["required"] and not n_param["required"]:
        return [f"PARAM '{o_param['name']}' became optional: {key}"]
    return []


def _diff_responses(
    o: Dict[str, Any], n: Dict[str, Any], key: str
) -> Tuple[List[str], List[str]]:
    """响应 schema 的差异。

    只有「具名模型换人」算破坏（客户端字段全变）；
    freeform → typed 是**兼容升级**（客户端拿到更结构化的数据，不该红）。
    """
    breaking: List[str] = []
    compatible: List[str] = []
    for code, o_schema in (o.get("responses") or {}).items():
        n_schema = (n.get("responses") or {}).get(code)
        if n_schema is None:
            continue
        if o_schema.startswith("ref:") and n_schema.startswith("ref:"):
            if o_schema != n_schema:
                breaking.append(
                    f"RESPONSE model changed: {key} [{code}] "
                    f"({o_schema} -> {n_schema})"
                )
        elif not o_schema.startswith("ref:") and n_schema.startswith("ref:"):
            compatible.append(
                f"RESPONSE now typed: {key} [{code}] ({o_schema} -> {n_schema})"
            )
    return breaking, compatible


def _diff_route(
    o: Dict[str, Any], n: Dict[str, Any], key: str
) -> Tuple[List[str], List[str]]:
    """单个路由（method+path 相同）的差异。"""
    breaking: List[str] = []
    compatible: List[str] = []

    # 状态码
    for code in sorted(set(o["status_codes"]) - set(n["status_codes"])):
        breaking.append(f"REMOVED status code {code}: {key}")
    for code in sorted(set(n["status_codes"]) - set(o["status_codes"])):
        compatible.append(f"ADDED status code {code}: {key}")

    # 参数
    p_breaking, p_compatible = _diff_params(o, n, key)
    breaking.extend(p_breaking)
    compatible.extend(p_compatible)

    # 请求体
    o_body = o.get("request_body", {})
    n_body = n.get("request_body", {})
    if o_body.get("schema") and o_body.get("schema") != n_body.get("schema"):
        breaking.append(
            f"REQUEST BODY schema changed: {key} "
            f"({o_body.get('schema')} -> {n_body.get('schema')})"
        )
    if not o_body.get("required", False) and n_body.get("required", False):
        breaking.append(f"REQUEST BODY became required: {key}")

    # 响应 schema：只有「具名模型换人」才是破坏；freeform → typed 是兼容升级
    r_breaking, r_compatible = _diff_responses(o, n, key)
    breaking.extend(r_breaking)
    compatible.extend(r_compatible)

    # 鉴权 —— 安全回归必须红
    if o.get("admin_protected") and not n.get("admin_protected"):
        breaking.append(
            f"SECURITY REGRESSION: admin auth removed: {key} "
            f"(这个端点从「要 token」变成「公开」了)"
        )
    elif not o.get("admin_protected") and n.get("admin_protected"):
        compatible.append(f"admin auth added: {key}")

    # deprecated
    if not o.get("deprecated", False) and n.get("deprecated", False):
        compatible.append(f"marked deprecated: {key}")

    return breaking, compatible


def _diff_models(
    old_models: Dict[str, Any], new_models: Dict[str, Any]
) -> Tuple[List[str], List[str]]:
    """具名响应模型的字段差异：字段被删/变必填/变类型 = 破坏。"""
    breaking: List[str] = []
    compatible: List[str] = []
    for name in sorted(set(old_models) - set(new_models)):
        breaking.append(f"REMOVED response model: {name}")
    for name in sorted(set(new_models) - set(old_models)):
        compatible.append(f"ADDED response model: {name}")
    for name in sorted(set(old_models) & set(new_models)):
        o_fields = old_models[name].get("fields", {})
        n_fields = new_models[name].get("fields", {})
        for fname in sorted(set(o_fields) - set(n_fields)):
            breaking.append(f"REMOVED field '{fname}' from model {name}")
        for fname in sorted(set(n_fields) - set(o_fields)):
            compatible.append(f"ADDED field '{fname}' to model {name}")
        for fname in sorted(set(o_fields) & set(n_fields)):
            if not o_fields[fname]["required"] and n_fields[fname]["required"]:
                breaking.append(f"field '{fname}' became required in model {name}")
            if o_fields[fname]["type"] != n_fields[fname]["type"]:
                breaking.append(
                    f"field '{fname}' type changed in model {name} "
                    f"({o_fields[fname]['type']} -> {n_fields[fname]['type']})"
                )
    return breaking, compatible


def diff_contracts(
    old: Dict[str, Any], new: Dict[str, Any]
) -> Tuple[List[str], List[str]]:
    """返回 (破坏性变更, 兼容性变更)。

    破坏性变更 → CI 必须变红。
    兼容性变更 → 只打印（人看），CI 不红。
    """
    breaking: List[str] = []
    compatible: List[str] = []

    old_routes = {_route_key(r): r for r in old.get("routes", [])}
    new_routes = {_route_key(r): r for r in new.get("routes", [])}

    for key in sorted(set(old_routes) - set(new_routes)):
        breaking.append(f"REMOVED route: {key}")
    for key in sorted(set(new_routes) - set(old_routes)):
        compatible.append(f"ADDED route: {key}")

    for key in sorted(set(old_routes) & set(new_routes)):
        r_breaking, r_compatible = _diff_route(old_routes[key], new_routes[key], key)
        breaking.extend(r_breaking)
        compatible.extend(r_compatible)

    m_breaking, m_compatible = _diff_models(
        old.get("models", {}) or {}, new.get("models", {}) or {}
    )
    breaking.extend(m_breaking)
    compatible.extend(m_compatible)

    return breaking, compatible


# ---------------------------------------------------------------- CLI

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write",
        action="store_true",
        help="把当前 HTTP 面写回基线文件（只在故意改 API 时用）",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="打印当前契约（JSON）",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="把兼容性变更也当成失败（默认只对破坏性变更失败）",
    )
    args = parser.parse_args()

    from web.backend.main import app

    current = extract_contract(app)

    if args.json:
        print(json.dumps(current, indent=2, sort_keys=True, ensure_ascii=False))
        return 0

    if args.write:
        CONTRACT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(CONTRACT_PATH, "w", encoding="utf-8") as fh:
            json.dump(current, fh, indent=2, sort_keys=True, ensure_ascii=False)
            fh.write("\n")
        print(
            f"wrote {CONTRACT_PATH} "
            f"({current['http_route_count']} http routes, "
            f"{len(current['websocket_routes'])} ws routes, "
            f"{len(current['models'])} models)"
        )
        return 0

    if not CONTRACT_PATH.exists():
        print(f"baseline not found: {CONTRACT_PATH}")
        print("run: python scripts/gen_http_contract.py --write")
        return 1
    with open(CONTRACT_PATH, encoding="utf-8") as fh:
        baseline = json.load(fh)

    breaking, compatible = diff_contracts(baseline, current)

    if breaking:
        print("HTTP API BREAKING CHANGES vs committed baseline:")
        for line in breaking:
            print(f"  {line}")
        print()
        print("如果这些是**故意**的变更，请跑：")
        print("    python scripts/gen_http_contract.py --write")
        print("并在 PR 描述里说明：哪些端点变了 / 为什么 / 客户端怎么改。")
        return 1

    if compatible:
        print("HTTP API compatible changes (informational, not failing):")
        for line in compatible:
            print(f"  {line}")
        print()

    print(
        f"OK: HTTP API matches baseline "
        f"({current['http_route_count']} http routes, "
        f"{len(current['websocket_routes'])} ws routes, "
        f"{len(current['models'])} models)"
    )
    return 1 if (args.strict and compatible) else 0


if __name__ == "__main__":
    raise SystemExit(main())
