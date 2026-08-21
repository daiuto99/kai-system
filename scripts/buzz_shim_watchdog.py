#!/usr/bin/env python3
"""KAI-1182 · Buzz shim autoheal watchdog — make "unhealthy" actionable.

The 2026-08-21 outage: kai-buzz-shim (:4001) HUNG — the process stayed alive but
wedged, refusing connections for ~2.5h. Docker's `restart: always` only fires on
process EXIT, so a hung-but-alive container is invisible to it; the compose
healthcheck correctly went unhealthy but nothing acts on a Docker healthcheck, and
there is no autoheal sidecar. Result: every advisor DM silently dropped for hours
until a session happened to force-recreate the container.

This watchdog closes that gap the self-hosted way (no new image, no sidecar that a
compose sweep could orphan — the exact failure class that caused the original 11-day
outage). On the worker HOST via cron, every minute:

  poke :4001/v1/models (the same liveness the green-baseline + healthcheck use)
  FAIL: increment a consecutive counter (debounce against a transient blip)
  >= FAIL_THRESHOLD consecutive fails, and not inside a post-restart cooldown:
      `docker restart kai-buzz-shim`  -> notify() pages Leo (provenance='real')
  OK:   reset the counter, refresh the heartbeat

Turns a multi-hour wedge into a ~2-3 min blip. Read-mostly: the ONLY mutation it
ever performs is restarting the one wedged shim container, and only after a debounce
+ cooldown. Never touches anything else.
"""
from __future__ import annotations

import fcntl
import json
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "shared"))  # notify_gateway lives here (same as advisor_dm_probe)

CONTAINER = "kai-buzz-shim"
MODELS_URL = "http://localhost:4001/v1/models"
REQUIRED = {"kai", "sky", "roads", "coach"}
REQUEST_TIMEOUT_SEC = 5
FAIL_THRESHOLD = 2            # consecutive fails before a restart (debounce ~2 min at 1-min cadence)
RESTART_COOLDOWN_SEC = 300   # after a restart, give start_period + recovery room before restarting again
SCHEMA = "kai.buzz_shim_watchdog.v1"

_VAULT_CANDIDATES = (Path("/home/leo/vault"), Path("/vault"))
STATE_FILENAME = "_buzz_shim_watchdog_state.json"
LOCK_PATH = Path("/tmp/buzz_shim_watchdog.lock")


def _acquire_lock():
    """Single-instance guard: a `docker restart` can outlast the 1-min cron tick (the
    2026-08-21 shim needed a force-kill after the 10s SIGTERM grace), so two runs could
    otherwise overlap, both read pre-restart state, and double-restart. Non-blocking —
    if another run holds the lock we skip this tick. Returns the fh (keep it referenced
    so the lock is held for the process lifetime) or None if already locked."""
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
    """Atomic write so a reader never sees a half-written heartbeat."""
    p = _state_path()
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(p)


def probe_live() -> tuple[bool, str]:
    """Poke :4001/v1/models. Returns (ok, reason). Never raises."""
    try:
        req = urllib.request.Request(MODELS_URL, method="GET")
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SEC) as r:
            status = getattr(r, "status", None) or getattr(r, "code", 200)
            raw = r.read()
    except Exception as e:
        return False, f"no round-trip: {type(e).__name__}: {e}"
    if status != 200:
        return False, f"non-200 status: {status}"
    try:
        data = json.loads(raw)
        ids = {m.get("id") for m in data.get("data", [])}
    except Exception as e:
        return False, f"unparseable models list: {type(e).__name__}: {e}"
    missing = REQUIRED - ids
    if missing:
        return False, "missing advisor models: " + ", ".join(sorted(missing))
    return True, "healthy"


def restart_shim() -> tuple[bool, str]:
    """Restart the wedged shim. Returns (ok, detail). Never raises."""
    try:
        p = subprocess.run(["docker", "restart", CONTAINER],
                           capture_output=True, text=True, timeout=60)
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
    if p.returncode != 0:
        return False, (p.stderr or p.stdout or f"exit {p.returncode}").strip()[:200]
    return True, "restarted"


def _page_leo(reason: str, consecutive: int, restart_detail: str, dry_run: bool) -> str:
    """Page Leo about the autoheal event. provenance='real' — the outage is real even
    though the watchdog self-healed it; a synthetic stamp would be gateway-suppressed."""
    title = f"Buzz shim AUTOHEALED — restarted after {consecutive} consecutive fail"
    body = (f"kai-buzz-shim :4001 was unresponsive ({reason}); the watchdog restarted it.\n"
            f"restart: {restart_detail}\n"
            f"This is the KAI-1182 hung-shim failure class — advisor DMs were dropping until "
            f"the restart. Self-healed; investigate if it recurs.")
    if dry_run:
        return f"[dry-run] would page: {title}"
    try:
        import os
        # Same host-vault audit-log redirect as advisor_dm_probe: notify_gateway defaults
        # its Rule-A audit log to the container path (/vault/...), unwritable from host cron.
        os.environ.setdefault("KAI_NOTIFY_LOG", str(_vault_dir() / "00_System" / "notify_log.jsonl"))
        from notify_gateway import notify, Event
        bucket = _now().strftime("%Y-%m-%dT%H")  # at most one autoheal page per hour if it flaps
        res = notify(Event(
            source="buzz_shim_watchdog",
            kind="alert",
            title=title,
            body=body,
            audience="personal",
            actionable=True,
            provenance="real",
            dedup_key=f"buzz_shim_autoheal:{bucket}",
        ))
        return f"paged: decision={res.decision} dest={res.destination} delivered={res.delivered}"
    except Exception as e:
        return f"PAGE FAILED: {type(e).__name__}: {e}"


def main(argv: list[str]) -> int:
    dry_run = "--dry-run" in argv
    now = _now()
    lock = _acquire_lock()  # held for the process lifetime; released on exit
    if lock is None:
        print(f"[{_iso(now)}] SKIP — another watchdog run holds the lock", flush=True)
        return 0
    prior = _load_prior()

    ok, reason = probe_live()
    consecutive = 0 if ok else int(prior.get("consecutive_failures", 0)) + 1

    action = None
    restarted = False
    if not ok and consecutive >= FAIL_THRESHOLD:
        # Cooldown guard: don't hammer restarts every minute while the container is still
        # inside its start_period — one restart, then wait RESTART_COOLDOWN_SEC before another.
        last_restart = prior.get("last_restart")
        in_cooldown = False
        if last_restart:
            try:
                age = time.time() - datetime.fromisoformat(
                    str(last_restart).replace("Z", "+00:00")).timestamp()
                in_cooldown = age < RESTART_COOLDOWN_SEC
            except Exception:
                in_cooldown = False
        if in_cooldown:
            action = "restart suppressed (cooldown)"
        elif dry_run:
            action = _page_leo(reason, consecutive, "[dry-run] no restart", dry_run=True)
        else:
            rok, rdetail = restart_shim()
            restarted = rok
            page_line = _page_leo(reason, consecutive, rdetail, dry_run=False)
            action = f"{'RESTARTED' if rok else 'RESTART FAILED'} ({rdetail}) | {page_line}"
            if rok:
                consecutive = 0  # cleared by the heal; next cycle re-verifies

    state = {
        "schema": SCHEMA,
        "last_check": _iso(now),
        "ok": ok,
        "reason": reason,
        "consecutive_failures": consecutive,
        "last_ok": _iso(now) if ok else prior.get("last_ok"),
        "last_restart": _iso(now) if restarted else prior.get("last_restart"),
        "restart_count_total": int(prior.get("restart_count_total", 0)) + (1 if restarted else 0),
        "dry_run": dry_run,
    }
    try:
        _write_state(state)
    except Exception as e:
        print(f"[{_iso(now)}] WARN heartbeat write failed: {e}", flush=True)

    # Log every run (concise) — matches the other host probes; the log is the audit trail.
    status = "OK" if ok else "FAIL"
    line = f"[{_iso(now)}] {status} shim :4001 — {reason}"
    if not ok:
        line += f" ({consecutive} consecutive)"
    if action:
        line += f" | {action}"
    print(line, flush=True)
    return 0 if ok or restarted else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
