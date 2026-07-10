import json
import logging
import re
from datetime import datetime as _dt, timezone as _tz, timedelta as _td
from zoneinfo import ZoneInfo
_ET = ZoneInfo("America/New_York")
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from config import VAULT_PATH

logger = logging.getLogger(__name__)
router = APIRouter()

GCAL_CREDS_FILE  = VAULT_PATH / "00_System" / "google_calendar_token.json"
GCAL_CLIENT_FILE = VAULT_PATH / "00_System" / "google_calendar_client.json"
GCAL_SCOPES = ["https://www.googleapis.com/auth/calendar.readonly",
               "https://www.googleapis.com/auth/calendar.events"]
ICS_FEEDS_FILE = VAULT_PATH / "00_System" / "ics_feeds.json"


def _gcal_service():
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build

        if not GCAL_CREDS_FILE.exists():
            return None
        creds = Credentials.from_authorized_user_file(str(GCAL_CREDS_FILE), GCAL_SCOPES)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            GCAL_CREDS_FILE.write_text(creds.to_json())
        return build("calendar", "v3", credentials=creds)
    except Exception as e:
        logger.exception("gcal service error: %s", e)
        return None


def _load_ics_feeds() -> dict:
    if ICS_FEEDS_FILE.exists():
        return json.loads(ICS_FEEDS_FILE.read_text())
    return {}


def _save_ics_feeds(feeds: dict):
    ICS_FEEDS_FILE.write_text(json.dumps(feeds, indent=2))


def _parse_ics(ics_text: str, days: int = 7) -> list:
    try:
        from icalendar import Calendar
        import recurring_ical_events
    except ImportError:
        logger.error("icalendar/recurring_ical_events not installed")
        return []

    now = _dt.now(_tz.utc)
    end = now + _td(days=days)
    _local = _ET

    try:
        cal = Calendar.from_ical(ics_text)
    except Exception as e:
        logger.exception("ics parse error: %s", e)
        return []

    try:
        occurrences = recurring_ical_events.of(cal).between(now, end)
    except Exception as e:
        logger.exception("ics recurring expand error: %s", e)
        return []

    events = []
    for component in occurrences:
        if component.name != "VEVENT":
            continue
        dtstart = component.get("DTSTART")
        if not dtstart:
            continue
        start_raw = dtstart.dt
        all_day = not hasattr(start_raw, "hour")
        if all_day:
            start_dt = _dt(start_raw.year, start_raw.month, start_raw.day, tzinfo=_tz.utc)
        else:
            start_dt = (start_raw.astimezone(_local) if start_raw.tzinfo else start_raw.replace(tzinfo=_tz.utc).astimezone(_local))

        event = {"title": str(component.get("SUMMARY", "")).strip(), "start": start_dt.isoformat(), "all_day": all_day}

        dtend = component.get("DTEND")
        if dtend:
            end_raw = dtend.dt
            if hasattr(end_raw, "hour"):
                event["end"] = (end_raw.astimezone(_local) if end_raw.tzinfo else end_raw.replace(tzinfo=_tz.utc).astimezone(_local)).isoformat()
            else:
                event["end"] = _dt(end_raw.year, end_raw.month, end_raw.day, tzinfo=_tz.utc).isoformat()

        loc = str(component.get("LOCATION", "")).strip()
        if loc:
            event["location"] = loc
        desc = str(component.get("DESCRIPTION", ""))[:120].strip()
        if desc:
            event["preview"] = desc
        org = component.get("ORGANIZER")
        if org and hasattr(org, "params"):
            cn = org.params.get("CN", "")
            if cn:
                event["organizer"] = str(cn)

        events.append(event)

    events.sort(key=lambda e: e.get("start", ""))
    return events

@router.get("/calendar/auth-url")
def gcal_auth_url():
    try:
        from google_auth_oauthlib.flow import Flow
        if not GCAL_CLIENT_FILE.exists():
            raise HTTPException(400, "google_calendar_client.json not found in vault")
        flow = Flow.from_client_secrets_file(
            str(GCAL_CLIENT_FILE), scopes=GCAL_SCOPES,
            redirect_uri="urn:ietf:wg:oauth:2.0:oob"
        )
        auth_url, _ = flow.authorization_url(prompt="consent", access_type="offline")
        return {"auth_url": auth_url}
    except Exception as e:
        logger.exception("gcal auth url: %s", e)
        raise HTTPException(500, str(e))


class GCalCodeRequest(BaseModel):
    code: str


@router.post("/calendar/auth-code")
def gcal_auth_code(req: GCalCodeRequest):
    try:
        from google_auth_oauthlib.flow import Flow
        if not GCAL_CLIENT_FILE.exists():
            raise HTTPException(400, "google_calendar_client.json not found in vault")
        flow = Flow.from_client_secrets_file(
            str(GCAL_CLIENT_FILE), scopes=GCAL_SCOPES,
            redirect_uri="urn:ietf:wg:oauth:2.0:oob"
        )
        flow.fetch_token(code=req.code)
        creds = flow.credentials
        GCAL_CREDS_FILE.write_text(creds.to_json())
        return {"ok": True, "message": "Calendar authorized and token saved"}
    except Exception as e:
        logger.exception("gcal auth code: %s", e)
        raise HTTPException(500, str(e))


def _gcal_via_n8n(days: int) -> dict:
    try:
        import httpx as _hx, json as _j
        feeds_path = VAULT_PATH / "00_System" / "n8n_workflows.json"
        reg = _j.loads(feeds_path.read_text()) if feeds_path.exists() else {}
        entry = reg.get("kai-calendar-events")
        if not entry:
            return {"events": [], "error": "calendar not configured"}
        url = entry["webhook_url"] if isinstance(entry, dict) else entry
        r = _hx.post(url, json={"days": days}, timeout=30)
        raw = r.json()
        items = raw if isinstance(raw, list) else raw.get("events", [])
        events = []
        for e in items:
            start_obj = e.get("start", {})
            start = start_obj.get("dateTime", start_obj.get("date", "")) if isinstance(start_obj, dict) else str(start_obj)
            end_obj = e.get("end", {})
            end = end_obj.get("dateTime", end_obj.get("date", "")) if isinstance(end_obj, dict) else str(end_obj)
            events.append({
                "id": e.get("id", ""),
                "title": e.get("summary", "(no title)"),
                "start": start,
                "end": end,
                "location": e.get("location", ""),
                "description": e.get("description", ""),
                "calendar": e.get("calendarName", "primary"),
            })
        return {"events": events}
    except Exception as ex:
        logger.exception("_gcal_via_n8n: %s", ex)
        return {"events": [], "error": str(ex)}


@router.get("/calendar/events")
def gcal_events(days: int = 7, calendar_id: str = "primary"):
    import datetime
    svc = _gcal_service()
    if not svc:
        return _gcal_via_n8n(days)
    now = datetime.datetime.utcnow().isoformat() + "Z"
    end = (datetime.datetime.utcnow() + datetime.timedelta(days=days)).isoformat() + "Z"
    result = svc.events().list(
        calendarId=calendar_id, timeMin=now, timeMax=end,
        maxResults=50, singleEvents=True, orderBy="startTime"
    ).execute()
    events = []
    for e in result.get("items", []):
        start = e["start"].get("dateTime", e["start"].get("date"))
        events.append({
            "id": e["id"],
            "title": e.get("summary", "(no title)"),
            "start": start,
            "end": e["end"].get("dateTime", e["end"].get("date")),
            "location": e.get("location", ""),
            "description": e.get("description", ""),
            "calendar": calendar_id,
        })
    return {"events": events}


class GCalEventCreate(BaseModel):
    title: str
    start: str
    end: str
    description: str = ""
    location: str = ""
    calendar_id: str = "primary"


@router.post("/calendar/events")
def gcal_create_event(body: GCalEventCreate):
    svc = _gcal_service()
    if not svc:
        raise HTTPException(503, "calendar not configured")
    event = {
        "summary": body.title,
        "location": body.location,
        "description": body.description,
        "start": {"dateTime": body.start, "timeZone": "America/New_York"},
        "end":   {"dateTime": body.end,   "timeZone": "America/New_York"},
    }
    created = svc.events().insert(calendarId=body.calendar_id, body=event).execute()
    return {"ok": True, "event_id": created["id"], "link": created.get("htmlLink")}


@router.get("/calendar/ics")
def get_ics_calendars(days: int = 7):
    import httpx as _hx
    feeds = _load_ics_feeds()
    if not feeds:
        return {"events": [], "accounts": [], "note": "No ICS feeds registered. POST /calendar/ics/register to add one."}
    all_events = []
    errors = []
    for name, url in feeds.items():
        try:
            r = _hx.get(url, timeout=10, follow_redirects=True)
            if r.status_code == 200:
                evts = _parse_ics(r.text, days=days)
                for e in evts:
                    e["account"] = name
                all_events.extend(evts)
            else:
                errors.append(f"{name}: HTTP {r.status_code}")
        except Exception as ex:
            logger.exception("ics fetch %s: %s", name, ex)
            errors.append(f"{name}: {str(ex)}")
    all_events.sort(key=lambda e: e.get("start", ""))
    return {"events": all_events, "accounts": list(feeds.keys()), "count": len(all_events), "days": days, "errors": errors}


class ICSFeedRequest(BaseModel):
    name: str
    url: str


@router.post("/calendar/ics/register")
def register_ics_feed(req: ICSFeedRequest):
    feeds = _load_ics_feeds()
    feeds[req.name] = req.url
    _save_ics_feeds(feeds)
    return {"ok": True, "name": req.name, "registered": len(feeds)}


@router.delete("/calendar/ics/{name}")
def remove_ics_feed(name: str):
    feeds = _load_ics_feeds()
    if name not in feeds:
        raise HTTPException(status_code=404, detail=f"Feed not found: {name}")
    del feeds[name]
    _save_ics_feeds(feeds)
    return {"ok": True, "removed": name}


@router.get("/calendar/ics/feeds")
def list_ics_feeds():
    feeds = _load_ics_feeds()
    return {"feeds": list(feeds.keys()), "count": len(feeds)}
