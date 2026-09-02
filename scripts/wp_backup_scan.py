#!/usr/bin/env python3
"""WP-20.6 — Cloudways WP-fleet backup-POLICY reader (read-only).

Wires the MAINTAIN board's `backup` column to a LIVE Cloudways reading instead of
the hardcoded `not_wired` stub. HONESTY (no-theater floor): the Cloudways v1 API
exposes backup POLICY (schedule / retention / local-backups) on GET /server, but
NO last-completed-backup timestamp anywhere on the server or app objects (probed
2026-09-02). So we report the POLICY — a real protected/at-risk reading — and mark
last-run freshness explicitly `not_exposed_by_api`, never a faked timestamp.

Read-only: no Cloudways mutation, only its own state file. The API token is sent
in a POST body only and is never logged or written (L18); no secret-bearing field
(master/mysql/app passwords in the /server payload) is persisted — only backup_*.

Writes vault/00_System/wp_backup_state.json (worker SSOT). The MAINTAIN board
(kai-worker-api routes/wordpress.py) reads it, mapping each site to its server by
cloudways_server_id. A creds/API failure writes {servers:{}, error:...} so the
board shows not_checked WITH the reason — never a faked green.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SECRETS = ROOT / "secrets"
CW_BASE = "https://api.cloudways.com/api/v1"
# Cloudways' WAF 403s the default urllib User-Agent — a real UA is required
# (verified in green_baseline.check_cloudways_auth: UA=None -> 403, UA set -> 200).
UA = "KAI-wp-backup-scan/1.0"
SOURCE = "cloudways_api GET /server (backup policy; last-run time not exposed by API)"


def _vault_dir() -> Path:
    for p in (Path("/vault"), Path("/home/leo/vault")):
        if p.is_dir():
            return p
    return Path("/home/leo/vault")


STATE_FILE = _vault_dir() / "00_System" / "wp_backup_state.json"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cloudways_token(opener=urllib.request.urlopen) -> str | None:
    email = (SECRETS / "cloudways_account_email.txt").read_text().strip()
    api_key = (SECRETS / "cloudways_api_token.txt").read_text().strip()
    data = urllib.parse.urlencode({"email": email, "api_key": api_key}).encode()
    req = urllib.request.Request(
        f"{CW_BASE}/oauth/access_token", data=data, method="POST",
        headers={"User-Agent": UA})
    with opener(req, timeout=20) as resp:
        return json.load(resp).get("access_token")


def _get_servers(token: str, opener=urllib.request.urlopen) -> list:
    req = urllib.request.Request(
        f"{CW_BASE}/server",
        headers={"Authorization": "Bearer " + token, "User-Agent": UA})
    with opener(req, timeout=20) as resp:
        data = json.load(resp)
    servers = data.get("servers") if isinstance(data, dict) else data
    return servers or []


def _default_fetch() -> list:
    token = _cloudways_token()
    if not token:
        raise RuntimeError("Cloudways OAuth returned no access_token")
    return _get_servers(token)


def _flag(v) -> bool:
    """Cloudways returns booleans as STRINGS ('0'/'1') as often as real bools —
    and bool('0') is True in Python, which would read a live server as terminated.
    Cast honestly: '0'/''/'false'/'no'/0/False/None -> False, everything else True."""
    if isinstance(v, str):
        return v.strip().lower() not in ("0", "", "false", "no")
    return bool(v)


def _int(v):
    try:
        return int(str(v).strip())
    except (ValueError, TypeError):
        return None


def policy_for(server: dict) -> dict:
    """Honest backup-POLICY cell for one server (no faked last-run time). Robust to
    Cloudways' string-typed flags/ints (is_terminated='0', backup_retention='8')."""
    freq = server.get("backup_frequency")
    retention = _int(server.get("backup_retention"))
    btime = server.get("backup_time")
    local = _flag(server.get("local_backups"))
    terminated = _flag(server.get("is_terminated"))
    running = str(server.get("status", "")).strip().lower() == "running"
    # A running, non-terminated server with a scheduled off-site backup + retention
    # window = protected; anything else = at_risk. Policy only — the Cloudways API
    # does not expose when the last backup actually ran.
    scheduled = bool(btime) and bool(retention) and retention > 0
    if terminated or not running:
        status = "at_risk"
        detail = (f"server status {server.get('status', '?')!s}"
                  + (" (terminated)" if terminated else "") + " — backups unverifiable")
    else:
        status = "protected" if scheduled else "at_risk"
        detail = (f"Cloudways scheduled backups {'ON' if scheduled else 'OFF'}"
                  + (f" · daily {btime} UTC · {retention}d retention" if scheduled else "")
                  + f" · local_backups {'on' if local else 'off'}"
                  + " · last-run time not exposed by Cloudways API")
    return {
        "label": server.get("label"),
        "status": status,
        "backup_frequency": freq,
        "backup_time": btime,
        "backup_retention": retention,
        "local_backups": local,
        "last_run": "not_exposed_by_api",
        "detail": detail,
    }


def scan(fetch=_default_fetch) -> dict:
    """Return the full state dict. `fetch` is injectable for tests; it returns the
    list of Cloudways server objects (encapsulating token + GET /server)."""
    try:
        servers = fetch() or []
        return {
            "generated_at": now_iso(),
            "source": SOURCE,
            "servers": {str(s.get("id")): policy_for(s)
                        for s in servers if s.get("id") is not None},
            "error": None,
        }
    except Exception as exc:  # honest: a failed reader -> not_checked, never green
        return {"generated_at": now_iso(), "source": SOURCE, "servers": {},
                "error": f"{type(exc).__name__}: {exc}"}


def main() -> int:
    state = scan()
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(STATE_FILE)
    print(json.dumps({"servers": len(state["servers"]), "error": state["error"]}))
    return 1 if state["error"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
