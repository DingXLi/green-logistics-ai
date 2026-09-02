"""
季节性扰动 (Seasonal Perturbation) — iter #37

Background:
- ``data/seasonal_adjuster.py`` ships a static 12-month × material-type table
  sourced from Avfall Sverige 2023. That captures the *baseline* seasonal
  pattern (concrete peaks in summer, etc.) but does not let operators model
  real-world shocks:
    * Holiday spikes (e.g. Christmas paper+cardboard surge)
    * Weather events (e.g. -30% concrete supply during a -15 °C week)
    * Strike / plant shutdown
    * Regulatory changes (e.g. +25% metal recovery quota)
- This module adds a SQLite-backed perturbation registry that overlays the
  static SEASONAL_FACTORS for a configurable sim_day window.

Design (iter #37):
- Perturbations are scoped to:
    - ``start_sim_day`` / ``end_sim_day`` (inclusive window)
    - ``material_type`` (or ``"*"`` for all materials)
    - ``multiplier`` (applied to base seasonal factor; 1.0 = no change)
- Multiple perturbations may overlap. When several apply to the same
  (sim_day, material), they *multiply* — keeps semantics predictable
  (e.g. -30% weather × +20% holiday = 1.0 × 0.7 × 1.2 = 0.84)
- Application point: coordinator calls ``apply_perturbations(base, mat, day, persistence)``
  right after ``get_supply_multiplier()`` / ``get_demand_multiplier()``.
  This keeps ``seasonal_adjuster`` pure / DB-free (no new module imports).
- Admin CRUD via ``/api/admin/seasonal-perturbations`` (12th protected endpoint).

DB schema is owned by ``agents/persistence.py`` (single source of truth,
applied via SCHEMA_SQL at init).
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Dict, Any, List, Optional

from loguru import logger


# A perturbation multiplies the base seasonal factor by ``multiplier``.
# 1.0 = neutral, >1 = boost, <1 = dampen.
# Allowed range chosen to match SEASONAL_FACTORS bounds (typical 0.3-1.5)
# plus generous headroom for shock simulations.
MIN_MULTIPLIER = 0.1   # hard floor (-90%)
MAX_MULTIPLIER = 3.0   # hard ceiling (+200%)


# Wildcard material means "apply to all material types".
ALL_MATERIALS = "*"


@dataclass
class SeasonalPerturbation:
    """A single perturbation rule."""

    id: Optional[int]
    label: str
    start_sim_day: int
    end_sim_day: int
    material_type: str
    multiplier: float
    created_at: str  # ISO 8601
    active: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def is_active_at(self, sim_day: int) -> bool:
        if not self.active:
            return False
        return self.start_sim_day <= sim_day <= self.end_sim_day


def apply_perturbations(
    base_factor: float,
    material_type: str,
    sim_day: int,
    active_perturbations: List[SeasonalPerturbation],
) -> float:
    """
    Multiply ``base_factor`` by every active perturbation matching the
    ``(sim_day, material_type)`` pair.

    Multiplicative semantics:
      * All-materials perturbation (material="*") applies regardless of material.
      * Material-specific perturbation only applies to its material.
      * When several match, they multiply (e.g. -30% × +20% = 0.84).

    Args:
        base_factor: result of ``seasonal_adjuster.get_supply_multiplier``
                     / ``get_demand_multiplier`` (typically 0.3 - 1.5).
        material_type: e.g. "concrete", "metal_scrap", or "*" wildcard.
        sim_day: 0-indexed simulation day.
        active_perturbations: list of pre-fetched perturbations
                              (coordinator passes the result of one query
                              per cycle to avoid N+1).

    Returns:
        Perturbed factor, clamped to ``[MIN_MULTIPLIER, MAX_MULTIPLIER]``.
    """
    if not active_perturbations:
        return base_factor
    perturbed = base_factor
    for p in active_perturbations:
        if not p.is_active_at(sim_day):
            continue
        # Match by material: "*" wildcard matches everything, else exact match.
        if p.material_type != ALL_MATERIALS and p.material_type != material_type:
            continue
        perturbed *= p.multiplier
        logger.debug(
            f"[perturb] sim_day={sim_day} mat={material_type} "
            f"label='{p.label}' x{p.multiplier} -> {perturbed:.3f}"
        )
    # Clamp to safe range so a misconfigured 10x shock doesn't blow up
    # downstream aggregations (cost / utilization / forecast R²).
    if perturbed < MIN_MULTIPLIER:
        return MIN_MULTIPLIER
    if perturbed > MAX_MULTIPLIER:
        return MAX_MULTIPLIER
    return perturbed


def validate_perturbation(
    label: str,
    start_sim_day: int,
    end_sim_day: int,
    material_type: str,
    multiplier: float,
) -> Optional[str]:
    """
    Return ``None`` if valid, else an error message string.
    Called by both the API layer and tests.
    """
    if not label or not label.strip():
        return "label must not be empty"
    if len(label) > 100:
        return "label must be <= 100 chars"
    if start_sim_day < 0 or end_sim_day < 0:
        return "sim_day must be >= 0"
    if end_sim_day < start_sim_day:
        return f"end_sim_day ({end_sim_day}) must be >= start_sim_day ({start_sim_day})"
    if not material_type:
        return "material_type must not be empty (use '*' for all)"
    if multiplier < MIN_MULTIPLIER or multiplier > MAX_MULTIPLIER:
        return f"multiplier {multiplier} out of range [{MIN_MULTIPLIER}, {MAX_MULTIPLIER}]"
    return None
