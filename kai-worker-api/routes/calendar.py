import json
import logging
import re
from datetime import datetime as _dt, timezone as _tz, timedelta as _td
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
    unfolded = []
    for raw in ics_text.splitlines():
        if raw and raw[0] in (" ", "\t") and unfolded:
            unfolded[-1] += raw[1:]
        else:
            unfolded.append(raw)

    TZ_OFFSETS = {
        "Eastern Standard Time": -5, "Eastern Daylight Time": -4,
        "Central Standard Time": -6, "Mountain Standard Time": -7,
        "Pacific Standard Time": -8,
    }

    def _parse_dt(prop: str, val: str):
        offset_h = 0
        tzid_match = re.search(r"TZID=([^:]+)", prop)
        if tzid_match:
            tzname = tzid_match.group(1)
            offset_h = TZ_OFFSETS.get(tzname, 0)
        if val.endswith("Z"):
            val = val[:-1] + "+00:00"
        try:
            if "T" in val:
                naive = _dt.strptime(val[:15], "%Y%m%dT%H%M%S")
                return (naive - _td(hours=offset_h)).replace(tzinfo=_tz.utc), False
            else:
                return _dt.strptime(val[:8], "%Y%m%d").replace(tzinfo=_tz.utc), True
        except Exception:
            return None, False

    now = _dt.now(_tz.utc)
    cutoff = now + _td(days=days)
    events, current, in_event = [], {}, False

    for line in unfolded:
        if line == "BEGIN:VEVENT":
            in_event, current = True, {}
        elif line == "END:VEVENT" and in_event:
            in_event = False
            start = current.get("start")
            if start and now <= start <= cutoff:
                events.append(current)
        elif in_event:
            if ":" not in line:
                continue
            prop, _, val = line.partition(":")
            prop_name = prop.split(";")[0].upper()
            if prop_name == "SUMMARY":
                current["title"] = val.replace("\\,", ",").replace("\\n", " ").strip()
            elif prop_name == "DTSTART":
                dt, all_day = _parse_dt(prop, val)
                if dt:
                    current["start"] = dt
                    current["all_day"] = all_day
            elif prop_name == "DTEND":
                dt, _ = _parse_dt(prop, val)
                if dt:
                    current["end"] = dt.isoformat()
            elif prop_name == "LOCATION":
                current["location"] = val.replace("\\,", ",").strip()
            elif prop_name == "DESCRIPTION":
                current["preview"] = val.replace("\\n", " ")[:120].strip()
            elif prop_name == "ORGANIZER":
                m = re.search(r"CN=([^;:]+)", prop + ":" + val)
                if m:
                    current["organizer"] = m.group(1)

    events.sort(key=lambda e: e.get("start", now))
    for e in events:
        if isinstance(e.get("start"), _dt):
            e["start"] = e["start"].isoformat()
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


@router.get("/calendar/events")
def gcal_events(days: int = 7, calendar_id: str = "primary"):
    import datetime
    svc = _gcal_service()
    if not svc:
        return {"events": [], "error": "calendar not configured"}
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
