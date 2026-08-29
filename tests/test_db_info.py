"""
Tests for /api/admin/db-info endpoint (iter #20).

Covers:
- Persistence.get_db_info() — full DB metadata (size, checksum, version)
- API endpoint /api/admin/db-info
- All required fields present
- 503 when no persistence
"""

from __future__ import annotations

import pytest

from agents.persistence import Persistence


@pytest.fixture
def persistence(tmp_path) -> Persistence:
    """Fresh DB per test."""
    db_path = tmp_path / "test_db_info.db"
    return Persistence(str(db_path))


def _record_cycle(p: Persistence, cid: str, day: int = 1) -> None:
    """Seed a minimal cycle."""
    p.begin_cycle(cid, sim_day=day, sim_hour=10, activity_factor=1.0,
                  n_supply_offers=1, n_demand_requests=1)
    p.commit_cycle(cid, kpi={
        "n_supply_offers": 1, "n_demand_requests": 1, "n_matches": 1,
        "total_tons": 5.0, "total_cost_sek": 100, "total_co2_kg": 5,
        "total_distance_km": 10, "n_vehicles_used": 1,
        "n_vehicles_available": 5, "fleet_utilization_pct": 20,
        "solver_status": "feasible",
    }, wall_duration_ms=10)


class TestDBInfoPersistence:
    """Tests for Persistence.get_db_info()."""

    def test_empty_db_info(self, persistence):
        """Empty DB → all fields present, totals = 0."""
        info = persistence.get_db_info()
        assert info["db_exists"] is True
        assert info["db_size_bytes"] >= 0
        assert info["db_size_mb"] >= 0
        assert info["db_modified_at"] is not None
        assert len(info["md5_checksum_first_100kb"]) == 32  # md5 hex length
        assert info["sqlite_version"] != ""
        assert isinstance(info["schema_version"], int)
        assert info["auto_vacuum_mode"] in ("disabled", "full", "incremental", "unknown")
        assert info["total_rows"] == 0
        assert info["index_count"] >= 0
        assert "time_range" in info

    def test_db_with_data(self, persistence):
        """DB with seeded cycles → total_rows > 0, time_range populated."""
        _record_cycle(persistence, "OPT0001", day=1)
        _record_cycle(persistence, "OPT0002", day=2)
        info = persistence.get_db_info()
        assert info["total_rows"] > 0
        assert info["time_range"]["oldest_cycle"] is not None
        assert info["time_range"]["newest_cycle"] is not None

    def test_md5_changes_after_insert(self, persistence):
        """md5 应该随 DB 写入而变化."""
        info_before = persistence.get_db_info()
        _record_cycle(persistence, "OPT0001")
        info_after = persistence.get_db_info()
        assert info_before["md5_checksum_first_100kb"] != info_after["md5_checksum_first_100kb"]

    def test_table_counts_all_tables(self, persistence):
        """table_counts 应该包含所有 6 张表的 row count."""
        _record_cycle(persistence, "OPT0001")
        info = persistence.get_db_info()
        expected_tables = {
            "optimization_cycles", "supply_offers", "demand_requests",
            "matches", "routes", "llm_decisions"
        }
        assert set(info["table_counts"].keys()) == expected_tables

    def test_db_modified_at_iso_format(self, persistence):
        """db_modified_at 应该是 ISO format string."""
        info = persistence.get_db_info()
        # Should parse as ISO datetime
        from datetime import datetime
        if info["db_modified_at"]:
            datetime.fromisoformat(info["db_modified_at"])


class TestAPIDBInfo:
    """API tests for /api/admin/db-info (iter #20)."""

    @pytest.fixture(autouse=True)
    def setup_fake_coordinator(self, tmp_path):
        from unittest.mock import MagicMock
        from web.backend import main as backend_main
        from fastapi.testclient import TestClient

        db_path = tmp_path / "api_db_info.db"
        persistence = Persistence(str(db_path))
        _record_cycle(persistence, "OPT0001", day=1)
        _record_cycle(persistence, "OPT0002", day=2)

        fake_coord = MagicMock()
        fake_coord.persistence = persistence
        backend_main.coordinator = fake_coord
        self.client = TestClient(backend_main.app)

    def test_endpoint_returns_200(self):
        """GET /api/admin/db-info returns 200."""
        resp = self.client.get("/api/admin/db-info")
        assert resp.status_code == 200

    def test_response_has_all_fields(self):
        """Response includes all expected fields."""
        resp = self.client.get("/api/admin/db-info")
        data = resp.json()
        expected = {
            "db_path", "db_exists", "db_size_bytes", "db_size_mb",
            "db_modified_at", "md5_checksum_first_100kb",
            "sqlite_version", "schema_version", "auto_vacuum_mode",
            "table_counts", "total_rows", "index_count", "time_range",
        }
        assert expected.issubset(data.keys()), f"Missing fields: {expected - data.keys()}"

    def test_md5_is_32_hex_chars(self):
        """md5_checksum_first_100kb 应该是 32 char hex."""
        resp = self.client.get("/api/admin/db-info")
        data = resp.json()
        assert len(data["md5_checksum_first_100kb"]) == 32
        # Should be valid hex
        int(data["md5_checksum_first_100kb"], 16)

    def test_total_rows_is_positive(self):
        resp = self.client.get("/api/admin/db-info")
        data = resp.json()
        assert data["total_rows"] > 0

    def test_time_range_populated(self):
        resp = self.client.get("/api/admin/db-info")
        data = resp.json()
        assert data["time_range"]["oldest_cycle"] is not None
        assert data["time_range"]["newest_cycle"] is not None

    def test_endpoint_returns_503_if_no_persistence(self):
        from web.backend import main as backend_main
        old_coord = backend_main.coordinator
        backend_main.coordinator = None
        try:
            resp = self.client.get("/api/admin/db-info")
            assert resp.status_code == 503
        finally:
            backend_main.coordinator = old_coord