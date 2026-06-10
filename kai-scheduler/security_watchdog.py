"""security_watchdog.py — KAI security monitoring
Checks: secret rotation hygiene, API call spike detection, daily audit logging.
Runs hourly from kai-scheduler. Posts alerts to #kai-security.
"""
import json
import logging
import os
import datetime
from pathlib import Path
import httpx

log = logging.getLogger(__name__)

VAULT_PATH       = Path("/vault")
SECRETS_PATH     = Path("/run/secrets")
AUDIT_LOG_PATH   = VAULT_PATH / "00_System" / "security_audit_log.json"
SECRET_MAX_DAYS  = 90
SPIKE_MULTIPLIER = 3.0   # flag if current hour >= 3x historical average
MIN_SPIKE_CALLS  = 5     # ignore spikes below this absolute count

_alert_sent: dict = {}


def _load_secret(name: str) -> str:
    p = SECRETS_PATH / name
    if p.exists():
        return p.read_text().strip()
    return os.environ.get(name.upper(), "")


def _slack_security(text: str):
    """Post to #kai-security, fall back to #kai-system."""
    token = _load_secret("slack_bot_token")
    if not token:
        return
    for channel in ("#devops", "#devops"):
        try:
            r = httpx.post(
                "https://slack.com/api/chat.postMessage",
                headers={"Authorization": f"Bearer {token}"},
                json={"channel": channel, "text": text,
                      "username": "KAI Security", "icon_emoji": ":shield:"},
                timeout=10,
            )
            if r.json().get("ok"):
                return
        except Exception as e:
            log.error(f"Slack security post error ({channel}): {e}")


def _dedup(key: str, window_hours: int = 24) -> bool:
    """Return True if alert should fire (not already sent within window)."""
    now = datetime.datetime.now()
    last = _alert_sent.get(key)
    if last and (now - last).total_seconds() < window_hours * 3600:
        return False
    _alert_sent[key] = now
    return True


def _check_secret_rotation() -> list:
    alerts = []
    if not SECRETS_PATH.exists():
        return alerts
    now = datetime.datetime.now()
    for f in SECRETS_PATH.iterdir():
        try:
            age_days = (now - datetime.datetime.fromtimestamp(f.stat().st_mtime)).days
            if age_days > SECRET_MAX_DAYS:
                alerts.append((f.name, age_days))
        except Exception:
            pass
    return alerts


def _check_call_spike() -> list:
    alerts = []
    try:
        usage_path = VAULT_PATH / "00_System" / "token_usage.json"
        if not usage_path.exists():
            return alerts
        data = json.loads(usage_path.read_text())
        today = datetime.date.today().isoformat()
        cur_hour = datetime.datetime.now().strftime("%H")

        # Historical hourly call counts (exclude current hour)
        historical = []
        for day in data.get("days", []):
            for h, hd in day.get("hours", {}).items():
                if not (day["date"] == today and h == cur_hour):
                    historical.append(hd.get("calls", 0))

        if len(historical) < 5:
            return alerts
        avg = sum(historical) / len(historical)
        if avg < 1:
            return alerts

        day_data = next((d for d in data.get("days", []) if d["date"] == today), None)
        if not day_data:
            return alerts
        cur_calls = day_data.get("hours", {}).get(cur_hour, {}).get("calls", 0)
        if cur_calls >= avg * SPIKE_MULTIPLIER and cur_calls >= MIN_SPIKE_CALLS:
            alerts.append(f"{cur_calls} calls this hour vs avg {avg:.1f}")
    except Exception as e:
        log.error(f"Call spike check error: {e}")
    return alerts


def _write_audit_entry():
    try:
        usage_path = VAULT_PATH / "00_System" / "token_usage.json"
        if not usage_path.exists():
            return
        data = json.loads(usage_path.read_text())
        today = datetime.date.today().isoformat()
        day_data = next((d for d in data.get("days", []) if d["date"] == today), None)
        if not day_data:
            return

        audit = []
        if AUDIT_LOG_PATH.exists():
            try:
                audit = json.loads(AUDIT_LOG_PATH.read_text())
            except Exception:
                pass

        entry = {
            "timestamp": datetime.datetime.now().isoformat(),
            "date": today,
            "calls": day_data.get("calls", 0),
            "cost_usd": round(day_data.get("cost_usd", 0), 6),
            "by_advisor": day_data.get("by_advisor", {}),
            "by_model":   day_data.get("by_model", {}),
        }
        existing = next((e for e in audit if e["date"] == today), None)
        if existing:
            existing.update(entry)
        else:
            audit.append(entry)
        AUDIT_LOG_PATH.write_text(json.dumps(audit[-90:], indent=2))
    except Exception as e:
        log.error(f"Audit write error: {e}")


def run_security_checks():
    """Run all security checks. Called hourly from kai-scheduler."""
    log.info("security watchdog: starting")
    alerts = []

    for name, age in _check_secret_rotation():
        key = f"secret:{name}"
        if _dedup(key, window_hours=168):  # once per week per secret
            alerts.append(f":key: Secret `{name}` not rotated in {age} days (threshold: {SECRET_MAX_DAYS})")

    for msg in _check_call_spike():
        key = f"spike:{datetime.datetime.now().strftime('%Y-%m-%d-%H')}"
        if _dedup(key, window_hours=1):
            alerts.append(f":chart_with_upwards_trend: API call spike — {msg}")

    _write_audit_entry()

    if alerts:
        header = f":shield: *KAI Security Alert* — {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}"
        _slack_security(header + "\n" + "\n".join(alerts))
        log.warning(f"security watchdog: {len(alerts)} alert(s) posted")
    else:
        log.info("security watchdog: all clear")
