"""Council gate endpoint — receives workflow gates from kai-orchestrator,
processes them async, and POSTs resolution back to the callback URL.

Gate types:
  plan_gate       — Leo approves the plan upfront via Slack before work begins
  dev_gate        — LSE reviews brief → KAI quality-checks → Leo approves via Slack
  creative_gate   — Creative Director produces brief → KAI validates (loop max 3x) → Leo approves brief once → built
  devops_gate     — DevOps reviews infra implications, auto-approves routine items

Flow for plan/dev/creative gates:
  1. Orchestrator POSTs to /council/gate
  2. Council processes in background (director review + KAI quality check)
  3. Council posts to Slack (#kai-system) with summary + approve/reject instructions
  4. Gate enters pending_leo state — workflow pauses
  5. Leo replies in Slack → /council/gate/{gate_id}/resolve fires callback
  6. Orchestrator resumes workflow

NOTE: _process_gate is intentionally sync (not async def) so FastAPI's BackgroundTasks
runs it in a thread pool, preventing LangGraph graph.invoke() from blocking asyncio.
"""
import json
import logging
import os
from pathlib import Path
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel
import function_map as fm

logger = logging.getLogger(__name__)
router = APIRouter()

_GATES_STORE: dict[str, dict] = {}
_VAULT_GATES = Path("/vault/00_System/gates")
_VAULT_REFERENCES = Path("/vault/60_Council/creative/references")
_BUILD_PROFILES = {
    "creative": Path("/vault/60_Council/creative/BUILD_PROFILE.md"),
    "dev":      Path("/vault/60_Council/dev/BUILD_PROFILE.md"),
}


def _load_references(property_name: str) -> str:
    """Load relevant reference files from vault for a given property.

    Returns a formatted string for injection into the Creative Director prompt.
    Filters by property (matches 'all' or the specific property name).
    """
    if not _VAULT_REFERENCES.exists():
        return ""
    refs = []
    for ref_file in sorted(_VAULT_REFERENCES.glob("*.md")):
        if ref_file.name == "README.md":
            continue
        try:
            text = ref_file.read_text()
            # Parse frontmatter
            prop = ""
            url = ""
            relevance = ""
            category = ""
            for line in text.splitlines():
                if line.startswith("url:"):
                    url = line.split("url:", 1)[1].strip()
                elif line.startswith("property:"):
                    prop = line.split("property:", 1)[1].strip()
                elif line.startswith("relevance:"):
                    relevance = line.split("relevance:", 1)[1].strip()
                elif line.startswith("category:"):
                    category = line.split("category:", 1)[1].strip()
            # Filter by property
            prop_match = prop == "all" or not property_name or property_name.lower() in prop.lower()
            if prop_match and url and relevance:
                refs.append(f"- [{category}] IMAGE: {url}\n  WHY: {relevance}")
        except Exception:
            continue
    if not refs:
        return ""
    return "REFERENCE LIBRARY (curated by Leo — use these as mood board sources):\n" + "\n".join(refs)


class GateRequest(BaseModel):
    gate_id:      str
    gate_type:    str = "dev_gate"
    brief:        dict
    callback_url: str


class GateResolve(BaseModel):
    approved: bool
    notes:    str = ""
    resolver: str = "leo"


# ── Slack helpers ─────────────────────────────────────────────────────────────

def _slack_token() -> str:
    p = Path("/run/secrets/slack_bot_token")
    return p.read_text().strip() if p.exists() else os.environ.get("SLACK_BOT_TOKEN", "")


def _slack_post(channel: str, text: str, blocks: list = None, attachments: list = None) -> str:
    token = _slack_token()
    if not token:
        logger.warning("No Slack token — gate notification skipped")
        return ""
    payload = {"channel": channel, "text": text}
    if blocks:
        payload["blocks"] = blocks
    if attachments:
        payload["attachments"] = attachments
    try:
        r = httpx.post(
            "https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
            timeout=10,
        )
        data = r.json()
        if data.get("ok"):
            return data.get("ts", "")
        logger.warning("Slack post failed: %s", data.get("error"))
    except Exception as e:
        logger.exception("Slack post error: %s", e)
    return ""


def _extract_image_urls(text: str) -> list[str]:
    """Parse IMAGE: <url> lines from a creative brief. Returns list of valid https URLs."""
    urls = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.upper().startswith("IMAGE:"):
            url = stripped[6:].strip()
            if url.startswith("https://") and len(url) > 12:
                urls.append(url)
    return urls[:5]  # Slack image blocks cap at 5 to keep message readable


def _extract_verdict(text: str, fallback: str = "see artifact") -> str:
    """Pull a one-line `VERDICT: <line>` from an advisor response.

    Advisors are prompted to put their headline verdict on a line starting with
    `VERDICT:`. Returns just the line content (no prefix). If absent, returns
    the fallback string — the gate review still produces a valid Slack message,
    and a warning is logged so prompt drift is visible.
    """
    if not text:
        return fallback
    for raw in text.splitlines():
        line = raw.strip()
        if line.upper().startswith("VERDICT:"):
            return line.split(":", 1)[1].strip() or fallback
    logger.warning("No VERDICT: line in advisor response (len=%d) — using fallback", len(text))
    return fallback


def _latest_creative_brief(gate_id: str) -> str:
    """Return text of the highest-numbered director iteration artifact, or ""."""
    d = _gate_dir(gate_id)
    if not d.exists():
        return ""
    iters = sorted(d.glob("director_iter_*.md"))
    if not iters:
        return ""
    try:
        return iters[-1].read_text()
    except Exception:
        return ""


def _gate_slack_message(gate_id: str, gate_type: str, summary: str, kai_assessment: str, status: str) -> tuple[list, list]:
    """Build the short Slack message for a gate awaiting Leo.

    Slack is a pointer, not a payload: subject + chain status + artifact dir +
    decision commands. Full content lives in vault/00_System/gates/{gate_id}/.
    Nothing here truncates because nothing here is long by design.
    """
    gate_label = {
        "plan_gate":      "Plan Approval",
        "dev_gate":       "Dev Review",
        "creative_gate":  "Creative Review",
        "devops_gate":    "DevOps Review",
    }.get(gate_type, gate_type)
    icon = {"plan_gate": "📋", "dev_gate": "⚙️", "creative_gate": "🎨", "devops_gate": "🔧"}.get(gate_type, "🔒")

    artifact_path = f"vault/00_System/gates/{gate_id}/"

    body = (
        f"*Gate ID:* `{gate_id}` · *Status:* {status}\n"
        f"{summary}\n"
        f"*Artifacts:* `{artifact_path}`"
    )

    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": f"{icon} Gate: {gate_label}"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": body}},
        {"type": "section", "text": {"type": "mrkdwn",
            "text": f"• `approve {gate_id}` — approve\n• `reject {gate_id}: [reason]` — reject"}},
    ]

    # Mood-board images as attachments (creative gates only). Pull from the
    # persisted director iteration artifact, not the Slack summary.
    attachments = []
    if gate_type == "creative_gate":
        latest = _latest_creative_brief(gate_id)
        for i, url in enumerate(_extract_image_urls(latest), 1):
            attachments.append({
                "fallback": f"Mood board {i}",
                "image_url": url,
                "color": "#C9A84C",
            })

    return blocks, attachments


# ── API endpoints ─────────────────────────────────────────────────────────────

@router.post("/council/gate")
async def receive_gate(req: GateRequest, background_tasks: BackgroundTasks):
    """Accept a gate from the orchestrator and schedule async processing."""
    _GATES_STORE[req.gate_id] = {
        "gate_id":      req.gate_id,
        "gate_type":    req.gate_type,
        "brief":        req.brief,
        "status":       "processing",
        "resolution":   None,
        "callback_url": req.callback_url,
        "created_at":   datetime.now(timezone.utc).isoformat(),
    }
    background_tasks.add_task(_process_gate, req)
    return {"gate_id": req.gate_id, "status": "accepted"}


@router.get("/council/gate/{gate_id}/state")
def gate_state(gate_id: str):
    """Fallback poll — orchestrator checks here every 30s if callback was missed."""
    entry = _GATES_STORE.get(gate_id)
    if entry is None:
        return {"error": "gate not found"}
    return {
        "gate_id":        gate_id,
        "status":         entry["status"],
        "resolution":     entry["resolution"],
        "summary":        entry.get("summary"),
        "kai_assessment": entry.get("kai_assessment"),
    }


@router.post("/council/gate/{gate_id}/resolve")
def resolve_gate(gate_id: str, req: GateResolve):
    """Resolve a pending gate — called when Leo approves or rejects via Slack."""
    entry = _GATES_STORE.get(gate_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Gate {gate_id} not found")
    if entry["status"] not in ("pending_leo", "processing"):
        raise HTTPException(status_code=409, detail=f"Gate {gate_id} already in state {entry['status']}")

    resolution = {
        "approved":   req.approved,
        "notes":      req.notes,
        "advisor":    req.resolver,
        "resolved_at": datetime.now(timezone.utc).isoformat(),
    }
    entry["status"]     = "resolved"
    entry["resolution"] = resolution

    _persist_gate_record(gate_id, entry["gate_type"], entry["brief"], resolution)
    _fire_callback(entry["callback_url"], resolution)

    action = "approved" if req.approved else "rejected"
    _slack_post("#devops", f"Gate `{gate_id}` {action} by {req.resolver}. {req.notes}")
    logger.info("Gate %s %s by %s", gate_id, action, req.resolver)

    # Learning capture — log Leo's decision and notes as an insight
    _capture_gate_learning(gate_id, entry["gate_type"], req.approved, req.notes, req.resolver)

    return {"gate_id": gate_id, "status": "resolved", "approved": req.approved}


def _capture_gate_learning(gate_id: str, gate_type: str, approved: bool, notes: str, resolver: str):
    """Capture Leo's gate decision. For creative gates, run taste distillation into BUILD_PROFILE."""
    try:
        from pathlib import Path as _Path
        insights_file = _Path("/vault/60_Council/learning/gate_feedback.md")
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        action = "APPROVED" if approved else "REJECTED"
        entry_text = f"\n## [{ts}] Gate {action} — {gate_type}\n"
        entry_text += f"Gate ID: `{gate_id}`\n"
        entry_text += f"Resolver: {resolver}\n"
        if notes:
            entry_text += f"Notes: {notes}\n"
        existing = insights_file.read_text() if insights_file.exists() else "# Gate Feedback Log\n"
        insights_file.write_text(existing + entry_text)
        logger.info("Gate learning captured for %s", gate_id)

        # Creative gate distillation — extract taste rules into BUILD_PROFILE
        if gate_type == "creative_gate":
            _distill_creative_taste(gate_id, approved, notes)
    except Exception as e:
        logger.warning("Gate learning capture failed: %s", e)


def _distill_creative_taste(gate_id: str, approved: bool, notes: str):
    """Extract 1-3 taste rules from Leo's creative gate decision and append to BUILD_PROFILE."""
    try:
        gate_entry = _GATES_STORE.get(gate_id, {})
        approved_brief = gate_entry.get("approved_brief", "")
        brief = gate_entry.get("brief", {})
        if not approved_brief:
            logger.info("Distillation skipped for %s — no approved_brief stored", gate_id)
            return

        action = "APPROVED" if approved else "REJECTED"
        notes_section = f"Leo's notes: {notes}" if notes else "No additional notes."

        from graphs.graph import get_graph
        graph = get_graph()
        message = (
            f"[Creative Taste Distillation — {action}]\n\n"
            f"Leo just {action.lower()} a creative brief for: {brief.get('title', 'unknown')}\n"
            f"{notes_section}\n\n"
            f"The brief that was {'approved' if approved else 'rejected'}:\n{approved_brief}\n\n"
            "Extract 1-3 concrete taste rules from this decision. Rules must be:\n"
            "- Written as imperative sentences (e.g. 'Use editorial serif typefaces — no grotesque defaults')\n"
            "- Specific — not vague principles\n"
            "- Tied to what Leo accepted or rejected, not general advice\n"
            "- Scoped to the property if property-specific, or general if broadly applicable\n\n"
            "Format your response as:\n"
            "RULE: <rule text> [property: <name> or general]\n"
            "(one RULE: line per rule, 1-3 rules total)\n\n"
            "Nothing else — just the RULE lines."
        )
        state = {
            "channel": "kai", "message": message, "user_id": "gate-engine",
            "thread_ts": f"distill-{gate_id[:8]}", "attachments": [], "privacy_mode": False,
            "history": [], "target_advisor": "kai", "routing_reason": "taste distillation",
            "advisor_reply": "", "final_reply": "", "model_used": "",
            "input_tokens": 0, "output_tokens": 0, "audit_log": [],
        }
        result = graph.invoke(state, config={"configurable": {"thread_id": f"distill-{gate_id[:8]}"}})
        rules_text = result.get("final_reply", "").strip()

        if not rules_text or "RULE:" not in rules_text:
            logger.info("Distillation produced no rules for %s", gate_id)
            return

        # Append rules to BUILD_PROFILE under Compiled Taste Notes
        build_profile_path = _BUILD_PROFILES["creative"]
        if not build_profile_path.exists():
            logger.warning("BUILD_PROFILE not found — distillation rules not saved")
            return

        profile = build_profile_path.read_text()
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        new_entries = f"\n### {ts} — Gate `{gate_id[:8]}` ({action})\n"
        for line in rules_text.splitlines():
            if line.strip().startswith("RULE:"):
                rule = line.strip()[5:].strip()
                new_entries += f"- {rule}\n"

        if "*(No entries yet" in profile:
            profile = profile.replace("*(No entries yet — populated after first creative gate approve/reject cycle)*", new_entries.strip())
        else:
            profile = profile + "\n" + new_entries

        build_profile_path.write_text(profile)
        logger.info("Taste distillation written to BUILD_PROFILE for gate %s", gate_id)

    except Exception as e:
        logger.warning("Creative taste distillation failed for %s: %s", gate_id, e)


# ── Gate processing ───────────────────────────────────────────────────────────

def _process_gate(req: GateRequest):
    """Run the gate chain (sync, thread pool) then notify Leo via Slack."""
    try:
        gate_type = req.gate_type
        brief     = req.brief

        if gate_type == "plan_gate":
            summary, kai_assessment = _plan_gate_review(brief, req.gate_id)
        elif gate_type == "dev_gate":
            summary, kai_assessment = _dev_gate_review(brief, req.gate_id)
        elif gate_type == "creative_gate":
            summary, kai_assessment = _creative_gate_review(brief, req.gate_id)
        elif gate_type == "devops_gate":
            summary, kai_assessment = _devops_gate_review(brief, req.gate_id)
            # Auto-approve when the verdict's first token is ROUTINE.
            # First-token check (not substring) so a body containing the word
            # STRUCTURAL inside a ROUTINE verdict doesn't falsely escalate.
            first_token = (kai_assessment.split() or [""])[0].rstrip(",.;:—-").upper()
            if first_token == "ROUTINE":
                resolution = {"approved": True, "notes": kai_assessment, "advisor": "devops"}
                _GATES_STORE[req.gate_id]["status"]     = "resolved"
                _GATES_STORE[req.gate_id]["resolution"] = resolution
                _persist_gate_record(req.gate_id, gate_type, brief, resolution)
                _fire_callback(req.callback_url, resolution)
                return
        else:
            logger.warning("Unknown gate_type %r — notifying Leo", gate_type)
            _persist_artifact(req.gate_id, "brief", json.dumps(brief, indent=2))
            summary        = f"*Subject:* (unknown gate type `{gate_type}`)\n*Chain:* none — Leo must decide"
            kai_assessment = f"Unknown gate type — see brief.md"  # noqa: F541

        # Move to pending_leo: post to Slack, wait for Leo's response
        _GATES_STORE[req.gate_id]["status"]         = "pending_leo"
        _GATES_STORE[req.gate_id]["summary"]        = summary
        _GATES_STORE[req.gate_id]["kai_assessment"] = kai_assessment
        blocks, attachments = _gate_slack_message(req.gate_id, gate_type, summary, kai_assessment, "Awaiting Leo's approval")
        fallback = f"Gate {req.gate_id} ({gate_type}) needs your approval. Reply `approve {req.gate_id}` or `reject {req.gate_id}: reason`"
        _slack_post("#devops", fallback, blocks, attachments)
        logger.info("Gate %s posted to Slack — awaiting Leo", req.gate_id)

    except Exception as e:
        logger.exception("Gate processing failed for %s", req.gate_id)
        _GATES_STORE[req.gate_id]["status"] = "error"
        _fire_callback(req.callback_url, {
            "approved": False,
            "notes":    f"Gate processing error: {e}",
            "advisor":  "system",
        })


def _plan_gate_review(brief: dict, gate_id: str) -> tuple[str, str]:
    """Plan gate (1-hop chain) — KAI reviews the plan against Leo's direction.

    Persists brief.md and kai_verdict.md. Returns a short Slack summary +
    one-line verdict. Full content lives in the gate's artifact directory.
    """
    _persist_artifact(gate_id, "brief", json.dumps(brief, indent=2))
    job_name = brief.get("workflow", brief.get("job_id", "Unknown workflow"))

    kai_full = _kai_quality_check(
        "plan", brief,
        "Review this plan for completeness, clarity, and alignment with Leo's direction. "
        "Is it clear what will be built? Are the steps logical? Are there obvious gaps?"
    )
    _persist_artifact(gate_id, "kai_verdict", kai_full)
    kai_line = _extract_verdict(kai_full, fallback="see kai_verdict.md")

    summary = f"*Subject:* {job_name}\n*Chain:* KAI plan-review — {kai_line}"
    return summary, kai_line


def _dev_gate_review(brief: dict, gate_id: str) -> tuple[str, str]:
    """Dev gate (2-hop chain) — LSE engineering review → KAI quality check."""
    _persist_artifact(gate_id, "brief", json.dumps(brief, indent=2))
    build_profile = _BUILD_PROFILES["dev"].read_text() if _BUILD_PROFILES["dev"].exists() else ""
    brief_text = json.dumps(brief, indent=2)
    job_name = brief.get("workflow", brief.get("title", brief.get("job_id", "Engineering work")))

    lse_full = _call_advisor("dev",
        f"[LSE Sign-Off Required]\n\nBuild Profile Standards:\n{build_profile}\n\n"
        f"Engineering Brief:\n{brief_text}\n\n"
        "Review this brief against the build profile. Does it meet Leo's engineering standards? "
        "What is your assessment and sign-off? Be specific about what was checked.\n\n"
        "RESPONSE FORMAT — first line MUST be:\n"
        "VERDICT: <SIGNED-OFF | CONCERNS | REJECTED> — one-sentence headline\n"
        "Then the full review on subsequent lines.",
        gate_id
    )
    _persist_artifact(gate_id, "lse_review", lse_full)
    lse_line = _extract_verdict(lse_full, fallback="see lse_review.md")

    kai_full = _kai_quality_check(
        "dev", brief,
        f"LSE has reviewed and signed off:\n{lse_full}\n\n"
        "Does this engineering work meet Leo's standards? Is it scoped correctly? "
        "Is the approach sound? Would Leo approve this?"
    )
    _persist_artifact(gate_id, "kai_verdict", kai_full)
    kai_line = _extract_verdict(kai_full, fallback="see kai_verdict.md")

    summary = f"*Subject:* {job_name}\n*Chain:* LSE — {lse_line} · KAI — {kai_line}"
    return summary, kai_line


_BRIEF_REQUIRED_SECTIONS = [
    "style direction",
    "color palette",
    "typography",
    "content voice",
    "mood board",
]

def _kai_validate_brief(produced_brief: str) -> tuple[bool, str]:
    """KAI checks whether the Creative Director's brief meets all 5 required sections.

    Returns (approved: bool, feedback: str).
    approved=True only when all sections are present and specific.
    """
    try:
        from graphs.graph import get_graph
        graph = get_graph()
        message = (
            "[KAI Brief Validation]\n\n"
            "You are reviewing a creative brief produced by the Creative Director.\n"
            "The brief MUST contain all 5 of these sections, each specific and non-vague:\n"
            "1. Style Direction — 2-3 specific sentences, not generic phrases\n"
            "2. Color Palette — exact hex values with usage notes for each color\n"
            "3. Typography — specific named fonts with hierarchy rationale\n"
            "4. Content Voice — actual example sentences (not descriptions of tone)\n"
            "5. Mood Board — 3-5 references each with a one-line WHY note\n\n"
            "Additional rejection criteria:\n"
            "- Reject if any section is missing\n"
            "- Reject if color palette has no hex values\n"
            "- Reject if typography names only font categories (not specific fonts)\n"
            "- Reject if content voice only describes tone without example sentences\n"
            "- Reject if mood board references have no WHY notes\n"
            "- Reject if brief contradicts BUILD_PROFILE for the property\n\n"
            f"Brief to review:\n{produced_brief}\n\n"
            "Reply with APPROVED if all 5 sections pass all criteria.\n"
            "Reply with REJECTED: <specific list of what is missing or weak> if not.\n"
            "Be strict. Leo relies on this check so he only sees briefs that are ready."
        )
        state = {
            "channel": "kai", "message": message, "user_id": "gate-engine",
            "thread_ts": "kai-brief-validation", "attachments": [], "privacy_mode": False,
            "history": [], "target_advisor": "kai", "routing_reason": "brief validation",
            "advisor_reply": "", "final_reply": "", "model_used": "",
            "input_tokens": 0, "output_tokens": 0, "audit_log": [],
        }
        result = graph.invoke(state, config={"configurable": {"thread_id": "kai-brief-validation"}})
        reply = result.get("final_reply", "")
        approved = reply.strip().upper().startswith("APPROVED")
        return approved, reply
    except Exception as e:
        logger.exception("KAI brief validation failed: %s", e)
        return False, f"[Brief validation unavailable: {e}]"


def _creative_gate_review(brief: dict, gate_id: str) -> tuple[str, str]:
    """Brief-first creative gate flow.

    Creative Director produces a 5-section brief. KAI validates it internally
    (loop max 3 iterations). Only when KAI approves does the brief surface to Leo.
    Leo approves once — then it gets built.
    """
    build_profile = _BUILD_PROFILES["creative"].read_text() if _BUILD_PROFILES["creative"].exists() else ""
    direction = brief.get("direction", json.dumps(brief, indent=2))
    title = brief.get("title", brief.get("project", "Creative request"))
    property_name = brief.get("property", brief.get("project", ""))

    # KAI-394: load curated references from vault for this property
    reference_library = _load_references(property_name)

    iteration_log = []
    produced_brief = ""
    kai_feedback = ""
    approved = False

    for iteration in range(1, 4):
        feedback_section = (
            f"\n\nKAI FEEDBACK FROM PREVIOUS ATTEMPT (iteration {iteration - 1}):\n{kai_feedback}\n"
            "Address every point above before resubmitting."
            if iteration > 1 else ""
        )

        produced_brief = _call_advisor("creative",
            f"[Creative Brief — Iteration {iteration}/3]\n\n"
            f"BUILD PROFILE:\n{build_profile}\n\n"
            + (f"{reference_library}\n\n" if reference_library else "")
            + f"LEO'S DIRECTION:\n{direction}\n\n"
            f"PROPERTY: {property_name}\n"
            f"{feedback_section}\n"
            "Produce a structured creative brief with ALL 5 required sections:\n\n"
            "## Style Direction\n(2-3 specific sentences — what it looks like, feels like, and why)\n\n"
            "## Color Palette\n(3-5 colors with exact hex values and usage notes)\n\n"
            "## Typography\n(primary + secondary font — specific names, not categories — with hierarchy rationale)\n\n"
            "## Content Voice\n(2-3 example sentences written in the actual voice — not descriptions of tone)\n\n"
            "## Mood Board\n"
            "(3-5 references. For EACH reference you MUST include:\n"
            "  - A description of what it is\n"
            "  - IMAGE: <direct image URL> on its own line\n"
            "  - WHY: one sentence on what specifically applies to this brief\n"
            "Draw from the REFERENCE LIBRARY above first. You may add additional references if needed.)\n\n"
            "Do not include execution plans, wireframes, or design specs. Brief only.",
            gate_id
        )

        _persist_artifact(gate_id, f"director_iter_{iteration}", produced_brief)

        approved, kai_feedback = _kai_validate_brief(produced_brief)
        _persist_artifact(gate_id, f"kai_validation_iter_{iteration}", kai_feedback)
        iteration_log.append({
            "iteration": iteration,
            "approved": approved,
            "feedback": kai_feedback[:500],
        })

        logger.info("Gate %s brief iteration %d — approved=%s", gate_id, iteration, approved)

        if approved:
            break

    _persist_artifact(gate_id, "iteration_log", json.dumps(iteration_log, indent=2))

    if not approved:
        verdict = f"ESCALATE — Director failed KAI validation after {len(iteration_log)} iterations"
        summary = (
            f"*Subject:* {title}\n"
            f"*Chain:* Director (×{len(iteration_log)}) → KAI — {verdict}"
        )
    else:
        verdict = f"APPROVED — Brief approved by KAI on iteration {len(iteration_log)}"
        summary = (
            f"*Subject:* {title}\n"
            f"*Chain:* Director (×{len(iteration_log)}) → KAI — {verdict}"
        )

    # Store the approved brief on the gate record for use at resolution
    _GATES_STORE[gate_id]["approved_brief"] = produced_brief

    return summary, verdict


def _devops_gate_review(brief: dict, gate_id: str) -> tuple[str, str]:
    """DevOps gate (1-hop chain) — DevOps reviews infrastructure implications.

    Single-advisor by design: DevOps is the authoritative voice on infra.
    Auto-approves routine work; escalates anything whose verdict contains
    STRUCTURAL or REJECTED.
    """
    _persist_artifact(gate_id, "brief", json.dumps(brief, indent=2))
    brief_text = json.dumps(brief, indent=2)
    job_name = brief.get("workflow", brief.get("title", "Infrastructure change"))

    devops_full = _call_advisor("devops",
        f"[DevOps Infrastructure Review]\n{brief_text}\n\n"
        "Review the infrastructure implications. Is this routine or structural? "
        "Any risks, dependencies, or architectural concerns?\n\n"
        "RESPONSE FORMAT — first line MUST be:\n"
        "VERDICT: <ROUTINE | STRUCTURAL | REJECTED> — one-sentence headline\n"
        "Then the full review on subsequent lines. "
        "Use STRUCTURAL when this needs Leo's approval.",
        gate_id
    )
    _persist_artifact(gate_id, "devops_review", devops_full)
    devops_line = _extract_verdict(devops_full, fallback="see devops_review.md")

    summary = f"*Subject:* {job_name}\n*Chain:* DevOps — {devops_line}"
    return summary, devops_line


# ── Advisor + KAI calls ───────────────────────────────────────────────────────

def _call_advisor(advisor: str, message: str, thread_id: str) -> str:
    """Call a council advisor via the graph and return the reply."""
    try:
        from graphs.graph import get_graph
        graph = get_graph()
        state = {
            "channel":        advisor,
            "message":        message,
            "user_id":        "gate-engine",
            "thread_ts":      thread_id,
            "attachments":    [],
            "privacy_mode":   False,
            "history":        [],
            "target_advisor": advisor,
            "routing_reason": f"gate engine → {advisor}",
            "advisor_reply":  "",
            "final_reply":    "",
            "model_used":     "",
            "input_tokens":   0,
            "output_tokens":  0,
            "audit_log":      [],
        }
        result = graph.invoke(state, config={"configurable": {"thread_id": thread_id or advisor}})
        return result.get("final_reply", "")
    except Exception as e:
        logger.exception("Advisor call failed for %s: %s", advisor, e)
        return f"[{advisor} unavailable: {e}]"


def _kai_quality_check(check_type: str, brief: dict, instruction: str) -> str:
    """KAI reviews the work against Leo's direction and system standards."""
    try:
        from graphs.graph import get_graph
        graph = get_graph()
        gate_policy = fm.get_gate_policy(f"{check_type}_gate") or {}
        standards = json.dumps(gate_policy, indent=2) if gate_policy else ""

        message = (
            f"[KAI Quality Check — {check_type}]\n\n"
            f"Gate standards:\n{standards}\n\n"
            f"Brief:\n{json.dumps(brief, indent=2)}\n\n"
            f"{instruction}\n\n"
            "RESPONSE FORMAT — first line MUST be:\n"
            "VERDICT: <READY | NOT READY | CONCERNS> — one-sentence headline\n"
            "Then the full assessment on subsequent lines."
        )
        state = {
            "channel":        "kai",
            "message":        message,
            "user_id":        "gate-engine",
            "thread_ts":      f"kai-qc-{check_type}",
            "attachments":    [],
            "privacy_mode":   False,
            "history":        [],
            "target_advisor": "kai",
            "routing_reason": "gate quality check",
            "advisor_reply":  "",
            "final_reply":    "",
            "model_used":     "",
            "input_tokens":   0,
            "output_tokens":  0,
            "audit_log":      [],
        }
        result = graph.invoke(state, config={"configurable": {"thread_id": f"kai-qc-{check_type}"}})
        return result.get("final_reply", "Quality check unavailable")
    except Exception as e:
        logger.exception("KAI quality check failed: %s", e)
        return f"[KAI quality check unavailable: {e}]"


# ── Persistence ───────────────────────────────────────────────────────────────

def _gate_dir(gate_id: str) -> Path:
    """Per-gate artifact directory under the vault."""
    return _VAULT_GATES / gate_id


def _persist_artifact(gate_id: str, artifact_type: str, content: str) -> str:
    """Save a gate artifact to vault/00_System/gates/{gate_id}/{artifact_type}.md.

    Returns the absolute path written, or "" on failure.
    """
    try:
        d = _gate_dir(gate_id)
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{artifact_type}.md"
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        p.write_text(f"# Gate Artifact: {artifact_type}\nGate: {gate_id}\nWritten: {ts}\n\n{content}\n")
        return str(p)
    except Exception:
        logger.exception("Artifact persist failed: %s/%s", gate_id, artifact_type)
        return ""


def _persist_gate_record(gate_id: str, gate_type: str, brief: dict, resolution: dict):
    """Write the gate audit record to vault/00_System/gates/{gate_id}/audit.json."""
    try:
        d = _gate_dir(gate_id)
        d.mkdir(parents=True, exist_ok=True)
        record = {
            "gate_id":     gate_id,
            "gate_type":   gate_type,
            "brief":       brief,
            "resolution":  resolution,
            "resolved_at": datetime.now(timezone.utc).isoformat(),
        }
        (d / "audit.json").write_text(json.dumps(record, indent=2))
    except Exception:
        logger.exception("Gate audit persist failed for %s", gate_id)


def _fire_callback(callback_url: str, resolution: dict):
    """POST resolution back to the orchestrator callback URL."""
    try:
        r = httpx.post(callback_url, json=resolution, timeout=10)
        if r.status_code == 200:
            logger.info("Gate callback OK: %s", callback_url)
        else:
            logger.warning("Gate callback %s returned %d: %s", callback_url, r.status_code, r.text[:200])
    except Exception as e:
        logger.exception("Gate callback failed: %s — %s", callback_url, e)
