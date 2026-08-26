"""
Green Logistics AI - Web Backend

FastAPI 应用提供 REST API
"""

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Dict, Any, Optional, Set
from datetime import datetime
from loguru import logger
import sys
import os
import random
import asyncio
import time
import json
from contextlib import asynccontextmanager

# 添加父目录到路径以便导入
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from agents.coordinator import MultiAgentCoordinator
from agents.world_builder import WorldConfig
from optimization.vrp_solver import VRPSolver, Location, Vehicle
from synthetic.data_generator import SyntheticDataGenerator

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

    async def _run_cycle_safe(self) -> None:
        """单次 cycle: lock 保护 + try/except 隔离错误"""
        if self._lock.locked():
            # 上一轮还没跑完, 跳过这一轮避免重叠
            logger.debug("Scheduler: 上一 cycle 未完成, 跳过本次")
            return
        async with self._lock:
            self.running = True
            try:
                result = await self.coord.run_optimization_cycle()
                self.cycle_count += 1
                self.last_cycle_at = datetime.utcnow().isoformat() + "Z"
                self.last_cycle_id = result.get("optimization_id")
                self.last_error = None
                matches = (result.get("matches") or {}).get("total_matches", 0)
                logger.info(
                    f"Scheduler cycle #{self.cycle_count} 完成: "
                    f"{self.last_cycle_id} ({matches} matches)"
                )
                # WebSocket 广播: cycle 完成推给所有 dashboard
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
            "error_count": self.error_count,
            "last_error": self.last_error,
            "last_cycle_at": self.last_cycle_at,
            "last_cycle_id": self.last_cycle_id,
            "next_cycle_in_seconds": next_in,
            "started_at": self.started_at,
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
    global scheduler
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


class OptimizationResponse(BaseModel):
    """优化响应"""
    status: str
    optimization_id: Optional[str] = None
    timestamp: str
    matches_count: int
    total_tons: float
    total_cost_sek: float
    total_co2_kg: float


class FleetStatusResponse(BaseModel):
    """车队状态响应"""
    total_vehicles: int
    available: int
    en_route: int
    utilization_rate: float


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
    """健康检查"""
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat()
    }


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
            last_result = await coordinator.run_optimization_cycle()
        # 写 cache
        async with _optimize_cache_lock:
            _optimize_cache["last"] = {"ts": time.monotonic(), "result": last_result}
        cached_flag = False
        logger.info(f"Run 按钮触发新 cycle: {last_result.get('optimization_id')}")

    # 提取关键指标
    matches = last_result.get("matches", {})
    routes = last_result.get("route_optimization", {})

    return OptimizationResponse(
        status="success" if not cached_flag else "cached",
        optimization_id=last_result.get("optimization_id"),
        timestamp=last_result.get("timestamp"),
        matches_count=matches.get("total_matches", 0),
        total_tons=matches.get("total_tons", 0),
        total_cost_sek=routes.get("total_cost_sek", 0),
        total_co2_kg=routes.get("total_co2_kg", 0)
    )


@app.get("/api/facilities")
async def get_facilities(city: Optional[str] = None, facility_type: Optional[str] = None):
    """
    返回真实瑞典废料处理设施 (Renova / Ragn-Sells / Stena / Swerock / Suez / Sysav 等)。

    数据源: data/real_sweden_facilities (手工整理的 13 个公司公开设施坐标)
    Query: city / facility_type (可选过滤)
    """
    from data.real_sweden_facilities import (
        ALL_FACILITIES,
        FACILITY_TYPE_COUNTS,
        get_facilities_by_city,
        get_facilities_by_type,
        get_facility_count,
    )

    if city:
        facilities = get_facilities_by_city(city)
    elif facility_type:
        facilities = get_facilities_by_type(facility_type)
    else:
        facilities = list(ALL_FACILITIES)

    return {
        "total": len(facilities),
        "total_available": get_facility_count(),
        "facility_type_counts": FACILITY_TYPE_COUNTS,
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
    """返回上一次 cycle 的指标 + 多久前跑的, 供前端展示 'Last updated: 5 min ago'"""
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
    
    return {
        "last_cycle_id": last_cycle.get("cycle_id"),
        "last_cycle_at": last_ts,
        "age_seconds": age_seconds,
        "total_cycles": summary.get("n_cycles", 0),
        "total_tons": summary.get("total_tons", 0),
        "total_cost_sek": summary.get("total_cost_sek", 0),
        "total_co2_kg": summary.get("total_co2_kg", 0),
    }


# ============================================
# V3: Pareto 前沿端点
# ============================================
@app.get("/api/optimize/pareto")
async def get_pareto_front(n_points: int = 10, time_limit_seconds: int = 5):
    """
    返回多目标 (cost vs CO2) Pareto 前沿

    - n_points: 扫描权重点数 (2..20)
    - time_limit_seconds: 每个点的 OR-Tools 时限

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
    solver = VRPSolver()
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
        })

    return {
        "n_points": len(summary),
        "n_pickups": len(pickup_locations),
        "n_deliveries": len(delivery_locations),
        "n_vehicles": len(vehicles_data),
        "pareto": summary,
    }


@app.get("/api/optimize/carbon-scenarios")
async def get_carbon_scenarios(
    carbon_prices: Optional[str] = None,
    time_limit_seconds: int = 3,
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
        solver = VRPSolver()
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


@app.get("/api/sample-data/supply/{location_id}")
async def get_sample_supply_data(location_id: str, days: int = 1):
    """获取示例供应数据"""
    if data_generator is None:
        raise HTTPException(status_code=503, detail="Data generator not initialized")
    
    from datetime import timedelta
    all_data = []
    
    for day in range(days):
        date = datetime.now() + timedelta(days=day)
        data = data_generator.generate_daily_supply(
            location_id=location_id,
            date=date,
            intervals_per_day=24
        )
        all_data.extend(data)
    
    return {
        "location_id": location_id,
        "days": days,
        "data_points": len(all_data),
        "data": all_data
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
async def get_kpi_timeseries():
    """KPI 时间序列（按 sim_day 聚合）"""
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    return coordinator.persistence.get_kpi_timeseries()


@app.get("/api/persistence/summary")
async def get_persistence_summary():
    """全局统计汇总"""
    if coordinator is None or coordinator.persistence is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    return coordinator.persistence.get_summary()


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


# ============================================
# 主程序
# ============================================
if __name__ == "__main__":
    import uvicorn
    
    logger.info("启动 Green Logistics AI 后端服务器...")
    uvicorn.run(app, host="0.0.0.0", port=8000)
