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
check_json_field "/api/admin/auth/status has header_formats list" GET "/api/admin/auth/status" ".protected_endpoint_count" "13"

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

check_endpoint "/api/persistence/forecast-calibration" 200 GET "/api/persistence/forecast-calibration"
check_endpoint "/api/persistence/forecast-calibration by metric" 200 GET "/api/persistence/forecast-calibration?metric=cost_sek"
check_endpoint "/api/persistence/forecast-calibration by method" 200 GET "/api/persistence/forecast-calibration?method=linear"
check_endpoint "/api/persistence/forecast-calibration invalid metric" 400 GET "/api/persistence/forecast-calibration?metric=invalid"
check_endpoint "/api/persistence/forecast-calibration invalid method" 400 GET "/api/persistence/forecast-calibration?method=invalid"
check_json_field "/api/persistence/forecast-calibration has overall" GET "/api/persistence/forecast-calibration" ".overall | type" "object"
check_json_field "/api/persistence/forecast-calibration has n_total_predictions" GET "/api/persistence/forecast-calibration" ".n_total_predictions | type" "number"
check_endpoint "/api/persistence/material-aggregates" 200 GET "/api/persistence/material-aggregates"
check_endpoint "/api/persistence/cycle-kpi-summary" 200 GET "/api/persistence/cycle-kpi-summary"
check_endpoint "/api/persistence/supply-cohort-retention" 200 GET "/api/persistence/supply-cohort-retention"
check_endpoint "/api/persistence/cohort-retention-by-period" 200 GET "/api/persistence/cohort-retention-by-period"
check_endpoint "/api/persistence/cohort-retention-by-period?period_unit=week" 200 GET "/api/persistence/cohort-retention-by-period?period_unit=week"
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

# ---- Seasonal / external ----
check_endpoint "/api/seasonal-factors" 200 GET "/api/seasonal-factors"
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