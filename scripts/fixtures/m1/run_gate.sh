#!/usr/bin/env bash
set -euo pipefail
umask 077

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
FIXTURE="$ROOT/scripts/fixtures/m1"
REGISTRY="/home/leo/vault/00_System/registry/facts.json"
PERSONA_DIR="/home/leo/vault/60_Council/m1smoke"
CAPTURE_DIR="${CAPTURE_DIR:-$(mktemp -d /tmp/kai-m1-gate.XXXXXX)}"
BACKUP="$(mktemp /tmp/kai-m1-registry.XXXXXX)"
mkdir -p "$CAPTURE_DIR"
chmod 0700 "$CAPTURE_DIR"
chmod 0600 "$BACKUP"

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
        and fact.get("lifecycle") == "verified"
        for fact in data["facts"]
    ),
    "m1smoke_fact_ids": [
        fact.get("id") for fact in data["facts"] if fact.get("advisor") == "m1smoke"
    ],
}, indent=2))
PY
}

backup_registry() {
  python3 - "$REGISTRY" "$BACKUP" <<'PY'
import fcntl
import os
import sys
from pathlib import Path

registry, backup = map(Path, sys.argv[1:])
lock_path = registry.with_name(f".{registry.name}.lock")
with lock_path.open("a+", encoding="utf-8") as lock_file:
    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
    raw = registry.read_bytes()
    backup.write_bytes(raw)
    os.chmod(backup, 0o600)
PY
}

restore_registry() {
  python3 - "$REGISTRY" "$BACKUP" <<'PY'
import fcntl
import hashlib
import json
import os
import stat
import sys
import tempfile
from pathlib import Path

registry, backup = map(Path, sys.argv[1:])
before_raw = backup.read_bytes()
before = json.loads(before_raw)
lock_path = registry.with_name(f".{registry.name}.lock")
with lock_path.open("a+", encoding="utf-8") as lock_file:
    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
    current = json.loads(registry.read_bytes())
    removed = [
        fact.get("id") for fact in current["facts"] if fact.get("advisor") == "m1smoke"
    ]
    candidate = dict(current)
    candidate["facts"] = [
        fact for fact in current["facts"] if fact.get("advisor") != "m1smoke"
    ]
    if candidate != before:
        raise SystemExit(
            "cleanup refused: non-m1smoke registry data changed during the gate; "
            "live data was left untouched"
        )

    mode = stat.S_IMODE(registry.stat().st_mode)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{registry.name}.m1-cleanup.", suffix=".tmp", dir=registry.parent
    )
    try:
        with os.fdopen(fd, "wb") as temp_file:
            os.fchmod(temp_file.fileno(), mode)
            temp_file.write(before_raw)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        os.replace(temp_name, registry)
        directory_fd = os.open(registry.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise

print(json.dumps({
    "registry_restore": "PASS",
    "removed_m1smoke_fact_ids": sorted(removed),
    "sha256": hashlib.sha256(registry.read_bytes()).hexdigest(),
}, indent=2))
PY
}

read_package_id() {
  python3 -c 'import json,sys; print(json.load(sys.stdin)["package_id"])'
}

chat() {
  local project="${1-}"
  local payload
  payload="$(python3 - "$project" <<'PY'
import json
import sys

payload = {
    "channel": "m1smoke",
    "message": "What is the verified M1 project marker?",
    "user_id": "m1-smoke-gate",
}
if sys.argv[1]:
    payload["project"] = sys.argv[1]
    payload["task_type"] = "m1-scope"
print(json.dumps(payload))
PY
)"
  printf '%s' "$payload" | curl -fsS -X POST http://localhost:8002/council/message \
    -H 'Content-Type: application/json' --data-binary @-
}

read_log() {
  docker exec kai-orchestrator python /kai-system/scripts/m0_read_assembly_log.py "$1"
}

cleanup() {
  local original_rc=$?
  local cleanup_rc=0
  trap - EXIT
  set +e

  printf 'COMMAND: delete dedicated m1smoke Qdrant collection\n'
  COLLECTION_DELETE="$(curl -sS -X DELETE http://localhost:6333/collections/m1smoke)"
  COLLECTION_DELETE_RC=$?
  printf '%s\n' "$COLLECTION_DELETE" | python3 -m json.tool 2>/dev/null || printf '%s\n' "$COLLECTION_DELETE"
  printf '%s\n' "$COLLECTION_DELETE" > "$CAPTURE_DIR/collection-delete.json"
  if [[ $COLLECTION_DELETE_RC -ne 0 ]]; then cleanup_rc=1; fi

  printf 'COMMAND: atomically restore Fact Registry snapshot\n'
  restore_registry | tee "$CAPTURE_DIR/registry-restore.json"
  if [[ ${PIPESTATUS[0]} -ne 0 ]]; then cleanup_rc=1; fi

  rm -f "$PERSONA_DIR/M1SMOKE.md"
  rmdir "$PERSONA_DIR" 2>/dev/null || true

  printf 'REGISTRY AFTER CLEANUP\n'
  snapshot_registry | tee "$CAPTURE_DIR/registry-after-cleanup.json"
  AFTER_SHA="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["sha256"])' "$CAPTURE_DIR/registry-after-cleanup.json")"
  if [[ "$AFTER_SHA" != "$BEFORE_SHA" ]]; then
    printf 'cleanup SHA mismatch: before=%s after=%s\n' "$BEFORE_SHA" "$AFTER_SHA" >&2
    cleanup_rc=1
  fi

  COLLECTION_STATUS="$(curl -sS -o /dev/null -w '%{http_code}' http://localhost:6333/collections/m1smoke)"
  printf 'COLLECTION AFTER CLEANUP http_status=%s (404 required)\n' "$COLLECTION_STATUS"
  if [[ "$COLLECTION_STATUS" != "404" ]]; then cleanup_rc=1; fi
  printf 'RETAINED: assembly-log/conversation audit rows and checked-in fixture/channel mapping only\n'
  printf 'CAPTURE DIRECTORY: %s\n' "$CAPTURE_DIR"

  rm -f "$BACKUP"
  if [[ $original_rc -eq 0 && $cleanup_rc -eq 0 ]]; then
    printf 'M1 GATE INCLUDING CLEANUP: PASS\n'
    exit 0
  fi
  exit 1
}

backup_registry
printf 'REGISTRY BEFORE GATE\n'
snapshot_registry | tee "$CAPTURE_DIR/registry-before.json"
BEFORE_SHA="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["sha256"])' "$CAPTURE_DIR/registry-before.json")"
python3 - "$CAPTURE_DIR/registry-before.json" <<'PY'
import json
import sys

snapshot = json.load(open(sys.argv[1]))
assert snapshot["pre_existing_fact_intact"]
assert snapshot["m1smoke_fact_ids"] == [], "pre-existing m1smoke facts must be cleaned first"
PY
trap cleanup EXIT

printf 'COMMAND: install dedicated m1smoke synthetic persona\n'
install -d -m 0775 "$PERSONA_DIR"
install -m 0664 "$FIXTURE/M1SMOKE.md" "$PERSONA_DIR/M1SMOKE.md"

printf 'COMMAND: seed Tier 3 fixture collection (not project-scoped; not used as scoping proof)\n'
python3 "$ROOT/scripts/ingest.py" "$FIXTURE/test_document.md" \
  --advisor m1smoke --title "M1 Tier 3 Scope Boundary Fixture"

printf 'COMMAND: seed Tier 4 alpha facts\n'
python3 "$ROOT/scripts/ingest.py" --facts "$FIXTURE/alpha_facts.json" \
  --advisor m1smoke --project alpha --ingested-by m1-smoke-gate

printf 'COMMAND: seed Tier 4 beta facts\n'
python3 "$ROOT/scripts/ingest.py" --facts "$FIXTURE/beta_facts.json" \
  --advisor m1smoke --project beta --ingested-by m1-smoke-gate

printf 'REGISTRY AFTER SEED\n'
snapshot_registry | tee "$CAPTURE_DIR/registry-after-seed.json"

for scope in alpha beta unscoped; do
  printf 'LIVE CHAT RESPONSE: %s\n' "$scope"
  if [[ "$scope" == "unscoped" ]]; then
    chat > "$CAPTURE_DIR/$scope-response.json"
  else
    chat "$scope" > "$CAPTURE_DIR/$scope-response.json"
  fi
  python3 -m json.tool "$CAPTURE_DIR/$scope-response.json"
  PACKAGE_ID="$(read_package_id < "$CAPTURE_DIR/$scope-response.json")"
  printf 'ASSEMBLY LOG: scope=%s package_id=%s\n' "$scope" "$PACKAGE_ID"
  read_log "$PACKAGE_ID" > "$CAPTURE_DIR/$scope-assembly-log.json"
  python3 -m json.tool "$CAPTURE_DIR/$scope-assembly-log.json"
done

python3 "$FIXTURE/assert_gate.py" \
  "$CAPTURE_DIR/alpha-response.json" "$CAPTURE_DIR/alpha-assembly-log.json" \
  "$CAPTURE_DIR/beta-response.json" "$CAPTURE_DIR/beta-assembly-log.json" \
  "$CAPTURE_DIR/unscoped-response.json" "$CAPTURE_DIR/unscoped-assembly-log.json"
