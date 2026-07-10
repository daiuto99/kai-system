"""
HabitSync service — proxies calls to self-hosted HabitSync instance.
Auth: Basic Auth (kai / password from Docker secret).
"""
import os
import base64
import httpx
from datetime import date, timedelta

HABITSYNC_URL = os.getenv("HABITSYNC_URL", "http://kai-habitsync:6842")
_PASSWORD_FILE = "/run/secrets/habitsync_password"

def _auth_header() -> dict:
    try:
        with open(_PASSWORD_FILE) as f:
            password = f.read().strip()
    except FileNotFoundError:
        password = os.getenv("HABITSYNC_PASSWORD", "")
    token = base64.b64encode(f"kai:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def _extract_emoji(name: str) -> tuple[str, str]:
    """Return (emoji, display_name). Extracts leading emoji from name like '🎸 Guitar'."""
    if not name:
        return "", ""
    parts = name.split(None, 1)
    if parts and ord(parts[0][0]) > 0x00FF:
        return parts[0], parts[1] if len(parts) > 1 else parts[0]
    return "", name

def _today_epoch() -> int:
    return date.today().toordinal() - date(1970, 1, 1).toordinal()

def _date_from_epoch(epoch_day: int) -> str:
    return (date(1970, 1, 1) + timedelta(days=epoch_day)).isoformat()

def get_habits() -> list:
    """Return all habits with 7-day completion history."""
    headers = _auth_header()
    with httpx.Client(timeout=10) as client:
        r = client.get(f"{HABITSYNC_URL}/api/habit/list", headers=headers)
        r.raise_for_status()
        habits = r.json()

        today = date.today()  # noqa: F841
        epoch_to = _today_epoch()
        epoch_from = epoch_to - 6

        result = []
        for h in habits:
            uuid = h.get("uuid") or h.get("id")
            # Get last 7 days of records
            rr = client.get(
                f"{HABITSYNC_URL}/api/record/{uuid}",
                params={"epochDayFrom": epoch_from, "epochDayTo": epoch_to},
                headers=headers,
            )
            records = rr.json() if rr.status_code == 200 else []
            completions = [
                _date_from_epoch(rec["epochDay"])
                for rec in records
                if rec.get("recordValue", 0) > 0
            ]
            emoji, display_name = _extract_emoji(h.get("name", ""))
            result.append({
                "id": uuid,
                "name": h.get("name", ""),
                "displayName": display_name or h.get("name", ""),
                "emoji": emoji,
                "color": h.get("color", 0),
                "group": h.get("group", ""),
                "completions": completions,
            })
    return result

def log_habit(habit_uuid: str) -> dict:
    """Mark habit complete for today."""
    headers = _auth_header()
    with httpx.Client(timeout=10) as client:
        r = client.post(
            f"{HABITSYNC_URL}/api/record/{habit_uuid}/simple",
            params={"value": 1.0},
            headers=headers,
        )
        r.raise_for_status()
        epoch_day = r.json().get("epochDay", _today_epoch())
        return {"date": _date_from_epoch(epoch_day), "value": 1.0}

def unlog_habit(habit_uuid: str) -> dict:
    """Undo today's completion (set value to 0)."""
    headers = _auth_header()
    with httpx.Client(timeout=10) as client:
        r = client.post(
            f"{HABITSYNC_URL}/api/record/{habit_uuid}/simple",
            params={"value": 0.0},
            headers=headers,
        )
        r.raise_for_status()
        return {"date": date.today().isoformat(), "value": 0.0}





def create_habit(name: str) -> dict:
    """Create a new habit in HabitSync."""
    headers = _auth_header()
    with httpx.Client(timeout=10) as client:
        r = client.post(
            f"{HABITSYNC_URL}/api/habit",
            json={"name": name},
            headers=headers,
        )
        r.raise_for_status()
        h = r.json()
        uuid = h.get("uuid") or h.get("id")
        emoji, display_name = _extract_emoji(h.get("name", ""))
        return {
            "id": uuid,
            "name": h.get("name", ""),
            "displayName": display_name or h.get("name", ""),
            "emoji": emoji,
            "color": h.get("color", 0),
            "group": h.get("group", ""),
            "completions": [],
        }

def update_habit(habit_uuid: str, emoji: str, name: str) -> dict:
    full_name = (emoji + " " + name).strip() if emoji else name
    headers = _auth_header()
    with httpx.Client(timeout=10) as client:
        r = client.put(
            f"{HABITSYNC_URL}/api/habit/{habit_uuid}",
            json={"name": full_name},
            headers=headers,
        )
        r.raise_for_status()
        h = r.json()
        em, dn = _extract_emoji(h.get("name", ""))
        return {
            "id": habit_uuid,
            "name": h.get("name", ""),
            "displayName": dn or h.get("name", ""),
            "emoji": em,
            "color": h.get("color", 0),
        }
