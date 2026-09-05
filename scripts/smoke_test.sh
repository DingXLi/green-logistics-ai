#!/usr/bin/env bash
# E2E smoke test script for green-logistics-ai API (iter #16)
#
# Exercises the live API surface end-to-end:
# - /health (basic liveness)
# - /api/health/deep (multi-subsystem check)
# - /api/admin/db-stats (DB inventory)
# - /api/admin/db-maintenance (VACUUM + ANALYZE)
# - /api/persistence/summary (cycle count)
# - /api/persistence/match-distance-stats
# - /api/persistence/supply-aggregates
# - /api/persistence/material-aggregates
# - /api/persistence/cycle-kpi-summary
# - /api/optimize/last
# - /api/optimize/batch?scenarios=baseline&limit=1
# - /api/facilities/distance-matrix
# - /api/seasonal/factors
# - /api/optimize/pareto?n_points=3
#
# Usage:
#   ./scripts/smoke_test.sh                              # localhost:8000
#   HF_BASE=https://lidingx-green-logistics.hf.space ./scripts/smoke_test.sh
#
# Exit codes:
#   0 = all checks passed
#   1 = at least one endpoint failed
#
# Prereqs (iter #27): check_json_field 需要 jq (apt-get install jq)。
# macOS: brew install jq
# HF Space deploy 也需要 curl + bash (默认都有)。

set -u

BASE="${HF_BASE:-http://localhost:8000}"
COOKIE_JAR="$(mktemp)"
trap 'rm -f "$COOKIE_JAR"' EXIT

# iter #33: 如果设置了 GL_ADMIN_TOKEN, 自动附加到 admin endpoint
ADMIN_HEADER_ARGS=()
if [[ -n "${GL_ADMIN_TOKEN:-}" ]]; then
    ADMIN_HEADER_ARGS=(-H "X-Admin-Token: ${GL_ADMIN_TOKEN}")
fi

# iter #27: 检查 jq 可用性
if ! command -v jq >/dev/null 2>&1; then
    echo "❌ jq not found. Install: sudo apt-get install jq  (or brew install jq on macOS)"
    exit 2
fi

# Colors (auto-disabled if no TTY)
if [[ -t 1 ]]; then
    RED='\033[0;31m'
    GREEN='\033[0;32m'
    YELLOW='\033[0;33m'
    NC='\033[0m'
else
    RED=''
    GREEN=''
    YELLOW=''
    NC=''
fi

PASS=0
FAIL=0
FAILED_ENDPOINTS=()

# check_endpoint <name> <expected_status> <method> <path> [extra args...]
check_endpoint() {
    local name="$1"
    local expected_status="$2"
    local method="$3"
    local path="$4"
    shift 4
    local url="${BASE}${path}"
    local http_code

    if [[ "$method" == "POST" ]]; then
        http_code=$(curl -s -o /dev/null -w "%{http_code}" \
            -X POST -b "$COOKIE_JAR" -c "$COOKIE_JAR" \
            "$@" "$url" 2>/dev/null)
    else
        http_code=$(curl -s -o /dev/null -w "%{http_code}" \
            -X GET -b "$COOKIE_JAR" -c "$COOKIE_JAR" \
            "$@" "$url" 2>/dev/null)
    fi

    if [[ "$http_code" == "$expected_status" ]]; then
        echo -e "  ${GREEN}✓${NC} $name [$method $path → $http_code]"
        PASS=$((PASS + 1))
    else
        echo -e "  ${RED}✗${NC} $name [$method $path → expected $expected_status, got $http_code]"
        FAIL=$((FAIL + 1))
        FAILED_ENDPOINTS+=("$name")
    fi
}

# check_json_field <name> <method> <path> <jq_filter> <expected_value> [extra args...]
check_json_field() {
    local name="$1"
    local method="$2"
    local path="$3"
    local jq_filter="$4"
    local expected="$5"
    shift 5
    local url="${BASE}${path}"
    local actual

    actual=$(curl -s -X "$method" -b "$COOKIE_JAR" -c "$COOKIE_JAR" \
        "$@" "$url" 2>/dev/null | jq -r "$jq_filter" 2>/dev/null)

    if [[ "$actual" == "$expected" ]]; then
        echo -e "  ${GREEN}✓${NC} $name [$jq_filter == $expected]"
        PASS=$((PASS + 1))
    else
        echo -e "  ${RED}✗${NC} $name [$jq_filter: expected '$expected', got '$actual']"
        FAIL=$((FAIL + 1))
        FAILED_ENDPOINTS+=("$name")
    fi
}

echo ""
echo "🦞 Green Logistics AI — E2E smoke test"
echo "   target: $BASE"
echo "   time:   $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
echo ""

# ---- Basic liveness ----
check_endpoint "/health basic" 200 GET "/health"

# ---- Deep health (multi-subsystem) ----
check_endpoint "/api/health/deep" 200 GET "/api/health/deep"

# ---- Admin / DB ----
# iter #34: GL_ADMIN_TOKEN 设置时, 所有 admin endpoint 都需要 X-Admin-Token header
check_endpoint "/api/admin/db-stats" 200 GET "/api/admin/db-stats" "${ADMIN_HEADER_ARGS[@]}"
check_endpoint "/api/admin/db-info" 200 GET "/api/admin/db-info" "${ADMIN_HEADER_ARGS[@]}"
check_endpoint "/api/admin/db-maintenance" 200 POST "/api/admin/db-maintenance" "${ADMIN_HEADER_ARGS[@]}"

# ---- iter #42: db maintenance recommendation + log ----
check_endpoint "/api/admin/db-maintenance/recommendation" 200 GET "/api/admin/db-maintenance/recommendation" "${ADMIN_HEADER_ARGS[@]}"
check_json_field "/api/admin/db-maintenance/recommendation has should_vacuum" GET "/api/admin/db-maintenance/recommendation" ".should_vacuum | type" "boolean" "${ADMIN_HEADER_ARGS[@]}"
check_json_field "/api/admin/db-maintenance/recommendation has stats" GET "/api/admin/db-maintenance/recommendation" ".stats | type" "object" "${ADMIN_HEADER_ARGS[@]}"

check_endpoint "/api/admin/db-maintenance/log" 200 GET "/api/admin/db-maintenance/log" "${ADMIN_HEADER_ARGS[@]}"
check_endpoint "/api/admin/db-maintenance/log limit=5" 200 GET "/api/admin/db-maintenance/log?limit=5" "${ADMIN_HEADER_ARGS[@]}"
check_endpoint "/api/admin/db-maintenance/log invalid limit" 400 GET "/api/admin/db-maintenance/log?limit=0" "${ADMIN_HEADER_ARGS[@]}"
check_json_field "/api/admin/db-maintenance/log has entries array" GET "/api/admin/db-maintenance/log" ".entries | type" "array" "${ADMIN_HEADER_ARGS[@]}"
check_endpoint "/api/admin/perf-stats" 200 GET "/api/admin/perf-stats" "${ADMIN_HEADER_ARGS[@]}"
# iter #27: per-endpoint error tracking field
check_json_field "/api/admin/perf-stats has total_errors field" GET "/api/admin/perf-stats" ".total_errors" "0" "${ADMIN_HEADER_ARGS[@]}"
check_endpoint "/api/admin/llm-stats" 200 GET "/api/admin/llm-stats?recent=5" "${ADMIN_HEADER_ARGS[@]}"
check_endpoint "/api/admin/db-export" 200 GET "/api/admin/db-export?table=cycles" "${ADMIN_HEADER_ARGS[@]}"
check_endpoint "/api/admin/db-export ndjson" 200 GET "/api/admin/db-export?table=cycles&fmt=ndjson" "${ADMIN_HEADER_ARGS[@]}"
check_endpoint "/api/admin/db-export parquet" 200 GET "/api/admin/db-export?table=cycles&fmt=parquet" "${ADMIN_HEADER_ARGS[@]}"
check_endpoint "/api/admin/db-export invalid table" 400 GET "/api/admin/db-export?table=bogus" "${ADMIN_HEADER_ARGS[@]}"

# ---- iter #37: seasonal perturbation CRUD (admin) ----
check_endpoint "/api/admin/seasonal-perturbations" 200 GET "/api/admin/seasonal-perturbations" "${ADMIN_HEADER_ARGS[@]}"
check_json_field "/api/admin/seasonal-perturbations has perturbations" GET "/api/admin/seasonal-perturbations" ".perturbations | type" "array" "${ADMIN_HEADER_ARGS[@]}"
# create one
create_resp=$(curl -s -X POST -b "$COOKIE_JAR" -c "$COOKIE_JAR" \
    "${ADMIN_HEADER_ARGS[@]}" \
    "${BASE}/api/admin/seasonal-perturbations?label=smoke-surge&start_sim_day=0&end_sim_day=10&material_type=concrete&multiplier=1.5")
if echo "$create_resp" | jq -e '.created.id' >/dev/null 2>&1; then
    echo -e "  ${GREEN}✓${NC} create perturbation (smoke-surge)"
    PASS=$((PASS + 1))
else
    echo -e "  ${RED}✗${NC} create perturbation (smoke-surge)"
    FAIL=$((FAIL + 1))
    FAILED_ENDPOINTS+=("create perturbation")
fi
# cleanup
curl -s -X DELETE -b "$COOKIE_JAR" -c "$COOKIE_JAR" \
    "${ADMIN_HEADER_ARGS[@]}" \
    "${BASE}/api/admin/seasonal-perturbations/$(echo $create_resp | jq -r '.created.id')" >/dev/null 2>&1

# ---- iter #36: public auth discovery endpoint (no auth required) ----
check_endpoint "/api/admin/auth/status" 200 GET "/api/admin/auth/status"
# auth_enabled value depends on whether GL_ADMIN_TOKEN is set;
# assert field type is boolean instead of hardcoded value.
check_json_field "/api/admin/auth/status has auth_enabled bool" GET "/api/admin/auth/status" ".auth_enabled | type" "boolean"
check_json_field "/api/admin/auth/status has header_formats list" GET "/api/admin/auth/status" ".protected_endpoint_count" "18"

# ---- iter #46: auth status with token preview when auth enabled ----
# When auth is enabled (GL_ADMIN_TOKEN set), token_preview should be a string
# with masked format. When disabled, it should be null. We only verify the
# field exists by checking `has("token_preview")` is true.
check_json_field "/api/admin/auth/status token_preview field exists" GET "/api/admin/auth/status" 'has("token_preview")' "true"

# ---- Persistence endpoints ----
check_endpoint "/api/persistence/summary" 200 GET "/api/persistence/summary"
check_endpoint "/api/persistence/match-distance-stats" 200 GET "/api/persistence/match-distance-stats"
check_endpoint "/api/persistence/supply-aggregates" 200 GET "/api/persistence/supply-aggregates"

# ---- iter #41: Vehicle historical stats ----
check_endpoint "/api/persistence/vehicle-stats" 200 GET "/api/persistence/vehicle-stats"
check_endpoint "/api/persistence/vehicle-stats with limit" 200 GET "/api/persistence/vehicle-stats?limit=5"
check_endpoint "/api/persistence/vehicle-stats invalid limit (0)" 400 GET "/api/persistence/vehicle-stats?limit=0"
check_endpoint "/api/persistence/vehicle-stats invalid limit (>1000)" 400 GET "/api/persistence/vehicle-stats?limit=9999"
check_json_field "/api/persistence/vehicle-stats has vehicles array" GET "/api/persistence/vehicle-stats" ".vehicles | type" "array"
check_json_field "/api/persistence/vehicle-stats has n_vehicles" GET "/api/persistence/vehicle-stats" ".n_vehicles | type" "number"

# ---- iter #42: per-material cohort retention + forecast calibration ----
check_endpoint "/api/persistence/cohort-retention-by-material" 200 GET "/api/persistence/cohort-retention-by-material"
check_json_field "/api/persistence/cohort-retention-by-material has by_material array" GET "/api/persistence/cohort-retention-by-material" ".by_material | type" "array"
check_json_field "/api/persistence/cohort-retention-by-material has n_materials" GET "/api/persistence/cohort-retention-by-material" ".n_materials | type" "number"

# ---- iter #44: runtime-config persistence + cohort cross-tab ----
check_endpoint "/api/admin/runtime-config/overrides" 200 GET "/api/admin/runtime-config/overrides" "${ADMIN_HEADER_ARGS[@]}"
check_json_field "/api/admin/runtime-config/overrides has n_overrides" GET "/api/admin/runtime-config/overrides" ".n_overrides | type" "number" "${ADMIN_HEADER_ARGS[@]}"
check_json_field "/api/admin/runtime-config/overrides has overrides array" GET "/api/admin/runtime-config/overrides" ".overrides | type" "array" "${ADMIN_HEADER_ARGS[@]}"

check_endpoint "/api/persistence/cohort-retention-crosstab" 200 GET "/api/persistence/cohort-retention-crosstab"
check_endpoint "/api/persistence/cohort-retention-crosstab with material filter" 200 GET "/api/persistence/cohort-retention-crosstab?material_type=concrete"
check_endpoint "/api/persistence/cohort-retention-crosstab with n_periods" 200 GET "/api/persistence/cohort-retention-crosstab?n_periods=2"
check_endpoint "/api/persistence/cohort-retention-crosstab invalid period_unit" 400 GET "/api/persistence/cohort-retention-crosstab?period_unit=invalid"
check_json_field "/api/persistence/cohort-retention-crosstab has materials" GET "/api/persistence/cohort-retention-crosstab" ".materials | type" "array"
check_json_field "/api/persistence/cohort-retention-crosstab has matrix" GET "/api/persistence/cohort-retention-crosstab" ".matrix | type" "array"

check_endpoint "/api/persistence/forecast-calibration" 200 GET "/api/persistence/forecast-calibration"
check_endpoint "/api/persistence/forecast-calibration by metric" 200 GET "/api/persistence/forecast-calibration?metric=cost_sek"
check_endpoint "/api/persistence/forecast-calibration by method" 200 GET "/api/persistence/forecast-calibration?method=linear"
check_endpoint "/api/persistence/forecast-calibration invalid metric" 400 GET "/api/persistence/forecast-calibration?metric=invalid"
check_endpoint "/api/persistence/forecast-calibration invalid method" 400 GET "/api/persistence/forecast-calibration?method=invalid"
check_json_field "/api/persistence/forecast-calibration has overall" GET "/api/persistence/forecast-calibration" ".overall | type" "object"
check_json_field "/api/persistence/forecast-calibration has n_total_predictions" GET "/api/persistence/forecast-calibration" ".n_total_predictions | type" "number"

# ---- iter #43: forecast calibration trend + runtime config ----
check_endpoint "/api/persistence/forecast-calibration/trend" 200 GET "/api/persistence/forecast-calibration/trend"
check_endpoint "/api/persistence/forecast-calibration/trend by metric" 200 GET "/api/persistence/forecast-calibration/trend?metric=cost_sek"
check_endpoint "/api/persistence/forecast-calibration/trend by method" 200 GET "/api/persistence/forecast-calibration/trend?method=linear"
check_endpoint "/api/persistence/forecast-calibration/trend invalid metric" 400 GET "/api/persistence/forecast-calibration/trend?metric=invalid"
check_json_field "/api/persistence/forecast-calibration/trend has trend array" GET "/api/persistence/forecast-calibration/trend" ".trend | type" "array"

check_endpoint "/api/admin/runtime-config" 200 GET "/api/admin/runtime-config" "${ADMIN_HEADER_ARGS[@]}"
check_json_field "/api/admin/runtime-config has items array" GET "/api/admin/runtime-config" ".items | type" "array" "${ADMIN_HEADER_ARGS[@]}"
check_json_field "/api/admin/runtime-config has n_keys" GET "/api/admin/runtime-config" ".n_keys | type" "number" "${ADMIN_HEADER_ARGS[@]}"
check_endpoint "/api/persistence/material-aggregates" 200 GET "/api/persistence/material-aggregates"
check_endpoint "/api/persistence/cycle-kpi-summary" 200 GET "/api/persistence/cycle-kpi-summary"
check_endpoint "/api/persistence/supply-cohort-retention" 200 GET "/api/persistence/supply-cohort-retention"
check_endpoint "/api/persistence/cohort-retention-by-period" 200 GET "/api/persistence/cohort-retention-by-period"
check_endpoint "/api/persistence/cohort-retention-by-period?period_unit=week" 200 GET "/api/persistence/cohort-retention-by-period?period_unit=week"
check_endpoint "/api/persistence/cohort-retention-by-period?material_type=concrete (iter #45)" 200 GET "/api/persistence/cohort-retention-by-period?material_type=concrete"
check_json_field "/api/persistence/cohort-retention-by-period has material_type_filter" GET "/api/persistence/cohort-retention-by-period?material_type=concrete" ".material_type_filter" "concrete"
check_endpoint "/api/persistence/forecast" 200 GET "/api/persistence/forecast?horizon=7&history_n=14"
check_endpoint "/api/persistence/forecast?metrics=cost_sek" 200 GET "/api/persistence/forecast?metrics=cost_sek&horizon=3"
check_endpoint "/api/persistence/forecast invalid horizon" 400 GET "/api/persistence/forecast?horizon=0"
# iter #28: multi-method forecast endpoint
check_endpoint "/api/persistence/forecast/multi" 200 GET "/api/persistence/forecast/multi?horizon=3"
check_endpoint "/api/persistence/forecast/multi?methods=linear" 200 GET "/api/persistence/forecast/multi?horizon=3&methods=linear"
check_endpoint "/api/persistence/forecast/multi invalid methods" 400 GET "/api/persistence/forecast/multi?horizon=3&methods=invalid"
# iter #30: forecast confidence / ensemble
check_endpoint "/api/persistence/forecast-confidence" 200 GET "/api/persistence/forecast-confidence?horizon=3"
check_endpoint "/api/persistence/forecast-confidence methods" 200 GET "/api/persistence/forecast-confidence?horizon=3&methods=linear,moving_average"
check_endpoint "/api/persistence/forecast-confidence invalid methods" 400 GET "/api/persistence/forecast-confidence?horizon=3&methods=invalid"
# iter #35: forecast method auto-resolution + persistence endpoints
check_endpoint "/api/persistence/forecast?method=auto" 200 GET "/api/persistence/forecast?method=auto&metrics=cost_sek&horizon=3"
check_endpoint "/api/persistence/forecast invalid method" 400 GET "/api/persistence/forecast?method=bogus"
# iter #36: forecast-method-prefs GET needs admin auth when token is set
check_endpoint "/api/persistence/forecast-method-prefs" 200 GET "/api/persistence/forecast-method-prefs" "${ADMIN_HEADER_ARGS[@]}"

# ---- iter #38: perturbation impact analytics (public) ----
check_endpoint "/api/persistence/perturbation-impact" 200 GET "/api/persistence/perturbation-impact?limit=5"
check_json_field "/api/persistence/perturbation-impact has cycles array" GET "/api/persistence/perturbation-impact" ".cycles | type" "array"
check_json_field "/api/persistence/perturbation-impact has summary" GET "/api/persistence/perturbation-impact" ".summary.n_cycles_total | type" "number"
# iter #46: per-material perturbation impact breakdown
check_endpoint "/api/persistence/perturbation-impact-by-material (iter #46)" 200 GET "/api/persistence/perturbation-impact-by-material"
check_json_field "/api/persistence/perturbation-impact-by-material has by_material" GET "/api/persistence/perturbation-impact-by-material" ".by_material | type" "array"
check_json_field "/api/persistence/perturbation-impact-by-material has summary" GET "/api/persistence/perturbation-impact-by-material" ".summary | type" "object"
# iter #47: anomaly detection (z-score)
check_endpoint "/api/persistence/anomalous-cycles (iter #47)" 200 GET "/api/persistence/anomalous-cycles"
check_json_field "/api/persistence/anomalous-cycles has anomalies array" GET "/api/persistence/anomalous-cycles" ".anomalies | type" "array"
check_json_field "/api/persistence/anomalous-cycles has z_threshold" GET "/api/persistence/anomalous-cycles" ".z_threshold | type" "number"
check_endpoint "/api/persistence/anomalous-cycles?z_threshold=3.5" 200 GET "/api/persistence/anomalous-cycles?z_threshold=3.5"
check_endpoint "/api/persistence/cycle-kpi-summary?last_n=7" 200 GET "/api/persistence/cycle-kpi-summary?last_n=7"
# iter #27: parquet exports (consistency with /admin/db-export)
check_endpoint "/api/persistence/export/cycles.parquet" 200 GET "/api/persistence/export/cycles.parquet?limit=10"
check_endpoint "/api/persistence/export/supplies.parquet" 200 GET "/api/persistence/export/supplies.parquet?limit=10"
check_endpoint "/api/persistence/export/matches.parquet" 200 GET "/api/persistence/export/matches.parquet?limit=10"
check_endpoint "/api/persistence/export/routes.parquet" 200 GET "/api/persistence/export/routes.parquet?limit=10"
# iter #27: json + ndjson (consistency)
check_endpoint "/api/persistence/export/cycles.json" 200 GET "/api/persistence/export/cycles.json?limit=10"
check_endpoint "/api/persistence/export/supplies.json" 200 GET "/api/persistence/export/supplies.json?limit=10"
check_endpoint "/api/persistence/export/matches.json" 200 GET "/api/persistence/export/matches.json?limit=10"
check_endpoint "/api/persistence/export/routes.json" 200 GET "/api/persistence/export/routes.json?limit=10"
check_endpoint "/api/persistence/export/cycles.ndjson" 200 GET "/api/persistence/export/cycles.ndjson?limit=10"
check_endpoint "/api/persistence/export/supplies.ndjson" 200 GET "/api/persistence/export/supplies.ndjson?limit=10"
check_endpoint "/api/persistence/export/matches.ndjson" 200 GET "/api/persistence/export/matches.ndjson?limit=10"
check_endpoint "/api/persistence/export/routes.ndjson" 200 GET "/api/persistence/export/routes.ndjson?limit=10"
# iter #47: perturbed supplies CSV export (extends supplies.csv with iter #38 cols)
check_endpoint "/api/persistence/export/perturbed-supplies.csv (iter #47)" 200 GET "/api/persistence/export/perturbed-supplies.csv?limit=10"
check_endpoint "/api/persistence/export/perturbed-supplies.csv?only_perturbed=true (iter #47)" 200 GET "/api/persistence/export/perturbed-supplies.csv?limit=10&only_perturbed=true"
# iter #48: cycle-detail CSV export (combined 5-section CSV)
check_endpoint "/api/persistence/export/cycle-detail/c1.csv (iter #48, 404 expected)" 404 GET "/api/persistence/export/cycle-detail/nonexistent.csv"
# Note: 200 check skipped because depends on having a real cycle_id; the 404 confirms endpoint is wired
# iter #48: LLM cost by decision type
check_endpoint "/api/persistence/llm-cost-by-type (iter #48)" 200 GET "/api/persistence/llm-cost-by-type"
check_json_field "/api/persistence/llm-cost-by-type has by_type" GET "/api/persistence/llm-cost-by-type" ".by_type | type" "array"
check_endpoint "/api/persistence/llm-cost-by-type?since=0&until=999 (iter #48)" 200 GET "/api/persistence/llm-cost-by-type?since_sim_day=0&until_sim_day=999"
# iter #48: per-target LLM stats
check_endpoint "/api/persistence/llm-decision-targets (iter #48)" 200 GET "/api/persistence/llm-decision-targets"
check_json_field "/api/persistence/llm-decision-targets has targets" GET "/api/persistence/llm-decision-targets" ".targets | type" "array"
# iter #49: material supply-demand balance (iter #48 leftover, finished iter #49)
check_endpoint "/api/persistence/material-supply-demand-balance (iter #49)" 200 GET "/api/persistence/material-supply-demand-balance"
check_json_field "/api/persistence/material-supply-demand-balance has by_material" GET "/api/persistence/material-supply-demand-balance" ".by_material | type" "array"
# iter #49: fleet utilization percentiles
check_endpoint "/api/persistence/fleet-utilization-summary (iter #49)" 200 GET "/api/persistence/fleet-utilization-summary"
check_json_field "/api/persistence/fleet-utilization-summary has n_cycles" GET "/api/persistence/fleet-utilization-summary" ".n_cycles | type" "number"
# iter #49: perturbation history
check_endpoint "/api/persistence/perturbation-history (iter #49)" 200 GET "/api/persistence/perturbation-history"
check_json_field "/api/persistence/perturbation-history has perturbations" GET "/api/persistence/perturbation-history" ".perturbations | type" "array"
# iter #27: WS origin allowlist metadata
check_endpoint "/api/ws/stats" 200 GET "/api/ws/stats" "${ADMIN_HEADER_ARGS[@]}"

# ---- Optimization endpoints ----
check_endpoint "/api/optimize/last" 200 GET "/api/optimize/last"
check_endpoint "/api/optimize/batch" 200 POST "/api/optimize/batch" \
    -H "Content-Type: application/json" \
    -d '{"scenarios":[{"name":"baseline","n_points":3,"time_limit_seconds":3,"co2_price":0,"use_real_roads":false}]}'
check_endpoint "/api/optimize/pareto" 200 GET "/api/optimize/pareto?n_points=3"

# ---- iter #40: simulation runner (POST /api/simulate/run) ----
# Use dry_run=true to avoid polluting production DB; 1 day is fast enough
# for smoke validation. Response shape: status / cycles_completed / kpi_summary.
check_endpoint "/api/simulate/run?days=1&dry_run=true" 200 POST "/api/simulate/run?days=1&dry_run=true"
check_json_field "/api/simulate/run has status" POST "/api/simulate/run?days=1&dry_run=true" ".status" "success"
# invalid days → 400
check_endpoint "/api/simulate/run?days=0" 400 POST "/api/simulate/run?days=0"
check_endpoint "/api/simulate/run?days=999" 400 POST "/api/simulate/run?days=999"

# ---- iter #39: carbon scenario analytics (true total cost + breakeven) ----
check_endpoint "/api/optimize/carbon-scenarios" 200 GET "/api/optimize/carbon-scenarios?carbon_prices=0,1.5,3&time_limit_seconds=2"
check_json_field "/api/optimize/carbon-scenarios has breakeven field" GET "/api/optimize/carbon-scenarios?carbon_prices=0,1.5,3&time_limit_seconds=2" ".breakeven_price_sek_per_kg | type" "number"

# ---- iter #41: Pareto-frontier sweet-spot finder ----
check_endpoint "/api/optimize/sweet-spot default weights" 200 GET "/api/optimize/sweet-spot?time_limit_seconds=1&use_real_roads=false"
check_endpoint "/api/optimize/sweet-spot pure-cost weight" 200 GET "/api/optimize/sweet-spot?weight_cost=1.0&weight_co2=0.0&time_limit_seconds=1&use_real_roads=false"
check_endpoint "/api/optimize/sweet-spot pure-co2 weight" 200 GET "/api/optimize/sweet-spot?weight_cost=0.0&weight_co2=1.0&time_limit_seconds=1&use_real_roads=false"
check_endpoint "/api/optimize/sweet-spot invalid weights (negative)" 400 GET "/api/optimize/sweet-spot?weight_cost=-0.1"
check_endpoint "/api/optimize/sweet-spot invalid time_limit" 400 GET "/api/optimize/sweet-spot?time_limit_seconds=0"
check_json_field "/api/optimize/sweet-spot has scenarios array" GET "/api/optimize/sweet-spot?time_limit_seconds=1&use_real_roads=false" ".scenarios | type" "array"
check_json_field "/api/optimize/sweet-spot has weight_cost" GET "/api/optimize/sweet-spot?time_limit_seconds=1&use_real_roads=false" ".weight_cost | type" "number"

# ---- Data / facilities ----
check_endpoint "/api/facilities/distance-matrix" 200 GET "/api/facilities/distance-matrix"
# iter #47: region profiles (SCB kommunstatistik)
check_endpoint "/api/regions (iter #47)" 200 GET "/api/regions"
check_json_field "/api/regions has n_regions" GET "/api/regions" ".n_regions | type" "number"
check_json_field "/api/regions has regions array" GET "/api/regions" ".regions | type" "array"
check_json_field "/api/regions has total_population" GET "/api/regions" ".total_population | type" "number"

# ---- Seasonal / external ----
check_endpoint "/api/seasonal-factors" 200 GET "/api/seasonal-factors"
# iter #50: SMHI weather endpoint
check_endpoint "/api/weather (iter #50, Borås default)" 200 GET "/api/weather"
check_json_field "/api/weather has source field" GET "/api/weather" ".source | type" "string"
check_endpoint "/api/weather?lat=59.3&lon=18.07 (iter #50, Stockholm)" 200 GET "/api/weather?lat=59.3293&lon=18.0686"

# iter #51: Eurostat external signals (construction / industrial / business confidence)
check_endpoint "/api/signals/external (iter #51)" 200 GET "/api/signals/external"
check_json_field "/api/signals/external has country" GET "/api/signals/external" ".country | type" "string"
check_json_field "/api/signals/external has construction" GET "/api/signals/external" ".construction | type" "object"
check_json_field "/api/signals/external has construction multiplier" GET "/api/signals/external" ".construction.multiplier | type" "number"
check_json_field "/api/signals/external has industrial" GET "/api/signals/external" ".industrial | type" "object"
check_json_field "/api/signals/external has business_confidence" GET "/api/signals/external" ".business_confidence | type" "object"
check_json_field "/api/signals/external has composite_demand_multiplier" GET "/api/signals/external" ".composite_demand_multiplier | type" "number"
check_json_field "/api/signals/external has composite_supply_multiplier" GET "/api/signals/external" ".composite_supply_multiplier | type" "number"
check_endpoint "/api/signals/external?country=SE&use_cache=true" 200 GET "/api/signals/external?country=SE&use_cache=true"
# iter #46: per-material seasonal time-series
check_endpoint "/api/persistence/seasonal-timeseries-by-material (iter #46)" 200 GET "/api/persistence/seasonal-timeseries-by-material"
check_json_field "/api/persistence/seasonal-timeseries-by-material has materials" GET "/api/persistence/seasonal-timeseries-by-material" ".materials | type" "array"
check_json_field "/api/persistence/seasonal-timeseries-by-material has matrix" GET "/api/persistence/seasonal-timeseries-by-material" ".matrix | type" "array"
# iter #28: LLM cost timeseries
check_endpoint "/api/persistence/llm-cost-timeseries" 200 GET "/api/persistence/llm-cost-timeseries"
check_endpoint "/api/persistence/llm-cost-timeseries?since_sim_day=0" 200 GET "/api/persistence/llm-cost-timeseries?since_sim_day=0"
check_endpoint "/api/persistence/llm-cost-timeseries invalid range" 400 GET "/api/persistence/llm-cost-timeseries?since_sim_day=10&until_sim_day=5"
# iter #29: LLM cost forecast
check_endpoint "/api/persistence/llm-cost-forecast" 200 GET "/api/persistence/llm-cost-forecast?horizon=3"
check_endpoint "/api/persistence/llm-cost-forecast method=ma" 200 GET "/api/persistence/llm-cost-forecast?horizon=3&method=moving_average"
check_endpoint "/api/persistence/llm-cost-forecast invalid method" 400 GET "/api/persistence/llm-cost-forecast?horizon=3&method=invalid"

# ---- JSON field validation (use python instead of jq for portability) ----
check_python_field() {
    local name="$1"
    local method="$2"
    local path="$3"
    local py_expr="$4"
    local expected="$5"
    shift 5
    local url="${BASE}${path}"
    local actual

    actual=$(curl -s -X "$method" -b "$COOKIE_JAR" -c "$COOKIE_JAR" \
        "$@" "$url" 2>/dev/null | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    result = $py_expr
    print(result if result is not None else 'null')
except Exception as e:
    print(f'ERR: {e}')
" 2>/dev/null)

    if [[ "$actual" == "$expected" ]]; then
        echo -e "  ${GREEN}✓${NC} $name [$py_expr == $expected]"
        PASS=$((PASS + 1))
    else
        echo -e "  ${RED}✗${NC} $name [$py_expr: expected '$expected', got '$actual']"
        FAIL=$((FAIL + 1))
        FAILED_ENDPOINTS+=("$name")
    fi
}

# /health should have status field == 'healthy'
check_python_field "/health has status" GET "/health" \
    "data.get('status')" "healthy"

# /api/admin/db-stats should have db_size_bytes (a number)
check_python_field "/api/admin/db-stats has db_size_bytes" GET "/api/admin/db-stats" \
    "type(data.get('db_size_bytes', None)).__name__" "int" "${ADMIN_HEADER_ARGS[@]}"

# /api/persistence/cycle-kpi-summary should have total_cycles (a number)
check_python_field "/api/persistence/cycle-kpi-summary has total_cycles" GET "/api/persistence/cycle-kpi-summary" \
    "type(data.get('total_cycles', None)).__name__" "int"

echo ""
echo "--- summary ---"
echo "  passed: $PASS"
echo "  failed: $FAIL"
if [[ $FAIL -gt 0 ]]; then
    echo ""
    echo -e "${RED}failed endpoints:${NC}"
    for ep in "${FAILED_ENDPOINTS[@]}"; do
        echo "  - $ep"
    done
    echo ""
    echo -e "${RED}❌ smoke test FAILED${NC}"
    exit 1
fi
echo ""
echo -e "${GREEN}✅ smoke test PASSED${NC}"
exit 0