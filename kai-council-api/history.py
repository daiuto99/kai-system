"""GET/DELETE /history/{channel} — dashboard's read API onto the Memory Service.

CONTEXT_SPEC §13 Phase 1.5: this used to read/write a flat per-channel JSONL
(vault/60_Council/_history/{channel}.jsonl) that mixed every surface's turns
into one namespace (the F7 identity-entanglement bug) and was never actually
read back into model context (F2 — BUG e5e54431's root cause). Both bugs are
fixed by the Memory Service (context_service.py, on kai-orchestrator): this
module is now a thin proxy onto GET /context/conversation, scoped to the
dashboard's own device key so it stops doing the server's job by hand.

The legacy JSONL files under _history/ are frozen read-only artifacts now —
seeded once into the new store (see the one-time import), never written to
or read from again.
"""
import logging
import httpx
from fastapi import APIRouter
from council_config import ADVISOR_CHANNELS, ORCHESTRATOR_URL

logger = logging.getLogger(__name__)
router = APIRouter()


def _dashboard_key(channel: str) -> dict:
    advisor = ADVISOR_CHANNELS.get(channel, channel)
    return {"advisor": advisor, "device": f"dashboard:chat:{channel}"}


@router.get("/history/{channel}")
def get_history(channel: str, limit: int = 50):
    key = _dashboard_key(channel)
    try:
        r = httpx.get(
            f"{ORCHESTRATOR_URL}/context/conversation",
            params={"advisor": key["advisor"], "device": key["device"], "limit": limit},
            timeout=10,
        )
        r.raise_for_status()
        turns = r.json().get("turns", [])
    except Exception as e:
        logger.exception("history.get_history: context.get_conversation failed: %s", e)
        return {"messages": [], "channel": channel}

    messages = [
        {
            "role": t["role"],
            "content": t["content"],
            # epoch-seconds-as-string — matches the format the dashboard
            # already constructs client-side (Date.now() / 1000).
            "ts": _to_epoch_seconds(t.get("created_at")),
        }
        for t in turns
    ]
    return {"messages": messages, "channel": channel}


def _to_epoch_seconds(ts: str | None) -> str:
    """created_at is ISO8601 ("...Z", from context_service.now_iso()) for turns
    recorded going forward, but legacy-imported turns (§13 one-time seed import)
    preserve their original epoch-float-string ts from the old JSONL — handle
    both so imported history doesn't silently render blank timestamps."""
    if not ts:
        return ""
    try:
        return str(float(ts))
    except ValueError:
        pass
    try:
        from datetime import datetime
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return str(dt.timestamp())
    except Exception:
        return ""


@router.delete("/history/{channel}")
def clear_history(channel: str):
    """Clears the dashboard's own display only. Deleting the authoritative
    server-side conversation store is a retention/deletion capability that
    doesn't exist yet (§9's deterministic-delete requirement is Phase 3
    scope) — this endpoint does not pretend to do it."""
    return {
        "ok": True,
        "channel": channel,
        "note": "Server-side conversation memory is unaffected — deletion is "
                "not yet implemented (CONTEXT_SPEC §9, Phase 3 scope).",
    }
