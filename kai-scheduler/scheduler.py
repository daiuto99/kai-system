"""
kai-scheduler — Sprint 24
- Morning brief at 7:30 AM ET → Telegram KAI Briefs group
- Afternoon brief at 12:30 PM ET → Telegram KAI Briefs group
- Evening brief at 8:00 PM ET → Telegram KAI Briefs group
- Telegram long polling (routes messages to KAI council)
"""
import time
import logging
import threading
import os
from datetime import datetime, date as _date, timedelta as _td
from zoneinfo import ZoneInfo
from pathlib import Path
import httpx
import concurrent.futures

logging.basicConfig(level=logging.INFO, format="%(asctime)s [scheduler] %(message)s")
log = logging.getLogger(__name__)

WORKER_API    = "http://kai-worker-api:8001"
COUNCIL_API   = "http://kai-council-api:8002"
VAULT_PATH    = Path("/vault")
BRIEF_CHAT_ID = -5172026335  # KAI Briefs Telegram group
ET            = ZoneInfo("America/New_York")


def load_secret(name: str) -> str:
    p = Path(f"/run/secrets/{name}")
    if p.exists():
        return p.read_text().strip()
    return os.environ.get(name.upper(), "")


# ── Telegram helper ────────────────────────────────────────────────────────────

def tg_send(token: str, chat_id: int, text: str):
    try:
        httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
            timeout=15,
        )
    except Exception as e:
        log.error(f"Telegram send error: {e}")


# ── Slack helpers (kept for health alerts only) ────────────────────────────────

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


# ── Context builders ───────────────────────────────────────────────────────────

def _fetch_oura(date_str: str, headers: dict) -> tuple:
    """Return (readiness_data, sleep_data) for a given date, or (None, None)."""
    try:
        rd_r = httpx.get(
            "https://api.ouraring.com/v2/usercollection/daily_readiness",
            params={"start_date": date_str, "end_date": date_str},
            headers=headers, timeout=15,
        )
        sl_r = httpx.get(
            "https://api.ouraring.com/v2/usercollection/daily_sleep",
            params={"start_date": date_str, "end_date": date_str},
            headers=headers, timeout=15,
        )
        rd = rd_r.json().get("data", []) if rd_r.status_code == 200 else []
        sl = sl_r.json().get("data", []) if sl_r.status_code == 200 else []
        return (rd[0] if rd else None, sl[0] if sl else None)
    except Exception as e:
        log.warning(f"Oura fetch ({date_str}): {e}")
        return (None, None)


def _fmt_duration(seconds) -> str:
    s = int(seconds) if seconds else 0
    return f"{s // 3600}h{(s % 3600) // 60}m" if s else "?"



def _fetch_section(fn):
    """Run a context-fetch function, return its string output or empty string on error."""
    try:
        return fn()
    except Exception as e:
        log.warning(f"Parallel context fetch failed ({fn.__name__}): {e}")
        return ""

def build_context() -> str:
    now_et = datetime.now(ET)
    today_str = now_et.strftime("%Y-%m-%d")
    parts = [f"**TODAY IS:** {now_et.strftime('%A, %B %d, %Y')}"]

    # Run all data fetches in parallel using ThreadPoolExecutor
    import json as _json

    def _get_calendar():
        gcal_events, ics_events = [], []
        try:
            r = httpx.post("http://kai-n8n:5678/webhook/kai-calendar-events",
                           json={"days": 1}, timeout=15)
            gcal_events = r.json() if r.status_code == 200 else []
        except Exception as e:
            log.warning(f"Google Calendar fetch: {e}")
        try:
            r = httpx.get(f"{WORKER_API}/calendar/ics?days=1", timeout=10)
            ics_events = r.json().get("events", []) if r.status_code == 200 else []
        except Exception as e:
            log.warning(f"ICS calendar fetch: {e}")
        return gcal_events, ics_events

    def _get_tasks():
        r = httpx.get(f"{WORKER_API}/focus/today", timeout=10)
        return r.json() if r.status_code == 200 else []

    def _get_habits():
        r = httpx.get(f"{WORKER_API}/habits", timeout=10)
        return r.json().get("habits", []) if r.status_code == 200 else []

    def _get_weather():
        loc = "Philadelphia,PA"
        try:
            loc_data = _json.loads((VAULT_PATH / "00_System" / "current_location.json").read_text())
            loc = "{},{}".format(loc_data["lat"], loc_data["lon"])
        except Exception:
            try:
                ui = _json.loads((VAULT_PATH / "00_System" / "ui_settings.json").read_text())
                loc = ui.get("weather_location", loc)
            except Exception:
                pass
        r = httpx.get(f"https://wttr.in/{loc.replace(' ', '+')}?format=j1", timeout=8)
        return r.json() if r.status_code == 200 else None

    def _get_oura():
        oura_token = load_secret("oura_token")
        if not oura_token:
            return None
        headers = {"Authorization": f"Bearer {oura_token}"}
        from datetime import date as _d2, timedelta as _td2
        today_s = _d2.today().isoformat()
        yesterday_s = (_d2.today() - _td2(days=1)).isoformat()
        for date_str, label in [(today_s, ""), (yesterday_s, " (yesterday)")]:
            rd, sl = _fetch_oura(date_str, headers)
            if rd or sl:
                return rd, sl, label
        return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        fut_cal = pool.submit(_get_calendar)
        fut_tasks = pool.submit(_get_tasks)
        fut_habits = pool.submit(_get_habits)
        fut_weather = pool.submit(_get_weather)
        fut_oura = pool.submit(_get_oura)

    cal_result = fut_cal.result() if not fut_cal.exception() else ([], [])
    tasks_result = fut_tasks.result() if not fut_tasks.exception() else []
    habits_result = fut_habits.result() if not fut_habits.exception() else []
    weather_result = fut_weather.result() if not fut_weather.exception() else None
    oura_result = fut_oura.result() if not fut_oura.exception() else None

    gcal_events, ics_events = cal_result

    # Calendar (data pre-fetched in parallel above)
    try:
        all_events = []
        for ev in gcal_events:
            start = ev.get("start", {})
            start_str = start.get("dateTime", start.get("date", "")) if isinstance(start, dict) else str(start)
            all_events.append({"start": start_str[:16], "summary": ev.get("summary", ""), "source": "Google"})
        for ev in ics_events:
            summary = ev.get("title", ev.get("summary", "")).strip()
            if summary:
                all_events.append({"start": str(ev.get("start", ""))[:16], "summary": summary,
                                   "source": ev.get("account", ev.get("calendar", "ICS"))})
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

    # Tasks (pre-fetched in parallel above)
    try:
        tasks = tasks_result
        if isinstance(tasks, list) and tasks:
            parts.append("\n**TODAY'S TASKS:**")
            for t in tasks[:10]:
                p_icon = {4: "🔴", 3: "🟠", 2: "🟡"}.get(t.get("priority", 1), "⚪")
                parts.append(f"  {p_icon} {t.get('content', '')}")
        else:
            parts.append("\n**TASKS:** Inbox is clear.")
    except Exception as e:
        log.warning(f"Tasks process: {e}")
        parts.append("\n**TASKS:** Unavailable.")

    # Habits (pre-fetched in parallel above)
    try:
        habits = habits_result
        missing = [h for h in habits if today_str not in h.get("completions", [])]
        if missing:
            parts.append("\n**HABITS NOT YET DONE:**")
            for h in missing[:8]:
                emoji = h.get("emoji", "•")
                name = h.get("displayName") or h.get("name", "")
                completions = h.get("completions", [])
                missed_days = sum(
                    1 for i in range(1, 4)
                    if (_date.today() - _td(days=i)).isoformat() not in completions
                )
                nudge = f" ⚠️ {missed_days}d missed" if missed_days >= 2 else ""
                parts.append(f"  {emoji} {name}{nudge}")
    except Exception as e:
        log.warning(f"Habits fetch: {e}")

    # Weather (pre-fetched in parallel above)
    try:
        if weather_result:
            wd = weather_result
            cur = wd["current_condition"][0]
            day = wd["weather"][0]
            rain_pct = max(int(h.get("chanceofrain", 0)) for h in day.get("hourly", [{}]))
            parts.append(
                f"\n**WEATHER:**\n"
                f"  {cur['temp_F']}° {cur['weatherDesc'][0]['value']} | "
                f"Feels {cur['FeelsLikeF']}° | "
                f"H {day['maxtempF']}° / L {day['mintempF']}° | "
                f"Rain {rain_pct}%"
            )
    except Exception as e:
        log.warning(f"Weather process: {e}")

    # Oura (pre-fetched in parallel above)
    try:
        if oura_result:
            rd, sl, label = oura_result
            oura_lines = []
            if rd:
                oura_lines.append(f"  Readiness: {rd.get('score', '?')}/100{label}")
            if sl:
                oura_lines.append(f"  Sleep: {sl.get('score', '?')}/100 | {_fmt_duration(sl.get('total_sleep_duration', 0))} total")
            if oura_lines:
                parts.append("\n**OURA:**")
                parts.extend(oura_lines)
        else:
            parts.append("\n**OURA:** No data (ring not synced)")
    except Exception as e:
        log.warning(f"Oura process: {e}")

    return "\n".join(parts)


def build_afternoon_context() -> str:
    now_et = datetime.now(ET)
    now_str = now_et.strftime("%Y-%m-%dT%H:%M")
    parts = [f"**AFTERNOON — {now_et.strftime('%A, %B %d, %I:%M %p ET')}**"]

    # Remaining calendar events
    try:
        r = httpx.post("http://kai-n8n:5678/webhook/kai-calendar-events",
                       json={"days": 1}, timeout=15)
        if r.status_code == 200:
            remaining = []
            for ev in r.json():
                start = ev.get("start", {})
                start_str = start.get("dateTime", start.get("date", "")) if isinstance(start, dict) else str(start)
                if start_str[:16] >= now_str:
                    remaining.append({"start": start_str[:16], "summary": ev.get("summary", "")})
            remaining.sort(key=lambda x: x["start"])
            if remaining:
                parts.append("\n**REMAINING TODAY:**")
                for ev in remaining[:8]:
                    parts.append(f"  • {ev['start']} — {ev['summary']}")
            else:
                parts.append("\n**CALENDAR:** Nothing left today.")
    except Exception as e:
        log.warning(f"Afternoon calendar: {e}")

    # Tasks
    try:
        r = httpx.get(f"{WORKER_API}/focus/today", timeout=10)
        tasks = r.json()
        if isinstance(tasks, list) and tasks:
            parts.append("\n**TASKS:**")
            for t in tasks[:8]:
                p_icon = {4: "🔴", 3: "🟠", 2: "🟡"}.get(t.get("priority", 1), "⚪")
                parts.append(f"  {p_icon} {t.get('content', '')}")
    except Exception as e:
        log.warning(f"Afternoon tasks: {e}")

    return "\n".join(parts)


def build_evening_context() -> str:
    now_et = datetime.now(ET)
    today_str = now_et.strftime("%Y-%m-%d")
    tomorrow_str = (_date.today() + _td(days=1)).isoformat()
    parts = [f"**EVENING — {now_et.strftime('%A, %B %d')}**"]

    # Habit completion summary
    try:
        r = httpx.get(f"{WORKER_API}/habits", timeout=10)
        habits = r.json().get("habits", [])
        done = [h for h in habits if today_str in h.get("completions", [])]
        missed = [h for h in habits if today_str not in h.get("completions", [])]
        parts.append("\n**HABITS TODAY:**")
        for h in done:
            parts.append(f"  ✅ {h.get('emoji', '')} {h.get('displayName') or h.get('name', '')}")
        for h in missed:
            parts.append(f"  ⬜ {h.get('emoji', '')} {h.get('displayName') or h.get('name', '')}")
    except Exception as e:
        log.warning(f"Evening habits: {e}")

    # Tomorrow's calendar
    try:
        r = httpx.post("http://kai-n8n:5678/webhook/kai-calendar-events",
                       json={"days": 2}, timeout=15)
        if r.status_code == 200:
            tmrw = []
            for ev in r.json():
                start = ev.get("start", {})
                start_str = start.get("dateTime", start.get("date", "")) if isinstance(start, dict) else str(start)
                if start_str[:10] == tomorrow_str:
                    tmrw.append({"start": start_str[:16], "summary": ev.get("summary", "")})
            tmrw.sort(key=lambda x: x["start"])
            if tmrw:
                parts.append("\n**TOMORROW:**")
                for ev in tmrw[:6]:
                    parts.append(f"  • {ev['start']} — {ev['summary']}")
            else:
                parts.append("\n**TOMORROW:** Calendar clear.")
    except Exception as e:
        log.warning(f"Evening tomorrow calendar: {e}")

    return "\n".join(parts)


# ── Brief senders ──────────────────────────────────────────────────────────────

def send_morning_brief():
    log.info("Generating morning brief...")
    tg_token = load_secret("telegram_bot_token")
    if not tg_token:
        log.error("No telegram_bot_token — skipping morning brief")
        return

    context = build_context()
    prompt = (
        "Morning brief for Leo. Lead with signal — no preamble.\n\n"
        "Format: Telegram (*bold*, bullets). 15 lines max. Never fabricate data.\n\n"
        "Sections (include only if data present):\n"
        "HEALTH — Readiness score + one sentence interpretation. Flag recovery if readiness < 70.\n"
        "FOCUS — 2-3 things that make today count. Flag any conflicts or overloads.\n"
        "HABITS — Pending habits only. Flag any with 2+ days missed.\n"
        "ONE THING — The single move that matters most today. One line.\n\n"
        "Start with the highest-signal section, not health by default.\n\n"
        f"Data:\n{context}"
    )

    try:
        r = httpx.post(
            f"{COUNCIL_API}/council/message",
            json={"channel": "kai", "message": prompt, "user_id": "scheduler", "history": []},
            timeout=90,
        )
        r.raise_for_status()
        brief = r.json().get("reply", context)
    except Exception as e:
        log.error(f"Council API error (morning): {e}")
        brief = context

    now_et = datetime.now(ET)
    tg_send(tg_token, BRIEF_CHAT_ID,
            f"🌅 *Morning Brief — {now_et.strftime('%A, %B %d')}*\n\n{brief}")
    log.info("Morning brief sent to Telegram KAI Briefs")


def send_afternoon_brief():
    log.info("Generating afternoon brief...")
    tg_token = load_secret("telegram_bot_token")
    if not tg_token:
        return

    context = build_afternoon_context()
    prompt = (
        "Generate a short afternoon check-in for Leo.\n\n"
        "Sections:\n"
        "1. *PULSE* — 1-2 sentences on whether the day is on track.\n"
        "2. *REMAINING* — Anything from calendar or tasks needing attention before EOD.\n\n"
        "Format for Telegram (*bold*, bullets). 8 lines max. End with: 'Anything shift today?'\n\n"
        f"Data:\n{context}"
    )

    try:
        r = httpx.post(
            f"{COUNCIL_API}/council/message",
            json={"channel": "kai", "message": prompt, "user_id": "scheduler", "history": []},
            timeout=60,
        )
        r.raise_for_status()
        brief = r.json().get("reply", context)
    except Exception as e:
        log.error(f"Council API error (afternoon): {e}")
        brief = context

    now_et = datetime.now(ET)
    tg_send(tg_token, BRIEF_CHAT_ID,
            f"☀️ *Afternoon Check-in — {now_et.strftime('%I:%M %p')}*\n\n{brief}")
    log.info("Afternoon brief sent to Telegram KAI Briefs")


def send_evening_brief():
    log.info("Generating evening brief...")
    tg_token = load_secret("telegram_bot_token")
    if not tg_token:
        return

    context = build_evening_context()
    prompt = (
        "Generate a short evening wind-down brief for Leo.\n\n"
        "Sections:\n"
        "1. *HABITS* — Acknowledge what was done. Note misses without judgment.\n"
        "2. *TOMORROW* — 2-3 sentences previewing tomorrow based on the calendar.\n"
        "3. *REFLECT* — One grounding question to close out the day.\n\n"
        "Format for Telegram (*bold*, bullets). Warm, not clinical. 10 lines max.\n\n"
        f"Data:\n{context}"
    )

    try:
        r = httpx.post(
            f"{COUNCIL_API}/council/message",
            json={"channel": "kai", "message": prompt, "user_id": "scheduler", "history": []},
            timeout=60,
        )
        r.raise_for_status()
        brief = r.json().get("reply", context)
    except Exception as e:
        log.error(f"Council API error (evening): {e}")
        brief = context

    now_et = datetime.now(ET)
    tg_send(tg_token, BRIEF_CHAT_ID,
            f"🌙 *Evening Brief — {now_et.strftime('%A, %B %d')}*\n\n{brief}")
    log.info("Evening brief sent to Telegram KAI Briefs")


# ── Telegram Long Polling ──────────────────────────────────────────────────────

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
                if text == "/chatid":
                    tg_send(token, chat_id, "Chat ID: " + str(chat_id))
                    continue
                if text == "/start":
                    tg_send(token, chat_id,
                            "🤖 *KAI online.*\n\nSend a message or use an advisor prefix:\n"
                            "/beats /coach /biz /sky /roads /tech /dev /ops")
                    continue
                advisor = "kai"
                message = text
                ADVISORS = {"kai", "beats", "coach", "biz", "sky", "roads", "tech", "dev",
                            "ops", "creative", "learning", "support"}
                PRIVATE_ADVISORS = {"ember", "doc"}
                if text.startswith("/"):
                    parts = text[1:].split(None, 1)
                    cmd = parts[0].lower()
                    if cmd in PRIVATE_ADVISORS:
                        tg_send(token, chat_id,
                                f"⚠️ /{cmd} conversations stay on the dashboard for privacy — "
                                f"Telegram is not end-to-end encrypted.")
                        continue
                    if cmd in ADVISORS and len(parts) > 1:
                        advisor, message = cmd, parts[1]
                    elif cmd in ADVISORS:
                        tg_send(token, chat_id,
                                f"Send a message after the advisor name: /{cmd} your message here")
                        continue
                log.info(f"Telegram @{username} ({chat_id}) → {advisor}: {message[:60]}")
                try:
                    resp = httpx.post(
                        f"{COUNCIL_API}/council/message",
                        json={"channel": advisor, "message": message,
                              "user_id": f"telegram:{username}", "history": []},
                        timeout=90,
                    )
                    resp.raise_for_status()
                    reply = resp.json().get("reply", "No response.")
                except Exception as e:
                    log.error(f"Council API error (Telegram): {e}")
                    reply = "⚠️ KAI is temporarily unavailable."
                tg_send(token, chat_id, reply)

        except httpx.TimeoutException:
            pass
        except Exception as e:
            log.error(f"Telegram poll error: {e}")
            time.sleep(5)


def check_worker_health():
    try:
        r = httpx.get(f"{WORKER_API}/system/health", timeout=10)
        if r.status_code != 200:
            return
        data = r.json()
        alerts = data.get("alerts", [])
        if not alerts:
            return
        slack_token = load_secret("slack_bot_token")
        msg = ("*Worker Health Alert* — thresholds breached:\n"
               + "\n".join(f"  • {a}" for a in alerts)
               + f"\n\nDisk: {data['disk_pct']}% | Mem: {data['mem_pct']}% | "
                 f"Temp: {data.get('temp_c', 'N/A')}°C | Uptime: {data['uptime']}")
        if slack_token:
            slack_post(slack_token, "kai-system", msg)
        log.info(f"Health alert sent: {alerts}")
    except Exception as e:
        log.warning(f"Health check error: {e}")


# ── Main ───────────────────────────────────────────────────────────────────────

_morning_sent:   str = ""
_afternoon_sent: str = ""
_evening_sent:   str = ""
_health_sent:    str = ""


def main():
    global _morning_sent, _afternoon_sent, _evening_sent, _health_sent
    log.info("kai-scheduler started — briefs at 7:30/12:30/20:00 ET + Telegram polling")

    tg_thread = threading.Thread(target=telegram_poll_loop, daemon=True, name="telegram-poll")
    tg_thread.start()

    while True:
        now = datetime.now(ET)
        date_str = now.strftime("%Y-%m-%d")

        if now.hour == 7 and now.minute == 30 and _morning_sent != date_str:
            _morning_sent = date_str
            try:
                send_morning_brief()
            except Exception as e:
                log.error(f"Morning brief error: {e}")

        if now.hour == 9 and now.minute == 0 and _health_sent != date_str:
            _health_sent = date_str
            try:
                check_worker_health()
            except Exception as e:
                log.error(f"Health check error: {e}")

        if now.hour == 12 and now.minute == 30 and _afternoon_sent != date_str:
            _afternoon_sent = date_str
            try:
                send_afternoon_brief()
            except Exception as e:
                log.error(f"Afternoon brief error: {e}")

        if now.hour == 20 and now.minute == 0 and _evening_sent != date_str:
            _evening_sent = date_str
            try:
                send_evening_brief()
            except Exception as e:
                log.error(f"Evening brief error: {e}")

        time.sleep(30)


if __name__ == "__main__":
    main()
