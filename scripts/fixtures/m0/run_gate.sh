#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
FIXTURE="$ROOT/scripts/fixtures/m0"
REGISTRY="/home/leo/vault/00_System/registry/facts.json"
CAPTURE_DIR="${CAPTURE_DIR:-}"

if [[ -n "$CAPTURE_DIR" ]]; then
  install -d -m 0700 "$CAPTURE_DIR"
fi

capture() {
  local name="$1"
  local content="$2"
  if [[ -n "$CAPTURE_DIR" ]]; then
    printf '%s\n' "$content" > "$CAPTURE_DIR/$name"
    chmod 0600 "$CAPTURE_DIR/$name"
  fi
}

snapshot_registry() {
  python3 - "$REGISTRY" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
raw = path.read_bytes()
data = json.loads(raw)
print(json.dumps({
    "path": str(path),
    "sha256": hashlib.sha256(raw).hexdigest(),
    "valid_json": True,
    "facts_count": len(data["facts"]),
    "pre_existing_fact_intact": any(
        fact.get("id") == "fact-kai-system-topology-001"
        and fact.get("key") == "kai-system_authoritative_repo"
        and fact.get("lifecycle") == "verified"
        for fact in data["facts"]
    ),
    "facts": data["facts"],
}, indent=2))
PY
}

pretty_json() {
  python3 -m json.tool
}

package_id_from_response() {
  python3 -c 'import json,sys; print(json.load(sys.stdin)["package"]["package_id"])'
}

assemble() {
  local advisor="$1"
  python3 - "$advisor" <<'PY' |
import json
import sys
print(json.dumps({
    "key": {"advisor": sys.argv[1], "device": "m0-smoke-gate"},
    "message": "Where is the cobalt compass, and what is the verified M0 registry marker?",
    "project": "m0-seed",
    "task_type": "registry-smoke",
    "channel": "m0-smoke",
}))
PY
    docker exec -i kai-orchestrator curl -sS -X POST \
      http://localhost:8003/context/assemble \
      -H 'Content-Type: application/json' --data-binary @-
}

printf 'COMMAND: install dedicated smoke personas\n'
install -d -m 0775 /home/leo/vault/60_Council/m0smoke /home/leo/vault/60_Council/m0isolation
install -m 0664 "$FIXTURE/M0SMOKE.md" /home/leo/vault/60_Council/m0smoke/M0SMOKE.md
install -m 0664 "$FIXTURE/M0ISOLATION.md" /home/leo/vault/60_Council/m0isolation/M0ISOLATION.md

printf 'REGISTRY BEFORE VALID INGEST\n'
BEFORE_VALID="$(snapshot_registry)"
printf '%s\n' "$BEFORE_VALID"
capture registry-before.json "$BEFORE_VALID"

printf 'COMMAND: Tier 3 fixture ingest\n'
python3 "$ROOT/scripts/ingest.py" "$FIXTURE/test_document.md" \
  --advisor m0smoke --title "M0 Seed Smoke Document"

printf 'COMMAND: Tier 4 fixture ingest\n'
python3 "$ROOT/scripts/ingest.py" --facts "$FIXTURE/test_facts.json" \
  --advisor m0smoke --project m0-seed --task-type registry-smoke \
  --ingested-by m0-smoke-gate

printf 'REGISTRY AFTER VALID INGEST\n'
AFTER_VALID="$(snapshot_registry)"
printf '%s\n' "$AFTER_VALID"
capture registry-after-valid.json "$AFTER_VALID"
python3 - "$BEFORE_VALID" "$AFTER_VALID" <<'PY'
import json
import sys
before, after = map(json.loads, sys.argv[1:])
before_ids = {fact["id"] for fact in before["facts"]}
after_ids = {fact["id"] for fact in after["facts"]}
required = {"m0-smoke-fact-001", "m0-smoke-fact-002"}
assert after["pre_existing_fact_intact"]
assert required <= after_ids
assert before_ids <= after_ids
print(json.dumps({
    "registry_append_gate": "PASS",
    "new_ids": sorted(after_ids - before_ids),
    "fixture_ids_present": sorted(required & after_ids),
}, indent=2))
PY

printf 'COMMAND: deliberately invalid Tier 4 input\n'
HASH_BEFORE_INVALID="$(python3 -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())' "$REGISTRY")"
set +e
INVALID_OUTPUT="$(python3 "$ROOT/scripts/ingest.py" --facts "$FIXTURE/invalid_facts.json" \
  --advisor m0smoke --project m0-seed --task-type registry-smoke \
  --ingested-by m0-smoke-gate 2>&1)"
INVALID_RC=$?
set -e
printf 'exit_code=%s\n%s\n' "$INVALID_RC" "$INVALID_OUTPUT"
capture invalid-rejection.txt "exit_code=$INVALID_RC
$INVALID_OUTPUT"
HASH_AFTER_INVALID="$(python3 -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())' "$REGISTRY")"
test "$INVALID_RC" -ne 0
test "$HASH_BEFORE_INVALID" = "$HASH_AFTER_INVALID"
AFTER_INVALID="$(snapshot_registry)"
capture registry-after-invalid.json "$AFTER_INVALID"
capture fail-closed.json "{\"exit_code\": $INVALID_RC, \"sha256_before\": \"$HASH_BEFORE_INVALID\", \"sha256_after\": \"$HASH_AFTER_INVALID\", \"unchanged\": true}"
printf 'FAIL_CLOSED PASS sha256_before=%s sha256_after=%s\n' \
  "$HASH_BEFORE_INVALID" "$HASH_AFTER_INVALID"

printf 'LIVE ASSEMBLE RESPONSE: m0smoke\n'
SMOKE_RESPONSE="$(assemble m0smoke)"
capture m0smoke-response.json "$SMOKE_RESPONSE"
printf '%s\n' "$SMOKE_RESPONSE" | pretty_json
SMOKE_PACKAGE_ID="$(printf '%s\n' "$SMOKE_RESPONSE" | package_id_from_response)"
printf 'ASSEMBLY LOG: package_id=%s\n' "$SMOKE_PACKAGE_ID"
SMOKE_LOG="$(docker exec kai-orchestrator python /kai-system/scripts/m0_read_assembly_log.py "$SMOKE_PACKAGE_ID")"
capture m0smoke-assembly-log.json "$SMOKE_LOG"
printf '%s\n' "$SMOKE_LOG" | pretty_json

printf 'LIVE ASSEMBLE RESPONSE: m0isolation\n'
ISOLATION_RESPONSE="$(assemble m0isolation)"
capture m0isolation-response.json "$ISOLATION_RESPONSE"
printf '%s\n' "$ISOLATION_RESPONSE" | pretty_json
ISOLATION_PACKAGE_ID="$(printf '%s\n' "$ISOLATION_RESPONSE" | package_id_from_response)"
printf 'ASSEMBLY LOG: package_id=%s\n' "$ISOLATION_PACKAGE_ID"
ISOLATION_LOG="$(docker exec kai-orchestrator python /kai-system/scripts/m0_read_assembly_log.py "$ISOLATION_PACKAGE_ID")"
capture m0isolation-assembly-log.json "$ISOLATION_LOG"
printf '%s\n' "$ISOLATION_LOG" | pretty_json

python3 - "$SMOKE_RESPONSE" "$SMOKE_LOG" "$ISOLATION_RESPONSE" "$ISOLATION_LOG" <<'PY'
import json
import sys

smoke, smoke_log, isolation, isolation_log = map(json.loads, sys.argv[1:])
sp = smoke["package"]
ip = isolation["package"]
marker = "cobalt compass"
fact_ids = {"m0-smoke-fact-001", "m0-smoke-fact-002"}

assert marker in sp["recall_text"].lower()
assert 'source="qdrant:m0smoke"' in sp["recall_text"]
assert fact_ids <= set(sp["budget_report"]["t4"]["facts"])
assert "silver-orchid-7319" in sp["facts_text"]
assert any(
    "scripts/fixtures/m0/test_document.md" in hit["doc_id"]
    for hit in smoke_log["tiers"]["t3"]["hits"]
)
assert fact_ids <= set(smoke_log["tiers"]["t4"]["facts"])

assert marker not in ip["recall_text"].lower()
assert not fact_ids.intersection(ip["budget_report"]["t4"]["facts"])
assert not any(
    "scripts/fixtures/m0/test_document.md" in hit["doc_id"]
    for hit in isolation_log["tiers"]["t3"]["hits"]
)
assert not fact_ids.intersection(isolation_log["tiers"]["t4"]["facts"])

print(json.dumps({
    "gate": "PASS",
    "smoke_package_id": sp["package_id"],
    "isolation_package_id": ip["package_id"],
    "tier3_source_attributed": True,
    "tier4_fixture_facts_present": sorted(fact_ids),
    "advisor_namespace_isolation": True,
}, indent=2))
PY

if [[ -n "$CAPTURE_DIR" ]]; then
  printf 'FULL CAPTURE DIRECTORY: %s (mode 0700; files mode 0600)\n' "$CAPTURE_DIR"
fi
