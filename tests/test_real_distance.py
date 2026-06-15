"""
Tests for real_distance module (OSM-based distance matrix).

单测,网络/不网络都要能跑:
- haversine 模式总是 OK
- OSM 模式如果 Overpass 失败,要 fallback 到 haversine (不报错)
"""

import time
import numpy as np
import pytest

from optimization.real_distance import (
    build_distance_matrix,
    haversine_matrix,
    _haversine_km,
)


# ============================================================
# Haversine 单元
# ============================================================

class TestHaversine:
    def test_same_point_zero(self):
        assert _haversine_km(57.7, 14.2, 57.7, 14.2) == 0.0

    def test_borås_to_göteborg(self):
        # Borås → Göteborg 直线 ~130 km (经度差 2.2°)
        d = _haversine_km(57.7089, 14.1618, 57.7089, 11.9746)
        assert 125 < d < 135

    def test_borås_to_stockholm(self):
        d = _haversine_km(57.7089, 14.1618, 59.3293, 18.0686)
        assert 280 < d < 310  # ~290 km straight line

    def test_matrix_shape(self):
        locs = [(57.7, 14.2), (59.3, 18.1), (57.7, 11.9)]
        m = haversine_matrix(locs)
        assert m.shape == (3, 3)
        assert m[0, 0] == 0 and m[1, 1] == 0 and m[2, 2] == 0
        # 对称
        assert m[0, 1] == pytest.approx(m[1, 0])
        assert m[0, 2] == pytest.approx(m[2, 0])


# ============================================================
# build_distance_matrix (主入口)
# ============================================================

class TestBuildDistanceMatrix:
    def test_empty_returns_trivial(self):
        m, src = build_distance_matrix([], prefer_real_roads=False)
        assert m.shape == (0, 0)
        assert src == "trivial"

    def test_single_point(self):
        m, src = build_distance_matrix([(57.7, 14.2)], prefer_real_roads=False)
        assert m.shape == (1, 1)
        assert m[0, 0] == 0

    def test_haversine_path_explicit(self):
        locs = [(57.7089, 14.1618), (57.7089, 11.9746)]
        m, src = build_distance_matrix(locs, prefer_real_roads=False)
        assert src == "haversine"
        assert 125 < m[0, 1] < 135

    def test_osm_path_or_fallback(self):
        """如果 Overpass 在线 + Borås region 命中,返回 osm;否则 haversine。
        都不应该抛异常。"""
        locs = [
            (57.7089, 14.1618),  # Borås center
            (57.7300, 14.1900),
            (57.6700, 14.1000),
        ]
        t0 = time.time()
        m, src = build_distance_matrix(
            locs, region="Borås, Sweden", timeout_s=60, prefer_real_roads=True
        )
        elapsed = time.time() - t0
        assert src in ("osm", "haversine")  # 任一都 OK
        assert m.shape == (3, 3)
        assert elapsed < 90  # 60s timeout + buffer

    def test_cache_hit(self):
        """第二次同 locations 应该用 cache (明显快)。"""
        locs = [(57.7089, 14.1618), (57.7300, 14.1900)]
        t0 = time.time()
        m1, src1 = build_distance_matrix(locs, region="Borås, Sweden", timeout_s=60, prefer_real_roads=True)
        first = time.time() - t0
        t0 = time.time()
        m2, src2 = build_distance_matrix(locs, region="Borås, Sweden", timeout_s=60, prefer_real_roads=True)
        second = time.time() - t0
        # 第二次应该 < 1s (cache hit)
        assert second < 1.0, f"cache miss? first={first:.2f}s second={second:.2f}s"
        assert np.allclose(m1, m2)
        assert src1 == src2
