"""Council gate endpoint — receives workflow gates from kai-orchestrator,
processes them async, and POSTs resolution back to the callback URL.

Gate types:
  dev            — KAI reviews the brief, auto-approves (engineering checkpoint)
  creative_review — Ember drafts creative, stores in vault, notifies Leo to review

NOTE: _process_gate is intentionally a sync function (not async def) so that
FastAPI's BackgroundTasks runs it in a thread pool. This prevents LangGraph's
synchronous graph.invoke() from blocking the asyncio event loop before the
HTTP response is flushed to the orchestrator.
"""
import json
import logging
import threading
from pathlib import Path
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter()

_GATES_STORE: dict[str, dict] = {}  # in-process store for gate state (keyed by gate_id)
_VAULT_GATES = Path("/vault/00_System/gates")


class GateRequest(BaseModel):
    gate_id:      str
    gate_type:    str = "dev"
    brief:        dict
    callback_url: str


@router.post("/council/gate")
async def receive_gate(req: GateRequest, background_tasks: BackgroundTasks):
    """Accept a gate from the orchestrator and schedule async processing."""
    _GATES_STORE[req.gate_id] = {
        "gate_id":    req.gate_id,
        "gate_type":  req.gate_type,
        "brief":      req.brief,
        "status":     "processing",
        "resolution": None,
    }
    # Sync background task → runs in thread pool, not blocking the event loop
    background_tasks.add_task(_process_gate, req)
    return {"gate_id": req.gate_id, "status": "accepted"}


@router.get("/council/gate/{gate_id}/state")
def gate_state(gate_id: str):
    """Fallback poll — orchestrator checks here every 30s if callback was missed."""
    entry = _GATES_STORE.get(gate_id)
    if entry is None:
        return {"error": "gate not found"}
    return {
        "gate_id":    gate_id,
        "status":     entry["status"],
        "resolution": entry["resolution"],
    }


def _process_gate(req: GateRequest):
    """Run the gate through the council (sync, runs in thread pool) and fire the callback."""
    try:
        resolution = _run_council_review(req)
        _GATES_STORE[req.gate_id]["status"]     = "resolved"
        _GATES_STORE[req.gate_id]["resolution"] = resolution

        _persist_gate(req, resolution)
        _fire_callback(req.callback_url, resolution)

    except Exception as e:
        logger.exception("Gate processing failed for %s", req.gate_id)
        _GATES_STORE[req.gate_id]["status"] = "error"
        _fire_callback(req.callback_url, {
            "approved": False,
            "notes":    f"Gate processing error: {e}",
            "advisor":  "system",
        })


def _run_council_review(req: GateRequest) -> dict:
    """Route the gate brief to the appropriate advisor and return a resolution."""
    gate_type = req.gate_type
    brief     = req.brief

    if gate_type == "dev":
        return _dev_review(brief)

    if gate_type == "creative_review":
        return _creative_review(brief, req.gate_id)

    logger.warning("Unknown gate_type %r — auto-approving", gate_type)
    return {
        "approved": True,
        "notes":    f"Auto-approved: unknown gate_type '{gate_type}'",
        "advisor":  "system",
    }


def _dev_review(brief: dict) -> dict:
    """Engineering checkpoint — run brief through graph, auto-approve."""
    try:
        from graphs.graph import get_graph
        graph = get_graph()
        summary = json.dumps(brief, indent=2)[:1500]
        state = {
            "channel":        "system",
            "message":        f"[Dev gate review]\n{summary}",
            "user_id":        "orchestrator",
            "thread_ts":      brief.get("job_id", ""),
            "attachments":    [],
            "privacy_mode":   False,
            "history":        [],
            "target_advisor": "",
            "routing_reason": "",
            "advisor_reply":  "",
            "final_reply":    "",
            "model_used":     "",
            "input_tokens":   0,
            "output_tokens":  0,
            "audit_log":      [],
        }
        result = graph.invoke(state, config={"configurable": {
            "thread_id": brief.get("job_id", "dev-gate")
        }})
        notes = result.get("final_reply", "")[:500]
        return {"approved": True, "notes": notes, "advisor": "kai"}
    except Exception as e:
        logger.exception("Dev review graph call failed")
        return {"approved": True, "notes": f"Graph unavailable: {e}", "advisor": "system"}


def _creative_review(brief: dict, gate_id: str) -> dict:
    """Ember creative review — persist draft to vault, auto-approve with draft as notes."""
    try:
        from graphs.graph import get_graph
        graph = get_graph()
        summary = json.dumps(brief, indent=2)[:2000]
        state = {
            "channel":        "creative",
            "message":        f"[Creative gate]\n{summary}",
            "user_id":        "orchestrator",
            "thread_ts":      gate_id,
            "attachments":    [],
            "privacy_mode":   False,
            "history":        [],
            "target_advisor": "ember",
            "routing_reason": "",
            "advisor_reply":  "",
            "final_reply":    "",
            "model_used":     "",
            "input_tokens":   0,
            "output_tokens":  0,
            "audit_log":      [],
        }
        result = graph.invoke(state, config={"configurable": {"thread_id": gate_id}})
        creative_draft = result.get("final_reply", "")

        _VAULT_GATES.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        draft_path = _VAULT_GATES / f"{ts}_{gate_id[:8]}_creative.md"
        draft_path.write_text(
            f"# Creative Gate — {gate_id}\n\n"
            f"**Brief:**\n```json\n{json.dumps(brief, indent=2)}\n```\n\n"
            f"**Ember Draft:**\n\n{creative_draft}\n"
        )
        logger.info("Creative draft saved: %s", draft_path)

        return {
            "approved":   True,
            "notes":      creative_draft[:1000],
            "advisor":    "ember",
            "vault_path": str(draft_path),
        }
    except Exception as e:
        logger.exception("Creative review failed")
        return {"approved": True, "notes": f"Creative review error: {e}", "advisor": "system"}


def _fire_callback(callback_url: str, resolution: dict):
    """POST resolution back to the orchestrator callback URL."""
    try:
        r = httpx.post(callback_url, json=resolution, timeout=10)
        if r.status_code == 200:
            logger.info("Gate callback OK: %s", callback_url)
        else:
            logger.warning("Gate callback %s returned %d: %s",
                           callback_url, r.status_code, r.text[:200])
    except Exception as e:
        logger.exception("Gate callback failed: %s — %s", callback_url, e)


def _persist_gate(req: GateRequest, resolution: dict):
    """Write gate audit record to vault."""
    try:
        _VAULT_GATES.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        record = {
            "gate_id":    req.gate_id,
            "gate_type":  req.gate_type,
            "brief":      req.brief,
            "resolution": resolution,
            "resolved_at": ts,
        }
        audit_path = _VAULT_GATES / f"{ts}_{req.gate_id[:8]}.json"
        audit_path.write_text(json.dumps(record, indent=2))
    except Exception:
        logger.exception("Gate audit persist failed for %s", req.gate_id)
