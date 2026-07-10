#!/usr/bin/env bash
# S5-1: lint + test CI gate — L12 enforced
# Run before committing: cd ~/kai-system && bash scripts/ci.sh
# All services must pass ruff + seed suite before commit is valid.
set -euo pipefail

SERVICES=(
    "kai-worker-api:kai-worker-api"
    "kai-council-api:kai-council-api"
    "kai-orchestrator:kai-orchestrator"
)

FAIL=0

for entry in "${SERVICES[@]}"; do
    svc="${entry%%:*}"
    container="${entry##*:}"

    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  ruff check: $svc"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    docker exec "$container" ruff check /app --no-cache --output-format=full || FAIL=1

    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  pytest: $svc"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    docker exec "$container" pytest /app/tests/ -v --tb=short -m "not destructive" || FAIL=1
done

echo ""
if [ "$FAIL" -ne 0 ]; then
    echo "[FAIL] CI gate: one or more services failed lint or tests. Do not commit."
    exit 1
fi

echo "[PASS] All services: ruff + seed suite green."
