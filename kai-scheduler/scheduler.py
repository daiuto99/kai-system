"""
kai-scheduler — Sprint 24
- Telegram long polling (routes messages to KAI council)
"""
import time
import logging
import threading
import os
from datetime import datetime, date as _date, timedelta as _td, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
import httpx
import concurrent.futures
from worker_auth import worker_auth
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
               username: str = "KAI", icon_url: str = "https://kai.sonicink.space/avatar-kai.png"):
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
                           "user_id": f"telegram:{username}", "history": [],
                           "trigger_source": f"telegram:dm:{advisor}"}
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


def _fetch_worker_health(timeout: float = 10):
    """Real scheduler→worker transport used by the health job and invariant."""
    return httpx.get(
        f"{WORKER_API}/system/health",
        timeout=timeout,
        auth=worker_auth(),
    )


def check_worker_health():
    try:
        r = _fetch_worker_health()
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
            slack_post(slack_token, "devops", msg)
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
    """Post check-in questions to #devops and store the thread ts for reply detection."""
    try:
        r = httpx.post(
            f"{WORKER_API}/checkin/send",
            json={"checkin_type": checkin_type, "channel": "devops"},
            timeout=20,
            auth=worker_auth(),
        )
        result = r.json() if r.status_code == 200 else {}
        if result.get("ok"):
            log.info("checkin sent to Slack: %s ts=%s", checkin_type, result.get("ts"))
        else:
            log.error("checkin send failed: %s", result)
    except Exception as e:
        log.error("checkin send error (%s): %s", checkin_type, e)


def _n8n_oauth_health_job():
    """KAI-432 / N8N-2: hourly check of n8n + OAuth health, alert #devops on debounced failure."""
    import json as _json
    import subprocess
    from pathlib import Path as _Path

    HEALTH_FILE = _Path("/vault/00_System/n8n_health.json")
    checks = {}
    overall_ok = True

    # Check 1: kai-n8n container running
    try:
        r = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Running}}", "kai-n8n"],
            capture_output=True, text=True, timeout=10,
        )
        running = r.stdout.strip() == "true"
        checks["container_running"] = running
        if not running:
            overall_ok = False
    except Exception as e:
        checks["container_running"] = f"error: {e}"
        overall_ok = False

    # Check 2: internal /healthz reachable
    try:
        r = httpx.get("http://kai-n8n:5678/healthz", timeout=5)
        checks["healthz_internal"] = r.status_code == 200
        if r.status_code != 200:
            overall_ok = False
    except Exception as e:
        checks["healthz_internal"] = f"error: {type(e).__name__}"
        overall_ok = False

    # Check 3: external n8n.sonicink.space reachable (Cloudflare tunnel)
    try:
        r = httpx.get("https://n8n.sonicink.space/healthz", timeout=10, follow_redirects=True)
        checks["external_reachable"] = r.status_code in (200, 401, 403)  # auth gate OK
        if r.status_code >= 500:
            overall_ok = False
    except Exception as e:
        checks["external_reachable"] = f"error: {type(e).__name__}"
        # External flake is debounced — don't mark overall_ok=False on first miss

    # Persist + debounce
    now_iso = datetime.now(timezone.utc).isoformat()
    prev = {}
    if HEALTH_FILE.exists():
        try:
            prev = _json.loads(HEALTH_FILE.read_text())
        except Exception:
            prev = {}
    record = {"checked_at": now_iso, "ok": overall_ok, "checks": checks,
              "previous_ok": prev.get("ok", True)}
    HEALTH_FILE.parent.mkdir(parents=True, exist_ok=True)
    HEALTH_FILE.write_text(_json.dumps(record, indent=2))

    # Alert on TWO consecutive failures
    if (not overall_ok) and (prev.get("ok") is False):
        token = load_secret("slack_bot_token")
        if token:
            fail_lines = [f"• {k}: {v}" for k, v in checks.items() if v is not True]
            msg = ("*n8n health check failed twice in a row* — investigate before workflows start dropping silently.\n"
                   + "\n".join(fail_lines)
                   + f"\n\nRecovery: see `scripts/n8n_oauth_recover.md`")
            slack_post(token, "#devops", msg,
                       username="DevOps",
                       icon_url="https://kai.sonicink.space/avatar-devops.png")
        log.warning("n8n health: alerted #devops (2x failure) — %s", checks)
    elif not overall_ok:
        log.info("n8n health: first failure — debouncing, will alert next tick if still broken")
    else:
        log.info("n8n health: ok")


def _contract_test_job():
    """KAI-459 Layer 2 — endpoint contract smoke tests run nightly at 04:00 ET.

    Hits every safe GET endpoint on worker-api + council-api, verifies no 5xx
    response, writes result to /vault/00_System/contract_test_results.json.
    The inv_endpoint_contracts invariant reads that file and fails on stale
    results or any contract failures — alert routes through the §6 CRITICAL
    invariant path.
    """
    try:
        from contract_tests import run
        result = run()
        log.info("contract tests: %s", result["summary"])
    except Exception as e:
        log.error("contract test job failed: %s", e)


def _heartbeat_job():
    """KAI dead-man's-switch: push heartbeat to healthchecks.io every 5 min.

    External monitor — if this stops firing for ~10 min (grace period set on
    the healthchecks.io check), they alert Leo via Slack webhook / Telegram /
    email. None of those alert paths route through the KAI worker, so they
    survive the worker being completely down.

    Body is a compact state summary the dashboard shows alongside the ping.
    """
    url = load_secret("healthchecks_url")
    if not url:
        log.warning("heartbeat: no healthchecks_url secret — skipping")
        return
    try:
        # Compact state summary — included in ping body for the HC dashboard
        import json as _j
        from pathlib import Path as _P
        summary = {"ts": datetime.now(ET).isoformat()}
        try:
            inv = _P("/vault/00_System/invariants.json")
            if inv.exists():
                idata = _j.loads(inv.read_text())
                vals = idata.get("invariants", {})
                summary["invariants"] = (
                    f"{sum(1 for v in vals.values() if v.get('pass'))}/{len(vals)}"
                )
        except Exception:
            pass
        try:
            ph = _P("/vault/_persona_health.json")
            if ph.exists():
                summary["persona_health_issues"] = len(_j.loads(ph.read_text()))
        except Exception:
            pass
        httpx.post(url, json=summary, timeout=10)
    except Exception as e:
        # Failing to ping is itself a signal — by NOT pinging, we let the
        # external monitor alert. Log locally for debugging.
        log.warning("heartbeat: ping failed (%s) — external monitor will alert", e)


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
            with httpx.Client(timeout=15, auth=worker_auth()) as hc:
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

    def _sprint_a_expire_job():
        try:
            with httpx.Client(timeout=15, auth=worker_auth()) as hc:
                r = hc.post(
                    f"{WORKER_API}/sprint-a/expire-stale",
                    json={"expiry_hours": 24, "notify_channel": "#devops"},
                )
            reg_record("sprint_a_expire", "ok", duration_s=0)
        except Exception as e:
            reg_record("sprint_a_expire", "fail", error=str(e))
            log.error("sprint_a expire job error: %s", e)

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
                            "channel": "#devops",
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
    sched.add_job(_sprint_a_expire_job,                  IntervalTrigger(hours=1),    id="sprint_a_expire", coalesce=True, max_instances=1)
    sched.add_job(_n8n_oauth_health_job,                 IntervalTrigger(hours=1),    id="n8n_health", coalesce=True, max_instances=1)
    sched.add_job(_heartbeat_job,                        IntervalTrigger(minutes=5),  id="heartbeat",  coalesce=True, max_instances=1)
    sched.add_job(_contract_test_job,                    CronTrigger(hour=4, minute=0, timezone=tz), id="contract_tests", coalesce=True, max_instances=1)
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
