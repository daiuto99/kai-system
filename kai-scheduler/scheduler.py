"""
kai-scheduler — Sprint 24
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
from invariants import run_invariants
from execution_registry import record as reg_record
from triage import triage_failure

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
ET            = ZoneInfo("America/New_York")  # default; overridden dynamically in main loop

def _leo_timezone() -> ZoneInfo:
    """Return Leo's current timezone from current_location.json, falling back to ET."""
    try:
        import json as _j
        d = _j.loads((VAULT_PATH / "00_System" / "current_location.json").read_text())
        return ZoneInfo(d.get("timezone", "America/New_York"))
    except Exception:
        return ET


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
                            "/beats /coach /sky /roads /tech /dev /ops")
                    continue
                advisor = "kai"
                message = text
                ADVISORS = {"kai", "beats", "coach", "sky", "roads", "tech", "dev",
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

_afternoon_sent:        str = ""
_health_sent:           str = ""
_morning_checkin_sent:  str = ""
_evening_checkin_sent:  str = ""
_last_watchdog_run: str = ""
_last_security_run: str = ""
_last_invariant_run: str = ""
_last_inbox_scan: str = ""


# ── Calendar-aware location ────────────────────────────────────────────────────

def _update_location_from_calendar():
    """Check calendar events for location changes and update current_location.json."""
    try:
        import json as _j
        loc_file = VAULT_PATH / "00_System" / "current_location.json"
        current_tz = "America/New_York"
        if loc_file.exists():
            current_tz = _j.loads(loc_file.read_text()).get("timezone", current_tz)

        # Fetch gcal events (today + tomorrow)
        r = httpx.post("http://kai-n8n:5678/webhook/kai-calendar-events",
                       json={"days": 2}, timeout=15)
        if r.status_code != 200 or not r.content.strip():
            return
        events = r.json() if isinstance(r.json(), list) else []

        # First event with a non-empty location, sorted by start
        candidates = []
        for ev in events:
            loc = (ev.get("location") or "").strip()
            if not loc:
                continue
            start = ev.get("start", {})
            start_str = start.get("dateTime", start.get("date", "")) if isinstance(start, dict) else str(start)
            candidates.append((start_str, loc))
        candidates.sort(key=lambda x: x[0])
        if not candidates:
            return
        target_loc = candidates[0][1]

        # Geocode via Nominatim
        geo_r = httpx.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": target_loc, "format": "json", "limit": 1},
            headers={"User-Agent": "KAI-Scheduler/1.0 (sonicink.space)"},
            timeout=10,
        )
        if geo_r.status_code != 200 or not geo_r.json():
            return
        geo = geo_r.json()[0]
        lat, lon = float(geo["lat"]), float(geo["lon"])

        # Get timezone
        tz_r = httpx.get(
            "https://timeapi.io/api/TimeZone/coordinate",
            params={"latitude": lat, "longitude": lon},
            timeout=10,
        )
        if tz_r.status_code != 200:
            return
        new_tz = tz_r.json().get("timeZone", "America/New_York")

        if new_tz == current_tz:
            return  # No change

        data = {
            "lat": lat, "lon": lon, "accuracy": None,
            "timezone": new_tz, "city": target_loc,
            "source": "calendar",
            "updated": datetime.now(ZoneInfo(new_tz)).isoformat(),
        }
        loc_file.write_text(_j.dumps(data, indent=2))
        log.info(f"Location auto-updated from calendar: {target_loc} → tz={new_tz}")

    except Exception as e:
        log.debug(f"Calendar location check: {e}")



def send_checkin(checkin_type: str):
    """Post check-in questions to #kai-system and store the thread ts for reply detection."""
    try:
        r = httpx.post(
            f"{WORKER_API}/checkin/send",
            json={"checkin_type": checkin_type, "channel": "kai-system"},
            timeout=20,
        )
        result = r.json() if r.status_code == 200 else {}
        if result.get("ok"):
            log.info("checkin sent to Slack: %s ts=%s", checkin_type, result.get("ts"))
        else:
            log.error("checkin send failed: %s", result)
    except Exception as e:
        log.error("checkin send error (%s): %s", checkin_type, e)


def main():
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger

    log.info("kai-scheduler started — APScheduler + Telegram polling")

    tz = _leo_timezone()

    def _safe(fn_name, fn, *args):
        t0 = time.monotonic()
        try:
            fn(*args)
            reg_record(fn_name, "ok", duration_s=time.monotonic() - t0)
        except Exception as e:
            reg_record(fn_name, "fail", error=str(e))
            log.error("%s error: %s", fn_name, e)
            triage_failure(fn_name, str(e))

    def _inbox_job():
        try:
            with httpx.Client(timeout=15) as hc:
                hc.post(f"{WORKER_API}/inbox/scan")
            reg_record("inbox_scan", "ok")
        except Exception as e:
            reg_record("inbox_scan", "fail", error=str(e))
            log.error("Inbox scan error: %s", e)

    def _invariant_job():
        now_tz = datetime.now(_leo_timezone())
        try:
            run_invariants(send_daily_digest=(now_tz.hour == 8))
        except Exception as e:
            log.error("Invariant engine error: %s", e)

    def _watchdog_job():
        _safe("watchdog", run_watchdog_checks)
        try:
            _update_location_from_calendar()
        except Exception as e:
            log.error("Calendar location check error: %s", e)

    # Reschedule daily CronTrigger jobs when Leo's timezone changes
    _scheduled_tz = [tz]

    def _tz_check_job(sched):
        new_tz = _leo_timezone()
        if str(new_tz) == str(_scheduled_tz[0]):
            return
        log.info("Timezone changed: %s → %s — rescheduling daily jobs", _scheduled_tz[0], new_tz)
        _scheduled_tz[0] = new_tz
        # no daily cron jobs to reschedule


    def _weekly_learning_cron():
        """Monday 07:00 ET -- write weekly learning recap to vault + post Slack."""
        import json as _json
        now_local = datetime.now(_leo_timezone())
        iso_week = now_local.strftime("%Y-W%W")
        vault_dir = Path("/vault/60_Council/learning")
        vault_dir.mkdir(parents=True, exist_ok=True)
        out_path = vault_dir / (iso_week + ".md")
        inv_path = Path("/vault/00_System/invariants.json")
        inv_summary = "Invariant data unavailable"
        if inv_path.exists():
            try:
                inv_data = _json.loads(inv_path.read_text())
                passing = sum(1 for v in inv_data.get("invariants", {}).values() if v.get("pass"))
                total = len(inv_data.get("invariants", {}))
                updated = inv_data.get("updated_at", "?")[:16]
                inv_summary = str(passing) + "/" + str(total) + " passing as of " + updated
            except Exception:
                pass
        date_str = now_local.strftime("%Y-%m-%d %H:%M %Z")
        content = (
            "# KAI Weekly Learning Recap -- " + iso_week + "\n"
            "**Generated:** " + date_str + "\n\n"
            "## System Health\n"
            "- Invariants: " + inv_summary + "\n\n"
            "## This Week (Sprint 6 Learning Loop will fill this in)\n"
            "- Events aggregation: pending Sprint 6\n"
            "- Pattern analysis: pending Sprint 6\n"
            "- Proposal: pending Sprint 6\n\n"
            "## Notes\n"
            "_Add session notes here._\n"
        )
        try:
            out_path.write_text(content)
            log.info("weekly cron: wrote %s", out_path)
        except Exception as e:
            log.error("weekly cron: vault write failed: %s", e)
            return
        token = load_secret("slack_bot_token")
        if token:
            try:
                with httpx.Client(timeout=10) as hc:
                    hc.post(
                        "https://slack.com/api/chat.postMessage",
                        headers={"Authorization": "Bearer " + token},
                        json={
                            "channel": "#kai-system",
                            "text": (
                                ":spiral_calendar_pad: *KAI Weekly -- " + iso_week + "*\n"
                                "Invariants: " + inv_summary + "\n"
                                "Vault: `60_Council/learning/" + iso_week + ".md`\n"
                                "_Sprint 6 will add events + pattern analysis._"
                            ),
                            "username": "KAI Weekly", "icon_emoji": ":calendar:",
                        },
                    )
                log.info("weekly cron: Slack posted for %s", iso_week)
            except Exception as e:
                log.error("weekly cron: Slack failed: %s", e)

    sched = BackgroundScheduler(timezone=tz)

    # Daily brief jobs — CronTrigger in Leo's local timezone
    # BRIEFS PAUSED 2026-05-19 — re-enable when Leo directs
    # morning_checkin, evening_checkin, worker_health_check removed — watchdog covers alerting

    # Periodic jobs
    sched.add_job(_watchdog_job,                         IntervalTrigger(minutes=30), id="watchdog",   coalesce=True, max_instances=1)
    sched.add_job(run_security_checks,                   IntervalTrigger(hours=1),    id="security",   coalesce=True, max_instances=1)
    sched.add_job(_invariant_job,                        IntervalTrigger(minutes=30), id="invariants", coalesce=True, max_instances=1)
    sched.add_job(_inbox_job,                            IntervalTrigger(seconds=60), id="inbox_scan", coalesce=True, max_instances=1)
    sched.add_job(lambda: _tz_check_job(sched),          IntervalTrigger(hours=1),    id="tz_check",   coalesce=True, max_instances=1)
    # weekly_learning_cron removed — no content yet

    sched.start()
    log.info("APScheduler started with %d jobs", len(sched.get_jobs()))

    tg_thread = threading.Thread(target=telegram_poll_loop, daemon=True, name="telegram-poll")
    tg_thread.start()

    try:
        while True:
            time.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        sched.shutdown()


if __name__ == "__main__":
    main()
