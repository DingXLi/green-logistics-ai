"""
Facility distance matrix tests (iter #15) — /api/facilities/distance-matrix.

测试覆盖:
- get_distance_matrix() returns N×N matrix
- Symmetric (matrix[i][j] == matrix[j][i])
- Diagonal is 0
- City filter + facility_type filter
- API endpoint 200 / empty case
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestDistanceMatrixData(unittest.TestCase):
    """get_distance_matrix() data-layer tests"""

    def test_matrix_full(self):
        from data.real_sweden_facilities import (
            ALL_FACILITIES, get_distance_matrix,
        )
        result = get_distance_matrix(ALL_FACILITIES, use_haversine=True)
        self.assertEqual(result["n_facilities"], len(ALL_FACILITIES))
        self.assertEqual(len(result["facility_ids"]), len(ALL_FACILITIES))
        self.assertEqual(len(result["matrix_km"]), len(ALL_FACILITIES))
        # 13 facilities → pair_count = 13*12/2 = 78
        self.assertEqual(result["pair_count"], 78)
        self.assertEqual(result["method"], "haversine")

    def test_matrix_symmetric(self):
        from data.real_sweden_facilities import (
            ALL_FACILITIES, get_distance_matrix,
        )
        result = get_distance_matrix(ALL_FACILITIES, use_haversine=True)
        m = result["matrix_km"]
        for i in range(len(m)):
            for j in range(i + 1, len(m)):
                self.assertEqual(m[i][j], m[j][i], f"asymmetric at {i},{j}")

    def test_matrix_diagonal_zero(self):
        from data.real_sweden_facilities import (
            ALL_FACILITIES, get_distance_matrix,
        )
        result = get_distance_matrix(ALL_FACILITIES, use_haversine=True)
        m = result["matrix_km"]
        for i in range(len(m)):
            self.assertEqual(m[i][i], 0.0)

    def test_matrix_known_distance(self):
        """Borås ↔ Göteborg 大约 120-140 km (实距 haversine)"""
        from data.real_sweden_facilities import (
            BORAS_FACILITIES, GOTEBORG_FACILITIES, get_distance_matrix,
        )
        mixed = BORAS_FACILITIES[:1] + GOTEBORG_FACILITIES[:1]
        result = get_distance_matrix(mixed, use_haversine=True)
        m = result["matrix_km"]
        d = m[0][1]
        # Borås (57.72, 14.16) ↔ Göteborg (57.73, 12.01): haversine ~127 km
        self.assertGreater(d, 100)
        self.assertLess(d, 160)


class TestDistanceMatrixApi(unittest.TestCase):
    """/api/facilities/distance-matrix"""

    def setUp(self):
        from web.backend import main as backend_main
        backend_main.coordinator = None  # 避免需要 coordinator
        from fastapi.testclient import TestClient
        self.client = TestClient(backend_main.app)
        self.backend_main = backend_main

    def test_full_matrix_200(self):
        resp = self.client.get("/api/facilities/distance-matrix")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["n_facilities"], 13)
        self.assertEqual(data["method"], "haversine")
        self.assertEqual(data["pair_count"], 78)

    def test_city_filter(self):
        resp = self.client.get("/api/facilities/distance-matrix?city=Borås")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        # Borås has 4 facilities
        self.assertEqual(data["n_facilities"], 4)
        # smaller pair count
        self.assertEqual(data["pair_count"], 4 * 3 // 2)

    def test_facility_type_filter(self):
        resp = self.client.get(
            "/api/facilities/distance-matrix?facility_type=recycling_center"
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        # Should be < 13 (filtered)
        self.assertLess(data["n_facilities"], 13)
        self.assertGreater(data["n_facilities"], 0)

    def test_city_filter_stockholm(self):
        """city=Stockholm 返回该市设施数 (5)"""
        resp = self.client.get(
            "/api/facilities/distance-matrix?city=Stockholm"
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["n_facilities"], 5)

    def test_method_haversine(self):
        resp = self.client.get("/api/facilities/distance-matrix")
        data = resp.json()
        # 默认 use_real_roads=False → haversine
        self.assertEqual(data["method"], "haversine")


if __name__ == "__main__":
    unittest.main()
