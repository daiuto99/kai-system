"""
provision_gate — the live Telegram approval adapter for the authorized provisioning path (KAI-984).

Implements the `provision_capability.Gate` Protocol against the EXISTING mode-lock approval
primitive (`kai-worker-api/routes/mode_lock.py`), which posts an interactive Telegram card and
resolves on Leo's tap. AR-5.x/KAI-999 moved that remote-approval channel from Slack to Telegram;
this adapter reuses it verbatim — it does not add a new approval surface.

Two security decisions are baked in (design §3.3, R3, and the conservative reading of open Q2):

  1. FRESH PER-ACTION TAP. Provisioning must NOT be auto-approved by an active 90-minute write
     unlock. `request_approval` short-circuits to `approved_session` when an active session exists
     *for the same requester*, so this adapter posts under a DEDICATED requester namespace
     (`kai-provision`) that is never session-unlocked — guaranteeing a brand-new Telegram card
     every time. A create-time `approved_session` (which would only happen if that namespace were
     ever session-unlocked) is treated as "no fresh tap" and DENIED, fail-closed.

  2. ONLY `approved_once` AUTHORIZES A MOVE. A blanket "Allow session (1h)" tap does NOT authorize
     moving a credential — moving a secret is a per-action act (the design's recommended default;
     Q2 "time-boxed window" is unanswered, so we take the strict side). Every non-`approved_once`
     terminal state (denied / expired / consumed / session-grant) and a poll timeout => DENY.

This adapter never sees, reads, or forwards the secret VALUE — it deals only in the secret NAME,
the node, and the approval decision. R5 (no value in transcript) is therefore trivially satisfied
here; the value lives only in the SecretSource + Transport boundary downstream.

Everything with a side effect (HTTP, sleep, clock) is injected so the deny paths are testable with
no live worker-api, Telegram, or wall-clock. The default live client speaks HTTP to the worker-api.
"""
from __future__ import annotations

import json
import time
import urllib.request
from base64 import b64encode
from pathlib import Path
from typing import Callable, Protocol

from provision_capability import Approval

# A dedicated requester identity so `_active_session_for` in mode_lock.py can NEVER match a
# write-unlock session — this is what forces a fresh per-action Telegram card (decision #1).
PROVISION_REQUESTER = "kai-provision"

_TERMINAL_DENY = frozenset({"denied", "expired", "consumed"})


class HttpClient(Protocol):
    def post(self, path: str, body: dict) -> dict:
        """POST JSON, return the parsed JSON object. MUST raise on any transport/HTTP error."""
        ...

    def get(self, path: str) -> dict:
        """GET, return the parsed JSON object. MUST raise on any transport/HTTP error."""
        ...


class _UrllibClient:
    """Minimal live HTTP client for the worker-api. Optional HTTP Basic auth read from a file
    (never from an argv/env value that would land in a process listing)."""

    def __init__(self, base_url: str, auth_file: str | None = None, timeout: float = 10.0) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout
        self._auth_header: str | None = None
        if auth_file:
            try:
                cred = Path(auth_file).read_text().strip()
                if cred:
                    self._auth_header = "Basic " + b64encode(cred.encode()).decode()
            except OSError:
                self._auth_header = None  # fail-closed toward "no auth"; endpoint decides

    def _req(self, method: str, path: str, body: dict | None) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self._base + path, data=data, method=method)
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if self._auth_header:
            req.add_header("Authorization", self._auth_header)
        with urllib.request.urlopen(req, timeout=self._timeout) as r:
            return json.loads(r.read().decode())

    def post(self, path: str, body: dict) -> dict:
        return self._req("POST", path, body)

    def get(self, path: str) -> dict:
        return self._req("GET", path, None)


class TelegramApprovalGate:
    """`Gate` implementation: post a specific approval card and block until Leo taps or timeout.

    Args:
        client: HttpClient to the worker-api (default: live urllib client).
        poll_interval_s: seconds between status polls.
        timeout_s: max wall-clock to wait for a decision, then DENY (also the request TTL).
        sleep / monotonic: injected for testing.
    """

    def __init__(
        self,
        *,
        client: HttpClient | None = None,
        base_url: str = "http://127.0.0.1:8001",
        auth_file: str | None = None,
        poll_interval_s: float = 3.0,
        # Generous per-action window: provisioning is the async/away-from-keyboard
        # case KAI-999 widened the Telegram approval window to 1h for (mode_lock
        # DEFAULT_REQUEST_TTL_S=3600). A 5-min window silently expired Leo's tap on
        # the 2026-07-31 live test; match the mode-lock window here. Deny still on timeout.
        timeout_s: float = 3600.0,
        surface: str = "telegram",
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client if client is not None else _UrllibClient(base_url, auth_file)
        self._poll = poll_interval_s
        self._timeout = timeout_s
        # COMMS P2 channel-agnostic approval: 'present' surfaces the card in-session (no Telegram
        # push — the away-only channel); 'telegram' (default) posts the remote card. Either way the
        # gate polls the SAME channel-neutral decision store, so a present in-session approval and a
        # remote Telegram tap resolve through identical logic — nothing is platform-locked.
        self._surface = surface
        self._sleep = sleep
        self._monotonic = monotonic

    def request_approval(self, *, secret_name: str, node: str, requester: str) -> Approval:
        # R4: the card names the exact secret, the exact node, and the real requester, so an
        # anomalous request is catchable and rubber-stamping is discouraged.
        target = f"secret '{secret_name}' -> node '{node}'"
        reason = (
            f"KAI authorized provisioning. Move server-held secret '{secret_name}' to tailnet "
            f"KAI node '{node}'. Requester: {requester}. Tap 'Allow once' to authorize this single "
            f"action (a session grant does NOT authorize moving a credential)."
        )
        try:
            create = self._client.post(
                "/mode_lock/request_approval",
                {
                    "tool": "provision_secret",
                    "target": target,
                    "reason": reason,
                    "requester": PROVISION_REQUESTER,  # decision #1: no session short-circuit
                    "ttl_s": int(self._timeout),
                    "surface": self._surface,          # COMMS P2: present (keyboard) vs telegram (away)
                },
            )
        except BaseException:  # noqa: BLE001 — a gate error is a denial, never an allow
            return Approval(False, None, "gate post failed")

        if not isinstance(create, dict):
            return Approval(False, None, "gate bad response")
        request_id = create.get("request_id")
        if not isinstance(request_id, str) or not request_id:
            return Approval(False, None, "gate no request_id")
        # Decision #1 fail-closed: an immediate session short-circuit is not a fresh per-action tap.
        if create.get("status") == "approved_session":
            return Approval(False, request_id, "no fresh per-action tap (pre-existing session)")

        deadline = self._monotonic() + self._timeout
        while True:
            try:
                self._sleep(self._poll)
            except BaseException:  # noqa: BLE001 — interrupted wait => fail-closed deny
                return Approval(False, request_id, "gate wait interrupted")
            try:
                st = self._client.get(f"/mode_lock/approval_status/{request_id}?consume=1")
                status = st.get("status") if isinstance(st, dict) else None
            except BaseException:  # noqa: BLE001 — transient poll error: keep trying until deadline
                status = None

            if status == "approved_once":
                return Approval(True, request_id, "approved (fresh per-action tap)")
            if status in _TERMINAL_DENY:
                return Approval(False, request_id, "not approved")
            if status == "approved_session":
                # Decision #2: a blanket session grant does not authorize moving a credential.
                return Approval(False, request_id, "session grant not accepted for provisioning")
            # pending / unknown / transient poll error => wait, then deny on timeout.
            if self._monotonic() >= deadline:
                return Approval(False, request_id, "approval timed out")
