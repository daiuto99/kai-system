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

router = APIRouter()

_LOG = Path("/vault/00_System/notify_log.jsonl")


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
