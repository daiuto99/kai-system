"""L6 destructive-op governance (S5R-3).

Every destructive capability call must:
  1. Validate operator + reason in the request body (422 if absent/short).
  2. Call audit_before() which writes to capability_audit.jsonl BEFORE execution.
  3. Mirror the record to Slack #kai-system BEFORE execution.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import httpx
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

AUDIT_LOG = Path("/vault/00_System/capability_audit.jsonl")
_SLACK_CHANNEL = "#devops"


class DestructiveRequest(BaseModel):
    operator: str = Field(..., min_length=1, description="Who is requesting (e.g. 'leo', 'kai-council')")
    reason: str = Field(..., min_length=10, description="Why this destructive op is being performed (>=10 chars)")


def _slack_token() -> str:
    p = Path("/run/secrets/slack_bot_token")
    return p.read_text().strip() if p.exists() else os.environ.get("SLACK_BOT_TOKEN", "")


def audit_before(endpoint: str, detail: dict, operator: str, reason: str) -> dict:
    """Write audit JSONL + Slack mirror. MUST be called before the destructive op runs."""
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "endpoint": endpoint,
        "operator": operator,
        "reason": reason,
        "detail": detail,
    }

    # Append-only JSONL written first — this is the audit trail that proves ordering
    AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(AUDIT_LOG, "a") as f:
        f.write(json.dumps(record) + "\n")

    # Slack mirror to #kai-system (non-blocking on failure — audit log is the source of truth)
    token = _slack_token()
    if token:
        try:
            text = (
                f":rotating_light: *Destructive op* — `{endpoint}`\n"
                f"*Operator:* {operator}  |  *Reason:* {reason}\n"
                f"*Detail:* `{json.dumps(detail)}`  |  *ts:* {record['ts']}"
            )
            httpx.post(
                "https://slack.com/api/chat.postMessage",
                headers={"Authorization": f"Bearer {token}"},
                json={"channel": _SLACK_CHANNEL, "text": text, "username": "KAI DevOps"},
                timeout=10,
            )
        except Exception as e:
            logger.warning("audit slack mirror failed (audit log written): %s", e)

    return record
