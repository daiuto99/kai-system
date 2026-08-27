"""L6 destructive-op governance (S5R-3).

Every destructive capability call must:
  1. Validate operator + reason in the request body (422 if absent/short).
  2. Call audit_before() which writes to capability_audit.jsonl BEFORE execution.
  3. Mirror the record to Telegram BEFORE execution.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

AUDIT_LOG = Path("/vault/00_System/capability_audit.jsonl")


class DestructiveRequest(BaseModel):
    operator: str = Field(..., min_length=1, description="Who is requesting (e.g. 'leo', 'kai-council')")
    reason: str = Field(..., min_length=10, description="Why this destructive op is being performed (>=10 chars)")


def audit_before(endpoint: str, detail: dict, operator: str, reason: str) -> dict:
    """Write audit JSONL + Telegram mirror. MUST be called before the destructive op runs."""
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

    # AR-5.3: Telegram mirror (sole surface, AR-5) — non-blocking on failure;
    # the append-only JSONL above is the source of truth.
    try:
        from tg_alert import tg_alert
        tg_alert(
            f"🚨 Destructive op — {endpoint}\n"
            f"Operator: {operator}  |  Reason: {reason}\n"
            f"Detail: {json.dumps(detail)}  |  ts: {record['ts']}"
        )
    except Exception as e:
        logger.warning("audit telegram mirror failed (audit log written): %s", e)

    return record
