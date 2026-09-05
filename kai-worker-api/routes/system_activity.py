"""System-activity feed — surfaces the notify() gateway audit (notify_log.jsonl)
on the dashboard System tab. COMMS Phase 3.

Every Leo-facing decision KAI's single gateway made is recorded (Rule A): what was
delivered, what was suppressed (synthetic / dedup), what stayed on the dashboard.
This route reads that log so the dashboard can show it — read-only, no side effects.

L18: the log already stores only truncated titles + decision metadata (never a token
or a bot URL), so passing records straight through is safe.
"""
import json
from pathlib import Path

from fastapi import APIRouter, Query
from pydantic import BaseModel

import notify_gateway as ng

router = APIRouter()

_LOG = Path("/vault/00_System/notify_log.jsonl")


class NotifyRequest(BaseModel):
    """Autonomous-action FYI. `channel` is legacy — callers historically passed
    'devops'; operational/DevOps activity is KAI's to log, so it lands on the
    dashboard System tab (the Leo-visible surface) regardless."""
    text: str
    channel: str = "dashboard"
    source: str = "autonomous"
    kind: str = "alert"


@router.post("/notify")
def post_notify(body: NotifyRequest):
    """Autonomous-action FYI sink. Routes through the single notify gateway to the
    dashboard System tab — the Leo-visible surface — NOT the retired #devops Slack
    channel. Replaces a dead /notify (404) that made sprint_watchdog, the async
    close-complete FYI, and sprint_runner all fail SILENTLY. L18: the gateway logs
    only a truncated title + decision metadata (never a token or bot URL)."""
    res = ng.notify(ng.Event(
        source=body.source, kind=body.kind, title=body.text,
        audience="dashboard", provenance="real"))
    return {"ok": True, "decision": res.decision, "destination": res.destination}


def _tail_lines(path: Path, n: int) -> list[str]:
    if not path.exists():
        return []
    try:
        lines = path.read_text(errors="replace").splitlines()
    except Exception:
        return []
    return lines[-n:]


@router.get("/system/activity")
def system_activity(limit: int = Query(200, ge=1, le=2000)):
    """Recent gateway decisions, newest first, with a decision-count summary."""
    records = []
    for ln in _tail_lines(_LOG, limit):
        ln = ln.strip()
        if not ln:
            continue
        try:
            r = json.loads(ln)
        except Exception:
            continue
        records.append({
            "ts":        r.get("ts"),
            "event":     r.get("event"),
            "reason":    r.get("reason"),
            "decision":  r.get("decision"),
            "delivered": r.get("delivered"),
            "title":     (r.get("title") or "").replace("\n", " ").strip()[:240],
        })
    records.reverse()  # newest first

    summary: dict[str, int] = {}
    for r in records:
        d = r.get("decision") or "unknown"
        summary[d] = summary.get(d, 0) + 1

    return {
        "records": records,
        "summary": summary,
        "count": len(records),
        "log_present": _LOG.exists(),
    }
