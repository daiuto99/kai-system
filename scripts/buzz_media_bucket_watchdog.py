#!/usr/bin/env python3
"""KAI-1478 · Buzz media bucket self-heal watchdog — make a vanished bucket actionable.

The 2026-09-20 incident: the MinIO `buzz-media` bucket was gone (MinIO /data held no
buckets, only .minio.sys dated Sep 12). buzz-relay's storage sweep threw NoSuchBucket
every 5 min for 8 DAYS and every Buzz media/attachment path was broken — while every
monitor stayed green, because nothing watched the relay's own error stream and no probe
exercised object storage. Docker cannot heal this: the container is healthy, the bucket
inside MinIO is simply absent.

This watchdog closes that gap the self-hosted way — a host cron, not a compose init
container (the exact sidecar/orphan trap buzz_shim_watchdog documents: a compose sweep
orphans a sidecar and re-creates the original 11-day-outage failure class). A cron
self-heal also beats a startup-only fix: it restores the bucket whenever it vanishes,
not only on a container restart. Every 5 min on the worker HOST:

  ask buzz-minio (via docker exec + its OWN root creds) whether BUCKET exists
  MISSING: `mc mb -p` to recreate it  ->  notify() pages #devops (provenance='real')
  OK:      refresh the heartbeat

MinIO root creds are read INSIDE the container from its own env — no secret ever crosses
to the host or into this script's args (L18). The ONLY mutation is `mc mb` on the one
missing bucket. Heartbeat is watched by meta_monitor (KAI-1115): a dead probe pages up.
"""
from __future__ import annotations

import fcntl
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "shared"))  # notify_gateway lives here

import os

MINIO_CONTAINER = "buzz-minio"
RELAY_CONTAINER = "buzz-relay"
BUCKET = os.environ.get("BUZZ_S3_BUCKET", "buzz-media")
EXEC_TIMEOUT_SEC = 30
SCHEMA = "kai.buzz_media_bucket_watchdog.v1"

_VAULT_CANDIDATES = (Path("/home/leo/vault"), Path("/vault"))
STATE_FILENAME = "_buzz_media_bucket_watchdog_state.json"
LOCK_PATH = Path("/tmp/buzz_media_bucket_watchdog.lock")


def _acquire_lock():
    """Single-instance guard: `mc mb` + alias set can outlast a tick under load; two runs
    could both see MISSING and double-create. Non-blocking — skip the tick if held."""
    fh = open(LOCK_PATH, "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    return fh


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _vault_dir() -> Path:
    for c in _VAULT_CANDIDATES:
        if c.exists():
            return c
    return _VAULT_CANDIDATES[0]


def _state_path() -> Path:
    return _vault_dir() / STATE_FILENAME


def _load_prior() -> dict:
    try:
        return json.loads(_state_path().read_text())
    except Exception:
        return {}


def _write_state(state: dict) -> None:
    p = _state_path()
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(p)


# The MinIO client runs INSIDE buzz-minio and authenticates with the container's own
# root creds (env vars never leave the container). "loc" alias is local to this exec.
_MC_PREAMBLE = (
    'mc alias set loc http://127.0.0.1:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" '
    ">/dev/null 2>&1 || { echo ALIAS_FAIL; exit 3; }; "
)


def bucket_exists() -> tuple[bool, str]:
    """Return (exists, reason). Never raises. reason carries the failure text on error."""
    script = _MC_PREAMBLE + f'mc ls "loc/{BUCKET}" >/dev/null 2>&1 && echo EXISTS || echo MISSING'
    try:
        p = subprocess.run(["docker", "exec", MINIO_CONTAINER, "sh", "-c", script],
                           capture_output=True, text=True, timeout=EXEC_TIMEOUT_SEC)
    except Exception as e:
        return False, f"probe error: {type(e).__name__}: {e}"
    out = (p.stdout or "").strip().splitlines()
    tag = out[-1] if out else ""
    if tag == "EXISTS":
        return True, "present"
    if tag == "MISSING":
        return False, "bucket absent"
    detail = (p.stderr or p.stdout or f"exit {p.returncode}").strip()[:180]
    return False, f"indeterminate ({tag or detail})"


def create_bucket() -> tuple[bool, str]:
    """Recreate the missing bucket. Returns (ok, detail). Never raises. Idempotent (-p)."""
    script = _MC_PREAMBLE + f'mc mb -p "loc/{BUCKET}" 2>&1'
    try:
        p = subprocess.run(["docker", "exec", MINIO_CONTAINER, "sh", "-c", script],
                           capture_output=True, text=True, timeout=EXEC_TIMEOUT_SEC)
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
    if p.returncode != 0:
        return False, (p.stderr or p.stdout or f"exit {p.returncode}").strip()[:180]
    return True, (p.stdout or "created").strip()[:180]


def _page(reason: str, heal_detail: str, dry_run: bool) -> str:
    """Page #devops about the self-heal. provenance='real' — the outage is real even
    though we healed it; a synthetic stamp would be gateway-suppressed."""
    title = f"Buzz media bucket AUTOHEALED — recreated '{BUCKET}' after it vanished"
    body = (f"MinIO bucket '{BUCKET}' was missing ({reason}); the watchdog recreated it.\n"
            f"heal: {heal_detail}\n"
            f"This is the KAI-1478 vanished-bucket class — buzz-relay storage sweeps were "
            f"failing (NoSuchBucket) and Buzz media/attachments were broken until now. "
            f"Self-healed; investigate why the bucket disappeared if it recurs.")
    if dry_run:
        return f"[dry-run] would page #devops: {title}"
    try:
        vault = _vault_dir()
        # notify_gateway captures these paths at import — set BEFORE importing it. From host
        # cron the container defaults (/vault/...) are unwritable; redirect to the host vault
        # (same physical files via the bind mount). KAI_DEVOPS_QUEUE is what routes to #devops.
        os.environ.setdefault("KAI_NOTIFY_LOG", str(vault / "00_System" / "notify_log.jsonl"))
        os.environ.setdefault("KAI_NOTIFY_DEDUP", str(vault / "00_System" / "notify_dedup.json"))
        os.environ.setdefault("KAI_DEVOPS_QUEUE", str(vault / "00_System" / "devops_queue.jsonl"))
        from notify_gateway import notify, Event
        bucket = _now().strftime("%Y-%m-%dT%H")  # at most one heal page per hour if it flaps
        res = notify(Event(
            source="buzz_media_bucket_watchdog",
            kind="alert",
            title=title,
            body=body,
            audience="devops",     # -> #devops Buzz channel (infra/system alerts lane)
            actionable=True,
            provenance="real",
            dedup_key=f"buzz_media_bucket_autoheal:{bucket}",
        ))
        return f"paged: decision={res.decision} dest={res.destination} delivered={res.delivered}"
    except Exception as e:
        return f"PAGE FAILED: {type(e).__name__}: {e}"


def main(argv: list[str]) -> int:
    dry_run = "--dry-run" in argv
    now = _now()
    lock = _acquire_lock()
    if lock is None:
        print(f"[{_iso(now)}] SKIP — another run holds the lock", flush=True)
        return 0
    prior = _load_prior()

    exists, reason = bucket_exists()
    action = None
    healed = False
    if not exists and reason == "bucket absent":
        if dry_run:
            action = _page(reason, "[dry-run] no create", dry_run=True)
        else:
            ok, detail = create_bucket()
            healed = ok
            page_line = _page(reason, detail, dry_run=False)
            action = f"{'RECREATED' if ok else 'CREATE FAILED'} ({detail}) | {page_line}"
    elif not exists:
        # Indeterminate/probe error — do NOT blindly create; log for the heartbeat watcher.
        action = f"skip create — {reason}"

    state = {
        "schema": SCHEMA,
        "last_check": _iso(now),
        "bucket": BUCKET,
        "exists": exists,
        "reason": reason,
        "last_ok": _iso(now) if exists else prior.get("last_ok"),
        "last_heal": _iso(now) if healed else prior.get("last_heal"),
        "heal_count_total": int(prior.get("heal_count_total", 0)) + (1 if healed else 0),
        "dry_run": dry_run,
    }
    try:
        _write_state(state)
    except Exception as e:
        print(f"[{_iso(now)}] WARN heartbeat write failed: {e}", flush=True)

    status = "OK" if exists else "MISSING"
    line = f"[{_iso(now)}] {status} bucket '{BUCKET}' — {reason}"
    if action:
        line += f" | {action}"
    print(line, flush=True)
    return 0 if exists or healed else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
