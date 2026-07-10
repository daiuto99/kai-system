import json
import uuid
import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

from graphs.graph import get_graph
from insights import extract_and_strip_insights, append_insights_to_vault, strip_markdown

logger = logging.getLogger(__name__)
router = APIRouter()

AUDIT_DIR = Path("/vault/00_System/orchestration_audit")


class OrchestrationRequest(BaseModel):
    channel: str
    message: str
    user_id: str = ""
    history: list = []
    thread_ts: str = ""
    attachments: list = []
    privacy_mode: bool = False


@router.post("/council/orchestrate")
def orchestrate(req: OrchestrationRequest):
    graph = get_graph()
    thread_id = req.thread_ts or str(uuid.uuid4())

    initial_state = {
        "channel": req.channel,
        "message": req.message,
        "user_id": req.user_id,
        "thread_ts": req.thread_ts,
        "attachments": req.attachments,
        "privacy_mode": req.privacy_mode,
        "history": req.history,
        "target_advisor": "",
        "routing_reason": "",
        "advisor_reply": "",
        "final_reply": "",
        "model_used": "",
        "input_tokens": 0,
        "output_tokens": 0,
        "audit_log": [],
    }

    config = {"configurable": {"thread_id": thread_id}}
    result = graph.invoke(initial_state, config=config)

    advisor = result["target_advisor"]
    raw_reply = result["final_reply"]

    # Insight extraction for Ember
    if advisor == "ember":
        clean_reply, insights = extract_and_strip_insights(raw_reply)
        append_insights_to_vault(insights)
    else:
        clean_reply = raw_reply

    clean_reply = strip_markdown(clean_reply)

    channel = req.channel.lstrip("#")

    # Audit log
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with (AUDIT_DIR / f"{today}.jsonl").open("a") as f:
        f.write(json.dumps({
            "ts": datetime.now(timezone.utc).isoformat(),
            "thread_id": thread_id,
            "channel": req.channel,
            "user_id": req.user_id,
            "target_advisor": advisor,
            "routing_reason": result["routing_reason"],
            "model": result["model_used"],
            "input_tokens": result["input_tokens"],
            "output_tokens": result["output_tokens"],
        }) + "\n")

    return {
        "reply": clean_reply,
        "advisor": advisor,
        "model": result["model_used"],
        "routing_reason": result["routing_reason"],
        "usage": {
            "input_tokens": result["input_tokens"],
            "output_tokens": result["output_tokens"],
        },
    }
