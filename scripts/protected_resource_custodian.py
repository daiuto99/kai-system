#!/usr/bin/env python3
"""KAI-1480 Phase 1 · Protected stateful-resource custodian (DETECTION half).

Every minute on the worker HOST, verify that each store in config/protected_resources.json
still exists, is bound to the RIGHT container at the RIGHT mount, and passes its integrity
invariant. On ANY drift -> page #devops (provenance='real') and turn the baseline RED (the
baseline reads the same verifier). This is the loud detector the 2026-09-12 incident
(KAI-1475) lacked: buzz-minio was silently rebound to an empty volume and every monitor
stayed green for 8 days.

DETECTION ONLY. This does NOT prevent or heal — Phase 2 (Leo-approved) adds the
external-volume + approval-gated veto. Verification logic lives in the shared, pure
`protected_resources` module so this custodian and green_baseline share one source of truth.

Heartbeat is written to the vault and watched by meta_monitor (KAI-1115): a dead probe
pages up. Notify env is set BEFORE importing notify_gateway (host cron can't write the
container default paths) — mirrors buzz_media_bucket_watchdog.
"""
from __future__ import annotations

import fcntl
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "shared"))

from protected_resources import verify_all  # noqa: E402

_VAULT_CANDIDATES = (Path("/home/leo/vault"), Path("/vault"))
STATE_FILENAME = "_protected_resource_custodian_state.json"
LOCK_PATH = Path("/tmp/protected_resource_custodian.lock")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _vault_dir() -> Path:
    for c in _VAULT_CANDIDATES:
        if c.exists():
            return c
    return _VAULT_CANDIDATES[0]


def _acquire_lock():
    fh = open(LOCK_PATH, "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    return fh


def _write_state(state: dict) -> None:
    p = _vault_dir() / STATE_FILENAME
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(p)


def _page(failures: list[str], dry_run: bool) -> str:
    n = len(failures)
    title = f"PROTECTED-RESOURCE DRIFT — {n} stateful-store invariant(s) failing"
    body = (
        "A registered protected stateful resource has drifted (KAI-1480). This is the "
        "vanish-via-rebind / silent-data-loss class the 2026-09-12 incident exposed.\n\n"
        + "\n".join(f"  - {f}" for f in failures)
        + "\n\nDETECTION only — no auto-heal. Investigate immediately; a rebound or missing "
        "volume means a stateful store is not durable right now."
    )
    if dry_run:
        return f"[dry-run] would page #devops: {title}"
    try:
        vault = _vault_dir()
        import os
        os.environ.setdefault("KAI_NOTIFY_LOG", str(vault / "00_System" / "notify_log.jsonl"))
        os.environ.setdefault("KAI_NOTIFY_DEDUP", str(vault / "00_System" / "notify_dedup.json"))
        os.environ.setdefault("KAI_DEVOPS_QUEUE", str(vault / "00_System" / "devops_queue.jsonl"))
        from notify_gateway import notify, Event

        # Dedup on the SET of failing resources per hour, so a persistent drift pages once/hour
        # but a NEW failing resource pages immediately.
        keyset = ",".join(sorted({f.split(":")[0] for f in failures}))
        hour = _now().strftime("%Y-%m-%dT%H")
        res = notify(Event(
            source="protected_resource_custodian",
            kind="alert",
            title=title,
            body=body,
            audience="devops",
            actionable=True,
            provenance="real",
            dedup_key=f"protected_resource_drift:{keyset}:{hour}",
        ))
        return f"paged: decision={res.decision} dest={res.destination} delivered={res.delivered}"
    except Exception as e:  # noqa: BLE001
        return f"PAGE FAILED: {type(e).__name__}: {e}"


def main(argv: list[str]) -> int:
    dry_run = "--dry-run" in argv
    lock = _acquire_lock()
    if lock is None:
        print("another instance holds the lock; skipping this tick")
        return 0
    try:
        failures, count = verify_all()
        now = _now()
        state = {
            "schema": "kai.protected_resource_custodian.v1",
            "last_run": _iso(now),
            "resource_count": count,
            "ok": not failures,
            "failures": failures,
        }
        _write_state(state)
        if failures:
            page_result = _page(failures, dry_run)
            print(f"DRIFT: {len(failures)} failure(s) across {count} resources — {page_result}")
            for f in failures:
                print(f"  RED {f}")
            return 1
        print(f"OK: all {count} protected resources intact")
        return 0
    finally:
        lock.close()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
