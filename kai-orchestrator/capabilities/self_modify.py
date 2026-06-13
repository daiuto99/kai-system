"""self_modify.propose capability — M2-1.A scaffold.

Records a devops self-modify proposal: a Plane-tracked, ritually-justified
diff for a system file under the orchestrator's reach. M2-1.A only LOGS
the proposal — it does not verify, apply, commit, or open a Plane gate.
M2-1.B adds the semantic verifier + apply/commit/Plane chain.
M2-1.C adds the KAI-mediated approval prompt + Mode-Lock cutover.

Input contract (validated here):
  plane_ticket_id  (str, required)  — Plane issue this self-modify resolves
  gate             (str, required)  — §3 JARVIS gate line moved toward
  principle        (str, required)  — §5 LSE operating principle invoked
  retirement       (str, required)  — what gets retired/simplified alongside
  diff             (str, required)  — unified diff to be applied (NOT applied in A)

The structured record is appended to
  /vault/00_System/self_modify_proposals.jsonl
"""
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from models import CapabilityResult
from . import capability

_LOG_PATH = Path("/vault/00_System/self_modify_proposals.jsonl")

_REQUIRED = ("plane_ticket_id", "gate", "principle", "retirement", "diff")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _validate(inputs: dict) -> tuple[bool, list[str]]:
    missing = [k for k in _REQUIRED if not inputs.get(k)]
    return (not missing), missing


@capability("self_modify.propose")
def propose(**inputs) -> CapabilityResult:
    """Record a self-modify proposal. Scaffold only — does not apply the diff."""
    ok, missing = _validate(inputs)
    if not ok:
        return CapabilityResult(
            ok=False, status="failed_permanent",
            error={"type": "missing_required_fields", "missing": missing,
                   "required": list(_REQUIRED)},
        )

    proposal_id = f"prop_{uuid.uuid4().hex[:12]}"
    record = {
        "proposal_id": proposal_id,
        "logged_at": _now_iso(),
        "plane_ticket_id": inputs["plane_ticket_id"],
        "ritual": {
            "gate": inputs["gate"],
            "principle": inputs["principle"],
            "retirement": inputs["retirement"],
        },
        "diff": {
            "bytes": len(inputs["diff"]),
            "lines": inputs["diff"].count("\n") + 1,
            "preview": inputs["diff"][:400],
            "body": inputs["diff"],
        },
        "stage": "proposed",
        "scaffold_version": "M2-1.A",
    }

    try:
        _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _LOG_PATH.open("a") as f:
            f.write(json.dumps(record) + "\n")
    except Exception as e:
        return CapabilityResult(
            ok=False, status="failed_recoverable",
            error={"type": "log_write_failed", "detail": str(e),
                   "path": str(_LOG_PATH)},
        )

    return CapabilityResult(
        ok=True, status="succeeded",
        data={
            "proposal_id": proposal_id,
            "logged_at": record["logged_at"],
            "log_path": str(_LOG_PATH),
            "diff_bytes": record["diff"]["bytes"],
            "stage": "proposed",
        },
        verification={
            "verified": True,
            "method": "jsonl_append",
            "evidence": {"proposal_id": proposal_id,
                         "path": str(_LOG_PATH)},
        },
    )
