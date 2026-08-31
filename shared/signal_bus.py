"""Voice signal bus — file-based presence state (KAI-1283 P-3, local voice layer).

The single place KAI's live conversational state lives so the dashboard and kiosk
can *feel* present, not just show a chat box. CPU-only, no broker: one JSON file,
atomic write, last-writer-wins, monotonic sequence. The voice services (STT now,
TTS + council later) write state transitions; readers (orchestrator /presence →
dashboard + kiosk) render them.

States (the only valid set): idle | listening | thinking | speaking | error.

Freshness is a first-class property. A writer that dies mid-turn must not pin the
surface on "thinking" forever, so read_state() treats any state older than
STALE_AFTER_S as idle (with `stale: true`), never trusting a wedged timestamp.

Fail-soft everywhere: a read or write failure degrades to a safe idle default and
never raises into the caller's path — the presence layer is cosmetic and must never
take down the service that writes to it.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("signal_bus")

# Authored home: the vault system dir, mounted rw into the voice-bearing containers.
BUS_PATH = Path(os.environ.get("KAI_SIGNAL_BUS_PATH", "/vault/00_System/signal_bus.json"))

VALID_STATES = ("idle", "listening", "thinking", "speaking", "error")

# A live turn (speak → transcribe → think → reply) is seconds, not minutes. Past
# this window with no update, assume the writer died and fall back to idle.
STALE_AFTER_S = 30.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _idle(reason: str = "default", *, stale: bool = False) -> dict:
    return {
        "state": "idle",
        "detail": None,
        "source": None,
        "seq": 0,
        "updated_at": _now_iso(),
        "stale": stale,
        "_reason": reason,
    }


def read_state() -> dict:
    """Return the current presence state, always a valid dict.

    Never raises. Missing/corrupt file → idle default. A state whose updated_at is
    older than STALE_AFTER_S is reported as idle with `stale: True` so a wedged
    writer can't pin the surface.
    """
    if not BUS_PATH.exists():
        return _idle("absent")
    try:
        data = json.loads(BUS_PATH.read_text())
    except Exception as e:  # noqa: BLE001 — cosmetic layer, degrade don't raise
        log.warning("signal_bus: read error, defaulting idle: %s", e)
        return _idle("unreadable")

    state = data.get("state")
    if state not in VALID_STATES:
        return _idle("invalid_state")

    # Staleness check against updated_at.
    try:
        ts = datetime.fromisoformat(data["updated_at"])
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - ts).total_seconds()
    except Exception:
        age = None

    if state != "idle" and age is not None and age > STALE_AFTER_S:
        out = _idle("stale", stale=True)
        out["seq"] = int(data.get("seq", 0))
        return out

    return {
        "state": state,
        "detail": data.get("detail"),
        "source": data.get("source"),
        "seq": int(data.get("seq", 0)),
        "updated_at": data.get("updated_at"),
        "stale": False,
    }


def set_state(state: str, detail: str | None = None, source: str | None = None) -> dict:
    """Write a new presence state atomically. Returns the written record.

    Invalid state → coerced to 'error' rather than raising, so a caller bug surfaces
    visibly on the dashboard instead of crashing the turn. seq is monotonic off the
    prior record.
    """
    if state not in VALID_STATES:
        log.warning("signal_bus: invalid state %r coerced to 'error'", state)
        detail = detail or f"invalid state {state!r}"
        state = "error"

    prior = 0
    try:
        if BUS_PATH.exists():
            prior = int(json.loads(BUS_PATH.read_text()).get("seq", 0))
    except Exception:
        prior = int(time.time())  # monotonic-enough fallback if prior is corrupt

    record = {
        "state": state,
        "detail": detail,
        "source": source,
        "seq": prior + 1,
        "updated_at": _now_iso(),
        "stale": False,
    }

    try:
        BUS_PATH.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(BUS_PATH.parent), prefix=".signal_bus.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(record, f)
            os.replace(tmp, BUS_PATH)  # atomic on POSIX
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    except Exception as e:  # noqa: BLE001
        log.warning("signal_bus: write error, state not persisted: %s", e)

    return record


if __name__ == "__main__":  # tiny manual smoke: python signal_bus.py listening "heard you"
    import sys

    if len(sys.argv) > 1:
        print(set_state(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None, "cli"))
    else:
        print(read_state())
