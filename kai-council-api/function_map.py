"""Function map — single read-side source of truth for routing decisions.

Every "which advisor / which specialist / who receives bugs / what gate fires"
question the orchestration layer asks is answered here, against the canonical
JSON specs at vault/00_System/org_model.json + specialists.json.

Sprint 03 T1 — substrate. T2 (S11-6) and T3 (S11-7) replace improvised lookups
in routes_council_gate.py, council_config.py, complexity.py, and the scheduler
with calls into this module.
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path  # noqa: F401
from typing import Any

from council_config import VAULT_PATH

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
                logger.error("function_map: org_model.json missing at %s", _ORG_MODEL_PATH)
                _cache["org_model"] = {}

        if _cache["specialists"] is None or sp_mtime != _cache["specialists_mtime"]:
            if _SPECIALISTS_PATH.exists():
                _cache["specialists"] = json.loads(_SPECIALISTS_PATH.read_text())
                _cache["specialists_mtime"] = sp_mtime
            else:
                logger.error("function_map: specialists.json missing at %s", _SPECIALISTS_PATH)
                _cache["specialists"] = []

        return _cache["org_model"], _cache["specialists"]


# ── Advisor routing (S11-6 consumer surface) ──────────────────────────────────

def get_advisor_for_domain(text: str) -> dict:
    """Resolve free text to an advisor via org_model.advisor_domain_map keywords.

    Returns {"advisor": str|None, "domain": str|None, "matched_keyword": str|None}.
    Case-insensitive substring match. First domain whose keyword appears wins —
    domain order in org_model is authoritative (specific before general).
    """
    om, _ = _load()
    domain_map = om.get("advisor_domain_map", {})
    lower = (text or "").lower()
    for domain, info in domain_map.items():
        if domain == "direct_advisors" or not isinstance(info, dict):
            continue
        for kw in info.get("keywords", []):
            if kw and kw.lower() in lower:
                return {"advisor": info.get("advisor"), "domain": domain, "matched_keyword": kw}
    return {"advisor": None, "domain": None, "matched_keyword": None}


def list_advisor_domains() -> list[dict]:
    """All domain→advisor entries (excluding the direct_advisors meta-key)."""
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


# ── Bug routing (S11-7 consumer surface) ──────────────────────────────────────

def get_first_receiver_for_bug() -> str:
    """Who receives every bug first. Per org_model — currently support-engineer."""
    om, _ = _load()
    return om.get("governance", {}).get("bug_triage", {}).get("first_receiver", "support-engineer")


def get_bug_escalation_path() -> list[str]:
    """Escalation chain after the first receiver triages."""
    om, _ = _load()
    return list(om.get("governance", {}).get("bug_triage", {}).get("escalation_path", []))


def get_bug_owner(category: str) -> str | None:
    """Map a bug category to the owning team (code_bug→dev, infra_bug→devops, ...)."""
    om, _ = _load()
    routing = om.get("routing_rules", {}).get("bug", {}).get("routing", {})
    return routing.get(category)


def get_team_assignee(team: str) -> str | None:
    """Plane user UUID for a team role (devops/dev/creative/kai). None means
    no Plane user exists for that role yet — caller should fall back to devops.
    """
    om, _ = _load()
    return om.get("governance", {}).get("team_assignees", {}).get(team)


def get_team_slack_channel(team: str) -> str:
    """Slack channel for triage notifications for a team role. Defaults to #devops."""
    om, _ = _load()
    channels = om.get("governance", {}).get("team_slack_channels", {})
    return channels.get(team) or "#devops"


# ── Specialists ───────────────────────────────────────────────────────────────

def get_specialist(specialist_id: str) -> dict | None:
    """Full specialist record by id."""
    _, specs = _load()
    return next((s for s in specs if s.get("id") == specialist_id), None)


def get_specialists_by_director(director: str) -> list[dict]:
    """All specialists reporting to a given director (creative, dev, doc, coach, kai)."""
    _, specs = _load()
    return [s for s in specs if s.get("director") == director]


def list_specialists() -> list[dict]:
    """All specialists — light projection (id, name, director, domain)."""
    _, specs = _load()
    return [
        {"id": s.get("id"), "name": s.get("name"),
         "director": s.get("director"), "domain": s.get("domain")}
        for s in specs
    ]


# ── Gate + routing rule policies ──────────────────────────────────────────────

def get_gate_policy(gate_id: str) -> dict | None:
    """Policy block for a named gate (plan_gate, creative_gate, dev_gate, devops_gate)."""
    om, _ = _load()
    return om.get("gate_policies", {}).get(gate_id)


def get_routing_rule(rule_id: str) -> dict | None:
    """Routing rule for a task type (project_brief, creative_task, engineering_task, ...)."""
    om, _ = _load()
    return om.get("routing_rules", {}).get(rule_id)


def list_routing_rules() -> dict[str, dict]:
    """All routing rules keyed by task type."""
    om, _ = _load()
    return dict(om.get("routing_rules", {}))


def get_governance(role: str) -> dict | None:
    """Governance block for client / pm / creative_agency / engineering_agency / ..."""
    om, _ = _load()
    return om.get("governance", {}).get(role)


# ── Health / introspection ────────────────────────────────────────────────────

def summary() -> dict:
    """Counts + load status for a /function_map/summary probe."""
    om, specs = _load()
    return {
        "org_model_loaded": bool(om),
        "specialists_loaded": bool(specs),
        "org_model_path": str(_ORG_MODEL_PATH),
        "specialists_path": str(_SPECIALISTS_PATH),
        "org_model_version": om.get("version"),
        "advisor_domains": len([d for d in om.get("advisor_domain_map", {}) if d != "direct_advisors"]),
        "specialists": len(specs),
        "routing_rules": list(om.get("routing_rules", {}).keys()),
        "gate_policies": list(om.get("gate_policies", {}).keys()),
        "bug_first_receiver": get_first_receiver_for_bug(),
    }
