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
from pathlib import Path
import httpx

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
    parts = []

    # Calendar events via n8n
    try:
        r = httpx.get(f"{WORKER_API}/n8n/workflows", timeout=5)
        workflows = r.json().get("workflows", {})
        cal_webhook = None
        for name, cfg in workflows.items():
            if "calendar" in name.lower():
                cal_webhook = cfg.get("webhook_url")
                break
        if cal_webhook:
            r = httpx.post(cal_webhook, json={}, timeout=15)
            events = r.json()
            if isinstance(events, list) and events:
                parts.append("**TODAY'S CALENDAR:**")
                for ev in events[:10]:
                    parts.append(f"  • {ev.get('start','')} — {ev.get('summary', ev.get('title',''))}")
            else:
                parts.append("**CALENDAR:** No events today.")
    except Exception as e:
        log.warning(f"Calendar fetch: {e}")
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


    # Oura health data
    try:
        from datetime import date as _date, timedelta as _td
        oura_token = load_secret("oura_token")
        if oura_token:
            _today = _date.today().isoformat()
            _yesterday = (_date.today() - _td(days=1)).isoformat()
            _headers = {"Authorization": f"Bearer {oura_token}"}
            _base = "https://api.ouraring.com/v2/usercollection"
            _oura_parts = []
            _r = httpx.get(f"{_base}/daily_readiness",
                           params={"start_date": _yesterday, "end_date": _today},
                           headers=_headers, timeout=10)
            _rd = _r.json().get("data", [])
            if _rd:
                _l = _rd[-1]
                _c = _l.get("contributors", {})
                _oura_parts.append(
                    f"  Readiness: {_l.get('score','?')}/100 | "
                    f"HRV balance: {_c.get('hrv_balance','?')} | "
                    f"RHR: {_c.get('resting_heart_rate','?')} | "
                    f"Recovery index: {_c.get('recovery_index','?')}"
                )
            _r = httpx.get(f"{_base}/daily_sleep",
                           params={"start_date": _yesterday, "end_date": _today},
                           headers=_headers, timeout=10)
            _sd = _r.json().get("data", [])
            if _sd:
                _l = _sd[-1]
                _c = _l.get("contributors", {})
                _oura_parts.append(
                    f"  Sleep score: {_l.get('score','?')}/100 | "
                    f"Total: {_c.get('total_sleep','?')} | "
                    f"REM: {_c.get('rem_sleep','?')} | "
                    f"Deep: {_c.get('deep_sleep','?')} | "
                    f"Efficiency: {_c.get('efficiency','?')} | "
                    f"Restfulness: {_c.get('restfulness','?')}"
                )
            if _oura_parts:
                parts.append("\n**OURA HEALTH (last night):**")
                parts.extend(_oura_parts)
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
            json={"channel": "chief", "message": prompt, "user_id": "scheduler"},
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
                        json={"channel": "chief", "message": text, "user_id": f"telegram:{username}"},
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

    while True:
        now = datetime.now()
        date_str = now.strftime("%Y-%m-%d")
        if now.hour == 7 and now.minute == 0 and _brief_sent_date != date_str:
            _brief_sent_date = date_str
            try:
                send_morning_brief()
            except Exception as e:
                log.error(f"Morning brief error: {e}")
        time.sleep(30)


if __name__ == "__main__":
    main()
