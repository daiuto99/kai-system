"""Read-only mirror of kai-council-api/function_map.py — CONTEXT_SPEC §13 Tier 5.

Tier 5's org_model_context block (persona.py's former load_org_model_context())
needs org_model.json/specialists.json read functions inside the orchestrator
process. There is no shared Python package between containers (same pattern
as safe_path.py, per MEG v2.26), so this mirrors the subset of
kai-council-api/function_map.py that Tier 5 actually consumes. The canonical
data — org_model.json + specialists.json at VAULT_PATH/00_System — stays the
single source of truth (L7); this is a second in-process reader of the same
files, not a second copy of the data.
"""
import json
import logging
import threading
from pathlib import Path
from typing import Any

VAULT_PATH = Path("/vault")

logger = logging.getLogger(__name__)

_ORG_MODEL_PATH   = VAULT_PATH / "00_System" / "org_model.json"
_SPECIALISTS_PATH = VAULT_PATH / "00_System" / "specialists.json"

_lock = threading.Lock()
_cache: dict[str, Any] = {
    "org_model_mtime": 0.0,
    "specialists_mtime": 0.0,
    "org_model": None,
    "specialists": None,
}


def _load() -> tuple[dict, list]:
    """Reload either file if its mtime has changed. Returns (org_model, specialists)."""
    with _lock:
        om_mtime = _ORG_MODEL_PATH.stat().st_mtime if _ORG_MODEL_PATH.exists() else 0.0
        sp_mtime = _SPECIALISTS_PATH.stat().st_mtime if _SPECIALISTS_PATH.exists() else 0.0

        if _cache["org_model"] is None or om_mtime != _cache["org_model_mtime"]:
            if _ORG_MODEL_PATH.exists():
                _cache["org_model"] = json.loads(_ORG_MODEL_PATH.read_text())
                _cache["org_model_mtime"] = om_mtime
            else:
                logger.error("function_map_read: org_model.json missing at %s", _ORG_MODEL_PATH)
                _cache["org_model"] = {}

        if _cache["specialists"] is None or sp_mtime != _cache["specialists_mtime"]:
            if _SPECIALISTS_PATH.exists():
                _cache["specialists"] = json.loads(_SPECIALISTS_PATH.read_text())
                _cache["specialists_mtime"] = sp_mtime
            else:
                logger.error("function_map_read: specialists.json missing at %s", _SPECIALISTS_PATH)
                _cache["specialists"] = []

        return _cache["org_model"], _cache["specialists"]


def get_specialist(specialist_id: str) -> dict | None:
    """Registry-authoritative specialist record, including its declared persona path."""
    _, specialists = _load()
    return next((item for item in specialists if item.get("id") == specialist_id), None)


def list_advisor_domains() -> list[dict]:
    """All domain->advisor entries (excluding the direct_advisors meta-key)."""
    om, _ = _load()
    out = []
    for domain, info in om.get("advisor_domain_map", {}).items():
        if domain == "direct_advisors" or not isinstance(info, dict):
            continue
        out.append({
            "domain": domain,
            "advisor": info.get("advisor"),
            "keywords": list(info.get("keywords", [])),
        })
    return out


def list_direct_advisors() -> list[str]:
    """Advisors Leo also talks to directly (per org_model.advisor_domain_map.direct_advisors)."""
    om, _ = _load()
    return list(om.get("advisor_domain_map", {}).get("direct_advisors", []))


def get_first_receiver_for_bug() -> str:
    """Who receives every bug first. Per org_model — currently support-engineer."""
    om, _ = _load()
    return om.get("governance", {}).get("bug_triage", {}).get("first_receiver", "support-engineer")


def list_routing_rules() -> dict[str, dict]:
    """All routing rules keyed by task type."""
    om, _ = _load()
    return dict(om.get("routing_rules", {}))


def get_governance(role: str) -> dict | None:
    """Governance block for client / pm / creative_agency / engineering_agency / ..."""
    om, _ = _load()
    return om.get("governance", {}).get(role)
