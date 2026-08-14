#!/usr/bin/env bash
# S5-1: lint + test CI gate — L12 enforced
# Enforced by pre-push and session-close gates: cd ~/kai-system && bash scripts/ci.sh
# All services must pass ruff + seed suite before commit is valid.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

python3 scripts/green_baseline.py

SERVICES=(
    "kai-worker-api:kai-worker-api"
    "kai-council-api:kai-council-api"
    "kai-orchestrator:kai-orchestrator"
)

FAIL=0

run_ruff_gate() {
    local svc="$1"
    local container="$2"
    local report
    report="$(mktemp)"
    docker exec "$container" ruff check /app --no-cache --output-format=json >"$report" || true
    python3 scripts/check_ruff_baseline.py \
        --baseline scripts/ruff-baseline.json \
        --report "$svc:$report" || FAIL=1
    rm -f "$report"
}

for entry in "${SERVICES[@]}"; do
    svc="${entry%%:*}"
    container="${entry##*:}"

    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  ruff check: $svc"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    run_ruff_gate "$svc" "$container"

    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  pytest: $svc"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    docker exec "$container" pytest /app/tests/ -v --tb=short \
        -m "not destructive and not whole_repo" || FAIL=1
done

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  whole-repo guards"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
python3 -m pytest -v --tb=short -m whole_repo \
    kai-worker-api/tests/test_internal_auth_guard.py \
    scripts/tests/test_ruff_baseline.py \
    scripts/tests/test_green_baseline.py || FAIL=1
PYTHONPATH="$ROOT/kai-council-api" python3 -m pytest -v --tb=short -m whole_repo \
    kai-council-api/tests/test_kai807_council_boundary.py || FAIL=1

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  findings contract (honesty: no uncaused alarm)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
python3 -m pytest -q scripts/tests/test_findings_contract.py scripts/tests/test_fleet_findings.py || FAIL=1
python3 shared/test_notify_gateway.py || FAIL=1  # KAI-1100: notify gateway refuses a bare uncaused alarm

echo ""
if [ "$FAIL" -ne 0 ]; then
    echo "[FAIL] CI gate: one or more services failed lint or tests. Do not commit."
    exit 1
fi

echo "[PASS] All services: green baseline + baseline ruff + service tests + whole-repo guards green."
