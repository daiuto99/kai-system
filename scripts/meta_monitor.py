#!/usr/bin/env python3
"""KAI-1115 · [M-R1] Meta-monitor — the watcher of the watchers.

The KAI-1108 failure class applied to MONITORING ITSELF: a probe asserts nothing
when it is DEAD. advisor_dm_probe (KAI-1110) pages when the advisor path breaks —
but if the probe's own cron is removed, its process crashes, or it hangs, it goes
silent and NOTHING notices. Leo's advisor DMs died silently for 11 days precisely
because no independent process re-derived health from observed liveness. This is
that independent process, one level up: it watches the PROBES' heartbeats and pages
when any goes stale, so a deleted/dead probe cannot die silently.

Design pins:
  • INDEPENDENT of every probe's success path. It reads their heartbeat FILES and
    checks freshness — it does not import them, share their cron, or depend on their
    liveness. A probe whose file stops advancing (cron removed / process crashed /
    hung) crosses its max-age window and pages on the very next meta-monitor tick.
  • Separate PROCESS on the worker host (its own cron, */5). The residual it cannot
    cover is TOTAL WORKER DEATH — if the box dies, this dies with it. That off-box
    watcher lands on 71-kai-mini when M-R3 reimages it (tracked follow-up); until
    then green_baseline reads this monitor's own heartbeat at session start.
  • The page is provenance='real' (an actually-dead probe is a real outage) and
    audience='personal' so it reaches Leo's phone — a synthetic-stamped alarm would
    be suppressed by the gateway and silently re-create KAI-1108 one level up.
  • The page carries an explicit CAUSE (Findings Contract): a stale heartbeat means
    the named probe's process/cron stopped or hung — never a bare uncaused alarm.
  • Read-only w.r.t. the system: reads heartbeat files, writes its OWN heartbeat.
    Never restarts, mutates, or provisions anything. Never raises (fail-soft).

The REGISTRY is code (not external data) on purpose: removing a probe's watch is a
source change caught by source_drift/green_baseline — you cannot quietly un-watch a
probe. Adding a new probe to the fleet = one registry line, no other code change.
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "shared"))  # /shared convention (notify_gateway lives here)

SCHEMA = "kai.meta_monitor.v1"
_VAULT_CANDIDATES = (Path("/home/leo/vault"), Path("/vault"))
STATE_FILENAME = "_meta_monitor_state.json"


def _vault_dir() -> Path:
    for c in _VAULT_CANDIDATES:
        if c.exists():
            return c
    return _VAULT_CANDIDATES[0]


# ── The registry — the probes this monitor guarantees are alive ────────────────
#
# Each entry: a probe's heartbeat file + how to read its freshness + the max age
# past which the probe is presumed dead. max_age is derived from the probe's cron
# cadence with slack: a probe that runs every N sec is "stale" only after it has
# missed ~2-3 cycles, so a single skipped/slow tick never pages.
#
#   kind="iso_field"  : JSON file; `field` holds an ISO-8601 UTC timestamp.
#   kind="epoch_raw"  : whole file is a single unix-epoch integer.
#
def _registry() -> list[dict]:
    v = _vault_dir()
    return [
        {
            "name": "advisor_dm_probe",
            "ticket": "KAI-1110",
            "path": v / "_advisor_probe_state.json",
            "kind": "iso_field",
            "field": "last_probe",
            "cadence_sec": 900,        # */15 host cron
            "max_age_sec": 900 * 2 + 300,   # 35 min — ~2 missed cycles + slack
        },
        {
            "name": "fleet_heartbeat",
            "ticket": "KAI-1047",
            "path": v / "_fleet_state.json",
            "kind": "iso_field",
            "field": "updated",
            "cadence_sec": 180,        # */3 host cron
            "max_age_sec": 180 * 3 + 120,   # 11 min — ~3 missed cycles + slack
        },
        {
            "name": "buzz_approve_loop",
            "ticket": "KAI-1108",
            "path": v / "00_System" / "buzz_approve_heartbeat",
            "kind": "epoch_raw",
            "field": None,
            "cadence_sec": 5,          # written every poll cycle (seconds)
            "max_age_sec": 300,        # 5 min — the loop whose silent death was KAI-1108
        },
    ]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _parse_epoch(entry: dict) -> float | None:
    """Return the heartbeat's epoch seconds, or None if unreadable/missing."""
    p: Path = entry["path"]
    if not p.exists():
        return None
    try:
        raw = p.read_text().strip()
    except Exception:
        return None
    if entry["kind"] == "epoch_raw":
        try:
            return float(raw)
        except Exception:
            return None
    # iso_field
    try:
        data = json.loads(raw)
        ts = data.get(entry["field"])
        if not ts:
            return None
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return None


def check_one(entry: dict, now_epoch: float) -> dict:
    """Freshness verdict for one probe. Never raises."""
    epoch = _parse_epoch(entry)
    if epoch is None:
        return {"name": entry["name"], "ticket": entry["ticket"], "ok": False,
                "age_sec": None, "max_age_sec": entry["max_age_sec"],
                "reason": f"heartbeat unreadable/missing at {entry['path']}"}
    age = now_epoch - epoch
    if age > entry["max_age_sec"]:
        return {"name": entry["name"], "ticket": entry["ticket"], "ok": False,
                "age_sec": round(age), "max_age_sec": entry["max_age_sec"],
                "reason": (f"heartbeat stale: {round(age)}s old > {entry['max_age_sec']}s max "
                           f"(cadence {entry['cadence_sec']}s)")}
    return {"name": entry["name"], "ticket": entry["ticket"], "ok": True,
            "age_sec": round(age), "max_age_sec": entry["max_age_sec"],
            "reason": f"fresh: {round(age)}s old"}


def _page(result: dict, dry_run: bool) -> str:
    """Page Leo that a probe is presumed dead. provenance='real', audience='personal',
    and an explicit cause — a silent probe is a real outage one level up."""
    name = result["name"]
    ticket = result["ticket"]
    title = f"Probe DOWN — {name} heartbeat stale (meta-monitor)"
    body = (f"The meta-monitor found no fresh heartbeat from probe '{name}' ({ticket}).\n"
            f"{result['reason']}\n"
            f"A probe that stops writing its heartbeat is presumed dead — this is the "
            f"KAI-1108 failure class applied to monitoring itself.")
    cause = (f"probe '{name}' process/cron stopped or hung — heartbeat file not updated "
             f"within {result['max_age_sec']}s")
    if dry_run:
        return f"[dry-run] would page: {title} | cause: {cause}"
    try:
        import os
        # Host-cron context: point BOTH the audit log and the dedup store at the host
        # vault (the container defaults to /vault/..., unwritable here). Without the
        # dedup path a sustained outage re-pages every 5-min tick instead of hourly.
        os.environ.setdefault("KAI_NOTIFY_LOG", str(_vault_dir() / "00_System" / "notify_log.jsonl"))
        os.environ.setdefault("KAI_NOTIFY_DEDUP", str(_vault_dir() / "00_System" / "notify_dedup.json"))
        from notify_gateway import notify, Event
        bucket = _now().strftime("%Y-%m-%dT%H")   # hour-bucketed dedup: persistent, not spammy
        res = notify(Event(
            source="meta_monitor",
            kind="alert",
            title=title,
            body=body,
            audience="personal",
            actionable=True,
            provenance="real",
            status="stale",
            cause=cause,
            dedup_key=f"meta_monitor_stale:{name}:{bucket}",
        ))
        return f"paged: decision={res.decision} dest={res.destination} delivered={res.delivered}"
    except Exception as e:
        return f"PAGE FAILED: {type(e).__name__}: {e}"


def _write_state(state: dict) -> None:
    """Atomic write so a reader never sees a half-written heartbeat."""
    p = _vault_dir() / STATE_FILENAME
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(p)


def main(argv: list[str]) -> int:
    dry_run = "--dry-run" in argv
    now = _now()
    now_epoch = now.timestamp()

    results = [check_one(e, now_epoch) for e in _registry()]
    stale = [r for r in results if not r["ok"]]

    pages = []
    for r in stale:
        pages.append({"name": r["name"], "result": _page(r, dry_run)})

    state = {
        "schema": SCHEMA,
        "last_run": _iso(now),
        "checked": [r["name"] for r in results],
        "stale": [r["name"] for r in stale],
        "results": results,
        "paged": [p for p in pages if p["result"].startswith("paged")],
        "dry_run": dry_run,
    }
    try:
        _write_state(state)
    except Exception as e:
        print(f"[{_iso(now)}] WARN heartbeat write failed: {e}", flush=True)

    status = "OK" if not stale else f"STALE({len(stale)})"
    line = f"[{_iso(now)}] {status} checked={len(results)}"
    for r in results:
        line += f" | {r['name']}={'ok' if r['ok'] else 'STALE'}({r['age_sec']}s)"
    for p in pages:
        line += f" || {p['name']}: {p['result']}"
    print(line, flush=True)
    return 0 if not stale else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
