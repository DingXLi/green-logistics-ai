"""
iter #44: tests for runtime config persistence + apply-batch.

Covers:
1. Persistence methods (pure logic):
   - save_runtime_config
   - load_runtime_config
   - delete_runtime_config
   - list_runtime_config_overrides
2. /api/admin/runtime-config POST with persist=true
3. /api/admin/runtime-config/apply batch endpoint
4. /api/admin/runtime-config/overrides endpoint
5. /api/admin/runtime-config/reset with clear_persisted=true
"""
import os
import sys
import tempfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def persistence():
    from agents.persistence import Persistence
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_rc_persist.db")
        p = Persistence(db_path)
        yield p


# ---------------------------------------------------------------------------
# Pure persistence tests
# ---------------------------------------------------------------------------


def test_save_and_load_runtime_config(persistence):
    persistence.save_runtime_config("default_carbon_price_sek_per_kg", 2.5)
    overrides = persistence.load_runtime_config()
    assert "default_carbon_price_sek_per_kg" in overrides
    assert overrides["default_carbon_price_sek_per_kg"] == 2.5


def test_save_overwrites_existing(persistence):
    persistence.save_runtime_config("default_forecast_horizon", 14)
    persistence.save_runtime_config("default_forecast_horizon", 21)
    overrides = persistence.load_runtime_config()
    assert overrides["default_forecast_horizon"] == 21


def test_save_bool_value(persistence):
    persistence.save_runtime_config("auto_vacuum_enabled", True)
    overrides = persistence.load_runtime_config()
    assert overrides["auto_vacuum_enabled"] is True


def test_save_string_value(persistence):
    persistence.save_runtime_config("perturbation_max_multiplier", 5.0)
    overrides = persistence.load_runtime_config()
    assert overrides["perturbation_max_multiplier"] == 5.0


def test_load_empty(persistence):
    overrides = persistence.load_runtime_config()
    assert overrides == {}


def test_load_multiple(persistence):
    persistence.save_runtime_config("default_carbon_price_sek_per_kg", 2.5)
    persistence.save_runtime_config("default_forecast_horizon", 21)
    persistence.save_runtime_config("auto_vacuum_enabled", True)
    overrides = persistence.load_runtime_config()
    assert len(overrides) == 3
    assert overrides["default_carbon_price_sek_per_kg"] == 2.5
    assert overrides["default_forecast_horizon"] == 21
    assert overrides["auto_vacuum_enabled"] is True


def test_delete_runtime_config(persistence):
    persistence.save_runtime_config("default_forecast_horizon", 21)
    assert persistence.delete_runtime_config("default_forecast_horizon") is True
    assert "default_forecast_horizon" not in persistence.load_runtime_config()


def test_delete_nonexistent(persistence):
    assert persistence.delete_runtime_config("not_a_key") is False


def test_list_runtime_config_overrides(persistence):
    persistence.save_runtime_config("default_forecast_horizon", 21)
    persistence.save_runtime_config("auto_vacuum_enabled", True)
    rows = persistence.list_runtime_config_overrides()
    assert len(rows) == 2
    # Sorted by key
    assert rows[0]["key"] == "auto_vacuum_enabled"
    assert rows[1]["key"] == "default_forecast_horizon"
    # Has parse fields
    for r in rows:
        assert "value" in r
        assert "parsed_value" in r
        assert "parse_ok" in r
        assert "updated_at" in r
        assert r["parse_ok"] is True


def test_list_handles_bad_json_gracefully(persistence):
    """If a row has malformed JSON, list should still work and mark parse_ok=False."""
    persistence.save_runtime_config("good_key", 42)
    # Directly insert a bad value
    with persistence._conn() as conn:
        conn.execute(
            "INSERT INTO runtime_config (key, value, updated_at) VALUES (?, ?, ?)",
            ("bad_key", "not_valid_json{", "2026-09-03T00:00:00"),
        )
    rows = persistence.list_runtime_config_overrides()
    keys = [r["key"] for r in rows]
    assert "good_key" in keys
    assert "bad_key" in keys
    bad_row = next(r for r in rows if r["key"] == "bad_key")
    assert bad_row["parse_ok"] is False


def test_load_handles_bad_json_gracefully(persistence):
    """Bad JSON rows should be skipped in load_runtime_config (with warning)."""
    persistence.save_runtime_config("good_key", 42)
    with persistence._conn() as conn:
        conn.execute(
            "INSERT INTO runtime_config (key, value, updated_at) VALUES (?, ?, ?)",
            ("bad_key", "not_valid_json{", "2026-09-03T00:00:00"),
        )
    overrides = persistence.load_runtime_config()
    assert "good_key" in overrides
    assert "bad_key" not in overrides  # skipped


# ---------------------------------------------------------------------------
# HTTP endpoint tests
# ---------------------------------------------------------------------------


def test_post_runtime_config_with_persist(monkeypatch):
    from web.backend import main as backend_main
    from fastapi.testclient import TestClient

    backend_main._reset_runtime_config()
    client = TestClient(backend_main.app)

    class _FakePersistence:
        def save_runtime_config(self, key, value):
            return {"key": key, "value": value, "updated_at": "2026-09-03T00:00:00"}

    class _FakeCoord:
        persistence = _FakePersistence()

    monkeypatch.setattr(backend_main, "coordinator", _FakeCoord())
    resp = client.post(
        "/api/admin/runtime-config",
        params={"key": "default_carbon_price_sek_per_kg", "value": "3.0", "persist": "true"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["applied"] is True
    assert data["persisted"] is True
    assert data["value"] == 3.0
    assert data["updated_at"] is not None


def test_post_runtime_config_without_persist(monkeypatch):
    from web.backend import main as backend_main
    from fastapi.testclient import TestClient

    backend_main._reset_runtime_config()
    client = TestClient(backend_main.app)
    resp = client.post(
        "/api/admin/runtime-config",
        params={"key": "default_carbon_price_sek_per_kg", "value": "3.0"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["applied"] is True
    assert data["persisted"] is False
    assert data["updated_at"] is None


def test_post_runtime_config_persist_no_coordinator(monkeypatch):
    from web.backend import main as backend_main
    from fastapi.testclient import TestClient

    backend_main._reset_runtime_config()
    monkeypatch.setattr(backend_main, "coordinator", None)
    client = TestClient(backend_main.app)
    resp = client.post(
        "/api/admin/runtime-config",
        params={"key": "default_carbon_price_sek_per_kg", "value": "3.0", "persist": "true"},
    )
    assert resp.status_code == 503


def test_apply_batch_valid(monkeypatch):
    from web.backend import main as backend_main
    from fastapi.testclient import TestClient
    import json

    backend_main._reset_runtime_config()
    client = TestClient(backend_main.app)

    class _FakePersistence:
        def save_runtime_config(self, key, value):
            return {"key": key, "value": value, "updated_at": "2026-09-03T00:00:00"}

    class _FakeCoord:
        persistence = _FakePersistence()

    monkeypatch.setattr(backend_main, "coordinator", _FakeCoord())
    updates = [
        {"key": "default_carbon_price_sek_per_kg", "value": "3.0"},
        {"key": "default_forecast_horizon", "value": "21"},
        {"key": "auto_vacuum_enabled", "value": "true"},
    ]
    resp = client.post(
        "/api/admin/runtime-config/apply",
        params={"updates": json.dumps(updates), "persist": "true"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["n_applied"] == 3
    assert data["persisted"] is True
    keys = [a["key"] for a in data["applied"]]
    assert "default_carbon_price_sek_per_kg" in keys
    assert "default_forecast_horizon" in keys
    assert "auto_vacuum_enabled" in keys


def test_apply_batch_invalid_json(monkeypatch):
    from web.backend import main as backend_main
    from fastapi.testclient import TestClient

    backend_main._reset_runtime_config()
    client = TestClient(backend_main.app)
    resp = client.post(
        "/api/admin/runtime-config/apply",
        params={"updates": "not_valid_json"},
    )
    assert resp.status_code == 400


def test_apply_batch_atomic_no_partial(monkeypatch):
    """If one entry is invalid, NO entries should be applied."""
    from web.backend import main as backend_main
    from fastapi.testclient import TestClient
    import json

    backend_main._reset_runtime_config()
    client = TestClient(backend_main.app)
    updates = [
        {"key": "default_forecast_horizon", "value": "21"},  # valid
        {"key": "not_a_real_key", "value": "5"},  # invalid key
    ]
    resp = client.post(
        "/api/admin/runtime-config/apply",
        params={"updates": json.dumps(updates)},
    )
    assert resp.status_code == 400
    # Verify the valid one was NOT applied (atomic)
    assert backend_main._get_runtime_config("default_forecast_horizon") == 7  # default


def test_apply_batch_too_many(monkeypatch):
    from web.backend import main as backend_main
    from fastapi.testclient import TestClient
    import json

    backend_main._reset_runtime_config()
    client = TestClient(backend_main.app)
    updates = [{"key": "default_forecast_horizon", "value": str(i)} for i in range(51)]
    resp = client.post(
        "/api/admin/runtime-config/apply",
        params={"updates": json.dumps(updates)},
    )
    assert resp.status_code == 400


def test_apply_batch_empty(monkeypatch):
    from web.backend import main as backend_main
    from fastapi.testclient import TestClient
    import json

    backend_main._reset_runtime_config()
    client = TestClient(backend_main.app)
    resp = client.post(
        "/api/admin/runtime-config/apply",
        params={"updates": json.dumps([])},
    )
    assert resp.status_code == 400


def test_overrides_endpoint(monkeypatch):
    from web.backend import main as backend_main
    from fastapi.testclient import TestClient

    backend_main._reset_runtime_config()
    client = TestClient(backend_main.app)

    fake_overrides = [
        {"key": "auto_vacuum_enabled", "value": "true", "parsed_value": True, "parse_ok": True, "updated_at": "2026-09-03T00:00:00"},
        {"key": "default_forecast_horizon", "value": "21", "parsed_value": 21, "parse_ok": True, "updated_at": "2026-09-03T00:00:00"},
    ]

    class _FakePersistence:
        def list_runtime_config_overrides(self):
            return fake_overrides

    class _FakeCoord:
        persistence = _FakePersistence()

    monkeypatch.setattr(backend_main, "coordinator", _FakeCoord())
    resp = client.get("/api/admin/runtime-config/overrides")
    assert resp.status_code == 200
    data = resp.json()
    assert data["n_overrides"] == 2
    assert len(data["overrides"]) == 2
    assert data["overrides"][0]["key"] == "auto_vacuum_enabled"


def test_overrides_no_coordinator(monkeypatch):
    from web.backend import main as backend_main
    from fastapi.testclient import TestClient

    backend_main._reset_runtime_config()
    monkeypatch.setattr(backend_main, "coordinator", None)
    client = TestClient(backend_main.app)
    resp = client.get("/api/admin/runtime-config/overrides")
    assert resp.status_code == 503


def test_reset_with_clear_persisted(monkeypatch):
    from web.backend import main as backend_main
    from fastapi.testclient import TestClient
    import tempfile
    from agents.persistence import Persistence

    backend_main._reset_runtime_config()
    # Use real Persistence so _conn() is a proper context manager
    with tempfile.TemporaryDirectory() as tmpdir:
        p = Persistence(os.path.join(tmpdir, "test.db"))
        # Pre-populate
        p.save_runtime_config("default_forecast_horizon", 21)
        p.save_runtime_config("auto_vacuum_enabled", True)
        p.save_runtime_config("default_carbon_price_sek_per_kg", 2.5)

        class _FakeCoord:
            persistence = p

        monkeypatch.setattr(backend_main, "coordinator", _FakeCoord())
        client = TestClient(backend_main.app)
        resp = client.post(
            "/api/admin/runtime-config/reset",
            params={"clear_persisted": "true"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["reset"] is True
        assert data["deleted_persisted"] == 3
        # Verify rows are gone
        assert p.load_runtime_config() == {}


def test_reset_without_clear(monkeypatch):
    from web.backend import main as backend_main
    from fastapi.testclient import TestClient

    backend_main._reset_runtime_config()
    client = TestClient(backend_main.app)
    resp = client.post("/api/admin/runtime-config/reset")
    assert resp.status_code == 200
    data = resp.json()
    assert data["reset"] is True
    assert data["deleted_persisted"] == 0
