"""COMMS Phase 1 — the notify() gateway (KAI-1004).

The SINGLE chokepoint for every KAI-originated message to a Leo-facing surface.
Design authority: docs/COMMS_DELIVERY_ARCHITECTURE_2026-07.md (ratified Leo 2026-07-31).

Hard constraints:
  Rule A — every send/decision is logged, always (append-only notify_log.jsonl).
  Rule B — Leo is reached (Telegram) ONLY for a decision/authorization/approval only
           he can give, or a genuine personal-consequence matter. Everything else —
           all technical/operational/DevOps activity — routes to the dashboard log
           (the future System-tab source), never pushed to Leo.

This module owns the ONLY raw Telegram transport in the codebase (`send_telegram`).
No other module may call api.telegram.org directly — enforced by
scripts/check_notify_chokepoint.py (fails loud). Public surface:
  - notify(event)      classified autonomous push: reality-gate → classify/route → dedup → log
  - send_telegram(...) direct, always-logged transport for conversational replies + approval cards
  - tg_alert(msg)      back-compat shim over notify(), defaulting to the dashboard audience

L18: this module never logs a bot token, a /bot<TOKEN>/ URL, or httpx error text.
It logs only decision metadata and truncated titles. Fail-soft everywhere: a logging
or transport failure never raises into the caller's path.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx

log = logging.getLogger("notify_gateway")
_API = "https://api.telegram.org"

# Findings Contract (KAI-1100) — a problem-asserting signal may not leave this
# gateway uncaused. findings.py is a sibling module in shared/; import it
# defensively so the gateway still routes (fail-closed) if it is ever absent.
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from findings import NOT_YET_DIAGNOSED, needs_cause
except Exception:  # pragma: no cover - contract module optional at import time
    NOT_YET_DIAGNOSED = "not-yet-diagnosed"
    _BAD = {"stale", "alert", "fail", "failed", "degraded", "error", "critical",
            "warn", "warning", "red", "amber", "offline", "down", "unreachable"}
    def needs_cause(status) -> bool:  # noqa: E306
        return str(status or "").lower() in _BAD

_LOG_PATH = Path(os.environ.get("KAI_NOTIFY_LOG", "/vault/00_System/notify_log.jsonl"))
_DEDUP_PATH = Path(os.environ.get("KAI_NOTIFY_DEDUP", "/vault/00_System/notify_dedup.json"))
_DEDUP_WINDOW_S = int(os.environ.get("KAI_NOTIFY_DEDUP_WINDOW", "3600"))

# The only audiences permitted to reach Leo's Telegram (Rule B).
_LEO_AUDIENCES = {"approval", "personal"}

# The org-model-backed autonomy engine (shared/autonomy_decisions.py). Imported
# defensively so the gateway still functions (fail-closed to dashboard) if absent.
try:
    from autonomy_decisions import classify as _classify
except Exception:  # pragma: no cover - engine optional at import time
    _classify = None


# ── Event / result ────────────────────────────────────────────────────────────

@dataclass
class Event:
    """A single notification offered to the gateway. Routing is driven by
    `audience` (Rule B) and refined by `classify()` when `action` is supplied."""
    source: str                          # originating module/team, e.g. "invariants"
    kind: str                            # "invariant" | "bug" | "gate" | "alert" | ...
    title: str = ""
    body: str = ""
    audience: str = "dashboard"          # "dashboard" | "approval" | "personal"
    actionable: bool = False
    provenance: str = "real"             # "real" | "synthetic"
    dedup_key: Optional[str] = None
    action: Optional[dict] = None        # optional dict passed to classify()
    action_ref: Optional[str] = None     # e.g. a gate_id
    status: Optional[str] = None         # finding status; if it asserts a problem, a cause is required
    cause: Optional[str] = None          # verified cause, or NOT_YET_DIAGNOSED once stamped


@dataclass
class NotifyResult:
    decision: str        # delivered | suppressed_synthetic | suppressed_dedup | dashboard_only | send_failed
    destination: str     # telegram | dashboard | sink
    delivered: bool
    reason: str


# ── Reality gate ────────────────────────────────────────────────────────────────

def _test_mode() -> bool:
    """A test/synthetic context must never reach Leo's real Telegram. True under
    pytest (in-process tests import the app) or when a harness sets
    KAI_NOTIFY_TEST_SINK=1. Inert in production (live services never import pytest).
    Absorbs the COMMS P0 reality-gate stubs.

    An explicit KAI_NOTIFY_TEST_SINK value wins over pytest auto-detection, so the
    gateway's OWN unit tests can set it to "0" to exercise routing with a stubbed
    transport, while in-process contract tests (env unset) still auto-suppress."""
    override = os.environ.get("KAI_NOTIFY_TEST_SINK")
    if override is not None:
        return override == "1"
    return "pytest" in sys.modules


# ── Secrets / helpers ───────────────────────────────────────────────────────────

def _secret(name: str) -> str:
    """Resolve a secret by name. Order: Docker secret mount → env var → host secrets
    directory (KAI_SECRETS_DIR, default ~/kai-system/secrets) as a <name> or <name>.txt
    file. The host-file fallback is what lets host-cron pagers (advisor_dm_probe,
    fleet_heartbeat, meta_monitor) that run OUTSIDE the container — where /run/secrets is
    not mounted and the env is bare — still reach Telegram. Without it a host-cron page
    resolves an empty token and delivers to nobody (the KAI-1108 failure class; see the
    urgent host-cron-delivery bug). File-based only: the value is never written to the
    environment or logged (L18). Fail-closed: any filesystem error at any source
    yields "" rather than raising into the notify() path — a page must never crash
    on a secret-read hiccup — the entire resolution is wrapped."""
    try:
        p = Path(f"/run/secrets/{name}")
        if p.exists():
            return p.read_text().strip()
        env = os.environ.get(name.upper())
        if env:
            return env.strip()
        base = Path(os.environ.get("KAI_SECRETS_DIR", str(Path.home() / "kai-system" / "secrets")))
        for cand in (base / name, base / f"{name}.txt"):
            if cand.exists():
                return cand.read_text().strip()
    except Exception:
        return ""
    return ""


def _chat_ids() -> list[str]:
    raw = _secret("telegram_allowed_chat_ids")
    return [c.strip() for c in raw.replace("\n", ",").split(",") if c.strip()]


def _int_or_str(chat_id):
    try:
        return int(chat_id)
    except (TypeError, ValueError):
        return chat_id


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Rule A: append-only log ─────────────────────────────────────────────────────

def _log(record: dict) -> None:
    """Append one structured record to the notification log. Never raises."""
    record = {"ts": _now_iso(), **record}
    try:
        _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_LOG_PATH, "a") as f:
            f.write(json.dumps(record, default=str) + "\n")
    except Exception as e:
        log.error("notify_log write failed (%s) for kind=%s", type(e).__name__, record.get("kind"))


def _log_notify(event: Event, res: NotifyResult) -> None:
    _log({
        "event": "notify",
        "source": event.source,
        "kind": event.kind,
        "audience": event.audience,
        "provenance": event.provenance,
        "actionable": event.actionable,
        "decision": res.decision,
        "destination": res.destination,
        "delivered": res.delivered,
        "reason": res.reason,
        "dedup_key": event.dedup_key,
        "action_ref": event.action_ref,
        "status": event.status,
        "cause": event.cause,
        "title": (event.title or "")[:200],
        "body": (event.body or "")[:2000],
    })


# ── Dedup (best-effort, file-backed, fail-soft) ──────────────────────────────────

def _dedup_load() -> dict:
    try:
        return json.loads(_DEDUP_PATH.read_text()) if _DEDUP_PATH.exists() else {}
    except Exception:
        return {}


def _dedup_seen(dedup_key: Optional[str]) -> bool:
    """True if this key was delivered within the window (→ suppress a re-notify)."""
    if not dedup_key:
        return False
    last = _dedup_load().get(dedup_key)
    return last is not None and (time.time() - float(last)) < _DEDUP_WINDOW_S


def _dedup_mark(dedup_key: Optional[str]) -> None:
    if not dedup_key:
        return
    now = time.time()
    data = {k: v for k, v in _dedup_load().items() if (now - float(v)) < _DEDUP_WINDOW_S}
    data[dedup_key] = now
    try:
        _DEDUP_PATH.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(_DEDUP_PATH, json.dumps(data))
    except Exception as e:
        log.error("notify_dedup write failed: %s", type(e).__name__)


def _atomic_write_text(path: Path, text: str) -> None:
    """Write `text` to `path` atomically and uid-agnostically.

    The dedup/log surfaces under /vault are shared by the host cron probes
    (uid 1000) and the containers (also uid 1000) — but a stray root-owned
    process occasionally recreates the file as root:root:644, after which an
    in-place `write_text` from a uid-1000 writer hits PermissionError (74×
    observed, KAI cron logs). Writing a fresh temp we own and os.replace-ing it
    in needs only DIRECTORY write (every legitimate writer owns/roots the dir),
    so it succeeds regardless of the target file's current owner, and the swap
    is atomic. Mode 0o664 keeps the group (uid-1000 fleet) writable next time.
    """
    d = path.parent
    fd, tmp = tempfile.mkstemp(dir=str(d), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
        try:
            os.chmod(tmp, 0o664)
        except OSError:
            pass  # best-effort; replace still succeeds
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ── Transport: the ONLY raw Telegram sendMessage in the codebase ────────────────
# The enforcement check (scripts/check_notify_chokepoint.py) fails if any module
# other than this one calls api.telegram.org/.../sendMessage. `_raw_post` is the
# single place that URL appears; every public entry point wraps it with the reality
# gate + Rule-A log. (editMessageText / answerCallbackQuery — UI plumbing on an
# already-sent card, not a new voice — are out of P1's "send" scope; Phase 2 owns
# the tap round-trip.)

def _raw_post(chat_id, text: str, reply_markup: Optional[dict],
              parse_mode: Optional[str], disable_notification: bool = False):
    """The one and only raw sendMessage. Returns the httpx.Response or None on a
    transport error / missing token. L18: never logs the token/URL or error body."""
    token = _secret("telegram_bot_token")
    if not token:
        return None
    payload: dict = {"chat_id": _int_or_str(chat_id), "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if disable_notification:
        payload["disable_notification"] = True
    try:
        return httpx.post(f"{_API}/bot{token}/sendMessage", json=payload, timeout=15)
    except Exception as e:
        log.error("telegram send failed: %s", type(e).__name__)
        return None


def send_message(chat_id, text: str, *, reason: str,
                 reply_markup: Optional[dict] = None,
                 parse_mode: Optional[str] = None,
                 disable_notification: bool = False) -> dict:
    """Send one Telegram message and return result detail:
    {'delivered': bool, 'message_id': int|None}. For callers that must edit the card
    later (e.g. approval prompts). Reality-gated + logged (Rule A)."""
    if _test_mode():
        _log({"event": "send", "reason": reason, "chat_id": _safe_chat(chat_id),
              "decision": "suppressed_synthetic", "delivered": False,
              "title": (text or "")[:120]})
        log.info("send SUPPRESSED (test context): reason=%s", reason)
        return {"delivered": False, "message_id": None, "suppressed": True}
    if not _secret("telegram_bot_token"):
        _log({"event": "send", "reason": reason, "decision": "dropped_no_token", "delivered": False})
        log.error("send: telegram_bot_token missing — %s dropped", reason)
        return {"delivered": False, "message_id": None}
    r = _raw_post(chat_id, text, reply_markup, parse_mode, disable_notification)
    delivered = bool(r is not None and r.status_code == 200)
    message_id = None
    if delivered:
        try:
            body = r.json()
            message_id = (body.get("result") or {}).get("message_id")
            # S1-B4 (audit #03): a page counts as delivered ONLY on
            # API-200 + ok + a real message_id readback — the receipt that
            # the message actually landed, not merely that the API accepted it.
            delivered = bool(body.get("ok")) and message_id is not None
        except Exception:
            delivered = False
    _log({"event": "send", "reason": reason, "chat_id": _safe_chat(chat_id),
          "decision": "delivered" if delivered else "send_failed",
          "delivered": delivered, "message_id": message_id, "title": (text or "")[:120]})
    return {"delivered": delivered, "message_id": message_id}


def send_telegram(chat_id, text: str, *, reason: str,
                  reply_markup: Optional[dict] = None,
                  parse_mode: Optional[str] = None,
                  disable_notification: bool = False) -> bool:
    """Send one Telegram message; returns True on delivery. The common transport for
    conversational replies and gate cards; notify() uses it internally for Leo-bound
    events. Thin bool wrapper over send_message()."""
    return send_message(chat_id, text, reason=reason,
                        reply_markup=reply_markup, parse_mode=parse_mode,
                        disable_notification=disable_notification)["delivered"]


def delete_message(chat_id, message_id, *, reason: str = "heartbeat-cleanup") -> bool:
    """S1-B4 — delete a message the gateway just sent (the delivery heartbeat sends a
    silent probe, reads its message_id as the receipt, then deletes it so Leo's DM
    keeps no daily footprint — Rule B). Best-effort, logged, fail-soft. Not a 'send',
    so outside the sendMessage chokepoint. L18: never logs the token/URL."""
    if _test_mode() or message_id is None:
        return False
    token = _secret("telegram_bot_token")
    if not token:
        return False
    try:
        r = httpx.post(f"{_API}/bot{token}/deleteMessage",
                       json={"chat_id": _int_or_str(chat_id), "message_id": message_id},
                       timeout=15)
        ok = bool(r is not None and r.status_code == 200 and (r.json() or {}).get("ok"))
    except Exception as e:
        log.error("telegram delete failed: %s", type(e).__name__)
        ok = False
    _log({"event": "delete", "reason": reason, "chat_id": _safe_chat(chat_id),
          "message_id": message_id, "delivered": ok})
    return ok


def _safe_chat(chat_id) -> str:
    return str(chat_id) if chat_id is not None else "*"


# ── Routing ─────────────────────────────────────────────────────────────────────

def _route(event: Event) -> tuple[str, str]:
    """Return (destination, reason). Fail-closed toward the dashboard (silence to Leo)."""
    # classify() refinement when an action dict is supplied (reuse autonomy engine).
    if event.action and _classify is not None:
        try:
            d = _classify(event.action)
            if d.mode in ("approve", "confirm_once"):
                return "telegram", f"classify:{d.mode}:{d.reason}"
            return "dashboard", f"classify:{d.mode}:{d.reason}"
        except Exception as e:
            log.error("classify failed, routing to dashboard: %s", type(e).__name__)
            return "dashboard", "classify_error_failclosed"
    # Audience-based routing (Rule B): only approval / personal-consequence reach Leo.
    if event.audience in _LEO_AUDIENCES:
        return "telegram", f"audience:{event.audience}"
    return "dashboard", f"audience:{event.audience}"


def _enforce_cause(event: Event) -> bool:
    """Findings Contract at the exit: an event whose status asserts a problem
    (findings.needs_cause) may not leave the gateway as a bare alarm. If it
    carries no cause, stamp the literal not-yet-diagnosed so the signal reaches
    its surface as an explicit 'cause unknown' — never a guessable void the
    operator would be tempted to fill from memory. Returns True when the finding
    is (or became) undiagnosed: an honest, logged number, never hidden."""
    if needs_cause(event.status) and not (event.cause and str(event.cause).strip()):
        event.cause = NOT_YET_DIAGNOSED
        return True
    return False


def _format(event: Event) -> str:
    parts = [p for p in (event.title, event.body) if p]
    text = "\n\n".join(parts) or event.kind
    if event.cause:
        # Standard operator report shape: 'reading — cause: verified|not-yet-diagnosed'.
        if str(event.cause) == NOT_YET_DIAGNOSED:
            text += "\n\ncause: not-yet-diagnosed"
        else:
            text += f"\n\ncause: verified — {event.cause}"
    return text


# ── The gateway ─────────────────────────────────────────────────────────────────

def notify(event: Event) -> NotifyResult:
    """The single classified path for any autonomous, KAI-originated notification.
    Ordered, fail-closed toward silence: reality gate → classify/route → dedup →
    route → log. A technical/operational event (container down, invariant failure)
    is DevOps's to handle + log — it lands on the dashboard, not Leo's phone."""
    # 1. Reality gate — kills synthetic/test by construction.
    if event.provenance != "real" or _test_mode():
        res = NotifyResult("suppressed_synthetic", "sink", False, "provenance!=real or test-mode")
        _log_notify(event, res)
        return res

    # 1b. Findings Contract — a problem-asserting signal cannot leave uncaused.
    #     Stamp not-yet-diagnosed (visible + logged) rather than push a bare alarm.
    undiagnosed = _enforce_cause(event)
    if undiagnosed:
        log.warning("uncaused %s (status=%s) stamped not-yet-diagnosed: %s",
                    event.kind, event.status, (event.title or "")[:80])

    # 2. Decide destination.
    dest, reason = _route(event)

    # 3. Dedup — a standing condition notifies once, not every cycle.
    if dest == "telegram" and _dedup_seen(event.dedup_key):
        res = NotifyResult("suppressed_dedup", "dashboard", False, f"dedup:{event.dedup_key}")
        _log_notify(event, res)
        return res

    # 4. Route.
    if dest == "telegram":
        text = _format(event)
        ok = False
        for cid in _chat_ids():
            ok = send_telegram(cid, text, reason=f"notify:{event.kind}") or ok
        if ok:
            _dedup_mark(event.dedup_key)
        res = NotifyResult("delivered" if ok else "send_failed", "telegram", ok, reason)
    else:
        res = NotifyResult("dashboard_only", "dashboard", False, reason)

    # 5. Log (Rule A) — always, regardless of outcome.
    _log_notify(event, res)
    return res


# ── Back-compat shim ────────────────────────────────────────────────────────────

def tg_alert(message: str, *, source: str = "legacy", kind: str = "alert",
             audience: str = "dashboard", dedup_key: Optional[str] = None,
             status: Optional[str] = None, cause: Optional[str] = None) -> bool:
    """The old fire-and-forget alert helper, now routed through the gateway. Defaults
    to the dashboard audience — a bare technical/system alert is DevOps's to log, not
    Leo's to be pushed (Rule B). Callers that must reach Leo pass audience='approval'
    or 'personal', or use notify()/send_telegram directly."""
    res = notify(Event(source=source, kind=kind, title=message,
                       audience=audience, dedup_key=dedup_key,
                       status=status, cause=cause))
    return res.delivered
