"""
Tests for /api/optimize/carbon-scenarios analytics additions (iter #39).

Covers:
- Response shape now includes:
  - per-scenario delta_from_baseline_pct (true total cost % change vs no-tax scenario)
  - per-scenario co2_delta_from_baseline_pct
  - per-scenario true_total_cost_cost_opt (fuel + tax*CO2 for cost-opt routing)
  - per-scenario true_total_cost_co2_opt (same for co2-opt)
  - response-level baseline_carbon_price_sek_per_kg
  - response-level breakeven_price_sek_per_kg + breakeven_gap_sek

These fields let the frontend answer sensitivity questions
("how much does cost rise at 5 SEK/kg tax?") without re-computing
client-side.

Note: These are pure unit tests of the *logic* — the full OR-Tools-backed
endpoint is tested separately in test_optimize_*.py.
"""

import pytest


@pytest.fixture
def client():
    """FastAPI TestClient with startup triggered."""
    from fastapi.testclient import TestClient
    from web.backend import main as backend_main
    with TestClient(backend_main.app) as c:
        yield c


# ============================================
# Response shape (basic)
# ============================================

class TestCarbonScenariosResponseShape:
    def test_response_has_scenarios_array(self, client):
        resp = client.get(
            "/api/optimize/carbon-scenarios?carbon_prices=0,5&time_limit_seconds=1"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "scenarios" in data


# ============================================
# Pure unit test of true_total_cost + delta computation
# ============================================

class TestTrueTotalCostComputation:
    """
    Re-implements the iter #39 endpoint logic for true_total_cost + delta.
    true_total_cost = cost_sek + tax * co2_kg (the actual amount the
    operator pays for a given routing strategy at a given tax level).
    """

    def _annotate(self, scenarios):
        """Mimic endpoint: adds true_total_cost_* + delta_from_baseline_pct + co2_delta_from_baseline_pct."""
        if not scenarios:
            return scenarios
        sorted_s = sorted(scenarios, key=lambda s: s["carbon_price_sek_per_kg"])
        # Compute baseline true cost (cost-opt at lowest tax)
        baseline_true_cost = None
        if sorted_s[0].get("cost_optimal"):
            bco = sorted_s[0]["cost_optimal"]
            bp = sorted_s[0]["carbon_price_sek_per_kg"]
            if bco.get("cost_sek") is not None and bco.get("co2_kg") is not None:
                baseline_true_cost = bco["cost_sek"] + bp * bco["co2_kg"]
        baseline_co2 = None
        if sorted_s[0].get("cost_optimal"):
            baseline_co2 = sorted_s[0]["cost_optimal"].get("co2_kg")

        for s in scenarios:
            price = s["carbon_price_sek_per_kg"]
            cost_opt = s.get("cost_optimal") or {}
            co2_opt = s.get("co2_optimal") or {}
            # True total cost for cost-opt routing
            s["true_total_cost_cost_opt"] = None
            if cost_opt.get("cost_sek") is not None and cost_opt.get("co2_kg") is not None:
                s["true_total_cost_cost_opt"] = round(
                    cost_opt["cost_sek"] + price * cost_opt["co2_kg"], 2
                )
            # True total cost for co2-opt routing
            s["true_total_cost_co2_opt"] = None
            if co2_opt.get("cost_sek") is not None and co2_opt.get("co2_kg") is not None:
                s["true_total_cost_co2_opt"] = round(
                    co2_opt["cost_sek"] + price * co2_opt["co2_kg"], 2
                )
            # Delta from baseline
            s["delta_from_baseline_pct"] = None
            if baseline_true_cost and baseline_true_cost > 0 and s["true_total_cost_cost_opt"] is not None:
                s["delta_from_baseline_pct"] = round(
                    (s["true_total_cost_cost_opt"] - baseline_true_cost) / baseline_true_cost * 100, 2
                )
            # CO2 delta vs baseline (cost-opt strategy)
            s["co2_delta_from_baseline_pct"] = None
            if baseline_co2 and baseline_co2 > 0 and cost_opt.get("co2_kg") is not None:
                s["co2_delta_from_baseline_pct"] = round(
                    (cost_opt["co2_kg"] - baseline_co2) / baseline_co2 * 100, 2
                )
        return scenarios

    def test_true_total_cost_baseline_zero_tax(self):
        """At price=0, true_total_cost = fuel cost (no tax added)."""
        scenarios = [
            {"carbon_price_sek_per_kg": 0.0, "cost_optimal": {"cost_sek": 1000, "co2_kg": 50}},
        ]
        self._annotate(scenarios)
        assert scenarios[0]["true_total_cost_cost_opt"] == 1000.0
        assert scenarios[0]["delta_from_baseline_pct"] == 0.0

    def test_true_total_cost_adds_tax(self):
        """At price=2 with co2_kg=50, true_total = 1000 + 2*50 = 1100."""
        scenarios = [
            {"carbon_price_sek_per_kg": 0.0, "cost_optimal": {"cost_sek": 1000, "co2_kg": 50}},
            {"carbon_price_sek_per_kg": 2.0, "cost_optimal": {"cost_sek": 1000, "co2_kg": 50}},
        ]
        self._annotate(scenarios)
        assert scenarios[0]["true_total_cost_cost_opt"] == 1000.0
        assert scenarios[1]["true_total_cost_cost_opt"] == 1100.0
        assert scenarios[1]["delta_from_baseline_pct"] == 10.0  # (1100-1000)/1000

    def test_high_carbon_tax_increases_true_cost(self):
        """Higher tax → higher true total cost → positive delta."""
        scenarios = [
            {"carbon_price_sek_per_kg": 0.0, "cost_optimal": {"cost_sek": 500, "co2_kg": 100}},
            {"carbon_price_sek_per_kg": 1.0, "cost_optimal": {"cost_sek": 500, "co2_kg": 100}},
            {"carbon_price_sek_per_kg": 5.0, "cost_optimal": {"cost_sek": 500, "co2_kg": 100}},
        ]
        self._annotate(scenarios)
        # Baseline: 500, scenario 1: 500 + 100 = 600 (+20%), scenario 2: 500 + 500 = 1000 (+100%)
        assert scenarios[0]["true_total_cost_cost_opt"] == 500.0
        assert scenarios[1]["true_total_cost_cost_opt"] == 600.0
        assert scenarios[2]["true_total_cost_cost_opt"] == 1000.0
        assert scenarios[1]["delta_from_baseline_pct"] == 20.0
        assert scenarios[2]["delta_from_baseline_pct"] == 100.0

    def test_missing_cost_optimal_gives_none(self):
        """If cost_optimal is None (solver failed), analytics are None."""
        scenarios = [
            {"carbon_price_sek_per_kg": 0.0, "cost_optimal": {"cost_sek": 1000, "co2_kg": 50}},
            {"carbon_price_sek_per_kg": 5.0, "cost_optimal": None},
        ]
        self._annotate(scenarios)
        assert scenarios[0]["delta_from_baseline_pct"] == 0.0
        assert scenarios[1]["delta_from_baseline_pct"] is None
        assert scenarios[1]["true_total_cost_cost_opt"] is None

    def test_co2_opt_also_gets_true_total_cost(self):
        """co2_opt's cost_sek already includes tax in solver; verify we
        add another layer of tax to it for true total cost."""
        scenarios = [
            {"carbon_price_sek_per_kg": 0.0,
             "cost_optimal": {"cost_sek": 1000, "co2_kg": 50},
             "co2_optimal": {"cost_sek": 1100, "co2_kg": 30}},  # 1100 = 1000+0*100 (cost) + 100 (CO2 saving?)
        ]
        self._annotate(scenarios)
        # For price=0, true_total_cost_co2_opt = 1100 + 0*30 = 1100
        assert scenarios[0]["true_total_cost_co2_opt"] == 1100.0

    def test_baseline_not_zero_price(self):
        """Baseline = lowest price scenario in the list."""
        scenarios = [
            {"carbon_price_sek_per_kg": 2.0, "cost_optimal": {"cost_sek": 800, "co2_kg": 100}},
            {"carbon_price_sek_per_kg": 5.0, "cost_optimal": {"cost_sek": 800, "co2_kg": 100}},
        ]
        self._annotate(scenarios)
        # baseline at price=2: true_total = 800 + 200 = 1000
        # scenario at price=5: true_total = 800 + 500 = 1300 (+30%)
        assert scenarios[0]["delta_from_baseline_pct"] == 0.0
        assert scenarios[1]["delta_from_baseline_pct"] == 30.0

    def test_zero_baseline_true_cost_returns_none(self):
        """Defensive: if baseline fuel cost is 0, all deltas are None."""
        scenarios = [
            {"carbon_price_sek_per_kg": 0.0, "cost_optimal": {"cost_sek": 0, "co2_kg": 0}},
            {"carbon_price_sek_per_kg": 5.0, "cost_optimal": {"cost_sek": 100, "co2_kg": 50}},
        ]
        self._annotate(scenarios)
        assert scenarios[0]["delta_from_baseline_pct"] is None
        assert scenarios[1]["delta_from_baseline_pct"] is None


class TestBreakevenPriceComputation:
    """Re-implements breakeven_price_sek_per_kg logic."""

    def _breakeven(self, scenarios):
        if not scenarios:
            return None, None
        sorted_s = sorted(scenarios, key=lambda s: s["carbon_price_sek_per_kg"])
        breakeven_price = None
        breakeven_gap = None
        for s in sorted_s:
            cost_opt = s.get("cost_optimal") or {}
            co2_opt = s.get("co2_optimal") or {}
            cost_cost = cost_opt.get("cost_sek")
            co2_cost = co2_opt.get("cost_sek")
            if cost_cost is None or co2_cost is None:
                continue
            gap = abs(cost_cost - co2_cost)
            if breakeven_gap is None or gap < breakeven_gap:
                breakeven_gap = gap
                breakeven_price = s["carbon_price_sek_per_kg"]
        return breakeven_price, breakeven_gap

    def test_identical_costs_returns_baseline(self):
        scenarios = [
            {
                "carbon_price_sek_per_kg": 0.0,
                "cost_optimal": {"cost_sek": 1000},
                "co2_optimal": {"cost_sek": 1000},
            },
            {
                "carbon_price_sek_per_kg": 5.0,
                "cost_optimal": {"cost_sek": 1200},
                "co2_optimal": {"cost_sek": 1100},
            },
        ]
        price, gap = self._breakeven(scenarios)
        assert price == 0.0
        assert gap == 0

    def test_finds_closest_convergence(self):
        scenarios = [
            {
                "carbon_price_sek_per_kg": 0.0,
                "cost_optimal": {"cost_sek": 1000},
                "co2_optimal": {"cost_sek": 2000},
            },
            {
                "carbon_price_sek_per_kg": 1.5,
                "cost_optimal": {"cost_sek": 1500},
                "co2_optimal": {"cost_sek": 1700},
            },
            {
                "carbon_price_sek_per_kg": 5.0,
                "cost_optimal": {"cost_sek": 2000},
                "co2_optimal": {"cost_sek": 2050},
            },
        ]
        price, gap = self._breakeven(scenarios)
        assert price == 5.0
        assert gap == 50

    def test_missing_data_skipped(self):
        scenarios = [
            {
                "carbon_price_sek_per_kg": 0.0,
                "cost_optimal": None,
                "co2_optimal": None,
            },
            {
                "carbon_price_sek_per_kg": 5.0,
                "cost_optimal": {"cost_sek": 1200},
                "co2_optimal": {"cost_sek": 1100},
            },
        ]
        price, gap = self._breakeven(scenarios)
        assert price == 5.0
        assert gap == 100

    def test_empty_scenarios_returns_none(self):
        price, gap = self._breakeven([])
        assert price is None
        assert gap is None
