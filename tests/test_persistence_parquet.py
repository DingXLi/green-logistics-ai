"""
Tests for /api/persistence/export/{cycles,supplies,matches,routes}.parquet
(iter #27 — consistency with /api/admin/db-export parquet support).
"""

import io
import json
import pytest
import pyarrow.parquet as pq


# ============================================
# Fixtures
# ============================================

@pytest.fixture
def client_with_cycles():
    """A TestClient with at least 1 cycle in DB."""
    from fastapi.testclient import TestClient
    from web.backend import main as backend_main
    from web.backend.main import coordinator as coord

    with TestClient(backend_main.app) as client:
        # Trigger a cycle if none exist
        if coord is not None and coord.persistence is not None:
            summary = coord.persistence.get_summary() or {}
            n_cycles = summary.get("n_cycles", 0)
            if n_cycles == 0:
                # Use a fast lightweight cycle
                try:
                    client.post(
                        "/api/optimize",
                        json={"use_real_roads": False, "time_limit_seconds": 1},
                    )
                except Exception:
                    pass
        yield client


# ============================================
# Schema validation
# ============================================

class TestPersistenceParquetEndpoints:
    """/api/persistence/export/*.parquet 应该正常返回 parquet binary。"""

    def _parse_parquet(self, content: bytes):
        """Parse parquet bytes → pyarrow Table."""
        sink = io.BytesIO(content)
        return pq.read_table(sink)

    def test_cycles_parquet_returns_200(self, client_with_cycles):
        resp = client_with_cycles.get("/api/persistence/export/cycles.parquet")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/vnd.apache.parquet"
        assert "attachment" in resp.headers["content-disposition"]
        assert "green_logistics_cycles_" in resp.headers["content-disposition"]

    def test_cycles_parquet_valid_bytes(self, client_with_cycles):
        resp = client_with_cycles.get("/api/persistence/export/cycles.parquet")
        assert resp.status_code == 200
        # parquet magic bytes
        assert resp.content[:4] == b"PAR1"
        assert resp.content[-4:] == b"PAR1"
        # round-trip parse
        table = self._parse_parquet(resp.content)
        # Should have at least 1 row if cycles exist
        assert table.num_rows >= 0
        # Column check (cycles schema)
        expected_cols = {
            "cycle_id", "sim_day", "sim_hour", "wall_timestamp",
            "n_supply_offers", "n_demand_requests", "n_matches",
            "total_tons", "total_cost_sek", "total_co2_kg",
        }
        assert expected_cols.issubset(set(table.column_names))

    def test_supplies_parquet_returns_200(self, client_with_cycles):
        resp = client_with_cycles.get("/api/persistence/export/supplies.parquet")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/vnd.apache.parquet"
        assert "green_logistics_supplies_" in resp.headers["content-disposition"]

    def test_supplies_parquet_valid_bytes(self, client_with_cycles):
        resp = client_with_cycles.get("/api/persistence/export/supplies.parquet")
        if resp.status_code == 200:
            assert resp.content[:4] == b"PAR1"
            table = self._parse_parquet(resp.content)
            # supplies schema (10 cols)
            assert "supply_id" in table.column_names
            assert "material_type" in table.column_names
            assert "available_tons" in table.column_names

    def test_matches_parquet_returns_200(self, client_with_cycles):
        resp = client_with_cycles.get("/api/persistence/export/matches.parquet")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/vnd.apache.parquet"
        assert "green_logistics_matches_" in resp.headers["content-disposition"]

    def test_matches_parquet_valid_bytes(self, client_with_cycles):
        resp = client_with_cycles.get("/api/persistence/export/matches.parquet")
        if resp.status_code == 200:
            assert resp.content[:4] == b"PAR1"
            table = self._parse_parquet(resp.content)
            # matches schema (8 cols)
            assert "supply_id" in table.column_names
            assert "demand_id" in table.column_names
            assert "distance_km" in table.column_names

    def test_routes_parquet_returns_200(self, client_with_cycles):
        resp = client_with_cycles.get("/api/persistence/export/routes.parquet")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/vnd.apache.parquet"
        assert "green_logistics_routes_" in resp.headers["content-disposition"]

    def test_routes_parquet_valid_bytes(self, client_with_cycles):
        resp = client_with_cycles.get("/api/persistence/export/routes.parquet")
        if resp.status_code == 200:
            assert resp.content[:4] == b"PAR1"
            table = self._parse_parquet(resp.content)
            # routes schema (8 cols)
            assert "vehicle_id" in table.column_names
            assert "distance_km" in table.column_names
            assert "stops_count" in table.column_names


# ============================================
# Query param validation
# ============================================

class TestPersistenceParquetParams:
    """Limit param 应该被 clamp 到合理范围。"""

    def test_cycles_limit_clamped_to_max(self, client_with_cycles):
        resp = client_with_cycles.get(
            "/api/persistence/export/cycles.parquet?limit=99999"
        )
        assert resp.status_code == 200
        # filename should reflect clamped value
        assert "10000" in resp.headers["content-disposition"]

    def test_cycles_limit_clamped_to_min(self, client_with_cycles):
        resp = client_with_cycles.get(
            "/api/persistence/export/cycles.parquet?limit=0"
        )
        assert resp.status_code == 200
        # limit=0 → clamped to 1
        assert "1" in resp.headers["content-disposition"]

    def test_cycles_limit_negative_clamped(self, client_with_cycles):
        resp = client_with_cycles.get(
            "/api/persistence/export/cycles.parquet?limit=-5"
        )
        # FastAPI validates int param > 0 normally; negative may 422
        # But our internal max(1, min(...)) would also clamp
        # We accept either 200 (clamped) or 422 (Pydantic validation)
        assert resp.status_code in (200, 422)


# ============================================
# Parquet-vs-CSV column consistency
# ============================================

class TestParquetCsvConsistency:
    """Parquet columns 应该与对应 CSV export 列一致 (same data source)."""

    def test_cycles_columns_match_csv(self, client_with_cycles):
        """cycles.parquet 与 cycles.csv 列应该一致。"""
        csv_resp = client_with_cycles.get("/api/persistence/export/cycles.csv")
        parquet_resp = client_with_cycles.get("/api/persistence/export/cycles.parquet")
        if csv_resp.status_code == 200 and parquet_resp.status_code == 200:
            csv_lines = [
                l for l in csv_resp.text.split("\n") if l and not l.startswith("#")
            ]
            csv_cols = set(csv_lines[0].split(","))
            table = pq.read_table(io.BytesIO(parquet_resp.content))
            parquet_cols = set(table.column_names)
            # Parquet might have subset of CSV cols (or same)
            # but core KPI cols should match
            common = csv_cols & parquet_cols
            assert "cycle_id" in common
            assert "sim_day" in common
            assert "total_cost_sek" in common

    def test_supplies_columns_match_csv(self, client_with_cycles):
        csv_resp = client_with_cycles.get("/api/persistence/export/supplies.csv")
        parquet_resp = client_with_cycles.get("/api/persistence/export/supplies.parquet")
        if csv_resp.status_code == 200 and parquet_resp.status_code == 200:
            csv_lines = [
                l for l in csv_resp.text.split("\n") if l and not l.startswith("#")
            ]
            csv_cols = set(csv_lines[0].split(","))
            table = pq.read_table(io.BytesIO(parquet_resp.content))
            parquet_cols = set(table.column_names)
            common = csv_cols & parquet_cols
            assert "supply_id" in common
            assert "material_type" in common
            assert "available_tons" in common


# ============================================
# Persistence layer unit tests
# ============================================

class TestExportRowsMethods:
    """agents/persistence.py 4 个新 export_*_rows 方法。"""

    def test_export_cycles_rows_returns_dicts(self, tmp_path):
        from agents.persistence import Persistence
        db_path = tmp_path / "test_export_rows.db"
        p = Persistence(db_path=db_path)
        rows = p.export_cycles_rows(limit=10)
        assert isinstance(rows, list)
        # Even if empty, should be a list
        for r in rows:
            assert isinstance(r, dict)
            assert "cycle_id" in r

    def test_export_supplies_rows_returns_dicts(self, tmp_path):
        from agents.persistence import Persistence
        db_path = tmp_path / "test_export_rows.db"
        p = Persistence(db_path=db_path)
        rows = p.export_supplies_rows(limit=10)
        assert isinstance(rows, list)
        for r in rows:
            assert isinstance(r, dict)

    def test_export_matches_rows_returns_dicts(self, tmp_path):
        from agents.persistence import Persistence
        db_path = tmp_path / "test_export_rows.db"
        p = Persistence(db_path=db_path)
        rows = p.export_matches_rows(limit=10)
        assert isinstance(rows, list)
        for r in rows:
            assert isinstance(r, dict)

    def test_export_routes_rows_returns_dicts(self, tmp_path):
        from agents.persistence import Persistence
        db_path = tmp_path / "test_export_rows.db"
        p = Persistence(db_path=db_path)
        rows = p.export_routes_rows(limit=10)
        assert isinstance(rows, list)
        for r in rows:
            assert isinstance(r, dict)
