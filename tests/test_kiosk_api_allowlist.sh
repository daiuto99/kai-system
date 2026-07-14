#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${KAI_PUBLIC_BASE_URL:-https://kai.sonicink.space}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NGINX_CONFIG="${KAI_NGINX_CONFIG:-${REPO_ROOT}/kai-web/nginx.conf}"

mutation_paths=(
  /kiosk-api/dispatch
  /kiosk-api/workflows/run
  /kiosk-api/capability/vault.write
  /kiosk-api/gates/x/resolve
)

allowlisted_paths=(
  /kiosk-api/invariants/state
  /kiosk-api/cost-summary
  /kiosk-api/status
  /kiosk-api/jobs
)

status_code() {
  local method="$1"
  local path="$2"
  curl --silent --show-error --output /dev/null \
    --connect-timeout 10 --max-time 20 \
    --request "$method" --write-out '%{http_code}' "${BASE_URL}${path}"
}

assert_status() {
  local expected="$1"
  local method="$2"
  local path="$3"
  local actual
  actual="$(status_code "$method" "$path")"
  printf '%s %s %s\n' "$actual" "$method" "$path"
  if [[ "$actual" != "$expected" ]]; then
    printf 'FAIL: expected %s for %s %s, got %s\n' "$expected" "$method" "$path" "$actual" >&2
    return 1
  fi
}

for path in "${mutation_paths[@]}"; do
  assert_status 404 GET "$path"
done

for path in "${allowlisted_paths[@]}"; do
  query=""
  if [[ "$path" == /kiosk-api/jobs ]]; then
    query='?limit=6'
  fi
  assert_status 200 GET "${path}${query}"
  assert_status 403 POST "$path"
done

assert_status 200 GET /kiosk.html

if grep -Eq 'rewrite[[:space:]]+\^/kiosk-api/' "$NGINX_CONFIG"; then
  echo 'FAIL: prefix-stripping kiosk proxy is present' >&2
  exit 1
fi

if grep -Eq '^[[:space:]]*location[[:space:]]+/kiosk-api/' "$NGINX_CONFIG"; then
  echo 'FAIL: wildcard kiosk proxy location is present' >&2
  exit 1
fi

for path in /kiosk-api/invariants/state /kiosk-api/cost-summary /kiosk-api/status /kiosk-api/jobs; do
  if ! grep -Fq "location = ${path} {" "$NGINX_CONFIG"; then
    printf 'FAIL: missing exact allowlist location for %s\n' "$path" >&2
    exit 1
  fi
done

if [[ "$(grep -Ec '^[[:space:]]*location = /kiosk-api/' "$NGINX_CONFIG")" != 4 ]]; then
  echo 'FAIL: kiosk allowlist must contain exactly four exact locations' >&2
  exit 1
fi

echo 'PASS: kiosk API is exact-path GET-only and public mutation routes are closed'
