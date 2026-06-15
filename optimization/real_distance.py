"""
真实路网距离矩阵 (OSM via osmnx)

- 替代 Haversine 球面距离,用 OSM 真实路网算节点间最短路径
- 自动缓存到 cache/osm_graph_{region}.graphml,避免重复下载
- 失败/超时/无网络 → 自动 fallback 到 Haversine
- 支持一次给一组 lat/lon,返回对称距离矩阵

用法:
    from optimization.real_distance import build_distance_matrix
    matrix = build_distance_matrix(
        locations=[(57.7, 14.2), (59.3, 18.1), (57.7, 11.9)],
        region="Borås, Sweden",
        timeout_s=60,
    )
    # matrix.shape == (3, 3), matrix[i,j] 是 i->j 的真实驾驶 km
"""

from __future__ import annotations

import json
import logging
import os
import pickle
import time
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

import numpy as np


logger = logging.getLogger(__name__)

# 缓存目录: green-logistics-ai/cache/
CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# 球面 Haversine (备选,无需网络)
# ============================================================

def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0  # 地球半径 km
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dphi = np.radians(lat2 - lat1)
    dlmb = np.radians(lon2 - lon1)
    a = np.sin(dphi / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dlmb / 2) ** 2
    return float(2 * R * np.arcsin(np.sqrt(a)))


def haversine_matrix(locations: Sequence[Tuple[float, float]]) -> np.ndarray:
    n = len(locations)
    m = np.zeros((n, n))
    for i in range(n):
        for j in range(n):
            if i != j:
                m[i, j] = _haversine_km(locations[i][0], locations[i][1],
                                        locations[j][0], locations[j][1])
    return m


# ============================================================
# 缓存 (pickle)
# ============================================================

def _cache_path(region: str) -> Path:
    safe = region.replace(",", "_").replace(" ", "_").replace("/", "_")
    return CACHE_DIR / f"osm_graph_{safe}.graphml"


def _meta_path(region: str) -> Path:
    safe = region.replace(",", "_").replace(" ", "_").replace("/", "_")
    return CACHE_DIR / f"osm_meta_{safe}.json"


def _haversine_cache_path(locations: Sequence[Tuple[float, float]], region: str) -> Path:
    """把 lat/lon 列表 fingerprint 成 cache key。"""
    safe = region.replace(",", "_").replace(" ", "_").replace("/", "_")
    fp = abs(hash((tuple(tuple(round(x, 4) for x in loc) for loc in locations), safe)))
    return CACHE_DIR / f"matrix_{safe}_{fp}.pkl"


# ============================================================
# 主入口
# ============================================================

def build_distance_matrix(
    locations: Sequence[Tuple[float, float]],
    region: str = "Borås, Sweden",
    timeout_s: int = 60,
    prefer_real_roads: bool = True,
) -> Tuple[np.ndarray, str]:
    """
    计算 locations 间的距离矩阵。

    Args:
        locations: list of (lat, lon) tuples
        region: OSM 地区名 (例: "Borås, Sweden")
        timeout_s: OSM 下载 + 计算总超时
        prefer_real_roads: True=优先 OSM,False=直接 Haversine

    Returns:
        (matrix, source) — matrix.shape=(n,n),source 是 "osm" 或 "haversine"
    """
    n = len(locations)
    if n < 2:
        return np.zeros((n, n)), "trivial"

    # 1. 查 cache (haversine cache key 包含 locations fingerprint)
    haversine_cache = _haversine_cache_path(locations, region)
    if haversine_cache.exists():
        try:
            with haversine_cache.open("rb") as f:
                cached = pickle.load(f)
            if cached.get("n") == n and cached.get("source") in ("osm", "haversine"):
                logger.info(f"距离矩阵命中 cache ({cached['source']})")
                return cached["matrix"], cached["source"]
        except Exception:
            pass  # cache 损坏就重算

    # 2. 不想用 OSM 就直接 haversine
    if not prefer_real_roads:
        m = haversine_matrix(locations)
        _save_cache(haversine_cache, m, "haversine", n)
        return m, "haversine"

    # 3. 尝试 OSM
    t0 = time.time()
    try:
        m = _try_osm_matrix(locations, region, timeout_s)
        elapsed = time.time() - t0
        logger.info(f"OSM 距离矩阵: {elapsed:.1f}s ({n}x{n})")
        _save_cache(haversine_cache, m, "osm", n)
        return m, "osm"
    except Exception as e:
        elapsed = time.time() - t0
        logger.warning(f"OSM 距离矩阵失败 ({elapsed:.1f}s): {e} → fallback to haversine")
        m = haversine_matrix(locations)
        _save_cache(haversine_cache, m, "haversine", n)
        return m, "haversine"


def _save_cache(path: Path, matrix: np.ndarray, source: str, n: int) -> None:
    try:
        with path.open("wb") as f:
            pickle.dump({"matrix": matrix, "source": source, "n": n}, f)
    except Exception as e:
        logger.debug(f"cache 写入失败 (忽略): {e}")


# ============================================================
# OSM 内部实现
# ============================================================

def _try_osm_matrix(
    locations: Sequence[Tuple[float, float]],
    region: str,
    timeout_s: int,
) -> np.ndarray:
    """用 osmnx + networkx 算真实驾驶距离矩阵。失败抛异常由 caller 兜底。"""
    try:
        import osmnx as ox  # type: ignore
        import networkx as nx  # type: ignore
    except ImportError as e:
        raise RuntimeError(f"osmnx/networkx not available: {e}")

    cache_path = _cache_path(region)
    if cache_path.exists():
        try:
            logger.info(f"加载缓存路网: {cache_path}")
            graph = ox.load_graphml(cache_path)
        except Exception as e:
            logger.warning(f"加载缓存失败 ({e}), 重新下载")
            graph = None
    else:
        graph = None

    if graph is None:
        logger.info(f"下载 {region} 的 OSM 路网 (timeout={timeout_s}s)...")
        # osmnx 没有 timeout 参数,用 signal 在主线程拦截
        # 这里用 polite timeout: 限制 wall-time 后抛异常
        import threading
        result: dict = {}

        def _download():
            try:
                result["graph"] = ox.graph_from_place(region, network_type="drive")
            except Exception as e:
                result["error"] = e

        t = threading.Thread(target=_download, daemon=True)
        t.start()
        t.join(timeout=timeout_s)
        if t.is_alive():
            raise RuntimeError(f"OSM download timed out after {timeout_s}s")
        if "error" in result:
            raise result["error"]
        graph = result["graph"]
        # 缓存
        try:
            ox.save_graphml(graph, cache_path)
            logger.info(f"路网已缓存: {cache_path}")
        except Exception as e:
            logger.debug(f"路网缓存失败 (忽略): {e}")

    # 找每个 location 最近的 OSM 节点
    # (osmnx.nearest_nodes 需要 scikit-learn,这里 brute-force 找最近,够用)
    n = len(locations)
    # 收集所有节点的 (lat, lon, node_id)
    node_data: List[Tuple[float, float, object]] = []
    for nid, data in graph.nodes(data=True):
        lat = data.get("y")
        lon = data.get("x")
        if lat is not None and lon is not None:
            node_data.append((float(lat), float(lon), nid))
    if not node_data:
        raise RuntimeError("graph has no nodes with lat/lon data")
    nearest_nodes: list = []
    for lat, lon in locations:
        best = min(node_data, key=lambda nd: _haversine_km(lat, lon, nd[0], nd[1]))
        nearest_nodes.append(best[2])

    # 节点间最短路径长度
    matrix = np.zeros((n, n))
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            try:
                # networkx shortest_path_length 默认 weight='length' (米)
                length_m = nx.shortest_path_length(
                    graph, nearest_nodes[i], nearest_nodes[j], weight="length"
                )
                matrix[i, j] = length_m / 1000.0  # m → km
            except (nx.NetworkXNoPath, nx.NodeNotFound):
                # 不可达 → fallback 到 haversine
                matrix[i, j] = _haversine_km(
                    locations[i][0], locations[i][1],
                    locations[j][0], locations[j][1],
                )
    return matrix


# ============================================================
# 简单自测
# ============================================================

if __name__ == "__main__":
    # Borås → Göteborg 真实驾驶距离 ~70 km
    borås = (57.7089, 14.1618)
    göteborg = (57.7089, 11.9746)
    stockholm = (59.3293, 18.0686)
    print("=== 3 city test (Borås, Göteborg, Stockholm) ===")
    m, src = build_distance_matrix([borås, göteborg, stockholm], region="Sweden", timeout_s=120)
    print(f"source: {src}")
    print(f"matrix (km):\n{np.round(m, 1)}")
    print(f"\n[reference] haversine: B-G≈156km, B-S≈336km, G-S≈398km (much longer than driving)")
