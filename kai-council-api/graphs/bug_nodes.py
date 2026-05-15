import json
import logging
import urllib.request as ur
from datetime import datetime, timezone
from pathlib import Path

from council_config import VAULT_PATH, _slack_token
from persona import load_persona
from router import _run_agentic_loop
from graphs.bug_state import BugState

logger = logging.getLogger(__name__)

SLACK_CHANNEL = "kai-system"
MODEL = "claude-sonnet-4-6"


# ── Slack helper ─────────────────────────────────────────────────────────────

def _slack_post(text: str, thread_ts: str = None) -> str:
    """Post to #kai-system. Returns ts of posted message."""
    token = _slack_token()
    if not token:
        logger.warning("No Slack token — skipping notification")
        return ""
    payload = {"channel": SLACK_CHANNEL, "text": text}
    if thread_ts:
        payload["thread_ts"] = thread_ts
    data = json.dumps(payload).encode()
    req = ur.Request(
        "https://slack.com/api/chat.postMessage",
        data=data,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with ur.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read())
            return result.get("ts", "")
    except Exception as e:
        logger.error(f"Slack post failed: {e}")
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

PROPOSED FIX:
[Specific, scoped change. Include: what to change, where, why it solves the root cause]

RISK:
[What could go wrong if the diagnosis is wrong, and how to catch it]

UNKNOWNS:
[Any gaps in your analysis that couldn't be resolved]
"""

    messages = [{"role": "user", "content": prompt}]
    reply, _, _ = _run_agentic_loop(messages, [], MODEL, system, "support-engineer")

    # Post to Slack
    if iteration == 0:
        ts = _slack_post(
            f":beetle: *Bug Investigation Started*\n*{state['issue_name']}* (Priority: {state['priority']})\n"
            f"Support Engineer is diagnosing... I'll update this thread as the review progresses."
        )
    else:
        ts = state.get("slack_thread_ts", "")
        _slack_post(f":arrows_counterclockwise: *Revised diagnosis (iteration {iteration + 1})*\n{reply[:400]}…", thread_ts=ts)

    return {
        **state,
        "diagnosis": reply,
        "proposed_fix": reply,
        "status": "peer_review",
        "iteration": iteration + 1,
        "slack_thread_ts": ts or state.get("slack_thread_ts", ""),
        "audit_log": _audit(state, "support_diagnosis", "diagnosed", iteration=iteration + 1),
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
    reply, _, _ = _run_agentic_loop(messages, [], MODEL, system, "lse")

    approved = "DECISION: APPROVE" in reply.upper() or reply.upper().startswith("APPROVE")

    _slack_post(
        f":white_check_mark: *LSE Review*: {'Approved' if approved else ':x: Rejected'}\n{reply[:300]}…",
        thread_ts=state.get("slack_thread_ts"),
    )

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
    reply, _, _ = _run_agentic_loop(messages, [], MODEL, system, "architect")

    approved = "DECISION: APPROVE" in reply.upper() or reply.upper().startswith("APPROVE")

    _slack_post(
        f":triangular_ruler: *Architect Review*: {'Approved' if approved else ':x: Rejected'}\n{reply[:300]}…",
        thread_ts=state.get("slack_thread_ts"),
    )

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
    reply, _, _ = _run_agentic_loop(messages, [], MODEL, system, "kai")

    approved = "DECISION: ESCALATE" in reply.upper()
    return_notes = reply if not approved else ""

    _slack_post(
        f":robot_face: *KAI Validation*: {'Escalating to Leo' if approved else 'Returning for revision'}\n{reply[:300]}…",
        thread_ts=state.get("slack_thread_ts"),
    )

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
    lse_icon = ":white_check_mark:" if state.get("lse_approved") else ":x:"
    arch_icon = ":white_check_mark:" if state.get("architect_approved") else ":x:"

    # Extract just the diagnosis block for the summary
    diag = state.get("diagnosis", "")
    diag_preview = diag[:600] + "…" if len(diag) > 600 else diag

    msg = f""":loudspeaker: *Bug Fix Ready for Your Approval*

*Bug:* {state['issue_name']}
*Priority:* {state['priority']}

*Root Cause & Proposed Fix:*
{diag_preview}

*Peer Review:*
{lse_icon} LSE: {"Approved" if state.get('lse_approved') else "Rejected with notes"}
{arch_icon} Architect: {"Approved" if state.get('architect_approved') else "Rejected with notes"}

*KAI Assessment:*
{state.get('kai_assessment', '')[:300]}

Reply with *approve* or *reject* to proceed."""

    _slack_post(msg, thread_ts=state.get("slack_thread_ts"))

    return {
        **state,
        "status": "awaiting_leo",
        "audit_log": _audit(state, "leo_notify", "notified"),
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
