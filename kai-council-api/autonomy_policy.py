"""Machine-readable autonomy decisions shared by future gate types."""
from dataclasses import dataclass
import json
import os
from pathlib import Path


_ORG_MODEL_PATH = Path(os.environ.get("KAI_ORG_MODEL_PATH", "/vault/00_System/org_model.json"))


@dataclass(frozen=True)
class Decision:
    mode: str
    reason: str


def _thresholds() -> tuple[str, ...]:
    model = json.loads(_ORG_MODEL_PATH.read_text())
    return tuple(model["routing_rules"]["infrastructure_task"]["high_risk_threshold"])


def classify(action: dict) -> Decision:
    """Apply the determined policy; thresholds are read from org_model.json."""
    owner = str(action.get("owner", "leo")).lower()
    if action.get("external_party") or owner in {"client", "external"}:
        if action.get("specific_person"):
            return Decision("confirm_once", "specific person outside Leo requires one confirmation")
        return Decision("approve", "external-facing or non-Leo owner requires approval")
    haystack = " ".join(str(action.get(k, "")).lower() for k in ("op", "target", "site", "risk"))
    for threshold in _thresholds():
        if threshold.lower() in haystack:
            return Decision("approve", f"high-risk threshold: {threshold}")
    return Decision("autonomous", "low-risk Leo-owned action")
