"""Council gate endpoint — receives workflow gates from kai-orchestrator,
processes them async, and POSTs resolution back to the callback URL.

Gate types:
  plan_gate       — Leo approves the plan upfront via Slack before work begins
  dev_gate        — LSE reviews brief → KAI quality-checks → Leo approves via Slack
  creative_gate   — Creative Director produces brief → KAI validates (loop max 3x) → Leo approves brief once → built
  devops_gate     — DevOps reviews infra implications, auto-approves routine items

Flow for plan/dev/creative gates:
  1. Orchestrator POSTs to /council/gate
  2. Council processes in background (director review + KAI quality check)
  3. Council posts to Slack (#kai-system) with summary + approve/reject instructions
  4. Gate enters pending_leo state — workflow pauses
  5. Leo replies in Slack → /council/gate/{gate_id}/resolve fires callback
  6. Orchestrator resumes workflow

NOTE: _process_gate is intentionally sync (not async def) so FastAPI's BackgroundTasks
runs it in a thread pool, preventing LangGraph graph.invoke() from blocking asyncio.
"""
import json
import logging
import os
import asyncio
import re
import tempfile
import threading
from pathlib import Path
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel
import function_map as fm
from council_config import WORKER_URL, _worker_auth, ORCHESTRATOR_URL
from autonomy_policy import classify

logger = logging.getLogger(__name__)
router = APIRouter()

_VAULT_GATES = Path("/vault/00_System/gates")
_VAULT_REFERENCES = Path("/vault/60_Council/creative/references")
_BUILD_PROFILES = {
    "creative": Path("/vault/60_Council/creative/BUILD_PROFILE.md"),
    "dev":      Path("/vault/60_Council/dev/BUILD_PROFILE.md"),
}
_GATE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{4,128}$")

# HOSTOPS-(c): privileged host mutations are ALWAYS human-only. They are handled
# explicitly (not via the "unknown gate type" fall-through) so a future
# auto-approve fallback can never silently capture them. There is deliberately no
# code path that resolves one of these without Leo.
_HOSTOPS_GATE_TYPES = frozenset({"hostops_place_secret", "hostops_deploy_plugin"})
_WORDPRESS_SITES = Path("/vault/00_System/wordpress_sites.json")


class ReviewerUnavailable(RuntimeError):
    """A required reviewer did not return a real verdict."""


def _validated_gate_dir(root: Path, gate_id: str) -> Path:
    if not _GATE_ID_RE.fullmatch(gate_id):
        raise ValueError("gate_id must be 4-128 letters, numbers, underscores, or hyphens")
    return root / gate_id


class PersistentGateStore:
    """Small atomic JSON store with an in-process read cache.

    Each gate has its own state.json under the already-mounted vault. Returning
    copies prevents nested dict mutation from bypassing persistence; callers
    must write via __setitem__/_update_gate.
    """

    def __init__(self, root: Path):
        self.root = root
        self._cache: dict[str, dict] = {}
        self._lock = threading.RLock()

    def _path(self, gate_id: str) -> Path:
        return _validated_gate_dir(self.root, gate_id) / "state.json"

    @staticmethod
    def _copy(value: dict) -> dict:
        return json.loads(json.dumps(value))

    def __setitem__(self, gate_id: str, value: dict) -> None:
        if value.get("gate_id") != gate_id:
            raise ValueError("gate payload gate_id does not match key")
        payload = json.dumps(value, indent=2, sort_keys=True)
        path = self._path(gate_id)
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(prefix=".state.", dir=path.parent)
            tmp = Path(tmp_name)
            try:
                os.fchmod(fd, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(payload)
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(tmp, path)
                dir_fd = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
                self._cache[gate_id] = self._copy(value)
            finally:
                tmp.unlink(missing_ok=True)

    def __getitem__(self, gate_id: str) -> dict:
        with self._lock:
            if gate_id not in self._cache:
                path = self._path(gate_id)
                if not path.exists():
                    raise KeyError(gate_id)
                value = json.loads(path.read_text())
                if not isinstance(value, dict) or value.get("gate_id") != gate_id:
                    raise ValueError(f"invalid persisted gate state for {gate_id}")
                self._cache[gate_id] = value
            return self._copy(self._cache[gate_id])

    def get(self, gate_id: str, default=None):
        try:
            return self[gate_id]
        except KeyError:
            return default

    def __delitem__(self, gate_id: str) -> None:
        with self._lock:
            path = self._path(gate_id)
            path.unlink(missing_ok=True)
            self._cache.pop(gate_id, None)

    def update(self, gate_id: str, **changes) -> dict:
        with self._lock:
            entry = self[gate_id]
            entry.update(changes)
            self[gate_id] = entry
            return entry

    def clear_cache(self) -> None:
        """Model a process restart without deleting durable state."""
        with self._lock:
            self._cache.clear()


_GATES_STORE = PersistentGateStore(_VAULT_GATES)

# Approval surface (b19bf598): 'telegram' (default, live) or 'buzz'. When 'buzz',
# the host-side buzz_approve.py poller sends the prompt to Leo's Buzz channel and
# resolves from his verified reply, so we skip the parallel Telegram send below.
_APPROVAL_SURFACE = os.environ.get("GATE_APPROVAL_SURFACE", "buzz").strip().lower()
_BUZZ_HEARTBEAT_PATH = os.environ.get("BUZZ_APPROVAL_HEARTBEAT", "/vault/00_System/buzz_approve_heartbeat")
_BUZZ_HEARTBEAT_MAX_AGE = int(os.environ.get("BUZZ_HEARTBEAT_MAX_AGE", "30"))

def _buzz_alive() -> bool:
    """True iff the Buzz approval poller wrote a heartbeat within the freshness
    window. Fail-safe: any read error / stale beat -> False, so a dead Buzz poller
    makes the Telegram backup fire (Telegram = lifeline when Buzz is down)."""
    try:
        ts = int(Path(_BUZZ_HEARTBEAT_PATH).read_text().strip())
        return (datetime.now(timezone.utc).timestamp() - ts) <= _BUZZ_HEARTBEAT_MAX_AGE
    except Exception:
        return False

# ── Telegram backup ESCALATION (b19bf598 hardening) ───────────────────────────
# When Buzz is primary, a gate prompted on Buzz but not resolved within this window
# is escalated ONCE to the Telegram lifeline (covers relay-dies-after-prompt and
# Leo-did-not-see-Buzz). The heartbeat check handles Buzz-down-at-creation; this
# handles delivered-but-unanswered.
_GATE_ESCALATE_SECONDS = int(os.environ.get("GATE_TELEGRAM_ESCALATE_SECONDS", "900"))
_GATE_ESCALATE_CHECK_SECONDS = int(os.environ.get("GATE_ESCALATE_CHECK_SECONDS", "60"))

def _escalate_stale_gates() -> None:
    """Escalate pending_leo gates unresolved past the window to Telegram (once)."""
    if _APPROVAL_SURFACE != "buzz":
        return
    # Telegram is the LAST-RESORT lifeline: escalate ONLY when the Buzz poller is
    # actually dead (heartbeat stale). If Buzz is alive, Leo is reachable there and
    # buzz_approve re-nudges on Buzz — never ping Telegram just because he is slow.
    if _buzz_alive():
        return
    now = datetime.now(timezone.utc).timestamp()
    try:
        dirs = list(_GATES_STORE.root.iterdir())
    except FileNotFoundError:
        return
    for d in dirs:
        if not d.is_dir():
            continue
        entry = _GATES_STORE.get(d.name)
        if not entry or entry.get("status") != "pending_leo" or entry.get("tg_escalated"):
            continue
        try:
            created = datetime.fromisoformat(entry["created_at"]).timestamp()
        except Exception:
            continue
        if (now - created) < _GATE_ESCALATE_SECONDS:
            continue
        if _tg_alert_buzz_down(d.name, entry.get("gate_type", "gate"), entry.get("summary") or ""):
            _update_gate(d.name, tg_escalated=True)
            logger.warning("Buzz down + gate %s unresolved >%ss — sent Telegram BUZZ-DOWN alert "
                           "(held, not tappable; resolve on Buzz-recovery or keyboard)",
                           d.name, _GATE_ESCALATE_SECONDS)

async def _gate_escalation_loop():
    while True:
        await asyncio.sleep(_GATE_ESCALATE_CHECK_SECONDS)
        try:
            await asyncio.to_thread(_escalate_stale_gates)
        except Exception as e:
            logger.error("gate escalation loop error: %s", e)

def start_gate_escalator() -> None:
    """Launch the escalation loop as a background task (called from app lifespan)."""
    asyncio.create_task(_gate_escalation_loop())


def _update_gate(gate_id: str, **changes) -> dict:
    try:
        return _GATES_STORE.update(gate_id, **changes)
    except KeyError:
        raise KeyError(f"gate {gate_id} not found")


def _load_references(property_name: str) -> str:
    """Load relevant reference files from vault for a given property.

    Returns a formatted string for injection into the Creative Director prompt.
    Filters by property (matches 'all' or the specific property name).
    """
    if not _VAULT_REFERENCES.exists():
        return ""
    refs = []
    for ref_file in sorted(_VAULT_REFERENCES.glob("*.md")):
        if ref_file.name == "README.md":
            continue
        try:
            text = ref_file.read_text()
            # Parse frontmatter
            prop = ""
            url = ""
            relevance = ""
            category = ""
            for line in text.splitlines():
                if line.startswith("url:"):
                    url = line.split("url:", 1)[1].strip()
                elif line.startswith("property:"):
                    prop = line.split("property:", 1)[1].strip()
                elif line.startswith("relevance:"):
                    relevance = line.split("relevance:", 1)[1].strip()
                elif line.startswith("category:"):
                    category = line.split("category:", 1)[1].strip()
            # Filter by property
            prop_match = prop == "all" or not property_name or property_name.lower() in prop.lower()
            if prop_match and url and relevance:
                refs.append(f"- [{category}] IMAGE: {url}\n  WHY: {relevance}")
        except Exception:
            continue
    if not refs:
        return ""
    return "REFERENCE LIBRARY (curated by Leo — use these as mood board sources):\n" + "\n".join(refs)


class GateRequest(BaseModel):
    gate_id:      str
    gate_type:    str = "dev_gate"
    brief:        dict
    callback_url: str


class GateResolve(BaseModel):
    approved: bool
    notes:    str = ""
    resolver: str = "leo"


# ── Telegram gate-approval helpers (AR-5.2) ───────────────────────────────────
# Gate approvals move from Slack to Telegram: the pending_leo prompt is sent to
# Leo's allowed chat with inline approve/reject buttons. The button click is
# handled by kai-worker-api routes/telegram.py, which POSTs back to
# /council/gate/{id}/resolve. Slack ingress (kai-slack-bot) is retired in the
# same ticket, so approvals never strand between surfaces.

_TELEGRAM_API = "https://api.telegram.org"


def _tg_token() -> str:
    p = Path("/run/secrets/telegram_bot_token")
    return p.read_text().strip() if p.exists() else os.environ.get("TELEGRAM_BOT_TOKEN", "")


def _tg_gate_chat_ids() -> list[str]:
    """Allowed chat ids that receive gate-approval prompts (one per line)."""
    p = Path("/run/secrets/telegram_allowed_chat_ids")
    if not p.exists():
        return []
    return [ln.strip() for ln in p.read_text().splitlines() if ln.strip()]


def _plain(s: str) -> str:
    """Strip legacy-Markdown decoration (`*` and backticks) that gate summaries still carry
    from when these messages were sent parse_mode='Markdown'. Now that they go as PLAIN text
    (KAI-1134), that decoration would otherwise render as literal markup noise to Leo.
    Underscores are left intact — they are common in real paths/ids and are not decoration."""
    return (s or "").replace("*", "").replace("`", "")


def _tg_send_gate(gate_id: str, gate_type: str, summary: str) -> bool:
    """Send the pending_leo gate prompt to Telegram with inline approve/reject
    buttons. Returns True if at least one allowed chat was notified.

    L18: never log httpx error text unredacted — it carries /bot<TOKEN>/ in the
    request URL. We log only exception type + status, never the message body.

    KAI-1004: a gate is a genuine approval only Leo can give — it routes to Telegram
    via the single gateway transport (reason="gate"), which owns the raw send, the
    Rule-A log, and the reality gate (test/synthetic suppressed by construction).
    """
    from notify_gateway import send_telegram
    chat_ids = _tg_gate_chat_ids()
    if not chat_ids:
        logger.warning("No telegram_allowed_chat_ids — gate %s not sent to Telegram", gate_id)
        return False

    gate_label = {
        "plan_gate": "Plan Approval", "dev_gate": "Dev Review",
        "creative_gate": "Creative Review", "devops_gate": "DevOps Review",
        "hostops_place_secret": "Host-Op: Place Secret",
        "hostops_deploy_plugin": "Host-Op: Deploy Plugin",
    }.get(gate_type, gate_type)
    icon = {"plan_gate": "📋", "dev_gate": "⚙️", "creative_gate": "🎨",
            "devops_gate": "🔧", "hostops_place_secret": "🔐",
            "hostops_deploy_plugin": "🚀"}.get(gate_type, "🔒")

    # Plain text (no parse_mode): Telegram legacy-Markdown 400s on unescaped dynamic
    # gate content (ids, types, free-text summary) and SILENTLY DROPS the message —
    # an approval/alert surface must never be content-fragile (KAI-1134).
    text = (
        f"{icon} Gate: {gate_label}\n"
        f"{gate_id}\n\n"
        f"{_plain(summary)}\n\n"
        f"Artifacts: vault/00_System/gates/{gate_id}/"
    )
    keyboard = {"inline_keyboard": [[
        {"text": "✅ Approve", "callback_data": f"gate:approve:{gate_id}"},
        {"text": "🛑 Reject",  "callback_data": f"gate:reject:{gate_id}"},
    ]]}

    sent_any = False
    for chat_id in chat_ids:
        if send_telegram(chat_id, text, reason="gate", reply_markup=keyboard):
            sent_any = True
        else:
            logger.warning("Telegram gate send failed for %s", gate_id)
    return sent_any


def _tg_alert_buzz_down(gate_id: str, gate_type: str, summary: str) -> bool:
    """Telegram = EMERGENCY-ONLY (ratified 2026-08-05, ticket 5adcca90). When Buzz is
    DOWN and a gate has gone stale, we do NOT send the tappable approve/reject card —
    Telegram never carries an approval. We send a plain BUZZ-DOWN ALERT (no buttons);
    the gate stays pending_leo and resolves ONLY on Buzz-recovery or the keyboard
    (in-session `YES`). Returns True if at least one allowed chat was alerted."""
    from notify_gateway import send_telegram
    chat_ids = _tg_gate_chat_ids()
    if not chat_ids:
        logger.warning("No telegram_allowed_chat_ids — buzz-down alert for %s not sent", gate_id)
        return False
    # Plain text (no parse_mode): this is the EMERGENCY lifeline — Telegram legacy-Markdown
    # 400s on unescaped dynamic content (gate id/type/summary) and silently drops the
    # alert, so the one message that must always land was the most fragile (KAI-1134).
    text = (
        "🚨 Buzz is DOWN — approval held\n"
        f"{gate_id} ({gate_type})\n\n"
        f"{_plain(summary)}\n\n"
        "This approval is HELD. Recover Buzz (health-check) or resolve at the keyboard. "
        "Telegram is the emergency line only — it cannot approve."
    )
    sent_any = False
    for chat_id in chat_ids:
        if send_telegram(chat_id, text, reason="gate"):
            sent_any = True
        else:
            logger.warning("Telegram buzz-down alert send failed for %s", gate_id)
    return sent_any


# ── Slack helpers ─────────────────────────────────────────────────────────────

def _slack_token() -> str:
    p = Path("/run/secrets/slack_bot_token")
    return p.read_text().strip() if p.exists() else os.environ.get("SLACK_BOT_TOKEN", "")


def _fyi(kind: str, title: str, body: str, source: str = "council_gate") -> None:
    """Route a gate-lifecycle FYI through the notify() chokepoint (KAI-1116).

    Replaces the retired _slack_post Slack no-op: gate-resolve, autonomous
    host-op, approval-send-failure and reviewer-failed-closed notices are
    operational/DevOps telemetry, not approvals — Rule B lands them on the
    dashboard notify log, never Leo's phone. Fail-safe: never raises into the
    gate path (mirrors the old no-op's non-raising contract).
    """
    try:
        from notify_gateway import notify, Event
        notify(Event(source=source, kind=kind, title=title, body=body, audience="dashboard"))
    except Exception:
        logger.exception("notify() FYI failed (kind=%s)", kind)


def _extract_verdict(text: str, fallback: str = "see artifact") -> str:
    """Pull a one-line `VERDICT: <line>` from an advisor response.

    Advisors are prompted to put their headline verdict on a line starting with
    `VERDICT:`. Returns just the line content (no prefix). If absent, returns
    the fallback string — the gate review still produces a valid Slack message,
    and a warning is logged so prompt drift is visible.
    """
    if not text:
        return fallback
    for raw in text.splitlines():
        line = raw.strip()
        if line.upper().startswith("VERDICT:"):
            return line.split(":", 1)[1].strip() or fallback
    logger.warning("No VERDICT: line in advisor response (len=%d) — using fallback", len(text))
    return fallback


# ── API endpoints ─────────────────────────────────────────────────────────────

@router.post("/council/gate")
async def receive_gate(req: GateRequest, background_tasks: BackgroundTasks):
    """Accept a gate from the orchestrator and schedule async processing."""
    _GATES_STORE[req.gate_id] = {
        "gate_id":      req.gate_id,
        "gate_type":    req.gate_type,
        "brief":        req.brief,
        "status":       "processing",
        "resolution":   None,
        "callback_url": req.callback_url,
        "created_at":   datetime.now(timezone.utc).isoformat(),
    }
    background_tasks.add_task(_process_gate, req)
    return {"gate_id": req.gate_id, "status": "accepted"}


# ── Orphaned-gate reaper (a39d7842) ───────────────────────────────────────────
# A gate PAUSES a job at pending_leo. If the job later reaches a terminal state
# (or vanishes) while the gate is still pending_leo, the gate is ORPHANED — the
# workflow is no longer waiting on Leo, but the poller keeps nagging him forever
# (the 6c83ec91 gate nagged ~20h). Reap those so they stop. FAIL-SAFE: any
# uncertainty (no job_id, orchestrator unreachable, non-terminal status) keeps the
# gate — a live gate is NEVER reaped on a transient blip.
_TERMINAL_JOB_STATES = {"succeeded", "failed", "failed_permanent", "cancelled", "orphaned"}


def _gate_is_orphaned(entry: dict) -> bool:
    job_id = (entry.get("brief") or {}).get("job_id")
    if not job_id:
        return False
    try:
        with httpx.Client(timeout=5) as c:
            r = c.get(f"{ORCHESTRATOR_URL}/jobs/{job_id}")
    except httpx.RequestError:
        return False  # transient — never reap on a blip
    if r.status_code != 200:
        return False
    try:
        data = r.json()
    except Exception:
        return False
    if data.get("error"):                       # {"error": "not found"} — job gone
        return True
    status = (data.get("job") or {}).get("status")
    return status in _TERMINAL_JOB_STATES


@router.get("/council/gate/pending")
def list_pending_gates():
    """Gates awaiting Leo (status=pending_leo). Read-only; polled by the Buzz
    approval process (buzz-eval/agent/buzz_approve.py) to send approval prompts."""
    out = []
    try:
        for d in sorted(_GATES_STORE.root.iterdir()):
            if not d.is_dir():
                continue
            entry = _GATES_STORE.get(d.name)
            # Defense in depth (KAI-1112): a synthetic_probe gate must NEVER be surfaced to
            # the buzz poller and prompted to Leo, even if a stray one is ever persisted in
            # pending_leo (e.g. a mixed-version rollout). _process_gate already keeps them out
            # of pending_leo; this is the second lock on that door.
            if entry and entry.get("gate_type") == "synthetic_probe":
                continue
            if entry and entry.get("status") == "pending_leo":
                if _gate_is_orphaned(entry):
                    # job died/terminal but gate stuck pending_leo — reap so it stops
                    # nagging Leo forever (a39d7842 / the 6c83ec91 20-hour incident).
                    try:
                        _GATES_STORE.update(d.name, status="orphaned", resolution={
                            "orphaned": True,
                            "reason": "underlying orchestrator job gone/terminal — auto-reaped",
                            "by": "council.reaper",
                        })
                        logger.info("reaped orphaned gate %s (job gone/terminal)", d.name)
                    except Exception as e:
                        logger.warning("reap failed for %s: %s", d.name, e)
                    continue
                out.append({
                    "gate_id": d.name,
                    "gate_type": entry.get("gate_type"),
                    "summary": entry.get("summary") or "",
                })
    except FileNotFoundError:
        pass
    return {"pending": out, "count": len(out)}


@router.get("/council/gate/{gate_id}/state")
def gate_state(gate_id: str):
    """Fallback poll — orchestrator checks here every 30s if callback was missed."""
    entry = _GATES_STORE.get(gate_id)
    if entry is None:
        return {"error": "gate not found"}
    return {
        "gate_id":        gate_id,
        "status":         entry["status"],
        "resolution":     entry["resolution"],
        "summary":        entry.get("summary"),
        "kai_assessment": entry.get("kai_assessment"),
    }


@router.post("/council/gate/{gate_id}/resolve")
def resolve_gate(gate_id: str, req: GateResolve):
    """Resolve a pending gate — called when Leo approves or rejects via Slack."""
    entry = _GATES_STORE.get(gate_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Gate {gate_id} not found")
    if entry["status"] not in ("pending_leo", "processing"):
        raise HTTPException(status_code=409, detail=f"Gate {gate_id} already in state {entry['status']}")

    resolution = {
        "approved":   req.approved,
        "notes":      req.notes,
        "advisor":    req.resolver,
        "resolved_at": datetime.now(timezone.utc).isoformat(),
    }
    entry = _update_gate(gate_id, status="resolved", resolution=resolution)

    _persist_gate_record(gate_id, entry["gate_type"], entry["brief"], resolution)
    # A synthetic_probe gate (KAI-1112) has no orchestrator workflow to resume, so there
    # is no callback to fire — skip it (avoids a weekly warning on an unreachable URL).
    if entry.get("gate_type") != "synthetic_probe":
        _fire_callback(entry["callback_url"], resolution)

    action = "approved" if req.approved else "rejected"
    _fyi("gate", f"Gate {action}: {gate_id}", f"Gate `{gate_id}` {action} by {req.resolver}. {req.notes}")
    logger.info("Gate %s %s by %s", gate_id, action, req.resolver)

    # Learning capture — log Leo's decision and notes as an insight
    _capture_gate_learning(gate_id, entry["gate_type"], req.approved, req.notes, req.resolver)

    return {"gate_id": gate_id, "status": "resolved", "approved": req.approved}


def _capture_gate_learning(gate_id: str, gate_type: str, approved: bool, notes: str, resolver: str):
    """Capture Leo's gate decision. For creative gates, run taste distillation into BUILD_PROFILE."""
    # KAI-1112: a synthetic approval-probe resolution is not a Leo decision — never let it
    # pollute the human taste/learning log (gate_feedback.md) with a weekly APPROVED line.
    if gate_type == "synthetic_probe":
        return
    try:
        from pathlib import Path as _Path
        insights_file = _Path("/vault/60_Council/learning/gate_feedback.md")
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        action = "APPROVED" if approved else "REJECTED"
        entry_text = f"\n## [{ts}] Gate {action} — {gate_type}\n"
        entry_text += f"Gate ID: `{gate_id}`\n"
        entry_text += f"Resolver: {resolver}\n"
        if notes:
            entry_text += f"Notes: {notes}\n"
        existing = insights_file.read_text() if insights_file.exists() else "# Gate Feedback Log\n"
        insights_file.write_text(existing + entry_text)
        logger.info("Gate learning captured for %s", gate_id)

        # Creative gate distillation — extract taste rules into BUILD_PROFILE
        if gate_type == "creative_gate":
            _distill_creative_taste(gate_id, approved, notes)
    except Exception as e:
        logger.warning("Gate learning capture failed: %s", e)


def _distill_creative_taste(gate_id: str, approved: bool, notes: str):
    """Extract 1-3 taste rules from Leo's creative gate decision and append to BUILD_PROFILE."""
    try:
        gate_entry = _GATES_STORE.get(gate_id, {})
        approved_brief = gate_entry.get("approved_brief", "")
        brief = gate_entry.get("brief", {})
        if not approved_brief:
            logger.info("Distillation skipped for %s — no approved_brief stored", gate_id)
            return

        action = "APPROVED" if approved else "REJECTED"
        notes_section = f"Leo's notes: {notes}" if notes else "No additional notes."

        from graphs.graph import get_graph
        graph = get_graph()
        message = (
            f"[Creative Taste Distillation — {action}]\n\n"
            f"Leo just {action.lower()} a creative brief for: {brief.get('title', 'unknown')}\n"
            f"{notes_section}\n\n"
            f"The brief that was {'approved' if approved else 'rejected'}:\n{approved_brief}\n\n"
            "Extract 1-3 concrete taste rules from this decision. Rules must be:\n"
            "- Written as imperative sentences (e.g. 'Use editorial serif typefaces — no grotesque defaults')\n"
            "- Specific — not vague principles\n"
            "- Tied to what Leo accepted or rejected, not general advice\n"
            "- Scoped to the property if property-specific, or general if broadly applicable\n\n"
            "Format your response as:\n"
            "RULE: <rule text> [property: <name> or general]\n"
            "(one RULE: line per rule, 1-3 rules total)\n\n"
            "Nothing else — just the RULE lines."
        )
        state = {
            "channel": "kai", "message": message, "user_id": "gate-engine",
            "thread_ts": f"distill-{gate_id[:8]}", "attachments": [], "privacy_mode": False,
            "history": [], "target_advisor": "kai", "routing_reason": "taste distillation",
            "advisor_reply": "", "final_reply": "", "model_used": "",
            "input_tokens": 0, "output_tokens": 0, "audit_log": [],
        }
        result = graph.invoke(state, config={"configurable": {"thread_id": f"distill-{gate_id[:8]}"}})
        rules_text = result.get("final_reply", "").strip()

        if not rules_text or "RULE:" not in rules_text:
            logger.info("Distillation produced no rules for %s", gate_id)
            return

        # Append rules to BUILD_PROFILE under Compiled Taste Notes
        build_profile_path = _BUILD_PROFILES["creative"]
        if not build_profile_path.exists():
            logger.warning("BUILD_PROFILE not found — distillation rules not saved")
            return

        profile = build_profile_path.read_text()
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        new_entries = f"\n### {ts} — Gate `{gate_id[:8]}` ({action})\n"
        for line in rules_text.splitlines():
            if line.strip().startswith("RULE:"):
                rule = line.strip()[5:].strip()
                new_entries += f"- {rule}\n"

        if "*(No entries yet" in profile:
            profile = profile.replace("*(No entries yet — populated after first creative gate approve/reject cycle)*", new_entries.strip())
        else:
            profile = profile + "\n" + new_entries

        build_profile_path.write_text(profile)
        logger.info("Taste distillation written to BUILD_PROFILE for gate %s", gate_id)

    except Exception as e:
        logger.warning("Creative taste distillation failed for %s: %s", gate_id, e)


# ── Gate processing ───────────────────────────────────────────────────────────

def _process_gate(req: GateRequest):
    """Run the gate chain (sync, thread pool) then notify Leo via Slack."""
    try:
        gate_type = req.gate_type
        brief     = req.brief

        if gate_type == "plan_gate":
            summary, kai_assessment = _plan_gate_review(brief, req.gate_id)
        elif gate_type == "dev_gate":
            summary, kai_assessment = _dev_gate_review(brief, req.gate_id)
        elif gate_type == "creative_gate":
            summary, kai_assessment = _creative_gate_review(brief, req.gate_id)
        elif gate_type == "devops_gate":
            summary, kai_assessment = _devops_gate_review(brief, req.gate_id)
            # Auto-approve when the verdict's first token is ROUTINE.
            # First-token check (not substring) so a body containing the word
            # STRUCTURAL inside a ROUTINE verdict doesn't falsely escalate.
            first_token = (kai_assessment.split() or [""])[0].rstrip(",.;:—-").upper()
            if first_token == "ROUTINE":
                resolution = {"approved": True, "notes": kai_assessment, "advisor": "devops"}
                _update_gate(req.gate_id, status="resolved", resolution=resolution)
                _persist_gate_record(req.gate_id, gate_type, brief, resolution)
                _fire_callback(req.callback_url, resolution)
                return
        elif gate_type in _HOSTOPS_GATE_TYPES:
            summary, kai_assessment = _hostops_gate_review(brief, req.gate_id)
            decision = classify(_hostops_action(brief))
            if decision.mode == "autonomous":
                resolution = {"approved": True, "notes": decision.reason, "advisor": "autonomy_policy"}
                _update_gate(req.gate_id, status="resolved", resolution=resolution)
                _persist_gate_record(req.gate_id, gate_type, brief, resolution)
                _fire_callback(req.callback_url, resolution)
                _fyi("hostops",
                     f"Autonomous host action: {brief.get('hostops_operation', 'hostops')} on {brief.get('site', 'unknown')}",
                     f"System action taken: {brief.get('hostops_operation', 'hostops')} on {brief.get('site', 'unknown')} — approved autonomously. {decision.reason}.")
                return
        elif gate_type == "synthetic_probe":
            # KAI-1112 · [MR1] Approvals round-trip probe. A synthetic low-stakes gate
            # (scripts/approval_round_trip_probe.py) that exercises the resolve RETURN
            # path — the path with zero resolved lines since 2026-08-12. It must NOT
            # reach pending_leo: that would make the buzz_approve poller prompt Leo on
            # his real kai-approvals channel every week. Leave it in 'processing' (the
            # resolve endpoint accepts 'processing'), so list_pending_gates never surfaces
            # it to the poller; the probe then resolves it directly, driving the exact
            # resolve_gate handler Leo's tap fires. No review, no notification, no Leo spam.
            logger.info("Gate %s is a synthetic_probe (KAI-1112) — no review/notify; awaiting probe resolve", req.gate_id)
            return
        else:
            logger.warning("Unknown gate_type %r — notifying Leo", gate_type)
            _persist_artifact(req.gate_id, "brief", json.dumps(brief, indent=2))
            summary        = f"*Subject:* (unknown gate type `{gate_type}`)\n*Chain:* none — Leo must decide"
            kai_assessment = "Unknown gate type — see brief.md"

        # Move to pending_leo: post to Slack, wait for Leo's response
        _update_gate(
            req.gate_id,
            status="pending_leo",
            summary=summary,
            kai_assessment=kai_assessment,
        )
        # Approval surface (b19bf598). Buzz-primary (adopted): the buzz_approve.py
        # poller prompts Leo on his Buzz channel and resolves from his verified reply,
        # so we do NOT also send Telegram. Default (telegram): unchanged AR-5.2 path.
        if _APPROVAL_SURFACE == "buzz" and _buzz_alive():
            logger.info("Gate %s pending_leo — Buzz primary (poller alive); Telegram backup on standby", req.gate_id)
        elif _APPROVAL_SURFACE == "buzz":
            # Buzz is primary but its poller is DOWN. Telegram = EMERGENCY-ONLY (ratified
            # 2026-08-05, ticket 5adcca90): send a no-button BUZZ-DOWN ALERT and HOLD the
            # gate — Telegram never carries the approval. It resolves on Buzz-recovery
            # (buzz_approve re-prompts) or at the keyboard (in-session `YES`).
            logger.warning("Gate %s pending_leo — Buzz poller STALE; Telegram BUZZ-DOWN alert (held, not tappable)", req.gate_id)
            _tg_alert_buzz_down(req.gate_id, gate_type, summary)
        else:
            # Default surface = telegram (Buzz not adopted as primary): the tappable
            # approve/reject card IS the chosen approval channel here — keep it.
            tg_ok = _tg_send_gate(req.gate_id, gate_type, summary)
            if not tg_ok:
                # Telegram send failed — record the deliverability failure on the
                # dashboard notify log so it is visible. The T2 queue below is the
                # durable, actionable fallback. Slack ingress is retired (AR-5.2).
                _fyi("gate", f"Gate approval Telegram send failed: {req.gate_id}",
                     f"Gate {req.gate_id} ({gate_type}): Telegram approval send failed; gate holds pending_leo (hostops gates also attempt the T2 queue below).")
        if gate_type in _HOSTOPS_GATE_TYPES:
            # The typed #devops route remains the durable fallback if this
            # notification surface is unavailable. The summary is reference-only.
            try:
                response = httpx.post(
                    f"{WORKER_URL}/t2/queue",
                    json={
                        "action": summary,
                        "detail": "Approve to resolve this hostops gate; reject to stop the workflow.",
                        "advisor": "kai",
                        "gate_id": req.gate_id,
                        "callback_url": req.callback_url,
                        "kind": "hostops_gate",
                    },
                    timeout=10,
                    auth=_worker_auth(),
                )
                response.raise_for_status()
                action_id = response.json().get("id")
                if not action_id:
                    raise ValueError("T2 queue response missing action id")
                _update_gate(req.gate_id, t2_action_id=action_id)
            except Exception as exc:
                logger.warning("Hostops gate %s T2 notification unavailable; #devops fallback remains: %s",
                               req.gate_id, exc)
        logger.info("Gate %s pending_leo — awaiting approval", req.gate_id)

    except Exception:
        logger.exception("Gate processing failed for %s", req.gate_id)
        resolution = {
            "approved": False,
            "notes": "Required reviewer unavailable; gate denied and may be retried",
            "advisor": "system",
            "retry_after": 60,
            "resolved_at": datetime.now(timezone.utc).isoformat(),
        }
        _update_gate(req.gate_id, status="resolved", resolution=resolution)
        _persist_gate_record(req.gate_id, req.gate_type, req.brief, resolution)
        _fire_callback(req.callback_url, resolution)
        _fyi(
            "alert",
            f"Gate reviewer failed closed: {req.gate_id}",
            f"Gate `{req.gate_id}` reviewer failed closed. "
            "Work was denied; retry is available after 60 seconds.",
        )


def _plan_gate_review(brief: dict, gate_id: str) -> tuple[str, str]:
    """Plan gate (1-hop chain) — KAI reviews the plan against Leo's direction.

    Persists brief.md and kai_verdict.md. Returns a short Slack summary +
    one-line verdict. Full content lives in the gate's artifact directory.
    """
    _persist_artifact(gate_id, "brief", json.dumps(brief, indent=2))
    job_name = brief.get("workflow", brief.get("job_id", "Unknown workflow"))

    kai_full = _kai_quality_check(
        "plan", brief,
        "Review this plan for completeness, clarity, and alignment with Leo's direction. "
        "Is it clear what will be built? Are the steps logical? Are there obvious gaps?"
    )
    _persist_artifact(gate_id, "kai_verdict", kai_full)
    kai_line = _extract_verdict(kai_full, fallback="see kai_verdict.md")

    summary = f"*Subject:* {job_name}\n*Chain:* KAI plan-review — {kai_line}"
    return summary, kai_line


def _dev_gate_review(brief: dict, gate_id: str) -> tuple[str, str]:
    """Dev gate (2-hop chain) — LSE engineering review → KAI quality check."""
    _persist_artifact(gate_id, "brief", json.dumps(brief, indent=2))
    build_profile = _BUILD_PROFILES["dev"].read_text() if _BUILD_PROFILES["dev"].exists() else ""
    brief_text = json.dumps(brief, indent=2)
    job_name = brief.get("workflow", brief.get("title", brief.get("job_id", "Engineering work")))

    # KAI-1083 — drafts-only builds are governed but never publish or touch live
    # infrastructure. Review them against drafts-only criteria, not the production
    # deploy checklist. The write_mode flag is the switch; absent it, the strict
    # production rubric applies unchanged.
    draft_only = brief.get("write_mode") == "draft_only"
    if draft_only:
        rubric = (
            "\n\nRUBRIC SELECTION — THIS IS A DRAFTS-ONLY BUILD.\n"
            "This workflow saves a WordPress page as a DRAFT. It never publishes, never sets a "
            "front page, and never mutates live content — enforced by the WP write chokepoint "
            "(status=draft) and by the workflow having no publish/homepage steps. The Cloudways "
            "FQDN in the brief is the draft's host, NOT a live-deploy target.\n"
            "Review against DRAFTS-ONLY criteria, and ONLY these:\n"
            "  - Is there a real deliverable (a scoped page draft), not just a job context object?\n"
            "  - Does the brief confirm it stays a draft (no publish / no homepage change)?\n"
            "  - Are WordPress credentials sourced from the secrets layer, not the job context?\n"
            "  - Is the brand-drift check part of the chain?\n"
            "DO NOT require: container-rebuild confirmation, a test suite, production security "
            "posture, or a live-deploy plan. Do NOT treat the FQDN as 'touching production "
            "infrastructure'. Those are deploy-gate concerns and do not apply to a draft.\n"
        )
    else:
        rubric = ""

    lse_full = _call_advisor("dev",
        f"[LSE Sign-Off Required]\n\nBuild Profile Standards:\n{build_profile}\n"
        f"{rubric}\n"
        f"Engineering Brief:\n{brief_text}\n\n"
        "Review this brief against the applicable rubric above. Does it meet Leo's engineering "
        "standards for its build tier? What is your assessment and sign-off? Be specific about "
        "what was checked.\n\n"
        "RESPONSE FORMAT — first line MUST be:\n"
        "VERDICT: <SIGNED-OFF | CONCERNS | REJECTED> — one-sentence headline\n"
        "Then the full review on subsequent lines.",
        gate_id
    )
    _persist_artifact(gate_id, "lse_review", lse_full)
    lse_line = _extract_verdict(lse_full, fallback="see lse_review.md")

    kai_instruction = f"LSE has reviewed and signed off:\n{lse_full}\n\n"
    if draft_only:
        kai_instruction += (
            "This is a DRAFTS-ONLY build — it never publishes or touches live infrastructure. "
            "Judge it against drafts-only criteria (a real scoped draft deliverable, stays a draft, "
            "creds via the secrets layer, brand-drift check present). Do NOT require deploy-tier "
            "artifacts (container rebuild, tests, production security posture). "
        )
    kai_instruction += (
        "Does this engineering work meet Leo's standards? Is it scoped correctly? "
        "Is the approach sound? Would Leo approve this?"
    )

    kai_full = _kai_quality_check("dev", brief, kai_instruction)
    _persist_artifact(gate_id, "kai_verdict", kai_full)
    kai_line = _extract_verdict(kai_full, fallback="see kai_verdict.md")

    summary = f"*Subject:* {job_name}\n*Chain:* LSE — {lse_line} · KAI — {kai_line}"
    return summary, kai_line


_BRIEF_REQUIRED_SECTIONS = [
    "style direction",
    "color palette",
    "typography",
    "content voice",
    "mood board",
]

def _kai_validate_brief(produced_brief: str) -> tuple[bool, str]:
    """KAI checks whether the Creative Director's brief meets all 5 required sections.

    Returns (approved: bool, feedback: str).
    approved=True only when all sections are present and specific.
    """
    try:
        message = (
            "[KAI Brief Validation]\n\n"
            "You are reviewing a creative brief produced by the Creative Director.\n"
            "The brief MUST contain all 5 of these sections, each specific and non-vague:\n"
            "1. Style Direction — 2-3 specific sentences, not generic phrases\n"
            "2. Color Palette — exact hex values with usage notes for each color\n"
            "3. Typography — specific named fonts with hierarchy rationale\n"
            "4. Content Voice — actual example sentences (not descriptions of tone)\n"
            "5. Mood Board — 3-5 references each with a one-line WHY note\n\n"
            "Additional rejection criteria:\n"
            "- Reject if any section is missing\n"
            "- Reject if color palette has no hex values\n"
            "- Reject if typography names only font categories (not specific fonts)\n"
            "- Reject if content voice only describes tone without example sentences\n"
            "- Reject if mood board references have no WHY notes\n"
            "- Reject if brief contradicts BUILD_PROFILE for the property\n\n"
            f"Brief to review:\n{produced_brief}\n\n"
            "Reply with APPROVED if all 5 sections pass all criteria.\n"
            "Reply with REJECTED: <specific list of what is missing or weak> if not.\n"
            "Be strict. Leo relies on this check so he only sees briefs that are ready."
        )
        reply = _gate_review_llm("kai", message, "gate:kai:brief-validation")  # KAI-1085 single-shot
        approved = reply.strip().upper().startswith("APPROVED")
        return approved, reply
    except Exception as e:
        logger.exception("KAI brief validation failed: %s", e)
        raise ReviewerUnavailable("KAI brief validation failed") from e


def _creative_gate_review(brief: dict, gate_id: str) -> tuple[str, str]:
    """Brief-first creative gate flow.

    Creative Director produces a 5-section brief. KAI validates it internally
    (loop max 3 iterations). Only when KAI approves does the brief surface to Leo.
    Leo approves once — then it gets built.
    """
    build_profile = _BUILD_PROFILES["creative"].read_text() if _BUILD_PROFILES["creative"].exists() else ""
    # BUG 12372f93 — the WP drafts-only workflow supplies the loaded vault brief as
    # vault_brief and the property slug as site; fall back to those so the creative gate
    # reviews the real brief/property, not an empty direction + a JSON dump of the brief.
    direction = brief.get("direction") or brief.get("vault_brief") or json.dumps(brief, indent=2)
    title = brief.get("title", brief.get("project", "Creative request"))
    property_name = brief.get("property") or brief.get("site") or brief.get("project", "")

    # KAI-394: load curated references from vault for this property
    reference_library = _load_references(property_name)

    iteration_log = []
    produced_brief = ""
    kai_feedback = ""
    approved = False

    for iteration in range(1, 4):
        feedback_section = (
            f"\n\nKAI FEEDBACK FROM PREVIOUS ATTEMPT (iteration {iteration - 1}):\n{kai_feedback}\n"
            "Address every point above before resubmitting."
            if iteration > 1 else ""
        )

        produced_brief = _call_advisor("creative",
            f"[Creative Brief — Iteration {iteration}/3]\n\n"
            f"BUILD PROFILE:\n{build_profile}\n\n"
            + (f"{reference_library}\n\n" if reference_library else "")
            + f"LEO'S DIRECTION:\n{direction}\n\n"
            f"PROPERTY: {property_name}\n"
            f"{feedback_section}\n"
            "Produce a structured creative brief with ALL 5 required sections:\n\n"
            "## Style Direction\n(2-3 specific sentences — what it looks like, feels like, and why)\n\n"
            "## Color Palette\n(3-5 colors with exact hex values and usage notes)\n\n"
            "## Typography\n(primary + secondary font — specific names, not categories — with hierarchy rationale)\n\n"
            "## Content Voice\n(2-3 example sentences written in the actual voice — not descriptions of tone)\n\n"
            "## Mood Board\n"
            "(3-5 references. For EACH reference you MUST include:\n"
            "  - A description of what it is\n"
            "  - IMAGE: <direct image URL> on its own line\n"
            "  - WHY: one sentence on what specifically applies to this brief\n"
            "Draw from the REFERENCE LIBRARY above first. You may add additional references if needed.)\n\n"
            "Do not include execution plans, wireframes, or design specs. Brief only.",
            gate_id
        )

        _persist_artifact(gate_id, f"director_iter_{iteration}", produced_brief)

        approved, kai_feedback = _kai_validate_brief(produced_brief)
        _persist_artifact(gate_id, f"kai_validation_iter_{iteration}", kai_feedback)
        iteration_log.append({
            "iteration": iteration,
            "approved": approved,
            "feedback": kai_feedback[:500],
        })

        logger.info("Gate %s brief iteration %d — approved=%s", gate_id, iteration, approved)

        if approved:
            break

    _persist_artifact(gate_id, "iteration_log", json.dumps(iteration_log, indent=2))

    if not approved:
        verdict = f"ESCALATE — Director failed KAI validation after {len(iteration_log)} iterations"
        summary = (
            f"*Subject:* {title}\n"
            f"*Chain:* Director (×{len(iteration_log)}) → KAI — {verdict}"
        )
    else:
        verdict = f"APPROVED — Brief approved by KAI on iteration {len(iteration_log)}"
        summary = (
            f"*Subject:* {title}\n"
            f"*Chain:* Director (×{len(iteration_log)}) → KAI — {verdict}"
        )

    # Store the approved brief on the gate record for use at resolution
    _update_gate(gate_id, approved_brief=produced_brief)

    return summary, verdict


def _devops_gate_review(brief: dict, gate_id: str) -> tuple[str, str]:
    """DevOps gate (1-hop chain) — DevOps reviews infrastructure implications.

    Single-advisor by design: DevOps is the authoritative voice on infra.
    Auto-approves routine work; escalates anything whose verdict contains
    STRUCTURAL or REJECTED.
    """
    _persist_artifact(gate_id, "brief", json.dumps(brief, indent=2))
    brief_text = json.dumps(brief, indent=2)
    job_name = brief.get("workflow", brief.get("title", "Infrastructure change"))

    devops_full = _call_advisor("devops",
        f"[DevOps Infrastructure Review]\n{brief_text}\n\n"
        "Review the infrastructure implications. Is this routine or structural? "
        "Any risks, dependencies, or architectural concerns?\n\n"
        "RESPONSE FORMAT — first line MUST be:\n"
        "VERDICT: <ROUTINE | STRUCTURAL | REJECTED> — one-sentence headline\n"
        "Then the full review on subsequent lines. "
        "Use STRUCTURAL when this needs Leo's approval.",
        gate_id
    )
    _persist_artifact(gate_id, "devops_review", devops_full)
    devops_line = _extract_verdict(devops_full, fallback="see devops_review.md")

    summary = f"*Subject:* {job_name}\n*Chain:* DevOps — {devops_line}"
    return summary, devops_line


def _hostops_action(brief: dict) -> dict:
    """Build a reference-only descriptor; ownership must be verifiable."""
    site = brief.get("site", "")
    owner = "unknown"
    try:
        owner = json.loads(_WORDPRESS_SITES.read_text()).get("sites", {}).get(site, {}).get("owner", "unknown")
    except (OSError, ValueError):
        logger.warning("wordpress site ownership unavailable for %s; failing closed", site)
    return {"op": brief.get("hostops_operation", ""), "site": site, "target": brief.get("secret_name") or brief.get("plugin", ""), "owner": owner, "reversible": True, "external_party": owner not in {"leo"}}


def _hostops_gate_review(brief: dict, gate_id: str) -> tuple[str, str]:
    """Privileged host-op gate — policy-routed and reference-only.

    KAI-820 HOSTOPS-(c): names the operation, target site, and app identity so
    Leo can approve by tap. The brief carries a secret *name* (a reference), never
    the payload bytes or key material (L18), so persisting it here is safe.
    """
    _persist_artifact(gate_id, "brief", json.dumps(brief, indent=2))
    op = brief.get("hostops_operation", "unknown")
    site = brief.get("site", "unknown")
    identity = brief.get("audit_identity", "")
    target = brief.get("secret_name") or brief.get("plugin") or ""
    summary = (
        f"*Subject:* privileged host-op `{op}` on `{site}`\n"
        f"*Target:* `{target}`  *Identity:* `{identity}`\n"
        f"*Chain:* autonomy policy decides whether Leo approval is required"
    )
    assessment = f"HOSTOPS mutation {op} on {site} — routing through autonomy policy"
    return summary, assessment


# ── Advisor + KAI calls ───────────────────────────────────────────────────────

# KAI-1085 — a gate review is a ONE-SHOT assessment. Routing it through the full
# advisor graph gives the model its agentic toolset, whose loop accumulates tokens
# against router.TURN_TOKEN_BUDGET (24k) and returns OVER_BUDGET_REPLY under a
# degraded advisor node. Mirror graphs/bug_nodes.py: call _run_agentic_loop with
# tools=[] so it returns on the first completion (no tool loop, no budget blow-out),
# on a strong cloud model independent of local-node health.
_GATE_REVIEW_MODEL = "claude-sonnet-4-6"
# BUG 12372f93 — single-shot gate reviews (tools=[]) make exactly one bounded model
# call, so the 24k agentic-loop TURN_TOKEN_BUDGET (which caps tool-loop accumulation)
# must not discard a COMPLETED review just because a large persona + build-profile
# pushes the INPUT past 24k. Generous finite budget; cost stays bounded (one call,
# max_tokens=2048) because there is no tool loop to run away.
_GATE_REVIEW_TOKEN_BUDGET = 120_000


def _gate_review_llm(persona_advisor: str, message: str, trigger: str) -> str:
    """Single-shot council review (KAI-1085). Raises ReviewerUnavailable on no verdict."""
    from router import _run_agentic_loop
    from persona import load_persona
    from council_config import _track_usage
    try:
        system = load_persona(persona_advisor)
        messages = [{"role": "user", "content": message}]
        reply, in_tok, out_tok, cr_tok, cc_tok = _run_agentic_loop(
            messages, [], _GATE_REVIEW_MODEL, system, persona_advisor,
            turn_token_budget=_GATE_REVIEW_TOKEN_BUDGET,
        )
        try:
            _track_usage(persona_advisor, in_tok, out_tok, "anthropic", _GATE_REVIEW_MODEL,
                         trigger_source=trigger, cache_read_tokens=cr_tok,
                         cache_creation_tokens=cc_tok)
        except Exception as exc:
            logger.warning("gate review usage tracking failed: %s", exc)
        if not reply.strip() or reply.startswith("over_budget:"):
            raise ReviewerUnavailable(f"{persona_advisor} gate review returned no verdict")
        return reply
    except Exception as e:
        logger.exception("Gate review call failed for %s: %s", persona_advisor, e)
        if isinstance(e, ReviewerUnavailable):
            raise
        raise ReviewerUnavailable(f"{persona_advisor} gate review failed") from e


def _call_advisor(advisor: str, message: str, thread_id: str) -> str:
    """Call a council advisor for a gate review — single-shot (KAI-1085)."""
    return _gate_review_llm(advisor, message, f"gate:{advisor}:review")


def _kai_quality_check(check_type: str, brief: dict, instruction: str) -> str:
    """KAI reviews the work against Leo's direction and system standards."""
    try:
        gate_policy = fm.get_gate_policy(f"{check_type}_gate") or {}
        standards = json.dumps(gate_policy, indent=2) if gate_policy else ""

        message = (
            f"[KAI Quality Check — {check_type}]\n\n"
            f"Gate standards:\n{standards}\n\n"
            f"Brief:\n{json.dumps(brief, indent=2)}\n\n"
            f"{instruction}\n\n"
            "RESPONSE FORMAT — first line MUST be:\n"
            "VERDICT: <READY | NOT READY | CONCERNS> — one-sentence headline\n"
            "Then the full assessment on subsequent lines."
        )
        reply = _gate_review_llm("kai", message, f"gate:kai-qc:{check_type}")  # KAI-1085 single-shot
        return reply
    except Exception as e:
        logger.exception("KAI quality check failed: %s", e)
        if isinstance(e, ReviewerUnavailable):
            raise
        raise ReviewerUnavailable("KAI quality check failed") from e


# ── Persistence ───────────────────────────────────────────────────────────────

def _gate_dir(gate_id: str) -> Path:
    """Per-gate artifact directory under the vault."""
    return _validated_gate_dir(_VAULT_GATES, gate_id)


def _persist_artifact(gate_id: str, artifact_type: str, content: str) -> str:
    """Save a gate artifact to vault/00_System/gates/{gate_id}/{artifact_type}.md.

    Returns the absolute path written, or "" on failure.
    """
    try:
        d = _gate_dir(gate_id)
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{artifact_type}.md"
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        p.write_text(f"# Gate Artifact: {artifact_type}\nGate: {gate_id}\nWritten: {ts}\n\n{content}\n")
        return str(p)
    except Exception:
        logger.exception("Artifact persist failed: %s/%s", gate_id, artifact_type)
        return ""


def _persist_gate_record(gate_id: str, gate_type: str, brief: dict, resolution: dict):
    """Write the gate audit record to vault/00_System/gates/{gate_id}/audit.json."""
    try:
        d = _gate_dir(gate_id)
        d.mkdir(parents=True, exist_ok=True)
        record = {
            "gate_id":     gate_id,
            "gate_type":   gate_type,
            "brief":       brief,
            "resolution":  resolution,
            "resolved_at": datetime.now(timezone.utc).isoformat(),
        }
        (d / "audit.json").write_text(json.dumps(record, indent=2))
    except Exception:
        logger.exception("Gate audit persist failed for %s", gate_id)


def _fire_callback(callback_url: str, resolution: dict):
    """POST resolution back to the orchestrator callback URL."""
    try:
        r = httpx.post(callback_url, json=resolution, timeout=10)
        if r.status_code == 200:
            logger.info("Gate callback OK: %s", callback_url)
        else:
            logger.warning("Gate callback %s returned %d: %s", callback_url, r.status_code, r.text[:200])
    except Exception as e:
        logger.exception("Gate callback failed: %s — %s", callback_url, e)
