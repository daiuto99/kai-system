"""
KAI Invariant Engine — S5-2 core + batch 1 + batch 2 (23 invariants).
Runs every 30 min via scheduler. Writes /vault/00_System/invariants.json.
Posts Slack + files Plane issue on pass→fail transition. Deduped: one Plane
issue per failure period (cleared on recovery). Kill switch: set
INVARIANT_RUNNER_ENABLED=false to skip all checks entirely (exit immediately).
D5 conservative auto-remediation: stale-job abandon, transport-probe map
update, workspace re-sync trigger. Each logs + readback-verifies its effect.
"""
import json
import logging
import os
import ssl
import socket
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
import httpx

log = logging.getLogger(__name__)

VAULT_PATH       = Path("/vault")
RESULT_PATH      = VAULT_PATH / "00_System" / "invariants.json"
WORKER_API       = "http://kai-worker-api:8001"
COUNCIL_API      = "http://kai-council-api:8002"
ORCHESTRATOR_API = "http://kai-orchestrator:8003"
MCP_API          = "http://kai-mcp-api:8003"
OLLAMA_API       = "http://kai-ollama:11434"

# Kill switch — INVARIANT_RUNNER_ENABLED=false skips all checks entirely (kill-switch-first)
_RUNNER_ENABLED = os.environ.get("INVARIANT_RUNNER_ENABLED", "true").lower() != "false"

# State tracking for pass→fail / fail→pass transition detection
_prev_state: dict[str, bool] = {}
_daily_digest_sent: str = ""   # date string (YYYY-MM-DD)

# Dedup: one Plane issue per failure period per invariant (cleared on recovery)
_violation_issue_ids: dict[str, int] = {}  # key → Plane sequence_id
_violation_issue_refs: dict[str, dict] = {}  # key → {sequence_id, issue_id}

# Plane constants (mirrors triage.py)
_PLANE_API = "http://host.docker.internal:8090/api/v1"
_PLANE_WS  = "sonicink"
_KAI_PROJECT = "78c49227-82d4-477d-a920-66b08cb91c56"
_plane_backlog_state_id: str | None = None


def _load_secret(name: str) -> str:
    p = Path(f"/run/secrets/{name}")
    if p.exists():
        # Take first line only — some secret files carry extra metadata on line 2
        lines = p.read_text().splitlines()
        return lines[0].strip() if lines else ""
    return os.environ.get(name.upper(), "")


def _slack_post(token: str, text: str):
    try:
        httpx.post(
            "https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {token}"},
            json={"channel": "#devops", "text": text,
                  "username": "KAI Invariants", "icon_emoji": ":shield:"},
            timeout=10,
        )
    except Exception as e:
        log.error("invariant slack post failed: %s", e)


def _get_plane_backlog_state() -> str | None:
    global _plane_backlog_state_id
    if _plane_backlog_state_id:
        return _plane_backlog_state_id
    token = _load_secret("plane_api_token")
    if not token:
        return None
    try:
        r = httpx.get(
            f"{_PLANE_API}/workspaces/{_PLANE_WS}/projects/{_KAI_PROJECT}/states/",
            headers={"X-API-Key": token}, timeout=10
        )
        for s in r.json().get("results", []):
            if s["group"] == "backlog":
                _plane_backlog_state_id = s["id"]
                return _plane_backlog_state_id
    except Exception as e:
        log.warning("invariants: could not resolve Plane backlog state: %s", e)
    return None


def _restore_invariant_issue_refs() -> None:
    """Restore durable failure→issue mappings after scheduler restarts."""
    if _violation_issue_ids or not RESULT_PATH.exists() or RESULT_PATH.stat().st_size == 0:
        return
    try:
        previous = json.loads(RESULT_PATH.read_text())
        refs = previous.get("open_issue_refs", {})
        for key, ref in refs.items():
            seq = ref.get("sequence_id")
            issue_id = ref.get("issue_id")
            if isinstance(seq, int) and issue_id:
                _violation_issue_ids[key] = seq
                _violation_issue_refs[key] = {
                    "sequence_id": seq,
                    "issue_id": issue_id,
                }
    except Exception as e:
        log.error("invariants: failed to restore issue mappings: %s", e)


def _mapped_issue_is_open(key: str) -> bool | None:
    """Return True/False for a verified mapping, None when Plane is unreadable."""
    ref = _violation_issue_refs.get(key)
    if not ref:
        # Legacy/in-process tests may only carry the sequence map. A newly filed
        # real issue always gets a durable ref below.
        return True if key in _violation_issue_ids else False
    token = _load_secret("plane_api_token")
    if not token:
        return None
    headers = {"X-API-Key": token}
    try:
        states_r = httpx.get(
            f"{_PLANE_API}/workspaces/{_PLANE_WS}/projects/{_KAI_PROJECT}/states/",
            headers=headers, timeout=10,
        )
        states_r.raise_for_status()
        states_data = json.loads(states_r.text)
        closed_ids = {
            state["id"] for state in states_data.get("results", [])
            if state.get("group") in ("completed", "cancelled")
        }
        issue_r = httpx.get(
            f"{_PLANE_API}/workspaces/{_PLANE_WS}/projects/{_KAI_PROJECT}"
            f"/issues/{ref['issue_id']}/",
            headers=headers, timeout=10,
        )
        issue_r.raise_for_status()
        return json.loads(issue_r.text).get("state") not in closed_ids
    except Exception as e:
        log.error("invariants: could not verify open issue for %s: %s", key, e)
        return None


def _file_invariant_issue(key: str, label: str, detail: str) -> None:
    """File a Plane bug for an invariant violation. Deduped: no-op if issue already open."""
    if key in _violation_issue_ids:
        log.info("invariants: dedup — %s already has open issue KAI-%s", key, _violation_issue_ids[key])
        return
    token = _load_secret("plane_api_token")
    if not token:
        log.error("invariants: plane_api_token not available — cannot file issue for %s", key)
        return
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    body: dict = {
        "name": f"[INV] {label} — {now_str}",
        "description_html": (
            f"<p><strong>Invariant:</strong> <code>{key}</code></p>"
            f"<p><strong>Failure detail:</strong> {detail[:500]}</p>"
            f"<p><strong>Detected:</strong> {now_str}</p>"
            f"<p>Auto-filed by KAI Invariant Engine. Resolve when the invariant "
            f"passes consistently. No duplicate will be filed while this issue is open.</p>"
        ),
        "priority": "high",
    }
    state_id = _get_plane_backlog_state()
    if state_id:
        body["state"] = state_id
    try:
        r = httpx.post(
            f"{_PLANE_API}/workspaces/{_PLANE_WS}/projects/{_KAI_PROJECT}/issues/",
            headers={"X-API-Key": token, "Content-Type": "application/json"},
            json=body, timeout=15,
        )
        r.raise_for_status()
        created = json.loads(r.text)
        seq = created.get("sequence_id")
        issue_id = created.get("id")
        if not isinstance(seq, int) or not issue_id:
            raise RuntimeError("Plane create response omitted issue identity")
        readback = httpx.get(
            f"{_PLANE_API}/workspaces/{_PLANE_WS}/projects/{_KAI_PROJECT}"
            f"/issues/{issue_id}/",
            headers={"X-API-Key": token}, timeout=10,
        )
        readback.raise_for_status()
        if state_id and json.loads(readback.text).get("state") != state_id:
            raise RuntimeError("Plane invariant issue readback was not in open backlog state")
        _violation_issue_ids[key] = seq
        _violation_issue_refs[key] = {"sequence_id": seq, "issue_id": issue_id}
        log.info("invariants: filed Plane issue KAI-%s for %s", seq, key)
    except Exception as e:
        log.error("invariants: failed to file Plane issue for %s: %s", key, e)


def _ensure_invariant_issue(key: str, label: str, detail: str) -> bool:
    """Ensure every current non-pass maps to a verified-open Plane issue."""
    if key in _violation_issue_ids:
        is_open = _mapped_issue_is_open(key)
        if is_open is True:
            return True
        if is_open is None:
            return False
        _violation_issue_ids.pop(key, None)
        _violation_issue_refs.pop(key, None)
    _file_invariant_issue(key, label, detail)
    return key in _violation_issue_ids


# ── Invariant definitions ─────────────────────────────────────────────────────

def inv_container_health() -> tuple[bool, str]:
    """All 3 core containers (worker-api, council-api, orchestrator) respond 200."""
    ok_list, fail_list = [], []
    for name, url in [
        ("worker-api",   f"{WORKER_API}/health"),
        ("council-api",  f"{COUNCIL_API}/health"),
        ("orchestrator", f"{ORCHESTRATOR_API}/health"),
    ]:
        try:
            r = httpx.get(url, timeout=5)
            if r.status_code == 200:
                ok_list.append(name)
            else:
                fail_list.append(f"{name}(HTTP {r.status_code})")
        except Exception as e:
            fail_list.append(f"{name}({type(e).__name__})")

    if fail_list:
        return False, "FAIL: " + ", ".join(fail_list) + " | OK: " + ", ".join(ok_list)
    return True, "ok: " + ", ".join(ok_list)


def inv_vault_writability() -> tuple[bool, str]:
    """Can write and delete a sentinel file in /vault/00_System/."""
    sentinel = VAULT_PATH / "00_System" / ".invariant_sentinel"
    t0 = time.monotonic()
    try:
        sentinel.write_text("invariant-check")
        sentinel.unlink()
        ms = int((time.monotonic() - t0) * 1000)
        return True, f"ok — write+delete in {ms}ms"
    except Exception as e:
        return False, f"vault write failed: {e}"


def inv_council_api_latency() -> tuple[bool, str]:
    """Council API /health responds in < 2000ms."""
    THRESHOLD_MS = 2000
    t0 = time.monotonic()
    try:
        r = httpx.get(f"{COUNCIL_API}/health", timeout=5)
        ms = int((time.monotonic() - t0) * 1000)
        if r.status_code == 200 and ms < THRESHOLD_MS:
            return True, f"{ms}ms"
        if r.status_code != 200:
            return False, f"HTTP {r.status_code} in {ms}ms"
        return False, f"slow: {ms}ms > {THRESHOLD_MS}ms threshold"
    except Exception as e:
        return False, f"latency check error: {e}"


def inv_slack_token() -> tuple[bool, str]:
    """Slack bot token passes auth.test."""
    token = _load_secret("slack_bot_token")
    if not token:
        return False, "slack_bot_token secret not mounted"
    try:
        r = httpx.post(
            "https://slack.com/api/auth.test",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        data = r.json()
        if data.get("ok"):
            return True, f"bot={data.get('bot_id','?')}"
        return False, f"auth failed: {data.get('error','unknown')}"
    except Exception as e:
        return False, f"auth.test error: {e}"


def inv_execution_registry_freshness() -> tuple[bool, str]:
    """At least one scheduled function ran in the last 90 minutes."""
    STALE_MINUTES = 90
    try:
        from execution_registry import REGISTRY_PATH
        import sqlite3
        if not REGISTRY_PATH.exists():
            return False, "execution_registry.db not found"
        db = sqlite3.connect(str(REGISTRY_PATH))
        row = db.execute(
            "SELECT function, run_time FROM runs ORDER BY run_time DESC LIMIT 1"
        ).fetchone()
        db.close()
        if not row:
            return False, "no runs recorded"
        fn, run_time = row
        ts = run_time if run_time.endswith("+00:00") else run_time + "+00:00"
        last = datetime.fromisoformat(ts)
        minutes_ago = int((datetime.now(timezone.utc) - last).total_seconds() / 60)
        if minutes_ago > STALE_MINUTES:
            return False, f"stale: {fn} last ran {minutes_ago}min ago (threshold {STALE_MINUTES}min)"
        return True, f"{fn} ran {minutes_ago}min ago"
    except Exception as e:
        return False, f"registry check error: {e}"


def inv_cert_expiry() -> tuple[bool, str]:
    """SSL cert for kai.sonicink.space must not expire within 30 days."""
    WARN_DAYS = 30
    HOST = "kai.sonicink.space"
    try:
        ctx = ssl.create_default_context()
        with ctx.wrap_socket(socket.socket(), server_hostname=HOST) as s:
            s.settimeout(10)
            s.connect((HOST, 443))
            cert = s.getpeercert()
        expires = datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z")
        days_left = (expires - datetime.utcnow()).days
        if days_left < WARN_DAYS:
            return False, f"cert expires in {days_left} days ({expires.strftime('%Y-%m-%d')}) — renew now"
        return True, f"ok — {days_left} days remaining ({expires.strftime('%Y-%m-%d')})"
    except Exception as e:
        return False, f"cert check error: {e}"


def inv_backup_integrity() -> tuple[bool, str]:
    """Backup must have run within 26 hours with a non-zero output file."""
    import re
    backup_log = Path("/backups/backup.log")
    if not backup_log.exists():
        backup_log = VAULT_PATH / "00_System" / "backup.log"
    if not backup_log.exists():
        return False, "backup.log not found at /backups/backup.log"
    lines = backup_log.read_text().splitlines()
    last_ts = None
    for line in reversed(lines):
        if "Backup complete" in line:
            m = re.search(r"\[(\d{8}_\d{6})\]", line)
            if m:
                last_ts = datetime.strptime(m.group(1), "%Y%m%d_%H%M%S")
            break
    if not last_ts:
        return False, "no successful backup found in log"
    hours_since = (datetime.now() - last_ts).total_seconds() / 3600
    if hours_since > 26:
        return False, f"last backup {hours_since:.1f}h ago — expected <26h"
    return True, f"ok — last: {last_ts.strftime('%Y-%m-%d %H:%M')}"


def inv_disk_usage() -> tuple[bool, str]:
    """Root filesystem must be under 85% used."""
    WARN_PCT = 85
    try:
        result = subprocess.run(
            ["df", "--output=pcent", "/"], capture_output=True, text=True, timeout=5
        )
        pct = int(result.stdout.strip().splitlines()[-1].replace("%", "").strip())
        if pct >= WARN_PCT:
            return False, f"{pct}% used — above {WARN_PCT}% threshold"
        return True, f"{pct}% used"
    except Exception as e:
        return False, f"disk check error: {e}"


def inv_llm_latency() -> tuple[bool, str]:
    """Ollama /api/tags responds in < 5000ms (models loaded and API responsive)."""
    THRESHOLD_MS = 5000
    t0 = time.monotonic()
    try:
        r = httpx.get(f"{OLLAMA_API}/api/tags", timeout=8)
        ms = int((time.monotonic() - t0) * 1000)
        if r.status_code == 200:
            models = [m["name"] for m in r.json().get("models", [])]
            if ms > THRESHOLD_MS:
                return False, f"slow: {ms}ms > {THRESHOLD_MS}ms ({len(models)} models)"
            return True, f"{ms}ms — {len(models)} model(s) loaded"
        return False, f"HTTP {r.status_code} in {ms}ms"
    except Exception as e:
        return False, f"ollama unreachable: {e}"


def inv_plane_api_health() -> tuple[bool, str]:
    """Plane is reachable and the configured credential is authenticated."""
    token = _load_secret("plane_api_token")
    if not token:
        return False, "Plane API credential missing"
    try:
        r = httpx.get(
            f"{_PLANE_API}/workspaces/{_PLANE_WS}/projects/{_KAI_PROJECT}/states/",
            headers={"X-API-Key": token},
            timeout=5,
        )
        if r.status_code == 200:
            return True, "ok — authenticated HTTP 200"
        if r.status_code in (401, 403):
            return False, f"Plane authentication rejected — HTTP {r.status_code}"
        return False, f"Plane API failed — HTTP {r.status_code}"
    except Exception as e:
        return False, f"plane unreachable: {e}"


# Per-advisor required blocks. Source of truth for KAI-458 Slice A.
# Universal requirements + advisor-specific overrides.
_PERSONA_UNIVERSAL_REQUIRED = ("<background_context>", "<date_reference>", "<current_datetime>")
_PERSONA_ADVISOR_REQUIRED: dict[str, tuple[str, ...]] = {
    "kai":      ("<org_model>",),
    "dev":      ("<organization_structure>", "<build_profile>"),
    "creative": ("<organization_structure>", "<build_profile>"),
}


def inv_persona_assembly() -> tuple[bool, str]:
    """Every advisor persona loads without warnings AND contains its required blocks.

    Catches the KAI-457 class of bug — silent degradation of persona prompts
    where the load function swallows errors and returns degraded context.
    Source of truth for advisor-specific block requirements is the dict above.
    """
    try:
        r = httpx.get(f"{COUNCIL_API}/internal/invariants/persona_check", timeout=30)
        if r.status_code != 200:
            return False, f"diagnostic endpoint HTTP {r.status_code}"
        data = r.json()
    except Exception as e:
        return False, f"diagnostic unreachable: {type(e).__name__}: {e}"

    results = data.get("results", {})
    if not results:
        return False, "no advisor results returned"

    failures: list[str] = []
    for advisor, r_data in results.items():
        if not r_data.get("load_ok"):
            failures.append(f"{advisor}: load failed ({r_data.get('error', 'unknown')})")
            continue
        warnings = r_data.get("warnings", [])
        if warnings:
            failures.append(f"{advisor}: {len(warnings)} warning(s) — {warnings[0][:80]}")
        blocks = r_data.get("blocks_present", {})
        required = list(_PERSONA_UNIVERSAL_REQUIRED) + list(_PERSONA_ADVISOR_REQUIRED.get(advisor, ()))
        missing = [b for b in required if not blocks.get(b, False)]
        if missing:
            failures.append(f"{advisor}: missing {','.join(missing)}")

    if failures:
        return False, "FAIL: " + " | ".join(failures[:5])
    return True, f"ok — {len(results)} advisors, all required blocks present, no warnings"


def inv_endpoint_contracts() -> tuple[bool, str]:
    """KAI-459 Layer 2 — every GET endpoint on worker-api + council-api stays
    non-5xx. Reads /vault/00_System/contract_test_results.json written by the
    nightly _contract_test_job. Fails if results are missing, stale (>30h),
    or contain any fail/error entries.
    """
    p = Path("/vault/00_System/contract_test_results.json")
    if not p.exists():
        return False, "no contract test results yet — first nightly run pending"
    try:
        data = json.loads(p.read_text())
    except Exception as e:
        return False, f"contract test results unreadable: {e}"
    try:
        run_at = datetime.fromisoformat(data.get("run_at", ""))
    except Exception:
        return False, "contract test results missing run_at"
    age_h = (datetime.now(timezone.utc) - run_at).total_seconds() / 3600
    if age_h > 30:
        return False, f"contract tests stale — {age_h:.1f}h since last run"
    s = data.get("summary", {})
    bad = s.get("fail", 0) + s.get("error", 0)
    if bad:
        first = next((r for r in data.get("results", [])
                     if r.get("status") in ("fail", "error")), {})
        return False, (f"{bad} contract failure(s) — "
                       f"{first.get('service','?')}{first.get('path','?')} "
                       f"{first.get('status','?')} {first.get('code','')}")
    return True, (f"ok — {s.get('pass',0)} pass, {s.get('skipped',0)} skipped, "
                  f"{data.get('elapsed_s',0)}s")


def inv_no_secrets_in_vault_docs() -> tuple[bool, str]:
    """S5R-15 — vault knowledge/history docs must not contain plaintext credentials.
    Scans for base64-encoded basic-auth patterns and htpasswd constructs.
    Excludes operational system files (CLAUDE.md, Dockerfile) that legitimately hold credentials.
    Fails if any match found outside redaction markers.
    """
    import re
    SECRET_PATTERNS = [
        re.compile(r'Authorization[:\s]+Basic\s+[A-Za-z0-9+/]{16,}={0,2}'),
        re.compile(r'htpasswd\s+-c\S*\s+\S+\s+\S+\s+\S{6,}'),
        re.compile(r'curl\s+.*-u\s+\w+:[A-Za-z0-9!@#]{6,}'),
    ]
    # Operational files that legitimately contain the credential
    EXCLUDED_NAMES = {'CLAUDE.md', 'Dockerfile'}
    vault = Path('/vault')
    hits = []
    for md in vault.rglob('*.md'):
        if md.name in EXCLUDED_NAMES:
            continue
        try:
            text = md.read_text(errors='ignore')
        except Exception:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if 'REDACTED' in line:
                continue
            for pat in SECRET_PATTERNS:
                if pat.search(line):
                    hits.append(f'{md.relative_to(vault)}:{lineno}')
                    break
    if hits:
        sample = hits[:3]
        return False, f'{len(hits)} potential secret(s) in vault docs — {sample}'
    return True, 'ok — no plaintext credentials found in vault knowledge docs'


# ── S5-2 first batch — six new invariants ────────────────────────────────────

def inv_ledger_pointer_consistent() -> tuple[bool, str]:
    """next_action must be recent and byte-derived from a live open Plane issue."""
    p = VAULT_PATH / "00_System" / "next_action.json"
    if not p.exists():
        return False, "next_action.json not found in vault — no session order set"
    try:
        data = json.loads(p.read_text())
    except Exception as e:
        return False, f"next_action.json unreadable: {e}"
    action = data.get("action", "").strip()
    if not action:
        return False, "next_action.json has no action field — session pointer missing"
    if data.get("source") != "live_plane_readback" or not data.get("issue_id"):
        return False, "next_action.json missing live-board provenance"
    try:
        written_at = datetime.fromisoformat(data.get("written_at", ""))
        age_h = (datetime.now(timezone.utc) - written_at).total_seconds() / 3600
        if age_h > 48:
            return False, f"next_action.json stale: {age_h:.0f}h since last write — session pointer drifted?"
    except Exception:
        return False, "next_action.json written_at is missing or invalid"

    token = _load_secret("plane_api_token")
    if not token:
        return False, "next_action live check blocked: Plane credential missing"
    project_id = data.get("project_id") or _KAI_PROJECT
    headers = {"X-API-Key": token}
    try:
        states_r = httpx.get(
            f"{_PLANE_API}/workspaces/{_PLANE_WS}/projects/{project_id}/states/",
            headers=headers, timeout=10,
        )
        issue_r = httpx.get(
            f"{_PLANE_API}/workspaces/{_PLANE_WS}/projects/{project_id}"
            f"/issues/{data['issue_id']}/",
            headers=headers, timeout=10,
        )
        states_r.raise_for_status()
        issue_r.raise_for_status()
        states = json.loads(states_r.text).get("results", [])
        issue = json.loads(issue_r.text)
        state = next((s for s in states if s.get("id") == issue.get("state")), {})
        if state.get("group") not in ("backlog", "unstarted", "started"):
            return False, f"next_action points to non-open Plane state {state.get('name')!r}"
        expected = (
            f"KAI-{issue.get('sequence_id')} ({issue.get('id')}, {state.get('name')}, "
            f"{issue.get('priority', 'none')}) — {issue.get('name')}"
        )
        if action != expected:
            return False, "next_action content differs from live Plane readback"
        return True, f"ok — live open Plane issue KAI-{issue.get('sequence_id')}"
    except Exception as e:
        return False, f"next_action live Plane readback failed: {type(e).__name__}: {e}"


def inv_internal_worker_auth() -> tuple[bool, str]:
    """Recovery-Plan Step 1 (Bug 48f85706/aec2d486) — regression guard for the
    internal-auth class fix. Broadened 2026-07-11 after independent review
    (docs/reviews/internal-auth-codex-review.md, finding F4) found the
    original version service-local: it proved only the worker boundary itself
    plus the scheduler's own credential, and could stay green while any other
    caller (e.g. orchestrator's calendar path) silently regressed to 401.

    Three legs, ALL must hold — the board goes RED if any caller class drops:

      (1) Worker boundary + real scheduler caller — an unauthenticated call
          to a protected worker route returns 401 (proves the fix isn't a
          network-origin bypass / middleware disable — BUG-18's failure
          mode), then scheduler._fetch_worker_health(), the same transport
          used by the scheduled health job, returns 200. This deliberately
          resolves worker_auth from scheduler.py, so a regression in
          scheduler.worker_auth turns the invariant red.
      (2) Orchestrator caller — a live round trip through
          kai-orchestrator's calendar.get_events CAPABILITY (not a direct
          worker call — this exercises orchestrator's own auth wiring,
          capabilities/calendar.py, end to end) returns ok=true. This is the
          exact caller F2 fixed and Codex's review found live-401.
      (3) MCP caller — a live round trip through kai-mcp-api's get_tasks
          TOOL (its `_call_worker()` choke point) succeeds, not a JSON-RPC
          error. Exercises a second, independent caller's credential mount.

    Caller classes this invariant still cannot observe from a single runtime
    probe (recorded, not silently assumed green): calendar.create_event is a
    mutating external write and is therefore NOT live-probed here; its worker
    auth argument is held by the static internal-auth guard, while this runtime
    leg covers only calendar.get_events. kai-slack-bot has no
    inbound HTTP surface to trigger a round trip through (Socket Mode /
    event-driven only — Codex's own review used a direct loader+transport
    check instead, see internal-auth-codex-review.md); n8n is an explicit,
    recorded accepted-risk pending S7-9 retirement, not probed here.

    'Fixed' means 'can't silently un-fix': if a future change disables the
    middleware, this fails on (1); if any caller's credential mount or wiring
    breaks, it fails on (1), (2), or (3) respectively — not just (1) as
    before.
    """
    url = f"{WORKER_API}/system/ops-state"
    try:
        r_noauth = httpx.get(url, timeout=5)  # GUARD: intentional-unauthenticated-probe
    except Exception as e:
        return False, f"worker unreachable (no-auth probe): {type(e).__name__}: {e}"
    if r_noauth.status_code != 401:
        return False, (
            f"SECURITY: unauthenticated {url} returned HTTP {r_noauth.status_code}, "
            f"expected 401 — worker auth is NOT enforced (origin bypass / middleware off?)"
        )

    try:
        # Import at call time: scheduler imports this module during startup.
        # By invariant execution time the scheduler module is initialized, and
        # this invokes the exact helper used by check_worker_health().
        import scheduler

        r_auth = scheduler._fetch_worker_health(timeout=5)
    except Exception as e:
        return False, f"scheduler worker-health caller failed: {type(e).__name__}: {e}"
    if r_auth.status_code != 200:
        return False, (
            "scheduler._fetch_worker_health returned HTTP "
            f"{r_auth.status_code}, expected 200 — scheduler caller auth is broken"
        )

    try:
        r_orch = httpx.post(
            f"{ORCHESTRATOR_API}/capability/calendar.get_events",
            json={"days": 1}, timeout=10,
        )
    except Exception as e:
        return False, f"orchestrator unreachable (calendar round-trip): {type(e).__name__}: {e}"
    if r_orch.status_code != 200 or not r_orch.json().get("ok"):
        return False, (
            f"orchestrator calendar.get_events did not succeed (HTTP {r_orch.status_code}, "
            f"body={r_orch.text[:200]}) — orchestrator's worker credential mount/wiring is broken"
        )

    try:
        r_mcp = httpx.post(
            f"{MCP_API}/",
            json={"jsonrpc": "2.0", "id": "inv-internal-worker-auth", "method": "tools/call",
                  "params": {"name": "get_tasks", "arguments": {}}},
            timeout=10,
        )
    except Exception as e:
        return False, f"kai-mcp-api unreachable (get_tasks round-trip): {type(e).__name__}: {e}"
    if r_mcp.status_code != 200 or "error" in (r_mcp.json() or {}):
        return False, (
            f"kai-mcp-api get_tasks did not succeed (HTTP {r_mcp.status_code}, "
            f"body={r_mcp.text[:200]}) — MCP's worker credential mount/wiring is broken"
        )

    return True, (
        "ok — worker enforces auth (401 unauth), real scheduler health caller "
        "authenticates (200), orchestrator calendar.get_events authenticates, "
        "MCP get_tasks authenticates; calendar.create_event is NOT runtime-probed "
        "(mutating write; static guard only)"
    )


def inv_secret_files_permissions() -> tuple[bool, str]:
    """S5-3 — all Docker secrets files must have mode 600 (not world/group-readable)."""
    secrets_dir = Path("/run/secrets")
    if not secrets_dir.exists():
        # Docker secrets not mounted in this context — vacuously ok (nothing to check).
        return True, "not applicable — /run/secrets not mounted (docker secrets absent in this context)"
    bad = []
    ok_count = 0
    for f in sorted(secrets_dir.iterdir()):
        if not f.is_file():
            continue
        mode_oct = oct(f.stat().st_mode)[-3:]
        if mode_oct != "600":
            bad.append(f"{f.name}:{mode_oct}")
        else:
            ok_count += 1
    if bad:
        return False, f"insecure permissions on {len(bad)} secret(s): {', '.join(bad)}"
    return True, f"ok — {ok_count} secret file(s) all mode 600"


def inv_no_wp_password_in_vault_json() -> tuple[bool, str]:
    """S5-3 — vault JSON files must not contain plaintext credentials (complement to vault_docs scan).
    Catches passwords embedded in JSON config files, not just markdown docs.
    """
    import re
    JSON_CRED_PATTERNS = [
        re.compile(r'"password"\s*:\s*"[^"]{6,}"'),
        re.compile(r'"api_key"\s*:\s*"[^"]{10,}"'),
        re.compile(r'"secret"\s*:\s*"[^"]{8,}"'),
        re.compile(r'"Authorization"\s*:\s*"Basic\s+[A-Za-z0-9+/]{16,}'),
    ]
    EXCLUDED_NAMES = {"invariants.json", "contract_test_results.json"}
    hits = []
    for jf in VAULT_PATH.rglob("*.json"):
        if jf.name in EXCLUDED_NAMES:
            continue
        try:
            text = jf.read_text(errors="ignore")
        except Exception:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if "REDACTED" in line or "placeholder" in line.lower():
                continue
            for pat in JSON_CRED_PATTERNS:
                if pat.search(line):
                    hits.append(f"{jf.relative_to(VAULT_PATH)}:{lineno}")
                    break
    if hits:
        return False, f"{len(hits)} potential credential(s) in vault JSON: {hits[:3]}"
    return True, "ok — no plaintext credentials found in vault JSON files"


def inv_capability_transports_healthy() -> tuple[bool, str]:
    """S5-3 — key external capability transports must be reachable.
    Checks Todoist REST API (core task capability) using mounted todoist_api_key.
    """
    token = _load_secret("todoist_api_key")
    if not token:
        return False, "todoist_api_key not mounted — Todoist transport unchecked"
    try:
        r = httpx.get(
            "https://api.todoist.com/rest/v2/projects",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        if r.status_code == 200:
            count = len(r.json())
            return True, f"ok — Todoist: {count} project(s)"
        if r.status_code == 410:
            # REST v2 endpoint deprecated (410 Gone). Nothing in KAI consumes Todoist
            # today; deferred until transport is rebuilt or formally retired in S7.
            # Decision logged: KAI Leo 2026-07-10 — defer-and-note.
            return True, "Todoist REST v2 deprecated (410 Gone) — transport deferred; no active consumer (S7)"
        return False, f"Todoist API HTTP {r.status_code}"
    except Exception as e:
        return False, f"Todoist transport unreachable: {type(e).__name__}: {e}"


def inv_vault_backup_skip_manifest() -> tuple[bool, str]:
    """S5R-23 — backup skip manifest must not contain 00_System or 60_Council paths.
    A skip of system-critical vault paths is a backup integrity gap.
    """
    manifest = Path("/backups/skip_manifest.txt")
    if not manifest.exists():
        return True, "ok — no skip manifest (no files skipped in last backup)"
    try:
        lines = [l.strip() for l in manifest.read_text().splitlines() if l.strip()]
    except Exception as e:
        return False, f"skip manifest unreadable: {e}"
    critical = [l for l in lines if "00_System" in l or "60_Council" in l or "00_system" in l]
    if critical:
        return False, f"{len(critical)} critical path(s) skipped in backup: {critical[0]}"
    return True, f"ok — {len(lines)} skip(s), none in critical paths"


def inv_audit_log_integrity() -> tuple[bool, str]:
    """S5R-3 — capability audit log must exist and not be empty (destructive op trail).
    The log is written by the destructive-op guard on every operator action.
    Absence means the guard is not firing or the audit trail was deleted.
    """
    audit_log = VAULT_PATH / "00_System" / "capability_audit.jsonl"
    if not audit_log.exists():
        return False, "capability_audit.jsonl not found — destructive op audit trail missing"
    try:
        lines = [l for l in audit_log.read_text().splitlines() if l.strip()]
    except Exception as e:
        return False, f"audit log unreadable: {e}"
    if not lines:
        return False, "capability_audit.jsonl is empty — no ops have been audited"
    try:
        last = json.loads(lines[-1])
        ts = last.get("timestamp", last.get("ts", "unknown"))
    except Exception:
        ts = "unparseable"
    return True, f"ok — {len(lines)} audit entry(ies), last: {str(ts)[:19]}"


# ── S5-3 batch 2 — four new invariants ───────────────────────────────────────

def inv_no_override_without_ack() -> tuple[bool, str]:
    """S5-3 — capability_audit.jsonl must have no entries with empty operator in last 72h.
    Unacknowledged overrides mean destructive ops ran outside the operator gate.
    """
    audit_log = VAULT_PATH / "00_System" / "capability_audit.jsonl"
    if not audit_log.exists():
        return True, "ok — no audit log yet (no destructive ops logged)"
    cutoff = datetime.now(timezone.utc) - timedelta(hours=72)
    unacked: list[str] = []
    try:
        for raw in audit_log.read_text().splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                entry = json.loads(raw)
            except Exception:
                continue
            ts_raw = entry.get("ts") or entry.get("timestamp", "")
            try:
                ts = datetime.fromisoformat(ts_raw)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
            except Exception:
                continue
            if ts < cutoff:
                continue
            if not entry.get("operator", "").strip():
                ep = entry.get("endpoint", "?")
                unacked.append(f"{ts_raw[:16]}/{ep}")
    except Exception as e:
        return False, f"audit log read error: {e}"
    if unacked:
        return False, f"{len(unacked)} unacknowledged override(s) in last 72h: {unacked[0]}"
    return True, "ok — no unacknowledged overrides in last 72h"


def inv_all_closed_issues_have_td() -> tuple[bool, str]:
    """S5-3 — sample recent human-managed closed Plane issues; each must have ≥1 comment.
    Catches KAI practice drift: closed tickets with no design doc or session close note.
    NOTE: Plane's server-side state filter is broken (ignored); filtering is done client-side.
    Auto-generated system tickets ([INV], [BUG] System health watchdog) are excluded.
    comment_count field is not returned by Plane API; comments are verified via the comments endpoint.
    """
    _AUTO_SKIP_PREFIXES = ("[INV] ", "[BUG] System health watchdog")

    token = _load_secret("plane_api_token")
    if not token:
        return False, "plane_api_token not mounted — cannot check closed issues"
    try:
        # Fetch states to build UUID→group map (client-side filtering required)
        r = httpx.get(
            f"{_PLANE_API}/workspaces/{_PLANE_WS}/projects/{_KAI_PROJECT}/states/",
            headers={"X-API-Key": token}, timeout=10
        )
        r.raise_for_status()
        done_ids: set[str] = {
            s["id"] for s in r.json().get("results", [])
            if s["group"] in ("done", "cancelled")
        }
        if not done_ids:
            return True, "ok — no done/cancelled states found"

        # Fetch recent issues — no state filter (Plane server-side filter is broken;
        # state param is silently ignored). Client-side filter below.
        r2 = httpx.get(
            f"{_PLANE_API}/workspaces/{_PLANE_WS}/projects/{_KAI_PROJECT}/issues/",
            headers={"X-API-Key": token},
            params={"per_page": 50, "order_by": "-updated_at"},
            timeout=15,
        )
        r2.raise_for_status()
        issues = r2.json().get("results", [])

        # Client-side: keep only done/cancelled, exclude auto-generated system tickets
        human_done = [
            i for i in issues
            if i.get("state") in done_ids
            and not any(i["name"].startswith(p) for p in _AUTO_SKIP_PREFIXES)
        ][:5]  # sample up to 5

        if not human_done:
            return True, "ok — no human-managed closed issues in recent window"

        # Verify comments via API (comment_count field not returned by Plane API)
        no_comment: list[str] = []
        for iss in human_done:
            r_c = httpx.get(
                f"{_PLANE_API}/workspaces/{_PLANE_WS}/projects/{_KAI_PROJECT}"
                f"/issues/{iss['id']}/comments/",
                headers={"X-API-Key": token}, timeout=8,
            )
            comments = r_c.json().get("results", []) if r_c.status_code == 200 else []
            if not comments:
                no_comment.append(f"KAI-{iss.get('sequence_id','?')}")

        if no_comment:
            return False, (f"{len(no_comment)} closed issue(s) with no comments "
                           f"(TD missing): {', '.join(no_comment[:3])}")
        return True, f"ok — sampled {len(human_done)} human-managed closed issue(s), all have ≥1 comment"
    except Exception as e:
        return False, f"Plane closed-issues check error: {type(e).__name__}: {e}"


def inv_session_saves_current() -> tuple[bool, str]:
    """S5-3 — session_close_log.json must exist and last entry must be <48h old.
    Stale close log means sessions are not being saved per procedure.
    """
    close_log = VAULT_PATH / "00_System" / "session_close_log.json"
    if not close_log.exists():
        return False, "session_close_log.json not found — no sessions closed yet"
    try:
        data = json.loads(close_log.read_text())
    except Exception as e:
        return False, f"session_close_log.json unreadable: {e}"
    ts_raw = data.get("timestamp", "")
    if not ts_raw:
        return False, "session_close_log.json has no timestamp field"
    try:
        ts = datetime.fromisoformat(ts_raw)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age_h = (datetime.now(timezone.utc) - ts).total_seconds() / 3600
        if age_h > 48:
            return False, f"last session close was {age_h:.0f}h ago — expected <48h"
        return True, f"ok — last close {age_h:.1f}h ago ({data.get('date', '?')})"
    except Exception as e:
        return False, f"session_close_log.json timestamp parse error: {e}"


def inv_workspace_sync_current() -> tuple[bool, str]:
    """S5-3 — git_activity.json must show a commit within 48h (vault sync is live).
    Stale commit activity means the Mac→worker rsync+commit chain has stopped.
    """
    git_log = VAULT_PATH / "00_System" / "git_activity.json"
    if not git_log.exists():
        return False, "git_activity.json not found — git activity tracking not running"
    try:
        entries = json.loads(git_log.read_text())
    except Exception as e:
        return False, f"git_activity.json unreadable: {e}"
    if not entries or not isinstance(entries, list):
        return False, "git_activity.json is empty — no commits recorded"
    first = entries[0]
    committed_at_raw = first.get("committed_at", "")
    if not committed_at_raw:
        return False, "most recent git entry has no committed_at field"
    try:
        ts = datetime.fromisoformat(committed_at_raw)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age_h = (datetime.now(timezone.utc) - ts).total_seconds() / 3600
        msg = first.get("message", "?")[:60]
        if age_h > 48:
            return False, f"last commit {age_h:.0f}h ago — workspace sync stale ({msg})"
        return True, f"ok — last commit {age_h:.1f}h ago: {msg}"
    except Exception as e:
        return False, f"git_activity timestamp parse error: {e}"


# ── D5 conservative auto-remediations ────────────────────────────────────────
# Each remediation logs what it did and readback-verifies its own effect.
# A remediation that claims success without readback is Pattern 1 (watchdog lie).

def _remediate_stale_jobs() -> tuple[bool, str]:
    """D5: write stale_jobs.json marker + readback-verify. Consumed by job runner."""
    marker = VAULT_PATH / "00_System" / "stale_jobs.json"
    now_str = datetime.now(timezone.utc).isoformat()
    payload = {
        "action": "stale_job_abandon",
        "triggered_at": now_str,
        "reason": "execution_registry_freshness invariant failed — no job ran in 90min",
        "consumed": False,
    }
    try:
        marker.write_text(json.dumps(payload, indent=2))
        written = json.loads(marker.read_text())
        assert written["action"] == "stale_job_abandon"
        assert written["triggered_at"] == now_str
        log.info("D5 remediate: stale_jobs.json written+verified at %s", now_str)
        return True, f"stale_jobs.json written+verified at {now_str}"
    except Exception as e:
        log.error("D5 remediate: stale-job abandon failed: %s", e)
        return False, f"stale-job abandon failed: {e}"


def _remediate_transport_probe(failed_key: str, failed_detail: str) -> tuple[bool, str]:
    """D5: update transport_status.json map + readback-verify. Marks transport degraded."""
    status_file = VAULT_PATH / "00_System" / "transport_status.json"
    now_str = datetime.now(timezone.utc).isoformat()
    existing: dict = {}
    if status_file.exists():
        try:
            existing = json.loads(status_file.read_text())
        except Exception:
            pass
    existing[failed_key] = {
        "status": "degraded",
        "last_checked": now_str,
        "detail": failed_detail[:200],
    }
    try:
        status_file.write_text(json.dumps(existing, indent=2))
        written = json.loads(status_file.read_text())
        assert written[failed_key]["status"] == "degraded"
        assert written[failed_key]["last_checked"] == now_str
        log.info("D5 remediate: transport_status.json updated+verified for %s", failed_key)
        return True, f"transport_status.json updated+verified for {failed_key}"
    except Exception as e:
        log.error("D5 remediate: transport probe update failed: %s", e)
        return False, f"transport probe update failed: {e}"


def _remediate_workspace_sync_trigger() -> tuple[bool, str]:
    """D5: write workspace_sync_trigger.json + readback-verify. Worker reads on warmboot."""
    trigger_file = VAULT_PATH / "00_System" / "workspace_sync_trigger.json"
    now_str = datetime.now(timezone.utc).isoformat()
    payload = {
        "action": "request_workspace_resync",
        "triggered_at": now_str,
        "reason": "workspace_sync_current invariant failed — git activity stale >48h",
        "consumed": False,
    }
    try:
        trigger_file.write_text(json.dumps(payload, indent=2))
        written = json.loads(trigger_file.read_text())
        assert written["action"] == "request_workspace_resync"
        assert written["consumed"] is False
        assert written["triggered_at"] == now_str
        log.info("D5 remediate: workspace_sync_trigger.json written+verified at %s", now_str)
        return True, "workspace_sync_trigger.json written+verified"
    except Exception as e:
        log.error("D5 remediate: workspace sync trigger failed: %s", e)
        return False, f"workspace sync trigger failed: {e}"


# Invariant key → D5 remediation function (called on pass→fail transition only)
_D5_REMEDIATIONS: dict[str, object] = {
    "execution_registry_freshness": lambda k, d: _remediate_stale_jobs(),
    "capability_transports_healthy": lambda k, d: _remediate_transport_probe(k, d),
    "workspace_sync_current": lambda k, d: _remediate_workspace_sync_trigger(),
}


# ── Engine ────────────────────────────────────────────────────────────────────

INVARIANTS = [
    # S5-2 batch 0: infrastructure health (original 13)
    ("container_health",              "Container Health",          inv_container_health),
    ("vault_writability",             "Vault Writability",         inv_vault_writability),
    ("council_api_latency",           "Council API Latency",       inv_council_api_latency),
    ("slack_token",                   "Slack Token",               inv_slack_token),
    ("execution_registry_freshness",  "Execution Registry Fresh",  inv_execution_registry_freshness),
    ("cert_expiry",                   "SSL Cert Expiry",           inv_cert_expiry),
    ("backup_integrity",              "Backup Integrity",          inv_backup_integrity),
    ("disk_usage",                    "Disk Usage",                inv_disk_usage),
    ("llm_latency",                   "LLM Latency",               inv_llm_latency),
    ("plane_api_health",              "Plane API Health",          inv_plane_api_health),
    ("persona_assembly",              "Persona Assembly",          inv_persona_assembly),
    ("endpoint_contracts",            "Endpoint Contracts",        inv_endpoint_contracts),
    ("no_secrets_in_vault_docs",      "No Secrets in Vault Docs",  inv_no_secrets_in_vault_docs),
    # S5-2 batch 1: first six (S5-2 + S5-1)
    ("ledger_pointer_consistent",     "Ledger Pointer Consistent", inv_ledger_pointer_consistent),
    ("secret_files_permissions",      "Secret Files Permissions",  inv_secret_files_permissions),
    ("internal_worker_auth",          "Internal Worker Auth",      inv_internal_worker_auth),
    ("no_wp_password_in_vault_json",  "No Password in Vault JSON", inv_no_wp_password_in_vault_json),
    ("capability_transports_healthy", "Capability Transports",     inv_capability_transports_healthy),
    ("vault_backup_skip_manifest",    "Backup Skip Manifest",      inv_vault_backup_skip_manifest),
    ("audit_log_integrity",           "Audit Log Integrity",       inv_audit_log_integrity),
    # S5-3 batch 2: session hygiene + practice discipline
    ("no_override_without_ack",       "No Unacked Override",       inv_no_override_without_ack),
    ("all_closed_issues_have_td",     "Closed Issues Have TD",     inv_all_closed_issues_have_td),
    ("session_saves_current",         "Session Saves Current",     inv_session_saves_current),
    ("workspace_sync_current",        "Workspace Sync Current",    inv_workspace_sync_current),
]

# S5-4: Deliberately deferred invariants — excluded from active checks, shown as
# "deferred" state on dashboard. Remove entry when deferral resolves.
# Host-side seeding note: to test violation+filing without real infra failure,
# write a stale timestamp to the relevant vault file (e.g. session_close_log.json
# with timestamp >48h ago) and run the scheduler manually — do NOT restart the
# container or call the worker API, which would update the real file. Restore
# the original file immediately after confirming filing+dedup+recovery.
DEFERRED_INVARIANTS: dict[str, tuple[str, str]] = {
    "google_calendar": (
        "Google Calendar",
        "Deliberately deferred: n8n OAuth intentionally dead until S7-9 (n8n retirement + calendar transport rebuild)",
    ),
}


def run_invariants(send_daily_digest: bool = False):
    """Run all invariants. Write results to vault. Alert on pass→fail transitions.

    Kill switch first: if INVARIANT_RUNNER_ENABLED=false, returns immediately
    without running any checks (safe fallback for bad-state deploys).
    Deduped Plane filing: one issue filed per failure period; cleared on recovery.
    """
    global _prev_state, _daily_digest_sent

    # ── Kill switch — MUST be first ──────────────────────────────────────────
    if not _RUNNER_ENABLED:
        log.info("invariants: INVARIANT_RUNNER_ENABLED=false — skipping all checks")
        return True, {}

    _restore_invariant_issue_refs()

    now_utc = datetime.now(timezone.utc)
    results: dict[str, dict] = {}
    all_pass = True
    transitions: list[str] = []
    recoveries: list[str] = []
    token = _load_secret("slack_bot_token")

    for key, label, fn in INVARIANTS:
        try:
            passed, detail = fn()
        except Exception as e:
            passed, detail = False, f"invariant error: {e}"

        results[key] = {
            "label":      label,
            "pass":       passed,
            "detail":     detail,
            "checked_at": now_utc.isoformat(),
        }
        if not passed:
            all_pass = False
            mapped = _ensure_invariant_issue(key, label, detail)
            results[key]["issue_mapping"] = "open" if mapped else "FAILED"
            if not mapped:
                results[key]["issue_mapping_error"] = (
                    "non-pass has no verified-open Plane issue"
                )

        # Transition detection: pass→fail and fail→pass
        prev = _prev_state.get(key)
        if prev is True and not passed:
            transitions.append(f"  :x: *{label}*: `{detail}`")
            # D5 conservative auto-remediation (readback-verified, no side effects)
            d5_fn = _D5_REMEDIATIONS.get(key)
            if d5_fn:
                ok_d5, msg_d5 = d5_fn(key, detail)
                log.info("D5[%s]: %s — %s", key, "ok" if ok_d5 else "FAIL", msg_d5)
        elif prev is False and passed:
            recoveries.append(f"  :white_check_mark: *{label}*")
            _violation_issue_ids.pop(key, None)         # clear dedup on recovery
            _violation_issue_refs.pop(key, None)
        _prev_state[key] = passed

    # Append deferred entries — shown as distinct state on dashboard (not pass/fail)
    for key, (label, reason) in DEFERRED_INVARIANTS.items():
        results[key] = {
            "label":      label,
            "pass":       None,
            "deferred":   True,
            "detail":     reason,
            "checked_at": now_utc.isoformat(),
        }

    # Write to vault (includes open_issue_ids per invariant for dashboard display)
    # open_issue_ids: {invariant_key: plane_sequence_id} — set on violation, cleared on recovery
    payload = {
        "updated_at": now_utc.isoformat(),
        "all_pass":   all_pass,
        "runner_enabled": _RUNNER_ENABLED,
        "open_issue_ids": dict(_violation_issue_ids),
        "open_issue_refs": dict(_violation_issue_refs),
        "invariants": results,
    }
    try:
        RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
        RESULT_PATH.write_text(json.dumps(payload, indent=2))
    except Exception as e:
        log.error("invariants: failed to write %s: %s", RESULT_PATH, e)

    # JARVIS §6 Slack alerts
    if transitions and token:
        body = "; ".join(t.lstrip("•").strip() for t in transitions)
        msg = (
            f"CRITICAL — KAI invariant failure at {now_utc.strftime('%H:%M UTC')}. "
            f"You need to take action — check the dashboard. {body}"
        )
        _slack_post(token, msg)
        log.warning("invariants: posted transition alert (%d failures)", len(transitions))

    if recoveries and token:
        body = "; ".join(r.lstrip("•").strip() for r in recoveries)
        msg = (
            f"System Issue Corrected: {body} — corrected by DevOps at "
            f"{now_utc.strftime('%H:%M UTC')}. System Status 100%."
        )
        _slack_post(token, msg)
        log.info("invariants: posted recovery alert (%d recovered)", len(recoveries))

    status = (
        "all_pass" if all_pass
        else f"{sum(1 for r in results.values() if r['pass'] is False)} failing"
    )
    log.info("invariants: %s (runner_enabled=%s)", status, _RUNNER_ENABLED)
    return all_pass, results
