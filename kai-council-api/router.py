import json
import logging
import re
from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel
import httpx
from council_config import ADVISOR_CHANNELS, WORKER_URL, _track_usage
from complexity import _classify_complexity, _get_advisor_config
from persona import load_persona
from history import _append_history
from insights import extract_and_strip_insights, append_insights_to_vault
from knowledge_layer import _auto_summarize
from execute_tool import execute_tool
from providers import get_anthropic_client, _call_ollama, _call_openai

logger = logging.getLogger(__name__)
router = APIRouter()


class MessageRequest(BaseModel):
    channel: str
    message: str
    user_id: str = ""
    history: list = []
    thread_ts: str = ""


class ContextUpdateRequest(BaseModel):
    advisor: str
    content: str


class CouncilResponse(BaseModel):
    reply: str
    advisor: str
    model: str
    usage: dict


KAI_TOOLS = [
    {
        "name": "save_workflow",
        "description": "Save or update a command button/workflow that appears in the dashboard functions bar. Use this when Leo asks you to define, create, update, or configure a workflow, command, or function button.",
        "input_schema": {
            "type": "object",
            "properties": {
                "id":          {"type": "string",  "description": "Unique slug (lowercase, hyphens ok). Use existing ID to update."},
                "label":       {"type": "string",  "description": "Short button label shown in the UI (2-4 words max)"},
                "prompt":      {"type": "string",  "description": "The full prompt text that gets sent when the button is clicked"},
                "send":        {"type": "boolean", "description": "True = send immediately on click. False = pre-fill the input box for editing before sending."},
                "description": {"type": "string",  "description": "One-line description of what this workflow does"}
            },
            "required": ["id", "label", "prompt", "send"]
        }
    },
    {"name": "list_workflows", "description": "List all currently saved command workflows/buttons.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "delete_workflow", "description": "Delete a workflow button by ID.", "input_schema": {"type": "object", "properties": {"id": {"type": "string", "description": "The workflow ID to delete"}}, "required": ["id"]}},
    {"name": "create_task", "description": "Create a task in Todoist.", "input_schema": {"type": "object", "properties": {"content": {"type": "string"}, "due_date": {"type": "string"}, "priority": {"type": "integer"}, "description": {"type": "string"}}, "required": ["content"]}},
    {"name": "create_project", "description": "Create a new project in KAI.", "input_schema": {"type": "object", "properties": {"id": {"type": "string"}, "name": {"type": "string"}, "description": {"type": "string"}, "status": {"type": "string", "enum": ["green","yellow","red"]}, "next": {"type": "string"}, "advisor": {"type": "string"}}, "required": ["id", "name", "description", "status"]}},
    {"name": "update_project", "description": "Update a project's status, next action, or milestone.", "input_schema": {"type": "object", "properties": {"id": {"type": "string"}, "status": {"type": "string"}, "next": {"type": "string"}, "milestone": {"type": "string"}, "milestone_pct": {"type": "integer"}}, "required": ["id"]}},
    {"name": "list_projects", "description": "List all current projects with their status.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "write_to_vault", "description": "Write a document to the vault.", "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}, "description": {"type": "string"}}, "required": ["path", "content"]}},
    {"name": "read_vault", "description": "Read a file from the vault.", "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
    {"name": "send_slack_message", "description": "Post a message to a Slack channel.", "input_schema": {"type": "object", "properties": {"channel": {"type": "string"}, "message": {"type": "string"}, "advisor": {"type": "string"}}, "required": ["channel", "message"]}},
    {"name": "start_mission", "description": "Record the start of an autonomous mission.", "input_schema": {"type": "object", "properties": {"name": {"type": "string"}, "scope": {"type": "array", "items": {"type": "string"}}, "notes": {"type": "string"}}, "required": ["name", "scope"]}},
    {"name": "complete_mission", "description": "Mark the current mission complete and compile the review briefing.", "input_schema": {"type": "object", "properties": {"built": {"type": "array", "items": {"type": "object"}}, "decisions": {"type": "array", "items": {"type": "string"}}}, "required": ["built"]}},
    {"name": "log_action", "description": "Log a governance action.", "input_schema": {"type": "object", "properties": {"action": {"type": "string"}, "tier": {"type": "integer"}, "approved_by": {"type": "string"}}, "required": ["action", "tier", "approved_by"]}},
    {"name": "get_calendar", "description": "Get upcoming calendar events across the next N days.", "input_schema": {"type": "object", "properties": {"days": {"type": "integer"}, "calendar_id": {"type": "string"}}}},
    {"name": "create_event", "description": "Create a Google Calendar event.", "input_schema": {"type": "object", "properties": {"title": {"type": "string"}, "start": {"type": "string"}, "end": {"type": "string"}, "description": {"type": "string"}, "location": {"type": "string"}, "calendar_id": {"type": "string"}}, "required": ["title", "start", "end"]}},
    {"name": "save_session", "description": "Save a structured summary of the current conversation session.", "input_schema": {"type": "object", "properties": {"title": {"type": "string"}, "topics": {"type": "array", "items": {"type": "string"}}, "decisions": {"type": "array", "items": {"type": "string"}}, "actions": {"type": "array", "items": {"type": "string"}}, "context": {"type": "string"}, "channel": {"type": "string"}}, "required": ["title", "topics"]}},
    {"name": "log_decision", "description": "Log a key decision to the decisions vault.", "input_schema": {"type": "object", "properties": {"decision": {"type": "string"}, "context": {"type": "string"}, "outcome": {"type": "string"}, "channel": {"type": "string"}}, "required": ["decision", "context"]}},
    {"name": "trigger_n8n_workflow", "description": "Trigger an n8n workflow by name.", "input_schema": {"type": "object", "properties": {"workflow": {"type": "string"}, "payload": {"type": "object"}}, "required": ["workflow"]}},
    {"name": "list_n8n_workflows", "description": "List all registered n8n workflows KAI can trigger.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "register_n8n_workflow", "description": "Register a new n8n workflow webhook URL.", "input_schema": {"type": "object", "properties": {"name": {"type": "string"}, "webhook_url": {"type": "string"}, "description": {"type": "string"}}, "required": ["name", "webhook_url"]}},
    {"name": "list_specialists", "description": "List all specialist personas.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "consult_specialist", "description": "Consult a specialist persona for expert input.", "input_schema": {"type": "object", "properties": {"specialist": {"type": "string"}, "question": {"type": "string"}, "context": {"type": "string"}}, "required": ["specialist", "question"]}},
    {"name": "read_email", "description": "Read recent emails from Gmail.", "input_schema": {"type": "object", "properties": {"max_results": {"type": "integer"}, "query": {"type": "string"}}}},
    {"name": "draft_email", "description": "Create an email draft in Gmail.", "input_schema": {"type": "object", "properties": {"to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"}}, "required": ["to", "subject", "body"]}},
    {"name": "setup_project", "description": "Full project creation pipeline.", "input_schema": {"type": "object", "properties": {"id": {"type": "string"}, "name": {"type": "string"}, "description": {"type": "string"}, "advisor": {"type": "string"}, "status": {"type": "string"}, "next": {"type": "string"}, "template_version": {"type": "string"}, "create_slack_channel": {"type": "boolean"}, "slack_channel_name": {"type": "string"}, "invite_contacts": {"type": "array", "items": {"type": "string"}}}, "required": ["id", "name"]}},
    {"name": "create_slack_channel", "description": "Create a new Slack channel.", "input_schema": {"type": "object", "properties": {"name": {"type": "string"}, "private": {"type": "boolean"}}, "required": ["name"]}},
    {"name": "invite_to_slack_channel", "description": "Invite people to a Slack channel (Tier 2).", "input_schema": {"type": "object", "properties": {"channel": {"type": "string"}, "emails": {"type": "array", "items": {"type": "string"}}, "contact_names": {"type": "array", "items": {"type": "string"}}}, "required": ["channel"]}},
    {"name": "lookup_contact", "description": "Look up a person in the contacts registry.", "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "add_contact", "description": "Add a new person to the contacts registry.", "input_schema": {"type": "object", "properties": {"name": {"type": "string"}, "email": {"type": "string"}, "role": {"type": "string"}, "slack_id": {"type": "string"}, "aliases": {"type": "array", "items": {"type": "string"}}}, "required": ["name"]}},
    {"name": "get_o365_calendar", "description": "Fetch calendar events from Revolt and/or Penn State O365.", "input_schema": {"type": "object", "properties": {"days": {"type": "integer"}}}},
    {"name": "web_search", "description": "Search the web for current information using Tavily.", "input_schema": {"type": "object", "properties": {"query": {"type": "string"}, "max_results": {"type": "integer"}}, "required": ["query"]}},
    {"name": "lookup_google_contact", "description": "Search Leo's Google Contacts.", "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "request_t2_approval", "description": "Queue any action that requires Leo's explicit approval via Slack.", "input_schema": {"type": "object", "properties": {"action": {"type": "string"}, "detail": {"type": "string"}, "slack_channel": {"type": "string"}}, "required": ["action", "detail"]}},
    {"name": "get_oura_data", "description": "Fetch Leo's Oura Ring health data.", "input_schema": {"type": "object", "properties": {"data_type": {"type": "string", "enum": ["readiness", "sleep", "activity", "all"]}, "days": {"type": "integer"}}, "required": ["data_type"]}},
    {"name": "wordpress_get_posts", "description": "Fetch recent posts from a WordPress site.", "input_schema": {"type": "object", "properties": {"site": {"type": "string"}, "count": {"type": "integer"}, "status": {"type": "string"}}, "required": ["site"]}},
    {"name": "wordpress_create_post", "description": "Create a WordPress post (draft by default).", "input_schema": {"type": "object", "properties": {"site": {"type": "string"}, "title": {"type": "string"}, "content": {"type": "string"}, "status": {"type": "string"}, "tags": {"type": "array", "items": {"type": "string"}}, "excerpt": {"type": "string"}}, "required": ["site", "title", "content"]}},
    {"name": "get_plane_issues", "description": "List projects, or list/fetch issues in Plane PM. Omit all args to list projects. Pass project_id to list issues. Pass project_id + issue_id for a specific issue.", "input_schema": {"type": "object", "properties": {"project_id": {"type": "string"}, "issue_id": {"type": "string"}, "state": {"type": "string"}}}},
    {"name": "update_plane_issue", "description": "Update a Plane issue — change state, add description, update name, etc.", "input_schema": {"type": "object", "properties": {"project_id": {"type": "string"}, "issue_id": {"type": "string"}, "name": {"type": "string"}, "description_html": {"type": "string"}, "state": {"type": "string"}}, "required": ["project_id", "issue_id"]}},
    {"name": "create_plane_issue", "description": "Create a new issue in Plane PM.", "input_schema": {"type": "object", "properties": {"project_id": {"type": "string"}, "name": {"type": "string"}, "description_html": {"type": "string"}, "priority": {"type": "string"}}, "required": ["project_id", "name"]}},
    {"name": "add_to_parking_lot", "description": "Save any item to Leo's Parking Lot.", "input_schema": {"type": "object", "properties": {"content": {"type": "string"}, "source": {"type": "string"}}, "required": ["content"]}},
    {"name": "list_templates", "description": "List available project template versions.", "input_schema": {"type": "object", "properties": {}}},
]


def _handle_auto_capture(message: str, advisor: str) -> dict | None:
    """Returns CouncilResponse dict if auto-captured, else None."""
    _url_pattern = re.compile(r'^https?://\S+$')
    _capture_patterns = [
        re.compile(r'^(add (this )?to (the )?lot|parking lot this|save this|capture this)[:\s]*(.*)$', re.IGNORECASE),
        re.compile(r'^(article on|check out|look into|save|add|note)[:\s]+\S.{2,}$', re.IGNORECASE),
    ]
    _msg_stripped = message.strip()
    _is_bare_url = bool(_url_pattern.match(_msg_stripped))
    _is_capture = any(p.match(_msg_stripped) for p in _capture_patterns)

    if _is_bare_url or _is_capture:
        try:
            httpx.post(
                f"{WORKER_URL}/parking-lot/quick",
                json={"text": _msg_stripped},
                timeout=10
            )
            _track_usage(advisor, 0, 0, "anthropic", "auto-capture")
            return {"reply": "Saved to your parking lot.", "advisor": advisor, "model": "auto-capture", "usage": {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0}}
        except Exception as e:
            logger.exception("auto_capture: %s", e)
    return None


def _run_agentic_loop(messages: list, tools: list, model: str, system_prompt: str, advisor: str) -> tuple:
    """Run Anthropic agentic loop. Returns (reply, input_tokens, output_tokens)."""
    client = get_anthropic_client()
    total_input_tokens = 0
    total_output_tokens = 0
    raw_reply = ""

    while True:
        kwargs = dict(
            model=model,
            max_tokens=2048,
            system=system_prompt,
            messages=messages,
        )
        if tools:
            kwargs["tools"] = tools

        response = client.messages.create(**kwargs)
        total_input_tokens  += response.usage.input_tokens
        total_output_tokens += response.usage.output_tokens

        if response.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": response.content})
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    result = execute_tool(block.name, block.input, advisor)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result),
                    })
            messages.append({"role": "user", "content": tool_results})
            continue

        raw_reply = next(
            (b.text for b in response.content if hasattr(b, "text")), ""
        )
        break

    return raw_reply, total_input_tokens, total_output_tokens


@router.post("/council/message")
def council_message(req: MessageRequest, background_tasks: BackgroundTasks = None):
    channel = req.channel.lstrip("#")
    advisor = ADVISOR_CHANNELS.get(channel)
    if not advisor:
        raise HTTPException(status_code=400, detail=f"Unknown channel: {channel}")

    # Try auto-capture shortcut
    auto = _handle_auto_capture(req.message, advisor)
    if auto:
        return auto

    system_prompt = load_persona(advisor, channel)
    messages = req.history[-10:]
    messages.append({"role": "user", "content": req.message})

    total_input_tokens = 0
    total_output_tokens = 0
    raw_reply = ""

    # Determine provider/model with complexity routing
    complexity = _classify_complexity(req.message)
    if advisor == "chief":
        if complexity == "deep":
            chief_model = "claude-opus-4-6"
        elif complexity == "simple":
            chief_model = "claude-haiku-4-5-20251001"
        else:
            chief_model = "claude-sonnet-4-6"
        adv_cfg = {"provider": "anthropic", "model": chief_model}
    else:
        adv_cfg = _get_advisor_config(advisor)
        if adv_cfg.get("provider") == "anthropic":
            if complexity == "deep":
                adv_cfg = dict(adv_cfg, model="claude-opus-4-6")
            elif complexity == "simple":
                adv_cfg = dict(adv_cfg, model="claude-haiku-4-5-20251001")

    provider = adv_cfg.get("provider", "anthropic")
    model    = adv_cfg.get("model", "claude-sonnet-4-6")
    actual_provider = provider
    actual_model    = model

    if provider == "anthropic":
        tools = KAI_TOOLS if advisor == "chief" else []
        raw_reply, total_input_tokens, total_output_tokens = _run_agentic_loop(
            messages, tools, model, system_prompt, advisor
        )

    elif provider == "ollama":
        try:
            raw_reply, total_input_tokens, total_output_tokens = _call_ollama(
                model, system_prompt, messages
            )
        except Exception as ollama_err:
            logger.exception("ollama fallback: %s", ollama_err)
            fallback_model = adv_cfg.get("fallback_model", "claude-sonnet-4-6")
            actual_provider = "anthropic"
            actual_model = fallback_model
            client = get_anthropic_client()
            response = client.messages.create(
                model=fallback_model, max_tokens=2048,
                system=system_prompt + f"\n\n[Note: Local model unavailable ({ollama_err}), using cloud fallback]",
                messages=messages,
            )
            total_input_tokens  = response.usage.input_tokens
            total_output_tokens = response.usage.output_tokens
            raw_reply = next((b.text for b in response.content if hasattr(b, "text")), "")

    elif provider == "openai":
        try:
            raw_reply, total_input_tokens, total_output_tokens = _call_openai(
                model, system_prompt, messages
            )
        except Exception as oai_err:
            logger.exception("openai fallback: %s", oai_err)
            fallback_model = adv_cfg.get("fallback_model", "claude-sonnet-4-6")
            actual_provider = "anthropic"
            actual_model = fallback_model
            client = get_anthropic_client()
            response = client.messages.create(
                model=fallback_model, max_tokens=2048,
                system=system_prompt + f"\n\n[Note: OpenAI unavailable ({oai_err}), using Anthropic fallback]",
                messages=messages,
            )
            total_input_tokens  = response.usage.input_tokens
            total_output_tokens = response.usage.output_tokens
            raw_reply = next((b.text for b in response.content if hasattr(b, "text")), "")
    else:
        raise HTTPException(400, f"Unknown provider: {provider}")

    # Insight extraction
    insights_logged = 0
    if advisor == "ember":
        clean_reply, insights = extract_and_strip_insights(raw_reply)
        insights_logged = append_insights_to_vault(insights)
    else:
        clean_reply = raw_reply

    # Log conversation history
    _append_history(channel, "user", req.message)
    _append_history(channel, "assistant", clean_reply)

    # Track token usage
    _track_usage(advisor, total_input_tokens, total_output_tokens, actual_provider, actual_model)

    # Auto-summarize in background
    if background_tasks:
        background_tasks.add_task(_auto_summarize, channel, advisor)

    return {
        "advisor": advisor,
        "channel": channel,
        "reply": clean_reply,
        "insights_logged": insights_logged,
        "input_tokens": total_input_tokens,
        "output_tokens": total_output_tokens,
        "provider": actual_provider,
        "model": actual_model,
    }


@router.post("/message")
def web_message(req: MessageRequest, background_tasks: BackgroundTasks = None):
    """Web UI alias — nginx strips /council/ prefix."""
    return council_message(req, background_tasks)


@router.post("/council/context/update")
def update_context(req: ContextUpdateRequest):
    from council_config import COUNCIL_PATH
    advisor_dir = COUNCIL_PATH / req.advisor
    if not advisor_dir.exists():
        raise HTTPException(status_code=404, detail=f"Advisor not found: {req.advisor}")
    context_file = advisor_dir / "context.md"
    context_file.write_text(req.content, encoding="utf-8")
    return {"status": "updated", "advisor": req.advisor}


@router.get("/council/context/{advisor}")
def get_context(advisor: str):
    from council_config import COUNCIL_PATH
    advisor_dir = COUNCIL_PATH / advisor
    context_file = advisor_dir / "context.md"
    if not context_file.exists():
        raise HTTPException(status_code=404, detail=f"Context not found for: {advisor}")
    return {"advisor": advisor, "content": context_file.read_text(encoding="utf-8")}
