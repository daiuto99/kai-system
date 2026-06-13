"""HTTP surface for function_map — shared registry consulted by Slack bot,
scheduler, web, and any orchestration code that needs to pick an advisor /
specialist / bug receiver / gate policy without improvising.
"""
from fastapi import APIRouter, HTTPException

import function_map as fm

router = APIRouter(prefix="/function_map")


@router.get("/summary")
def summary():
    return fm.summary()


@router.get("/advisor_for")
def advisor_for(text: str):
    if not text:
        raise HTTPException(400, "text query param required")
    return fm.get_advisor_for_domain(text)


@router.get("/advisor_domains")
def advisor_domains():
    return {"domains": fm.list_advisor_domains(), "direct_advisors": fm.list_direct_advisors()}


@router.get("/bug/first_receiver")
def bug_first_receiver():
    return {
        "first_receiver": fm.get_first_receiver_for_bug(),
        "escalation_path": fm.get_bug_escalation_path(),
    }


@router.get("/bug/owner")
def bug_owner(category: str):
    owner = fm.get_bug_owner(category)
    if owner is None:
        raise HTTPException(404, f"unknown bug category '{category}'")
    return {"category": category, "owner": owner}


@router.get("/team_assignee/{team}")
def team_assignee(team: str):
    uuid = fm.get_team_assignee(team)
    return {
        "team": team,
        "assignee_uuid": uuid,
        "fallback_to_devops": uuid is None,
    }


@router.get("/team_slack/{team}")
def team_slack(team: str):
    return {"team": team, "channel": fm.get_team_slack_channel(team)}


@router.get("/specialists")
def specialists(director: str | None = None):
    if director:
        return {"director": director, "specialists": fm.get_specialists_by_director(director)}
    return {"specialists": fm.list_specialists()}


@router.get("/specialist/{specialist_id}")
def specialist(specialist_id: str):
    spec = fm.get_specialist(specialist_id)
    if spec is None:
        raise HTTPException(404, f"specialist '{specialist_id}' not found")
    return spec


@router.get("/gate/{gate_id}")
def gate(gate_id: str):
    policy = fm.get_gate_policy(gate_id)
    if policy is None:
        raise HTTPException(404, f"gate '{gate_id}' not found")
    return {"gate": gate_id, "policy": policy}


@router.get("/routing/{rule_id}")
def routing(rule_id: str):
    rule = fm.get_routing_rule(rule_id)
    if rule is None:
        raise HTTPException(404, f"routing rule '{rule_id}' not found")
    return {"rule": rule_id, "policy": rule}


@router.get("/governance/{role}")
def governance(role: str):
    block = fm.get_governance(role)
    if block is None:
        raise HTTPException(404, f"governance role '{role}' not found")
    return {"role": role, "block": block}
