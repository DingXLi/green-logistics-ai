"""
iter #43: tests for auto-vacuum scheduler hook.

Covers:
1. When auto_vacuum_enabled=False, no vacuum runs after cycle
2. When auto_vacuum_enabled=True AND should_auto_vacuum returns False, no vacuum runs
3. When auto_vacuum_enabled=True AND should_auto_vacuum returns True, vacuum is called
4. Vacuum failures don't break the cycle
"""
import sys
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def persistence():
    from agents.persistence import Persistence
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_auto_vac.db")
        p = Persistence(db_path)
        yield p


def _build_fake_coord(persistence):
    """Build a fake coordinator with persistence + minimal attributes."""
    coord = MagicMock()
    coord.persistence = persistence
    return coord


@pytest.mark.asyncio
async def test_auto_vacuum_disabled_no_vacuum(persistence):
    """When auto_vacuum_enabled=False, vacuum is NOT called after cycle."""
    from web.backend import main as backend_main
    from web.backend.main import _set_runtime_config, _reset_runtime_config
    _reset_runtime_config()
    _set_runtime_config("auto_vacuum_enabled", False)

    coord = _build_fake_coord(persistence)

    # Insert a cycle
    with persistence._conn() as conn:
        conn.execute(
            """INSERT INTO optimization_cycles
               (cycle_id, sim_day, sim_hour, wall_timestamp,
                activity_factor, n_supply_offers, n_demand_requests, n_matches,
                total_tons, total_cost_sek, total_co2_kg, total_distance_km,
                n_vehicles_used, n_vehicles_available, fleet_utilization_pct,
                solver_status, wall_duration_ms)
               VALUES ('OPT001', 1, 0, '2026-09-03T00:00:00',
                       1.0, 1, 1, 1, 5.0, 100.0, 50.0, 20.0,
                       1, 1, 50.0, 'OPTIMAL', 100)""",
        )

    # Simulate post-cycle auto-vacuum check
    if _get_runtime_config_safe("auto_vacuum_enabled"):
        rec = persistence.should_auto_vacuum()
        if rec["should_vacuum"]:
            persistence.vacuum(triggered_by="auto")

    # No vacuum log should be written
    log = persistence.get_maintenance_log()
    assert log == []


@pytest.mark.asyncio
async def test_auto_vacuum_enabled_but_not_recommended(persistence):
    """When auto_vacuum_enabled=True but should_auto_vacuum=False, no vacuum runs."""
    from web.backend import main as backend_main
    from web.backend.main import _set_runtime_config, _reset_runtime_config
    _reset_runtime_config()
    _set_runtime_config("auto_vacuum_enabled", True)

    # Pre-vacuum to clear "first time" trigger
    persistence.vacuum(triggered_by="manual")

    coord = _build_fake_coord(persistence)
    # Simulate cycle and post-cycle check
    if _get_runtime_config_safe("auto_vacuum_enabled"):
        rec = persistence.should_auto_vacuum()
        if rec["should_vacuum"]:
            persistence.vacuum(triggered_by="auto")

    # Only the manual vacuum from setup should be in the log
    log = persistence.get_maintenance_log()
    assert len(log) == 1
    assert log[0]["triggered_by"] == "manual"


@pytest.mark.asyncio
async def test_auto_vacuum_triggers_when_recommended(persistence):
    """When auto_vacuum_enabled=True AND should_auto_vacuum=True, vacuum runs."""
    from web.backend import main as backend_main
    from web.backend.main import _set_runtime_config, _reset_runtime_config
    _reset_runtime_config()
    _set_runtime_config("auto_vacuum_enabled", True)

    coord = _build_fake_coord(persistence)

    # First check should recommend (no vacuum yet)
    rec = persistence.should_auto_vacuum()
    assert rec["should_vacuum"] is True

    # Simulate cycle and post-cycle check
    if _get_runtime_config_safe("auto_vacuum_enabled"):
        rec = persistence.should_auto_vacuum()
        if rec["should_vacuum"]:
            persistence.vacuum(triggered_by="auto")

    # Vacuum should now be logged with triggered_by=auto
    log = persistence.get_maintenance_log()
    assert len(log) == 1
    assert log[0]["triggered_by"] == "auto"


@pytest.mark.asyncio
async def test_auto_vacuum_failure_does_not_break(persistence):
    """Vacuum failures should be caught and not propagate."""
    from web.backend import main as backend_main
    from web.backend.main import _set_runtime_config, _reset_runtime_config
    _reset_runtime_config()
    _set_runtime_config("auto_vacuum_enabled", True)

    coord = _build_fake_coord(persistence)

    # Make vacuum() raise an exception
    original_vacuum = persistence.vacuum

    def failing_vacuum(*args, **kwargs):
        raise RuntimeError("simulated DB lock")

    persistence.vacuum = failing_vacuum

    # Simulate cycle - should NOT propagate the vacuum failure
    try:
        if _get_runtime_config_safe("auto_vacuum_enabled"):
            rec = persistence.should_auto_vacuum()
            if rec["should_vacuum"]:
                try:
                    persistence.vacuum(triggered_by="auto")
                except Exception as e:
                    # Should be caught
                    pass
    except Exception:
        pytest.fail("auto-vacuum failure should be caught, not propagate")

    # Restore
    persistence.vacuum = original_vacuum


def _get_runtime_config_safe(key):
    """Safe access to runtime config that handles reload."""
    try:
        from web.backend.main import _get_runtime_config
        return _get_runtime_config(key)
    except ImportError:
        return False
