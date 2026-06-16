# ============================================
# Green Logistics AI - HuggingFace Spaces Dockerfile
# ============================================
# 多阶段构建 -> 瘦镜像
# Stage 1: 装依赖
# Stage 2: 拷代码
# ============================================

FROM python:3.11-slim AS runtime

# 容器内时区
ENV TZ=Europe/Stockholm \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    # HF Spaces 持久化卷 (重启不丢)
    GL_DB_PATH=/data/simulation.db \
    # cache 目录指向 /data (避免镜像层爆)
    EXTERNAL_SIGNALS_CACHE=/data/cache \
    OSM_CACHE=/data/osm_cache

WORKDIR /app

# ---- 系统依赖 ----
# libgdal/geos/proj 是 geopandas/shapely 需要的
# curl 用于 HEALTHCHECK
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc g++ \
        libgdal-dev libgeos-dev libproj-dev \
        curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# ---- Python 依赖 ----
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ---- 应用代码 ----
COPY agents/        ./agents/
COPY optimization/  ./optimization/
COPY synthetic/     ./synthetic/
COPY config/        ./config/
COPY data/          ./data/
COPY web/backend/   ./web/backend/

# ---- 持久化目录 ----
# HF Spaces 会把 /data 挂到持久化卷; 我们把 SQLite + 缓存放进去
RUN mkdir -p /data/cache /data/osm_cache

# ---- 端口 (HF 默认 7860, 但 FastAPI 用 8000, README header 里声明) ----
EXPOSE 8000

# ---- 健康检查 ----
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

# ---- 启动 ----
CMD ["uvicorn", "web.backend.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
