"""Turn-gate v2 — server-side blocker authority (F2 / bbc788da).

The turn-gate's blocker escape-hatch used to keep its WRITABLE trust-state — the
registration ledger, the one-shot claim, and the per-session yield counter —
under ~/.kai/state/blockers on the AGENT's own uid. That is agent-writable: a run
could delete a claim file to re-yield, or reset the yield counter, past the bound.

This module moves that authority to non-agent-writable worker state. The blocker
state lives on /data/turngate — a root-owned Docker named volume (seeded 1000:1000
in the image) that the agent's tools cannot reach and that a worker-run session as
`leo` cannot write without sudo (there is none). The agent reaches it ONLY through
these authenticated endpoints (global BasicAuth middleware).

Invariants preserved, now enforced server-side on non-agent-writable state:
  INV1  Trusted issuance — a card is honored only if it matches a registered
        ledger row (a card minted without /register has no row -> refused).
  INV2  Atomic one-shot — O_CREAT|O_EXCL claim file per card identity; exactly one
        honor per card, replay/race refused.
  INV4  Per-session yield bound (3) — enforced here, keyed by session, so minting a
        fresh card per turn no longer grants unbounded yields.

INV3 (mechanical target RE-VERIFICATION at consumption) necessarily stays on the
CLIENT: the target is a path on the agent's host that the worker cannot stat. This
is the accepted residual — the WRITABLE trust-state moved; the filesystem check
stays where the filesystem is. See the ticket's "keep real residue".
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()

_STATE = Path(os.environ.get("TURNGATE_STATE_DIR", "/data/turngate"))
_LEDGER = _STATE / "ledger.jsonl"
_CLAIMS = _STATE / "claims"
_YIELDS = _STATE / "yields.json"

_YIELD_BOUND = 3

_TAXONOMY = frozenset({
    "credential_move", "destructive_host_op", "lock_asset",
    "brand_facing", "scope_change", "irreversible_external",
})
# Only classes with a mechanical, non-forgeable client-side target check may
# suppress the default-deny (mirrors check_context.py:_STOP_SUPPRESSING).
_STOP_SUPPRESSING = frozenset({"lock_asset", "credential_move", "destructive_host_op"})

# Single uvicorn worker (no --workers): a process-local lock serializes the
# yield-counter read-modify-write. The O_EXCL claim below is atomic across
# processes regardless, so the exactly-once guarantee never depends on this lock.
_LOCK = threading.Lock()


def _ensure() -> None:
    _STATE.mkdir(parents=True, exist_ok=True)
    _CLAIMS.mkdir(parents=True, exist_ok=True)


def _ident_key(action, klass, target, session, ts) -> str:
    """Canonical card identity — register and claim MUST key identically."""
    return json.dumps([action, klass, target, session, ts], sort_keys=True)


def _ident_hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()[:32]


def _in_ledger(key: str) -> bool:
    """INV1: True iff a registered row matches this identity."""
    try:
        with open(_LEDGER) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                rk = _ident_key(r.get("action"), r.get("class"), r.get("target"),
                                r.get("session"), r.get("ts"))
                if rk == key:
                    return True
    except FileNotFoundError:
        return False
    except Exception:
        return False
    return False


def _claim_once(ident_hash: str) -> bool:
    """INV2: atomic O_EXCL one-shot. True iff THIS caller created the claim; False
    if it already exists (replay) or cannot be created durably (then refuse)."""
    try:
        _CLAIMS.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(_CLAIMS / ident_hash),
                     os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            os.write(fd, ident_hash.encode())
        finally:
            os.close(fd)
        return True
    except FileExistsError:
        return False
    except Exception:
        return False


def _read_yields() -> tuple[dict, bool]:
    """Return (yields, ok). ok is False iff the store EXISTS but is unreadable /
    corrupt — the caller must then fail-CLOSED (we cannot prove we are under the
    bound). An absent store is a clean empty start (ok=True)."""
    if not _YIELDS.exists():
        return {}, True
    try:
        return json.loads(_YIELDS.read_text()), True
    except Exception:
        return {}, False


def _bump_yield(session: str) -> tuple[int, bool]:
    """INV4: increment + durably persist the per-session yield count. Returns
    (count, ok). ok is False if the store is corrupt OR the write did not persist —
    then the caller REFUSES the honor (fail-closed): a bound we cannot durably track
    must never grant a yield (Codex P0 — the old version failed OPEN, so a write
    failure let the count re-read low and the bound of 3 be exceeded). The write is
    atomic (tmp + os.replace) so an interrupted write cannot leave a torn/corrupt
    counter that resets the bound."""
    cur, ok = _read_yields()
    if not ok:
        return -1, False
    cur[session] = int(cur.get(session, 0)) + 1
    n = cur[session]
    try:
        _STATE.mkdir(parents=True, exist_ok=True)
        tmp = _YIELDS.with_suffix(".tmp")
        tmp.write_text(json.dumps(cur))
        os.replace(tmp, _YIELDS)
    except Exception:
        return n, False
    return n, True


class RegisterBody(BaseModel):
    action: str
    klass: str
    target: str
    evidence: str = ""
    ticket: str = ""
    session: str = "default"
    ts: str
    target_verified: bool = False


class ClaimBody(BaseModel):
    action: str
    klass: str
    target: str
    session: str = "default"
    ts: str


@router.post("/turn-gate/blocker/register")
def register_blocker(body: RegisterBody):
    """Record a blocker card in the server-side ledger (the SSOT for trusted
    issuance). Defense-in-depth validation mirrors the client; the ledger is the
    authority the claim endpoint checks against."""
    _ensure()
    if body.klass not in _TAXONOMY:
        return {"registered": False, "reason": f"'{body.klass}' is not a hard-gate class"}
    if len(body.evidence.strip()) < 20:
        return {"registered": False, "reason": "evidence must be >= 20 chars"}
    row = {
        "action": body.action, "class": body.klass, "target": body.target,
        "evidence": body.evidence, "ticket": body.ticket, "session": body.session,
        "ts": body.ts, "target_verified": bool(body.target_verified),
        "registered_at": datetime.now(timezone.utc).isoformat(),
    }
    key = _ident_key(body.action, body.klass, body.target, body.session, body.ts)
    with _LOCK:
        with open(_LEDGER, "a") as f:
            f.write(json.dumps(row) + "\n")
    return {"registered": True, "ident": _ident_hash(key)}


@router.post("/turn-gate/blocker/claim")
def claim_blocker(body: ClaimBody):
    """Honor a blocker yield exactly once, on non-agent-writable state.

    Returns {honored: bool, reason, yield_count, bound, over_bound}. The client
    honors the stop-suppression iff honored is True (and it has independently
    re-verified the target, INV3). Any refusal is explicit so the client never
    silently releases."""
    _ensure()
    key = _ident_key(body.action, body.klass, body.target, body.session, body.ts)
    ih = _ident_hash(key)
    with _LOCK:
        if not _in_ledger(key):
            return {"honored": False, "reason": "untrusted issuance — no matching ledger row"}
        if body.klass not in _STOP_SUPPRESSING:
            return {"honored": False, "reason": f"class '{body.klass}' is not stop-suppressing"}
        if not _claim_once(ih):
            return {"honored": False, "reason": "already claimed (replay/race guard)"}
        yc, ok = _bump_yield(body.session)
        if not ok:
            # cannot durably track the bound -> fail-closed (never honor a yield we
            # cannot count). The one-shot claim above is already spent, which is
            # correct: this identity is done, and no yield was granted.
            return {"honored": False, "fail_closed": True,
                    "reason": "yield counter unpersistable/corrupt — fail-closed"}
        if yc > _YIELD_BOUND:
            return {"honored": False, "over_bound": True, "yield_count": yc,
                    "bound": _YIELD_BOUND,
                    "reason": f"{yc} yields this session exceeds bound {_YIELD_BOUND}"}
        return {"honored": True, "yield_count": yc, "bound": _YIELD_BOUND}


@router.get("/turn-gate/blocker/status")
def blocker_status(session: Optional[str] = None):
    """Introspection: yield counts + bound. Read-only; no state change."""
    _ensure()
    try:
        yields = json.loads(_YIELDS.read_text())
    except Exception:
        yields = {}
    if session is not None:
        return {"session": session, "yield_count": int(yields.get(session, 0)),
                "bound": _YIELD_BOUND}
    return {"yields": yields, "bound": _YIELD_BOUND}
