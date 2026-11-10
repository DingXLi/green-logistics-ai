"""Green Logistics AI —— 持久化查询路由包（iter #83）。

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


# ============================================
# V2 新增：持久化 KPI 查询端点
# ============================================
@router.get("/api/persistence/recent-cycles")
async def get_recent_cycles(limit: int = 10):
    """获取最近 N 个优化周期的 KPI（从 SQLite 读）"""
    coordinator = _coordinator()
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    return coordinator.persistence.get_recent_cycles(limit=limit)


@router.get("/api/persistence/kpi-timeseries")
async def get_kpi_timeseries(
    since_sim_day: Optional[int] = None,
    until_sim_day: Optional[int] = None,
):
    """KPI 时间序列 (iter #8 + iter #18 时间窗口 + iter #52 efficiency) — 按 sim_day 聚合。

    Query (iter #18):
    - since_sim_day: 起始 sim_day (含)
    - until_sim_day: 结束 sim_day (含)

    Response fields (iter #52 新增):
    - cost_per_ton_sek, co2_per_ton_kg: per-ton efficiency
    - cost_per_match_sek: per-match efficiency
    - n_supply_offers, n_demand_requests: supply/demand counts per day
    - supply_demand_ratio: matches / max(supply, demand), 0-1
    """
    coordinator = _coordinator()
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    if since_sim_day is not None and until_sim_day is not None:
        if since_sim_day > until_sim_day:
            raise HTTPException(
                status_code=400,
                detail=f"since_sim_day ({since_sim_day}) > until_sim_day ({until_sim_day})",
            )
    return coordinator.persistence.get_kpi_timeseries(
        since_sim_day=since_sim_day,
        until_sim_day=until_sim_day,
    )


@router.get("/api/persistence/fleet-timeseries")
async def get_fleet_timeseries():
    """
    Fleet 时间序列 (iter #9) — 按 sim_day 聚合 fleet 指标。

    返回: sim_day → {sim_day, n_vehicles_used, n_vehicles_available,
                     fleet_utilization_pct, total_distance_km,
                     n_matches, total_tons}

    用途: Dashboard fleet trend 图, 分析调度模式。
    """
    coordinator = _coordinator()
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    return coordinator.persistence.get_fleet_timeseries()


@router.get("/api/persistence/seasonal-timeseries")
async def get_seasonal_timeseries():
    """按月份聚合的 KPI + seasonal_factor (iter #4)。

    供前端分析 "夏季 vs 冬季" 成本/CO2 差异。
    """
    coordinator = _coordinator()
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    return coordinator.persistence.get_seasonal_timeseries()


@router.get("/api/persistence/seasonal-timeseries-by-material")
async def get_seasonal_timeseries_by_material():
    """
    iter #46: Seasonal time-series cross-tab (material × month).

    Returns a 2D matrix showing seasonal_multiplier + total_tons for
    each (material, month) cell. Useful for spotting which materials
    are most / least affected by seasonal swings.

    Response shape:
        n_materials, n_months, materials, month_labels, matrix: [...]
    """
    coordinator = _coordinator()
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    return coordinator.persistence.get_seasonal_timeseries_by_material()


# ============================================
# iter #38: Perturbation impact analytics
# ============================================
# Surfaces how active perturbations changed cycle KPIs over time.
# Returns time-series data + aggregate summary so the frontend can
# chart base-vs-effective seasonal factors.
@router.get("/api/persistence/perturbation-impact")
async def get_perturbation_impact(
    since_sim_day: Optional[int] = None,
    until_sim_day: Optional[int] = None,
    limit: int = 90,
):
    """
    iter #38: Per-cycle perturbation impact analysis.

    For each cycle in the window, returns:
    - sim_day
    - base_seasonal_factor_avg (无扰动 baseline)
    - seasonal_factor_avg (effective, 含扰动)
    - perturbation_count (该 cycle 命中几个 supply 点被扰动)
    - perturbation_total_multiplier (effective/base ratio)
    - delta (seasonal_factor_avg - base_seasonal_factor_avg)

    Plus aggregate summary:
    - n_cycles_total / n_cycles_with_perturbation
    - avg_delta / max_delta / min_delta
    - max_total_multiplier

    Query:
    - since_sim_day / until_sim_day: optional sim_day filter
    - limit: max cycles to return (default 90, ordered by sim_day DESC)
    """
    coordinator = _coordinator()
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")

    since = int(since_sim_day) if since_sim_day is not None else None
    until = int(until_sim_day) if until_sim_day is not None else None
    if since is not None and until is not None and until < since:
        raise HTTPException(
            status_code=400,
            detail=f"until_sim_day ({until}) must be >= since_sim_day ({since})",
        )

    return coordinator.persistence.get_perturbation_impact(
        since_sim_day=since, until_sim_day=until, limit=limit
    )


@router.get("/api/persistence/perturbation-impact-by-material")
async def get_perturbation_impact_by_material(
    since_sim_day: Optional[int] = None,
    until_sim_day: Optional[int] = None,
):
    """
    iter #46: Per-material perturbation impact breakdown.

    Aggregates supply_offers.perturbation_applied by material_type to show
    which materials are most affected by active perturbations. Useful for
    spotting "perturbation X is hitting concrete 5x more than metal_scrap".

    Query:
    - since_sim_day / until_sim_day: optional sim_day filter (same as
      /api/persistence/perturbation-impact)

    Returns:
        {
          by_material: [{material_type, n_perturbed, n_total,
                          perturbation_rate_pct, avg_effective_multiplier,
                          avg_base_multiplier, avg_ratio}],
          summary: {n_materials, n_perturbed_total, n_supply_offers_total,
                    overall_perturbation_rate_pct},
          window: {since_sim_day, until_sim_day},
        }
    """
    coordinator = _coordinator()
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    since = int(since_sim_day) if since_sim_day is not None else None
    until = int(until_sim_day) if until_sim_day is not None else None
    if since is not None and until is not None and until < since:
        raise HTTPException(
            status_code=400,
            detail=f"until_sim_day ({until}) must be >= since_sim_day ({since})",
        )
    return coordinator.persistence.get_perturbation_impact_by_material(
        since_sim_day=since, until_sim_day=until
    )


@router.get("/api/persistence/llm-cost-timeseries")
async def get_llm_cost_timeseries(
    since_sim_day: Optional[int] = None,
    until_sim_day: Optional[int] = None,
):
    """
    LLM cost 时间序列 (iter #28) — 按 sim_day 聚合 llm_decisions 表。

    Returns: [{sim_day, n_decisions, llm_n, fallback_n, avg_multiplier,
               avg_confidence, llm_success_rate_pct}, ...]

    Query:
    - since_sim_day: 起始 sim_day (含)
    - until_sim_day: 结束 sim_day (含)

    用途:
    - Dashboard LLM 使用趋势图
    - 检测 LLM fallback 频率异常
    - 未来可接 forecast endpoint 预测 LLM cost

    Note: llm_decisions 表不存精确 cost_usd; 真实 cost 看 /api/admin/llm-stats
    (in-memory LLMTracker)
    """
    coordinator = _coordinator()
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    if since_sim_day is not None and since_sim_day < 0:
        raise HTTPException(status_code=400, detail="since_sim_day must be >= 0")
    if until_sim_day is not None and until_sim_day < 0:
        raise HTTPException(status_code=400, detail="until_sim_day must be >= 0")
    if since_sim_day is not None and until_sim_day is not None and since_sim_day > until_sim_day:
        raise HTTPException(
            status_code=400,
            detail=f"since_sim_day ({since_sim_day}) > until_sim_day ({until_sim_day})",
        )
    return {
        "since_sim_day": since_sim_day,
        "until_sim_day": until_sim_day,
        "rows": coordinator.persistence.get_llm_cost_timeseries(
            since_sim_day=since_sim_day,
            until_sim_day=until_sim_day,
        ),
    }


@router.get("/api/persistence/llm-decision-targets")
async def get_llm_decision_targets(
    decision_type: Optional[str] = None,
    limit: int = 50,
):
    """
    iter #48: List unique LLM call targets with per-target stats.

    Groups llm_decisions by (target_id, decision_type). Useful for
    identifying which supply/demand points get the most LLM attention
    (and which fall back most often to deterministic predictions).

    Query:
    - decision_type: optional filter
                    ('demand_prediction' / 'supply_prediction' / etc.)
    - limit: max targets to return (default 50, max 500)

    Returns:
        {
          decision_type, limit,
          n_targets: int,
          targets: [
            {target_id, decision_type, target_type, n_calls,
             n_real_llm, n_fallback, last_called_sim_day,
             first_called_sim_day, avg_multiplier, avg_confidence},
            ...
          ],
        }
    """
    coordinator = _coordinator()
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    limit = max(1, min(500, limit))
    targets = coordinator.persistence.get_llm_decision_targets(
        decision_type=decision_type, limit=limit,
    )
    return {
        "decision_type": decision_type,
        "limit": limit,
        "n_targets": len(targets),
        "targets": targets,
    }


@router.get("/api/persistence/llm-cost-by-type")
async def get_llm_cost_by_type(
    since_sim_day: Optional[int] = None,
    until_sim_day: Optional[int] = None,
):
    """
    iter #48: LLM usage breakdown by decision_type.

    Aggregates llm_decisions table by decision_type (e.g.
    'demand_prediction', 'supply_prediction') to show which call type
    uses LLM the most + fallback rate per type.

    Returns:
        {
          since_sim_day, until_sim_day,
          by_type: [
            {decision_type, n_total, n_llm, n_fallback, llm_rate_pct,
             avg_multiplier, avg_confidence, n_unique_targets,
             first_decision_sim_day, last_decision_sim_day},
            ...
          ],
        }
    """
    coordinator = _coordinator()
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    if since_sim_day is not None and since_sim_day < 0:
        raise HTTPException(status_code=400, detail="since_sim_day must be >= 0")
    if until_sim_day is not None and until_sim_day < 0:
        raise HTTPException(status_code=400, detail="until_sim_day must be >= 0")
    if since_sim_day is not None and until_sim_day is not None and since_sim_day > until_sim_day:
        raise HTTPException(
            status_code=400,
            detail=f"since_sim_day ({since_sim_day}) > until_sim_day ({until_sim_day})",
        )
    return {
        "since_sim_day": since_sim_day,
        "until_sim_day": until_sim_day,
        "by_type": coordinator.persistence.get_llm_cost_by_decision_type(
            since_sim_day=since_sim_day, until_sim_day=until_sim_day,
        ),
    }


@router.get("/api/persistence/llm-cost-forecast")
async def get_llm_cost_forecast(
    horizon: int = 7,
    history_n: int = 14,
    method: str = "linear",
    since_sim_day: Optional[int] = None,
    until_sim_day: Optional[int] = None,
):
    """
    LLM usage/cost forecast (iter #29) — 预测未来 LLM decisions。

    预测 5 个 usage 指标: n_decisions / llm_n / fallback_n /
    avg_multiplier / avg_confidence。精确 cost_usd 仍以 /api/admin/llm-stats 为准。

    Query:
    - horizon: 预测未来多少 sim_day (default 7, range 1-30)
    - history_n: 历史 sim_day 数 (default 14, range 2-90)
    - method: linear / moving_average / exponential_smoothing
    - since_sim_day / until_sim_day: 过滤历史窗口
    """
    coordinator = _coordinator()
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    if horizon < 1 or horizon > 30:
        raise HTTPException(status_code=400, detail=f"horizon must be 1-30, got {horizon}")
    if history_n < 2 or history_n > 90:
        raise HTTPException(status_code=400, detail=f"history_n must be 2-90, got {history_n}")
    valid_methods = ("linear", "moving_average", "exponential_smoothing")
    if method not in valid_methods:
        raise HTTPException(
            status_code=400,
            detail=f"invalid method: {method!r}, valid: {list(valid_methods)}",
        )
    if since_sim_day is not None and since_sim_day < 0:
        raise HTTPException(status_code=400, detail="since_sim_day must be >= 0")
    if until_sim_day is not None and until_sim_day < 0:
        raise HTTPException(status_code=400, detail="until_sim_day must be >= 0")
    if since_sim_day is not None and until_sim_day is not None and since_sim_day > until_sim_day:
        raise HTTPException(
            status_code=400,
            detail=f"since_sim_day ({since_sim_day}) > until_sim_day ({until_sim_day})",
        )
    try:
        return coordinator.persistence.forecast_llm_cost(
            horizon=horizon,
            history_n=history_n,
            method=method,
            since_sim_day=since_sim_day,
            until_sim_day=until_sim_day,
        )
    except ImportError as e:
        raise HTTPException(status_code=501, detail=f"LLM cost forecast requires numpy: {e}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/persistence/forecast")
async def get_forecast(
    horizon: int = 7,
    history_n: int = 14,
    metrics: Optional[str] = None,
    method: str = "linear",
):
    """
    KPI forecast (iter #26 + iter #28) — 预测未来 N 个 sim_day。

    对每个 metric 用最近 history_n 个 sim_day 拟合, 预测 horizon 个未来 sim_day。
    返回 95% confidence interval + trend + R²。

    Query:
    - horizon: 预测未来多少 sim_day (default 7, range 1-30)
    - history_n: 用多少历史 sim_day 拟合 (default 14, range 2-90)
    - metrics: 逗号分隔的 metric 列表 (default = cost_sek,co2_kg,util_pct,matches)
        可选: cost_sek / co2_kg / util_pct / matches
    - method (iter #28): 预测方法 (default = linear)
        - linear: 线性回归 (适合趋势性数据)
        - moving_average: 全期均值 (适合平稳数据)
        - exponential_smoothing: 指数平滑 alpha=0.3 (近期值更重要)

    Returns:
        {
          horizon, history_n, method, last_sim_day, forecast_sim_days,
          metrics: {
            "cost_sek": {
              history: [{sim_day, value, is_forecast: false}, ...],
              forecast: [{sim_day, value, is_forecast: true, lower_95, upper_95}, ...],
              trend: "up" | "down" | "flat",
              method: "linear" | "moving_average" | "exponential_smoothing",
              slope_per_day, r_squared, residual_std, mean_value,
              method_meta: {intercept} / {window_mean, window_n} / {alpha, final_level}
            },
            ...
          }
        }
    """
    coordinator = _coordinator()
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")

    if horizon < 1 or horizon > 30:
        raise HTTPException(status_code=400, detail=f"horizon must be 1-30, got {horizon}")
    if history_n < 2 or history_n > 90:
        raise HTTPException(status_code=400, detail=f"history_n must be 2-90, got {history_n}")

    valid_methods = ("linear", "moving_average", "exponential_smoothing", "auto")
    if method not in valid_methods:
        raise HTTPException(
            status_code=400,
            detail=f"invalid method: {method!r}, valid: {list(valid_methods)}",
        )

    metrics_list = None
    if metrics:
        metrics_list = [m.strip() for m in metrics.split(",") if m.strip()]
        valid_metrics = {"cost_sek", "co2_kg", "util_pct", "matches"}
        invalid = [m for m in metrics_list if m not in valid_metrics]
        if invalid:
            raise HTTPException(
                status_code=400,
                detail=f"invalid metrics: {invalid}, valid: {sorted(valid_metrics)}",
            )

    # iter #35: method=auto → 使用各 metric 持久化的最佳 method
    if method == "auto":
        # 没指定 metrics → 默认全部 metric
        targets = metrics_list or list(valid_metrics)
        method = coordinator.persistence.get_best_method(targets[0]) or "linear"
        # 注: 只支持单一 method, 如果多 metric 有不同最佳 method, 只能取第一个
        # (这种情况推荐用 /api/persistence/forecast/multi 看对比, 或
        # 用 /api/persistence/forecast-confidence 自动选取)

    try:
        return coordinator.persistence.forecast_next_n_sim_days(
            horizon=horizon,
            history_n=history_n,
            metrics=metrics_list,
            method=method,
        )
    except ImportError as e:
        raise HTTPException(status_code=501, detail=f"Forecast requires numpy: {e}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/persistence/forecast-confidence")
async def get_forecast_confidence(
    horizon: int = 7,
    history_n: int = 14,
    metrics: Optional[str] = None,
    methods: Optional[str] = None,
):
    """Forecast confidence/ensemble summary (iter #30).

    Runs multiple forecast methods and returns:
    - per-method predictions and quality scores
    - ensemble mean + standard deviation + 95% interval
    - dispersion_pct (higher = methods disagree)
    - best_method (highest R² for linear / lowest residual_std otherwise)
    """
    coordinator = _coordinator()
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    if horizon < 1 or horizon > 30:
        raise HTTPException(status_code=400, detail=f"horizon must be 1-30, got {horizon}")
    if history_n < 2 or history_n > 90:
        raise HTTPException(status_code=400, detail=f"history_n must be 2-90, got {history_n}")

    valid_methods_all = ("linear", "moving_average", "exponential_smoothing")
    methods_list = list(valid_methods_all)
    if methods:
        methods_list = [m.strip() for m in methods.split(",") if m.strip()]
        invalid = [m for m in methods_list if m not in valid_methods_all]
        if invalid:
            raise HTTPException(
                status_code=400,
                detail=f"invalid methods: {invalid}, valid: {list(valid_methods_all)}",
            )
    metrics_list = None
    if metrics:
        metrics_list = [m.strip() for m in metrics.split(",") if m.strip()]
        valid_metrics = {"cost_sek", "co2_kg", "util_pct", "matches"}
        invalid = [m for m in metrics_list if m not in valid_metrics]
        if invalid:
            raise HTTPException(
                status_code=400,
                detail=f"invalid metrics: {invalid}, valid: {sorted(valid_metrics)}",
            )

    try:
        method_results: Dict[str, Dict[str, Any]] = {}
        for method in methods_list:
            method_results[method] = coordinator.persistence.forecast_next_n_sim_days(
                horizon=horizon,
                history_n=history_n,
                metrics=metrics_list,
                method=method,
            )
        if not method_results:
            return {"horizon": horizon, "history_n": history_n, "methods": [],
                    "last_sim_day": None, "forecast_sim_days": [],
                    "confidence": {}, "note": "no forecast methods requested"}

        first_result = next(iter(method_results.values()))
        metric_keys = list(first_result.get("metrics", {}).keys())
        confidence: Dict[str, Any] = {}
        for metric in metric_keys:
            histories: List[List[Dict[str, Any]]] = [
                result.get("metrics", {}).get(metric, {}).get("history", [])
                for result in method_results.values()
            ]
            history: List[Dict[str, Any]] = next((h for h in histories if h), [])
            forecast_by_method = {
                method: result.get("metrics", {}).get(metric, {}).get("forecast", [])
                for method, result in method_results.items()
            }
            sim_days = [p.get("sim_day") for p in next((v for v in forecast_by_method.values() if v), [])]
            ensemble = []
            per_method_quality = {}
            for method, points in forecast_by_method.items():
                metric_data = method_results[method].get("metrics", {}).get(metric, {})
                r2 = metric_data.get("r_squared", 0.0)
                residual = metric_data.get("residual_std", 0.0)
                per_method_quality[method] = {
                    "r_squared": r2,
                    "residual_std": residual,
                }
            for i, sim_day in enumerate(sim_days):
                values = []
                for method in methods_list:
                    points = forecast_by_method.get(method, [])
                    if i < len(points) and points[i].get("value") is not None:
                        values.append(float(points[i]["value"]))
                if not values:
                    continue
                mean_value = sum(values) / len(values)
                if len(values) > 1:
                    std_value = (sum((v - mean_value) ** 2 for v in values) / (len(values) - 1)) ** 0.5
                else:
                    std_value = 0.0
                ci_half = 1.96 * std_value
                dispersion = (std_value / abs(mean_value) * 100) if mean_value else 0.0
                ensemble.append({
                    "sim_day": sim_day,
                    "mean": round(mean_value, 2),
                    "stddev": round(std_value, 2),
                    "lower_95": round(mean_value - ci_half, 2),
                    "upper_95": round(mean_value + ci_half, 2),
                    "dispersion_pct": round(dispersion, 2),
                    "n_methods": len(values),
                })
            # For non-linear methods, lower residual_std is the useful score; for
            # linear, prefer R². Choose deterministically (method order on ties).
            best_method = methods_list[0]
            best_score = -float("inf")
            for method in methods_list:
                quality = per_method_quality.get(method, {})
                score = quality.get("r_squared", 0.0) if method == "linear" else -quality.get("residual_std", float("inf"))
                if score > best_score:
                    best_method = method
                    best_score = score
            confidence[metric] = {
                "history": history,
                "forecast": ensemble,
                "per_method_quality": per_method_quality,
                "best_method": best_method,
                "n_methods": len(methods_list),
            }

        # iter #35: 持久化每个 metric 的最佳 method (fire-and-forget)
        # 不抛异常, 失败只记 debug log — 不能因为 pref save 失败影响 forecast 返回
        for metric, conf in confidence.items():
            best = conf["best_method"]
            r2 = conf["per_method_quality"].get(best, {}).get("r_squared")
            try:
                coordinator.persistence.save_method_pref(
                    metric=metric,
                    method=best,
                    r_squared=r2,
                    history_n=history_n,
                )
            except Exception as e:
                logger.debug(f"save_method_pref failed (ignore): {e}")

        # iter #42: record predictions for calibration (fire-and-forget)
        # Stores each method's predicted value at each forecast_sim_day.
        # Once that sim_day arrives in optimization_cycles, /api/persistence/forecast-calibration
        # backfills actual_value + error to compute MAE / MAPE / RMSE.
        last_sim_day = first_result.get("last_sim_day") or 0
        for method, result in method_results.items():
            for metric_key, metric_data in result.get("metrics", {}).items():
                forecast_points = metric_data.get("forecast", [])
                if not forecast_points:
                    continue
                try:
                    coordinator.persistence.record_forecast_predictions(
                        metric=metric_key,
                        method=method,
                        predictions=forecast_points,
                        created_at_sim_day=int(last_sim_day),
                    )
                except Exception as e:
                    logger.debug(f"record_forecast_predictions failed (ignore): {e}")
        # Backfill actuals for past predictions (fire-and-forget)
        try:
            coordinator.persistence.backfill_forecast_actuals()
        except Exception as e:
            logger.debug(f"backfill_forecast_actuals failed (ignore): {e}")

        return {
            "horizon": horizon,
            "history_n": history_n,
            "methods": methods_list,
            "last_sim_day": first_result.get("last_sim_day"),
            "forecast_sim_days": first_result.get("forecast_sim_days", []),
            "confidence": confidence,
        }
    except ImportError as e:
        raise HTTPException(status_code=501, detail=f"Forecast requires numpy: {e}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/persistence/forecast-method-prefs")
async def get_forecast_method_prefs(_: None = Depends(require_admin)):
    """
    iter #35: 查看所有 metric 的最佳 method 偏好 (admin only).

    返回 {prefs: [{metric, best_method, r_squared, history_n, n_samples, updated_at}, ...]}.
    """
    coordinator = _coordinator()
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    prefs = coordinator.persistence.get_method_prefs()
    return {
        "prefs": prefs,
        "count": len(prefs),
        "metrics_covered": [p["metric"] for p in prefs],
    }


@router.delete("/api/persistence/forecast-method-prefs")
async def clear_forecast_method_prefs(
    metric: Optional[str] = None,
    _: None = Depends(require_admin),
):
    """
    iter #35: 清除 method prefs (admin only).

    Query:
    - metric (optional): 只清除指定 metric; 不传则清除全部
    """
    coordinator = _coordinator()
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    if metric:
        deleted = coordinator.persistence.delete_method_pref(metric)
        return {"deleted": deleted, "scope": "metric", "metric": metric}
    else:
        n = coordinator.persistence.clear_method_prefs()
        return {"deleted": n, "scope": "all"}


@router.get("/api/persistence/forecast-calibration")
async def get_forecast_calibration(
    metric: Optional[str] = None,
    method: Optional[str] = None,
):
    """
    iter #42: Forecast calibration — actual vs predicted accuracy stats.

    Returns MAE / MAPE / RMSE / bias for forecast predictions whose
    target sim_day has already occurred in optimization_cycles.

    Stats interpretation:
    - mae (Mean Absolute Error): average |actual - predicted|
    - rmse (Root Mean Squared Error): sqrt(mean(error²)); penalizes outliers
    - mape_pct: Mean Absolute Percentage Error; intuitive (lower = better)
    - bias: mean(actual - predicted); +ve = model under-predicts, -ve = over-predicts

    Query params:
    - metric: optional filter (e.g. 'cost_sek')
    - method: optional filter (e.g. 'linear')

    Returns:
        {
          n_total_predictions, n_evaluated, n_pending,
          overall: {mae, rmse, mape_pct, bias, ...},
          by_metric: {<metric>: {stats}},
          by_method: {<method>: {stats}},
          by_metric_method: {<metric>: {<method>: {stats}}},
        }
    """
    coordinator = _coordinator()
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    if metric and metric not in {"cost_sek", "co2_kg", "util_pct", "matches"}:
        raise HTTPException(
            status_code=400,
            detail=f"invalid metric: {metric}, valid: cost_sek, co2_kg, util_pct, matches",
        )
    if method and method not in {"linear", "moving_average", "exponential_smoothing"}:
        raise HTTPException(
            status_code=400,
            detail=f"invalid method: {method}",
        )

    # Backfill any new actuals first (cheap query; runs in O(pending))
    try:
        coordinator.persistence.backfill_forecast_actuals()
    except Exception as e:
        logger.debug(f"backfill_forecast_actuals failed (ignore): {e}")

    stats = coordinator.persistence.get_forecast_calibration(
        metric=metric, method=method,
    )
    n_total = coordinator.persistence.count_forecast_predictions(metric=metric)
    n_evaluated = stats.get("overall", {}).get("n_evaluated", 0)
    return {
        "n_total_predictions": n_total,
        "n_evaluated": n_evaluated,
        "n_pending": n_total - n_evaluated,
        "metric_filter": metric,
        "method_filter": method,
        **stats,
    }


@router.get("/api/persistence/forecast-calibration/trend")
async def get_forecast_calibration_trend(
    metric: Optional[str] = None,
    method: Optional[str] = None,
):
    """
    iter #43: Cumulative forecast calibration trend over time.

    Returns per-sim_day cumulative MAE/RMSE/MAPE/bias computed from
    evaluated predictions up to and including that day.

    Query params:
    - metric: optional filter (cost_sek / co2_kg / util_pct / matches)
    - method: optional filter (linear / moving_average / exponential_smoothing)

    Returns:
        {
          n_buckets: int,
          trend: [{
            bucket_sim_day: int,
            n_evaluated: int,
            cumulative_mae: float,
            cumulative_rmse: float,
            cumulative_mape_pct: float | null,
            cumulative_bias: float,
          }, ...],
          metric_filter, method_filter,
        }
    """
    coordinator = _coordinator()
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    if metric and metric not in {"cost_sek", "co2_kg", "util_pct", "matches"}:
        raise HTTPException(
            status_code=400,
            detail=f"invalid metric: {metric}",
        )
    if method and method not in {"linear", "moving_average", "exponential_smoothing"}:
        raise HTTPException(
            status_code=400,
            detail=f"invalid method: {method}",
        )

    # Backfill any new actuals first
    try:
        coordinator.persistence.backfill_forecast_actuals()
    except Exception as e:
        logger.debug(f"backfill_forecast_actuals failed (ignore): {e}")

    trend = coordinator.persistence.get_forecast_calibration_trend(
        metric=metric, method=method,
    )
    return {
        "n_buckets": len(trend),
        "trend": trend,
        "metric_filter": metric,
        "method_filter": method,
    }


@router.get("/api/persistence/prediction-accuracy-by-day")
async def get_prediction_accuracy_by_day(
    metric: Optional[str] = None,
    method: Optional[str] = None,
    lead_time_buckets: Optional[str] = None,
    since_created_day: Optional[int] = None,
    until_created_day: Optional[int] = None,
):
    """
    iter #58: Per-day prediction accuracy with lead-time breakdown.

    Unlike /forecast-calibration/trend (cumulative, bucketed by
    forecast_sim_day), this endpoint buckets by **created_at_sim_day**
    (the day the prediction was generated) and reports non-cumulative
    per-day accuracy, broken down by lead-time buckets (forecast_sim_day -
    created_at_sim_day).

    Query params:
    - metric: optional filter (cost_sek / co2_kg / util_pct / matches)
    - method: optional filter (linear / moving_average / exponential_smoothing)
    - lead_time_buckets: optional comma-separated list of "lo-hi" pairs
      (e.g. "1-1,2-7,8-30"). Default buckets: 1d, 2-3d, 4-7d, 8-14d, 15-30d.
    - since_created_day / until_created_day: optional day range filter.

    Returns:
      {
        metric_filter, method_filter,
        lead_time_buckets: [{label, min_lead, max_lead}, ...],
        day_range: {since_created_day, until_created_day},
        by_day: [{
          created_at_sim_day: int,
          n_predictions: int,
          n_evaluated: int,
          n_pending: int,
          overall_mape_pct: float | null,
          overall_mae: float | null,
          overall_bias: float | null,
          by_lead_time: {<bucket_label>: {n_evaluated, mape_pct, mae, bias}},
        }, ...],
        overall: {n_predictions, n_evaluated, n_pending, overall_mape_pct},
      }

    Use cases:
    - Track forecast quality over time (per-day accuracy trend)
    - Detect accuracy degradation at specific lead times (e.g. 1-day-ahead vs 7-day-ahead)
    - Identify which days had high pending predictions (still awaiting actuals)
    """
    coordinator = _coordinator()
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    if metric and metric not in {"cost_sek", "co2_kg", "util_pct", "matches"}:
        raise HTTPException(
            status_code=400,
            detail=f"invalid metric: {metric}",
        )
    if method and method not in {"linear", "moving_average", "exponential_smoothing"}:
        raise HTTPException(
            status_code=400,
            detail=f"invalid method: {method}",
        )

    # Parse lead_time_buckets CSV into list of tuples
    parsed_buckets: Optional[List[Tuple[int, int]]] = None
    if lead_time_buckets:
        try:
            parsed_buckets = []
            for token in lead_time_buckets.split(","):
                token = token.strip()
                if not token:
                    continue
                lo_s, hi_s = token.split("-", 1)
                parsed_buckets.append((int(lo_s.strip()), int(hi_s.strip())))
        except (ValueError, AttributeError) as e:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"invalid lead_time_buckets format: {lead_time_buckets!r}. "
                    f"Expected comma-separated 'lo-hi' pairs (e.g. '1-1,2-7,8-30'). "
                    f"Error: {e}"
                ),
            )

    # Backfill any new actuals first
    try:
        coordinator.persistence.backfill_forecast_actuals()
    except Exception as e:
        logger.debug(f"backfill_forecast_actuals failed (ignore): {e}")

    return coordinator.persistence.get_prediction_accuracy_by_day(
        metric=metric,
        method=method,
        lead_time_buckets=parsed_buckets,
        since_created_day=since_created_day,
        until_created_day=until_created_day,
    )


@router.get("/api/persistence/forecast/multi")
async def get_forecast_multi(
    horizon: int = 7,
    history_n: int = 14,
    metrics: Optional[str] = None,
    methods: Optional[str] = None,
):
    """
    KPI forecast comparison (iter #28) — 同时跑多个 method 返回 comparison。

    用于前端图表叠加显示 (linear vs ma vs es), 让用户看不同方法的预测差别。

    Query:
    - horizon: 预测未来多少 sim_day (default 7, range 1-30)
    - history_n: 用多少历史 sim_day 拟合 (default 14, range 2-90)
    - metrics: 逗号分隔的 metric 列表 (default = cost_sek,co2_kg,util_pct,matches)
    - methods: 逗号分隔的 method 列表 (default = linear,moving_average,exponential_smoothing)
        可选: linear / moving_average / exponential_smoothing

    Returns:
        {
          horizon, history_n, last_sim_day, forecast_sim_days,
          methods: ["linear", "moving_average", "exponential_smoothing"],
          comparison: {
            "cost_sek": {
              history: [...],
              forecasts: {
                "linear": [{sim_day, value, lower_95, upper_95, is_forecast: true}],
                "moving_average": [...],
                "exponential_smoothing": [...],
              },
              final_values: {linear: 120, ma: 100, es: 95},  // 最后一天的预测值
              range_pct: {linear: 5.2, ma: 0.0, es: -1.2},  // 预测变化率 (%)
            },
            ...
          }
        }
    """
    coordinator = _coordinator()
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")

    if horizon < 1 or horizon > 30:
        raise HTTPException(status_code=400, detail=f"horizon must be 1-30, got {horizon}")
    if history_n < 2 or history_n > 90:
        raise HTTPException(status_code=400, detail=f"history_n must be 2-90, got {history_n}")

    valid_methods_all = ("linear", "moving_average", "exponential_smoothing")
    methods_list = list(valid_methods_all)
    if methods:
        methods_list = [m.strip() for m in methods.split(",") if m.strip()]
        invalid = [m for m in methods_list if m not in valid_methods_all]
        if invalid:
            raise HTTPException(
                status_code=400,
                detail=f"invalid methods: {invalid}, valid: {list(valid_methods_all)}",
            )

    metrics_list = None
    if metrics:
        metrics_list = [m.strip() for m in metrics.split(",") if m.strip()]
        valid_metrics = {"cost_sek", "co2_kg", "util_pct", "matches"}
        invalid = [m for m in metrics_list if m not in valid_metrics]
        if invalid:
            raise HTTPException(
                status_code=400,
                detail=f"invalid metrics: {invalid}, valid: {sorted(valid_metrics)}",
            )

    try:
        # Run each method
        per_method_results: Dict[str, Dict[str, Any]] = {}
        last_sim_day = None
        forecast_sim_days: List[int] = []
        for method in methods_list:
            r = coordinator.persistence.forecast_next_n_sim_days(
                horizon=horizon,
                history_n=history_n,
                metrics=metrics_list,
                method=method,
            )
            per_method_results[method] = r
            last_sim_day = r.get("last_sim_day", last_sim_day)
            forecast_sim_days = r.get("forecast_sim_days", forecast_sim_days)

        # Build comparison structure
        # Use first method's metric keys as canonical
        if not per_method_results:
            return {
                "horizon": horizon,
                "history_n": history_n,
                "methods": [],
                "last_sim_day": last_sim_day,
                "forecast_sim_days": forecast_sim_days,
                "comparison": {},
            }
        first_method = methods_list[0]
        first_result = per_method_results[first_method]
        metric_keys = list(first_result.get("metrics", {}).keys())

        comparison: Dict[str, Any] = {}
        for metric in metric_keys:
            forecasts_per_method: Dict[str, List[Dict[str, Any]]] = {}
            history_out: List[Dict[str, Any]] = []
            for method in methods_list:
                metric_data = per_method_results[method]["metrics"].get(metric, {})
                if not history_out and metric_data.get("history"):
                    history_out = metric_data["history"]
                forecasts_per_method[method] = metric_data.get("forecast", [])

            # Compute final values + change %
            final_values: Dict[str, float] = {}
            range_pct: Dict[str, float] = {}
            mean_history = first_result["metrics"][metric].get("mean_value", 0.0)
            for method, fc_list in forecasts_per_method.items():
                if not fc_list:
                    continue
                last = fc_list[-1]["value"]
                final_values[method] = last
                if mean_history and mean_history != 0:
                    range_pct[method] = round((last - mean_history) / mean_history * 100, 2)

            comparison[metric] = {
                "history": history_out,
                "forecasts": forecasts_per_method,
                "final_values": final_values,
                "change_from_mean_pct": range_pct,
            }

        return {
            "horizon": horizon,
            "history_n": history_n,
            "methods": methods_list,
            "last_sim_day": last_sim_day,
            "forecast_sim_days": forecast_sim_days,
            "comparison": comparison,
        }
    except ImportError as e:
        raise HTTPException(status_code=501, detail=f"Forecast requires numpy: {e}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/persistence/summary")
async def get_persistence_summary():
    """全局统计汇总"""
    coordinator = _coordinator()
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    return coordinator.persistence.get_summary()


@router.get("/api/persistence/efficiency-metrics")
async def get_persistence_efficiency_metrics():
    """
    效率指标 (iter #7) — 从 optimization_cycles 聚合的 "per ton" 比率。

    用途:
    - Dashboard 顶部 KPI (cost/CO2 per ton)
    - 长期 ROI 报告
    - Trend tracking (每月 per-ton 比率变化)

    返回:
    - n_cycles, total_tons, total_cost_sek, total_co2_kg
    - cost_per_ton_sek, co2_per_ton_kg (核心指标)
    - avg_fleet_util_pct, match_rate_pct (效率指标)
    - avg_tons_per_cycle, avg_cost_per_cycle, avg_co2_per_cycle
    - min_sim_day, max_sim_day (时间范围)
    - cycles_with_matches (有 match 的 cycle 数)
    """
    coordinator = _coordinator()
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    return coordinator.persistence.get_efficiency_metrics()


@router.get("/api/persistence/monthly-efficiency-trend")
async def get_monthly_efficiency_trend():
    """
    月度 efficiency 趋势 (iter #8) — 按月份聚合的 cost/CO2 per ton 趋势。

    返回 1-12 月的 efficiency 序列 (按月份升序), 含:
    - month (1-12), month_name, n_cycles
    - total_tons, total_cost_sek, total_co2_kg
    - cost_per_ton_sek, co2_per_ton_kg
    - avg_seasonal_factor, avg_fleet_util_pct, match_rate_pct

    用途: 分析 summer (Jun-Aug) vs winter (Dec-Feb) 成本/CO2 差异。
    """
    coordinator = _coordinator()
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    return coordinator.persistence.get_monthly_efficiency_trend()


@router.get("/api/persistence/cycle-history")
async def get_cycle_history(
    limit: int = 50,
    sim_day_min: Optional[int] = None,
    sim_day_max: Optional[int] = None,
    has_matches_only: bool = False,
):
    """
    Cycle history list (iter #11) — 列出过往 optimization cycles 摘要。

    Query params:
    - limit: 最多返回条数 (default 50, max 500)
    - sim_day_min / sim_day_max: sim_day 范围过滤
    - has_matches_only: True → 仅 n_matches > 0 的 cycle

    每个 cycle 含 KPI 摘要 + n_routes (subquery join)。
    按 id DESC (新到旧) 排序。

    用途: Dashboard Cycle History 表格。
    """
    coordinator = _coordinator()
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    limit = max(1, min(500, limit))
    return coordinator.persistence.get_cycle_history(
        limit=limit,
        sim_day_min=sim_day_min,
        sim_day_max=sim_day_max,
        has_matches_only=has_matches_only,
    )


@router.get("/api/persistence/cycle-detail/{cycle_id}")
async def get_cycle_detail(
    cycle_id: str,
    match_limit: Optional[int] = None,
    match_offset: int = 0,
    route_limit: Optional[int] = None,
    route_offset: int = 0,
):
    """
    Cycle detail (iter #11) — 单个 cycle 的完整数据 (KPI + supply/demand/match/route)。

    iter #13: pagination query params:
    - match_limit / match_offset: 分页返回 matches (None/0 = 全返)
    - route_limit / route_offset: 分页返回 routes

    Returns:
        {cycle: {...}, supply_offers: [...], demand_requests: [...],
         matches: [...], routes: [...],
         pagination: {matches: {total, limit, offset, has_more},
                      routes: {total, limit, offset, has_more}}}

    404 if cycle_id 不存在。
    """
    coordinator = _coordinator()
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    # 防御性 clamp: limit 不能 < 1, offset 不能 < 0
    if match_limit is not None:
        match_limit = max(1, min(1000, match_limit))
    match_offset = max(0, match_offset)
    if route_limit is not None:
        route_limit = max(1, min(1000, route_limit))
    route_offset = max(0, route_offset)

    detail = coordinator.persistence.get_cycle_detail(
        cycle_id,
        match_limit=match_limit,
        match_offset=match_offset,
        route_limit=route_limit,
        route_offset=route_offset,
    )
    if detail is None:
        raise HTTPException(status_code=404, detail=f"Cycle {cycle_id} not found")
    return detail


@router.get("/api/persistence/export/cycle-detail/{cycle_id}.csv")
async def export_cycle_detail_csv(
    cycle_id: str,
    include_metadata: bool = True,
):
    """
    iter #48: Export full cycle detail (iter #11) as one combined CSV.

    Returns all sections in one file, separated by `# section:` comments:
        - cycle_metadata: single row of 22 KPI columns
        - supply_offers: all supply points in this cycle
        - demand_requests: all demand points
        - matches: all matches with distance + profit
        - routes: all vehicle routes with stops_count

    Path param:
    - cycle_id: the cycle to export

    Query:
    - include_metadata: iter #19, top file metadata header (default true)

    Returns:
        text/csv response. 404 if cycle_id not found.
    """
    coordinator = _coordinator()
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    csv_data = coordinator.persistence.export_cycle_detail_csv(
        cycle_id=cycle_id, include_metadata=include_metadata,
    )
    if csv_data is None:
        raise HTTPException(status_code=404, detail=f"Cycle {cycle_id} not found")
    return FastAPIResponse(
        content=csv_data,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": (
                f'attachment; filename="green_logistics_cycle_detail_{cycle_id}.csv"'
            ),
        },
    )


@router.get("/api/persistence/export/cycles.csv")
async def export_cycles_csv(limit: int = 1000, include_metadata: bool = True):
    """
    Export cycle history as CSV (iter #11 + iter #19 metadata)。

    Query:
    - limit: 最多多少行 (default 1000, max 10000)
    - include_metadata: iter #19, 是否在 CSV 顶部加 metadata (生成时间 + db 路径 + size + row count)

    Returns: text/csv 响应 + Content-Disposition: attachment。
    包含 19 列 KPI (见 Persistence.export_cycles_csv 注释)。
    """
    coordinator = _coordinator()
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    limit = max(1, min(10000, limit))
    csv_data = coordinator.persistence.export_cycles_csv(
        limit=limit, include_metadata=include_metadata,
    )
    return FastAPIResponse(
        content=csv_data,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": (
                f'attachment; filename="green_logistics_cycles_{limit}.csv"'
            ),
        },
    )


@router.get("/api/persistence/export/supplies.csv")
async def export_supplies_csv(limit: int = 10000, include_metadata: bool = True):
    """
    Export supply_offers as CSV (iter #17 + iter #19 metadata)。

    Query:
    - limit: 最多多少行 (default 10000, max 50000)
    - include_metadata: iter #19, 是否在 CSV 顶部加 metadata header

    Returns: text/csv 响应 + Content-Disposition: attachment。
    10 列: cycle_id, supply_id, material_type, location_lat, location_lon,
           available_tons, moisture_percent, quality_score, sim_day, sim_hour
    """
    coordinator = _coordinator()
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    limit = max(1, min(50000, limit))
    csv_data = coordinator.persistence.export_supplies_csv(
        limit=limit, include_metadata=include_metadata,
    )
    return FastAPIResponse(
        content=csv_data,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": (
                f'attachment; filename="green_logistics_supplies_{limit}.csv"'
            ),
        },
    )


@router.get("/api/persistence/export/matches.csv")
async def export_matches_csv(limit: int = 10000, include_metadata: bool = True):
    """
    Export matches as CSV (iter #17 + iter #19 metadata)。

    Query:
    - limit: 最多多少行 (default 10000, max 50000)
    - include_metadata: iter #19, 是否在 CSV 顶部加 metadata header

    Returns: text/csv 响应。
    8 列: cycle_id, supply_id, demand_id, material_type, tons, distance_km,
          estimated_profit_sek, sim_day
    """
    coordinator = _coordinator()
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    limit = max(1, min(50000, limit))
    csv_data = coordinator.persistence.export_matches_csv(
        limit=limit, include_metadata=include_metadata,
    )
    return FastAPIResponse(
        content=csv_data,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": (
                f'attachment; filename="green_logistics_matches_{limit}.csv"'
            ),
        },
    )


@router.get("/api/persistence/export/perturbed-supplies.csv")
async def export_perturbed_supplies_csv(
    limit: int = 10000,
    include_metadata: bool = True,
    only_perturbed: bool = False,
):
    """
    iter #47: Export supply_offers with perturbation tracking (15 cols).

    Extends the standard /export/supplies.csv (iter #17) with iter #38
    perturbation columns: base_seasonal_multiplier, seasonal_multiplier,
    perturbation_applied. Adds derived columns multiplier_ratio (effective
    / base) and was_perturbed (bool).

    Query:
    - limit: max rows (default 10000, max 50000)
    - include_metadata: iter #19, top metadata header
    - only_perturbed: if true, only export rows where perturbation_applied=1
                      (saves space when analyzing only shocked rows)

    Returns: text/csv 响应 + Content-Disposition: attachment.
    """
    coordinator = _coordinator()
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    limit = max(1, min(50000, limit))
    csv_data = coordinator.persistence.export_perturbed_supplies_csv(
        limit=limit, include_metadata=include_metadata, only_perturbed=only_perturbed,
    )
    suffix = "_perturbed" if only_perturbed else ""
    return FastAPIResponse(
        content=csv_data,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": (
                f'attachment; filename="green_logistics_perturbed_supplies{suffix}_{limit}.csv"'
            ),
        },
    )


@router.get("/api/persistence/export/routes.csv")
async def export_routes_csv(limit: int = 10000, include_metadata: bool = True):
    """
    Export routes as CSV (iter #17 + iter #19 metadata)。

    Query:
    - limit: 最多多少行 (default 10000, max 50000)
    - include_metadata: iter #19, 是否在 CSV 顶部加 metadata header

    Returns: text/csv 响应。
    8 列: cycle_id, vehicle_id, distance_km, duration_hours, cost_sek, co2_kg,
          stops_count, sim_day
    """
    coordinator = _coordinator()
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    limit = max(1, min(50000, limit))
    csv_data = coordinator.persistence.export_routes_csv(
        limit=limit, include_metadata=include_metadata,
    )
    return FastAPIResponse(
        content=csv_data,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": (
                f'attachment; filename="green_logistics_routes_{limit}.csv"'
            ),
        },
    )


# ============================================
# iter #27: Parquet format for /api/persistence/export/*
# 复用 _rows_to_parquet_bytes helper (iter #23)
# 新增: 4 个 parquet endpoint + 4 个 json endpoint (consistency)
# ============================================
def _build_parquet_response(rows: List[Dict[str, Any]], table: str, limit: int):
    """Common helper: list of dicts → parquet binary response.
    Returns 501 if pyarrow not installed; 500 with detail on serialization error.
    """
    try:
        parquet_bytes = _rows_to_parquet_bytes(rows)
    except ImportError as e:
        raise HTTPException(
            status_code=501,
            detail=f"Parquet export requires pyarrow. Install: pip install pyarrow>=15.0.0 ({e})",
        )
    except Exception as e:
        logger.error(f"Parquet serialization failed for {table}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Parquet serialization failed: {type(e).__name__}: {str(e)[:200]}",
        )
    filename = f"green_logistics_{table}_{limit}.parquet"
    return FastAPIResponse(
        content=parquet_bytes,
        media_type=PARQUET_MIMETYPE,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _build_json_response(rows: List[Dict[str, Any]], table: str, limit: int):
    """iter #27: list of dicts → JSON response (pretty-printed array)."""
    filename = f"green_logistics_{table}_{limit}.json"
    return JSONResponse(
        content=rows,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _build_ndjson_response(rows: List[Dict[str, Any]], table: str, limit: int):
    """iter #27: list of dicts → NDJSON (line-delimited JSON)."""
    ndjson_str = "\n".join(json.dumps(r, default=str) for r in rows)
    filename = f"green_logistics_{table}_{limit}.ndjson"
    return FastAPIResponse(
        content=ndjson_str,
        media_type="application/x-ndjson",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ============================================
# iter #27: Parquet endpoints (4)
# ============================================
@router.get("/api/persistence/export/cycles.parquet")
async def export_cycles_parquet(limit: int = 1000):
    """Export cycles as Apache Parquet (iter #27)。"""
    coordinator = _coordinator()
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    limit = max(1, min(10000, limit))
    rows = coordinator.persistence.export_cycles_rows(limit=limit)
    return _build_parquet_response(rows, "cycles", limit)


@router.get("/api/persistence/export/supplies.parquet")
async def export_supplies_parquet(limit: int = 10000):
    """Export supply_offers as Apache Parquet (iter #27)。"""
    coordinator = _coordinator()
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    limit = max(1, min(50000, limit))
    rows = coordinator.persistence.export_supplies_rows(limit=limit)
    return _build_parquet_response(rows, "supplies", limit)


@router.get("/api/persistence/export/matches.parquet")
async def export_matches_parquet(limit: int = 10000):
    """Export matches as Apache Parquet (iter #27)。"""
    coordinator = _coordinator()
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    limit = max(1, min(50000, limit))
    rows = coordinator.persistence.export_matches_rows(limit=limit)
    return _build_parquet_response(rows, "matches", limit)


@router.get("/api/persistence/export/routes.parquet")
async def export_routes_parquet(limit: int = 10000):
    """Export routes as Apache Parquet (iter #27)。"""
    coordinator = _coordinator()
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    limit = max(1, min(50000, limit))
    rows = coordinator.persistence.export_routes_rows(limit=limit)
    return _build_parquet_response(rows, "routes", limit)


# ============================================
# iter #27: JSON + NDJSON endpoints (4 + 4 = 8)
# ============================================
@router.get("/api/persistence/export/cycles.json")
async def export_cycles_json(limit: int = 1000):
    """Export cycles as pretty JSON array (iter #27)."""
    coordinator = _coordinator()
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    limit = max(1, min(10000, limit))
    rows = coordinator.persistence.export_cycles_rows(limit=limit)
    return _build_json_response(rows, "cycles", limit)


@router.get("/api/persistence/export/supplies.json")
async def export_supplies_json(limit: int = 10000):
    """Export supply_offers as pretty JSON array (iter #27)."""
    coordinator = _coordinator()
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    limit = max(1, min(50000, limit))
    rows = coordinator.persistence.export_supplies_rows(limit=limit)
    return _build_json_response(rows, "supplies", limit)


@router.get("/api/persistence/export/matches.json")
async def export_matches_json(limit: int = 10000):
    """Export matches as pretty JSON array (iter #27)."""
    coordinator = _coordinator()
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    limit = max(1, min(50000, limit))
    rows = coordinator.persistence.export_matches_rows(limit=limit)
    return _build_json_response(rows, "matches", limit)


@router.get("/api/persistence/export/routes.json")
async def export_routes_json(limit: int = 10000):
    """Export routes as pretty JSON array (iter #27)."""
    coordinator = _coordinator()
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    limit = max(1, min(50000, limit))
    rows = coordinator.persistence.export_routes_rows(limit=limit)
    return _build_json_response(rows, "routes", limit)


@router.get("/api/persistence/export/cycles.ndjson")
async def export_cycles_ndjson(limit: int = 1000):
    """Export cycles as NDJSON (iter #27)."""
    coordinator = _coordinator()
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    limit = max(1, min(10000, limit))
    rows = coordinator.persistence.export_cycles_rows(limit=limit)
    return _build_ndjson_response(rows, "cycles", limit)


@router.get("/api/persistence/export/supplies.ndjson")
async def export_supplies_ndjson(limit: int = 10000):
    """Export supply_offers as NDJSON (iter #27)."""
    coordinator = _coordinator()
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    limit = max(1, min(50000, limit))
    rows = coordinator.persistence.export_supplies_rows(limit=limit)
    return _build_ndjson_response(rows, "supplies", limit)


@router.get("/api/persistence/export/matches.ndjson")
async def export_matches_ndjson(limit: int = 10000):
    """Export matches as NDJSON (iter #27)."""
    coordinator = _coordinator()
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    limit = max(1, min(50000, limit))
    rows = coordinator.persistence.export_matches_rows(limit=limit)
    return _build_ndjson_response(rows, "matches", limit)


@router.get("/api/persistence/export/routes.ndjson")
async def export_routes_ndjson(limit: int = 10000):
    """Export routes as NDJSON (iter #27)."""
    coordinator = _coordinator()
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    limit = max(1, min(50000, limit))
    rows = coordinator.persistence.export_routes_rows(limit=limit)
    return _build_ndjson_response(rows, "routes", limit)


@router.get("/api/persistence/match-distance-stats")
async def get_match_distance_stats():
    """
    Match 距离统计 (iter #15) — 从 matches 表聚合 distance_km 指标。

    返回:
    - total_matches, n_cycles_with_matches
    - avg / min / max / median distance_km
    - distance_distribution: 4 桶 (<10, 10-50, 50-100, >=100 km)

    用途: 验证 OSM 距离是否合理, 监控 match quality。
    """
    coordinator = _coordinator()
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    return coordinator.persistence.get_match_distance_stats()


@router.get("/api/persistence/vehicle-stats")
async def get_vehicle_stats(
    vehicle_id: Optional[str] = None,
    limit: int = 100,
):
    """
    iter #41: Vehicle historical aggregates across cycles.

    Per-vehicle KPIs useful for fleet ops:
    - n_routes / total_distance_km / total_duration_hours
    - total_cost_sek / total_co2_kg
    - avg_cost_per_km_sek / avg_co2_per_km_kg (efficiency)
    - first/last cycle_id + last_sim_day

    Query params:
      vehicle_id: optional, return single vehicle stats
      limit:      max vehicles returned (default 100, max 1000)

    Sorted by total_distance_km DESC (most-active vehicles first).

    Returns:
      {
        n_vehicles, vehicles: [{vehicle_id, n_routes, total_distance_km,
                                 total_duration_hours, total_cost_sek, total_co2_kg,
                                 avg_distance_km, avg_duration_hours,
                                 avg_cost_per_km_sek, avg_co2_per_km_kg,
                                 first_cycle_id, last_cycle_id, last_sim_day}, ...]
      }
    """
    coordinator = _coordinator()
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    if limit < 1 or limit > 1000:
        raise HTTPException(status_code=400, detail="limit must be in [1, 1000]")

    vehicles = coordinator.persistence.get_vehicle_stats(
        vehicle_id=vehicle_id,
        limit=limit,
    )
    return {
        "n_vehicles": len(vehicles),
        "vehicles": vehicles,
    }


@router.get("/api/persistence/vehicle-timeseries")
async def get_vehicle_timeseries(
    vehicle_id: Optional[str] = None,
    metric: str = "co2_per_km",
    since_sim_day: Optional[int] = None,
    until_sim_day: Optional[int] = None,
    limit: int = 100,
):
    """
    iter #65: Per-vehicle time-series of efficiency metrics.

    Returns one row per cycle × per vehicle with the chosen efficiency
    metric, plus a per-vehicle summary (mean/min/max/latest + trend:
    improving / declining / stable based on first-half vs second-half
    delta).

    Query:
    - vehicle_id: optional filter (e.g. 'VEH_001')
    - metric: one of co2_per_km / co2_per_hour / cost_per_km / cost_per_hour /
      speed_km_per_hour / distance_km / n_stops (default co2_per_km)
    - since_sim_day / until_sim_day: optional sim_day window
    - limit: max rows to return (default 100, max 500)

    Returns:
      {
        metric, metric_description, direction,
        filter: {vehicle_id, since_sim_day, until_sim_day},
        n_routes_evaluated, n_routes_returned,
        timeseries: [{route_id, cycle_id, vehicle_id, sim_day, sim_hour,
                      distance_km, duration_hours, cost_sek, co2_kg,
                      n_stops, value}, ...],  # chronological order
        per_vehicle_summary: {
          vehicle_id: {n_routes, mean_value, min_value, max_value,
                       latest_value, trend, first_sim_day, last_sim_day},
          ...
        },
      }

    Use cases:
    - Plot each vehicle's co2_per_km over time
    - Identify vehicles trending toward greener routes
    - Spot anomalous per-cycle efficiency drops
    """
    coordinator = _coordinator()
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    limit = max(1, min(500, limit))
    try:
        return coordinator.persistence.get_vehicle_timeseries(
            vehicle_id=vehicle_id,
            metric=metric,
            since_sim_day=since_sim_day,
            until_sim_day=until_sim_day,
            limit=limit,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/persistence/cycle-trend-comparison")
async def get_cycle_trend_comparison(
    early_window: int = 5,
    late_window: int = 5,
    metric: str = "co2_per_ton",
    since_sim_day: Optional[int] = None,
    until_sim_day: Optional[int] = None,
):
    """
    iter #65: Compare early-window vs late-window cycles by metric.

    Aggregates the first N cycles vs the last N cycles in the window and
    compares their average value of the chosen metric. Returns mean /
    min / max / median / stddev for each window + absolute + pct delta
    + trend classification (improving / declining / stable).

    Query:
    - early_window: cycles from start of window (default 5)
    - late_window: cycles from end of window (default 5)
    - metric: one of co2_per_ton / cost_per_ton / co2_per_km / cost_per_km /
      fleet_utilization_pct / match_rate_vs_offers / tons_per_cycle
      (default co2_per_ton)
    - since_sim_day / until_sim_day: optional sim_day window

    Returns:
      {
        metric, metric_description, direction,
        window: {since_sim_day, until_sim_day},
        early: {n_cycles, cycle_ids, mean_value, min_value, max_value,
                median_value, stddev_value},
        late: {n_cycles, cycle_ids, mean_value, ...},
        delta: {absolute (late - early), pct_change},
        trend: 'improving' / 'declining' / 'stable',
      }
    """
    coordinator = _coordinator()
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    if early_window < 1 or late_window < 1:
        raise HTTPException(status_code=400, detail="windows must be >= 1")
    if early_window > 100 or late_window > 100:
        raise HTTPException(status_code=400, detail="windows must be <= 100")
    try:
        return coordinator.persistence.cycle_trend_comparison(
            early_window=early_window,
            late_window=late_window,
            metric=metric,
            since_sim_day=since_sim_day,
            until_sim_day=until_sim_day,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/persistence/material-timeseries")
async def get_material_timeseries(
    material_type: Optional[str] = None,
    metric: str = "matched_tons",
    since_sim_day: Optional[int] = None,
    until_sim_day: Optional[int] = None,
    limit: int = 200,
):
    """
    iter #65: Per-material time-series of volume / efficiency metrics.

    Returns one row per cycle × per material with the chosen metric
    (matched_tons / n_matches / avg_match_tons / co2_per_ton / cost_per_ton /
    match_rate). Includes `per_material_summary` dict with mean/min/max/
    latest + trend classification (improving / declining / stable, 10%
    threshold).

    Query:
    - material_type: optional filter (e.g. 'concrete')
    - metric: one of the 6 listed above (default matched_tons)
    - since_sim_day / until_sim_day: optional sim_day window
    - limit: max rows to return (default 200, max 500)
    """
    coordinator = _coordinator()
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    limit = max(1, min(500, limit))
    try:
        return coordinator.persistence.get_material_timeseries(
            material_type=material_type,
            metric=metric,
            since_sim_day=since_sim_day,
            until_sim_day=until_sim_day,
            limit=limit,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/persistence/perturbation-history")
async def get_perturbation_history(
    include_inactive: bool = True,
    since_sim_day: Optional[int] = None,
):
    """
    iter #49: Full perturbation history audit log.

    Lists all perturbations ever created (including deactivated ones),
    sorted by start_sim_day DESC. Useful for ops to see what shocks
    have been applied over time.

    Query:
    - include_inactive: if False, only return active=1
    - since_sim_day: only include perturbations whose window
                     started on or after this sim_day

    Returns:
        {
          n_total, n_active,
          perturbations: [
            {id, label, start_sim_day, end_sim_day, material_type,
             multiplier, active, created_at, duration_sim_days},
            ...
          ],
        }
    """
    coordinator = _coordinator()
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    if since_sim_day is not None and since_sim_day < 0:
        raise HTTPException(status_code=400, detail="since_sim_day must be >= 0")
    perturbations = coordinator.persistence.get_perturbation_history(
        include_inactive=include_inactive, since_sim_day=since_sim_day,
    )
    n_active = sum(1 for p in perturbations if p.get("active"))
    return {
        "n_total": len(perturbations),
        "n_active": n_active,
        "include_inactive": include_inactive,
        "since_sim_day": since_sim_day,
        "perturbations": perturbations,
    }


@router.get("/api/persistence/fleet-utilization-summary")
async def get_fleet_utilization_summary(
    since_sim_day: Optional[int] = None,
    until_sim_day: Optional[int] = None,
):
    """
    iter #49: Fleet utilization percentiles over time.

    Aggregates fleet_utilization_pct across cycles and returns
    distribution stats: mean / median / percentiles (p10-p99) /
    n_idle_cycles (< 25%) / n_busy_cycles (>= 75%) / stddev.

    Useful for fleet ops to spot chronic underutilization or saturation.

    Query:
    - since_sim_day / until_sim_day: optional filter

    Returns:
        {n_cycles, mean, median, p10, p25, p50, p75, p90, p99,
         min, max, stddev, n_idle_cycles, n_busy_cycles,
         since_sim_day, until_sim_day}
    """
    coordinator = _coordinator()
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    if since_sim_day is not None and since_sim_day < 0:
        raise HTTPException(status_code=400, detail="since_sim_day must be >= 0")
    if until_sim_day is not None and until_sim_day < 0:
        raise HTTPException(status_code=400, detail="until_sim_day must be >= 0")
    if since_sim_day is not None and until_sim_day is not None and since_sim_day > until_sim_day:
        raise HTTPException(
            status_code=400,
            detail=f"since_sim_day ({since_sim_day}) > until_sim_day ({until_sim_day})",
        )
    return coordinator.persistence.get_fleet_utilization_summary(
        since_sim_day=since_sim_day, until_sim_day=until_sim_day,
    )


@router.get("/api/persistence/cycle-duration-stats")
async def get_cycle_duration_stats(
    since_sim_day: Optional[int] = None,
    until_sim_day: Optional[int] = None,
):
    """
    Solver wall-time distribution stats (iter #53).

    Aggregates optimization_cycles.wall_duration_ms across cycles.

    Query:
    - since_sim_day: 起始 sim_day (含)
    - until_sim_day: 结束 sim_day (含)

    Returns:
      {
        n_cycles, mean_ms, median_ms, min_ms, max_ms, stddev_ms,
        p25_ms, p50_ms, p75_ms, p90_ms, p95_ms, p99_ms,
        slow_cycles_count (≥5s), fast_cycles_count (≤100ms),
        total_solver_time_seconds,
        since_sim_day, until_sim_day,
      }

    Use cases:
    - Identify slow solver runs (potential OR-Tools issues)
    - Track solver performance trends
    - Capacity planning
    """
    coordinator = _coordinator()
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    if since_sim_day is not None and until_sim_day is not None:
        if since_sim_day > until_sim_day:
            raise HTTPException(
                status_code=400,
                detail="since_sim_day must be <= until_sim_day",
            )
    return coordinator.persistence.get_cycle_duration_stats(
        since_sim_day=since_sim_day, until_sim_day=until_sim_day,
    )


@router.get("/api/persistence/cycle-duration-histogram")
async def get_cycle_duration_histogram(
    since_sim_day: Optional[int] = None,
    until_sim_day: Optional[int] = None,
):
    """
    iter #64: Cycle solver-duration histogram with fixed buckets.

    Same data as /api/persistence/cycle-duration-stats but bucketed for
    visualization. 8 fixed buckets: <100ms, 100-500ms, 0.5-1s, 1-5s,
    5-10s, 10-30s, 30-60s, 60s+. Each bucket reports count + pct_of_total.

    Query:
    - since_sim_day: 起始 sim_day (含)
    - until_sim_day: 结束 sim_day (含)

    Returns:
      {
        n_cycles, n_buckets,
        buckets: [{label, min_ms, max_ms, count, pct}, ...],
        stats: {mean_ms, median_ms, min_ms, max_ms, stddev_ms,
                slow_count, fast_count, total_seconds}
      }

    Use cases:
    - Render histogram chart in dashboard
    - Identify bimodal solver performance (some fast, some slow)
    - Spot outlier runs (60s+ bucket)
    """
    coordinator = _coordinator()
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    if since_sim_day is not None and until_sim_day is not None:
        if since_sim_day > until_sim_day:
            raise HTTPException(
                status_code=400,
                detail="since_sim_day must be <= until_sim_day",
            )
    return coordinator.persistence.get_cycle_duration_histogram(
        since_sim_day=since_sim_day, until_sim_day=until_sim_day,
    )


@router.get("/api/persistence/solver-duration-trend")
async def get_solver_duration_trend(
    window_size: int = 5,
    since_sim_day: Optional[int] = None,
    until_sim_day: Optional[int] = None,
):
    """
    iter #68: Solver duration p50/p95 trend over time windows.

    Complements /api/persistence/cycle-duration-stats (aggregate) and
    /api/persistence/cycle-duration-histogram (distribution) by showing
    how solver wall-time evolves across cycles.

    Each window contains N cycles (default 5, clamped [2, 30]). For each
    window we report p50 / p95 / mean / min / max / stddev wall_duration_ms.
    The overall trend (improving / declining / stable / unknown) compares
    first-half vs second-half median p50, with a 10% threshold to avoid
    noisy classification.

    Query:
    - window_size: cycles per window (default 5, [2, 30])
    - since_sim_day: 起始 sim_day (含)
    - until_sim_day: 结束 sim_day (含)

    Returns:
      {
        n_windows, window_size, n_cycles_evaluated,
        since_sim_day, until_sim_day,
        trend: 'improving' | 'declining' | 'stable' | 'unknown',
        trend_delta_pct, trend_confidence (0-1),
        first_window_p50_ms, last_window_p50_ms,
        windows: [
          {window_index, start_cycle_id, end_cycle_id, start_sim_day,
           end_sim_day, n_cycles, p50_ms, p95_ms, mean_ms,
           min_ms, max_ms, stddev_ms}, ...
        ],
      }

    Use cases:
    - Detect solver performance regression after model changes
    - Visualize wall_duration_ms trend in time-series chart
    - Identify when solver started being slower
    """
    coordinator = _coordinator()
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    if since_sim_day is not None and until_sim_day is not None:
        if since_sim_day > until_sim_day:
            raise HTTPException(
                status_code=400,
                detail="since_sim_day must be <= until_sim_day",
            )
    return coordinator.persistence.get_solver_duration_trend(
        window_size=window_size,
        since_sim_day=since_sim_day, until_sim_day=until_sim_day,
    )


@router.get("/api/persistence/solver-duration-by-season")
async def get_solver_duration_by_season(
    since_sim_day: Optional[int] = None,
    until_sim_day: Optional[int] = None,
):
    """
    iter #69: Solver duration breakdown by season (winter/spring/summer/fall).

    Complements /api/persistence/solver-duration-trend (iter #68, time-based)
    by slicing cycles into 4 seasons based on seasonal_month:

    - winter: Dec (12), Jan (1), Feb (2)
    - spring: Mar (3), Apr (4), May (5)
    - summer: Jun (6), Jul (7), Aug (8)
    - fall:   Sep (9), Oct (10), Nov (11)

    For each season we report wall_duration_ms p50/p95/mean/min/max/stddev
    + solver_status counts (OPTIMAL/FEASIBLE/INFEASIBLE/UNKNOWN), plus
    global stats across all seasons and slowest/fastest season by p50.

    Query:
    - since_sim_day: 起始 sim_day (含)
    - until_sim_day: 结束 sim_day (含)

    Returns:
      {
        n_cycles_evaluated, n_seasons_with_data,
        since_sim_day, until_sim_day,
        seasons: [
          {season, months, n_cycles, p50_ms, p95_ms, mean_ms,
           min_ms, max_ms, stddev_ms,
           solver_status_counts: {OPTIMAL, FEASIBLE, INFEASIBLE, UNKNOWN}},
          ...
        ],
        global_stats: {n_cycles, p50_ms, p95_ms, mean_ms,
                       min_ms, max_ms, stddev_ms},
        slowest_season, fastest_season,
        slowest_p50_ms, fastest_p50_ms, slowest_vs_fastest_pct,
      }

    Use cases:
    - Detect seasonal solver-performance drag (winter road conditions)
    - Compare solver wall-time across Sweden's 4 seasons
    - Spot if INFEASIBLE counts spike in any season (harder instances)
    """
    coordinator = _coordinator()
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    if since_sim_day is not None and until_sim_day is not None:
        if since_sim_day > until_sim_day:
            raise HTTPException(
                status_code=400,
                detail="since_sim_day must be <= until_sim_day",
            )
    return coordinator.persistence.get_solver_duration_by_season(
        since_sim_day=since_sim_day, until_sim_day=until_sim_day,
    )


@router.get("/api/persistence/solver-duration-by-status")
async def get_solver_duration_by_status(
    since_sim_day: Optional[int] = None,
    until_sim_day: Optional[int] = None,
):
    """
    iter #71: Solver wall-time breakdown by solver_status (OPTIMAL / FEASIBLE / INFEASIBLE / UNKNOWN).

    Complements /api/persistence/solver-duration-trend (iter #68, time-based)
    and /api/persistence/solver-duration-by-season (iter #69, season-based)
    by slicing cycles into 4 status buckets. Combined with iter #69, this
    gives a 3D view (time / season / status) of solver performance.

    For each status we report:
      - n_cycles, pct_of_total
      - wall_duration_ms p50 / p95 / mean / min / max / stddev
      - mean_cost_sek, mean_cost_per_ton_sek
      - mean_n_matches, mean_tons

    Plus global stats across all cycles, slowest/fastest status by p50,
    and rate percentages (optimal_rate_pct / feasible_rate_pct /
    infeasible_rate_pct) — these act as SLO-style health metrics.

    Query:
    - since_sim_day: 起始 sim_day (含)
    - until_sim_day: 结束 sim_day (含)

    Returns:
      {
        n_cycles_evaluated, n_statuses_with_data,
        since_sim_day, until_sim_day,
        statuses: [
          {status, n_cycles, pct_of_total,
           p50_ms, p95_ms, mean_ms, min_ms, max_ms, stddev_ms,
           mean_cost_sek, mean_cost_per_ton_sek,
           mean_n_matches, mean_tons}, ...
        ],
        global_stats: {n_cycles, p50_ms, p95_ms, mean_ms,
                       min_ms, max_ms, stddev_ms},
        slowest_status, fastest_status,
        slowest_p50_ms, fastest_p50_ms, slowest_vs_fastest_pct,
        infeasible_rate_pct, feasible_rate_pct, optimal_rate_pct,
      }

    Use cases:
    - Detect INFEASIBLE cycles that exhaust solver search budget (slow p50)
    - Compare cost-per-ton across solver outcomes
    - Compute infeasible_rate_pct as an SLO-style health metric
    - Spot if FEASIBLE cycles are costlier than OPTIMAL (solver compromised)
    """
    coordinator = _coordinator()
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    if since_sim_day is not None and until_sim_day is not None:
        if since_sim_day > until_sim_day:
            raise HTTPException(
                status_code=400,
                detail="since_sim_day must be <= until_sim_day",
            )
    return coordinator.persistence.get_solver_duration_by_status(
        since_sim_day=since_sim_day, until_sim_day=until_sim_day,
    )


@router.get("/api/persistence/solver-duration-by-season-status")
async def get_solver_duration_by_season_status(
    since_sim_day: Optional[int] = None,
    until_sim_day: Optional[int] = None,
):
    """
    iter #72: Solver wall-time 3D heatmap (season × status).

    Complements /api/persistence/solver-duration-by-season (iter #69) and
    /api/persistence/solver-duration-by-status (iter #71) by slicing
    cycles along BOTH axes simultaneously — a 4×4 = 16-cell matrix
    (winter/spring/summer/fall × OPTIMAL/FEASIBLE/INFEASIBLE/UNKNOWN).

    Each cell reports n_cycles + p50/p95/mean/min/max/stddev + mean_cost
    + cost_per_ton + pct_of_total. Cells with no data are returned with
    null values (not omitted) so the frontend can render a stable 16-cell
    heatmap.

    Also exposes:
      - season_rollup / status_rollup (1D marginals)
      - global_stats across the entire matrix
      - slowest_cell / fastest_cell (season, status) by p50
      - top_5_slowest_cells (hotspot table for ops)

    Query:
    - since_sim_day: 起始 sim_day (含)
    - until_sim_day: 结束 sim_day (含)

    Returns:
      {
        n_cycles_evaluated, n_cells_with_data,
        since_sim_day, until_sim_day,
        seasons: ["winter", "spring", "summer", "fall"],
        statuses: ["OPTIMAL", "FEASIBLE", "INFEASIBLE", "UNKNOWN"],
        cells: [
          {season, status, n_cycles, pct_of_total,
           p50_ms, p95_ms, mean_ms, min_ms, max_ms, stddev_ms,
           mean_cost_sek, mean_cost_per_ton_sek}, ...
        ],
        season_rollup: [{season, n_cycles, p50_ms, mean_ms}, ...],
        status_rollup:  [{status, n_cycles, p50_ms, mean_ms}, ...],
        global_stats: {n_cycles, p50_ms, p95_ms, mean_ms,
                       min_ms, max_ms, stddev_ms},
        slowest_cell: {season, status, p50_ms} | null,
        fastest_cell: {season, status, p50_ms} | null,
        slowest_vs_fastest_pct: float | null,
        top_5_slowest_cells: [
          {season, status, n_cycles, p50_ms, mean_ms}, ...
        ],
      }

    Use cases:
    - Spot "winter INFEASIBLE" hotspot — slowest (season, status) pair
    - Detect "summer OPTIMAL" sweet spot — fastest (season, status) pair
    - Render 3D heatmap with color = p50 and size = n_cycles
    - Find top-5 problematic combinations for ops triage
    """
    coordinator = _coordinator()
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    if since_sim_day is not None and until_sim_day is not None:
        if since_sim_day > until_sim_day:
            raise HTTPException(
                status_code=400,
                detail="since_sim_day must be <= until_sim_day",
            )
    return coordinator.persistence.get_solver_duration_by_season_status(
        since_sim_day=since_sim_day, until_sim_day=until_sim_day,
    )


@router.get("/api/persistence/cycle-duration-by-problem-size")
async def get_cycle_duration_by_problem_size(
    since_sim_day: Optional[int] = None,
    until_sim_day: Optional[int] = None,
):
    """
    iter #67: Solver duration + efficiency breakdown by problem size.

    Buckets cycles by ``total_tons`` into small (<10), medium (10-50),
    large (50-200), xlarge (>=200). For each bucket we report wall-time
    stats (mean / median / min / max / stddev), cost stats, cost-per-ton
    stats, distance, n_matches, solver status breakdown, and the
    dominant material_type.

    Also computes a ``scaling_signal``:
    - linear     duration grows proportionally to tonnage
    - superlinear duration grows faster than tonnage (red flag)
    - sublinear  duration barely grows (good — solver scales well)
    - unknown    not enough data to compare buckets

    Query:
    - since_sim_day: 起始 sim_day (含)
    - until_sim_day: 结束 sim_day (含)

    Returns:
      {
        n_cycles, n_buckets, since_sim_day, until_sim_day,
        bucket_bounds: [{label, min_tons, max_tons, open_upper_bound}, ...],
        buckets: [{label, min_tons, max_tons, count, pct_of_total,
                   duration_ms, cost_sek, cost_per_ton_sek, distance_km,
                   n_matches, solver_status_counts, dominant_material}, ...],
        scaling_signal: "linear" | "superlinear" | "sublinear" | "unknown"
      }

    Use cases:
    - Spot solver scaling bottlenecks (large buckets disproportionately slow)
    - Compare cost/ton efficiency across problem sizes
    - Identify which buckets have infeasible solver runs
    """
    coordinator = _coordinator()
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    if since_sim_day is not None and until_sim_day is not None:
        if since_sim_day > until_sim_day:
            raise HTTPException(
                status_code=400,
                detail="since_sim_day must be <= until_sim_day",
            )
    return coordinator.persistence.get_cycle_duration_by_problem_size(
        since_sim_day=since_sim_day, until_sim_day=until_sim_day,
    )


@router.get("/api/persistence/match-distance-buckets")
async def get_match_distance_buckets(
    since_sim_day: Optional[int] = None,
    until_sim_day: Optional[int] = None,
):
    """
    Match distance distribution histogram (iter #53).

    Aggregates matches.distance_km into 8 buckets (0-5, 5-10, 10-25, 25-50,
    50-100, 100-200, 200-500, 500+ km).

    Query:
    - since_sim_day: 起始 sim_day (含)
    - until_sim_day: 结束 sim_day (含)

    Returns:
      {
        total_matches, total_distance_km, avg_distance_km,
        median_distance_km, p95_distance_km,
        buckets: [{label, lower_km, upper_km, count, share}, ...],
        since_sim_day, until_sim_day,
      }

    Use cases:
    - Identify long-distance matches (CO2 hotspots)
    - Validate regional logistics patterns
    """
    coordinator = _coordinator()
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    if since_sim_day is not None and until_sim_day is not None:
        if since_sim_day > until_sim_day:
            raise HTTPException(
                status_code=400,
                detail="since_sim_day must be <= until_sim_day",
            )
    return coordinator.persistence.get_match_distance_buckets(
        since_sim_day=since_sim_day, until_sim_day=until_sim_day,
    )


@router.get("/api/persistence/cohort-by-hour")
async def get_cohort_by_hour(
    since_sim_day: Optional[int] = None,
    until_sim_day: Optional[int] = None,
):
    """
    Cohort analysis grouped by sim_hour (iter #53).

    Groups cycles by hour of day (0-23) and returns per-hour KPI aggregates.

    Query:
    - since_sim_day, until_sim_day: optional time window

    Returns:
      {
        n_hours_buckets, n_cycles,
        hour_buckets: [
          {hour, hour_label, time_of_day ('night'|'morning'|'afternoon'|'evening'),
           n_cycles, total_tons, total_cost_sek, total_co2_kg,
           avg_matches_per_cycle, avg_fleet_utilization_pct},
          ...
        ]
      }

    Use cases:
    - Identify peak simulation hours
    - Detect off-peak patterns
    """
    coordinator = _coordinator()
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    if since_sim_day is not None and until_sim_day is not None:
        if since_sim_day > until_sim_day:
            raise HTTPException(
                status_code=400,
                detail="since_sim_day must be <= until_sim_day",
            )
    return coordinator.persistence.get_cohort_by_hour_of_day(
        since_sim_day=since_sim_day, until_sim_day=until_sim_day,
    )


@router.get("/api/persistence/carbon-savings")
async def get_carbon_savings(
    since_sim_day: Optional[int] = None,
    until_sim_day: Optional[int] = None,
    baseline_factor_key: str = "traditional_baseline",
):
    """
    Carbon savings vs traditional baseline transport (iter #54).

    Compares actual CO2 emitted by the multi-agent optimization system
    vs a hypothetical baseline where all material was transported by
    individual trucks without optimization.

    Query:
    - since_sim_day: optional start sim_day
    - until_sim_day: optional end sim_day
    - baseline_factor_key: CO2 factor to compare against. Default
      'traditional_baseline' (0.124 kg/ton-km, ~50% empty trucks).
      Other options: 'truck_heavy', 'truck_medium', 'truck_light',
      'optimized_fleet'.

    Returns:
      {
        n_cycles, total_tons, total_distance_km, actual_co2_kg,
        baseline_co2_kg, savings_co2_kg, savings_pct,
        baseline_factor_kg_per_ton_km, baseline_factor_key,
        co2_per_ton_actual_kg, co2_per_ton_baseline_kg,
        available_factors,
        since_sim_day, until_sim_day,
      }

    Source for emission factors: EEA 2023, well-to-wheel estimates.
    """
    coordinator = _coordinator()
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    if since_sim_day is not None and until_sim_day is not None:
        if since_sim_day > until_sim_day:
            raise HTTPException(
                status_code=400,
                detail="since_sim_day must be <= until_sim_day",
            )
    try:
        return coordinator.persistence.get_carbon_savings_summary(
            since_sim_day=since_sim_day,
            until_sim_day=until_sim_day,
            baseline_factor_key=baseline_factor_key,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/persistence/top-suppliers")
async def get_top_suppliers(
    metric: str = "co2_per_ton",
    material_type: Optional[str] = None,
    limit: int = 10,
):
    """
    Top suppliers ranked by efficiency metrics (iter #55).

    Query:
    - metric: which efficiency to rank by. One of:
      - 'co2_per_ton' (kg CO2 / ton delivered, lower = better)
      - 'cost_per_ton' (SEK / ton delivered, lower = better)
      - 'co2_per_match' (kg CO2 / match, lower = better)
      - 'match_rate' (matches / cycle, higher = better)
      - 'avg_distance' (avg km per match, lower = better)
    - material_type: optional filter (e.g., 'concrete', 'metal_scrap')
    - limit: top N (default 10, max 100)

    Returns:
      {
        metric, metric_description, direction ('lower_is_better' / 'higher_is_better'),
        n_suppliers_evaluated, n_suppliers_returned,
        top_suppliers: [{supply_id, material_type, value, n_matches,
                          total_matched_tons, total_available_tons,
                          avg_quality_score, n_cycles_with_supply,
                          avg_distance_km}, ...]
      }

    Use cases:
    - Identify greenest suppliers (low co2_per_ton)
    - Identify most cost-efficient (low cost_per_ton)
    - Identify most reliable (high match_rate)
    - Identify shortest-distance matches
    """
    coordinator = _coordinator()
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    try:
        return coordinator.persistence.get_top_suppliers_by_efficiency(
            metric=metric, material_type=material_type, limit=limit,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/persistence/top-vehicles")
async def get_top_vehicles(
    metric: str = "co2_per_ton_km",
    limit: int = 10,
):
    """
    Top vehicles ranked by efficiency metrics (iter #55).

    Query:
    - metric: which efficiency to rank by. One of:
      - 'co2_per_ton_km' (kg CO2 / (ton × km), lower = better — greenest)
      - 'co2_per_km' (kg CO2 / km, lower = better)
      - 'cost_per_km' (SEK / km, lower = better)
      - 'utilization' (% of capacity used, higher = better)
    - limit: top N (default 10, max 100)

    Returns:
      {
        metric, metric_description, direction,
        n_vehicles_evaluated, n_vehicles_returned,
        top_vehicles: [{vehicle_id, value, n_routes, total_distance_km,
                        total_co2_kg, total_cost_sek, total_duration_hours,
                        avg_co2_per_km_kg, avg_cost_per_km_sek}, ...]
      }

    Use cases:
    - Identify greenest vehicles (low co2_per_ton_km)
    - Identify most cost-efficient (low cost_per_km)
    - Identify best-utilized (high utilization)
    """
    coordinator = _coordinator()
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")

    valid_metrics = {
        "co2_per_ton_km": ("lower_is_better",
                           "kg CO2 per (ton × km) — greenest"),
        "co2_per_km": ("lower_is_better", "kg CO2 per km"),
        "cost_per_km": ("lower_is_better", "SEK per km"),
        "utilization": ("higher_is_better", "avg fleet utilization %"),
    }
    if metric not in valid_metrics:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown metric {metric!r}. Valid: {list(valid_metrics)}",
        )

    direction, _desc = valid_metrics[metric]

    vehicle_stats = coordinator.persistence.get_vehicle_stats(limit=max(100, limit))

    # Compute per-metric value
    for v in vehicle_stats:
        total_dist = v.get("total_distance_km") or 0
        total_co2 = v.get("total_co2_kg") or 0
        total_cost = v.get("total_cost_sek") or 0
        # Estimate total tons: routes 表里没有 total_tons，
        # 用车队利用率当「有效运力」的代理指标。
        util = v.get("avg_fleet_utilization") or 0
        estimated_tons = total_dist * util / 100.0  # rough estimate
        if metric == "co2_per_ton_km":
            v["value"] = round(total_co2 / estimated_tons, 4) if estimated_tons > 0 else None
        elif metric == "co2_per_km":
            v["value"] = round(total_co2 / total_dist, 4) if total_dist > 0 else None
        elif metric == "cost_per_km":
            v["value"] = round(total_cost / total_dist, 4) if total_dist > 0 else None
        elif metric == "utilization":
            v["value"] = round(util, 2)

    # Filter out vehicles without data for the metric
    filtered = [v for v in vehicle_stats if v.get("value") is not None]

    # Sort by value
    if direction == "lower_is_better":
        filtered.sort(key=lambda x: x["value"])
    else:
        filtered.sort(key=lambda x: -x["value"])

    return {
        "metric": metric,
        "metric_description": _desc,
        "direction": direction,
        "n_vehicles_evaluated": len(filtered),
        "n_vehicles_returned": min(limit, len(filtered)),
        "top_vehicles": filtered[:max(1, min(100, limit))],
    }


@router.get("/api/persistence/top-cycles")
async def get_top_cycles(
    metric: str = "co2_per_ton",
    since_sim_day: Optional[int] = None,
    until_sim_day: Optional[int] = None,
    min_matches: int = 1,
    limit: int = 10,
):
    """
    Top optimization cycles ranked by efficiency metrics (iter #56).

    Query:
    - metric: which efficiency to rank by. One of:
      - 'co2_per_ton' (kg CO2 / ton delivered, lower = better)
      - 'cost_per_ton' (SEK / ton delivered, lower = better)
      - 'co2_per_km' (kg CO2 / km, lower = better)
      - 'cost_per_km' (SEK / km, lower = better)
      - 'fleet_utilization' (% capacity used, higher = better)
      - 'match_rate_vs_offers' (matches / supply offers, higher = better)
      - 'tons_per_cycle' (tons matched, higher = better)
    - since_sim_day: optional lower bound (inclusive) on sim_day
    - until_sim_day: optional upper bound (inclusive) on sim_day
    - min_matches: skip cycles with fewer than this many matches (default 1)
    - limit: top N (default 10, max 100)

    Returns:
      {
        metric, metric_description, direction ('lower_is_better' / 'higher_is_better'),
        n_cycles_evaluated, n_cycles_returned,
        sim_day_window: {since_sim_day, until_sim_day},
        top_cycles: [{cycle_id, sim_day, sim_hour, wall_timestamp,
                      value, n_matches, n_supply_offers, n_demand_requests,
                      total_tons, total_cost_sek, total_co2_kg,
                      total_distance_km, fleet_utilization_pct,
                      solver_status, wall_duration_ms}, ...]
      }

    Use cases:
    - Identify greenest cycles (low co2_per_ton)
    - Identify most cost-efficient cycles (low cost_per_ton)
    - Identify best-utilized cycles (high fleet_utilization)
    - Identify most productive cycles (high tons_per_cycle)
    """
    coordinator = _coordinator()
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    try:
        return coordinator.persistence.get_top_cycles_by_efficiency(
            metric=metric,
            since_sim_day=since_sim_day,
            until_sim_day=until_sim_day,
            min_matches=min_matches,
            limit=limit,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/persistence/supply-aggregates")
async def get_supply_aggregates(
    supply_id: Optional[str] = None,
    material_type: Optional[str] = None,
    limit: int = 100,
):
    """
    Supply 聚合统计 (iter #15) — 每个 supply_id 的累计 KPI。

    Query:
    - supply_id: 可选, 查单个 supply
    - material_type: 可选, 按 material_type 过滤
    - limit: 最多返回多少 supply (default 100, max 500)

    Returns:
        [{supply_id, material_type, n_cycles_with_supply,
          total_available_tons, total_matched_tons, avg_quality_score,
          n_matches, last_cycle_id, first_cycle_id}, ...]
    """
    coordinator = _coordinator()
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    limit = max(1, min(500, limit))
    return coordinator.persistence.get_supply_aggregates(
        supply_id=supply_id,
        material_type=material_type,
        limit_supplies=limit,
    )


@router.get("/api/persistence/top-demands")
async def get_top_demands(
    metric: str = "fulfillment_rate",
    material_type: Optional[str] = None,
    min_required_tons: float = 0.0,
    limit: int = 10,
):
    """
    Top demands ranked by fulfillment metrics (iter #57).

    Query:
    - metric: which fulfillment metric to rank by. One of:
      - 'fulfillment_rate' (matched/required, higher = better, capped at 2.0)
      - 'total_matched_tons' (higher = better)
      - 'unmet_demand_tons' (required - matched, lower = better — gap analysis)
      - 'match_rate' (matches per cycle, higher = better)
      - 'avg_match_distance_km' (lower = better)
    - material_type: optional filter
    - min_required_tons: skip demands with less than this required tonnage
    - limit: top N (default 10, max 100)

    Returns:
      {
        metric, metric_description, direction,
        n_demands_evaluated, n_demands_returned,
        top_demands: [{demand_id, material_type, value, ...}, ...]
      }

    Use cases:
    - Identify best-served demands (high fulfillment_rate)
    - Identify biggest unmet gaps (high unmet_demand_tons)
    - Identify hardest-to-reach (high avg_match_distance_km)
    """
    coordinator = _coordinator()
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    try:
        return coordinator.persistence.get_top_demands_by_fulfillment(
            metric=metric,
            material_type=material_type,
            min_required_tons=min_required_tons,
            limit=limit,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/persistence/top-facilities")
async def get_top_facilities(
    metric: str = "avg_distance",
    facility_ids: Optional[str] = None,
    city: Optional[str] = None,
    facility_type: Optional[str] = None,
    material_type: Optional[str] = None,
    since_sim_day: Optional[int] = None,
    until_sim_day: Optional[int] = None,
    limit: int = 10,
):
    """
    Top real-Sweden facilities ranked by supply-distance metrics (iter #59).

    Real Sweden facilities (13 hand-curated — Renova, Stena, Suez, harbor_cargo,
    recycling_center, paper_mill, etc.) are used as demand_ids. Each match links
    a supply to a facility, so we can aggregate per-facility supply-haul metrics.

    Query:
    - metric: which facility-distance metric to rank by. One of:
      - 'avg_distance' (avg km per match, lower = better — closest suppliers)
      - 'min_distance' (shortest match, lower = better)
      - 'max_distance' (longest match, lower = better)
      - 'total_matched_tons' (higher = better)
      - 'match_count' (higher = better)
      - 'match_rate' (matches per cycle, higher = better)
      - 'utilization_pct' (matched / capacity * 100, higher = better)
      - 'co2_per_ton' (kg CO2 / matched ton, lower = better)
    - facility_ids: comma-separated list (e.g. "GBG_RENOVA_SYA,GBG_HARBOR")
    - city: optional filter (Göteborg / Borås / Stockholm)
    - facility_type: optional filter (recycling_center / metal_recovery / etc)
    - material_type: optional material filter
    - since_sim_day, until_sim_day: optional sim_day window
    - limit: top N (default 10, max 100)

    Returns:
      {
        metric, metric_description, direction,
        filter: {city, facility_type, material_type, facility_ids, sim_day_window},
        n_facilities_evaluated, n_facilities_returned,
        top_facilities: [{facility_id, name, city, facility_type,
                          processing_capacity_tons_per_day, operator, source,
                          value, n_matches, n_cycles_with_demand,
                          total_matched_tons, total_required_tons,
                          avg_match_distance_km, min_match_distance_km,
                          max_match_distance_km, avg_match_tons,
                          utilization_pct, last_sim_day}, ...]
      }

    Use cases:
    - Identify facilities served by closest suppliers (low avg_distance)
    - Identify most-supplied facilities (high total_matched_tons)
    - Identify highest utilization vs declared capacity
    - Identify hardest-to-reach facilities (high avg_distance)
    - Geographic analysis: filter by city to compare regional performance
    """
    coordinator = _coordinator()
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")

    # Parse facility_ids CSV → List[str]
    facility_id_list: Optional[List[str]] = None
    if facility_ids:
        facility_id_list = [s.strip() for s in facility_ids.split(",") if s.strip()]
        if not facility_id_list:
            raise HTTPException(
                status_code=400,
                detail="facility_ids must be non-empty comma-separated IDs",
            )

    # Validate sim_day window
    if since_sim_day is not None and until_sim_day is not None:
        if since_sim_day > until_sim_day:
            raise HTTPException(
                status_code=400,
                detail=f"since_sim_day ({since_sim_day}) > until_sim_day ({until_sim_day})",
            )

    try:
        return coordinator.persistence.get_top_facilities_by_distance(
            metric=metric,
            facility_ids=facility_id_list,
            city=city,
            facility_type=facility_type,
            material_type=material_type,
            since_sim_day=since_sim_day,
            until_sim_day=until_sim_day,
            limit=limit,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/persistence/top-routes")
async def get_top_routes(
    metric: str = "co2_per_km",
    vehicle_id: Optional[str] = None,
    since_sim_day: Optional[int] = None,
    until_sim_day: Optional[int] = None,
    limit: int = 10,
):
    """
    iter #63: Top routes (per cycle × per vehicle) ranked by efficiency metrics.

    Each row in the routes table is a single vehicle's haul in a single cycle.
    This endpoint ranks those route rows by efficiency metrics so users can
    identify the greenest, cheapest, fastest, or most-loaded routes.

    Query:
    - metric (default co2_per_km): one of
        co2_per_km | co2_per_hour | cost_per_km | cost_per_hour |
        speed_km_per_hour | distance | tons_per_km
    - vehicle_id: optional filter (e.g. 'TRUCK_001')
    - since_sim_day / until_sim_day: optional sim_day window
    - limit (default 10, max 100): number of routes to return
    """
    coordinator = _coordinator()
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    limit = max(1, min(100, limit))
    try:
        return coordinator.persistence.get_top_routes_by_efficiency(
            metric=metric,
            vehicle_id=vehicle_id,
            since_sim_day=since_sim_day,
            until_sim_day=until_sim_day,
            limit=limit,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/persistence/top-materials")
async def get_top_materials(
    metric: str = "total_matched_tons",
    since_sim_day: Optional[int] = None,
    until_sim_day: Optional[int] = None,
    limit: int = 10,
):
    """
    iter #63: Top materials ranked by volume / efficiency metrics.

    Aggregates per material_type across supply_offers, demand_requests,
    matches and routes. Useful for identifying dominant materials, bottlenecks
    (low match_rate), and the cleanest / most cost-efficient materials.

    Query:
    - metric (default total_matched_tons): one of
        total_matched_tons | total_supply_tons | total_demand_tons |
        n_matches | n_supply_offers | n_demand_requests |
        match_rate | avg_tons_per_match |
        co2_per_ton | cost_per_ton | avg_distance_km
    - since_sim_day / until_sim_day: optional sim_day window
    - limit (default 10, max 50): number of materials to return
    """
    coordinator = _coordinator()
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    limit = max(1, min(50, limit))
    try:
        return coordinator.persistence.get_top_materials_by_volume(
            metric=metric,
            since_sim_day=since_sim_day,
            until_sim_day=until_sim_day,
            limit=limit,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/persistence/compare-cycles")
async def get_compare_cycles(
    cycle_id_a: str = Query(..., description="First cycle_id (e.g. 'OPT0001')"),
    cycle_id_b: str = Query(..., description="Second cycle_id (e.g. 'OPT0005')"),
):
    """
    Side-by-side comparison of two optimization cycles (iter #59).

    Compares cycle_a and cycle_b on:
    - Core KPIs (matches, tons, cost, CO2, distance, fleet utilization)
    - Derived metrics (cost_per_ton, co2_per_ton, cost_per_km, co2_per_km,
      avg_tons_per_match)
    - Seasonal context (seasonal_factor_avg, perturbation_count, etc)
    - Absolute + pct differences (b - a)
    - Winner on 5 key axes (lowest CO2/ton, lowest cost/ton, highest utilization,
      most matches, most tons)

    Query:
    - cycle_id_a: required, first cycle
    - cycle_id_b: required, second cycle

    Returns:
      {
        cycle_a: {cycle_id, sim_day, ...all KPIs and derived metrics...},
        cycle_b: {cycle_id, ...},
        differences: {absolute: {...}, pct_change: {...}},
        winner: {
          lowest_co2_per_ton_kg: {cycle_id, direction, by_abs, by_pct, a_value, b_value},
          lowest_cost_per_ton_sek: {...},
          highest_fleet_utilization_pct: {...},
          most_matches: {...},
          most_tons: {...}
        } | None
      }

    Either cycle may be missing (returns null for that side); differences
    and winner are null/empty in that case.

    Use cases:
    - Compare greenest vs worst cycle (invoke with cycle_id_a = best, cycle_id_b = worst)
    - A/B test solver changes
    - Before/after perturbation impact
    - Seasonal comparison (winter vs summer cycle)
    """
    coordinator = _coordinator()
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")

    if not cycle_id_a or not cycle_id_b:
        raise HTTPException(
            status_code=400,
            detail="Both cycle_id_a and cycle_id_b are required",
        )
    if cycle_id_a == cycle_id_b:
        raise HTTPException(
            status_code=400,
            detail="cycle_id_a and cycle_id_b must be different",
        )

    return coordinator.persistence.compare_cycles(
        cycle_id_a=cycle_id_a,
        cycle_id_b=cycle_id_b,
    )


@router.get("/api/persistence/compare-materials")
async def get_compare_materials(
    material_a: str = Query(..., description="First material_type (e.g. 'concrete')"),
    material_b: str = Query(..., description="Second material_type (e.g. 'metal_scrap')"),
    since_sim_day: Optional[int] = None,
    until_sim_day: Optional[int] = None,
):
    """
    iter #64: Side-by-side comparison of two materials.

    Compares two material_types on volume / activity / efficiency / sustainability
    metrics. Returns each material's full KPIs + absolute + pct differences
    + 5-axis winner (lowest CO₂/ton, lowest cost/ton, most matches,
    highest match rate, highest demand fulfillment).

    Query:
    - material_a (required): first material_type
    - material_b (required): second material_type (must differ from a)
    - since_sim_day / until_sim_day: optional sim_day window
    """
    coordinator = _coordinator()
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    if not material_a or not material_b:
        raise HTTPException(
            status_code=400,
            detail="Both material_a and material_b are required",
        )
    if material_a == material_b:
        raise HTTPException(
            status_code=400,
            detail="material_a and material_b must be different",
        )

    return coordinator.persistence.compare_materials(
        material_a=material_a,
        material_b=material_b,
        since_sim_day=since_sim_day,
        until_sim_day=until_sim_day,
    )


@router.get("/api/persistence/compare-routes")
async def get_compare_routes(
    route_id_a: int = Query(..., description="First route_id (e.g. 9)"),
    route_id_b: int = Query(..., description="Second route_id (e.g. 45)"),
):
    """
    iter #64: Side-by-side comparison of two specific route rows.

    Compares two routes on distance / duration / cost / co2 / cycle_tons /
    derived metrics (co2_per_km, co2_per_hour, cost_per_km, cost_per_hour,
    speed_km_per_hour) + absolute + pct differences + 5-axis winner
    (lowest CO₂/km, lowest CO₂/hour, lowest cost/km, highest speed,
    lowest duration).

    Query:
    - route_id_a (required): first route's primary key
    - route_id_b (required): second route's primary key (must differ from a)
    """
    coordinator = _coordinator()
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    if route_id_a == route_id_b:
        raise HTTPException(
            status_code=400,
            detail="route_id_a and route_id_b must be different",
        )
    return coordinator.persistence.compare_routes(
        route_id_a=route_id_a,
        route_id_b=route_id_b,
    )


@router.get("/api/persistence/demand-aggregates")
async def get_demand_aggregates(
    demand_id: Optional[str] = None,
    material_type: Optional[str] = None,
    limit: int = 100,
):
    """
    Demand 聚合统计 (iter #52) — 每个 demand_id 的累计 KPI。

    Mirror of /api/persistence/supply-aggregates on demand side:
    - top demands by required_tons (which demand sites require most?)
    - fulfillment_rate = matched / required (demand met or unmet?)
    - per-material demand patterns

    Query:
    - demand_id: 可选, 查单个 demand
    - material_type: 可选, 按 material_type 过滤
    - limit: 最多返回多少 demand (default 100, max 500)

    Returns:
        [{demand_id, material_type, n_cycles_with_demand,
          total_required_tons, total_matched_tons, fulfillment_rate,
          avg_required_tons, n_matches, avg_match_tons,
          last_cycle_id, first_cycle_id, last_sim_day, first_sim_day}, ...]

    fulfillment_rate: 0.0 (fully unmet) → 1.0 (perfectly met),
    clamped to [0.0, 2.0] (rarely >1 = oversupply).
    """
    coordinator = _coordinator()
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    limit = max(1, min(500, limit))
    return coordinator.persistence.get_demand_aggregates(
        demand_id=demand_id,
        material_type=material_type,
        limit_demands=limit,
    )


@router.get("/api/persistence/material-aggregates")
async def get_material_aggregates(
    material_type: Optional[str] = None,
    limit: int = 50,
):
    """
    Material type 聚合统计 (iter #16) — 每个 material_type 的累计 KPI。

    和 supply-aggregates 类似, 但按 material_type 维度聚合:
    - 哪些材料最常被生成 (建筑废料? 金属? 混合废料?)
    - 哪些材料匹配率最高
    - 哪些材料运输距离最长

    Query:
    - material_type: 可选, 只查某个 material
    - limit: 最多返回多少 material (default 50, max 200)

    Returns:
        [{material_type, n_supply_offers, n_cycles_with_material,
          n_distinct_supplies, total_available_tons, total_matched_tons,
          avg_quality_score, n_matches, avg_match_distance_km,
          max_match_distance_km, match_rate_pct}, ...]
    """
    coordinator = _coordinator()
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    limit = max(1, min(200, limit))
    return coordinator.persistence.get_material_aggregates(
        material_type=material_type,
        limit=limit,
    )


@router.get("/api/persistence/material-supply-demand-balance")
async def get_material_supply_demand_balance(
    since_sim_day: Optional[int] = None,
    until_sim_day: Optional[int] = None,
):
    """
    iter #48: Material supply vs demand balance.

    For each material_type, computes:
    - total_supply_tons: from supply_offers
    - total_demand_tons: from demand_requests
    - total_matched_tons: from matches
    - supply_demand_ratio: matched / supply
    - demand_fulfillment_pct: matched / demand
    - excess_supply_tons: supply - matched
    - unmet_demand_tons: demand - matched

    Useful for identifying materials with chronic oversupply or shortage.

    Returns:
        {
          since_sim_day, until_sim_day,
          by_material: [
            {material_type, total_supply_tons, total_demand_tons,
             total_matched_tons, supply_demand_ratio,
             demand_fulfillment_pct, excess_supply_tons,
             unmet_demand_tons, n_supply_offers, n_demand_requests,
             n_matches},
            ...
          ],
        }
    """
    coordinator = _coordinator()
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    if since_sim_day is not None and since_sim_day < 0:
        raise HTTPException(status_code=400, detail="since_sim_day must be >= 0")
    if until_sim_day is not None and until_sim_day < 0:
        raise HTTPException(status_code=400, detail="until_sim_day must be >= 0")
    if since_sim_day is not None and until_sim_day is not None and since_sim_day > until_sim_day:
        raise HTTPException(
            status_code=400,
            detail=f"since_sim_day ({since_sim_day}) > until_sim_day ({until_sim_day})",
        )
    return {
        "since_sim_day": since_sim_day,
        "until_sim_day": until_sim_day,
        "by_material": coordinator.persistence.get_material_supply_demand_balance(
            since_sim_day=since_sim_day, until_sim_day=until_sim_day,
        ),
    }


@router.get("/api/persistence/supply-cohort-retention")
async def get_supply_cohort_retention(material_type: Optional[str] = None):
    """
    Supply 留存分析 (iter #17) — 哪些 supply 点反复出现 vs 一次性出现。

    指标:
    - retention_rate_pct: 出现 ≥2 次的 supply 占比
    - one_time_pct: 只出现 1 次的 supply 占比
    - by_appearance_count: 按出现次数分布 (1 / 2 / 3-5 / 6-10 / 11+)

    Query:
    - material_type: 可选, 只查某种 material 的 supply 留存

    Returns:
        {
          total_supply_ids, n_one_time, n_repeating,
          retention_rate_pct, one_time_pct,
          by_appearance_count: [{label, n_supplies, pct}, ...],
          total_supply_offers, total_cycles_with_supply,
          material_type_filter,
        }
    """
    coordinator = _coordinator()
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    return coordinator.persistence.get_supply_cohort_retention(
        material_type=material_type,
    )


@router.get("/api/persistence/cohort-retention-by-material")
async def get_cohort_retention_by_material():
    """
    iter #42: Per-material supply retention breakdown.

    For each material_type, return the same retention metrics as
    /api/persistence/supply-cohort-retention. Lets operators see
    which materials have stable vs volatile supply sources (e.g.,
    wood retention 90% = recurring lumber suppliers; e-waste retention
    30% = one-off decommissioning projects).

    Returns:
        {
            n_materials: int,
            by_material: [{
                material_type: str,
                total_supply_ids: int,
                n_one_time: int,
                n_repeating: int,
                retention_rate_pct: float,
                one_time_pct: float,
                total_supply_offers: int,
                total_cycles_with_supply: int,
            }, ...]
        }
    """
    coordinator = _coordinator()
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    by_material = coordinator.persistence.get_cohort_retention_by_material()
    return {
        "n_materials": len(by_material),
        "by_material": by_material,
    }


@router.get("/api/persistence/cohort-retention-by-period")
async def get_cohort_retention_by_period(
    n_periods: int = 4,
    period_unit: str = "quartile",
    material_type: Optional[str] = None,
):
    """
    Supply 留存按时段划分 (iter #19 + iter #24 时间窗口扩展) — 早期 vs 后期 retention 对比。

    将所有 cycle 按 sim_day 顺序划分成多个 period, 每段独立计算 retention rate,
    让用户看 早期 vs 后期 churn 趋势。

    Query (iter #24 加 period_unit + iter #45 加 material_type):
    - n_periods: 分多少段 (default 4)
      - quartile: range 1-10
      - day:      range 1-30
      - week:     range 1-52
      - month:    range 1-12
    - period_unit: 划分单位 (default 'quartile')
      - quartile: equal-split by sim_day range (原 iter #19 行为)
      - day:      每段 = 1 sim_day
      - week:     每段 = 7 sim_days
      - month:    每段 = 30 sim_days
    - material_type: iter #45 加 — filter to single material (e.g. 'concrete')

    Returns:
        {
          total_supply_ids, n_periods, period_unit,  # period_unit iter #24
          material_type_filter,                       # iter #45
          period_labels, periods: [...],
          trend: "improving" | "declining" | "stable" | "unknown"
        }
    """
    coordinator = _coordinator()
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")

    # iter #24: validate period_unit early (return 400 not 500)
    valid_units = ("quartile", "day", "week", "month")
    if period_unit not in valid_units:
        raise HTTPException(
            status_code=400,
            detail=f"period_unit must be one of {list(valid_units)}, got '{period_unit}'",
        )

    # iter #24: per-unit max validation
    max_periods_map = {"quartile": 10, "day": 30, "week": 52, "month": 12}
    max_n = max_periods_map[period_unit]
    if n_periods < 1 or n_periods > max_n:
        raise HTTPException(
            status_code=400,
            detail=f"n_periods must be 1-{max_n} for period_unit='{period_unit}', got {n_periods}",
        )

    return coordinator.persistence.get_cohort_retention_by_period(
        n_periods=n_periods,
        period_unit=period_unit,
        material_type=material_type,
    )


@router.get("/api/persistence/cohort-retention-by-season")
async def get_cohort_retention_by_season(
    material_type: Optional[str] = None,
):
    """
    iter #70: Cohort retention broken down by SEASON (winter/spring/summer/fall).

    跟 iter #19 (by-period) 互补 — 时间窗切时间维度, season 切日历季节。
    Sweden 是季节性明显的国家, ops 可以看 retention 是否随 season 波动。
    对比 retention_rate_pct 的 best/worst_season 立即定位 churn 的季节性模式。

    Season 个月映射:
      winter: [12, 1, 2]   ❄️
      spring: [3, 4, 5]    🌱
      summer: [6, 7, 8]    ☀️
      fall:   [9, 10, 11]  🍂

    Query:
    - material_type: optional filter (e.g. 'concrete' / 'wood_waste')

    Returns:
        {
          n_seasons_with_data, total_supply_ids,
          seasons: [{
            season, season_name, season_emoji, months,
            n_supply_ids, n_one_time, n_repeating,
            retention_rate_pct, one_time_pct,
            total_supply_offers, n_cycles_in_season
          }, ...],
          best_season, worst_season,
          best_season_pct, worst_season_pct,
          worst_vs_best_pct,  # (worst-best)/best*100
          material_type_filter
        }
    """
    coordinator = _coordinator()
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")

    return coordinator.persistence.get_cohort_retention_by_season(
        material_type=material_type,
    )


@router.get("/api/persistence/cohort-retention-by-region")
async def get_cohort_retention_by_region(
    material_type: Optional[str] = None,
):
    """
    iter #73: Cohort retention broken down by GEOGRAPHIC REGION (city).

    跟 iter #70 (by-season) 互补 — season 切时间维度, region 切空间维度。
    Sweden 主要 supply 集中在 Borås / Göteborg / Stockholm 三角区域,
    ops 可以看 retention 是否随地理区域波动 (例如港口城市 churn 更高)。

    City assignment:
      使用 haversine 公式找 supply_offers 最近的 real_sweden_facilities
      设施, 用最近设施的城市作为 region。lat/lon 为 NULL 的 supply
      归到 `unknown` bucket (跟真实 region 分开计数)。

    Query:
    - material_type: optional filter (e.g. 'concrete' / 'wood_waste')

    Returns:
        {
          n_regions_with_data, n_unknown_with_data, total_supply_ids,
          regions: [{
            region, region_name, region_emoji,
            n_supply_ids, n_one_time, n_repeating,
            retention_rate_pct, one_time_pct,
            total_supply_offers, n_cycles_in_region
          }, ...],                  # always 4 (3 cities + unknown)
          best_region, worst_region,
          best_region_pct, worst_region_pct,
          worst_vs_best_pct,        # (worst-best)/best*100
          material_type_filter,
          city_assignment_method: "haversine_nearest_facility"
        }
    """
    coordinator = _coordinator()
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")

    return coordinator.persistence.get_cohort_retention_by_region(
        material_type=material_type,
    )


@router.get("/api/persistence/cohort-retention-crosstab")
async def get_cohort_retention_crosstab(
    n_periods: int = 4,
    period_unit: str = "quartile",
    material_type: Optional[str] = None,
):
    """
    iter #44: Cross-tab cohort retention (period × material).

    Returns a 2D matrix showing retention_rate_pct for each
    (period, material) cell. Useful for spotting "which materials
    are losing retention in which time periods".

    Query params:
    - n_periods: 1-10, default 4
    - period_unit: quartile | day | week | month, default quartile
    - material_type: optional filter to single material

    Returns:
        {
          n_periods, period_unit, material_filter,
          period_labels: [{period_idx, sim_day_min, sim_day_max}, ...],
          materials: [str, ...],
          matrix: [[retention_pct or null, ...], ...],  # [i][j] = period i × material j
          cell_counts: [[n_supply_ids, ...], ...],  # sample sizes per cell
          trend_per_material: {<mat>: "improving" | "declining" | "stable" | "unknown"},
        }
    """
    coordinator = _coordinator()
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    if period_unit not in {"quartile", "day", "week", "month"}:
        raise HTTPException(
            status_code=400,
            detail=f"invalid period_unit: {period_unit}",
        )
    max_periods_map = {"quartile": 10, "day": 30, "week": 52, "month": 12}
    max_n = max_periods_map[period_unit]
    if n_periods < 1 or n_periods > max_n:
        raise HTTPException(
            status_code=400,
            detail=f"n_periods must be 1-{max_n} for period_unit='{period_unit}', got {n_periods}",
        )
    return coordinator.persistence.get_cohort_retention_crosstab(
        n_periods=n_periods,
        period_unit=period_unit,
        material_type=material_type,
    )


@router.get("/api/persistence/cycle-kpi-summary")
async def get_cycle_kpi_summary(
    last_n: Optional[int] = None,
    since_sim_day: Optional[int] = None,
    until_sim_day: Optional[int] = None,
):
    """
    Cycle KPI summary (iter #16 + iter #17 时间窗口) — 所有 cycles 的整体 KPI。

    用于 dashboard 顶部数字 + 趋势 (last cycle, best cycle, worst cycle)。

    Query (iter #17):
    - last_n: 只看最近 N 个 cycle (按 sim_day DESC, e.g. last_n=7 看最近一周)
    - since_sim_day: 起始 sim_day (含), e.g. since_sim_day=20 看 day 20+ 后
    - until_sim_day: 结束 sim_day (含), e.g. until_sim_day=30 看 day 30 之前

    Returns:
        {
          total_cycles, n_cycles_with_matches,
          total_tons_matched, total_distance_km, total_co2_kg, total_cost_sek,
          avg_tons_per_cycle, avg_cost_per_ton_sek, avg_co2_per_ton_kg,
          fleet_utilization_avg_pct,
          sim_day_range, best_cycle, worst_cycle, last_cycle,
          filter: {last_n, since_sim_day, until_sim_day}
        }
    """
    coordinator = _coordinator()
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    # 边界检查
    if last_n is not None and last_n < 1:
        raise HTTPException(status_code=400, detail="last_n must be >= 1")
    if last_n is not None and last_n > 10000:
        raise HTTPException(status_code=400, detail="last_n too large (max 10000)")
    if since_sim_day is not None and until_sim_day is not None:
        if since_sim_day > until_sim_day:
            raise HTTPException(
                status_code=400,
                detail=f"since_sim_day ({since_sim_day}) > until_sim_day ({until_sim_day})"
            )
    return coordinator.persistence.get_cycle_kpi_summary(
        last_n=last_n,
        since_sim_day=since_sim_day,
        until_sim_day=until_sim_day,
    )


@router.get("/api/persistence/anomalous-cycles")
async def get_anomalous_cycles(
    z_threshold: float = 2.0,
    min_history: int = 5,
):
    """
    iter #47: Detect cycles with anomalous KPIs (statistical outliers).

    Uses z-score: a cycle is flagged if any of {cost_sek, co2_kg, util_pct,
    distance_km, tons} is >= z_threshold stddevs from the historical mean.
    Useful for ops to spot regressions, solver bugs, or fuel-price spikes.

    Query:
    - z_threshold: how many stddevs to flag (default 2.0 = ~5% extreme).
                  Lower = more sensitive, higher = only extreme outliers.
    - min_history: need at least N cycles to compute stats (default 5).

    Returns:
        {
          n_anomalous: int,
          n_total_cycles: int,
          z_threshold: float,
          min_history: int,
          anomalies: [
            {cycle_id, sim_day, sim_hour, wall_timestamp,
             anomalies: [{metric, value, mean, stddev, z_score, severity}, ...],
             max_severity: "high" | "medium" | "low",
             n_anomalies: int},
            ...
          ],
        }

    Severity:
    - "high"   : |z| >= 3.0 (≈ 0.3% extreme)
    - "medium" : |z| >= 2.5 (≈ 1% rare)
    - "low"    : |z| >= 2.0 (≈ 5% unusual)

    Returns empty list when not enough history.
    """
    coordinator = _coordinator()
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    anomalies = coordinator.persistence.detect_anomalous_cycles(
        z_threshold=z_threshold, min_history=min_history,
    )
    # Get total cycle count for context
    try:
        n_total = coordinator.persistence.get_summary().get("n_cycles", 0)
    except Exception:
        n_total = None
    return {
        "n_anomalous": len(anomalies),
        "n_total_cycles": n_total,
        "z_threshold": z_threshold,
        "min_history": min_history,
        "anomalies": anomalies,
    }


@router.get("/api/persistence/anomaly-summary")
async def get_anomaly_summary(
    z_threshold: float = 2.0,
    min_history: int = 5,
):
    """
    iter #66: Aggregated anomaly statistics across all cycles.

    Wraps ``detect_anomalous_cycles`` and adds:
    - per-metric anomaly counts and percentages
    - severity distribution (high/medium/low)
    - top anomalous metrics (sorted desc)
    - multi-anomaly cycle detection (cycles with >= 2 metric anomalies)
    - insufficient_history flag (when n_cycles < min_history)

    Query:
    - z_threshold: how many stddevs to flag (default 2.0)
    - min_history: minimum cycles needed (default 5)

    Returns:
        {
          n_anomalous_cycles: int,
          n_total_cycles: int,
          anomaly_rate_pct: float,
          z_threshold: float,
          min_history: int,
          total_anomaly_events: int,
          per_metric_counts: {metric: count, ...},
          per_metric_pct: {metric: pct, ...},
          per_severity_counts: {high: int, medium: int, low: int},
          top_anomalous_metrics: [{metric, count, pct_of_cycles}, ...],
          cycles_with_multiple_anomalies: int,
          multi_anomaly_rate_pct: float,
          most_common_metric: str | None,
          most_common_severity: str | None,
          insufficient_history: bool,
        }
    """
    coordinator = _coordinator()
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    return coordinator.persistence.get_anomaly_summary(
        z_threshold=z_threshold, min_history=min_history,
    )
