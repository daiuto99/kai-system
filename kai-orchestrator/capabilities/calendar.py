"""calendar.get_events, calendar.create_event capabilities — via worker-api."""
import httpx

from models import CapabilityResult
from . import capability

_WORKER_BASE = "http://kai-worker-api:8001"


@capability("calendar.get_events")
def get_events(days: int = 7, **_) -> CapabilityResult:
    """Fetch upcoming calendar events from worker-api. days = lookahead window."""
    try:
        r = httpx.get(
            f"{_WORKER_BASE}/calendar/events",
            params={"days": days},
            timeout=15.0,
        )
        if r.status_code != 200:
            return CapabilityResult(ok=False, status="failed_recoverable",
                                    error={"type": "worker_error", "status": r.status_code, "body": r.text[:200]})
        try:
            data = r.json()
        except Exception:
            return CapabilityResult(ok=False, status="failed_recoverable",
                                    error={"type": "parse_error", "body": r.text[:200]})
        events = data.get("events", data) if isinstance(data, dict) else data
        return CapabilityResult(ok=True, status="succeeded",
                                data={"days": days, "events": events, "count": len(events)},
                                verification={"verified": True, "method": "calendar_ok"})
    except httpx.RequestError as e:
        return CapabilityResult(ok=False, status="failed_recoverable",
                                error={"type": "connection_error", "detail": str(e)})


@capability("calendar.create_event")
def create_event(title: str, start: str, end: str,
                 description: str = "", location: str = "", **_) -> CapabilityResult:
    """Create a calendar event via worker-api.
    start/end: ISO 8601 strings (e.g. '2026-05-20T14:00:00').
    """
    payload = {
        "title": title,
        "start": start,
        "end": end,
        "description": description,
        "location": location,
    }
    try:
        r = httpx.post(
            f"{_WORKER_BASE}/calendar/events",
            json=payload,
            timeout=15.0,
        )
        if r.status_code not in (200, 201):
            return CapabilityResult(ok=False, status="failed_recoverable",
                                    error={"type": "worker_error", "status": r.status_code, "body": r.text[:200]})
        try:
            data = r.json()
        except Exception:
            data = {}
        event_id = data.get("event_id") or data.get("id")
        if not event_id:
            return CapabilityResult(
                ok=False, status="failed_recoverable",
                error={"type": "no_event_id", "response": str(data)[:200]},
            )
        return CapabilityResult(ok=True, status="succeeded",
                                data={"event_id": event_id, "title": title,
                                      "start": start, "end": end,
                                      "link": data.get("link")},
                                verification={"verified": True, "method": "create_ok"})
    except httpx.RequestError as e:
        return CapabilityResult(ok=False, status="failed_recoverable",
                                error={"type": "connection_error", "detail": str(e)})
