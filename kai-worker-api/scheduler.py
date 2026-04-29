"""
kai-scheduler — Sprint 8
- Morning brief at 7:00 AM ET
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


# ── Morning Brief ──────────────────────────────────────────────────────────────

def build_context() -> str:
    now_et = datetime.now(ZoneInfo("America/New_York"))
    parts = [f"**TODAY IS:** {now_et.strftime('%A, %B %d, %Y')}"]

    # Calendar — Google (filtered: Leo's, Band, Family) + ICS (Revolt, PSU)
    try:
        gcal_events = []
        ics_events = []

        # Google Calendar via internal n8n webhook
        try:
            r = httpx.post("http://kai-n8n:5678/webhook/kai-calendar-events",
                          json={"days": 1}, timeout=15)
            if r.status_code == 200 and r.content.strip():
                gcal_events = r.json() if isinstance(r.json(), list) else []
            else:
                raise ValueError(f"empty or non-200 response: {r.status_code}")
        except Exception as e:
            log.warning(f"Google Calendar fetch: {e}")
            try:
                _tok = load_secret("slack_bot_token")
                if _tok:
                    slack_post(_tok, "#kai-system",
                               f":warning: *Calendar integration down* — Google Calendar fetch failed during morning brief build.\n`{e}`\nAction needed: re-authorize Google Calendar credential in n8n (<https://n8n.sonicink.space|n8n.sonicink.space>)")
            except Exception as _se:
                log.warning(f"Failed to post calendar alert: {_se}")

        # ICS feeds (Revolt + PSU)
        try:
            r = httpx.get(f"{WORKER_API}/calendar/ics?days=1", timeout=10)
            ics_events = r.json().get("events", []) if r.status_code == 200 else []
        except Exception as e:
            log.warning(f"ICS calendar fetch: {e}")

        all_events = []
        for ev in gcal_events:
            start = ev.get("start", {})
            start_str = start.get("dateTime", start.get("date", "")) if isinstance(start, dict) else str(start)
            all_events.append({"start": start_str[:16], "summary": ev.get("summary", ""), "source": "Google"})
        for ev in ics_events:
            summary = ev.get("title", ev.get("summary", "")).strip()
            if summary:
                all_events.append({"start": str(ev.get("start", ""))[:16], "summary": summary, "source": ev.get("account", ev.get("calendar", "ICS"))})

        all_events.sort(key=lambda x: x["start"])

        if all_events:
            parts.append("**TODAY'S CALENDAR:**")
            for ev in all_events[:15]:
                parts.append(f"  • {ev['start']} [{ev['source']}] — {ev['summary']}")
        else:
            parts.append("**CALENDAR:** No events today.")
    except Exception as e:
        log.warning(f"Calendar build: {e}")
        parts.append("**CALENDAR:** Unavailable.")

    # Tasks
    try:
        r = httpx.get(f"{WORKER_API}/focus/today", timeout=10)
        tasks = r.json()
        if isinstance(tasks, list) and tasks:
            parts.append("\n**TODAY'S TASKS:**")
            for t in tasks[:10]:
                p_icon = {4: "🔴", 3: "🟠", 2: "🟡"}.get(t.get("priority", 1), "⚪")
                parts.append(f"  {p_icon} {t.get('content','')}")
        else:
            parts.append("\n**TASKS:** Inbox is clear.")
    except Exception as e:
        log.warning(f"Tasks fetch: {e}")
        parts.append("\n**TASKS:** Unavailable.")

    # Projects
    try:
        r = httpx.get(f"{WORKER_API}/projects", timeout=5)
        projects = [p for p in r.json().get("projects", []) if p.get("active")]
        if projects:
            parts.append("\n**ACTIVE PROJECTS:**")
            for p in projects:
                icon = {"green": "🟢", "yellow": "🟡", "red": "🔴"}.get(p.get("status", ""), "⚪")
                parts.append(f"  {icon} {p['name']} — {p.get('next','')}")
    except Exception as e:
        log.warning(f"Projects fetch: {e}")

    # Intention
    try:
        r = httpx.get(f"{WORKER_API}/checkin", timeout=5)
        checkin = r.json()
        intent = checkin.get("intent")
        sleep_q = checkin.get("sleep_quality")
        restfulness = checkin.get("restfulness")
        if intent:
            parts.append(f"\n**TODAY'S INTENTION:** {intent}")
        if sleep_q or restfulness:
            parts.append(
                f"\n**LEO'S SELF-REPORTED SLEEP:** "
                f"Quality: {sleep_q or 'not reported'} | "
                f"How rested: {restfulness or 'not reported'}"
            )
    except Exception:
        pass


    # Weather — wttr.in JSON format
    try:
        _loc = "Philadelphia,PA"
        try:
            import json as _json
            _ui = _json.loads((VAULT_PATH / "00_System" / "ui_settings.json").read_text())
            _loc = _ui.get("weather_location", _loc)
        except Exception:
            pass
        _wr = httpx.get(f"https://wttr.in/{_loc.replace(' ', '+')}?format=j1", timeout=8)
        if _wr.status_code == 200:
            _wd = _wr.json()
            _cur = _wd["current_condition"][0]
            _day = _wd["weather"][0]
            _rain_pct = max(int(h.get("chanceofrain", 0)) for h in _day.get("hourly", [{}]))
            _precip = sum(float(h.get("precipInches", 0)) for h in _day.get("hourly", []))
            parts.append(
                f"\n**WEATHER:**\n"
                f"  {_cur['temp_F']}° {_cur['weatherDesc'][0]['value']}\n"
                f"  Feels Like {_cur['FeelsLikeF']}°\n"
                f"  High {_day['maxtempF']}° / Low {_day['mintempF']}°\n"
                f"  Chance of Rain {_rain_pct}% — {round(_precip, 2)} in"
            )
    except Exception as _we:
        log.warning(f"Weather fetch: {_we}")

    # Oura health data — direct API call, with staleness check
    try:
        _oura_token = load_secret("oura_token")
        _today = datetime.now().strftime("%Y-%m-%d")
        _yesterday = (datetime.now().replace(hour=0, minute=0, second=0)
                      .__class__.fromtimestamp(datetime.now().timestamp() - 86400)).strftime("%Y-%m-%d")
        _headers = {"Authorization": f"Bearer {_oura_token}"}
        _oura_parts = []

        # Try today first, fall back to yesterday with date label
        for _date, _label in [(_today, ""), (_yesterday, " (yesterday — ring not yet synced)")]:
            _rd_r = httpx.get(
                "https://api.ouraring.com/v2/usercollection/daily_readiness",
                params={"start_date": _date, "end_date": _date},
                headers=_headers, timeout=15
            )
            _sl_r = httpx.get(
                "https://api.ouraring.com/v2/usercollection/daily_sleep",
                params={"start_date": _date, "end_date": _date},
                headers=_headers, timeout=15
            )
            _rd_data = _rd_r.json().get("data", []) if _rd_r.status_code == 200 else []
            _sl_data = _sl_r.json().get("data", []) if _sl_r.status_code == 200 else []

            if _rd_data or _sl_data:
                if _rd_data:
                    _rd = _rd_data[0]
                    _oura_parts.append(f"  Readiness: {_rd.get('score','?')}/100{_label}")
                if _sl_data:
                    _sl = _sl_data[0]
                    _total_s = _sl.get("total_sleep_duration", 0)
                    def _fmt(s): return f"{int(s)//3600}h{(int(s)%3600)//60}m" if s else "?"
                    _oura_parts.append(f"  Sleep: {_sl.get('score','?')}/100 | {_fmt(_total_s)} total")
                break

        if _oura_parts:
            parts.append("\n**OURA:**")
            parts.extend(_oura_parts)
        else:
            parts.append("\n**OURA:** No data (ring not synced)")
    except Exception as _e:
        log.warning(f"Oura fetch: {_e}")

    return "\n".join(parts)


def send_morning_brief():
    log.info("Generating morning brief...")
    slack_token = load_secret("slack_bot_token")
    if not slack_token:
        log.error("No slack_bot_token — skipping morning brief")
        return

    context = build_context()

    prompt = (
        "Good morning. Generate Leo's morning brief with these sections:\n\n"
        "1. *HEALTH CHECK (Doc)* — Interpret the Oura sleep + readiness data in plain language. "
        "What do the numbers mean for today's energy? Give one concrete recommendation "
        "(e.g. recover, push hard, moderate day). Skip this section entirely if no Oura data.\n"
        "2. *TODAY'S FOCUS* — What matters most from calendar + tasks + projects. Flag conflicts.\n"
        "3. *ONE THING* — The single most important action for today.\n\n"
        "Format for Slack (*bold*, bullets, emoji headers). Tight and direct — brief not novel.\n\n"
        f"Today's data:\n{context}"
    )

    try:
        r = httpx.post(
            f"{COUNCIL_API}/message",
            json={"channel": "kai", "message": prompt, "user_id": "scheduler"},
            timeout=90,
        )
        r.raise_for_status()
        brief = r.json().get("reply", context)
    except Exception as e:
        log.error(f"Council API error: {e}")
        brief = f"*Morning Brief*\n{context}"

    # Try DM first, fall back to #kai channel
    leo_id = slack_get_user_id(slack_token, "kai@sonicink.space")
    dm_ch = slack_open_dm(slack_token, leo_id) if leo_id else None
    target = dm_ch or "kai"

    slack_post(slack_token, target, f"*🌅 Morning Brief — {datetime.now().strftime('%A, %B %d')}*\n\n{brief}")
    log.info(f"Morning brief sent to {'DM' if dm_ch else '#kai'}")


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


# ── Main ───────────────────────────────────────────────────────────────────────

_brief_sent_date: str = ""


def main():
    global _brief_sent_date
    log.info("kai-scheduler started — morning brief at 7AM + Telegram polling")

    tg_thread = threading.Thread(target=telegram_poll_loop, daemon=True, name="telegram-poll")
    tg_thread.start()

    _last_watchdog_minute = -1
    while True:
        now = datetime.now()
        date_str = now.strftime("%Y-%m-%d")
        if now.hour == 7 and now.minute == 0 and _brief_sent_date != date_str:
            _brief_sent_date = date_str
            try:
                send_morning_brief()
            except Exception as e:
                log.error(f"Morning brief error: {e}")
        # Watchdog: run every 30 minutes at :00 and :30
        if now.minute in (0, 30) and now.minute != _last_watchdog_minute:
            _last_watchdog_minute = now.minute
            try:
                run_watchdog_checks()
            except Exception as e:
                log.error(f"Watchdog error: {e}")
        time.sleep(30)


if __name__ == "__main__":
    main()
