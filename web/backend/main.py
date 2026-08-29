"""
Green Logistics AI - Web Backend

FastAPI 应用提供 REST API
"""

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response as FastAPIResponse, JSONResponse
from pydantic import BaseModel, field_validator
from typing import List, Dict, Any, Optional, Set
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
    """Convert CSV string → list of dicts."""
    reader = csv.DictReader(io.StringIO(csv_str))
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
    """

    def __init__(self) -> None:
        self._clients: Set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._clients.add(ws)
        logger.info(f"WS client connected: {id(ws)} (total={len(self._clients)})")

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(ws)
        logger.info(f"WS client disconnected: {id(ws)} (total={len(self._clients)})")

    async def broadcast(self, payload: Dict[str, Any]) -> None:
        """广播 JSON 给所有 client。失败不抛出（一个 client 坏不拖累整体）。"""
        if not self._clients:
            return
        msg = json.dumps(payload, default=str)
        # snapshot + gather 避免 disconnect 时的 race
        async with self._lock:
            targets = list(self._clients)
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
        return {"connected_clients": len(self._clients)}


ws_broadcaster = WebSocketBroadcaster()


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
    """
    await ws_broadcaster.connect(ws)
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
async def ws_stats():
    """WebSocket 连接统计 (调试用)。"""
    return ws_broadcaster.stats()


@app.get("/api/debug/llm")
async def debug_llm():
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
            text = call_gemini(prompt, max_tokens=max_tok, system_instruction=sys_instr)
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

    return {
        "n_pickups": len(pickup_locations),
        "n_deliveries": len(delivery_locations),
        "n_vehicles": len(vehicles_data),
        "scenarios": scenarios,
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


@app.get("/api/persistence/cohort-retention-by-period")
async def get_cohort_retention_by_period(n_periods: int = 4):
    """
    Supply 留存按时段划分 (iter #19) — 早期 vs 后期 retention 对比。

    将所有 cycle 按 sim_day 顺序分成 n_periods 段 (默认 4 段 = 四分位),
    每段独立计算 retention rate, 让用户看 早期 vs 后期 churn 趋势。

    Query:
    - n_periods: 分多少段 (default 4, range 1-10)

    Returns:
        {
          total_supply_ids, n_periods, period_labels,
          periods: [{period_idx, period_label, sim_day_range,
                     n_supply_ids, n_one_time, n_repeating,
                     retention_rate_pct, one_time_pct}, ...],
          trend: "improving" | "declining" | "stable" | "unknown"
        }
    """
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    if n_periods < 1 or n_periods > 10:
        raise HTTPException(
            status_code=400,
            detail=f"n_periods must be 1-10, got {n_periods}",
        )
    return coordinator.persistence.get_cohort_retention_by_period(
        n_periods=n_periods,
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


@app.post("/api/admin/db-maintenance")
async def post_db_maintenance():
    """
    DB 维护 (iter #16) — VACUUM + ANALYZE。

    VACUUM: rebuild DB file, 释放碎片空间, 减小文件体积
    ANALYZE: 收集统计信息, 帮助 query planner 选最优 index

    Returns:
        {action, size_before_bytes, size_after_bytes,
         reclaimed_bytes, reclaimed_pct, success}
    """
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    return coordinator.persistence.vacuum()


@app.get("/api/admin/db-export")
async def export_db_data(
    table: str,
    fmt: str = "json",
    limit: int = 10000,
    since_sim_day: Optional[int] = None,
    gzip: bool = False,
):
    """
    Unified DB export endpoint (iter #18 + iter #19 gzip) — table + format export。

    Query:
    - table: cycles / supplies / matches / routes / llm_decisions
    - fmt: csv / json / ndjson (注意: 用 fmt 不是 format, 避免与 builtin 冲突)
    - limit: 最多多少行 (default 10000, max 50000)
    - since_sim_day: 只返 >= 该 sim_day 的行
    - gzip: bool = False — 是否 gzip 压缩 (iter #19, 大 payload 省带宽)
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
    if fmt not in ("csv", "json", "ndjson"):
        raise HTTPException(
            status_code=400,
            detail=f"Unknown format '{fmt}'. Valid: csv / json / ndjson",
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
    else:  # ndjson
        ndjson_str = "\n".join(json.dumps(r) for r in rows)
        filename = f"green_logistics_{table}_{limit}.ndjson"
        if gzip:
            return _maybe_gzip(ndjson_str.encode("utf-8"), True, filename)
        return FastAPIResponse(
            content=ndjson_str,
            media_type="application/x-ndjson",
            headers={"Content-Disposition": f"attachment; filename=\"{filename}\""},
        )


@app.get("/api/admin/db-stats")
async def get_db_stats():
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
async def get_db_info():
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
