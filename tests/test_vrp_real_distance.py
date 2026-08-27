"""
Tests for VRPSolver use_real_roads feature (iter #7).

VRPSolver 现在默认 use_real_roads=True,通过 real_distance.build_distance_matrix
用 OSM 真实路网。失败 → 自动 fallback Haversine,不会抛异常。

测试设计:
- _ensure_distance_matrix() / _calculate_distance_matrix_real() / _calculate_distance_matrix_haversine()
- distance_source 属性
- solve() / solve_pareto() 返回 distance_source
- set_distance_matrix() 仍然有效 ("preset" source)
- _snapshot() 复制所有 3 个新参数
"""

import pytest
import numpy as np
from optimization.vrp_solver import VRPSolver, Location, Vehicle


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def borås_depot():
    return Location(id="DEPOT", lat=57.7089, lon=14.1618, type="depot")


@pytest.fixture
def borås_pickup_delivery():
    """3 个 Borås 区域内的 pickup + delivery + 1 vehicle。"""
    pickup = Location(id="P1", lat=57.7300, lon=14.1900, demand_tons=5.0, type="pickup")
    delivery = Location(id="D1", lat=57.6700, lon=14.1000, demand_tons=-5.0, type="delivery")
    return pickup, delivery


@pytest.fixture
def one_vehicle(borås_depot):
    return Vehicle(
        id="V1", capacity_tons=20.0,
        start_location=borås_depot,
        co2_rate=0.85, cost_per_km=2.6,
    )


# ============================================================
# __init__ 参数
# ============================================================

class TestVRPSolverInit:
    def test_defaults(self):
        s = VRPSolver()
        assert s.use_real_roads is True
        assert s.region == "Borås, Sweden"
        assert s.distance_timeout_s == 30
        assert s._distance_source is None
        assert s.distance_source is None  # property

    def test_custom_args(self):
        s = VRPSolver(
            use_real_roads=False,
            region="Göteborg, Sweden",
            distance_timeout_s=10,
        )
        assert s.use_real_roads is False
        assert s.region == "Göteborg, Sweden"
        assert s.distance_timeout_s == 10


# ============================================================
# _ensure_distance_matrix
# ============================================================

class TestEnsureDistanceMatrix:
    def test_haversine_path(self, borås_depot, borås_pickup_delivery, one_vehicle):
        """use_real_roads=False → 一定走 haversine。"""
        pickup, delivery = borås_pickup_delivery
        s = VRPSolver(use_real_roads=False)
        s.add_location(borås_depot)
        s.add_location(pickup)
        s.add_location(delivery)
        s.add_vehicle(one_vehicle)

        assert s.distance_matrix is None
        s._ensure_distance_matrix()
        assert s.distance_matrix.shape == (3, 3)
        assert s.distance_source == "haversine"
        # 对称
        assert s.distance_matrix[0, 1] == pytest.approx(s.distance_matrix[1, 0])

    def test_real_roads_path_or_fallback(self, borås_depot, borås_pickup_delivery, one_vehicle):
        """use_real_roads=True → osm 或 haversine,取决于网络。都不应抛异常。"""
        pickup, delivery = borås_pickup_delivery
        s = VRPSolver(use_real_roads=True, region="Borås, Sweden", distance_timeout_s=30)
        s.add_location(borås_depot)
        s.add_location(pickup)
        s.add_location(delivery)
        s.add_vehicle(one_vehicle)

        s._ensure_distance_matrix()
        assert s.distance_matrix.shape == (3, 3)
        assert s.distance_source in ("osm", "haversine")
        # 不应该 pure zero matrix (除了对角线)
        assert s.distance_matrix[0, 0] == 0
        assert s.distance_matrix[0, 1] > 0

    def test_preset_matrix_not_rebuilt(self, borås_depot, borås_pickup_delivery, one_vehicle):
        """caller 已经 set_distance_matrix → _ensure_distance_matrix 不重新构建。"""
        pickup, delivery = borås_pickup_delivery
        s = VRPSolver(use_real_roads=True)
        s.add_location(borås_depot)
        s.add_location(pickup)
        s.add_location(delivery)
        s.add_vehicle(one_vehicle)

        # 注入 preset matrix
        custom = np.array([
            [0.0, 1.0, 2.0],
            [1.0, 0.0, 3.0],
            [2.0, 3.0, 0.0],
        ])
        s.set_distance_matrix(custom)
        s._ensure_distance_matrix()
        # 应该仍是 caller 注入的 matrix
        assert np.array_equal(s.distance_matrix, custom)
        assert s.distance_source == "preset"


# ============================================================
# solve() 返回 distance_source
# ============================================================

class TestSolveDistanceSource:
    def test_solve_returns_distance_source_haversine(
        self, borås_depot, borås_pickup_delivery, one_vehicle,
    ):
        pickup, delivery = borås_pickup_delivery
        s = VRPSolver(use_real_roads=False)
        s.add_location(borås_depot)
        s.add_location(pickup)
        s.add_location(delivery)
        s.add_vehicle(one_vehicle)

        result = s.solve(time_limit_seconds=5)
        assert "distance_source" in result
        assert result["distance_source"] == "haversine"
        assert result["use_real_roads"] is False
        assert result["status"] in ("optimal", "heuristic")

    def test_solve_pareto_includes_distance_source(
        self, borås_depot, borås_pickup_delivery, one_vehicle,
    ):
        """solve_pareto 每个点都携带 distance_source。"""
        pickup, delivery = borås_pickup_delivery
        s = VRPSolver(use_real_roads=False)
        s.add_location(borås_depot)
        s.add_location(pickup)
        s.add_location(delivery)
        s.add_vehicle(one_vehicle)

        pareto = s.solve_pareto(n_points=3, time_limit_seconds=3)
        assert len(pareto) == 3
        for p in pareto:
            assert "distance_source" in p


# ============================================================
# _snapshot 复制新参数
# ============================================================

class TestSnapshotCopiesNewParams:
    def test_snapshot_carries_use_real_roads_region_timeout(
        self, borås_depot, borås_pickup_delivery, one_vehicle,
    ):
        pickup, delivery = borås_pickup_delivery
        s = VRPSolver(
            use_real_roads=False,
            region="Göteborg, Sweden",
            distance_timeout_s=15,
        )
        s.add_location(borås_depot)
        s.add_location(pickup)
        s.add_location(delivery)
        s.add_vehicle(one_vehicle)

        snap = s._snapshot()
        assert snap.use_real_roads is False
        assert snap.region == "Göteborg, Sweden"
        assert snap.distance_timeout_s == 15


# ============================================================
# Edge cases
# ============================================================

class TestEdgeCases:
    def test_single_location_no_matrix_built(self, borås_depot):
        """1 个 location (仅 depot) → matrix 1x1 全 0,_ensure_distance_matrix 不应爆。"""
        s = VRPSolver(use_real_roads=True)
        s.add_location(borås_depot)
        s._ensure_distance_matrix()
        assert s.distance_matrix.shape == (1, 1)
        assert s.distance_matrix[0, 0] == 0
        # source 应该是 trivial (n<2 不走 OSM/Haversine)
        assert s.distance_source == "trivial"

    def test_no_vehicles_returns_empty_routes(self, borås_depot, borås_pickup_delivery):
        """无车辆 → solve 返回 status + distance_source。"""
        pickup, delivery = borås_pickup_delivery
        s = VRPSolver(use_real_roads=False)
        s.add_location(borås_depot)
        s.add_location(pickup)
        s.add_location(delivery)
        # 没 add_vehicle

        result = s.solve(time_limit_seconds=2)
        assert result["status"] in ("optimal", "heuristic", "fallback_nearest_neighbor")
        # distance_source 应仍是 haversine
        assert result["distance_source"] == "haversine"