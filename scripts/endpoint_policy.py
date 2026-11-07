#!/usr/bin/env python3
"""
endpoint_policy.py — 端点**自动发现** + **显式豁免登记册」 (iter #81)

为什么需要这个模块
------------------
iter #80 (ADR-0012) 把「响应体运行时形状」冻成了 CI 常驻不变量, 但它自己
在「负面 / 技术债」那一节白纸黑字写下:

    「端点清单(`ENDPOINTS`)是**手写**的 45 条 → 新端点不会自动进快照。
      → 已用 `test_baseline_covers_expected_endpoints` 守住下限,
        但「自动发现全部端点」留给后续(难点是筛掉有副作用 / 打外网 / 调 LLM 的端点)。」

这就是本模块要还掉的债。**手写清单是 fail-open 的**:
新加一个端点 → 没人想起来把它加进清单 → 门禁照常绿 → 客户那边照样炸。
「守住下限」只能证明「该保的还在保」, 不能证明「新增的也在保」。

本模块把 fail-open 换成 fail-closed:

1. **自动发现** —— 从 FastAPI 的路由表 (`app.routes`) 里枚举所有 GET 端点,
   不再手写路径。手写清单永远追不上代码。
2. **显式豁免登记册** —— 每个采不到的端点都必须在 `EXEMPTIONS` 里登记,
   且必须写明**机器可读的豁免理由**(`reason`) + 一句人话说明(`note`)。
   豁免从「沉默的缺口」变成「代码评审能看见的一行登记」。
3. **参数绑定** —— 需要 path/query 参数的端点, 在 `PARAM_BINDINGS` 里给出
   绑定值。**同一套机制**顺手解决三件事:
   - 必填参数 (compare-materials 要 material_a/material_b …)
   - 成本封顶 (pareto 默认 50s → 限参 2s, 形状**字节级一致**)
   - 避开外网 (use_real_roads=false → CI 不依赖 OSM)
4. **Fail-closed 校验** —— 有任何 GET 端点既没被采集、也没被登记豁免,
   本模块直接**报错退出**, 而不是安静地跳过。

四条「不变量」(与 ADR-0010 / 0011 / 0012 一脉相承)
--------------------------------------------------
- **不漏**: 每个 GET 端点必须「被采集」或「被显式豁免」, 二选一, 不允许沉默。
- **不腐**: 豁免登记册里不允许出现**已经不存在的路由**(陈旧豁免 = 死代码,
  而且会让人误以为那个端点还被保护着)。
- **不空**: 豁免理由必须来自封闭枚举 `EXEMPTION_REASONS`, 不能是自由文本。
- **不撒谎**: 采集到的形状必须真的来自运行时观测, 不许手写。

跑法
----
    python scripts/endpoint_policy.py          # 分类报告 + 校验(不采集, ~3s)
    python scripts/endpoint_policy.py --json   # 机器可读输出
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List, Tuple

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


# ==================================================================== 豁免理由
# 封闭枚举 —— 每个理由都是一个「机器可判定的事实」, 不是一句主观描述。
# 想加新理由, 必须先想清楚「怎么自动判定一个端点属于这一类」。

EXEMPTION_REASONS: Dict[str, str] = {
    "NON_JSON_RESPONSE": (
        "响应体不是 JSON(CSV / Parquet / NDJSON 下载流) —— "
        "「JSON 字段形状」这个概念对它不成立。"
    ),
    "EXTERNAL_NETWORK": (
        "响应形状**取决于能不能连上第三方 API**: 拿得到是 live 形态, "
        "拿不到是 fallback 形态(字段更少/多一个 all_values)。"
        "CI 的 cache/ 被 .gitignore 排除, 所以 CI 永远走 fallback —— "
        "冻哪一边都是撒谎。"
    ),
    "LLM_CALL": (
        "端点会真的调 Gemini(花钱 + 非确定性 + 要 API key)。"
    ),
    "REQUIRES_INSTANCE": (
        "需要一个运行期才存在的实例 id(如 batch task_id), "
        "静态 fixture 里根本造不出来。"
    ),
    "FIXTURE_NO_DATA": (
        "fixture DB 里没有对应的行 —— 采到的形状会是一堆 null, "
        "看着像契约, 其实是**假的**(真库上线后形状完全不同)。"
    ),
}


# ==================================================================== 参数绑定
# key = 路由**模板**路径(带 {} 的那个), value = 绑定值。
#
# 三种绑定, 同一个机制:
#   1. 必填参数   —— 没有它端点直接 422
#   2. 成本封顶   —— pareto 默认 50s, 限参后 2s, 形状字节级一致
#   3. 避开外网   —— use_real_roads=false, CI 不依赖 OSM 路网
#
# 形状一致性是**实测**的, 不是假设: 限参前后快照 md5 完全相同。
PARAM_BINDINGS: Dict[str, Dict[str, Any]] = {
    # ---- path 参数 ----
    "/api/persistence/cycle-detail/{cycle_id}": {
        "path": {"cycle_id": "ci-fixture-cycle-001"},
    },
    "/api/iot-telemetry/{vehicle_id}": {
        "path": {"vehicle_id": "VEH000"},
    },
    # ---- 必填 query 参数 ----
    "/api/persistence/compare-cycles": {
        "query": {"cycle_id_a": "ci-fixture-cycle-001",
                  "cycle_id_b": "ci-fixture-cycle-002"},
    },
    "/api/persistence/compare-materials": {
        "query": {"material_a": "concrete", "material_b": "metal_scrap"},
    },
    "/api/admin/db-export": {
        "query": {"table": "cycles", "fmt": "json", "limit": 10},
    },
    # ---- 成本封顶 + 避开外网(实测: 限参前后形状 md5 一致) ----
    "/api/optimize/pareto": {
        "query": {"n_points": 2, "time_limit_seconds": 1, "use_real_roads": "false"},
    },
    "/api/optimize/carbon-scenarios": {
        "query": {"carbon_prices": "0,1.5", "time_limit_seconds": 1,
                  "use_real_roads": "false"},
    },
    "/api/optimize/sweet-spot": {
        "query": {"time_limit_seconds": 1, "use_real_roads": "false"},
    },
}


# ==================================================================== 豁免登记册
# key = 路由模板路径。value = {"reason": <EXEMPTION_REASONS 里的键>, "note": <人话>}
#
# 每一个豁免都必须能回答: **「客户少了哪层保护, 换来了什么」**。
EXEMPTIONS: Dict[str, Dict[str, str]] = {
    # ---- 非 JSON 下载流(14 个) ----
    "/api/persistence/export/cycle-detail/{cycle_id}.csv": {
        "reason": "NON_JSON_RESPONSE",
        "note": "CSV 全文导出; 字段契约是「列名」, 不是 JSON 字段形状。",
    },
    "/api/persistence/export/cycles.csv": {
        "reason": "NON_JSON_RESPONSE", "note": "CSV 全文导出。",
    },
    "/api/persistence/export/supplies.csv": {
        "reason": "NON_JSON_RESPONSE", "note": "CSV 全文导出。",
    },
    "/api/persistence/export/matches.csv": {
        "reason": "NON_JSON_RESPONSE", "note": "CSV 全文导出。",
    },
    "/api/persistence/export/perturbed-supplies.csv": {
        "reason": "NON_JSON_RESPONSE", "note": "CSV 全文导出。",
    },
    "/api/persistence/export/routes.csv": {
        "reason": "NON_JSON_RESPONSE", "note": "CSV 全文导出。",
    },
    "/api/persistence/export/cycles.parquet": {
        "reason": "NON_JSON_RESPONSE", "note": "Parquet 二进制列存。",
    },
    "/api/persistence/export/supplies.parquet": {
        "reason": "NON_JSON_RESPONSE", "note": "Parquet 二进制列存。",
    },
    "/api/persistence/export/matches.parquet": {
        "reason": "NON_JSON_RESPONSE", "note": "Parquet 二进制列存。",
    },
    "/api/persistence/export/routes.parquet": {
        "reason": "NON_JSON_RESPONSE", "note": "Parquet 二进制列存。",
    },
    "/api/persistence/export/cycles.ndjson": {
        "reason": "NON_JSON_RESPONSE", "note": "NDJSON(一行一条, 不是单个 JSON 文档)。",
    },
    "/api/persistence/export/supplies.ndjson": {
        "reason": "NON_JSON_RESPONSE", "note": "NDJSON。",
    },
    "/api/persistence/export/matches.ndjson": {
        "reason": "NON_JSON_RESPONSE", "note": "NDJSON。",
    },
    "/api/persistence/export/routes.ndjson": {
        "reason": "NON_JSON_RESPONSE", "note": "NDJSON。",
    },

    # ---- 形状依赖外网(实测, 不是猜的) ----
    "/api/weather": {
        "reason": "EXTERNAL_NETWORK",
        "note": ("live 形态有 current.temperature_c 等 7 个字段, "
                 "fallback 形态的 current 是**空对象 {}** —— "
                 "两种形态的字段集完全不同。CI 没缓存(cache/ 被 gitignore), "
                 "所以 CI 永远看到 fallback, 本地开发常常看到 live。"),
    },
    "/api/signals/external": {
        "reason": "EXTERNAL_NETWORK",
        "note": ("live 形态没有 all_values 字段, fallback 形态多出 "
                 "all_values.<YYYY-MM> 共 54 个叶子 —— 两种形态字段集不同。"),
    },

    # ---- 真调 LLM ----
    "/api/debug/llm": {
        "reason": "LLM_CALL",
        "note": "诊断端点会真的调 Gemini(4 个场景), 花钱且非确定性。",
    },

    # ---- 需要运行期实例 ----
    "/api/admin/simulate/batch/{task_id}": {
        "reason": "REQUIRES_INSTANCE",
        "note": "需要一个跑过的 batch task_id; 静态 fixture 造不出来(会 404)。",
    },

    # ---- fixture 里没有对应行 ----
    "/api/persistence/compare-routes": {
        "reason": "FIXTURE_NO_DATA",
        "note": ("fixture 只铺了 optimization_cycles, 没有 routes 行 —— "
                 "采到的形状是 route_a/route_b/winner 全 null, "
                 "看着像契约, 上真库后形状完全不是这样。"),
    },
}


# ==================================================================== 发现

def get_app() -> Any:
    """拿到 FastAPI app(延迟 import, 免得脚本一上来就拖一堆依赖)。"""
    from web.backend import main as backend_main
    return backend_main.app


def discover_get_routes(app: Any) -> List[str]:
    """从路由表里枚举所有 GET 端点的**模板路径**, 排序返回。

    只取 APIRoute —— Starlette 自带的 `/openapi.json` / `/docs` / `/redoc`
    是普通 Route, 不属于「客户调的业务端点」, 不进契约。
    """
    from fastapi.routing import APIRoute

    paths = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        methods = {m for m in (route.methods or set()) if m not in ("HEAD", "OPTIONS")}
        if methods == {"GET"}:
            paths.append(route.path)
    return sorted(set(paths))


def classify(app: Any) -> Dict[str, Any]:
    """把所有 GET 端点分成「采集」和「豁免」两类, 并做 fail-closed 校验。

    返回:
        {
          "covered":   [路由, ...],       # 要采集的
          "exempted":  {路由: {reason, note}},
          "unclassified": [路由, ...],    # 既不采集也不豁免 → 报错
          "stale":     [路由, ...],       # 豁免登记册里已经不存在的路由
          "bad_reason": [(路由, reason)], # 豁免理由不在封闭枚举里
          "total_get_routes": int,
        }
    """
    routes = discover_get_routes(app)
    route_set = set(routes)

    covered: List[str] = []
    exempted: Dict[str, Dict[str, str]] = {}
    unclassified: List[str] = []
    stale: List[str] = []
    bad_reason: List[Tuple[str, str]] = []

    for path in routes:
        if path in EXEMPTIONS:
            entry = EXEMPTIONS[path]
            reason = entry.get("reason", "")
            if reason not in EXEMPTION_REASONS:
                bad_reason.append((path, reason))
            exempted[path] = entry
        else:
            covered.append(path)

    for path in EXEMPTIONS:
        if path not in route_set:
            stale.append(path)

    return {
        "covered": covered,
        "exempted": exempted,
        "unclassified": unclassified,
        "routes": routes,
        "stale": sorted(stale),
        "bad_reason": bad_reason,
        "total_get_routes": len(routes),
    }


def build_request(path: str) -> Tuple[str, Dict[str, Any]]:
    """把路由模板 + 绑定值 → (真实路径, query 参数)。"""
    binding = PARAM_BINDINGS.get(path, {})
    real_path = path
    for name, value in (binding.get("path") or {}).items():
        real_path = real_path.replace("{" + name + "}", str(value))
    return real_path, dict(binding.get("query") or {})


# ==================================================================== 校验

def validate(result: Dict[str, Any]) -> List[str]:
    """返回错误列表(空 = 通过)。**fail-closed**: 有错就拒绝继续。"""
    errors: List[str] = []

    for path, reason in result["bad_reason"]:
        errors.append(
            f"豁免理由不在封闭枚举里: {path} -> {reason!r} "
            f"(合法值: {sorted(EXEMPTION_REASONS)})"
        )

    # **不漏** —— 每个 GET 端点必须「被采集」或「被豁免」, 二选一。
    # 这是 fail-closed 的核心: 宁可报错停下, 也不让端点沉默地逃出契约。
    all_routes = set(result.get("routes") or [])
    covered = set(result["covered"])
    exempted_paths = set(result["exempted"])
    orphans = all_routes - covered - exempted_paths
    for path in sorted(orphans):
        errors.append(
            f"未分类的 GET 端点: {path} —— "
            f"要么在 PARAM_BINDINGS 里绑定参数后采集, "
            f"要么在 EXEMPTIONS 里显式豁免并写明理由"
        )

    for path in result["stale"]:
        errors.append(
            f"陈旧豁免: {path} 已经不在路由表里了 —— "
            f"要么端点被删了(请删掉这行豁免), 要么路由改名了(请改这行)"
        )

    for path, entry in sorted(result["exempted"].items()):
        if not entry.get("note", "").strip():
            errors.append(f"豁免缺少人话说明(note): {path}")

    for path in result["covered"]:
        if path not in PARAM_BINDINGS and "{" in path:
            errors.append(
                f"端点有 path 参数但没有绑定: {path} "
                f"→ 请在 PARAM_BINDINGS 里给一个 fixture 里真实存在的值"
            )

    return errors


# ==================================================================== 报告

def build_report(result: Dict[str, Any]) -> Dict[str, Any]:
    reasons: Dict[str, int] = {}
    for entry in result["exempted"].values():
        r = entry.get("reason", "?")
        reasons[r] = reasons.get(r, 0) + 1
    return {
        "total_get_routes": result["total_get_routes"],
        "covered_count": len(result["covered"]),
        "exempted_count": len(result["exempted"]),
        "exempted_by_reason": reasons,
        "covered": result["covered"],
        "exempted": {k: v.get("reason", "?")
                     for k, v in sorted(result["exempted"].items())},
    }


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="端点自动发现 + 显式豁免登记册(fail-closed 校验)")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args()

    app = get_app()
    result = classify(app)
    errors = validate(result)
    report = build_report(result)

    if args.json:
        print(json.dumps({**report, "errors": errors}, ensure_ascii=False, indent=2))
        return 1 if errors else 0

    print(f"GET 端点总数: {report['total_get_routes']}")
    print(f"  采集: {report['covered_count']}")
    print(f"  豁免: {report['exempted_count']}  {report['exempted_by_reason']}")
    coverage = report["covered_count"] / max(1, report["total_get_routes"])
    print(f"  覆盖率: {coverage:.1%}")

    if errors:
        print("\n❌ 校验失败(fail-closed):")
        for e in errors:
            print(f"  - {e}")
        return 1

    print("\n✅ 校验通过: 每个 GET 端点都被采集或被显式豁免, 没有沉默的缺口。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
