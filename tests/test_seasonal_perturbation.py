"""
Tests for seasonal perturbation feature (iter #37).

Covers:
- data/seasonal_perturbation.py
  - SeasonalPerturbation dataclass + is_active_at()
  - apply_perturbations() — multiplicative semantics, wildcards, clamping
  - validate_perturbation() — bounds checks
- agents/persistence.py CRUD
  - add_seasonal_perturbation / list / get_active / delete / deactivate / clear
"""

import pytest

from data.seasonal_perturbation import (
    SeasonalPerturbation,
    apply_perturbations,
    validate_perturbation,
    MIN_MULTIPLIER,
    MAX_MULTIPLIER,
    ALL_MATERIALS,
)


def _make(label="Test", start=0, end=10000, mat="concrete", mult=1.5, active=True):
    """Helper to construct a perturbation in-memory (no DB needed).
    Default window [0, 10000] covers all test sim_days unless overridden."""
    return SeasonalPerturbation(
        id=None,
        label=label,
        start_sim_day=start,
        end_sim_day=end,
        material_type=mat,
        multiplier=mult,
        created_at="2026-09-02T00:00:00",
        active=active,
    )


# ============================================
# SeasonalPerturbation dataclass
# ============================================

class TestSeasonalPerturbation:
    def test_to_dict_roundtrip(self):
        p = _make(label="X", start=0, end=5, mat="metal_scrap", mult=0.8)
        d = p.to_dict()
        assert d["label"] == "X"
        assert d["start_sim_day"] == 0
        assert d["end_sim_day"] == 5
        assert d["material_type"] == "metal_scrap"
        assert d["multiplier"] == 0.8
        assert d["active"] is True

    def test_is_active_at_within_window(self):
        p = _make(start=10, end=20)
        assert p.is_active_at(10) is True   # inclusive lower
        assert p.is_active_at(15) is True
        assert p.is_active_at(20) is True   # inclusive upper

    def test_is_active_at_outside_window(self):
        p = _make(start=10, end=20)
        assert p.is_active_at(9) is False
        assert p.is_active_at(21) is False
        assert p.is_active_at(0) is False
        assert p.is_active_at(1000) is False

    def test_is_active_at_inactive(self):
        p = _make(start=10, end=20, active=False)
        assert p.is_active_at(15) is False   # even within window


# ============================================
# apply_perturbations — core logic
# ============================================

class TestApplyPerturbations:
    def test_no_perturbations_returns_base(self):
        assert apply_perturbations(1.0, "concrete", 5, []) == 1.0
        assert apply_perturbations(0.7, "metal_scrap", 100, []) == 0.7

    def test_single_perturbation_multiplies(self):
        p = _make(start=0, end=100, mult=1.5)
        result = apply_perturbations(1.0, "concrete", 50, [p])
        assert result == pytest.approx(1.5)

    def test_material_specific_match(self):
        p = _make(mat="concrete", mult=2.0)
        result = apply_perturbations(1.0, "concrete", 5, [p])
        assert result == pytest.approx(2.0)

    def test_material_specific_no_match(self):
        p = _make(mat="concrete", mult=2.0)
        # Different material → no perturbation applied
        result = apply_perturbations(1.0, "metal_scrap", 5, [p])
        assert result == 1.0

    def test_wildcard_material_matches_all(self):
        p = _make(mat=ALL_MATERIALS, mult=1.25)
        for mat in ["concrete", "metal_scrap", "wood_waste", "plastic"]:
            assert apply_perturbations(1.0, mat, 5, [p]) == pytest.approx(1.25)

    def test_window_miss_returns_base(self):
        p = _make(start=100, end=200, mult=2.0)
        # sim_day=50 not in window
        result = apply_perturbations(1.0, "concrete", 50, [p])
        assert result == 1.0

    def test_multiple_overlapping_perturbations_multiply(self):
        # -30% weather × +20% holiday = 0.84
        p1 = _make(label="weather", mat=ALL_MATERIALS, mult=0.7)
        p2 = _make(label="holiday", mat=ALL_MATERIALS, mult=1.2)
        result = apply_perturbations(1.0, "concrete", 5, [p1, p2])
        assert result == pytest.approx(0.84)

    def test_mixed_material_specific_and_wildcard(self):
        # Wildcard 1.5 + concrete-specific 0.5 = 0.75
        p1 = _make(mat=ALL_MATERIALS, mult=1.5)
        p2 = _make(mat="concrete", mult=0.5)
        assert apply_perturbations(1.0, "concrete", 5, [p1, p2]) == pytest.approx(0.75)
        # Wildcard still applies to other materials
        assert apply_perturbations(1.0, "metal_scrap", 5, [p1, p2]) == pytest.approx(1.5)

    def test_inactive_perturbations_ignored(self):
        p = _make(start=0, end=100, mult=2.0, active=False)
        result = apply_perturbations(1.0, "concrete", 50, [p])
        assert result == 1.0

    def test_clamp_to_min(self):
        # 0.5 * 0.5 * 0.5 * 0.5 = 0.0625 (below MIN 0.1) → clamped
        ps = [_make(label=f"p{i}", mult=0.5) for i in range(4)]
        result = apply_perturbations(1.0, "concrete", 5, ps)
        assert result == MIN_MULTIPLIER

    def test_clamp_to_max(self):
        # 2.5 * 2.5 = 6.25 (above MAX 3.0) → clamped
        ps = [_make(label="a", mult=2.5), _make(label="b", mult=2.5)]
        result = apply_perturbations(1.0, "concrete", 5, ps)
        assert result == MAX_MULTIPLIER


# ============================================
# validate_perturbation — bounds
# ============================================

class TestValidatePerturbation:
    def test_valid_returns_none(self):
        assert validate_perturbation("Christmas surge", 0, 30, "paper_cardboard", 1.5) is None
        assert validate_perturbation("All materials cut", 10, 20, "*", 0.5) is None

    def test_empty_label(self):
        assert validate_perturbation("", 0, 5, "concrete", 1.0) is not None
        assert validate_perturbation("   ", 0, 5, "concrete", 1.0) is not None

    def test_label_too_long(self):
        long_label = "x" * 101
        assert validate_perturbation(long_label, 0, 5, "concrete", 1.0) is not None

    def test_negative_sim_day(self):
        assert validate_perturbation("x", -1, 5, "concrete", 1.0) is not None
        assert validate_perturbation("x", 0, -5, "concrete", 1.0) is not None

    def test_end_before_start(self):
        assert validate_perturbation("x", 20, 10, "concrete", 1.0) is not None

    def test_empty_material(self):
        assert validate_perturbation("x", 0, 5, "", 1.0) is not None

    def test_multiplier_out_of_range(self):
        assert validate_perturbation("x", 0, 5, "concrete", 0.05) is not None
        assert validate_perturbation("x", 0, 5, "concrete", 5.0) is not None
        # Boundaries should be OK
        assert validate_perturbation("x", 0, 5, "concrete", MIN_MULTIPLIER) is None
        assert validate_perturbation("x", 0, 5, "concrete", MAX_MULTIPLIER) is None


# ============================================
# Persistence CRUD
# ============================================

class TestPersistenceSeasonalPerturbations:
    """Persistence layer CRUD for seasonal_perturbations table."""

    def _fresh_persistence(self, tmp_path):
        from agents.persistence import Persistence
        db = tmp_path / "test_perturb.db"
        p = Persistence(str(db))
        return p, db

    def test_add_returns_persisted_row(self, tmp_path):
        p, _ = self._fresh_persistence(tmp_path)
        result = p.add_seasonal_perturbation(
            label="Test perturbation",
            start_sim_day=10,
            end_sim_day=20,
            material_type="concrete",
            multiplier=1.5,
        )
        assert result["id"] is not None
        assert result["label"] == "Test perturbation"
        assert result["active"] is True
        assert result["multiplier"] == 1.5
        assert "created_at" in result

    def test_add_invalid_raises(self, tmp_path):
        p, _ = self._fresh_persistence(tmp_path)
        # Negative end_sim_day
        with pytest.raises(ValueError):
            p.add_seasonal_perturbation("x", 0, -1, "concrete", 1.0)
        # Out of range multiplier
        with pytest.raises(ValueError):
            p.add_seasonal_perturbation("x", 0, 5, "concrete", 99.0)

    def test_list_active_only_excludes_deactivated(self, tmp_path):
        p, _ = self._fresh_persistence(tmp_path)
        p.add_seasonal_perturbation("a", 0, 5, "concrete", 1.0)
        p.add_seasonal_perturbation("b", 10, 15, "metal_scrap", 0.8)
        # Deactivate one
        results = p.list_seasonal_perturbations(active_only=False)
        first_id = results[0]["id"]
        p.deactivate_seasonal_perturbation(first_id)
        # Now active_only=True should return only 1
        assert len(p.list_seasonal_perturbations(active_only=True)) == 1
        # active_only=False should return both
        assert len(p.list_seasonal_perturbations(active_only=False)) == 2

    def test_get_active_perturbations_filters_by_sim_day(self, tmp_path):
        p, _ = self._fresh_persistence(tmp_path)
        p.add_seasonal_perturbation("early", 0, 5, "concrete", 1.5)
        p.add_seasonal_perturbation("late", 10, 15, "concrete", 0.5)
        # sim_day=3 → only "early"
        active = p.get_active_perturbations(3)
        assert len(active) == 1
        assert active[0]["label"] == "early"
        # sim_day=12 → only "late"
        active = p.get_active_perturbations(12)
        assert len(active) == 1
        assert active[0]["label"] == "late"
        # sim_day=100 → none
        active = p.get_active_perturbations(100)
        assert len(active) == 0

    def test_get_active_filters_by_material(self, tmp_path):
        p, _ = self._fresh_persistence(tmp_path)
        p.add_seasonal_perturbation("concrete only", 0, 100, "concrete", 1.5)
        p.add_seasonal_perturbation("metal only", 0, 100, "metal_scrap", 0.5)
        p.add_seasonal_perturbation("all", 0, 100, "*", 1.2)
        # Query with material="concrete" → should match concrete + wildcard (not metal)
        active = p.get_active_perturbations(50, material_type="concrete")
        labels = sorted(a["label"] for a in active)
        assert labels == ["all", "concrete only"]
        # Query with metal_scrap → should match metal + wildcard
        active = p.get_active_perturbations(50, material_type="metal_scrap")
        labels = sorted(a["label"] for a in active)
        assert labels == ["all", "metal only"]

    def test_delete_returns_true_when_exists(self, tmp_path):
        p, _ = self._fresh_persistence(tmp_path)
        result = p.add_seasonal_perturbation("to-delete", 0, 5, "concrete", 1.0)
        assert p.delete_seasonal_perturbation(result["id"]) is True
        assert p.delete_seasonal_perturbation(result["id"]) is False  # gone now

    def test_clear_returns_rowcount(self, tmp_path):
        p, _ = self._fresh_persistence(tmp_path)
        p.add_seasonal_perturbation("a", 0, 5, "concrete", 1.0)
        p.add_seasonal_perturbation("b", 10, 15, "metal_scrap", 0.5)
        p.add_seasonal_perturbation("c", 20, 25, "*", 1.2)
        cleared = p.clear_seasonal_perturbations()
        assert cleared == 3
        assert len(p.list_seasonal_perturbations(active_only=False)) == 0

    def test_end_to_end_apply_via_persistence(self, tmp_path):
        """Real persistence → apply_perturbations round trip."""
        from data.seasonal_perturbation import apply_perturbations, SeasonalPerturbation
        p, _ = self._fresh_persistence(tmp_path)
        p.add_seasonal_perturbation("weather", 0, 100, "*", 0.7)
        # Get active perturbations as dicts, convert to dataclass for apply
        rows = p.get_active_perturbations(50)
        objs = [
            SeasonalPerturbation(
                id=r["id"], label=r["label"],
                start_sim_day=r["start_sim_day"],
                end_sim_day=r["end_sim_day"],
                material_type=r["material_type"],
                multiplier=r["multiplier"],
                active=bool(r["active"]),
                created_at=r["created_at"],
            )
            for r in rows
        ]
        result = apply_perturbations(1.0, "concrete", 50, objs)
        assert result == pytest.approx(0.7)
