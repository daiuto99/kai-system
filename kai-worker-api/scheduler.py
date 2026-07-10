"""
kai-scheduler — Sprint 8
- Telegram long polling (routes messages to KAI council)
"""
import time
import logging
import threading
import os
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
import httpx
from watchdog import run_watchdog_checks

logging.basicConfig(level=logging.INFO, format="%(asctime)s [scheduler] %(message)s")
log = logging.getLogger(__name__)

WORKER_API  = "http://kai-worker-api:8001"
COUNCIL_API = "http://kai-council-api:8002"
VAULT_PATH  = Path("/vault")


def load_secret(name: str) -> str:
    p = Path(f"/run/secrets/{name}")
    if p.exists():
        return p.read_text().strip()
    return os.environ.get(name.upper(), "")


# ── Slack helpers ──────────────────────────────────────────────────────────────

def slack_post(token: str, channel: str, text: str,
               username: str = "KAI", icon_url: str = "https://kai.sonicink.space/icon-192.png"):
    try:
        r = httpx.post(
            "https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {token}"},
            json={"channel": channel, "text": text, "username": username, "icon_url": icon_url},
            timeout=15,
        )
        return r.json()
    except Exception as e:
        log.error(f"Slack post error: {e}")
        return {"ok": False}


def slack_get_user_id(token: str, email: str) -> str | None:
    try:
        r = httpx.get(
            "https://slack.com/api/users.lookupByEmail",
            headers={"Authorization": f"Bearer {token}"},
            params={"email": email},
            timeout=10,
        )
        d = r.json()
        if d.get("ok"):
            return d["user"]["id"]
    except Exception as e:
        log.error(f"Slack user lookup error: {e}")
    return None


def slack_open_dm(token: str, user_id: str) -> str | None:
    try:
        r = httpx.post(
            "https://slack.com/api/conversations.open",
            headers={"Authorization": f"Bearer {token}"},
            json={"users": user_id},
            timeout=10,
        )
        d = r.json()
        if d.get("ok"):
            return d["channel"]["id"]
    except Exception as e:
        log.error(f"Slack DM open error: {e}")
    return None


# ── Telegram Long Polling ──────────────────────────────────────────────────────

def tg_send(token: str, chat_id: int, text: str):
    try:
        httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
            timeout=15,
        )
    except Exception as e:
        log.error(f"Telegram send error: {e}")


def telegram_poll_loop():
    token = load_secret("telegram_bot_token")
    if not token:
        log.warning("No telegram_bot_token — Telegram polling disabled")
        return

    log.info("Telegram polling started (@Kai_sonicink_bot)")
    offset = 0

    while True:
        try:
            r = httpx.get(
                f"https://api.telegram.org/bot{token}/getUpdates",
                params={"offset": offset, "timeout": 25, "allowed_updates": ["message"]},
                timeout=35,
            )
            r.raise_for_status()
            updates = r.json().get("result", [])

            for upd in updates:
                offset = upd["update_id"] + 1
                msg = upd.get("message")
                if not msg:
                    continue
                chat_id = msg.get("chat", {}).get("id")
                text = msg.get("text", "").strip()
                username = msg.get("from", {}).get("username", "unknown")
                if not text or not chat_id:
                    continue
                if text == "/start":
                    tg_send(token, chat_id, "🤖 *KAI online.* Send me a message.")
                    continue
                log.info(f"Telegram @{username} ({chat_id}): {text[:60]}")
                try:
                    resp = httpx.post(
                        f"{COUNCIL_API}/message",
                        json={"channel": "kai", "message": text, "user_id": f"telegram:{username}"},
                        timeout=90,
                    )
                    resp.raise_for_status()
                    reply = resp.json().get("reply", "No response.")
                except Exception as e:
                    log.error(f"Council API error (Telegram): {e}")
                    reply = "⚠️ KAI is temporarily unavailable."
                tg_send(token, chat_id, reply)

        except httpx.TimeoutException:
            pass  # Normal — no updates in poll window
        except Exception as e:
            log.error(f"Telegram poll error: {e}")
            time.sleep(5)


# ── Calendar-aware location ─────────────────────────────────────────────���──────

def _update_location_from_calendar():
    """Check today's/tomorrow's calendar events for a location change and update current_location.json."""
    try:
        import json as _j
        loc_file = VAULT_PATH / "00_System" / "current_location.json"
        current_tz = "America/New_York"
        current_city = ""
        if loc_file.exists():
            _cur = _j.loads(loc_file.read_text())
            current_tz = _cur.get("timezone", current_tz)
            current_city = _cur.get("city", "")  # noqa: F841

        # Fetch Google Calendar events (today + tomorrow)
        try:
            r = httpx.post("http://kai-n8n:5678/webhook/kai-calendar-events",
                           json={"days": 2}, timeout=15)
            if r.status_code != 200 or not r.content.strip():
                return
            events = r.json() if isinstance(r.json(), list) else []
        except Exception as e:
            log.debug(f"Calendar location check: {e}")
            return

        # Find first event with a non-empty location field, sorted by start time
        events_with_loc = []
        for ev in events:
            loc = (ev.get("location") or "").strip()
            if not loc:
                continue
            start = ev.get("start", {})
            start_str = start.get("dateTime", start.get("date", "")) if isinstance(start, dict) else str(start)
            events_with_loc.append((start_str, loc))
        events_with_loc.sort(key=lambda x: x[0])

        if not events_with_loc:
            return

        target_loc = events_with_loc[0][1]

        # Geocode via Nominatim
        geo_r = httpx.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": target_loc, "format": "json", "limit": 1},
            headers={"User-Agent": "KAI-Scheduler/1.0 (sonicink.space)"},
            timeout=10,
        )
        if geo_r.status_code != 200 or not geo_r.json():
            log.warning(f"Nominatim returned no result for: {target_loc}")
            return
        geo = geo_r.json()[0]
        lat, lon = float(geo["lat"]), float(geo["lon"])

        # Get timezone from coordinates
        tz_r = httpx.get(
            "https://timeapi.io/api/TimeZone/coordinate",
            params={"latitude": lat, "longitude": lon},
            timeout=10,
        )
        if tz_r.status_code != 200:
            log.warning(f"TimeAPI failed for {lat},{lon}")
            return
        new_tz = tz_r.json().get("timeZone", "America/New_York")

        if new_tz == current_tz:
            return  # No timezone change — nothing to do

        data = {
            "lat": lat,
            "lon": lon,
            "accuracy": None,
            "timezone": new_tz,
            "city": target_loc,
            "source": "calendar",
            "updated": datetime.now(ZoneInfo(new_tz)).isoformat(),
        }
        loc_file.write_text(_j.dumps(data, indent=2))
        log.info(f"Location updated from calendar: {target_loc} → tz={new_tz}")

    except Exception as e:
        log.warning(f"Calendar location update failed: {e}")



# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    log.info("kai-scheduler started — Telegram polling")

    tg_thread = threading.Thread(target=telegram_poll_loop, daemon=True, name="telegram-poll")
    tg_thread.start()

    _last_watchdog_minute = -1
    while True:
        try:
            import json as _ljson
            _ldata = _ljson.loads((VAULT_PATH / "00_System" / "current_location.json").read_text())
            _leo_tz = ZoneInfo(_ldata.get("timezone", "America/New_York"))
        except Exception:
            _leo_tz = ZoneInfo("America/New_York")
        now = datetime.now(_leo_tz)
        # Watchdog: run every 30 minutes at :00 and :30
        if now.minute in (0, 30) and now.minute != _last_watchdog_minute:
            _last_watchdog_minute = now.minute
            try:
                run_watchdog_checks()
            except Exception as e:
                log.error(f"Watchdog error: {e}")
            try:
                _update_location_from_calendar()
            except Exception as e:
                log.error(f"Calendar location check error: {e}")
        time.sleep(30)


if __name__ == "__main__":
    main()
