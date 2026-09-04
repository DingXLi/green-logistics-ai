"""
Green Logistics AI - Web Backend

FastAPI 应用提供 REST API
"""

from fastapi import FastAPI, HTTPException, Request, Header, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response as FastAPIResponse, JSONResponse
from pydantic import BaseModel, field_validator
from typing import List, Dict, Any, Optional, Set, Tuple
from datetime import datetime
from loguru import logger
import sys
import os
import random
import asyncio
import time
import json
import csv
import io
import gzip
from contextlib import asynccontextmanager

# 添加父目录到路径以便导入
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from agents.coordinator import MultiAgentCoordinator
from agents.world_builder import WorldConfig
from optimization.vrp_solver import VRPSolver, Location, Vehicle
from synthetic.data_generator import SyntheticDataGenerator


# ============================================
# iter #18: DB export helpers
# ============================================
def _csv_to_rows(csv_str: str) -> List[Dict[str, Any]]:
    """Convert CSV string → list of dicts.

    Skips metadata header lines (starting with '#') that may be present
    when CSV was exported with include_metadata=True (iter #20+).
    """
    # Filter out # comment lines (metadata header)
    lines = [l for l in csv_str.split("\n") if not l.startswith("#")]
    csv_clean = "\n".join(lines)
    reader = csv.DictReader(io.StringIO(csv_clean))
    return [dict(r) for r in reader]


def _rows_to_csv(rows: List[Dict[str, Any]]) -> str:
    """Convert list of dicts → CSV string."""
    if not rows:
        return ""
    buf = io.StringIO()
    fieldnames = list(rows[0].keys())
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for r in rows:
        writer.writerow(r)
    return buf.getvalue()


# ============================================
# iter #23: Parquet helpers (columnar analytics)
# ============================================
PARQUET_MIMETYPE = "application/vnd.apache.parquet"


def _rows_to_parquet_bytes(rows: List[Dict[str, Any]]) -> bytes:
    """
    把 list of dicts → Apache Parquet binary (iter #23)。

    优势 vs CSV:
    - Columnar storage (analytics query 更快)
    - 内嵌 schema (typed columns, no parse needed)
    - 压缩更好 (snappy 默认, 比 CSV gzip 还小 ~3-5x)
    - pandas/polars/duckdb/spark 原生支持

    处理:
    - 强制 schema 推断 (pyarrow default)
    - 空 rows → 返回最小 parquet (1 行 schema-only, schema 在 metadata 里)
    - 混合类型列 → 转 string (避免 pyarrow type conflict)
    """
    if not rows:
        # 返回空 parquet: schema-only table with no rows
        # pyarrow schema with single nullable int64 column (任何 schema 都可以)
        import pyarrow as pa
        schema = pa.schema([("_empty", pa.int64())])
        empty_table = pa.table({"_empty": pa.array([], type=pa.int64())}, schema=schema)
        sink = io.BytesIO()
        import pyarrow.parquet as pq
        pq.write_table(empty_table, sink, compression="snappy")
        return sink.getvalue()

    # Build DataFrame + handle type coercion (iter #23 best-effort)
    import pandas as pd
    df = pd.DataFrame(rows)

    # Convert mixed-type object columns to string (avoid pyarrow type errors)
    for col in df.columns:
        if df[col].dtype == "object":
            # Try to keep numeric if all values are numeric
            try:
                # Quick test: can it be int?
                pd.to_numeric(df[col], errors="raise")
                # keep numeric
            except (ValueError, TypeError):
                # stringify
                df[col] = df[col].astype(str).replace({"None": "", "nan": ""})

    table = __import__("pyarrow").Table.from_pandas(df, preserve_index=False)
    sink = io.BytesIO()
    import pyarrow.parquet as pq
    pq.write_table(table, sink, compression="snappy")
    return sink.getvalue()


def _maybe_gzip(content: bytes, use_gzip: bool, filename: str) -> FastAPIResponse:
    """可选 gzip 包装 (iter #19)。返回 Response with Content-Encoding header。

    Args:
        content: raw bytes (text content as UTF-8)
        use_gzip: 是否启用 gzip
        filename: 下载文件名
    """
    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
    }
    if use_gzip:
        compressed = gzip.compress(content)
        headers["Content-Encoding"] = "gzip"
        return FastAPIResponse(
            content=compressed,
            media_type="application/octet-stream",
            headers=headers,
        )
    # not gzipped — content 直接传
    return FastAPIResponse(
        content=content,
        headers=headers,
    )

# ============================================
# FastAPI 应用
# ============================================
app = FastAPI(
    title="Green Logistics AI",
    description="多智能体 AI 系统 - 绿色物流优化",
    version="0.1.0"
)

# CORS 配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 生产环境应该限制
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================
# iter #21: Performance tracking middleware
# ============================================
import time as _time
from collections import deque as _deque
import threading as _threading

_PERF_LOCK = _threading.Lock()
# 每个 endpoint 保留最近 100 次响应时间 (ms)
_PERF_BUFFER: Dict[str, _deque] = {}
# 总请求数 + 总错误数
_PERF_TOTAL = 0
_PERF_ERRORS = 0
# iter #27: per-endpoint error count (5xx only)
_PERF_ERRORS_BY_KEY: Dict[str, int] = {}


@app.middleware("http")
async def perf_middleware(request: Request, call_next):
    """记录每个 endpoint 的响应时间 (iter #21)。

    格式: X-Perf-Time-Ms header (每个响应都加) +
          /api/admin/perf-stats endpoint (聚合报告)
    """
    global _PERF_TOTAL, _PERF_ERRORS
    path = request.url.path
    method = request.method
    key = f"{method} {path}"
    start = _time.time()
    try:
        response = await call_next(request)
        elapsed_ms = (_time.time() - start) * 1000
        response.headers["X-Perf-Time-Ms"] = f"{elapsed_ms:.1f}"
        with _PERF_LOCK:
            _PERF_TOTAL += 1
            if response.status_code >= 500:
                _PERF_ERRORS += 1
                _PERF_ERRORS_BY_KEY[key] = _PERF_ERRORS_BY_KEY.get(key, 0) + 1
            buf = _PERF_BUFFER.setdefault(key, _deque(maxlen=100))
            buf.append(elapsed_ms)
        return response
    except Exception as e:
        elapsed_ms = (_time.time() - start) * 1000
        with _PERF_LOCK:
            _PERF_TOTAL += 1
            _PERF_ERRORS += 1
            _PERF_ERRORS_BY_KEY[key] = _PERF_ERRORS_BY_KEY.get(key, 0) + 1
            buf = _PERF_BUFFER.setdefault(key, _deque(maxlen=100))
            buf.append(elapsed_ms)
        raise


# ============================================
# 前端活动跟踪 middleware (智能调度用)
# ============================================
@app.middleware("http")
async def frontend_activity_middleware(request: Request, call_next):
    """
    把"前端有请求"当作"有人在看 dashboard"的信号。
    路径以 /api/ 或 / 开头的请求都计数 (排除 docs / openapi 之类)。
    """
    path = request.url.path
    if path.startswith(_FRONTEND_PATH_PREFIXES):
        _mark_frontend_activity(path)
    response = await call_next(request)
    return response

# 全局状态
coordinator: Optional[MultiAgentCoordinator] = None
data_generator: Optional[SyntheticDataGenerator] = None
scheduler: Optional["BackgroundScheduler"] = None

# ============================================
# 智能调度: 前端活动跟踪
# ============================================
# 最后一次前端请求的 monotonic 时间戳
# 任何 /api/* 请求 (含 /api/optimize POST) 都会更新它
# scheduler 用来判断"有人在看" → 活跃模式 vs 闲置模式
_last_frontend_activity: float = 0.0
_last_frontend_path: str = ""  # debug: 最后触发 activity 的 path
_last_frontend_path_at: float = 0.0  # 该 path 被设置时的 monotonic 时间
# asyncio.Event: 闲置中的 scheduler 在等这个 signal 唤醒
_wake_scheduler_event: asyncio.Event = asyncio.Event()
# ID 检查的 path 前缀 (只把"前端"请求当作活动信号, 不计 health check / docs)
# 只计 /api/* 业务端点 — /health, /docs, /openapi.json, / 都排除
# (HF Spaces 每 30s 打 /health, 不排除的话 scheduler 永远进不了 idle)
_FRONTEND_PATH_PREFIXES = (
    "/api/",      # 业务 API (含 /api/scheduler/status, /api/optimize 等)
)


def _mark_frontend_activity(path: str = "") -> None:
    """更新 last_frontend_activity + 唤醒闲置中的 scheduler。
    在 middleware / 端点里调用。"""
    global _last_frontend_activity, _last_frontend_path, _last_frontend_path_at
    now = time.monotonic()
    _last_frontend_activity = now
    _last_frontend_path = path
    _last_frontend_path_at = now
    # 如果 scheduler 正在闲置, 唤醒它 (loop 会看到 last_activity 更新后切回 active 模式)
    _wake_scheduler_event.set()


def _seconds_since_frontend_activity() -> float:
    """距离上次前端活动过了多少秒。scheduler 用来判断 idle。"""
    if _last_frontend_activity == 0.0:
        return float("inf")
    return time.monotonic() - _last_frontend_activity


# ============================================
# WebSocket 广播 (cycle_update 推送)
# ============================================
class WebSocketBroadcaster:
    """
    管理所有连接的 WebSocket client，广播 cycle_update。

    - 每个 client 独立 asyncio 队列 (阻塞消费 send) — 一个 client
      慢不会拖累其他 client
    - broadcast() 是 fire-and-forget (asyncio.gather return_exceptions=True)
      发送失败会被下一轮 try/except 接住，不影响业务逻辑
    - Connected client 列表是动态的 (心跳 / 断开会自动清理)
    - iter #27: 跟踪 broadcast 统计 (总数 / 成功 / 失败 / 上次时间)
    - iter #32: max-client guard (全局 + per-IP) + 连接 metrics
      (peak / total accepted / total rejected / 当前 IP 分布 /
      每个 client 连接时长累计)
    """

    def __init__(self) -> None:
        self._clients: Set[WebSocket] = set()
        self._lock = asyncio.Lock()
        # iter #27: broadcast statistics
        self._total_broadcasts: int = 0  # 调用 broadcast() 的总次数
        self._total_sends: int = 0       # 总发送尝试 (per-client)
        self._total_send_failures: int = 0  # 失败发送
        self._last_broadcast_at: Optional[str] = None  # ISO timestamp
        # iter #32: connection metrics
        self._peak_clients: int = 0  # 历史最高并发连接数
        self._total_connections_accepted: int = 0  # 累计接受连接数
        self._total_connections_rejected: int = 0  # 累计拒绝连接数 (满 / per-IP 超限)
        self._total_connection_seconds: float = 0.0  # 累计已断开 client 的存活秒数
        # iter #32: per-client metadata {ws: {client_id, ip, connect_time}}
        self._client_meta: Dict[WebSocket, Dict[str, Any]] = {}

    async def connect(
        self,
        ws: WebSocket,
        *,
        client_ip: Optional[str] = None,
    ) -> bool:
        """
        接受新 client 连接。

        Returns:
            True  - 接受成功
            False - 拒绝 (max_clients 或 per-IP 超限); caller 应主动 ws.close(code=1013)

        iter #32: 增加 client_ip 用于 per-IP 限流; 返回 bool 表示是否接受。
        """
        max_clients = _get_ws_max_clients()
        max_per_ip = _get_ws_max_per_ip()

        async with self._lock:
            # 全局 max 限流
            if max_clients > 0 and len(self._clients) >= max_clients:
                self._total_connections_rejected += 1
                logger.warning(
                    f"WS rejected: at capacity "
                    f"({len(self._clients)}/{max_clients}, "
                    f"total_rejected={self._total_connections_rejected})"
                )
                return False
            # per-IP 限流
            if max_per_ip > 0 and client_ip:
                ip_count = sum(
                    1 for meta in self._client_meta.values()
                    if meta.get("ip") == client_ip
                )
                if ip_count >= max_per_ip:
                    self._total_connections_rejected += 1
                    logger.warning(
                        f"WS rejected: per-IP limit hit "
                        f"(ip={client_ip}, count={ip_count}/{max_per_ip}, "
                        f"total_rejected={self._total_connections_rejected})"
                    )
                    return False

            await ws.accept()
            self._clients.add(ws)
            now = datetime.now()
            client_id = f"ws-{id(ws) & 0xffff:04x}-{int(now.timestamp() * 1000) % 100000}"
            self._client_meta[ws] = {
                "client_id": client_id,
                "ip": client_ip or "unknown",
                "connect_time": now,
            }
            self._total_connections_accepted += 1
            current = len(self._clients)
            if current > self._peak_clients:
                self._peak_clients = current

        logger.info(
            f"WS client connected: {client_id} ip={client_ip} "
            f"(total={current}, peak={self._peak_clients})"
        )
        return True

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(ws)
            meta = self._client_meta.pop(ws, None)
            if meta and "connect_time" in meta:
                duration = (datetime.now() - meta["connect_time"]).total_seconds()
                self._total_connection_seconds += duration
        logger.info(f"WS client disconnected: total={len(self._clients)}")

    async def broadcast(self, payload: Dict[str, Any]) -> None:
        """广播 JSON 给所有 client。失败不抛出（一个 client 坏不拖累整体）。"""
        if not self._clients:
            return
        msg = json.dumps(payload, default=str)
        # snapshot + gather 避免 disconnect 时的 race
        async with self._lock:
            targets = list(self._clients)
            # iter #27: track broadcast metadata
            self._total_broadcasts += 1
            self._total_sends += len(targets)
            self._last_broadcast_at = datetime.now().isoformat()
        results = await asyncio.gather(
            *[self._safe_send(ws, msg) for ws in targets],
            return_exceptions=True,
        )
        # 清理断开的 client
        dead: List[WebSocket] = []
        for ws, res in zip(targets, results):
            if isinstance(res, Exception):
                dead.append(ws)
        if dead:
            async with self._lock:
                self._total_send_failures += len(dead)
                for ws in dead:
                    self._clients.discard(ws)
                if dead:
                    logger.info(f"WS cleaned {len(dead)} dead client(s)")

    @staticmethod
    async def _safe_send(ws: WebSocket, msg: str) -> None:
        try:
            await ws.send_text(msg)
        except (WebSocketDisconnect, RuntimeError) as e:
            raise RuntimeError(f"send failed: {e}") from e

    def stats(self) -> Dict[str, Any]:
        """iter #27: 详细的 broadcast 统计; iter #32: + connection metrics。"""
        # per-IP 当前计数 (snapshot of currently-connected clients)
        ip_counts: Dict[str, int] = {}
        for meta in self._client_meta.values():
            ip = meta.get("ip", "unknown")
            ip_counts[ip] = ip_counts.get(ip, 0) + 1
        # 平均连接时长 (秒) — 基于已断开 client; 当前连接不算
        avg_duration = (
            round(self._total_connection_seconds / self._total_connections_accepted, 3)
            if self._total_connections_accepted > 0 else 0.0
        )
        return {
            "connected_clients": len(self._clients),
            "total_broadcasts": self._total_broadcasts,
            "total_sends": self._total_sends,
            "total_send_failures": self._total_send_failures,
            "success_rate_pct": (
                round((self._total_sends - self._total_send_failures)
                      / self._total_sends * 100, 2)
                if self._total_sends > 0 else 100.0
            ),
            "last_broadcast_at": self._last_broadcast_at,
            # iter #32: connection metrics
            "max_clients": _get_ws_max_clients(),
            "max_per_ip": _get_ws_max_per_ip(),
            "peak_clients": self._peak_clients,
            "total_connections_accepted": self._total_connections_accepted,
            "total_connections_rejected": self._total_connections_rejected,
            "total_connection_seconds": round(self._total_connection_seconds, 3),
            "avg_connection_seconds": avg_duration,
            "current_ip_distribution": ip_counts,
        }

    def reset_stats(self) -> None:
        """iter #27: 重置 broadcast 统计 (测试 / 调试用); iter #32: + connection metrics。"""
        self._total_broadcasts = 0
        self._total_sends = 0
        self._total_send_failures = 0
        self._last_broadcast_at = None
        # iter #32
        self._peak_clients = 0
        self._total_connections_accepted = 0
        self._total_connections_rejected = 0
        self._total_connection_seconds = 0.0
        # 注意: _client_meta 不重置 — 当前连接的 client 不应该被重置清掉


ws_broadcaster = WebSocketBroadcaster()


# ============================================
# iter #27: WebSocket Origin 校验 (security)
# ============================================
# GL_WS_ALLOWED_ORIGINS: comma-separated origin allowlist
#   e.g. "https://lidingx-green-logistics.hf.space,http://localhost:5173"
# - Default empty string → all origins allowed (backward compatible for dev)
# - Set on HF Space via env var to lock down production
# - Browser WS clients always send Origin header; non-browser clients may not
#   → if Origin absent → allow (backward compat); if Origin present + not in
#     allowlist → reject with code 1008 (policy violation)
_WS_ALLOWED_ORIGINS_RAW: str = os.environ.get("GL_WS_ALLOWED_ORIGINS", "")


def _get_ws_allowed_origins() -> set:
    """Return a fresh copy of the WS origin allowlist."""
    return {o.strip() for o in _WS_ALLOWED_ORIGINS_RAW.split(",") if o.strip()}


def is_ws_origin_allowed(origin: str | None) -> bool:
    """
    Check if a WS client's Origin is allowed.
    - If allowlist empty → allow all (dev mode)
    - If allowlist non-empty and origin absent → allow (non-browser clients)
    - If allowlist non-empty and origin present → must be in allowlist
    """
    allowed = _get_ws_allowed_origins()
    if not allowed:
        return True
    if not origin:
        return True
    return origin in allowed


# ============================================
# iter #32: WebSocket max-client guard
# ============================================
# GL_WS_MAX_CLIENTS: 全局并发连接数上限
#   - 默认 50 (足够前端 dashboard + 调试)
#   - 0 = 无限 (不推荐)
# GL_WS_MAX_PER_IP: 同一 IP 并发连接数上限 (防单个 tab 反复重连 / 防滥用)
#   - 默认 10
#   - 0 = 无限
def _get_ws_max_clients() -> int:
    raw = os.environ.get("GL_WS_MAX_CLIENTS", "50")
    try:
        v = int(raw)
        return max(v, 0)
    except (TypeError, ValueError):
        return 50


def _get_ws_max_per_ip() -> int:
    raw = os.environ.get("GL_WS_MAX_PER_IP", "10")
    try:
        v = int(raw)
        return max(v, 0)
    except (TypeError, ValueError):
        return 10


# ============================================
# iter #33: Admin token auth (WebSocket stats + future admin endpoints)
# ============================================
# GL_ADMIN_TOKEN: 设置后, 需要 Bearer / X-Admin-Token header 才能访问敏感 endpoint
#   - 空 (默认): 不启用 auth (向后兼容 dev / 本地)
#   - 生产环境推荐设置一个长随机 token (e.g. openssl rand -hex 32)
#   - 仅用于 admin/debug endpoint, 不用于业务 endpoint
_GL_ADMIN_TOKEN: str = os.environ.get("GL_ADMIN_TOKEN", "")


def _get_admin_token() -> str:
    """Return the configured admin token (fresh read, 支持运行时修改 env var)."""
    return os.environ.get("GL_ADMIN_TOKEN", "")


def _extract_admin_token(authorization: Optional[str], x_admin_token: Optional[str]) -> Optional[str]:
    """
    从 request headers 提取 admin token。

    支持两种方式 (任一即可):
    - Authorization: Bearer <token>
    - X-Admin-Token: <token>

    返回 token 字符串 (不含 "Bearer " 前缀), 或 None (没提供)。
    """
    if authorization:
        parts = authorization.split(None, 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1].strip()
        # 允许直接传 token (不强制 Bearer 前缀, 方便调试)
        return authorization.strip()
    if x_admin_token:
        return x_admin_token.strip()
    return None


def _check_admin_token(authorization: Optional[str], x_admin_token: Optional[str]) -> bool:
    """
    验证 admin token 是否匹配 GL_ADMIN_TOKEN。

    Returns:
        True - 访问被允许
        False - 拒绝 (401)
    """
    configured = _get_admin_token()
    if not configured:
        # 没配置 token → 不启用 auth (dev 模式)
        return True
    provided = _extract_admin_token(authorization, x_admin_token)
    if not provided:
        return False
    # 用 secrets.compare_digest 避免 timing attack
    import secrets
    try:
        return secrets.compare_digest(provided, configured)
    except (TypeError, ValueError):
        return False


# ============================================
# iter #34: FastAPI 依赖封装 (复用 iter #33 逻辑到所有 admin/debug endpoint)
# ============================================
from fastapi import Depends


async def require_admin(
    authorization: Optional[str] = Header(default=None),
    x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token"),
) -> None:
    """
    FastAPI dependency: 验证 admin token。如果 GL_ADMIN_TOKEN 未设置则跳过。

    用法: ``async def endpoint(_: None = Depends(require_admin)):``
    拒绝时抛 HTTPException(401, WWW-Authenticate: Bearer)。
    """
    if not _check_admin_token(authorization, x_admin_token):
        raise HTTPException(
            status_code=401,
            detail=(
                "Admin token required (set GL_ADMIN_TOKEN and pass via "
                "Authorization: Bearer or X-Admin-Token header)"
            ),
            headers={"WWW-Authenticate": "Bearer"},
        )


async def _broadcast_cycle_update(cycle_result: Dict[str, Any]) -> None:
    """Coordinator 跑完 cycle 后调用，广播给所有 WS client。"""
    # iter #7: 附带 running efficiency summary (cost/CO2 per ton),
    # 前端可以不用额外 fetch 就直接显示在 Dashboard 顶部
    eff_summary: Dict[str, Any] = {}
    try:
        if coordinator is not None and coordinator.persistence is not None:
            eff = coordinator.persistence.get_efficiency_metrics()
            # 只传关键字段,减小 payload
            eff_summary = {
                "n_cycles": eff.get("n_cycles", 0),
                "cost_per_ton_sek": eff.get("cost_per_ton_sek"),
                "co2_per_ton_kg": eff.get("co2_per_ton_kg"),
                "avg_fleet_util_pct": eff.get("avg_fleet_util_pct"),
                "match_rate_pct": eff.get("match_rate_pct"),
            }
    except Exception as e:
        logger.debug(f"WS broadcast efficiency summary failed (ignore): {e}")

    # iter #29: 附带 LLM usage/cost summary (from in-memory LLMTracker)
    # 精确 cost 看 /api/admin/llm-stats; WS 只传轻量 summary
    llm_summary: Dict[str, Any] = {}
    try:
        from agents.llm_tracker import get_llm_tracker
        llm_stats = get_llm_tracker().get_stats()
        llm_summary = {
            "total_calls": llm_stats.get("total_calls", 0),
            "total_errors": llm_stats.get("total_errors", 0),
            "total_tokens": llm_stats.get("total_tokens", 0),
            "total_cost_usd": llm_stats.get("total_cost_usd", 0.0),
            "error_rate_pct": llm_stats.get("error_rate_pct", 0.0),
        }
    except Exception as e:
        logger.debug(f"WS broadcast LLM summary failed (ignore): {e}")

    # iter #8: 附带 fleet metrics (n_vehicles, util, distance_to_depot)
    # 让前端实时显示车队状态, 不需要额外 fetch /api/fleet
    fleet_metrics: Dict[str, Any] = {}
    try:
        if coordinator is not None and coordinator.logistics_agent is not None:
            fs = await coordinator.logistics_agent.get_fleet_status()
            fleet_metrics = {
                "total_vehicles": fs.get("total_vehicles", 0),
                "available": fs.get("available", 0),
                "en_route": fs.get("en_route", 0),
                "loading": fs.get("loading", 0),
                "utilization_rate": fs.get("utilization_rate", 0),
                "total_distance_km": fs.get("total_distance_km", 0),
                "avg_distance_to_depot_km": fs.get("avg_distance_to_depot_km", 0),
            }
    except Exception as e:
        logger.debug(f"WS broadcast fleet metrics failed (ignore): {e}")

    payload = {
        "type": "cycle_update",
        "timestamp": datetime.now().isoformat(),
        "data": {
            "cycle_id": cycle_result.get("cycle_id"),
            "n_supply_offers": cycle_result.get("n_supply_offers"),
            "n_demand_requests": cycle_result.get("n_demand_requests"),
            "n_matches": cycle_result.get("n_matches"),
            "total_tons": cycle_result.get("total_tons"),
            "total_cost_sek": cycle_result.get("total_cost_sek"),
            "total_co2_kg": cycle_result.get("total_co2_kg"),
            "total_distance_km": cycle_result.get("total_distance_km"),
            "sim_day": cycle_result.get("sim_day"),
            "sim_hour": cycle_result.get("sim_hour"),
            "efficiency": eff_summary,
            "llm": llm_summary,
            "fleet": fleet_metrics,
            "distance_source": cycle_result.get("distance_source", "unknown"),
        },
    }
    try:
        await ws_broadcaster.broadcast(payload)
    except Exception as e:
        logger.warning(f"WS broadcast cycle_update failed: {e}")


# ============================================
# 后台调度器 (Task A)
# ============================================
class BackgroundScheduler:
    """
    周期性跑 coordinator.run_optimization_cycle() 的后台任务

    - Opt-in: GL_SCHEDULER_ENABLED=true 才启动
    - 重叠保护: asyncio.Lock, 上一个 cycle 没跑完就跳过
    - 故障隔离: try/except 包住 cycle, LLM quota 错误不会搞死 scheduler
    - 可控间隔: GL_SCHEDULER_INTERVAL (秒, 默认 30)
    - **智能闲置**: GL_SCHEDULER_IDLE_WINDOW (秒, 默认 300) 内无前端请求 → 不跑 cycle
      等到 _wake_scheduler_event 被 set (前端再来) 才切回 active 模式
    """

    def __init__(
        self,
        coord: MultiAgentCoordinator,
        interval_seconds: float = 30.0,
        idle_window_seconds: float = 300.0,
    ):
        self.coord = coord
        self.interval_seconds = interval_seconds
        self.idle_window_seconds = idle_window_seconds
        self._task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()
        self._stop_event = asyncio.Event()

        # 状态
        self.scheduler_active: bool = False   # 调度循环是否在跑
        self.running: bool = False            # 当前是否有 cycle 正在执行
        self.cycle_count: int = 0             # scheduler 累计跑的 cycle 数 (不含 warmup)
        self.error_count: int = 0
        self.last_cycle_at: Optional[str] = None
        self.last_cycle_id: Optional[str] = None
        self.last_error: Optional[str] = None
        self.started_at: Optional[str] = None
        self.is_idle: bool = False            # True = 当前在闲置模式, 不跑 cycle
        self.idle_entered_at: Optional[str] = None  # 上次进入闲置的时间
        # iter #12: dry_run 模式 — 跑 cycle 但不 persist (开发调试用)
        self.dry_run: bool = False
        self.dry_run_count: int = 0  # dry-run 跳的 cycle 数 (不加入 cycle_count)

    async def _run_cycle_safe(self) -> None:
        """单次 cycle: lock 保护 + try/except 隔离错误"""
        if self._lock.locked():
            # 上一轮还没跑完, 跳过这一轮避免重叠
            logger.debug("Scheduler: 上一 cycle 未完成, 跳过本次")
            return
        async with self._lock:
            self.running = True
            try:
                result = await self.coord.run_optimization_cycle(dry_run=self.dry_run)
                # iter #12: dry_run 模式不 persist (但不防礙 cycle 本身逻辑)
                if self.dry_run and result.get("optimization_id"):
                    logger.info(
                        f"Scheduler cycle #{self.cycle_count + 1} 跑成功 (DRY RUN, 未 persist)"
                    )
                    # 仍统计 cycle_count (仅表示跳了几次)
                    self.dry_run_count += 1
                else:
                    self.cycle_count += 1
                self.last_cycle_at = datetime.utcnow().isoformat() + "Z"
                self.last_cycle_id = result.get("optimization_id")
                self.last_error = None
                matches = (result.get("matches") or {}).get("total_matches", 0)
                logger.info(
                    f"Scheduler cycle 完成: "
                    f"{self.last_cycle_id} ({matches} matches)"
                    f"{' (DRY RUN)' if self.dry_run else ''}"
                )
                # WebSocket 广播: cycle 完成推给所有 dashboard
                # dry_run 模式不广播 (避免误导 dashboard 数据)
                if not self.dry_run:
                    try:
                        await _broadcast_cycle_update(result)
                    except Exception as e:
                        logger.warning(f"WS broadcast 在 scheduler cycle 失败: {e}")
                    # iter #43: auto-vacuum check after each cycle
                    if _get_runtime_config("auto_vacuum_enabled"):
                        try:
                            rec = self.coord.persistence.should_auto_vacuum()
                            if rec["should_vacuum"]:
                                logger.info(
                                    f"auto-vacuum triggered after cycle: {rec['reasons'][:2]}"
                                )
                                self.coord.persistence.vacuum(triggered_by="auto")
                        except Exception as e:
                            logger.warning(f"auto-vacuum failed (ignore): {e}")
            except Exception as e:
                self.error_count += 1
                self.last_error = f"{type(e).__name__}: {str(e)[:200]}"
                logger.exception(
                    f"Scheduler cycle #{self.cycle_count + 1} 失败 "
                    f"(已累计 {self.error_count} 次错误): {e}"
                )
            finally:
                self.running = False

    async def _loop(self) -> None:
        """
        主循环: 智能调度版本

        Active 模式 (有前端活动):
            跑 cycle → sleep interval → 跑 cycle → ...
        Idle 模式 (idle_window 内无前端活动):
            不跑 cycle, 等 wake_event 或 60s 周期检查
            收到 wake_event → 切回 active, 立即跑一个 cycle

        wake_event 逻辑: 任何前端请求触发 _mark_frontend_activity()
        → 设置 wake_event → loop 看到 last_activity 变了 → 切回 active
        """
        self.started_at = datetime.utcnow().isoformat() + "Z"
        logger.info(
            f"Scheduler 后台循环启动, 间隔 {self.interval_seconds}s, "
            f"idle_window={self.idle_window_seconds}s"
        )
        # 第一次跑一个 cycle (不立即等满 interval)
        # 初始化为 active 模式 (last_frontend_activity 是 startup 时设置的)
        self.is_idle = False
        self.idle_entered_at = None
        await self._run_cycle_safe()
        while not self._stop_event.is_set():
            idle_for = _seconds_since_frontend_activity()
            if idle_for > self.idle_window_seconds:
                # === Idle 模式 ===
                if not self.is_idle:
                    self.is_idle = True
                    self.idle_entered_at = datetime.utcnow().isoformat() + "Z"
                    logger.info(
                        f"Scheduler 进入 idle 模式 "
                        f"(已 {int(idle_for)}s 无前端活动, 阈值 {int(self.idle_window_seconds)}s)"
                    )
                # 等 wake_event 或 60s 超时
                _wake_scheduler_event.clear()
                try:
                    await asyncio.wait_for(
                        _wake_scheduler_event.wait(),
                        timeout=60.0,
                    )
                    # wake 被 set → 检查 last_frontend_activity 是否真的更新
                    new_idle_for = _seconds_since_frontend_activity()
                    if new_idle_for < self.idle_window_seconds:
                        self.is_idle = False
                        self.idle_entered_at = None
                        logger.info(
                            f"Scheduler 唤醒 (前端活动 "
                            f"{int(new_idle_for)}s 前)"
                        )
                        # 切回 active 后立刻跑一个 cycle
                        await self._run_cycle_safe()
                except asyncio.TimeoutError:
                    # 60s 周期检查, 重新评估 idle 状态
                    continue
            else:
                # === Active 模式 ===
                if self.is_idle:
                    # 不太可能走到这 (从 idle 出来是上面 wake 分支), 兜底
                    self.is_idle = False
                    self.idle_entered_at = None
                try:
                    # wait_for 让 stop_event 能在 shutdown 时打断 sleep
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=self.interval_seconds,
                    )
                    # 如果没超时, 说明 stop 被 set 了
                    break
                except asyncio.TimeoutError:
                    # 正常超时 → 跑下一个 cycle
                    await self._run_cycle_safe()
        self.scheduler_active = False
        self.is_idle = False
        logger.info("Scheduler 后台循环已退出")

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            logger.warning("Scheduler 已在运行, 忽略重复 start()")
            return
        self._stop_event.clear()
        self.scheduler_active = True
        self._task = asyncio.create_task(self._loop(), name="gl-scheduler")
        logger.info("Scheduler task 已创建")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=5.0)
            except asyncio.TimeoutError:
                self._task.cancel()
                try:
                    await self._task
                except (asyncio.CancelledError, Exception):
                    pass
            self._task = None
        self.scheduler_active = False

    def status(self) -> Dict[str, Any]:
        now = datetime.utcnow()
        next_in: Optional[float] = None
        if self.last_cycle_at and not self.is_idle:
            try:
                last = datetime.fromisoformat(self.last_cycle_at.rstrip("Z"))
                elapsed = (now - last).total_seconds()
                next_in = round(max(0.0, self.interval_seconds - elapsed), 1)
            except Exception:
                next_in = None
        idle_for = _seconds_since_frontend_activity()
        path_age = (time.monotonic() - _last_frontend_path_at) if _last_frontend_path_at else None
        return {
            "enabled": True,
            "active": self.scheduler_active,
            "running_now": self.running,
            "is_idle": self.is_idle,
            "idle_window_seconds": self.idle_window_seconds,
            "idle_for_seconds": round(idle_for, 1) if idle_for != float("inf") else None,
            "idle_entered_at": self.idle_entered_at,
            "last_frontend_path": _last_frontend_path,
            "last_frontend_path_age_s": round(path_age, 1) if path_age is not None else None,
            "interval_seconds": self.interval_seconds,
            "cycle_count": self.cycle_count,
            "dry_run": self.dry_run,           # iter #12
            "dry_run_count": self.dry_run_count,  # iter #12
            "error_count": self.error_count,
            "last_error": self.last_error,
            "last_cycle_at": self.last_cycle_at,
            "last_cycle_id": self.last_cycle_id,
            "next_cycle_in_seconds": next_in,
            "started_at": self.started_at,
        }

    def set_dry_run(self, enabled: bool) -> Dict[str, Any]:
        """iter #12: 切换 dry_run 模式 (跑 cycle 但不 persist)。

        Args:
            enabled: True → dry_run, False → normal mode

        Returns:
            dict with previous state + new state
        """
        prev = self.dry_run
        self.dry_run = enabled
        if not enabled:
            # 退出 dry_run 时清零 dry_run_count (仅作为 diagnostic counter)
            self.dry_run_count = 0
        logger.info(f"Scheduler dry_run: {prev} → {enabled}")
        return {
            "previous_dry_run": prev,
            "current_dry_run": enabled,
        }


# ============================================
# 启动事件
# ============================================
@app.on_event("startup")
async def startup_event():
    """应用启动时初始化"""
    global coordinator, data_generator

    logger.info("初始化 Green Logistics AI 后端 (V2)...")

    # 初始化协调器（V2：自动从 WorldBuilder 引导 20/10/30 世界）
    world_config = WorldConfig(
        n_supply_points=20,
        n_demand_points=10,
        n_vehicles=30,
        seed=42,
    )
    # db_path 走环境变量 (HF Spaces 部署时设为 /data/simulation.db 用持久化卷)
    db_path = os.environ.get("GL_DB_PATH", "data/simulation.db")
    coordinator = MultiAgentCoordinator(
        config=world_config,
        db_path=db_path,
    )

    # 初始化数据生成器（IoT/fleet 端点用）
    data_generator = SyntheticDataGenerator(seed=42)

    logger.info(
        f"后端初始化完成：{len(coordinator.supply_agents)} supply / "
        f"{len(coordinator.market_agent.demand_points)} demand / "
        f"{len(coordinator.logistics_agent.vehicles)} vehicles"
    )

    # 预热: 首次启动 DB 还没有 cycle 数据, 跑 1 个 cycle
    # 让 Lovable 前端立刻能看到真实 KPI, 而不是 0 cycles
    # 后续启动 (DB 已有数据) 跳过, 保持快速重启
    try:
        n_cycles = (coordinator.persistence.get_summary() or {}).get("n_cycles") or 0
        if n_cycles == 0:
            logger.info("DB 空, 预热 1 个优化 cycle (预计 5-15s)...")
            result = await coordinator.run_optimization_cycle()
            matches = result.get("matches", {}) or {}
            opt_id = (result.get("optimization_id") or "?")[:8]
            logger.info(
                f"预热完成: {matches.get('total_matches', 0)} matches, "
                f"cycle_id={opt_id}"
            )
        else:
            logger.info(f"DB 已有 {n_cycles} cycles, 跳过预热")
    except Exception as e:
        # 预热失败不能阻止服务启动
        logger.warning(f"启动预热失败 (服务继续运行): {e}")

    # 后台调度器 (Task A): opt-in via GL_SCHEDULER_ENABLED
    # 默认 false 保持现有 demo 行为; 用户委托时设为 true 让 Lovable
    # 30s 轮询能拿到新鲜数据
    # 智能调度: 启勥时初始化 last_frontend_activity = now,
    # 避免 scheduler 启动后立刻进入 idle
    global scheduler, _last_frontend_activity
    _last_frontend_activity = time.monotonic()
    scheduler_enabled = os.environ.get(
        "GL_SCHEDULER_ENABLED", "false"
    ).strip().lower() in ("1", "true", "yes", "on")
    scheduler_interval = float(os.environ.get("GL_SCHEDULER_INTERVAL", "30"))
    scheduler_idle_window = float(
        os.environ.get("GL_SCHEDULER_IDLE_WINDOW", "300")
    )
    if scheduler_enabled:
        scheduler = BackgroundScheduler(
            coordinator,
            interval_seconds=scheduler_interval,
            idle_window_seconds=scheduler_idle_window,
        )
        scheduler.start()
        logger.info(
            f"Scheduler 已启动 (interval={scheduler_interval}s, "
            f"idle_window={scheduler_idle_window}s, smart_idle=True)"
        )
    else:
        logger.info(
            "Scheduler 未启用 (设 GL_SCHEDULER_ENABLED=true 打开)"
        )

    # iter #44: load persisted runtime config overrides (if any)
    if coordinator is not None and coordinator.persistence is not None:
        try:
            overrides = coordinator.persistence.load_runtime_config()
            for k, v in overrides.items():
                if _set_runtime_config(k, v):
                    logger.info(f"runtime_config: loaded override {k}={v}")
        except Exception as e:
            logger.warning(f"runtime_config load failed (ignore): {e}")


@app.on_event("shutdown")
async def shutdown_event():
    """应用关闭时停掉 scheduler"""
    global scheduler  # noqa: F824 (声明全局, 虽然只读)
    if scheduler is not None:
        logger.info("正在停止 Scheduler...")
        await scheduler.stop()
        logger.info("Scheduler 已停止")


# ============================================
# 数据模型
# ============================================
class OptimizationRequest(BaseModel):
    """优化请求"""
    run_simulation: bool = False
    simulation_days: int = 1
    # iter #8: caller 可控制 VRP 距离 (OSM vs Haversine)
    use_real_roads: bool = True
    region: Optional[str] = None
    # iter #12: caller 可控制是否推送 WS (避免手动 API 调用中重复推送给其他连接)
    ws_broadcast: bool = True


class BatchScenarioRequest(BaseModel):
    """iter #13: batch optimization 单个 scenario 的配置"""
    name: str = "scenario"        # 用户给的名字 (用于 response 识别)
    n_points: int = 4              # Pareto 点数
    time_limit_seconds: int = 3    # 每个点的时间限制
    co2_price: float = 0.0         # 碳价 (SEK/kg)
    use_real_roads: bool = True
    region: Optional[str] = None


class BatchOptimizeRequest(BaseModel):
    """iter #13: 批量优化请求 — 多个 scenarios 并行计算"""
    scenarios: List[BatchScenarioRequest]

    @field_validator("scenarios")
    @classmethod
    def _validate_scenarios(cls, v: List[BatchScenarioRequest]) -> List[BatchScenarioRequest]:
        if not (1 <= len(v) <= 8):
            raise ValueError("scenarios count must be in [1, 8]")
        for i, s in enumerate(v):
            if s.n_points < 2 or s.n_points > 20:
                raise ValueError(f"scenarios[{i}].n_points must be in [2, 20]")
            if s.time_limit_seconds < 1 or s.time_limit_seconds > 60:
                raise ValueError(f"scenarios[{i}].time_limit_seconds must be in [1, 60]")
            if s.co2_price < 0 or s.co2_price > 100:
                raise ValueError(f"scenarios[{i}].co2_price must be in [0, 100]")
        return v


class OptimizationResponse(BaseModel):
    """优化响应"""
    status: str
    optimization_id: Optional[str] = None
    timestamp: str
    matches_count: int
    total_tons: float
    total_cost_sek: float
    total_co2_kg: float
    # iter #8: 距离 source 反馈给前端
    distance_source: Optional[str] = None


class FleetStatusResponse(BaseModel):
    """车队状态响应"""
    total_vehicles: int
    available: int
    en_route: int
    utilization_rate: float
    # iter #6: 新增 fields
    loading: Optional[int] = 0
    total_distance_km: Optional[float] = 0.0
    avg_distance_to_depot_km: Optional[float] = 0.0
    depot: Optional[Dict[str, Any]] = None


class SupplyPoint(BaseModel):
    """供应点"""
    agent_id: str
    stock_tons: float
    material_type: str
    location: Dict[str, float]


# ============================================
# API 端点
# ============================================

@app.get("/")
async def root():
    """根路径"""
    return {
        "name": "Green Logistics AI",
        "version": "0.1.0",
        "status": "running"
    }


@app.get("/health")
async def health_check():
    """
    Health check + environment metadata.

    Returns:
        - status: "healthy" / "degraded"
        - timestamp: ISO 8601 UTC
        - environment: "production" / "development" (推断自 HF Space env vars)
        - data_mode: "real" (默认) — 表示 demand 使用真实瑞典设施
        - features: enabled feature flags

    Note: 这是 production mode endpoint, 不含 demo / sample data 路径。
    """
    is_hf_space = bool(os.environ.get("SPACE_ID"))  # HuggingFace Spaces env
    is_production = is_hf_space or os.environ.get("ENVIRONMENT") == "production"
    features = {
        "websocket_enabled": True,
        "carbon_scenarios": True,
        "seasonal_factors": True,
        "real_sweden_facilities": True,
        "scheduler_enabled": bool(os.environ.get("GL_SCHEDULER_ENABLED")),
    }
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "environment": "production" if is_production else "development",
        "data_mode": "real",  # production mode 只用真实数据
        "features": features,
    }


@app.get("/api/health/deep")
async def health_check_deep():
    """
    Deep health check (iter #14) — 检查所有依赖子系统的状态。

    返回每个子系统的 status (ok / degraded / down) + 详情:
    - database: SQLite 可读 + cycle 数
    - websocket: broadcaster client 数 + 状态
    - osrm: OSM 可用性 (尝试小范围 query, timeout 3s)
    - scheduler: enabled / running / cycle_count / dry_run
    - llm: GOOGLE_API_KEY 是否设置 + 最近一次调用是否成功
    - agents: supply/demand/vehicle 计数

    Status 总体:
    - all_ok = 所有子系统都 ok
    - degraded = 某些子系统 degraded 但仍可服务
    - down = 关键子系统 (database) 不可用

    用途: 诊断页面 / monitoring / 部署后验证
    """
    checks: Dict[str, Any] = {}
    overall_status = "ok"

    # 1. Database
    try:
        if coordinator is not None and coordinator.persistence is not None:
            summary = coordinator.persistence.get_summary() or {}
            checks["database"] = {
                "status": "ok",
                "n_cycles": summary.get("n_cycles", 0),
                "db_path": getattr(coordinator.persistence, "db_path", None),
            }
        else:
            checks["database"] = {
                "status": "degraded",
                "reason": "Persistence not initialized",
            }
            overall_status = "degraded"
    except Exception as e:
        checks["database"] = {"status": "down", "error": str(e)[:200]}
        overall_status = "down"

    # 2. WebSocket
    try:
        stats = ws_broadcaster.stats()
        checks["websocket"] = {
            "status": "ok" if stats.get("total_clients", 0) >= 0 else "down",
            "total_clients": stats.get("total_clients", 0),
            "broadcasts_sent": stats.get("broadcasts_sent", 0),
        }
    except Exception as e:
        checks["websocket"] = {"status": "down", "error": str(e)[:200]}
        overall_status = "degraded"

    # 3. OSRM / OSM availability (heuristic: try import osmnx, check cache)
    try:
        from optimization.real_distance import _osmnx_available  # type: ignore
        osmnx_ok = _osmnx_available()
        checks["osm"] = {
            "status": "ok" if osmnx_ok else "degraded",
            "osmnx_available": osmnx_ok,
            "reason": "OSM available" if osmnx_ok else "osmnx not installed or import failed",
        }
        if not osmnx_ok:
            overall_status = "degraded"
    except Exception as e:
        checks["osm"] = {"status": "degraded", "reason": str(e)[:200]}
        overall_status = "degraded"

    # 4. Scheduler
    sched = globals().get("scheduler")
    if sched is not None:
        try:
            sched_status = sched.status()
            checks["scheduler"] = {
                "status": "ok" if sched.scheduler_active else "idle",
                "active": sched.scheduler_active,
                "cycle_count": sched.cycle_count,
                "dry_run": sched.dry_run,
                "error_count": sched.error_count,
                "last_cycle_at": sched.last_cycle_at,
            }
        except Exception as e:
            checks["scheduler"] = {"status": "degraded", "error": str(e)[:200]}
    else:
        checks["scheduler"] = {
            "status": "idle",
            "reason": "GL_SCHEDULER_ENABLED is not set to true",
        }

    # 5. LLM (Gemini)
    try:
        api_key_set = bool(os.environ.get("GOOGLE_API_KEY"))
        if api_key_set:
            from agents.llm_config import get_llm_config
            cfg = get_llm_config()
            checks["llm"] = {
                "status": "ok",
                "api_key_set": True,
                "model": cfg.get("model"),
                "max_retries": cfg.get("max_retries"),
            }
        else:
            checks["llm"] = {
                "status": "degraded",
                "api_key_set": False,
                "reason": "GOOGLE_API_KEY not set (LLM predictions will use deterministic fallback)",
            }
            if overall_status == "ok":
                overall_status = "degraded"
    except Exception as e:
        checks["llm"] = {"status": "degraded", "error": str(e)[:200]}

    # 6. Agents
    try:
        if coordinator is not None:
            checks["agents"] = {
                "status": "ok",
                "n_supply": len(coordinator.supply_agents),
                "n_demand": len(coordinator.market_agent.demand_points)
                if hasattr(coordinator, "market_agent") else 0,
                "n_vehicles": len(coordinator.logistics_agent.vehicles)
                if hasattr(coordinator, "logistics_agent") else 0,
            }
        else:
            checks["agents"] = {
                "status": "down",
                "reason": "Coordinator not initialized",
            }
            overall_status = "down"
    except Exception as e:
        checks["agents"] = {"status": "degraded", "error": str(e)[:200]}
        overall_status = "degraded"

    return {
        "status": overall_status,
        "timestamp": datetime.now().isoformat(),
        "checks": checks,
    }


@app.get("/api/dashboard-summary")
async def get_dashboard_summary():
    """
    One-shot dashboard summary (iter #11) — 一次性返回 dashboard 所需所有数据。

    避免前端串行 N 个 fetch 调用。返回:
    - health: status / timestamp / features
    - summary: cycles / tons / cost / CO2 / avg util
    - efficiency: cost_per_ton / co2_per_ton / match_rate
    - fleet: total vehicles / available / utilization / distance
    - last_cycle: 最近 cycle 的精简 KPI (供 header badges)
    - scheduler: status (enabled, cycle_count, etc.)

    适用于: 首次加载 dashboard 时一次性拿全 state。
    """
    result: Dict[str, Any] = {
        "timestamp": datetime.now().isoformat(),
        "health": None,
        "summary": None,
        "efficiency": None,
        "fleet": None,
        "last_cycle": None,
        "scheduler": None,
    }
    # health
    is_hf_space = bool(os.environ.get("SPACE_ID"))
    is_production = is_hf_space or os.environ.get("ENVIRONMENT") == "production"
    result["health"] = {
        "status": "healthy",
        "environment": "production" if is_production else "development",
        "features": {
            "websocket_enabled": True,
            "carbon_scenarios": True,
            "seasonal_factors": True,
            "real_sweden_facilities": True,
            "scheduler_enabled": bool(os.environ.get("GL_SCHEDULER_ENABLED")),
        },
    }
    if coordinator is None:
        return result

    # summary + efficiency
    if coordinator.persistence is not None:
        try:
            result["summary"] = coordinator.persistence.get_summary()
        except Exception as e:
            result["summary"] = {"error": str(e)}
        try:
            result["efficiency"] = coordinator.persistence.get_efficiency_metrics()
        except Exception as e:
            result["efficiency"] = {"error": str(e)}

    # last cycle (from cached coordinator._last_cycle_result)
    last = getattr(coordinator, "_last_cycle_result", None)
    if last:
        result["last_cycle"] = {
            "sim_day": last.get("sim_day"),
            "sim_hour": last.get("sim_hour"),
            "total_cost_sek": last.get("total_cost_sek"),
            "total_co2_kg": last.get("total_co2_kg"),
            "total_tons": last.get("total_tons"),
            "n_matches": last.get("n_matches"),
            "distance_source": last.get("distance_source"),
            "fleet_utilization_pct": last.get("fleet_utilization_pct"),
        }

    # fleet status
    try:
        fleet = coordinator.persistence.get_summary() if coordinator.persistence else {}
        result["fleet"] = {
            "total_cycles": fleet.get("n_cycles", 0),
            "avg_utilization_pct": fleet.get("avg_utilization"),
        }
    except Exception as e:
        result["fleet"] = {"error": str(e)}

    # scheduler status
    sched = globals().get("scheduler")
    if sched is not None:
        try:
            result["scheduler"] = sched.status()
        except Exception as e:
            result["scheduler"] = {"error": str(e)}
    else:
        result["scheduler"] = {"enabled": False, "reason": "GL_SCHEDULER_ENABLED is not set to true"}

    return result


@app.websocket("/ws/cycle-updates")
async def ws_cycle_updates(ws: WebSocket):
    """
    WebSocket 推送。每个 coordinator cycle 完成后广播 cycle_update JSON。

    客户端连接后保持心跳 (server 主动 ping 10s 间隔)。
    断连 / 异常都会被清理。

    iter #27: Origin 校验 — 如果设置了 GL_WS_ALLOWED_ORIGINS env var,
    只接受 allowlist 里的 origin (防止任意网页连进来打 WebSocket)。
    """
    # iter #27: Origin 校验
    client_origin = ws.headers.get("origin")
    if not is_ws_origin_allowed(client_origin):
        logger.warning(
            f"WS rejected: origin={client_origin!r} not in allowlist "
            f"(allowed={_get_ws_allowed_origins()})"
        )
        # code=1008 = policy violation
        await ws.close(code=1008, reason="Origin not allowed")
        return
    # iter #32: max-client guard (全局 + per-IP)
    # 提取 client IP (uvicorn 传 x-forwarded-for / 直接 socket)
    client_ip: Optional[str] = None
    if ws.client and ws.client.host:
        client_ip = ws.client.host
    # x-forwarded-for (如果被反代)
    xff = ws.headers.get("x-forwarded-for")
    if xff:
        client_ip = xff.split(",")[0].strip()
    accepted = await ws_broadcaster.connect(ws, client_ip=client_ip)
    if not accepted:
        # code=1013 = try again later (server is at capacity)
        await ws.close(code=1013, reason="Server at capacity")
        return
    try:
        # 连接建立后立即推送一条 hello + 当前状态
        await ws.send_text(json.dumps({
            "type": "hello",
            "timestamp": datetime.now().isoformat(),
            "data": {
                "scheduler_stats": ws_broadcaster.stats(),
                "recent_cycle_count": (
                    (coordinator.persistence.get_summary() or {}).get("n_cycles", 0)
                    if coordinator else 0
                ),
            },
        }))
        while True:
            # server 主动 ping + 读 client 发来的 ping/pong (心跳)
            try:
                # wait_for 10s: 10s 内 client 发任何东西就读一下
                # 不发的话只是不 ping client — 不丢连接
                msg = await asyncio.wait_for(ws.receive_text(), timeout=10.0)
                # echo back (调试用)
                if msg == "ping":
                    await ws.send_text("pong")
            except asyncio.TimeoutError:
                # server 主动 ping 保持连接活跃
                try:
                    await ws.send_text(json.dumps({
                        "type": "keepalive",
                        "timestamp": datetime.now().isoformat(),
                    }))
                except RuntimeError:
                    break
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.warning(f"WS handler error: {e}")
    finally:
        await ws_broadcaster.disconnect(ws)


@app.get("/api/ws/stats")
async def ws_stats(_: None = Depends(require_admin)):
    """
    WebSocket 连接统计 (调试用)。

    iter #33: GL_ADMIN_TOKEN 设置后, 需要 admin token 才能访问 (防 IP 分布 / 连接
    计时等敏感信息泄漏)。支持两种方式:
    - Authorization: Bearer <token>
    - X-Admin-Token: <token>
    """
    stats = ws_broadcaster.stats()
    # iter #27: also expose origin allowlist info (for debug)
    allowed = _get_ws_allowed_origins()
    stats["origin_allowlist_active"] = bool(allowed)
    stats["origin_allowlist_size"] = len(allowed)
    if allowed:
        stats["origin_allowlist_sample"] = sorted(allowed)[:5]
    # iter #33: 告诉客户端 auth 是否启用 (避免静默禁用)
    stats["admin_token_configured"] = bool(_get_admin_token())
    return stats


@app.post("/api/ws/stats/reset")
async def ws_stats_reset(_: None = Depends(require_admin)):
    """
    iter #27: 重置 WebSocket broadcast 统计 (仅测试 / 调试用)。
    iter #33: 需要 admin token (同 ws_stats)。
    """
    ws_broadcaster.reset_stats()
    return {"reset": True, "stats": ws_broadcaster.stats()}


# ============================================
# iter #36: Public auth discovery endpoint
# ============================================
# This endpoint is intentionally NOT protected by require_admin — its purpose
# is to let unauthenticated clients discover whether auth is enabled and how
# to authenticate. Without it, debugging auth issues would require reading
# server-side env vars, which is impossible from outside.
#
# We deliberately expose only "safe" fields:
# - Whether auth is configured (boolean)
# - Token length + masked preview (helps client verify it has the right token
#   without leaking the actual secret)
# - List of header formats accepted
# - Count + paths of protected endpoints (already discoverable via OpenAPI)
@app.get("/api/admin/auth/status")
async def get_auth_status():
    """
    Public endpoint: report current admin auth state and usage.

    iter #36: Helps clients (admin UIs, debug scripts, CI runners) detect
    whether ``GL_ADMIN_TOKEN`` is set on the server before attempting to
    call protected endpoints.

    This endpoint is itself **not** protected so that an unauthenticated
    client can still ask "is auth enabled? if so, how do I send my token?".

    Response fields:
    - ``auth_enabled``: bool — whether ``GL_ADMIN_TOKEN`` is configured
    - ``token_length``: int — length of configured token (0 when unset)
    - ``token_preview``: str | null — ``"ab****yz"`` form for sanity check,
      or ``None`` when auth is disabled
    - ``header_formats``: list[str] — accepted ways to pass the token
    - ``protected_endpoints``: list[str] — paths that require admin auth
    - ``protected_endpoint_count``: int — number of protected endpoints
    - ``usage_hint``: str — short curl-style example
    """
    configured_token = _get_admin_token()
    auth_enabled = bool(configured_token)

    # Build a masked preview so clients can sanity-check they have the right
    # token without the server leaking the actual secret.
    # Format: first 2 + "****" + last 2 chars (only if token >= 8 chars)
    token_preview: Optional[str] = None
    if auth_enabled:
        if len(configured_token) >= 8:
            token_preview = f"{configured_token[:2]}****{configured_token[-2:]}"
        else:
            # Short token: just mask the whole thing
            token_preview = "****"

    return {
        "auth_enabled": auth_enabled,
        "token_length": len(configured_token),
        "token_preview": token_preview,
        "header_formats": [
            "Authorization: Bearer <token>",
            "X-Admin-Token: <token>",
        ],
        "protected_endpoints": _get_protected_endpoints(),
        "protected_endpoint_count": len(_get_protected_endpoints()),
        "usage_hint": (
            "curl -H 'Authorization: Bearer $GL_ADMIN_TOKEN' "
            "https://<host>/api/ws/stats"
            if auth_enabled
            else "Auth disabled — admin endpoints are currently public. "
            "Set GL_ADMIN_TOKEN to enable protection."
        ),
    }


def _get_protected_endpoints() -> list:
    """
    Return the static list of endpoints currently protected by ``require_admin``.

    iter #36: Single source of truth for both runtime auth and the
    ``/api/admin/auth/status`` discovery endpoint.

    The list is computed at call time (cheap — just a list literal) so that
    if a future iteration adds a new protected endpoint, this single source
    stays in sync via this function. The list itself is defined at module
    import, but the function call indirection means tests can patch it if
    needed.
    """
    # NOTE: keep in sync with all ``Depends(require_admin)`` usages above.
    # When you add a new protected endpoint, add it here too — the auth/status
    # endpoint will then expose it for client discovery.
    return [
        "/api/ws/stats",
        "/api/ws/stats/reset",
        "/api/debug/llm",
        "/api/persistence/forecast-method-prefs",  # GET (DELETE also protected)
        "/api/admin/db-maintenance",
        "/api/admin/db-export",
        "/api/admin/db-stats",
        "/api/admin/db-info",
        "/api/admin/perf-stats",
        "/api/admin/perf-stats/reset",
        "/api/admin/llm-stats",
        "/api/admin/llm-stats/reset",
        # iter #37: seasonal perturbation endpoints (CRUD)
        "/api/admin/seasonal-perturbations",
        # iter #42: db-maintenance recommendation + log
        "/api/admin/db-maintenance/recommendation",
        "/api/admin/db-maintenance/log",
        # iter #43: runtime config
        "/api/admin/runtime-config",  # GET, POST (also reset)
        # iter #44: runtime config batch + overrides
        "/api/admin/runtime-config/apply",  # POST
        "/api/admin/runtime-config/overrides",  # GET
    ]


@app.get("/api/debug/llm")
async def debug_llm(_: None = Depends(require_admin)):
    """诊断 LLM 配置 (env var + model + 试一次调用)"""
    from agents.llm_config import get_llm_config
    api_key = os.environ.get("GOOGLE_API_KEY", "")
    cfg = get_llm_config()
    result = {
        "google_api_key_set": bool(api_key),
        "google_api_key_length": len(api_key),
        "model_config": cfg["model"],
        "max_retries": cfg["max_retries"],
        "tests": {},
    }
    if not api_key:
        result["tests"] = {"basic": {"ok": False, "error": "GOOGLE_API_KEY not set"}}
        return result
    # 多场景测试
    from agents.llm_caller import call_gemini, GeminiAPIError
    tests = [
        ("basic_5tok", "Reply with exactly OK", 5),
        ("basic_20tok", "Reply with exactly OK", 20),
        ("basic_2048tok", "Reply with exactly OK", 2048),
        ("with_system", "Forecast: 1", 50),
        ("real_supply_prompt", "Forecast: multiplier 1.0, trend stable", 2048),
    ]
    for name, prompt, max_tok in tests:
        sys_instr = "You are a helpful assistant. Always respond with exactly what is asked." if name == "with_system" else None
        try:
            text = call_gemini(prompt, max_tokens=max_tok, system_instruction=sys_instr, caller=f"debug_llm.{name}")
            result["tests"][name] = {"ok": True, "response": text[:80], "len": len(text)}
        except GeminiAPIError as e:
            result["tests"][name] = {"ok": False, "error": str(e)[:300]}
        except Exception as e:
            result["tests"][name] = {"ok": False, "error": f"{type(e).__name__}: {str(e)[:300]}"}
    return result


@app.get("/api/status", response_model=Dict[str, Any])
async def get_system_status():
    """获取系统状态"""
    if coordinator is None:
        raise HTTPException(status_code=503, detail="System not initialized")
    
    overview = await coordinator.get_system_overview()
    return overview


@app.get("/api/fleet", response_model=FleetStatusResponse)
async def get_fleet_status():
    """获取车队状态"""
    if coordinator is None:
        raise HTTPException(status_code=503, detail="System not initialized")
    
    status = await coordinator.logistics_agent.get_fleet_status()
    return FleetStatusResponse(**status)


@app.get("/api/supply-points", response_model=List[SupplyPoint])
async def get_supply_points():
    """获取所有供应点"""
    if coordinator is None:
        raise HTTPException(status_code=503, detail="System not initialized")
    
    points = []
    for agent_id, agent in coordinator.supply_agents.items():
        stock = await agent.get_current_stock()
        points.append(SupplyPoint(**stock))
    
    return points


# Throttle: 防止用户连点 Run 按钮浪费 LLM 调用
# 30s 内的重复请求 → 返回上一次的缓存结果
_OPTIMIZE_CACHE_TTL_S = 30
_optimize_cache: Dict[str, Any] = {}
_optimize_cache_lock = asyncio.Lock()


@app.post("/api/optimize", response_model=OptimizationResponse)
async def run_optimization(request: OptimizationRequest = None):
    """运行优化 (纯按需 — 只在 Lovable 点 Run 按钮时跑)
    
    Throttle: 30s 内重复调用 → 返回缓存的最近一次结果 (avoid wasting LLM quota)
    如果没有 cache, 跑一次完整 cycle (25-30s)。
    """
    if coordinator is None:
        raise HTTPException(status_code=503, detail="System not initialized")

    now = time.monotonic()
    cached = _optimize_cache.get("last")
    if cached and (now - cached.get("ts", 0)) < _OPTIMIZE_CACHE_TTL_S:
        # 返缓存
        logger.info(f"Run 按钮命中 throttle, 返缓存 ({int(now - cached['ts'])}s 前跑的)")
        last_result = cached["result"]
        cached_flag = True
    else:
        # 跑新 cycle
        if request and request.run_simulation:
            results = await coordinator.simulate_day(days=request.simulation_days)
            last_result = results[-1]
        else:
            # iter #8: 传递 use_real_roads + region 到 coordinator
            use_real_roads = request.use_real_roads if request else True
            region = request.region if request else None
            last_result = await coordinator.run_optimization_cycle(
                use_real_roads=use_real_roads,
                region=region,
            )
        # 写 cache
        async with _optimize_cache_lock:
            _optimize_cache["last"] = {"ts": time.monotonic(), "result": last_result}
        cached_flag = False
        logger.info(f"Run 按钮触发新 cycle: {last_result.get('optimization_id')}")

    # 提取关键指标
    matches = last_result.get("matches", {})
    routes = last_result.get("route_optimization", {})

    # iter #12: 如果 caller 控制 ws_broadcast=False 且是刚跑的 (非 cache),
    # 主动推送给已连的 WS client (之前 scheduler 路径会推, 现在 API 路径下也推一次)
    if not cached_flag and request and request.ws_broadcast:
        try:
            await _broadcast_cycle_update(last_result)
        except Exception as e:
            logger.debug(f"WS broadcast in /api/optimize failed (ignore): {e}")

    return OptimizationResponse(
        status="success" if not cached_flag else "cached",
        optimization_id=last_result.get("optimization_id"),
        timestamp=last_result.get("timestamp"),
        matches_count=matches.get("total_matches", 0),
        total_tons=matches.get("total_tons", 0),
        total_cost_sek=routes.get("total_cost_sek", 0),
        total_co2_kg=routes.get("total_co2_kg", 0),
        distance_source=last_result.get("distance_source"),
    )


@app.get("/api/facilities")
async def get_facilities(
    city: Optional[str] = None,
    facility_type: Optional[str] = None,
    include_distance_to_depot: bool = True,
):  # 接受 'true' / 'false' 字符串
    include_distance_to_depot = str(include_distance_to_depot).lower() not in ("false", "0", "no")
    """
    返回真实瑞典废料处理设施 (Renova / Ragn-Sells / Stena / Swerock / Suez / Sysav 等)。

    数据源: data/real_sweden_facilities (手工整理的 13 个公司公开设施坐标)
    Query:
        city: 可选过滤 (Borås / Göteborg / Stockholm)
        facility_type: 可选过滤 (recycling_center, metal_recovery, ...)
        include_distance_to_depot: bool = True (添加 distance_to_depot_km haversine)

    响应 includes:
        - facilities[i].distance_to_depot_km: 该设施到 Borås depot 的 haversine 距离 (km)
    """
    from data.real_sweden_facilities import (
        ALL_FACILITIES,
        FACILITY_TYPE_COUNTS,
        get_facilities_by_city,
        get_facilities_by_type,
        get_facility_count,
    )

    if city:
        facilities = [dict(f) for f in get_facilities_by_city(city)]
    elif facility_type:
        facilities = [dict(f) for f in get_facilities_by_type(facility_type)]
    else:
        # 复制防止 mutate module-level ALL_FACILITIES
        facilities = [dict(f) for f in ALL_FACILITIES]

    # 加 distance_to_depot_km (haversine 到 Borås depot)
    if include_distance_to_depot:
        from agents.world_builder import CITY_CENTERS
        from agents.market_agent import _haversine_km
        depot_lat, depot_lon = CITY_CENTERS["Borås"]
        for f in facilities:
            f["distance_to_depot_km"] = round(
                _haversine_km(f["lat"], f["lon"], depot_lat, depot_lon),
                2,
            )

    return {
        "total": len(facilities),
        "total_available": get_facility_count(),
        "facility_type_counts": FACILITY_TYPE_COUNTS,
        "depot": {"city": "Borås", "lat": depot_lat, "lon": depot_lon} if include_distance_to_depot else None,
        "facilities": facilities,
    }


@app.get("/api/seasonal-factors")
async def get_seasonal_factors(sim_day: Optional[int] = None):
    """
    返回 Swedish 月度废料季节因子 (Avfall Sverige 2023, 图 4.2)。

    Query:
        sim_day: 可选, 0-indexed simulation day。不传则返回全年 12 个月表。
                  传则额外返回当前 sim_day 对应的 month + factor。

    Response:
        {
            "current_sim_day": int | None,
            "current_month": int | None,
            "factors_by_month": {1: {mat: f}, 2: {...}, ..., 12: {...}},
            "current_factors": {mat: f} | None
        }
    """
    from data.seasonal_adjuster import (
        get_all_factors,
        sim_day_to_month,
    )

    factors_by_month = {m: get_all_factors(m) for m in range(1, 13)}
    current_month = None
    current_factors = None
    if sim_day is not None:
        try:
            sim_day_int = int(sim_day)
            current_month = sim_day_to_month(sim_day_int)
            current_factors = get_all_factors(current_month)
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="sim_day must be integer")

    return {
        "current_sim_day": sim_day,
        "current_month": current_month,
        "factors_by_month": factors_by_month,
        "current_factors": current_factors,
        # iter #37: include active perturbations so the frontend can show
        # shock indicators (e.g. "weather event active: -30% all materials").
        # Pulled from coordinator.persistence (single source of truth);
        # works even when no admin token is set.
        "active_perturbations": (
            coordinator.persistence.get_active_perturbations(int(sim_day))
            if (sim_day is not None and coordinator is not None
                and coordinator.persistence is not None)
            else []
        ),
    }


@app.get("/api/optimize/last")
async def get_last_optimization():
    """返回上一次 cycle 的指标 + 多久前跑的, 供前端展示 'Last updated: 5 min ago'

    iter #12 扩展: 加入 seasonal_factor + month + cost_per_ton + efficiency 指标。
    """
    if coordinator is None:
        raise HTTPException(status_code=503, detail="System not initialized")
    if coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")

    summary = coordinator.persistence.get_summary() or {}
    recent = coordinator.persistence.get_recent_cycles(limit=1) or []
    last_cycle = recent[0] if recent else {}
    last_ts = last_cycle.get("wall_timestamp")

    age_seconds = None
    if last_ts:
        try:
            # wall_timestamp 是 naive 本地时间 (Dockerfile 设了 TZ=Europe/Stockholm),
            # 用 datetime.now() (naive local) 对比, 不要用 utcnow()
            last_dt = datetime.fromisoformat(last_ts)
            age_seconds = round((datetime.now() - last_dt).total_seconds(), 1)
        except Exception:
            pass

    # iter #8: distance_source 从 coordinator 上次 cycle 拿 (in-memory cache)
    distance_source = "unknown"
    last_cycle_meta = {}
    if hasattr(coordinator, "_last_cycle_result") and coordinator._last_cycle_result:
        last_cycle_meta = coordinator._last_cycle_result or {}
        distance_source = last_cycle_meta.get("distance_source", "unknown")
    elif hasattr(coordinator, "last_distance_source"):
        distance_source = coordinator.last_distance_source

    # iter #12: 计算 cost_per_ton / co2_per_ton / fleet_util
    last_tons = last_cycle.get("total_tons") or 0
    last_cost = last_cycle.get("total_cost_sek") or 0
    last_co2 = last_cycle.get("total_co2_kg") or 0
    cost_per_ton = round(last_cost / last_tons, 2) if last_tons > 0 else None
    co2_per_ton = round(last_co2 / last_tons, 2) if last_tons > 0 else None

    # iter #12: aggregate efficiency (全期, 不是 last cycle)
    efficiency = None
    try:
        efficiency = coordinator.persistence.get_efficiency_metrics()
    except Exception:
        pass

    return {
        "last_cycle_id": last_cycle.get("cycle_id"),
        "last_cycle_at": last_ts,
        "age_seconds": age_seconds,
        "total_cycles": summary.get("n_cycles", 0),
        "total_tons": summary.get("total_tons", 0),
        "total_cost_sek": summary.get("total_cost_sek", 0),
        "total_co2_kg": summary.get("total_co2_kg", 0),
        "distance_source": distance_source,
        # iter #12: last-cycle 详情
        "last_sim_day": last_cycle.get("sim_day"),
        "last_sim_hour": last_cycle.get("sim_hour"),
        "last_n_matches": last_cycle.get("n_matches"),
        "last_seasonal_factor_avg": last_cycle.get("seasonal_factor_avg"),
        "last_seasonal_month": last_cycle.get("seasonal_month"),
        "last_cost_per_ton_sek": cost_per_ton,
        "last_co2_per_ton_kg": co2_per_ton,
        "last_fleet_utilization_pct": last_cycle.get("fleet_utilization_pct"),
        "last_solver_status": last_cycle.get("solver_status"),
        "last_wall_duration_ms": last_cycle.get("wall_duration_ms"),
        "last_distance_km": last_cycle.get("total_distance_km"),
        "last_n_vehicles_used": last_cycle.get("n_vehicles_used"),
        "last_n_vehicles_available": last_cycle.get("n_vehicles_available"),
        # iter #12: 全期 efficiency 聚合
        "efficiency": efficiency,
    }


# ============================================
# iter #40: Long-running simulation runner
# ============================================
# One-shot endpoint to run N days of simulation synchronously and persist
# all cycles + KPIs to the DB. Useful for:
# - Filling analytics data so perturbation-impact / forecast / cohort panels
#   have something to show (today HF has only ~30 cycles).
# - Quick demo from the dashboard "Run 30-day sim" button.
# - Stress-testing the persistence layer with realistic data volumes.
#
# NOT for production traffic — runs OR-Tools once per day. Use the
# scheduler (/api/scheduler/control) for continuous background runs.
@app.post("/api/simulate/run")
async def run_simulation(
    days: int = 7,
    dry_run: bool = False,
):
    """
    iter #40: Run N days of multi-agent simulation synchronously.

    Body (query params):
    - days (default 7): number of simulation days to run. Clamped to [1, 90].
    - dry_run (default False): if True, skip persistence (compute-only mode).

    Returns:
    {
      "status": "success",
      "days_requested": int,
      "cycles_completed": int,
      "first_sim_day": int,
      "last_sim_day": int,
      "wall_duration_seconds": float,
      "kpi_summary": {
        "total_tons", "total_cost_sek", "total_co2_kg",
        "avg_fleet_utilization_pct", "n_matches_total",
      },
      "per_day": [{sim_day, cycle_id, n_matches, total_tons,
                   total_cost_sek, total_co2_kg, fleet_utilization_pct}, ...]
    }
    """
    if coordinator is None:
        raise HTTPException(status_code=503, detail="System not initialized")
    if coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")

    # Validate inputs
    if not isinstance(days, int):
        try:
            days = int(days)
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail=f"days must be int, got {type(days).__name__}")
    if days < 1 or days > 90:
        raise HTTPException(
            status_code=400,
            detail=f"days must be in [1, 90]; got {days} "
            f"(90-day cap to avoid runaway OR-Tools solves)",
        )

    # Capture starting sim_day for reporting
    starting_sim_day = coordinator.clock.now.day if hasattr(coordinator, "clock") else None
    starting_cycle_count_before = (
        coordinator.persistence.get_summary().get("n_cycles", 0)
        if hasattr(coordinator.persistence, "get_summary") else 0
    )

    import time as _time
    t0 = _time.time()
    try:
        # If dry_run, temporarily flag coordinator to skip persistence writes.
        # coordinator.run_optimization_cycle already supports dry_run internally
        # (iter #12). We delegate by passing it through simulate_day's logic:
        # simulate_day just calls run_optimization_cycle repeatedly.
        if dry_run:
            # Patch the run_optimization_cycle via the coordinator's flag
            # (simulate_day itself doesn't accept dry_run; use a temporary
            # monkey-patch on the coordinator's method).
            original = coordinator.run_optimization_cycle
            async def _dry_cycle(*args, **kwargs):
                kwargs["dry_run"] = True
                return await original(*args, **kwargs)
            coordinator.run_optimization_cycle = _dry_cycle  # type: ignore
            try:
                results = await coordinator.simulate_day(days=days)
            finally:
                coordinator.run_optimization_cycle = original  # type: ignore
        else:
            results = await coordinator.simulate_day(days=days)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Simulation failed after {days} days: {type(e).__name__}: {e}",
        )
    elapsed_s = round(_time.time() - t0, 2)

    # Aggregate KPIs
    kpi_summary = {
        "total_tons": 0.0,
        "total_cost_sek": 0.0,
        "total_co2_kg": 0.0,
        "n_matches_total": 0,
        "fleet_utilization_sum": 0.0,
        "fleet_utilization_count": 0,
    }
    per_day = []
    for r in results:
        # run_optimization_cycle returns dict with cycle_id + kpi sub-dict
        kpi = r.get("kpi", {}) if isinstance(r, dict) else {}
        if kpi:
            kpi_summary["total_tons"] += float(kpi.get("total_tons", 0) or 0)
            kpi_summary["total_cost_sek"] += float(kpi.get("total_cost_sek", 0) or 0)
            kpi_summary["total_co2_kg"] += float(kpi.get("total_co2_kg", 0) or 0)
            kpi_summary["n_matches_total"] += int(kpi.get("n_matches", 0) or 0)
            util = kpi.get("fleet_utilization_pct")
            if util is not None:
                kpi_summary["fleet_utilization_sum"] += float(util)
                kpi_summary["fleet_utilization_count"] += 1
        # Per-day summary (compact). sim_day + cycle_id come from TOP-LEVEL
        # of run_optimization_cycle result (not from kpi sub-dict).
        per_day.append({
            "sim_day": r.get("sim_day") if isinstance(r, dict) else None,
            "cycle_id": r.get("optimization_id") if isinstance(r, dict) else None,
            "n_matches": kpi.get("n_matches"),
            "total_tons": kpi.get("total_tons"),
            "total_cost_sek": kpi.get("total_cost_sek"),
            "total_co2_kg": kpi.get("total_co2_kg"),
            "fleet_utilization_pct": kpi.get("fleet_utilization_pct"),
        })

    # Round aggregates
    avg_util = (
        round(kpi_summary["fleet_utilization_sum"] / kpi_summary["fleet_utilization_count"], 2)
        if kpi_summary["fleet_utilization_count"] > 0 else None
    )
    final_sim_day = None
    if per_day and per_day[-1].get("sim_day") is not None:
        final_sim_day = per_day[-1]["sim_day"]

    return {
        "status": "success",
        "days_requested": days,
        "dry_run": dry_run,
        "cycles_completed": len(results),
        "first_sim_day": starting_sim_day,
        "last_sim_day": final_sim_day,
        "wall_duration_seconds": elapsed_s,
        "kpi_summary": {
            "total_tons": round(kpi_summary["total_tons"], 2),
            "total_cost_sek": round(kpi_summary["total_cost_sek"], 2),
            "total_co2_kg": round(kpi_summary["total_co2_kg"], 2),
            "n_matches_total": kpi_summary["n_matches_total"],
            "avg_fleet_utilization_pct": avg_util,
        },
        "per_day": per_day,
    }


# ============================================
# V3: Pareto 前沿端点
# ============================================
@app.get("/api/optimize/pareto")
async def get_pareto_front(
    n_points: int = 10,
    time_limit_seconds: int = 5,
    use_real_roads: bool = True,
    region: Optional[str] = None,
):
    """
    返回多目标 (cost vs CO2) Pareto 前沿

    - n_points: 扫描权重点数 (2..20)
    - time_limit_seconds: 每个点的 OR-Tools 时限
    - use_real_roads: bool = True (iter #7, OSM 真实路网 vs Haversine)
    - region: OSM 地区名 (default: coordinator 推断 / 'Borås, Sweden')

    用 coordinator 当前世界的 supply/demand/vehicle 状态构建 VRPSolver。
    """
    if coordinator is None:
        raise HTTPException(status_code=503, detail="System not initialized")
    if n_points < 2 or n_points > 20:
        raise HTTPException(
            status_code=400,
            detail="n_points must be in [2, 20]",
        )

    from optimization.vrp_solver import VRPSolver, Location, Vehicle

    # 收集 supply offers — 用 daily_capacity * 0.8 重建 supply (跟 cycle 一致)
    # 不读 current_stock: warmup / 上次 cycle 已把它消耗成 0, 读不出东西
    # daily_capacity 是世界引导时定的, 跟陈旧 state 解耦
    supply_offers = []
    for agent_id, agent in coordinator.supply_agents.items():
        supply_offers.append({
            "agent_id": agent_id,
            "available_tons": round(agent.daily_capacity * 0.8, 2),
            "material_type": agent.material_type,
            "location": agent.location,
        })

    demand_requests = []
    for dp in coordinator.market_agent.demand_points:
        demand_requests.append({
            "id": dp["id"],
            "name": dp["name"],
            "demand_tons": dp["current_demand_tons"],
            "preferred_materials": dp["preferred_materials"],
            "location": dp["location"],
            "material_type": dp.get("material_type"),
        })

    # 重新匹配
    matches_result = await coordinator.market_agent.match_supply_demand(
        supply_offers=supply_offers,
        demand_requests=demand_requests,
    )
    matches = matches_result.get("matches", [])

    if not matches:
        # 没有 matches 也要返 200 + 空结构, 让 Lovable 前端不报错
        return {
            "n_points": 0,
            "n_pickups": 0,
            "n_deliveries": 0,
            "n_vehicles": 0,
            "pareto": [],
            "reason": "No matches available to build VRP problem",
        }

    # 取前 15 个匹配，避免 OR-Tools 超时
    matches = matches[:15]

    # 收集 depot + pickup + delivery
    depot_loc = coordinator.logistics_agent.depot_location
    depot = Location(id="DEPOT", lat=depot_loc["lat"], lon=depot_loc["lon"], type="depot")

    supply_idx = {a.agent_id: a for a in coordinator.supply_agents.values()}
    demand_idx = {d["id"]: d for d in coordinator.market_agent.demand_points}

    pickup_locations = []
    delivery_locations = []
    for m in matches:
        sid = m.get("supply_id")
        did = m.get("demand_id")
        if sid not in supply_idx or did not in demand_idx:
            continue
        sup = supply_idx[sid]
        dem = demand_idx[did]
        pickup_locations.append({
            "id": sid,
            "lat": sup.location["lat"],
            "lon": sup.location["lon"],
            "tons": m.get("tons", 5.0),
        })
        delivery_locations.append({
            "id": did, "lat": dem["location"]["lat"], "lon": dem["location"]["lon"],
            "tons": m.get("tons", 5.0),
        })

    if not pickup_locations:
        return {
            "n_points": 0,
            "n_pickups": 0,
            "n_deliveries": 0,
            "n_vehicles": 0,
            "pareto": [],
            "reason": "No usable supply/demand locations",
        }

    # 配车辆（不超过 pickup 数）
    vehicles_data = [
        v for v in coordinator.logistics_agent.vehicles
        if v.get("status") == "available"
    ][:len(pickup_locations)]
    if not vehicles_data:
        return {
            "n_points": 0,
            "n_pickups": len(pickup_locations),
            "n_deliveries": len(delivery_locations),
            "n_vehicles": 0,
            "pareto": [],
            "reason": "No vehicles available",
        }

    # 构建 solver 并扫描 Pareto
    solver_kwargs: Dict[str, Any] = {"use_real_roads": use_real_roads}
    if region:
        solver_kwargs["region"] = region
    solver = VRPSolver(**solver_kwargs)
    solver.add_location(depot)
    for loc in pickup_locations:
        solver.add_location(Location(
            id=loc["id"], lat=loc["lat"], lon=loc["lon"],
            demand_tons=loc["tons"], type="pickup",
        ))
    for loc in delivery_locations:
        solver.add_location(Location(
            id=loc["id"], lat=loc["lat"], lon=loc["lon"],
            demand_tons=-loc["tons"], type="delivery",
        ))
    for vd in vehicles_data:
        solver.add_vehicle(Vehicle(
            id=vd["vehicle_id"],
            capacity_tons=vd.get("capacity_tons", 20.0),
            start_location=depot,
            co2_rate=vd.get("co2_emission_rate", 0.85),
            cost_per_km=2.6,
        ))

    # OR-Tools solve_pareto 是 CPU 密集同步调用, 直接在 async 函数里会
    # 阻塞 event loop 20-30s, 导致 /api/status / /api/fleet / /health 全排队
    # 用 asyncio.to_thread 丢到线程池, 让 event loop 处理其他请求
    pareto = await asyncio.to_thread(
        solver.solve_pareto,
        n_points=n_points, time_limit_seconds=time_limit_seconds,
    )

    # 序列化：去掉完整 routes（保留数量）
    summary = []
    for p in pareto:
        summary.append({
            "cost_weight": p["cost_weight"],
            "co2_weight": p["co2_weight"],
            "cost_sek": p["cost_sek"],
            "co2_kg": p["co2_kg"],
            "total_objective": p["total_objective"],
            "total_distance_km": p["total_distance_km"],
            "n_routes": len(p["routes"]),
            "status": p["status"],
            "distance_source": p.get("distance_source", "unknown"),
        })

    return {
        "n_points": len(summary),
        "n_pickups": len(pickup_locations),
        "n_deliveries": len(delivery_locations),
        "n_vehicles": len(vehicles_data),
        "pareto": summary,
        "distance_source": solver.distance_source,
        "use_real_roads": solver.use_real_roads,
    }


@app.get("/api/optimize/carbon-scenarios")
async def get_carbon_scenarios(
    carbon_prices: Optional[str] = None,
    time_limit_seconds: int = 3,
    use_real_roads: bool = True,
):
    """
    碳税情景分析：跑多个碳价下的 Pareto 前沿。

    默认场景（基于现实）：
    - 0.0 SEK/kg = 无碳税
    - 1.5 SEK/kg ≈ EU ETS 当前水平（~100 EUR/t）
    - 3.0 SEK/kg ≈ 2030 中间预测
    - 5.0 SEK/kg ≈ 激进碳税情景

    Query param: carbon_prices = "0,1.5,3,5" (逗号分隔)
    """
    if coordinator is None:
        raise HTTPException(status_code=503, detail="System not initialized")

    # 解析碳价列表
    if carbon_prices:
        try:
            prices = [float(p) for p in carbon_prices.split(",") if p]
        except ValueError:
            raise HTTPException(status_code=400, detail="carbon_prices must be comma-separated numbers")
        if not (1 <= len(prices) <= 8):
            raise HTTPException(status_code=400, detail="carbon_prices count must be in [1, 8]")
    else:
        # 默认 4 个场景
        prices = [0.0, 1.5, 3.0, 5.0]

    # 复用 /api/optimize/pareto 的世界构建逻辑
    from optimization.vrp_solver import VRPSolver, Location, Vehicle

    supply_offers = []
    for agent_id, agent in coordinator.supply_agents.items():
        supply_offers.append({
            "agent_id": agent_id,
            "available_tons": round(agent.daily_capacity * 0.8, 2),
            "material_type": agent.material_type,
            "location": agent.location,
        })
    demand_requests = []
    for dp in coordinator.market_agent.demand_points:
        demand_requests.append({
            "id": dp["id"],
            "name": dp["name"],
            "demand_tons": dp["current_demand_tons"],
            "preferred_materials": dp["preferred_materials"],
            "location": dp["location"],
            "material_type": dp.get("material_type"),
        })
    matches_result = await coordinator.market_agent.match_supply_demand(
        supply_offers=supply_offers,
        demand_requests=demand_requests,
    )
    matches = matches_result.get("matches", [])[:15]
    if not matches:
        return {
            "scenarios": [],
            "reason": "No matches available to build VRP problem",
        }

    depot_loc = coordinator.logistics_agent.depot_location
    depot = Location(id="DEPOT", lat=depot_loc["lat"], lon=depot_loc["lon"], type="depot")
    supply_idx = {a.agent_id: a for a in coordinator.supply_agents.values()}
    demand_idx = {d["id"]: d for d in coordinator.market_agent.demand_points}

    pickup_locations = []
    delivery_locations = []
    for m in matches:
        sid = m.get("supply_id")
        did = m.get("demand_id")
        if sid not in supply_idx or did not in demand_idx:
            continue
        sup = supply_idx[sid]
        dem = demand_idx[did]
        pickup_locations.append({
            "id": sid, "lat": sup.location["lat"], "lon": sup.location["lon"],
            "tons": m.get("tons", 5.0),
        })
        delivery_locations.append({
            "id": did, "lat": dem["location"]["lat"], "lon": dem["location"]["lon"],
            "tons": m.get("tons", 5.0),
        })
    if not pickup_locations:
        return {"scenarios": [], "reason": "No usable supply/demand locations"}

    vehicles_data = [
        v for v in coordinator.logistics_agent.vehicles
        if v.get("status") == "available"
    ][:len(pickup_locations)]
    if not vehicles_data:
        return {"scenarios": [], "reason": "No vehicles available"}

    def _build_solver() -> VRPSolver:
        solver = VRPSolver(use_real_roads=use_real_roads)
        solver.add_location(depot)
        for loc in pickup_locations:
            solver.add_location(Location(
                id=loc["id"], lat=loc["lat"], lon=loc["lon"],
                demand_tons=loc["tons"], type="pickup",
            ))
        for loc in delivery_locations:
            solver.add_location(Location(
                id=loc["id"], lat=loc["lat"], lon=loc["lon"],
                demand_tons=-loc["tons"], type="delivery",
            ))
        for vd in vehicles_data:
            solver.add_vehicle(Vehicle(
                id=vd["vehicle_id"],
                capacity_tons=vd.get("capacity_tons", 20.0),
                start_location=depot,
                co2_rate=vd.get("co2_emission_rate", 0.85),
                cost_per_km=2.6,
            ))
        return solver

    async def _solve_scenario(price: float) -> Dict[str, Any]:
        solver = _build_solver()
        # 4 个 Pareto 点代表 4 种 cost/co2 权衡
        pareto = await asyncio.to_thread(
            solver.solve_pareto,
            n_points=4,
            time_limit_seconds=time_limit_seconds,
            co2_price=price,
        )
        # 从 pareto 里取 cost-optimal (weight=1,0) 和 co2-optimal (weight=0,1)
        cost_opt = next((p for p in pareto if abs(p["cost_weight"] - 1.0) < 1e-6), None)
        co2_opt = next((p for p in pareto if abs(p["co2_weight"] - 1.0) < 1e-6), None)
        return {
            "carbon_price_sek_per_kg": price,
            "cost_optimal": {
                "cost_sek": cost_opt["cost_sek"] if cost_opt else None,
                "co2_kg": cost_opt["co2_kg"] if cost_opt else None,
                "n_routes": len(cost_opt["routes"]) if cost_opt else 0,
            } if cost_opt else None,
            "co2_optimal": {
                "cost_sek": co2_opt["cost_sek"] if co2_opt else None,
                "co2_kg": co2_opt["co2_kg"] if co2_opt else None,
                "n_routes": len(co2_opt["routes"]) if co2_opt else 0,
            } if co2_opt else None,
            "pareto": [
                {
                    "cost_weight": p["cost_weight"],
                    "co2_weight": p["co2_weight"],
                    "cost_sek": p["cost_sek"],
                    "co2_kg": p["co2_kg"],
                    "total_objective": p["total_objective"],
                }
                for p in pareto
            ],
        }

    scenarios = await asyncio.gather(*[_solve_scenario(p) for p in prices])

    # iter #39: compute TRUE total cost analytics per scenario so the
    # frontend can quickly answer "carbon tax X SEK/kg increases cost by Y%".
    # IMPORTANT: cost-optimal.cost_sek is just fuel/operating cost (co2_weight=0),
    # so it stays the same across tax levels. To show real cost sensitivity,
    # we compute true_total_cost = cost_sek + tax * co2_kg for each routing.
    sorted_scenarios = sorted(scenarios, key=lambda s: s["carbon_price_sek_per_kg"])
    baseline_true_cost_opt = None
    if sorted_scenarios and sorted_scenarios[0].get("cost_optimal"):
        bco = sorted_scenarios[0]["cost_optimal"]
        bprice = sorted_scenarios[0]["carbon_price_sek_per_kg"]
        if bco.get("cost_sek") is not None and bco.get("co2_kg") is not None:
            baseline_true_cost_opt = bco["cost_sek"] + bprice * bco["co2_kg"]

    for s in scenarios:
        price = s["carbon_price_sek_per_kg"]
        cost_opt = s.get("cost_optimal") or {}
        co2_opt = s.get("co2_optimal") or {}
        # True total cost for cost-optimal routing at this tax level
        s["true_total_cost_cost_opt"] = None
        if cost_opt.get("cost_sek") is not None and cost_opt.get("co2_kg") is not None:
            s["true_total_cost_cost_opt"] = round(
                cost_opt["cost_sek"] + price * cost_opt["co2_kg"], 2
            )
        # True total cost for co2-optimal routing (already includes tax in solver)
        s["true_total_cost_co2_opt"] = None
        if co2_opt.get("cost_sek") is not None and co2_opt.get("co2_kg") is not None:
            s["true_total_cost_co2_opt"] = round(
                co2_opt["cost_sek"] + price * co2_opt["co2_kg"], 2
            )
        # Delta from baseline (cost-opt strategy, true total)
        s["delta_from_baseline_pct"] = None
        if baseline_true_cost_opt and baseline_true_cost_opt > 0 and s["true_total_cost_cost_opt"] is not None:
            s["delta_from_baseline_pct"] = round(
                (s["true_total_cost_cost_opt"] - baseline_true_cost_opt) / baseline_true_cost_opt * 100, 2
            )
        # CO2 delta vs baseline (cost-opt strategy: should drop as tax rises)
        s["co2_delta_from_baseline_pct"] = None
        baseline_co2_opt = None
        if sorted_scenarios and sorted_scenarios[0].get("cost_optimal"):
            baseline_co2_opt = sorted_scenarios[0]["cost_optimal"].get("co2_kg")
        if baseline_co2_opt and baseline_co2_opt > 0 and cost_opt.get("co2_kg") is not None:
            s["co2_delta_from_baseline_pct"] = round(
                (cost_opt["co2_kg"] - baseline_co2_opt) / baseline_co2_opt * 100, 2
            )

    # iter #39: compute breakeven analysis — find carbon price at which
    # cost-optimal and co2-optimal converge (or come closest). Useful for
    # operators to understand sensitivity: "at what tax rate do we naturally
    # optimize for CO2 anyway?"
    breakeven_price = None
    breakeven_gap_sek = None
    for s in sorted_scenarios:
        cost_opt = s.get("cost_optimal") or {}
        co2_opt = s.get("co2_optimal") or {}
        cost_cost = cost_opt.get("cost_sek")
        co2_cost = co2_opt.get("cost_sek")
        if cost_cost is None or co2_cost is None:
            continue
        gap = abs(cost_cost - co2_cost)
        if breakeven_gap_sek is None or gap < breakeven_gap_sek:
            breakeven_gap_sek = gap
            breakeven_price = s["carbon_price_sek_per_kg"]

    return {
        "n_pickups": len(pickup_locations),
        "n_deliveries": len(delivery_locations),
        "n_vehicles": len(vehicles_data),
        "scenarios": scenarios,
        "use_real_roads": use_real_roads,
        # iter #39: analytics fields
        "baseline_carbon_price_sek_per_kg": (
            sorted_scenarios[0]["carbon_price_sek_per_kg"] if sorted_scenarios else None
        ),
        "breakeven_price_sek_per_kg": breakeven_price,
        "breakeven_gap_sek": breakeven_gap_sek,
    }


# ---------------------------------------------------------------------------
# iter #43: Runtime configuration registry — hot-tunable params
# ---------------------------------------------------------------------------
# Allows ops to change behavior (default carbon price, time limits, etc.)
# without restarting the service. Settings are in-memory (not persisted) so
# they reset on restart; combine with env vars for production defaults.

_RUNTIME_CONFIG_DEFAULTS: Dict[str, Any] = {
    "default_carbon_price_sek_per_kg": 1.5,        # CO2税默认 (used by /optimize/carbon-scenarios)
    "default_solver_time_limit_seconds": 3,          # OR-Tools budget per scenario
    "default_forecast_horizon": 7,                   # /api/persistence/forecast default horizon
    "default_forecast_history_n": 14,                # /api/persistence/forecast default history_n
    "max_routes_per_vehicle": 5,                     # max stops per vehicle
    "default_use_real_roads": True,                  # OSM distance vs haversine
    "sweet_spot_weight_cost": 0.5,                   # /api/optimize/sweet-spot default weight_cost
    "sweet_spot_weight_co2": 0.5,                    # /api/optimize/sweet-spot default weight_co2
    "auto_vacuum_enabled": False,                    # auto-trigger vacuum after each cycle
    "ws_max_clients_override": None,                 # None = use env GL_WS_MAX_CLIENTS
    "cohort_n_periods": 4,                           # /api/persistence/cohort-retention-by-period default
    "calibration_mape_warning_pct": 20.0,            # warning threshold for ForecastCalibration
    "perturbation_max_multiplier": 3.0,              # clamp upper bound for shocks
}
_runtime_config: Dict[str, Any] = dict(_RUNTIME_CONFIG_DEFAULTS)


def _get_runtime_config(key: str) -> Any:
    """Get a runtime config value (falls back to default)."""
    return _runtime_config.get(key, _RUNTIME_CONFIG_DEFAULTS.get(key))


def _set_runtime_config(key: str, value: Any) -> bool:
    """Set a runtime config value. Returns True if valid, False otherwise."""
    if key not in _RUNTIME_CONFIG_DEFAULTS:
        return False
    default = _RUNTIME_CONFIG_DEFAULTS[key]
    # Type validation
    if default is None:
        # Allow any type when default is None
        pass
    elif isinstance(default, bool):
        if not isinstance(value, bool):
            return False
    elif isinstance(default, int):
        if not isinstance(value, int) or isinstance(value, bool):
            return False
    elif isinstance(default, float):
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return False
        value = float(value)
    elif isinstance(default, str):
        if not isinstance(value, str):
            return False
    _runtime_config[key] = value
    return True


def _reset_runtime_config() -> None:
    """Reset all runtime config to defaults (admin only)."""
    global _runtime_config
    _runtime_config = dict(_RUNTIME_CONFIG_DEFAULTS)


# ---------------------------------------------------------------------------
# iter #41: sweet-spot finder — automatic Pareto-frontier recommendation
# ---------------------------------------------------------------------------

SWEET_SPOT_DEFAULT_PRICES = [0.0, 0.5, 1.0, 1.5, 2.5, 4.0, 6.0, 8.0]
"""Default carbon prices (SEK/kg) to sweep when finding sweet spot.

Spread is logarithmic-ish: dense in the low-tax region (most policy-relevant),
sparser at high tax (only useful for sensitivity testing).

Max 8 prices because /api/optimize/carbon-scenarios limits carbon_prices to [1, 8].
"""


def _compute_sweet_spot_score(
    scenarios: List[Dict[str, Any]],
    weight_cost: float,
    weight_co2: float,
) -> Dict[str, Any]:
    """Compute sweet-spot across scenarios using normalized weighted sum.

    Each scenario's cost and CO2 are normalized to [0, 1] using min-max across
    scenarios (baseline = cheapest/most-CO2 if cost_weight dominates, but we
    normalize per metric so direction is consistent).

    For cost: lower is better → normalized so cheapest scenario = 0, most expensive = 1.
    For CO2:  lower is better → normalized so lowest-CO2 scenario = 0, highest = 1.

    score = weight_cost * cost_norm + weight_co2 * co2_norm
    Scenario with minimum score = sweet spot.

    Returns dict with:
        sweet_spot_index: int | None  (index into scenarios)
        sweet_spot_price: float | None
        score_per_scenario: list[float]  (for visualization)
        cost_range_sek: tuple[float, float]
        co2_range_kg: tuple[float, float]
    """
    # Filter scenarios with valid data
    valid_idx: List[int] = []
    for i, s in enumerate(scenarios):
        cost_opt = s.get("cost_optimal") or {}
        if cost_opt.get("cost_sek") is None or cost_opt.get("co2_kg") is None:
            continue
        valid_idx.append(i)

    if not valid_idx:
        return {
            "sweet_spot_index": None,
            "sweet_spot_price": None,
            "score_per_scenario": [None] * len(scenarios),
            "cost_range_sek": [None, None],
            "co2_range_kg": [None, None],
            "n_valid_scenarios": 0,
        }

    # Collect true_total_cost and co2_kg for each valid scenario
    cost_values: List[float] = []
    co2_values: List[float] = []
    for i in valid_idx:
        s = scenarios[i]
        cost_opt = s["cost_optimal"]
        # cost_sek here is pure operating cost (co2_weight=0, no tax)
        # For sweet-spot analysis we want a stable cost signal independent of tax,
        # so use the cost-opt's raw fuel cost. Tax effect is captured by CO2 reduction.
        cost_values.append(float(cost_opt["cost_sek"]))
        co2_values.append(float(cost_opt["co2_kg"]))

    cost_min, cost_max = min(cost_values), max(cost_values)
    co2_min, co2_max = min(co2_values), max(co2_values)
    cost_range = cost_max - cost_min if cost_max > cost_min else 1.0
    co2_range = co2_max - co2_min if co2_max > co2_min else 1.0

    scores_per_scenario: List[Optional[float]] = [None] * len(scenarios)
    valid_scores: List[Tuple[int, float]] = []  # (scenario_index, score)
    for j, i in enumerate(valid_idx):
        cost_norm = (cost_values[j] - cost_min) / cost_range
        co2_norm = (co2_values[j] - co2_min) / co2_range
        score = weight_cost * cost_norm + weight_co2 * co2_norm
        scores_per_scenario[i] = round(score, 6)
        valid_scores.append((i, score))

    if not valid_scores:
        return {
            "sweet_spot_index": None,
            "sweet_spot_price": None,
            "score_per_scenario": scores_per_scenario,
            "cost_range_sek": [cost_min, cost_max],
            "co2_range_kg": [co2_min, co2_max],
            "n_valid_scenarios": 0,
        }

    best_idx, best_score = min(valid_scores, key=lambda x: x[1])
    return {
        "sweet_spot_index": best_idx,
        "sweet_spot_price": scenarios[best_idx]["carbon_price_sek_per_kg"],
        "best_score": round(best_score, 6),
        "score_per_scenario": scores_per_scenario,
        "cost_range_sek": [round(cost_min, 2), round(cost_max, 2)],
        "co2_range_kg": [round(co2_min, 2), round(co2_max, 2)],
        "n_valid_scenarios": len(valid_idx),
    }


@app.get("/api/optimize/sweet-spot")
async def get_sweet_spot(
    weight_cost: float = 0.5,
    weight_co2: float = 0.5,
    time_limit_seconds: int = 2,
    use_real_roads: bool = True,
):
    """
    iter #41: Pareto frontier sweet-spot finder.

    Sweeps a fixed set of carbon prices (SWEET_SPOT_DEFAULT_PRICES) and returns:
    - sweet-spot price (the carbon tax that minimizes weighted cost+CO2)
    - all scenarios with normalized scores
    - sensitivity table for frontend visualization

    Use case: an operator wants to know "at what carbon tax rate is our
    fleet optimal overall?" without manually exploring each scenario.

    Query params:
      weight_cost (0..1): how much to weight pure operating cost vs CO2 reduction
      weight_co2  (0..1): how much to weight CO2 reduction vs cost
                          (must satisfy weight_cost + weight_co2 > 0)
      time_limit_seconds: per-scenario OR-Tools solve budget (lower = faster sweep)
      use_real_roads: use OSM distance (default true; set false for haversine fallback)

    Returns:
      {
        sweet_spot: {carbon_price_sek_per_kg, cost_sek, co2_kg, score},
        scenarios: [{carbon_price, cost_sek, co2_kg, score, ...}],
        weight_cost, weight_co2,
        cost_range_sek: [min, max],
        co2_range_kg: [min, max],
        n_scenarios,
        use_real_roads,
      }
    """
    if coordinator is None:
        raise HTTPException(status_code=503, detail="System not initialized")

    # Validate weights
    if weight_cost < 0 or weight_co2 < 0:
        raise HTTPException(status_code=400, detail="weight_cost and weight_co2 must be >= 0")
    if weight_cost + weight_co2 <= 0:
        raise HTTPException(
            status_code=400,
            detail="weight_cost + weight_co2 must be > 0",
        )
    if time_limit_seconds < 1 or time_limit_seconds > 30:
        raise HTTPException(status_code=400, detail="time_limit_seconds must be in [1, 30]")

    # Reuse carbon-scenarios core by constructing it via shared logic.
    # We do this by calling the existing endpoint internally to avoid
    # duplicating the world-building logic.
    scenarios_resp = await get_carbon_scenarios(
        carbon_prices=",".join(str(p) for p in SWEET_SPOT_DEFAULT_PRICES),
        time_limit_seconds=time_limit_seconds,
        use_real_roads=use_real_roads,
    )

    scenarios = scenarios_resp.get("scenarios", [])
    if not scenarios:
        return {
            "sweet_spot": None,
            "scenarios": [],
            "weight_cost": weight_cost,
            "weight_co2": weight_co2,
            "cost_range_sek": [None, None],
            "co2_range_kg": [None, None],
            "n_scenarios": 0,
            "n_valid_scenarios": 0,
            "use_real_roads": use_real_roads,
            "reason": scenarios_resp.get("reason", "No scenarios computed"),
        }

    sweet = _compute_sweet_spot_score(scenarios, weight_cost, weight_co2)

    # Build output scenarios (lightweight: only cost-opt data + score)
    out_scenarios = []
    for i, s in enumerate(scenarios):
        cost_opt = s.get("cost_optimal") or {}
        co2_opt = s.get("co2_optimal") or {}
        out_scenarios.append({
            "carbon_price_sek_per_kg": s["carbon_price_sek_per_kg"],
            "cost_sek": cost_opt.get("cost_sek"),
            "co2_kg": cost_opt.get("co2_kg"),
            "co2_optimal_cost_sek": co2_opt.get("cost_sek"),
            "co2_optimal_co2_kg": co2_opt.get("co2_kg"),
            "n_routes": cost_opt.get("n_routes", 0),
            "score": sweet["score_per_scenario"][i],
            "is_sweet_spot": i == sweet["sweet_spot_index"],
        })

    sweet_spot_obj = None
    if sweet["sweet_spot_index"] is not None:
        ss = out_scenarios[sweet["sweet_spot_index"]]
        sweet_spot_obj = {
            "carbon_price_sek_per_kg": ss["carbon_price_sek_per_kg"],
            "cost_sek": ss["cost_sek"],
            "co2_kg": ss["co2_kg"],
            "score": ss["score"],
        }

    return {
        "sweet_spot": sweet_spot_obj,
        "scenarios": out_scenarios,
        "weight_cost": weight_cost,
        "weight_co2": weight_co2,
        "cost_range_sek": sweet["cost_range_sek"],
        "co2_range_kg": sweet["co2_range_kg"],
        "n_scenarios": len(out_scenarios),
        "n_valid_scenarios": sweet["n_valid_scenarios"],
        "use_real_roads": use_real_roads,
    }


@app.post("/api/optimize/batch")
async def optimize_batch(request: BatchOptimizeRequest):
    """
    批量优化 endpoint (iter #13) — 一次计算多个 scenario 并返回。

    与 carbon-scenarios 不同:
    - carbon-scenarios: 4 个默认碳价 (0/1.5/3/5 SEK/kg), 不接受定制
    - batch: caller 可以提交任意配置组合 (碳价 + time_limit + n_points + region)

    用途: UI 可一次性请求 (no-carbon) + (low-carbon) + (high-carbon) + (other-region)
    并行计算。返回 scenarios 列表与请求顺序对应。

    Body: {"scenarios": [{name, n_points, time_limit_seconds, co2_price,
                          use_real_roads, region}, ...]}

    Response: {"scenarios": [{name, carbon_price_sek_per_kg, cost_optimal,
                              co2_optimal, pareto, error?}, ...]}
    """
    if coordinator is None:
        raise HTTPException(status_code=503, detail="System not initialized")

    from optimization.vrp_solver import VRPSolver, Location, Vehicle

    # 复用碳价场景的世界构建逻辑
    supply_offers = []
    for agent_id, agent in coordinator.supply_agents.items():
        supply_offers.append({
            "agent_id": agent_id,
            "available_tons": round(agent.daily_capacity * 0.8, 2),
            "material_type": agent.material_type,
            "location": agent.location,
        })
    demand_requests = []
    for dp in coordinator.market_agent.demand_points:
        demand_requests.append({
            "id": dp["id"],
            "name": dp["name"],
            "demand_tons": dp["current_demand_tons"],
            "preferred_materials": dp["preferred_materials"],
            "location": dp["location"],
            "material_type": dp.get("material_type"),
        })
    matches_result = await coordinator.market_agent.match_supply_demand(
        supply_offers=supply_offers,
        demand_requests=demand_requests,
    )
    matches = matches_result.get("matches", [])[:15]

    if not matches:
        return {
            "scenarios": [
                {"name": s.name, "error": "No matches available"}
                for s in request.scenarios
            ],
            "reason": "No matches available",
        }

    depot_loc = coordinator.logistics_agent.depot_location
    depot = Location(id="DEPOT", lat=depot_loc["lat"], lon=depot_loc["lon"], type="depot")
    supply_idx = {a.agent_id: a for a in coordinator.supply_agents.values()}
    demand_idx = {d["id"]: d for d in coordinator.market_agent.demand_points}

    pickup_locations = []
    delivery_locations = []
    for m in matches:
        sid = m.get("supply_id")
        did = m.get("demand_id")
        if sid not in supply_idx or did not in demand_idx:
            continue
        sup = supply_idx[sid]
        dem = demand_idx[did]
        pickup_locations.append({
            "id": sid, "lat": sup.location["lat"], "lon": sup.location["lon"],
            "tons": m.get("tons", 5.0),
        })
        delivery_locations.append({
            "id": did, "lat": dem["location"]["lat"], "lon": dem["location"]["lon"],
            "tons": m.get("tons", 5.0),
        })
    if not pickup_locations:
        return {
            "scenarios": [
                {"name": s.name, "error": "No usable supply/demand locations"}
                for s in request.scenarios
            ],
            "reason": "No usable locations",
        }

    vehicles_data = [
        v for v in coordinator.logistics_agent.vehicles
        if v.get("status") == "available"
    ][:len(pickup_locations)]
    if not vehicles_data:
        return {
            "scenarios": [
                {"name": s.name, "error": "No vehicles available"}
                for s in request.scenarios
            ],
            "reason": "No vehicles available",
        }

    def _build_solver(use_real_roads: bool, region: Optional[str]) -> VRPSolver:
        solver_kwargs: Dict[str, Any] = {"use_real_roads": use_real_roads}
        if region:
            solver_kwargs["region"] = region
        solver = VRPSolver(**solver_kwargs)
        solver.add_location(depot)
        for loc in pickup_locations:
            solver.add_location(Location(
                id=loc["id"], lat=loc["lat"], lon=loc["lon"],
                demand_tons=loc["tons"], type="pickup",
            ))
        for loc in delivery_locations:
            solver.add_location(Location(
                id=loc["id"], lat=loc["lat"], lon=loc["lon"],
                demand_tons=-loc["tons"], type="delivery",
            ))
        for vd in vehicles_data:
            solver.add_vehicle(Vehicle(
                id=vd["vehicle_id"],
                capacity_tons=vd.get("capacity_tons", 20.0),
                start_location=depot,
                co2_rate=vd.get("co2_emission_rate", 0.85),
                cost_per_km=2.6,
            ))
        return solver

    async def _solve_scenario(s: BatchScenarioRequest) -> Dict[str, Any]:
        try:
            solver = _build_solver(s.use_real_roads, s.region)
            pareto = await asyncio.to_thread(
                solver.solve_pareto,
                n_points=s.n_points,
                time_limit_seconds=s.time_limit_seconds,
                co2_price=s.co2_price,
            )
            cost_opt = next(
                (p for p in pareto if abs(p["cost_weight"] - 1.0) < 1e-6), None
            )
            co2_opt = next(
                (p for p in pareto if abs(p["co2_weight"] - 1.0) < 1e-6), None
            )
            return {
                "name": s.name,
                "carbon_price_sek_per_kg": s.co2_price,
                "n_points": len(pareto),
                "cost_optimal": {
                    "cost_sek": cost_opt["cost_sek"] if cost_opt else None,
                    "co2_kg": cost_opt["co2_kg"] if cost_opt else None,
                    "n_routes": len(cost_opt["routes"]) if cost_opt else 0,
                } if cost_opt else None,
                "co2_optimal": {
                    "cost_sek": co2_opt["cost_sek"] if co2_opt else None,
                    "co2_kg": co2_opt["co2_kg"] if co2_opt else None,
                    "n_routes": len(co2_opt["routes"]) if co2_opt else 0,
                } if co2_opt else None,
                "pareto": [
                    {
                        "cost_weight": p["cost_weight"],
                        "co2_weight": p["co2_weight"],
                        "cost_sek": p["cost_sek"],
                        "co2_kg": p["co2_kg"],
                        "total_objective": p["total_objective"],
                    }
                    for p in pareto
                ],
                "distance_source": solver.distance_source,
                "use_real_roads": s.use_real_roads,
            }
        except Exception as e:
            return {
                "name": s.name,
                "carbon_price_sek_per_kg": s.co2_price,
                "error": f"{type(e).__name__}: {str(e)[:200]}",
            }

    scenarios = await asyncio.gather(*[_solve_scenario(s) for s in request.scenarios])
    return {
        "n_pickups": len(pickup_locations),
        "n_deliveries": len(delivery_locations),
        "n_vehicles": len(vehicles_data),
        "scenarios": scenarios,
    }


@app.get("/api/iot-telemetry/{vehicle_id}")
async def get_iot_telemetry(vehicle_id: str, hours: int = 4):
    """获取车辆 IoT 遥测数据"""
    if data_generator is None:
        raise HTTPException(status_code=503, detail="Data generator not initialized")
    
    telemetry = data_generator.generate_iot_telemetry(
        vehicle_id=vehicle_id,
        duration_hours=hours,
        interval_minutes=5
    )
    
    return {
        "vehicle_id": vehicle_id,
        "duration_hours": hours,
        "data_points": len(telemetry),
        "telemetry": telemetry
    }


@app.get("/api/fleet-snapshot")
async def get_fleet_snapshot():
    """获取车队实时快照"""
    if data_generator is None:
        raise HTTPException(status_code=503, detail="Data generator not initialized")

    vehicle_ids = [f"VEH{i:03d}" for i in range(10)]
    snapshot = data_generator.generate_fleet_snapshot(vehicle_ids)

    # 转换为前端期望的格式
    vehicles = []
    for v in snapshot:
        vehicles.append({
            "vehicle_id": v["vehicle_id"],
            "status": v["status"],
            "latitude": v["location"]["lat"],
            "longitude": v["location"]["lon"],
            "battery_level": v["fuel_level"],
            "cargo_load": v["current_load_tons"],
            "speed": 0 if v["status"] == "available" else random.uniform(30, 70),
            "carbon_emission_rate": 0.85,
            "heading": random.uniform(0, 360),
            "last_update": v["last_update"]
        })

    return {
        "timestamp": datetime.now().isoformat(),
        "vehicles": vehicles
    }


# ============================================
# V2 新增：持久化 KPI 查询端点
# ============================================

@app.get("/api/persistence/recent-cycles")
async def get_recent_cycles(limit: int = 10):
    """获取最近 N 个优化周期的 KPI（从 SQLite 读）"""
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    return coordinator.persistence.get_recent_cycles(limit=limit)


@app.get("/api/persistence/kpi-timeseries")
async def get_kpi_timeseries(
    since_sim_day: Optional[int] = None,
    until_sim_day: Optional[int] = None,
):
    """KPI 时间序列 (iter #8 + iter #18 时间窗口) — 按 sim_day 聚合。

    Query (iter #18):
    - since_sim_day: 起始 sim_day (含)
    - until_sim_day: 结束 sim_day (含)
    """
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


@app.get("/api/persistence/fleet-timeseries")
async def get_fleet_timeseries():
    """
    Fleet 时间序列 (iter #9) — 按 sim_day 聚合 fleet 指标。

    返回: sim_day → {sim_day, n_vehicles_used, n_vehicles_available,
                     fleet_utilization_pct, total_distance_km,
                     n_matches, total_tons}

    用途: Dashboard fleet trend 图, 分析调度模式。
    """
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    return coordinator.persistence.get_fleet_timeseries()


@app.get("/api/persistence/seasonal-timeseries")
async def get_seasonal_timeseries():
    """按月份聚合的 KPI + seasonal_factor (iter #4)。

    供前端分析 "夏季 vs 冬季" 成本/CO2 差异。
    """
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    return coordinator.persistence.get_seasonal_timeseries()


@app.get("/api/persistence/seasonal-timeseries-by-material")
async def get_seasonal_timeseries_by_material():
    """
    iter #46: Seasonal time-series cross-tab (material × month).

    Returns a 2D matrix showing seasonal_multiplier + total_tons for
    each (material, month) cell. Useful for spotting which materials
    are most / least affected by seasonal swings.

    Response shape:
        n_materials, n_months, materials, month_labels, matrix: [...]
    """
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    return coordinator.persistence.get_seasonal_timeseries_by_material()


# ============================================
# iter #38: Perturbation impact analytics
# ============================================
# Surfaces how active perturbations changed cycle KPIs over time.
# Returns time-series data + aggregate summary so the frontend can
# chart base-vs-effective seasonal factors.
@app.get("/api/persistence/perturbation-impact")
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


@app.get("/api/persistence/perturbation-impact-by-material")
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


@app.get("/api/persistence/llm-cost-timeseries")
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


@app.get("/api/persistence/llm-cost-forecast")
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


@app.get("/api/persistence/forecast")
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


@app.get("/api/persistence/forecast-confidence")
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


@app.get("/api/persistence/forecast-method-prefs")
async def get_forecast_method_prefs(_: None = Depends(require_admin)):
    """
    iter #35: 查看所有 metric 的最佳 method 偏好 (admin only).

    返回 {prefs: [{metric, best_method, r_squared, history_n, n_samples, updated_at}, ...]}.
    """
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    prefs = coordinator.persistence.get_method_prefs()
    return {
        "prefs": prefs,
        "count": len(prefs),
        "metrics_covered": [p["metric"] for p in prefs],
    }


@app.delete("/api/persistence/forecast-method-prefs")
async def clear_forecast_method_prefs(
    metric: Optional[str] = None,
    _: None = Depends(require_admin),
):
    """
    iter #35: 清除 method prefs (admin only).

    Query:
    - metric (optional): 只清除指定 metric; 不传则清除全部
    """
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    if metric:
        deleted = coordinator.persistence.delete_method_pref(metric)
        return {"deleted": deleted, "scope": "metric", "metric": metric}
    else:
        n = coordinator.persistence.clear_method_prefs()
        return {"deleted": n, "scope": "all"}


@app.get("/api/persistence/forecast-calibration")
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


@app.get("/api/persistence/forecast-calibration/trend")
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


# ============================================
# iter #37: Seasonal perturbation endpoints (admin)
# ============================================
# Allow operators to model one-off shocks (holiday spike, weather event,
# plant shutdown) that overlay the static SEASONAL_FACTORS table for a
# specific sim_day window. Coordinator reads these each cycle and
# multiplies them into the supply/demand multipliers.
#
# Schema is owned by ``agents/persistence.py``; logic in
# ``data/seasonal_perturbation.py`` (multiplicative semantics + bounds).
@app.get("/api/admin/seasonal-perturbations")
async def list_seasonal_perturbations(
    active_only: bool = True,
    sim_day: Optional[int] = None,
    material_type: Optional[str] = None,
    _: None = Depends(require_admin),
):
    """
    iter #37: List seasonal perturbations.

    Query:
    - active_only (default true): filter to active=1
    - sim_day: when provided, returns only perturbations whose window
              covers this day (and material filter, if given)
    - material_type: when provided, filters by material (wildcard '*'
                     matches any)

    Response:
    {
        "perturbations": [...],
        "count": int,
        "active_count": int
    }
    """
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")

    if sim_day is not None:
        rows = coordinator.persistence.get_active_perturbations(
            int(sim_day), material_type
        )
        all_count = len(rows)
    else:
        rows = coordinator.persistence.list_seasonal_perturbations(active_only=active_only)
        all_count = len(rows)

    return {
        "perturbations": rows,
        "count": all_count,
        "active_count": len([r for r in rows if r.get("active", 1)]),
    }


@app.post("/api/admin/seasonal-perturbations")
async def add_seasonal_perturbation(
    label: str,
    start_sim_day: int,
    end_sim_day: int,
    material_type: str = "*",
    multiplier: float = 1.0,
    _: None = Depends(require_admin),
):
    """
    iter #37: Insert a new perturbation rule.

    Body (query params):
    - label: human-readable description (e.g. "Christmas paper surge")
    - start_sim_day / end_sim_day: inclusive window (0-indexed)
    - material_type: specific material name, or '*' for all materials
    - multiplier: applied to base seasonal factor (e.g. 0.7 = -30%)

    Validation enforced in persistence layer; bad input -> HTTP 400.
    """
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    try:
        row = coordinator.persistence.add_seasonal_perturbation(
            label=label,
            start_sim_day=start_sim_day,
            end_sim_day=end_sim_day,
            material_type=material_type,
            multiplier=multiplier,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    logger.info(
        f"[perturb] added id={row['id']} label='{label}' "
        f"days=[{start_sim_day},{end_sim_day}] mat={material_type} x{multiplier}"
    )
    return {"created": row, "count": 1}


@app.delete("/api/admin/seasonal-perturbations/{perturbation_id}")
async def delete_seasonal_perturbation(
    perturbation_id: int,
    _: None = Depends(require_admin),
):
    """
    iter #37: Hard-delete a single perturbation.

    Returns 404 if not found, 200 with {"deleted": true|false} otherwise.
    """
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    deleted = coordinator.persistence.delete_seasonal_perturbation(perturbation_id)
    if not deleted:
        raise HTTPException(
            status_code=404, detail=f"perturbation id={perturbation_id} not found"
        )
    return {"deleted": True, "id": perturbation_id}


@app.post("/api/admin/seasonal-perturbations/{perturbation_id}/deactivate")
async def deactivate_seasonal_perturbation(
    perturbation_id: int,
    _: None = Depends(require_admin),
):
    """
    iter #37: Soft-delete (set active=0). Audit-friendly: row stays in DB
    so historical analysis can still see "perturbation X was active on day Y".
    """
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    updated = coordinator.persistence.deactivate_seasonal_perturbation(perturbation_id)
    if not updated:
        raise HTTPException(
            status_code=404, detail=f"perturbation id={perturbation_id} not found"
        )
    return {"deactivated": True, "id": perturbation_id}


@app.get("/api/persistence/forecast/multi")
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


@app.get("/api/persistence/summary")
async def get_persistence_summary():
    """全局统计汇总"""
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    return coordinator.persistence.get_summary()


@app.get("/api/persistence/efficiency-metrics")
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
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    return coordinator.persistence.get_efficiency_metrics()


@app.get("/api/persistence/monthly-efficiency-trend")
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
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    return coordinator.persistence.get_monthly_efficiency_trend()


@app.get("/api/persistence/cycle-history")
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
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    limit = max(1, min(500, limit))
    return coordinator.persistence.get_cycle_history(
        limit=limit,
        sim_day_min=sim_day_min,
        sim_day_max=sim_day_max,
        has_matches_only=has_matches_only,
    )


@app.get("/api/persistence/cycle-detail/{cycle_id}")
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


@app.get("/api/persistence/export/cycles.csv")
async def export_cycles_csv(limit: int = 1000, include_metadata: bool = True):
    """
    Export cycle history as CSV (iter #11 + iter #19 metadata)。

    Query:
    - limit: 最多多少行 (default 1000, max 10000)
    - include_metadata: iter #19, 是否在 CSV 顶部加 metadata (生成时间 + db 路径 + size + row count)

    Returns: text/csv 响应 + Content-Disposition: attachment。
    包含 19 列 KPI (见 Persistence.export_cycles_csv 注释)。
    """
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


@app.get("/api/persistence/export/supplies.csv")
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


@app.get("/api/persistence/export/matches.csv")
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


@app.get("/api/persistence/export/perturbed-supplies.csv")
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


@app.get("/api/persistence/export/routes.csv")
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
@app.get("/api/persistence/export/cycles.parquet")
async def export_cycles_parquet(limit: int = 1000):
    """Export cycles as Apache Parquet (iter #27)。"""
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    limit = max(1, min(10000, limit))
    rows = coordinator.persistence.export_cycles_rows(limit=limit)
    return _build_parquet_response(rows, "cycles", limit)


@app.get("/api/persistence/export/supplies.parquet")
async def export_supplies_parquet(limit: int = 10000):
    """Export supply_offers as Apache Parquet (iter #27)。"""
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    limit = max(1, min(50000, limit))
    rows = coordinator.persistence.export_supplies_rows(limit=limit)
    return _build_parquet_response(rows, "supplies", limit)


@app.get("/api/persistence/export/matches.parquet")
async def export_matches_parquet(limit: int = 10000):
    """Export matches as Apache Parquet (iter #27)。"""
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    limit = max(1, min(50000, limit))
    rows = coordinator.persistence.export_matches_rows(limit=limit)
    return _build_parquet_response(rows, "matches", limit)


@app.get("/api/persistence/export/routes.parquet")
async def export_routes_parquet(limit: int = 10000):
    """Export routes as Apache Parquet (iter #27)。"""
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    limit = max(1, min(50000, limit))
    rows = coordinator.persistence.export_routes_rows(limit=limit)
    return _build_parquet_response(rows, "routes", limit)


# ============================================
# iter #27: JSON + NDJSON endpoints (4 + 4 = 8)
# ============================================
@app.get("/api/persistence/export/cycles.json")
async def export_cycles_json(limit: int = 1000):
    """Export cycles as pretty JSON array (iter #27)."""
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    limit = max(1, min(10000, limit))
    rows = coordinator.persistence.export_cycles_rows(limit=limit)
    return _build_json_response(rows, "cycles", limit)


@app.get("/api/persistence/export/supplies.json")
async def export_supplies_json(limit: int = 10000):
    """Export supply_offers as pretty JSON array (iter #27)."""
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    limit = max(1, min(50000, limit))
    rows = coordinator.persistence.export_supplies_rows(limit=limit)
    return _build_json_response(rows, "supplies", limit)


@app.get("/api/persistence/export/matches.json")
async def export_matches_json(limit: int = 10000):
    """Export matches as pretty JSON array (iter #27)."""
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    limit = max(1, min(50000, limit))
    rows = coordinator.persistence.export_matches_rows(limit=limit)
    return _build_json_response(rows, "matches", limit)


@app.get("/api/persistence/export/routes.json")
async def export_routes_json(limit: int = 10000):
    """Export routes as pretty JSON array (iter #27)."""
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    limit = max(1, min(50000, limit))
    rows = coordinator.persistence.export_routes_rows(limit=limit)
    return _build_json_response(rows, "routes", limit)


@app.get("/api/persistence/export/cycles.ndjson")
async def export_cycles_ndjson(limit: int = 1000):
    """Export cycles as NDJSON (iter #27)."""
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    limit = max(1, min(10000, limit))
    rows = coordinator.persistence.export_cycles_rows(limit=limit)
    return _build_ndjson_response(rows, "cycles", limit)


@app.get("/api/persistence/export/supplies.ndjson")
async def export_supplies_ndjson(limit: int = 10000):
    """Export supply_offers as NDJSON (iter #27)."""
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    limit = max(1, min(50000, limit))
    rows = coordinator.persistence.export_supplies_rows(limit=limit)
    return _build_ndjson_response(rows, "supplies", limit)


@app.get("/api/persistence/export/matches.ndjson")
async def export_matches_ndjson(limit: int = 10000):
    """Export matches as NDJSON (iter #27)."""
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    limit = max(1, min(50000, limit))
    rows = coordinator.persistence.export_matches_rows(limit=limit)
    return _build_ndjson_response(rows, "matches", limit)


@app.get("/api/persistence/export/routes.ndjson")
async def export_routes_ndjson(limit: int = 10000):
    """Export routes as NDJSON (iter #27)."""
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    limit = max(1, min(50000, limit))
    rows = coordinator.persistence.export_routes_rows(limit=limit)
    return _build_ndjson_response(rows, "routes", limit)


@app.get("/api/persistence/match-distance-stats")
async def get_match_distance_stats():
    """
    Match 距离统计 (iter #15) — 从 matches 表聚合 distance_km 指标。

    返回:
    - total_matches, n_cycles_with_matches
    - avg / min / max / median distance_km
    - distance_distribution: 4 桶 (<10, 10-50, 50-100, >=100 km)

    用途: 验证 OSM 距离是否合理, 监控 match quality。
    """
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    return coordinator.persistence.get_match_distance_stats()


@app.get("/api/persistence/vehicle-stats")
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


@app.get("/api/persistence/supply-aggregates")
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
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    limit = max(1, min(500, limit))
    return coordinator.persistence.get_supply_aggregates(
        supply_id=supply_id,
        material_type=material_type,
        limit_supplies=limit,
    )


@app.get("/api/persistence/material-aggregates")
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
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    limit = max(1, min(200, limit))
    return coordinator.persistence.get_material_aggregates(
        material_type=material_type,
        limit=limit,
    )


@app.get("/api/persistence/supply-cohort-retention")
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
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    return coordinator.persistence.get_supply_cohort_retention(
        material_type=material_type,
    )


@app.get("/api/persistence/cohort-retention-by-material")
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
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    by_material = coordinator.persistence.get_cohort_retention_by_material()
    return {
        "n_materials": len(by_material),
        "by_material": by_material,
    }


@app.get("/api/persistence/cohort-retention-by-period")
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


@app.get("/api/persistence/cohort-retention-crosstab")
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


@app.get("/api/persistence/cycle-kpi-summary")
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


@app.get("/api/persistence/anomalous-cycles")
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


@app.post("/api/admin/db-maintenance")
async def post_db_maintenance(_: None = Depends(require_admin)):
    """
    DB 维护 (iter #16 + iter #42 audit log) — VACUUM + ANALYZE。

    VACUUM: rebuild DB file, 释放碎片空间, 减小文件体积
    ANALYZE: 收集统计信息, 帮助 query planner 选最优 index

    Returns:
        {action, size_before_bytes, size_after_bytes,
         reclaimed_bytes, reclaimed_pct, success, triggered_by, ran_at}
    """
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    return coordinator.persistence.vacuum(triggered_by="manual")


@app.get("/api/admin/db-maintenance/recommendation")
async def get_db_maintenance_recommendation(_: None = Depends(require_admin)):
    """
    iter #42: Auto-vacuum recommendation (admin).

    Returns whether VACUUM/ANALYZE is recommended based on:
    - DB size growth since last vacuum (> 30%)
    - Total cycle count (> 1000 with no vacuum)
    - Days since last vacuum (> 7)
    - First vacuum ever

    Returns:
        {
          should_vacuum: bool,
          reasons: [str, ...],
          stats: {
            db_size_bytes, db_size_mb, total_cycles,
            last_vacuum_at, days_since_last_vacuum,
            size_growth_pct_since_last_vacuum,
            size_after_last_vacuum_bytes,
            total_maintenance_runs,
          }
        }
    """
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    return coordinator.persistence.should_auto_vacuum()


@app.get("/api/admin/db-maintenance/log")
async def get_db_maintenance_log(
    limit: int = 20,
    _: None = Depends(require_admin),
):
    """
    iter #42: Recent DB maintenance audit log (admin).

    Returns last N entries from db_maintenance_log table
    (VACUUM/ANALYZE runs with size + timestamp).
    """
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=400, detail="limit must be in [1, 200]")
    return {
        "n_entries": 0,
        "entries": coordinator.persistence.get_maintenance_log(limit=limit),
    }


@app.get("/api/admin/runtime-config")
async def get_runtime_config(_: None = Depends(require_admin)):
    """
    iter #43: Read runtime configuration (admin).

    Returns current values for all hot-tunable params, with their
    defaults and a flag indicating which are non-default (modified).
    """
    items = []
    for key, default in _RUNTIME_CONFIG_DEFAULTS.items():
        current = _runtime_config.get(key, default)
        items.append({
            "key": key,
            "value": current,
            "default": default,
            "modified": current != default,
            "type": _type_name(default),
        })
    return {
        "n_keys": len(items),
        "items": items,
    }


@app.post("/api/admin/runtime-config")
async def update_runtime_config(
    key: str,
    value: Any,
    persist: bool = False,
    _: None = Depends(require_admin),
):
    """
    iter #43 + iter #44: Update a runtime config value (admin).

    Body params (form or query):
      key: config key (e.g. "default_carbon_price_sek_per_kg")
      value: new value (string-typed; parsed to int/float/bool/str)
      persist: if true, save to SQLite so the change survives restart (iter #44)

    Returns: {key, value, applied, persisted: bool, updated_at?: str}
    """
    if key not in _RUNTIME_CONFIG_DEFAULTS:
        raise HTTPException(
            status_code=400,
            detail=f"unknown key: {key}, valid: {sorted(_RUNTIME_CONFIG_DEFAULTS.keys())}",
        )
    default = _RUNTIME_CONFIG_DEFAULTS[key]
    # Parse value string to typed value
    try:
        parsed = _parse_config_value(value, default)
    except (ValueError, TypeError) as e:
        raise HTTPException(
            status_code=400,
            detail=f"value parse error: {e}",
        )
    if not _set_runtime_config(key, parsed):
        raise HTTPException(
            status_code=400,
            detail=f"invalid value type for {key}: expected {_type_name(default)}",
        )
    logger.info(f"runtime_config: {key} = {parsed} (was {_runtime_config.get(key)})")
    persisted = False
    updated_at = None
    if persist:
        if coordinator is None or coordinator.persistence is None:
            raise HTTPException(status_code=503, detail="Persistence not initialized")
        result = coordinator.persistence.save_runtime_config(key, parsed)
        persisted = True
        updated_at = result["updated_at"]
    return {
        "key": key,
        "value": _runtime_config[key],
        "applied": True,
        "persisted": persisted,
        "updated_at": updated_at,
    }


@app.post("/api/admin/runtime-config/apply")
async def apply_runtime_config_batch(
    updates: str,  # JSON-encoded [{key, value}, ...]
    persist: bool = False,
    _: None = Depends(require_admin),
):
    """
    iter #44: Apply a batch of runtime config updates atomically.

    Body params (form or query):
      updates: JSON string [{key, value}, ...] (each value is a string
               that will be parsed to the key's default type)
      persist: if true, also save all to SQLite

    On any validation failure, NO updates are applied (atomic transaction).
    Returns: {applied: [{key, value}], n_applied, persisted, errors: []}
    """
    import json as _json
    try:
        parsed_updates = _json.loads(updates)
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail=f"invalid JSON: {e}")
    if not isinstance(parsed_updates, list):
        raise HTTPException(status_code=400, detail="updates must be a JSON array")
    if not (1 <= len(parsed_updates) <= 50):
        raise HTTPException(status_code=400, detail="updates count must be in [1, 50]")

    # Validate all first (atomic)
    validated: List[Tuple[str, Any]] = []
    errors: List[Dict[str, str]] = []
    for i, item in enumerate(parsed_updates):
        if not isinstance(item, dict) or "key" not in item or "value" not in item:
            errors.append({"index": str(i), "reason": "item must have key+value"})
            continue
        k = item["key"]
        v = item["value"]
        if k not in _RUNTIME_CONFIG_DEFAULTS:
            errors.append({"index": str(i), "key": k, "reason": f"unknown key: {k}"})
            continue
        default = _RUNTIME_CONFIG_DEFAULTS[k]
        try:
            typed_v = _parse_config_value(v, default)
        except (ValueError, TypeError) as e:
            errors.append({"index": str(i), "key": k, "reason": f"parse error: {e}"})
            continue
        validated.append((k, typed_v))

    if errors:
        raise HTTPException(
            status_code=400,
            detail={"reason": "validation errors (no updates applied)", "errors": errors},
        )

    # Apply all
    applied_results = []
    for k, v in validated:
        if _set_runtime_config(k, v):
            applied_results.append({"key": k, "value": v})
            logger.info(f"runtime_config (batch): {k} = {v}")

    # Persist if requested
    if persist:
        if coordinator is None or coordinator.persistence is None:
            raise HTTPException(status_code=503, detail="Persistence not initialized")
        for k, v in applied_results:
            coordinator.persistence.save_runtime_config(k, v)

    return {
        "n_applied": len(applied_results),
        "applied": applied_results,
        "persisted": persist,
    }


@app.post("/api/admin/runtime-config/reset")
async def reset_runtime_config(
    clear_persisted: bool = False,
    _: None = Depends(require_admin),
):
    """
    iter #43 + iter #44: Reset all runtime config to defaults (admin).

    Query:
    - clear_persisted: also delete all rows in runtime_config table
    """
    _reset_runtime_config()
    deleted = 0
    if clear_persisted:
        if coordinator is None or coordinator.persistence is None:
            raise HTTPException(status_code=503, detail="Persistence not initialized")
        with coordinator.persistence._conn() as conn:
            cur = conn.execute("DELETE FROM runtime_config")
        deleted = cur.rowcount
    return {"reset": True, "n_keys": len(_RUNTIME_CONFIG_DEFAULTS), "deleted_persisted": deleted}


@app.get("/api/admin/runtime-config/overrides")
async def get_runtime_config_overrides(_: None = Depends(require_admin)):
    """
    iter #44: List all persisted runtime config overrides (admin).

    Returns rows from runtime_config SQLite table (not in-memory state).
    Useful for ops to see "what overrides are saved vs defaults".
    """
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    overrides = coordinator.persistence.list_runtime_config_overrides()
    return {
        "n_overrides": len(overrides),
        "overrides": overrides,
    }


def _type_name(value: Any) -> str:
    """Return JSON-friendly type name."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return type(value).__name__


def _parse_config_value(raw: Any, default: Any) -> Any:
    """Parse raw input (likely str from HTTP) into the default's type."""
    if default is None:
        return raw
    if isinstance(default, bool):
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, str):
            low = raw.strip().lower()
            if low in ("true", "1", "yes", "on"):
                return True
            if low in ("false", "0", "no", "off"):
                return False
        if isinstance(raw, int):
            return bool(raw)
        raise ValueError(f"cannot parse bool from {raw!r}")
    if isinstance(default, int):
        if isinstance(raw, bool):
            raise ValueError("expected int, got bool")
        return int(raw)
    if isinstance(default, float):
        if isinstance(raw, bool):
            raise ValueError("expected float, got bool")
        return float(raw)
    if isinstance(default, str):
        return str(raw)
    return raw


@app.get("/api/admin/db-export")
async def export_db_data(
    table: str,
    fmt: str = "json",
    limit: int = 10000,
    since_sim_day: Optional[int] = None,
    gzip: bool = False,
    _: None = Depends(require_admin),
):
    """
    Unified DB export endpoint (iter #18 + iter #19 gzip + iter #23 parquet) — table + format export。

    Query:
    - table: cycles / supplies / matches / routes / llm_decisions
    - fmt: csv / json / ndjson / parquet (注意: 用 fmt 不是 format, 避免与 builtin 冲突)
            - parquet (iter #23): Apache Parquet columnar binary, snappy compressed.
              适合 pandas / polars / duckdb / spark analytics. 比 CSV gzip 还小 3-5x.
    - limit: 最多多少行 (default 10000, max 50000)
    - since_sim_day: 只返 >= 该 sim_day 的行
    - gzip: bool = False — 是否 gzip 压缩 (iter #19, 大 payload 省带宽)

    Returns:
        binary file with Content-Disposition: attachment; filename=...
    """
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")

    table = table.strip().lower()
    if table not in ("cycles", "supplies", "matches", "routes", "llm_decisions"):
        raise HTTPException(
            status_code=400,
            detail=f"Unknown table '{table}'. Valid: cycles / supplies / matches / routes / llm_decisions",
        )
    fmt = fmt.strip().lower()
    if fmt not in ("csv", "json", "ndjson", "parquet"):
        raise HTTPException(
            status_code=400,
            detail=f"Unknown format '{fmt}'. Valid: csv / json / ndjson / parquet",
        )
    limit = max(1, min(50000, limit))

    # 拿 rows
    rows: List[Dict[str, Any]]
    if table == "cycles":
        rows = coordinator.persistence.get_cycle_history(limit=limit)
    elif table == "supplies":
        # 用 export_supplies_csv 然后 parse (最简单)
        csv_str = coordinator.persistence.export_supplies_csv(limit=limit)
        rows = _csv_to_rows(csv_str)
    elif table == "matches":
        csv_str = coordinator.persistence.export_matches_csv(limit=limit)
        rows = _csv_to_rows(csv_str)
    elif table == "routes":
        csv_str = coordinator.persistence.export_routes_csv(limit=limit)
        rows = _csv_to_rows(csv_str)
    else:  # llm_decisions
        rows = coordinator.persistence.get_llm_decisions(
            limit=limit,
            sim_day_min=since_sim_day,
        )

    # since_sim_day filter (CSV 解析后 sim_day 是字符串)
    if since_sim_day is not None and table != "llm_decisions":
        filtered = []
        for r in rows:
            sd = r.get("sim_day")
            if sd is None or sd == "":
                filtered.append(r)
                continue
            try:
                if int(sd) >= since_sim_day:
                    filtered.append(r)
            except (ValueError, TypeError):
                filtered.append(r)
        rows = filtered

    # format dispatch (iter #19 加 gzip 选项)
    if fmt == "csv":
        if table == "llm_decisions":
            # llm_decisions 没 CSV export, 返回 placeholder
            content = "id,note\nllm_decisions,format=csv+not implemented yet\n"
            if gzip:
                return _maybe_gzip(content.encode("utf-8"), True, "llm_decisions.csv")
            return FastAPIResponse(
                content=content,
                media_type="text/csv; charset=utf-8",
                headers={"Content-Disposition": "attachment; filename=\"llm_decisions.csv\""},
            )
        csv_out = _rows_to_csv(rows)
        filename = f"green_logistics_{table}_{limit}.csv"
        if gzip:
            return _maybe_gzip(csv_out.encode("utf-8"), True, filename)
        return FastAPIResponse(
            content=csv_out,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f"attachment; filename=\"{filename}\""},
        )
    elif fmt == "json":
        filename = f"green_logistics_{table}_{limit}.json"
        if gzip:
            json_str = json.dumps(rows)
            return _maybe_gzip(json_str.encode("utf-8"), True, filename)
        return JSONResponse(
            content=rows,
            headers={"Content-Disposition": f"attachment; filename=\"{filename}\""},
        )
    elif fmt == "ndjson":
        ndjson_str = "\n".join(json.dumps(r) for r in rows)
        filename = f"green_logistics_{table}_{limit}.ndjson"
        if gzip:
            return _maybe_gzip(ndjson_str.encode("utf-8"), True, filename)
        return FastAPIResponse(
            content=ndjson_str,
            media_type="application/x-ndjson",
            headers={"Content-Disposition": f"attachment; filename=\"{filename}\""},
        )
    else:  # parquet (iter #23, columnar analytics-friendly)
        try:
            parquet_bytes = _rows_to_parquet_bytes(rows)
        except ImportError as e:
            # pyarrow not installed (HF Space 镜像未安装)
            raise HTTPException(
                status_code=501,
                detail=f"Parquet export requires pyarrow. Install with: pip install pyarrow>=15.0.0 ({e})",
            )
        except Exception as e:
            logger.error(f"Parquet export failed for table={table} limit={limit}: {e}")
            raise HTTPException(
                status_code=500,
                detail=f"Parquet serialization failed: {type(e).__name__}: {str(e)[:200]}",
            )
        filename = f"green_logistics_{table}_{limit}.parquet"
        if gzip:
            return _maybe_gzip(parquet_bytes, True, filename)
        return FastAPIResponse(
            content=parquet_bytes,
            media_type=PARQUET_MIMETYPE,
            headers={"Content-Disposition": f"attachment; filename=\"{filename}\""},
        )


@app.get("/api/admin/db-stats")
async def get_db_stats(_: None = Depends(require_admin)):
    """
    DB 统计 endpoint (iter #15) — SQLite DB 大小 + 表行数 + 索引 + 时间范围。

    用途: 监控 DB 健康 (磁盘占用, 表增长), 诊断性能问题, CI 验证。

    Returns:
        db_path, db_size_bytes, db_size_mb,
        table_counts: {optimization_cycles, supply_offers, ...},
        total_rows, indexes, time_range
    """
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    return coordinator.persistence.get_db_stats()


@app.get("/api/admin/db-info")
async def get_db_info(_: None = Depends(require_admin)):
    """
    DB 完整 info (iter #20) — audit / debugging / ops 详细信息。

    包括:
    - db_path / db_size_bytes / db_size_mb / db_modified_at
    - md5_checksum (前 100KB, 用于 detect DB 变化)
    - sqlite_version / schema_version / auto_vacuum_mode
    - table_counts / total_rows / index_count
    - time_range

    用途:
    - 调试: 'DB 是不是同一个?' '什么时候被改过?'
    - audit: 每次重要操作 记录 db checksum
    - ops: schema 升级、 migration 验证
    """
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    return coordinator.persistence.get_db_info()


@app.get("/api/admin/perf-stats")
async def get_perf_stats(top: int = 10, _: None = Depends(require_admin)):
    """
    Performance stats (iter #21) — endpoint 响应时间聚合。

    返回:
    - total_requests: 总请求数 (包括 4xx/5xx)
    - total_errors: 5xx 错误数
    - error_rate_pct: error_rate
    - endpoints: [{endpoint, n_calls, n_errors, error_rate_pct, avg_ms, min_ms, max_ms,
                   p50_ms, p95_ms, p99_ms, last_ms}, ...]
      按 avg_ms DESC 排序 (top N 最慢)
    iter #27: 每个 endpoint 加 n_errors + error_rate_pct 字段
    """
    with _PERF_LOCK:
        total = _PERF_TOTAL
        errors = _PERF_ERRORS
        errors_by_key = dict(_PERF_ERRORS_BY_KEY)
        endpoints = []
        for key, buf in _PERF_BUFFER.items():
            if not buf:
                continue
            sorted_buf = sorted(buf)
            n = len(sorted_buf)
            avg = sum(sorted_buf) / n
            p50 = sorted_buf[n // 2]
            p95_idx = min(n - 1, int(n * 0.95))
            p99_idx = min(n - 1, int(n * 0.99))
            ep_errors = errors_by_key.get(key, 0)
            endpoints.append({
                "endpoint": key,
                "n_calls": n,
                "n_errors": ep_errors,
                "error_rate_pct": round(ep_errors / n * 100, 2) if n > 0 else 0.0,
                "avg_ms": round(avg, 2),
                "min_ms": round(sorted_buf[0], 2),
                "max_ms": round(sorted_buf[-1], 2),
                "p50_ms": round(p50, 2),
                "p95_ms": round(sorted_buf[p95_idx], 2),
                "p99_ms": round(sorted_buf[p99_idx], 2),
                "last_ms": round(sorted_buf[-1], 2),
            })
        endpoints.sort(key=lambda x: x["avg_ms"], reverse=True)
    return {
        "total_requests": total,
        "total_errors": errors,
        "error_rate_pct": round(errors / total * 100, 2) if total > 0 else 0.0,
        "buffer_size_per_endpoint": 100,
        "endpoints": endpoints[:max(1, min(100, top))],
    }


@app.post("/api/admin/perf-stats/reset")
async def reset_perf_stats(_: None = Depends(require_admin)):
    """Reset perf stats buffer (iter #21)。仅在测试用。"""
    global _PERF_TOTAL, _PERF_ERRORS
    with _PERF_LOCK:
        _PERF_BUFFER.clear()
        _PERF_TOTAL = 0
        _PERF_ERRORS = 0
        _PERF_ERRORS_BY_KEY.clear()
    return {"reset": True}


# ============================================================
# LLM cost tracking (iter #22)
# ============================================================

@app.get("/api/admin/llm-stats")
async def get_llm_stats(recent: int = 50, _: None = Depends(require_admin)):
    """
    LLM call 聚合统计 (iter #22) — token usage + 估算 cost。

    Query:
    - recent: int = 50 — 包含的最近 N 条 record (最多 500)

    Returns:
        {
            total_calls, total_errors, error_rate_pct,
            total_prompt_tokens, total_candidate_tokens, total_tokens,
            total_cost_usd, avg_tokens_per_call,
            by_caller: {caller: {calls, prompt_tokens, ..., errors}},
            by_model:  {model:  {...}},
            buffer_size, buffer_max,
            recent: [{timestamp, caller, model, tokens, cost, ...}, ...]
        }
    """
    from agents.llm_tracker import get_llm_tracker
    tracker = get_llm_tracker()
    stats = tracker.get_stats()
    n = max(0, min(500, recent))
    if n > 0:
        stats["recent"] = tracker.get_recent(n)
    return stats


@app.post("/api/admin/llm-stats/reset")
async def reset_llm_stats(_: None = Depends(require_admin)):
    """Reset LLM tracker (iter #22)。仅测试用。"""
    from agents.llm_tracker import get_llm_tracker
    get_llm_tracker().reset()
    return {"reset": True}


@app.get("/api/facilities/distance-matrix")
async def get_facility_distance_matrix(
    city: Optional[str] = None,
    facility_type: Optional[str] = None,
    use_real_roads: bool = False,
):
    """
    设施间距离矩阵 (iter #15) — N×N pairwise distance。

    Query:
    - city: 过滤 (Borås / Göteborg / Stockholm)
    - facility_type: 过滤 (recycling_center / metal_recovery / ...)
    - use_real_roads: bool = False (用 haversine, 快, 够用)
      True 尝试 OSM (慢, 可能超时)

    Returns:
        {
            n_facilities, facility_ids, matrix_km (N×N),
            method: "haversine" | "osrm" | "haversine_fallback",
            pair_count: N*(N-1)/2
        }
    """
    from data.real_sweden_facilities import (
        ALL_FACILITIES,
        get_facilities_by_city,
        get_facilities_by_type,
        get_distance_matrix,
    )

    # 过滤 (复制避免 mutate module-level)
    if city:
        facilities = [dict(f) for f in get_facilities_by_city(city)]
    elif facility_type:
        facilities = [dict(f) for f in get_facilities_by_type(facility_type)]
    else:
        facilities = [dict(f) for f in ALL_FACILITIES]

    if not facilities:
        return {
            "n_facilities": 0,
            "facility_ids": [],
            "matrix_km": [],
            "method": "haversine",
            "pair_count": 0,
        }

    return get_distance_matrix(facilities, use_haversine=not use_real_roads)


@app.get("/api/materials")
async def get_materials():
    """
    Materials 元数据 (iter #9) — 返回 system 支持的所有废料 material 类型。

    包含:
    - material name (concrete, metal_scrap, wood_waste, mixed_waste, plastic, paper_cardboard)
    - total_kt_per_year (Sweden 全国年废料产量)
    - per_capita_kg (人均年废料)
    - source (数据来源: SCB / Avfall Sverige / Eurostat)
    - seasonal_factor_min, max, peak_month (季节因子范围)
    - seasonal_pattern: 'summer_peak' / 'stable' / 'winter_peak' (自动分类)

    用途: Dashboard 显示 system 支持哪些 material + 数据来源.
    """
    from data.swedish_waste_stats import SWEDEN_WASTE_BASELINES, SEASONAL_FACTORS

    result = []
    for material, baseline in SWEDEN_WASTE_BASELINES.items():
        factors = SEASONAL_FACTORS.get(material, {})
        if factors:
            fmin = min(factors.values())
            fmax = max(factors.values())
            peak_month = max(factors, key=factors.get)
            # 自动分类 pattern
            if fmax > 1.2 and peak_month in (5, 6, 7, 8, 9):
                pattern = "summer_peak"  # 建筑废料型
            elif fmax > 1.05 and peak_month in (11, 12, 1, 2):
                pattern = "winter_peak"
            else:
                pattern = "stable"
        else:
            fmin = fmax = None
            peak_month = None
            pattern = "unknown"

        result.append({
            "material": material,
            "total_kt_per_year": baseline.get("total_kt_per_year"),
            "per_capita_kg": baseline.get("per_capita_kg"),
            "source": baseline.get("source"),
            "seasonal_factor_min": fmin,
            "seasonal_factor_max": fmax,
            "seasonal_peak_month": peak_month,
            "seasonal_pattern": pattern,
        })

    return {
        "n_materials": len(result),
        "materials": result,
        "data_source": "data/swedish_waste_stats.py (SCB + Avfall Sverige + Eurostat)",
    }


# ============================================
# V2 新增：调度器状态端点 (Task A)
# ============================================
@app.get("/api/scheduler/status")
async def get_scheduler_status():
    """
    返回后台 scheduler 状态:
    - enabled: GL_SCHEDULER_ENABLED 是否为 true
    - active: scheduler 循环是否在跑
    - running_now: 当前是否有 cycle 在执行
    - is_idle: 是否在闲置模式 (smart_idle 开启后)
    - idle_for_seconds: 距上次前端活动多少秒
    - cycle_count: scheduler 跑的 cycle 数 (不含 warmup)
    - error_count: 累计失败次数
    - last_cycle_at: 上次 cycle 完成时间 (UTC ISO8601)
    - last_cycle_id: 上次 cycle 的 ID
    - next_cycle_in_seconds: 距下次 cycle 的倒计时 (idle 时为 null)
    """
    if scheduler is None:
        return {
            "enabled": False,
            "active": False,
            "running_now": False,
            "is_idle": False,
            "reason": "GL_SCHEDULER_ENABLED is not set to true",
        }
    return scheduler.status()


@app.post("/api/scheduler/control")
async def scheduler_control(action: str = "status"):
    """
    Scheduler 控制 endpoint (iter #10) — 让用户手动 start/stop/restart scheduler。

    - action="status": 返回当前状态 (等价于 GET /api/scheduler/status)
    - action="start": 启动 scheduler (if not already running)
    - action="stop": 停止 scheduler (if running)
    - action="restart": 先 stop 再 start

    限制:
    - 仅在 GL_SCHEDULER_ENABLED=true 启动的 scheduler 可被操作
    - 需要 scheduler 存在 (否则 503)

    返回: {action, success, status (scheduler 当前状态)}
    """
    global scheduler
    action = action.lower().strip()

    if scheduler is None:
        raise HTTPException(
            status_code=503,
            detail="Scheduler not initialized. Set GL_SCHEDULER_ENABLED=true on startup.",
        )

    if action == "status":
        success = True
    elif action == "start":
        if scheduler.scheduler_active:
            logger.info("Scheduler control: start 已在运行, 跳过")
        else:
            scheduler.start()
            logger.info("Scheduler control: start triggered")
        success = True
    elif action == "stop":
        if not scheduler.scheduler_active:
            logger.info("Scheduler control: stop 未在运行, 跳过")
        else:
            await scheduler.stop()
            logger.info("Scheduler control: stop triggered")
        success = True
    elif action == "restart":
        if scheduler.scheduler_active:
            await scheduler.stop()
        scheduler.start()
        logger.info("Scheduler control: restart triggered")
        success = True
    elif action == "dry_run_on":
        result = scheduler.set_dry_run(True)
        logger.info("Scheduler control: dry_run enabled")
        return {
            "action": action,
            "success": True,
            "previous_dry_run": result["previous_dry_run"],
            "current_dry_run": result["current_dry_run"],
            "status": scheduler.status(),
        }
    elif action == "dry_run_off":
        result = scheduler.set_dry_run(False)
        logger.info("Scheduler control: dry_run disabled")
        return {
            "action": action,
            "success": True,
            "previous_dry_run": result["previous_dry_run"],
            "current_dry_run": result["current_dry_run"],
            "status": scheduler.status(),
        }
    else:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown action: {action}. Allowed: status, start, stop, restart",
        )

    return {
        "action": action,
        "success": success,
        "status": scheduler.status(),
    }


# ============================================
# 主程序
# ============================================
if __name__ == "__main__":
    import uvicorn
    
    logger.info("启动 Green Logistics AI 后端服务器...")
    uvicorn.run(app, host="0.0.0.0", port=8000)
