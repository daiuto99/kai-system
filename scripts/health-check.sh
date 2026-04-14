#!/usr/bin/env bash
set -euo pipefail

GREEN="$(tput setaf 2 2>/dev/null || echo '')"
RED="$(tput setaf 1 2>/dev/null || echo '')"
RESET="$(tput sgr0 2>/dev/null || echo '')"

pass() { echo "  ✓ $1"; }
fail() { echo "  ✗ $1"; FAILED=1; }

FAILED=0

echo ""
echo "KAI System Health Check — $(date)"
echo "══════════════════════════════════"

echo ""
echo "Containers:"
for svc in kai-worker-api kai-council-api kai-scheduler; do
  STATUS=$(docker inspect --format='{{.State.Status}}' "$svc" 2>/dev/null || echo 'not found')
  if [ "$STATUS" = "running" ]; then
    pass "$svc: running"
  else
    fail "$svc: $STATUS"
  fi
done

echo ""
echo "Health endpoints:"

check_endpoint() {
  local name="$1" url="$2"
  HTTP=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$url" 2>/dev/null || echo "000")
  if [ "$HTTP" = "200" ]; then
    pass "$name ($url)"; 
  else
    fail "$name ($url) — HTTP $HTTP"
  fi
}

check_endpoint "kai-worker-api" "http://localhost:8001/health"
check_endpoint "kai-council-api" "http://localhost:8002/health"

echo ""
echo "Vault sync:"
VAULT_FILES=$(find /home/leo/vault -type f 2>/dev/null | wc -l)
if [ "$VAULT_FILES" -gt 0 ]; then
  pass "Vault mounted: $VAULT_FILES files"
else
  fail "Vault empty or not synced"
fi

echo ""
if [ "$FAILED" -eq 0 ]; then
  echo "All checks passed."
else
  echo "Some checks failed."
  exit 1
fi
echo ""
