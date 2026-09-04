import json
import logging
import os
import threading
import uuid
from datetime import datetime as _dt
from urllib.parse import urlparse
import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from config import VAULT_PATH
from safe_http import safe_json

logger = logging.getLogger(__name__)
router = APIRouter()

T2_QUEUE_FILE = VAULT_PATH / "00_System" / "t2_queue.json"
LEO_USER_ID = os.environ.get("LEO_USER_ID", "U0AG93XJ927")
_T2_LOCK = threading.RLock()
_ORCHESTRATOR_URL = "http://kai-orchestrator:8003"


def _gate_resolve_secret() -> str:
    """C2 [SEC] 8ae14701 phase-2 — dedicated credential for the orchestrator
    gate-resolve/override boundary (mirrors the council-api phase-1 guard)."""
    for _p in ("/run/secrets/gate_resolve_secret",
               "/home/leo/kai-system/secrets/gate_resolve_secret.txt"):
        try:
            _s = open(_p).read().strip()
        except OSError:
            continue
        if _s:
            return _s
    return ""


def _t2_load() -> list:
    if T2_QUEUE_FILE.exists():
        return json.loads(T2_QUEUE_FILE.read_text())
    return []


def _t2_save(queue: list):
    T2_QUEUE_FILE.write_text(json.dumps(queue, indent=2))


def _post_t2_result(entry: dict, approved: bool):
    # AR-5.3: Slack retired (AR-5) — dormant no-op (T2 result surfacing is Telegram).
    return


class T2ActionRequest(BaseModel):
    action: str
    detail: str = ""
    advisor: str = "kai"
    channel: str = ""  # ignored — all T2 prompts go to KAI↔Leo DM
    gate_id: str = ""
    callback_url: str = ""
    kind: str = ""
    # P-4a (proactive queue): a stable identity so the Finding→card producer never
    # double-posts the same finding (dedup on re-run). Empty for legacy/gate cards.
    dedup_key: str = ""
    # P-4a: DEFERRED-PUSH (Leo, 2026-08-31). notify=False creates the card SILENTLY —
    # a pull-only item, no Telegram/DM push — until Leo is actively using the system.
    # Defaults True so every existing caller (gates, advisors) is unchanged.
    notify: bool = True


class T2RespondRequest(BaseModel):
    action_id: str
    approved: bool
    user_id: str = ""
    notes: str = ""


@router.get("/t2/queue")
def get_t2_queue(kind: str = ""):
    """The queue. Optional ?kind= filters to one card kind — P-4a's proactive PULL
    view is GET /t2/queue?kind=finding (the digest reads only pending finding-cards)."""
    queue = _t2_load()
    if kind:
        queue = [e for e in queue if e.get("kind") == kind]
    return {"queue": queue}


@router.post("/t2/queue")
def create_t2_action(req: T2ActionRequest):
    with _T2_LOCK:
        queue = _t2_load()

        # P-4a dedup: a proactive finding-card carries a stable dedup_key. If one is
        # already PENDING for that key, return it instead of double-posting — so the
        # producer is safe to re-run every sweep. (Legacy/gate cards carry no key.)
        if req.kind == "finding" and req.dedup_key:
            for e in queue:
                if (e.get("kind") == "finding" and e.get("status") == "pending"
                        and e.get("dedup_key") == req.dedup_key):
                    return {"ok": True, "id": e["id"], "entry": e, "deduped": True}

        action_id = str(uuid.uuid4())[:8]
        entry = {
            "id": action_id,
            "action": req.action,
            "detail": req.detail,
            "advisor": req.advisor,
            "status": "pending",
            "created_at": _dt.now().isoformat(),
            # slack_ts / slack_channel_id: retired-Slack-named vestige fields, ALWAYS None
            # since AR-5.3 rerouted T2 notifications to Telegram (below). Kept as inert keys
            # to avoid a data migration of existing queue entries; removal deferred (KAI-1243).
            "slack_ts": None,
            "slack_channel_id": None,
            "gate_id": req.gate_id,
            "callback_url": req.callback_url,
            "kind": req.kind,
            "dedup_key": req.dedup_key,
        }

        # AR-5.3: rerouted to Telegram (sole surface). T2 request notification.
        # P-4a: notify=False creates SILENTLY (deferred-push) — pull-only, no ping.
        if req.notify:
            try:
                from tg_alert import tg_alert
                tg_alert(
                    f"T2 Action Request — {action_id}\n"
                    f"Advisor: {req.advisor.upper()}\n"
                    f"Action: {req.action}"
                    + (f"\nDetail: {req.detail}" if req.detail else "")
                )
            except Exception as e:
                logger.exception("T2 Telegram post error: %s", e)

        queue.append(entry)
        _t2_save(queue)
        return {"ok": True, "id": action_id, "entry": entry}


@router.post("/t2/respond")
def respond_t2_action(req: T2RespondRequest):
    with _T2_LOCK:
        queue = _t2_load()
        for entry in queue:
            if entry["id"] != req.action_id:
                continue
            if entry["status"] != "pending":
                return {"ok": False, "error": f"Action already {entry['status']}", "entry": entry}

            if entry.get("kind") == "hostops_gate":
                gate_id = entry.get("gate_id")
                if not gate_id:
                    raise HTTPException(500, "hostops T2 action missing gate binding")
                callback_url = entry.get("callback_url", "")
                parsed = urlparse(callback_url)
                resolve_url = callback_url or f"{_ORCHESTRATOR_URL}/gates/{gate_id}/resolve"
                if callback_url and (parsed.scheme != "http" or parsed.netloc != "kai-orchestrator:8003"):
                    raise HTTPException(500, "hostops T2 action has invalid gate callback")
                notes = req.notes or ("Approved by Leo via T2 tap" if req.approved else "Rejected by Leo via T2 tap")
                resolution = {"approved": req.approved, "notes": notes}
                if req.approved:
                    resolution["advisor"] = req.user_id
                try:
                    response = httpx.post(resolve_url, json=resolution, timeout=15,
                                          headers={"X-KAI-Gate-Resolve": _gate_resolve_secret()})
                    response.raise_for_status()
                    orchestrator_response = safe_json(response, default={"body": response.text})
                except httpx.HTTPError as exc:
                    raise HTTPException(502, f"hostops gate resolve failed: {exc}") from exc
                entry["status"] = "approved" if req.approved else "rejected"
                entry["responded_by"] = req.user_id
                entry["responded_at"] = _dt.now().isoformat()
                _t2_save(queue)
                logger.info("Hostops T2 gate %s resolved by %s", gate_id, req.user_id)
                _post_t2_result(entry, req.approved)
                return {"ok": True, "kind": "hostops_gate", "executed": True,
                        "entry": entry, "orchestrator": orchestrator_response}

            entry["status"] = "approved" if req.approved else "rejected"
            entry["responded_by"] = req.user_id
            entry["responded_at"] = _dt.now().isoformat()
            _t2_save(queue)
            logger.info("T2 respond %s -> %s: %s", req.action_id, entry["status"], entry["action"])
            _post_t2_result(entry, req.approved)
            return {"ok": True, "kind": entry.get("kind", "t2"), "executed": False, "entry": entry}
    raise HTTPException(404, f"T2 action {req.action_id} not found")


@router.post("/t2/approve/{action_id}")
def approve_t2_action(action_id: str):
    queue = _t2_load()
    for entry in queue:
        if entry["id"] == action_id:
            entry["status"] = "approved"
            entry["approved_at"] = _dt.now().isoformat()
            _t2_save(queue)
            logger.info("T2 action %s approved: %s", action_id, entry['action'])
            return {"ok": True, "entry": entry}
    raise HTTPException(404, f"T2 action {action_id} not found")


@router.post("/t2/reject/{action_id}")
def reject_t2_action(action_id: str):
    queue = _t2_load()
    for entry in queue:
        if entry["id"] == action_id:
            entry["status"] = "rejected"
            entry["rejected_at"] = _dt.now().isoformat()
            _t2_save(queue)
            return {"ok": True, "entry": entry}
    raise HTTPException(404, f"T2 action {action_id} not found")
