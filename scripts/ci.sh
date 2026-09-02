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

# MR5 (KAI-1201): optionally scope the per-service ruff+pytest to changed services only,
# to cut close wall-clock. CI_SCOPE_SERVICES = space-separated service names.
#   UNSET            -> full run (every service) — the pre-push / manual default.
#   SET (even empty) -> run only the listed services; empty = skip all per-service checks.
# The green baseline (above) and the whole-repo guards + findings-contract tests (below)
# ALWAYS run regardless of scope — the safety floor is never scoped away. The close engine
# (close_engine.step_ci_gate) sets this from the services changed since the last close and
# falls back to a full run on any cross-cutting change.
svc_in_scope() {
    local svc="$1" s
    # var UNSET => full run; var SET (even empty) => only listed services
    if [ -z "${CI_SCOPE_SERVICES+x}" ]; then return 0; fi
    for s in ${CI_SCOPE_SERVICES}; do [ "$s" = "$svc" ] && return 0; done
    return 1
}

if [ -z "${CI_SCOPE_SERVICES+x}" ]; then
    echo "[scope] full CI — all services (green baseline + whole-repo guards always run)"
else
    echo "[scope] CI scoped to changed services: '${CI_SCOPE_SERVICES}' (green baseline + whole-repo guards always run)"
fi

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

    if ! svc_in_scope "$svc"; then
        echo ""
        echo "  [scoped-out] $svc unchanged since last close — skipping ruff+pytest (MR5 KAI-1201)"
        continue
    fi

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
echo "  kai-scheduler watchdog subsystem (KAI-48)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
# The watchdog is the remediation subsystem's detector — it must itself be tested.
# kai-scheduler is NOT in SERVICES (its tests live in /app root, not /app/tests, and
# the container is non-root so pytest is baked into the image). Run them in-place.
if docker ps --format '{{.Names}}' | grep -qx 'kai-scheduler'; then
    docker exec kai-scheduler python -m pytest -q -p no:cacheprovider \
        /app/test_watchdog_dedup.py \
        /app/test_token_redaction.py \
        /app/test_fleet_watchdog.py \
        /app/test_kai808_telegram_allowlist.py || FAIL=1
else
    echo "  [FAIL] kai-scheduler container not running — cannot exercise watchdog tests"
    FAIL=1
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  kai-buzz approval parser + batched cards (P-3)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
# The approval surface is the phone front door — its decision parser + batched-card
# composition are exercised by buzz_approve.py --selftest (offline crypto+parser gate).
# kai-buzz is NOT in SERVICES (tests live in /app root, container is non-root).
if docker ps --format '{{.Names}}' | grep -qx 'kai-buzz'; then
    docker exec kai-buzz python buzz_approve.py --selftest || FAIL=1
else
    echo "  [FAIL] kai-buzz container not running — cannot exercise approval selftest"
    FAIL=1
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  proactive queue: Finding->pull-card bridge + producer (P-4a)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
# The proactive PULL queue is built by two host-runnable pure units (no container):
# shared/proactive_queue.py (Finding->card bridge: leo-facing filter, code-composed
# card, dedup, notify=False deferred-push) and scripts/proactive_producer.py (leo-facing
# allowlist + idempotent produce). Both carry offline --selftest gates.
python3 shared/proactive_queue.py --selftest || FAIL=1
python3 scripts/proactive_producer.py --selftest || FAIL=1

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
python3 -m pytest -q scripts/tests/test_findings_contract.py scripts/tests/test_fleet_findings.py scripts/tests/test_fleet_eval.py scripts/tests/test_devops_disk_remediation.py scripts/tests/test_devops_ownership.py scripts/tests/test_devops_updates_custodian.py scripts/tests/test_devops_backups_custodian.py scripts/tests/test_devops_services_custodian.py scripts/tests/test_devops_security_custodian.py scripts/tests/test_devops_fleet_custodian.py scripts/tests/test_devops_currency_custodian.py scripts/tests/test_devops_inventory_custodian.py scripts/tests/test_currency_deps.py scripts/tests/test_currency_wp_fleet.py scripts/tests/test_currency_fleet.py scripts/tests/test_system_recover.py || FAIL=1
python3 shared/test_notify_gateway.py || FAIL=1  # KAI-1100: notify gateway refuses a bare uncaused alarm
python3 shared/test_sprint_gate.py || FAIL=1  # S1-A1: sprint hard-gate helper (raise/poll/timeout/fail-closed)
python3 shared/test_gutenberg.py || FAIL=1  # AR-3/KAI-965: Gutenberg block-grammar validator (safety floor)
python3 shared/test_witness.py || FAIL=1  # W-1: three-state trust verdict (default UNKNOWN) + external-witness receipt contract

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  no-live-Slack gate (AR-5 / KAI-1243 retirement)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
bash scripts/ci_no_slack_api.sh || FAIL=1  # KAI-1243: Slack stays retired — no live api/tool/secret ref

echo ""
if [ "$FAIL" -ne 0 ]; then
    echo "[FAIL] CI gate: one or more services failed lint or tests. Do not commit."
    exit 1
fi

echo "[PASS] All services: green baseline + baseline ruff + service tests + whole-repo guards green."
