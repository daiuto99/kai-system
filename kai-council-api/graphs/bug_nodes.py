import logging
from datetime import datetime, timezone

from council_config import _track_usage
from persona import load_persona
from router import _run_agentic_loop
from graphs.bug_state import BugState

logger = logging.getLogger(__name__)

SLACK_CHANNEL = "devops"
MODEL = "claude-sonnet-4-6"


# ── Slack helper ─────────────────────────────────────────────────────────────

def _slack_post(text: str, thread_ts: str = None) -> str:
    """AR-5.3: rerouted to Telegram (sole surface). Name/signature kept so call
    sites stay unchanged; thread_ts is ignored (Telegram has no threads) and no
    ts is returned."""
    try:
        from tg_alert import tg_alert
        tg_alert(text)
    except Exception as e:
        logger.error(f"tg_alert failed: {e}")
    return ""


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat()


def _audit(state: BugState, node: str, action: str, **kw) -> list:
    entry = {"ts": _ts(), "node": node, "action": action, **kw}
    return list(state.get("audit_log", [])) + [entry]


# ── Node: support_diagnosis ──────────────────────────────────────────────────

def support_diagnosis(state: BugState) -> BugState:
    iteration = state.get("iteration", 0)
    prior_feedback = state.get("prior_feedback", "")

    system = load_persona("support-engineer")

    context = f"""BUG REPORT
Title: {state['issue_name']}
Priority: {state['priority']}
Description:
{state['issue_description']}
"""
    if prior_feedback:
        context += f"""
--- PEER REVIEW FEEDBACK (iteration {iteration}) ---
{prior_feedback}
Please revise your diagnosis and proposed fix based on this feedback.
"""

    prompt = f"""{context}

Provide your full investigation. Use this exact format:

DIAGNOSIS:
[Your root cause analysis — be specific, trace to the actual source]

CONFIDENCE: [High / Medium / Low]
[One sentence explaining your confidence level]

ROUTING: [dev | devops | creative | kai]
[Which team owns this fix: dev=code/logic bugs, devops=infrastructure/container/deployment bugs, creative=content/design bugs, kai=unknown/needs escalation]

RISK_LEVEL: [low | high]
[low=routine fix, can be deployed autonomously after LSE+KAI review. high=structural change or data risk, Leo must approve before deploy]

PROPOSED FIX:
[Specific, scoped change. Include: what to change, where, why it solves the root cause]

RISK:
[What could go wrong if the diagnosis is wrong, and how to catch it]

UNKNOWNS:
[Any gaps in your analysis that couldn't be resolved]
"""

    messages = [{"role": "user", "content": prompt}]
    reply, input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens = _run_agentic_loop(
        messages, [], MODEL, system, "support-engineer"
    )
    _track_usage("support-engineer", input_tokens, output_tokens, "anthropic", MODEL,
                 trigger_source="graph:bug_nodes:support_diagnosis",
                 cache_read_tokens=cache_read_tokens,
                 cache_creation_tokens=cache_creation_tokens)

    # Slack output suppressed — the council runs silently for audit/Plane refinement.
    # The single user-facing Slack message comes from kai-scheduler/triage.py
    # at failure time (KAI-404). leo_notify is also silent (see below).
    ts = state.get("slack_thread_ts", "")

    # Parse routing and risk from support engineer output
    import re as _re
    routing_m   = _re.search(r"ROUTING:\s*(dev|devops|creative|kai)", reply, _re.IGNORECASE)
    risk_m      = _re.search(r"RISK_LEVEL:\s*(low|high)", reply, _re.IGNORECASE)
    bug_routing = routing_m.group(1).lower() if routing_m else "dev"
    risk_level  = risk_m.group(1).lower() if risk_m else "high"

    return {
        **state,
        "diagnosis": reply,
        "proposed_fix": reply,
        "status": "peer_review",
        "bug_routing": bug_routing,
        "risk_level": risk_level,
        "iteration": iteration + 1,
        "slack_thread_ts": ts or state.get("slack_thread_ts", ""),
        "audit_log": _audit(state, "support_diagnosis", "diagnosed",
                            iteration=iteration + 1, routing=bug_routing, risk=risk_level),
    }


# ── Node: lse_review ────────────────────────────────────────────────────────

def lse_review(state: BugState) -> BugState:
    system = load_persona("kai")

    prompt = f"""You are reviewing a bug diagnosis from the Support Engineer. 
Your job: verify the root cause analysis is sound and the proposed fix is appropriate.

BUG: {state['issue_name']}
Description: {state['issue_description']}

--- SUPPORT ENGINEER DIAGNOSIS ---
{state['diagnosis']}

Review this carefully. Use this exact format:

DECISION: [APPROVE / REJECT]

ASSESSMENT:
[Your technical assessment of the diagnosis and fix — 2-4 sentences]

CONCERNS:
[Any issues with the approach, or "None" if you approve cleanly]

If you REJECT, be specific about what needs to change.
"""

    messages = [{"role": "user", "content": prompt}]
    reply, input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens = _run_agentic_loop(
        messages, [], MODEL, system, "lse"
    )
    _track_usage("lse", input_tokens, output_tokens, "anthropic", MODEL,
                 trigger_source="graph:bug_nodes:lse_review",
                 cache_read_tokens=cache_read_tokens,
                 cache_creation_tokens=cache_creation_tokens)

    approved = "DECISION: APPROVE" in reply.upper() or reply.upper().startswith("APPROVE")

    # Slack output suppressed (KAI-404).

    return {
        **state,
        "lse_review": reply,
        "lse_approved": approved,
        "audit_log": _audit(state, "lse_review", "reviewed", approved=approved),
    }


# ── Node: architect_review ───────────────────────────────────────────────────

def architect_review(state: BugState) -> BugState:
    system = load_persona("dev")

    prompt = f"""You are reviewing a bug diagnosis from the Support Engineer.
The LSE has also reviewed — their assessment is included below.

BUG: {state['issue_name']}
Description: {state['issue_description']}

--- SUPPORT ENGINEER DIAGNOSIS ---
{state['diagnosis']}

--- LSE REVIEW ---
{state['lse_review']}

Your job: review the technical approach from an architecture perspective.
Is the proposed fix sound? Does it solve the root cause without introducing new risk?

Use this exact format:

DECISION: [APPROVE / REJECT]

ASSESSMENT:
[Your architectural assessment — 2-4 sentences]

CONCERNS:
[Architectural concerns, or "None" if clean]
"""

    messages = [{"role": "user", "content": prompt}]
    reply, input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens = _run_agentic_loop(
        messages, [], MODEL, system, "architect"
    )
    _track_usage("architect", input_tokens, output_tokens, "anthropic", MODEL,
                 trigger_source="graph:bug_nodes:architect_review",
                 cache_read_tokens=cache_read_tokens,
                 cache_creation_tokens=cache_creation_tokens)

    approved = "DECISION: APPROVE" in reply.upper() or reply.upper().startswith("APPROVE")

    # Slack output suppressed (KAI-404).

    return {
        **state,
        "architect_review": reply,
        "architect_approved": approved,
        "audit_log": _audit(state, "architect_review", "reviewed", approved=approved),
    }


# ── Node: kai_validation ─────────────────────────────────────────────────────

def kai_validation(state: BugState) -> BugState:
    system = load_persona("kai")

    prompt = f"""A bug investigation has completed peer review and needs your validation before escalating to Leo.

BUG: {state['issue_name']}
Priority: {state['priority']}
Description: {state['issue_description']}

--- SUPPORT ENGINEER DIAGNOSIS ---
{state['diagnosis']}

--- LSE REVIEW ({"APPROVED" if state.get('lse_approved') else "REJECTED"}) ---
{state.get('lse_review', 'N/A')}

--- ARCHITECT REVIEW ({"APPROVED" if state.get('architect_approved') else "REJECTED"}) ---
{state.get('architect_review', 'N/A')}

Your job: determine if due diligence was done and this is ready for Leo's approval.
Ask yourself: Is the root cause well-established? Is the proposed fix appropriately scoped? Were concerns properly addressed?

Use this exact format:

DECISION: [ESCALATE / RETURN]

ASSESSMENT:
[Your assessment of the full investigation thread — 2-4 sentences]

If RETURN: what specifically needs to be improved before escalating.
"""

    messages = [{"role": "user", "content": prompt}]
    reply, input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens = _run_agentic_loop(
        messages, [], MODEL, system, "kai"
    )
    _track_usage("kai", input_tokens, output_tokens, "anthropic", MODEL,
                 trigger_source="graph:bug_nodes:kai_validation",
                 cache_read_tokens=cache_read_tokens,
                 cache_creation_tokens=cache_creation_tokens)

    approved = "DECISION: ESCALATE" in reply.upper()
    return_notes = reply if not approved else ""

    # Slack output suppressed (KAI-404).

    return {
        **state,
        "kai_assessment": reply,
        "kai_approved": approved,
        "kai_return_notes": return_notes,
        "status": "awaiting_leo" if approved else "peer_review",
        "audit_log": _audit(state, "kai_validation", "validated", escalate=approved),
    }


# ── Node: leo_notify ─────────────────────────────────────────────────────────

def leo_notify(state: BugState) -> BugState:
    """Council finished refining the diagnosis — record completion in audit log.

    Slack output suppressed (KAI-404). Leo's single Slack message for this bug
    was posted by kai-scheduler/triage.py at failure time. The refined diagnosis
    from the council lives on the Plane ticket; if Leo wants more detail beyond
    the one-liner, he opens the ticket.
    """
    return {
        **state,
        "status": "awaiting_leo",
        "audit_log": _audit(state, "leo_notify", "audit_only_silent"),
    }


# ── Routing decisions ────────────────────────────────────────────────────────

def peer_review_decision(state: BugState) -> str:
    """After both reviews: approved → kai_validation, rejected → support_diagnosis (if iterations < 3)."""
    lse_ok = state.get("lse_approved", False)
    arch_ok = state.get("architect_approved", False)
    if lse_ok and arch_ok:
        return "run_kai_validation"
    if state.get("iteration", 0) >= 3:
        logger.warning(f"Bug {state['issue_id']}: max iterations hit, escalating anyway")
        return "run_kai_validation"
    # Build feedback for next iteration
    feedback_parts = []
    if not lse_ok:
        feedback_parts.append(f"LSE: {state.get('lse_review', '')}")
    if not arch_ok:
        feedback_parts.append(f"Architect: {state.get('architect_review', '')}")
    state["prior_feedback"] = "\n\n".join(feedback_parts)
    return "run_support_diagnosis"


def kai_decision(state: BugState) -> str:
    """After KAI validation: escalate → leo_notify, return → support_diagnosis."""
    if state.get("kai_approved"):
        return "run_leo_notify"
    if state.get("iteration", 0) >= 3:
        return "run_leo_notify"
    state["prior_feedback"] = state.get("kai_return_notes", "")
    return "run_support_diagnosis"
