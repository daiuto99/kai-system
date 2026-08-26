#!/usr/bin/env bash
# KAI-807 live regression: council remains internal and rejects anonymous work.
set -euo pipefail

root=$(cd "$(dirname "$0")/.." && pwd)
council_service=$(sed -n '/^  kai-council-api:/,/^  kai-web:/p' "$root/docker-compose.yml")
if grep -q '8002:8002' <<<"$council_service"; then
  echo "FAIL: kai-council-api publishes host port 8002"
  exit 1
fi

status=$(docker exec kai-orchestrator python3 -c '
import json, urllib.error, urllib.request
request = urllib.request.Request(
    "http://kai-council-api:8002/council/message",
    data=json.dumps({"channel":"kai","message":"KAI-807 auth regression probe","user_id":"regression"}).encode(),
    headers={"Content-Type":"application/json"}, method="POST")
try:
    print(urllib.request.urlopen(request, timeout=10).status)
except urllib.error.HTTPError as exc:
    print(exc.code)
')

if [[ "$status" != "401" && "$status" != "503" ]]; then
  echo "FAIL: anonymous council message returned $status (expected 401 or 503)"
  exit 1
fi
echo "PASS: council port unpublished; anonymous council message denied ($status)"
