"""[S1-A1] Sprint approval helper — raise a HARD GATE from an autonomous sprint.

The council approval ROUTER already exists and is live (kai-council-api
routes_council_gate.py): Buzz-primary send, Telegram emergency-only alert on
Buzz-down, T2 durable fallback, full pending_leo -> resolve lifecycle. But a gate
could only be RAISED by an orchestrator workflow (it needs a job_id/step_id and
the orchestrator engine.open_gate + a callback server). An autonomous sprint run
has none of that.

This module is the missing caller-facing primitive: request_sprint_gate() raises
a `sprint_gate` through the SAME live router and BLOCKS by polling
/council/gate/{id}/state until Leo taps (Buzz primary / Telegram emergency), or
until a timeout. No callback server required — the sprint polls.

Ratified comms model (2026-08-05): Buzz is the sole tap-to-approve surface;
Telegram is EMERGENCY-ONLY and never carries the approval. This helper does not
change that — it reuses the router, which already enforces it.

L18: never logs a secret. Auth is the worker basic-auth credential the sprint
process already holds as a docker secret / host secrets file.
"""
from __future__ import annotations

import logging
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx

log = logging.getLogger("sprint_gate")

# Container-internal by default (council-api is not host-published); overridable
# for a host-side sprint runner via COUNCIL_API_URL.
_COUNCIL_API = os.environ.get("COUNCIL_API_URL", "http://kai-council-api:8002").rstrip("/")

# Same secret-resolution order as every other internal caller (council_config,
# scheduler): docker secret mount first, then host secrets file.
_AUTH_FILES = (
    "/run/secrets/kai_worker_auth",
    "/run/wp_secrets/kai_worker_auth.txt",
    "/home/leo/kai-system/secrets/kai_worker_auth.txt",
)



class SprintGateError(RuntimeError):
    """Raised when the gate could not be RAISED (never for a reject — a reject is a
    valid, returned outcome). A raise failure must stop the sprint, not be mistaken
    for a decision."""


@dataclass
class GateOutcome:
    gate_id: str
    approved: bool          # False on reject OR on timeout (see resolved)
    resolved: bool          # True iff Leo actually decided; False iff timed out
    notes: str = ""
    resolver: str = ""

    @property
    def timed_out(self) -> bool:
        return not self.resolved


def _worker_auth() -> Optional[tuple[str, str]]:
    for pth in _AUTH_FILES:
        try:
            raw = Path(pth).read_text().strip()
        except Exception:
            continue
        if ":" in raw:
            user, pw = raw.split(":", 1)
            if user and pw:
                return (user, pw)
    return None


def _new_gate_id() -> str:
    # uuid4 hex is 32 chars of [0-9a-f] — inside the councils [A-Za-z0-9_-]{4,128}.
    return "sprint-" + uuid.uuid4().hex


def request_sprint_gate(
    summary: str,
    detail: str = "",
    *,
    timeout_s: float = 1800.0,
    poll_interval_s: float = 5.0,
    base_url: Optional[str] = None,
) -> GateOutcome:
    """Raise a sprint HARD GATE and BLOCK until Leo decides or timeout_s elapses.

    summary  one-line decision framing (what is being authorized).
    detail   optional longer context shown as the KAI assessment.
    timeout_s  how long to hold the sprint waiting for a tap. On timeout the
               outcome is approved=False, resolved=False (timed_out=True) — the
               caller MUST treat a timeout as NOT approved (fail-closed).

    Returns a GateOutcome. Raises SprintGateError only if the gate could not be
    raised (council unreachable / non-200) — a genuine reject is a normal return.
    """
    api = (base_url or _COUNCIL_API).rstrip("/")
    auth = _worker_auth()
    gate_id = _new_gate_id()

    payload = {
        "gate_id": gate_id,
        "gate_type": "sprint_gate",
        "brief": {"summary": summary, "detail": detail, "kind": "sprint_gate"},
        # No callback server for a sprint run; the council skips the callback for
        # sprint_gate and we poll /state instead. A sentinel keeps the model happy.
        "callback_url": "sprint://poll",
    }

    try:
        r = httpx.post(f"{api}/council/gate", json=payload, auth=auth, timeout=15)
        r.raise_for_status()
    except Exception as e:
        raise SprintGateError(f"could not raise sprint gate: {type(e).__name__}") from e

    log.info("Sprint gate %s raised; awaiting Leo (timeout %ss)", gate_id, int(timeout_s))

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        time.sleep(poll_interval_s)
        try:
            sr = httpx.get(f"{api}/council/gate/{gate_id}/state", auth=auth, timeout=15)
            if sr.status_code != 200:
                continue
            state = sr.json()
        except Exception as e:
            log.warning("Sprint gate %s poll error: %s", gate_id, type(e).__name__)
            continue
        if state.get("status") == "resolved":
            res = state.get("resolution") or {}
            approved = bool(res.get("approved"))
            log.info("Sprint gate %s resolved: %s", gate_id, "APPROVED" if approved else "REJECTED")
            return GateOutcome(
                gate_id=gate_id,
                approved=approved,
                resolved=True,
                notes=str(res.get("notes") or ""),
                resolver=str(res.get("advisor") or res.get("resolver") or ""),
            )

    log.warning("Sprint gate %s timed out after %ss — treating as NOT approved", gate_id, int(timeout_s))
    return GateOutcome(gate_id=gate_id, approved=False, resolved=False,
                       notes="timed out waiting for approval", resolver="")
