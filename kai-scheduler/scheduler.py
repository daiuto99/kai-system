"""
kai-scheduler — Sprint 24
- Morning brief at 9:15 AM ET → Telegram KAI Briefs group
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
from watchdog import run_watchdog_checks
from security_watchdog import run_security_checks

logging.basicConfig(level=logging.INFO, format="%(asctime)s [scheduler] %(message)s")

def strip_markdown(text):
    import re
    EMOJI = re.compile(
        r"[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF"
        r"\U0001F680-\U0001F6FF\U0001F1E0-\U0001F1FF"
        r"\U00002700-\U000027BF\U0001F900-\U0001F9FF"
        r"\U0001FA00-\U0001FA6F\U0001FA70-\U0001FAFF"
        r"\U00002600-\U000026FF]+", re.UNICODE)
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\*{3}(.+?)\*{3}", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"\*{2}(.+?)\*{2}", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"\*(.+?)\*", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"_{2}(.+?)_{2}", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"_(.+?)_", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"`(.+?)`", r"\1", text)
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*\d+\.\s+", "", text, flags=re.MULTILINE)
    text = EMOJI.sub("", text)
    text = re.sub(r" +", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

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
    """
    Synthesis-layer context builder. Fetches all data in parallel, pre-processes
    into signals for KAI to narrate from — not raw data to report.
    """
    from datetime import date as _d2, timedelta as _td2
    import json as _json

    now_et = datetime.now(ET)
    today_str = now_et.strftime("%Y-%m-%d")
    today_date = _d2.today()

    def _get_calendar():
        gcal_events, ics_events = [], []
        try:
            r = httpx.post("http://kai-n8n:5678/webhook/kai-calendar-events",
                           json={"days": 14}, timeout=20)
            gcal_events = r.json() if r.status_code == 200 else []
        except Exception as e:
            log.warning(f"Google Calendar fetch: {e}")
        try:
            r = httpx.get(f"{WORKER_API}/calendar/ics?days=14", timeout=15)
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

    def _get_oura_trend():
        oura_token = load_secret("oura_token")
        if not oura_token:
            return None
        headers = {"Authorization": f"Bearer {oura_token}"}
        results = []
        for i in range(7):
            date_str = (today_date - _td2(days=i)).isoformat()
            rd, sl = _fetch_oura(date_str, headers)
            results.append({"date": date_str, "days_ago": i, "readiness": rd, "sleep": sl})
        return results

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        fut_cal     = pool.submit(_get_calendar)
        fut_tasks   = pool.submit(_get_tasks)
        fut_habits  = pool.submit(_get_habits)
        fut_weather = pool.submit(_get_weather)
        fut_oura    = pool.submit(_get_oura_trend)

    cal_result     = fut_cal.result()     if not fut_cal.exception()     else ([], [])
    tasks_result   = fut_tasks.result()   if not fut_tasks.exception()   else []
    habits_result  = fut_habits.result()  if not fut_habits.exception()  else []
    weather_result = fut_weather.result() if not fut_weather.exception() else None
    oura_trend     = fut_oura.result()    if not fut_oura.exception()    else None

    signals = [f"TODAY: {now_et.strftime('%A, %B %d, %Y at %I:%M %p ET')}"]

    # HEALTH SIGNALS
    try:
        if oura_trend:
            today_oura  = oura_trend[0]
            today_rd    = today_oura["readiness"]
            today_sl    = today_oura["sleep"]
            week_scores = [d["readiness"]["score"] for d in oura_trend if d["readiness"]]

            health_lines = ["HEALTH:"]
            if today_rd:
                score = today_rd["score"]
                trend_note = ""
                if len(week_scores) >= 3:
                    recent_avg = sum(week_scores[1:4]) / len(week_scores[1:4])
                    if score < recent_avg - 5:
                        trend_note = " (down from recent average)"
                    elif score > recent_avg + 5:
                        trend_note = " (up from recent average)"
                health_lines.append(f"  Readiness today: {score}/100{trend_note}")
            else:
                health_lines.append("  Ring not synced yet today — no readiness score available.")

            if today_sl:
                duration = _fmt_duration(today_sl.get("total_sleep_duration", 0))
                health_lines.append(f"  Sleep last night: {today_sl['score']}/100, {duration}")

            if len(week_scores) >= 3:
                avg = round(sum(week_scores) / len(week_scores))
                below_75 = sum(1 for s in week_scores if s < 75)
                direction = ""
                if len(week_scores) >= 4:
                    first_half  = sum(week_scores[len(week_scores)//2:]) / (len(week_scores) - len(week_scores)//2)
                    second_half = sum(week_scores[:len(week_scores)//2]) / (len(week_scores)//2)
                    if second_half < first_half - 3:
                        direction = ", declining"
                    elif second_half > first_half + 3:
                        direction = ", improving"
                health_lines.append(f"  7-day avg readiness: {avg}/100{direction}. {below_75} of {len(week_scores)} days below 75.")

            signals.append("\n".join(health_lines))
        else:
            signals.append("HEALTH: Oura not configured.")
    except Exception as e:
        log.warning(f"Health signals: {e}")
        signals.append("HEALTH: Unavailable.")

    # CALENDAR SIGNALS
    try:
        gcal_events, ics_events = cal_result
        TRAVEL_KEYWORDS = {"airbnb", "hotel", "marriott", "hyatt", "hilton", "westin", "flight",
                           "check-in", "check in", "resort", "inn", "motel"}
        FUN_KEYWORDS    = {"concert", "show", "game", "festival", "dinner", "happy hour", "party",
                           "wedding", "birthday", "comedy", "theatre", "theater", "museum", "tour",
                           "brunch", "gig", "performance", "recital", "match", "drinks"}

        all_events = []
        for ev in gcal_events:
            start = ev.get("start", {})
            start_str = start.get("dateTime", start.get("date", "")) if isinstance(start, dict) else str(start)
            all_events.append({"start": start_str[:16], "date": start_str[:10],
                                "summary": ev.get("summary", ""), "location": ev.get("location", "")})
        for ev in ics_events:
            summary = ev.get("title", ev.get("summary", "")).strip()
            if summary:
                start_str = str(ev.get("start", ""))[:16]
                all_events.append({"start": start_str, "date": start_str[:10],
                                   "summary": summary, "location": ev.get("location", "")})

        all_events.sort(key=lambda x: x["start"])
        today_events = [e for e in all_events if e["date"] == today_str]
        upcoming     = [e for e in all_events if e["date"] > today_str]

        cal_lines = ["CALENDAR:"]

        if today_events:
            cal_lines.append(f"  Today ({len(today_events)} event(s)):")
            for ev in today_events[:10]:
                t = ev["start"][11:16] if len(ev["start"]) > 10 else ""
                sl = ev["summary"].lower()
                tag = " [fun]" if any(k in sl for k in FUN_KEYWORDS) else (
                      " [travel]" if any(k in sl for k in TRAVEL_KEYWORDS) else "")
                cal_lines.append(f"    {t} {ev['summary']}{tag}")
        else:
            cal_lines.append("  Today: clear.")

        if upcoming:
            by_date = {}
            for ev in upcoming:
                by_date.setdefault(ev["date"], []).append(ev)
            cal_lines.append("  Next 7 days:")
            for d in sorted(by_date)[:7]:
                evs = by_date[d]
                try:
                    label = _d2.fromisoformat(d).strftime("%a %b %d")
                except Exception:
                    label = d
                fun    = [e for e in evs if any(k in e["summary"].lower() for k in FUN_KEYWORDS)]
                travel = [e for e in evs if any(k in e["summary"].lower() or k in e["location"].lower()
                                                for k in TRAVEL_KEYWORDS)]
                density = " (heavy)" if len(evs) >= 5 else (" (busy)" if len(evs) >= 3 else "")
                notes = []
                if fun:    notes.append(f"fun: {', '.join(e['summary'] for e in fun[:2])}")
                if travel: notes.append(f"travel signal: {', '.join(e['summary'] for e in travel[:2])}")
                note_str = f" — {'; '.join(notes)}" if notes else ""
                cal_lines.append(f"    {label}: {len(evs)} event(s){density}{note_str}")

        signals.append("\n".join(cal_lines))
    except Exception as e:
        log.warning(f"Calendar signals: {e}")
        signals.append("CALENDAR: Unavailable.")

    # TASK SIGNALS
    try:
        tasks = tasks_result if isinstance(tasks_result, list) else []
        task_lines = ["TASKS:"]
        if tasks:
            high    = [t for t in tasks if t.get("priority", 1) >= 3]
            overdue = [t for t in tasks if t.get("due") and t["due"] < today_str]
            task_lines.append(f"  {len(tasks)} task(s) today. {len(high)} high priority. {len(overdue)} overdue.")
            for t in tasks[:8]:
                p     = t.get("priority", 1)
                plbl  = {4: "urgent", 3: "high", 2: "medium"}.get(p, "")
                due   = t.get("due", "")
                od    = (today_date - _d2.fromisoformat(due)).days if due and due < today_str else 0
                od_note = f" [{od}d overdue]" if od > 0 else ""
                p_note  = f" [{plbl}]"        if plbl  else ""
                task_lines.append(f"  - {t.get('content', '')}{p_note}{od_note}")
        else:
            task_lines.append("  Board is clear.")
        signals.append("\n".join(task_lines))
    except Exception as e:
        log.warning(f"Task signals: {e}")

    # HABIT SIGNALS
    try:
        habits = habits_result
        habit_lines = ["HABITS:"]
        if habits:
            done    = [h for h in habits if today_str in h.get("completions", [])]
            pending = [h for h in habits if today_str not in h.get("completions", [])]
            habit_lines.append(f"  {len(done)} done today, {len(pending)} pending.")
            for h in pending[:6]:
                name = h.get("displayName") or h.get("name", "")
                completions = h.get("completions", [])
                missed = sum(1 for i in range(1, 5) if (today_date - _td2(days=i)).isoformat() not in completions)
                pattern = f" — missed {missed} of last 4 days" if missed >= 2 else ""
                habit_lines.append(f"  - {h.get('emoji', '')} {name} (pending){pattern}")
        else:
            habit_lines.append("  No habits configured.")
        signals.append("\n".join(habit_lines))
    except Exception as e:
        log.warning(f"Habit signals: {e}")

    # WEATHER
    try:
        if weather_result:
            wd  = weather_result
            cur = wd["current_condition"][0]
            day = wd["weather"][0]
            rain_pct = max(int(h.get("chanceofrain", 0)) for h in day.get("hourly", [{}]))
            signals.append(
                f"WEATHER: {cur['temp_F']}°F {cur['weatherDesc'][0]['value']}, "
                f"feels {cur['FeelsLikeF']}°F. High {day['maxtempF']}° / Low {day['mintempF']}°. "
                f"Rain {rain_pct}%."
            )
    except Exception as e:
        log.warning(f"Weather signals: {e}")

    signals.append(
        "INSTRUCTION: This is synthesis input, not a data dump to report. "
        "Use every signal to build a narrative. Read what's missing as much as what's there. "
        "Interpret. Speak directly to Leo."
    )

    return "\n\n".join(signals)


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
        "You are KAI delivering the morning brief. Picture this: Leo just handed you a coffee "
        "and you're sitting down together on the patio before the day starts. Unhurried. Present. "
        "You've already looked at everything — health, calendar, tasks, habits — and you're reading him the room.\n\n"
        "Speak directly to him. No section headers, no labels, no bullet lists. "
        "Narrate. One thought flows into the next like a person talking, not a report printing.\n\n"
        "Lead with whatever matters most today — that might be a readiness number that changes the plan, "
        "a calendar that's stacked, or one task that's been sitting too long. You decide what leads.\n\n"
        "Interpret the data, don't report it. Readiness 74 isn't a number — it's context for how he should "
        "pace the day. A clear calendar isn't a fact — it's an opportunity. A missed habit isn't a miss — "
        "it's worth a mention if it's becoming a pattern.\n\n"
        "Close with one question or a clean handoff — something that puts the ball in his court "
        "and invites the day to start. Not 'is there anything else' — something real.\n\n"
        "No emojis. No markdown, no asterisks, no bullet characters, no section headers. Plain prose only. "
        "Keep it under 15 lines. Never fabricate data — if something isn't in the context, don't invent it.\n\n"
        f"Context:\n{context}"
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
    brief = strip_markdown(brief)
    tg_send(tg_token, BRIEF_CHAT_ID,
            "Morning Brief — " + now_et.strftime("%A, %B %d") + chr(10)*2 + brief)


    log.info("Morning brief sent to Telegram KAI Briefs")


def send_afternoon_brief():
    log.info("Generating afternoon brief...")
    tg_token = load_secret("telegram_bot_token")
    if not tg_token:
        return

    context = build_afternoon_context()
    prompt = (
        "You are KAI delivering the afternoon check-in. Picture this: Leo just finished lunch "
        "and you're walking together — quick, easy pace, not a meeting. You've got maybe two minutes "
        "and you're giving him the mid-day read while he's moving.\n\n"
        "Keep it tight. No section headers, no bullets, no labels. Just talk. "
        "How's the day tracking? What's still on the board that needs to move before tonight? "
        "Anything that shifted or came in that he should know about?\n\n"
        "If the day is clean, say so in one line and point at what's next. "
        "If something needs attention, name it clearly and directly — no softening.\n\n"
        "Close with one short question that takes the temperature — not a formality, something that "
        "actually invites a real answer.\n\n"
        "No emojis. No markdown. Plain prose only. 8 lines max. Never fabricate data.\n\n"
        f"Context:\n{context}"
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
    brief = strip_markdown(brief)
    tg_send(tg_token, BRIEF_CHAT_ID,
            "Afternoon Check-in — " + now_et.strftime("%I:%M %p") + chr(10)*2 + brief)


    log.info("Afternoon brief sent to Telegram KAI Briefs")


def send_evening_brief():
    log.info("Generating evening brief...")
    tg_token = load_secret("telegram_bot_token")
    if not tg_token:
        return

    context = build_evening_context()
    prompt = (
        "You are KAI delivering the evening brief. Picture this: the day is done. Leo's settled in — "
        "glass of wine or a cappuccino, no more agenda. This is the quiet close of the day, "
        "not a debrief. Warm. Unhurried. Like a trusted friend who was in it with him all day.\n\n"
        "Speak to him directly. No section headers, no structure, no list of what got done. "
        "Just talk. Acknowledge how the day went without making it a report card. "
        "If habits were hit, note it naturally. If something was missed, mention it once, lightly — "
        "no judgment, no follow-up lecture.\n\n"
        "Look at tomorrow briefly — not a full preview, just a heads up if something's worth knowing "
        "before he sleeps. One calendar note if it matters. Nothing more.\n\n"
        "Close with one grounding question. Something real — not 'how are you feeling' but something "
        "that invites reflection on the day or what's ahead. The kind of thing a good friend asks "
        "over a drink at the end of a long day.\n\n"
        "No emojis. No markdown. Plain prose only. Warm, not clinical. 10 lines max. "
        "Never fabricate data.\n\n"
        f"Context:\n{context}"
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
    brief = strip_markdown(brief)
    tg_send(tg_token, BRIEF_CHAT_ID,
            "Evening Brief — " + now_et.strftime("%A, %B %d") + chr(10)*2 + brief)


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
                text = (msg.get("text") or msg.get("caption") or "").strip()
                username = msg.get("from", {}).get("username", "unknown")
                doc = msg.get("document")
                photos = msg.get("photo")
                if not chat_id:
                    continue
                if not text and not doc and not photos:
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
                attachments = []
                if doc:
                    file_id = doc.get("file_id")
                    filename = doc.get("file_name", "file")
                    try:
                        import base64 as _b64
                        gr = httpx.get(f"https://api.telegram.org/bot{token}/getFile",
                                       params={"file_id": file_id}, timeout=10)
                        file_path = gr.json()["result"]["file_path"]
                        dr = httpx.get(f"https://api.telegram.org/file/bot{token}/{file_path}", timeout=30)
                        ext = file_path.rsplit(".", 1)[-1].lower() if "." in file_path else ""
                        mime = {"pdf": "application/pdf", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                                "png": "image/png", "gif": "image/gif", "webp": "image/webp"}.get(ext, "application/octet-stream")
                        if mime in ("application/pdf", "image/jpeg", "image/png", "image/gif", "image/webp"):
                            attachments.append({"type": "document" if mime == "application/pdf" else "image",
                                                "media_type": mime,
                                                "data": _b64.standard_b64encode(dr.content).decode(),
                                                "filename": filename})
                            if not message:
                                message = f"[File attached: {filename}]"
                    except Exception as e:
                        log.error(f"Telegram file download error: {e}")
                        message = message or f"[File: {filename} — could not download]"
                elif photos:
                    largest = max(photos, key=lambda p: p.get("file_size", 0))
                    try:
                        import base64 as _b64
                        gr = httpx.get(f"https://api.telegram.org/bot{token}/getFile",
                                       params={"file_id": largest["file_id"]}, timeout=10)
                        file_path = gr.json()["result"]["file_path"]
                        dr = httpx.get(f"https://api.telegram.org/file/bot{token}/{file_path}", timeout=30)
                        attachments.append({"type": "image", "media_type": "image/jpeg",
                                            "data": _b64.standard_b64encode(dr.content).decode(),
                                            "filename": "photo.jpg"})
                        if not message:
                            message = "[Photo attached]"
                    except Exception as e:
                        log.error(f"Telegram photo download error: {e}")
                        message = message or "[Photo — could not download]"
                payload = {"channel": advisor, "message": message,
                           "user_id": f"telegram:{username}", "history": []}
                if attachments:
                    payload["attachments"] = attachments
                try:
                    resp = httpx.post(
                        f"{COUNCIL_API}/council/message",
                        json=payload,
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
_last_watchdog_run: str = ""
_last_security_run: str = ""


def main():
    global _morning_sent, _afternoon_sent, _evening_sent, _health_sent, _last_watchdog_run, _last_security_run
    log.info("kai-scheduler started — briefs at 9:15/12:30/20:00 ET + Telegram polling")

    tg_thread = threading.Thread(target=telegram_poll_loop, daemon=True, name="telegram-poll")
    tg_thread.start()

    while True:
        now = datetime.now(ET)
        date_str = now.strftime("%Y-%m-%d")

        if now.hour == 9 and now.minute == 15 and _morning_sent != date_str:
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


        # Security watchdog — hourly
        _security_key = f"{now.hour}"
        if now.minute < 1 and _last_security_run != _security_key:
            _last_security_run = _security_key
            try:
                run_security_checks()
            except Exception as e:
                log.error(f"Security watchdog error: {e}")


        _watchdog_key = f"{now.hour}:{now.minute}"
        if now.minute in (0, 30) and _last_watchdog_run != _watchdog_key:
            _last_watchdog_run = _watchdog_key
            try:
                run_watchdog_checks()
            except Exception as e:
                log.error(f"Watchdog error: {e}")

        time.sleep(30)


if __name__ == "__main__":
    main()
