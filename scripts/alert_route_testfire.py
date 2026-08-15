#!/usr/bin/env python3
"""KAI-1115 · [M-R1] Alert-route test-fire — prove every route can deliver.

A monitor is only as good as the route its page travels. A dead alert route
(revoked Telegram token, broken dashboard writer) means a real outage pages into
the void — the same silent-failure shape as KAI-1108, one layer further out. This
proactively exercises EVERY Leo-facing alert route on a monthly cadence and proves
delivery, so a broken route is found by a scheduled test-fire, not by a real
outage that never reaches anyone.

Routes proven (the two the notify() gateway exposes — COMMS Rule B):
  • telegram   — audience='personal' → Leo's phone. Proof = NotifyResult.delivered
                 (the Telegram API returned ok). This is a REAL delivered message,
                 clearly stamped [test-fire] so it reads as expected monthly noise.
  • dashboard  — audience='dashboard' → notify_log.jsonl (System-tab source). The
                 gateway reports dashboard sends as delivered=False by design (not
                 pushed to Leo), so proof here = decision=='dashboard_only' AND the
                 uniquely-tokened record is read back from notify_log.jsonl.

Each run carries a unique token so the telegram send is never dedup-suppressed and
the dashboard record is unambiguously findable. Read-only w.r.t. the system apart
from its own state file; never raises (fail-soft). Exits non-zero if any route
fails to deliver, so the cron log and the state file both carry the verdict.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "shared"))

SCHEMA = "kai.alert_testfire.v1"
_VAULT_CANDIDATES = (Path("/home/leo/vault"), Path("/vault"))
STATE_FILENAME = "_alert_testfire_state.json"


def _vault_dir() -> Path:
    for c in _VAULT_CANDIDATES:
        if c.exists():
            return c
    return _VAULT_CANDIDATES[0]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _log_path() -> Path:
    return _vault_dir() / "00_System" / "notify_log.jsonl"


def _readback_dashboard(token: str) -> bool:
    """Confirm the dashboard test-fire record actually landed in notify_log.jsonl."""
    p = _log_path()
    if not p.exists():
        return False
    try:
        # Scan only the tail — the record we just wrote is at the end.
        lines = p.read_text().splitlines()[-50:]
    except Exception:
        return False
    for ln in reversed(lines):
        if token in ln:
            return True
    return False


def fire_telegram(token: str, dry_run: bool) -> dict:
    """Fire the telegram route with a real, delivered, [test-fire]-stamped message."""
    if dry_run:
        return {"route": "telegram", "ok": True, "decision": "dry_run",
                "delivered": False, "detail": "dry-run: not sent"}
    os.environ.setdefault("KAI_NOTIFY_LOG", str(_log_path()))
    os.environ.setdefault("KAI_NOTIFY_DEDUP", str(_vault_dir() / "00_System" / "notify_dedup.json"))
    try:
        from notify_gateway import notify, Event
        res = notify(Event(
            source="alert_route_testfire",
            kind="alert",
            title="[test-fire] telegram route",
            body=(f"Monthly alert-route test-fire (KAI-1115). If you see this, the "
                  f"telegram alert route is live. Token: {token}. No action needed."),
            audience="personal",
            actionable=False,
            provenance="real",
            dedup_key=f"alert_testfire:telegram:{token}",  # unique -> never dedup-suppressed
        ))
        return {"route": "telegram", "ok": bool(res.delivered), "decision": res.decision,
                "delivered": bool(res.delivered), "detail": f"dest={res.destination} reason={res.reason}"}
    except Exception as e:
        return {"route": "telegram", "ok": False, "decision": "exception",
                "delivered": False, "detail": f"{type(e).__name__}: {e}"}


def fire_dashboard(token: str, dry_run: bool) -> dict:
    """Fire the dashboard route and prove the record reached notify_log.jsonl."""
    if dry_run:
        return {"route": "dashboard", "ok": True, "decision": "dry_run",
                "delivered": False, "detail": "dry-run: not sent"}
    os.environ.setdefault("KAI_NOTIFY_LOG", str(_log_path()))
    os.environ.setdefault("KAI_NOTIFY_DEDUP", str(_vault_dir() / "00_System" / "notify_dedup.json"))
    try:
        from notify_gateway import notify, Event
        res = notify(Event(
            source="alert_route_testfire",
            kind="alert",
            title=f"[test-fire] dashboard route {token}",
            body="Monthly alert-route test-fire (KAI-1115) — dashboard/System-tab route.",
            audience="dashboard",
            actionable=False,
            provenance="real",
        ))
        landed = _readback_dashboard(token)
        ok = (res.decision == "dashboard_only") and landed
        return {"route": "dashboard", "ok": ok, "decision": res.decision,
                "delivered": landed,   # for dashboard, "delivered" == record read back from the log
                "detail": f"logged_readback={landed} reason={res.reason}"}
    except Exception as e:
        return {"route": "dashboard", "ok": False, "decision": "exception",
                "delivered": False, "detail": f"{type(e).__name__}: {e}"}


def _write_state(state: dict) -> None:
    p = _vault_dir() / STATE_FILENAME
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(p)


def main(argv: list[str]) -> int:
    dry_run = "--dry-run" in argv
    now = _now()
    token = f"tf-{int(now.timestamp())}-{os.getpid()}"  # unique per run

    results = [fire_telegram(token, dry_run), fire_dashboard(token, dry_run)]
    all_ok = all(r["ok"] for r in results)

    state = {
        "schema": SCHEMA,
        "last_run": _iso(now),
        "token": token,
        "all_routes_ok": all_ok,
        "routes": results,
        "dry_run": dry_run,
    }
    try:
        _write_state(state)
    except Exception as e:
        print(f"[{_iso(now)}] WARN state write failed: {e}", flush=True)

    verdict = "ALL ROUTES OK" if all_ok else "ROUTE FAILURE"
    line = f"[{_iso(now)}] {verdict} token={token}"
    for r in results:
        line += f" | {r['route']}={'ok' if r['ok'] else 'FAIL'}({r['decision']}) {r['detail']}"
    print(line, flush=True)
    return 0 if all_ok else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
