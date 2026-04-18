from fastapi import FastAPI, HTTPException, BackgroundTasks
import httpx
from pathlib import Path
from pydantic import BaseModel
import anthropic
import os
import re
from datetime import datetime

app = FastAPI(title="kai-council-api", version="0.2.0")

VAULT_PATH = Path("/vault")
COUNCIL_PATH = VAULT_PATH / "60_Council"

ADVISOR_CHANNELS = {
    "kai":   "chief",
    "chief": "chief",
    "beats": "beats",
    "beats-personal": "beats",
    "ember": "ember",
    "doc": "doc",
    "coach": "coach",
    "biz": "biz",
    "council": "chief",
    "council-daily": "chief",
    "council-weekly": "chief",
    "council-monthly": "chief",
    "sky": "sky",
    "roads": "roads",
}

INSIGHT_CATEGORIES = {"Insights", "Truths", "Patterns", "Realizations", "Questions"}

# Matches: [INSIGHT:Category] content [/INSIGHT]
# Also handles: [INSIGHT:Category] content (no closing tag, to end of line)
INSIGHT_PATTERN = re.compile(
    r"\[INSIGHT:([A-Za-z]+)\](.*?)(?:\[/INSIGHT\]|(?=\[INSIGHT:)|\Z)",
    re.DOTALL,
)

WORKER_URL = "http://kai-worker-api:8001"

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
    {
        "name": "list_workflows",
        "description": "List all currently saved command workflows/buttons.",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "delete_workflow",
        "description": "Delete a workflow button by ID.",
        "input_schema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "The workflow ID to delete"}
            },
            "required": ["id"]
        }
    },
    {
        "name": "create_task",
        "description": "Create a task in Todoist. Use when Leo asks you to add a task, to-do, or action item.",
        "input_schema": {
            "type": "object",
            "properties": {
                "content":     {"type": "string",  "description": "Task title"},
                "due_date":    {"type": "string",  "description": "Due date YYYY-MM-DD (optional)"},
                "priority":    {"type": "integer", "description": "1=urgent, 2=high, 3=medium, 4=normal. Default 4."},
                "description": {"type": "string",  "description": "Task notes (optional)"}
            },
            "required": ["content"]
        }
    },
    {
        "name": "create_project",
        "description": "Create a new project in KAI. Use when setting up a new project, initiative, or work stream.",
        "input_schema": {
            "type": "object",
            "properties": {
                "id":          {"type": "string", "description": "Unique slug (lowercase, hyphens)"},
                "name":        {"type": "string", "description": "Display name"},
                "description": {"type": "string", "description": "One-line description"},
                "status":      {"type": "string", "description": "green/yellow/red", "enum": ["green","yellow","red"]},
                "next":        {"type": "string", "description": "Current focus / next action"},
                "advisor":     {"type": "string", "description": "Advisor who owns this (kai/biz/ember/etc)"}
            },
            "required": ["id", "name", "description", "status"]
        }
    },
    {
        "name": "update_project",
        "description": "Update a project's status, next action, or milestone.",
        "input_schema": {
            "type": "object",
            "properties": {
                "id":            {"type": "string",  "description": "Project ID"},
                "status":        {"type": "string",  "description": "green/yellow/red"},
                "next":          {"type": "string",  "description": "Current focus / next action"},
                "milestone":     {"type": "string",  "description": "Current milestone description"},
                "milestone_pct": {"type": "integer", "description": "Milestone % complete 0-100"}
            },
            "required": ["id"]
        }
    },
    {
        "name": "list_projects",
        "description": "List all current projects with their status.",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "write_to_vault",
        "description": "Write a document to the vault. Use during mission execution to save deliverables, plans, briefs, mockups, or any generated content.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path":    {"type": "string", "description": "Relative vault path (e.g. '20_Projects/flower-shop/brief.md')"},
                "content": {"type": "string", "description": "Full file content"},
                "description": {"type": "string", "description": "One-line description of this document"}
            },
            "required": ["path", "content"]
        }
    },
    {
        "name": "read_vault",
        "description": "Read a file from the vault for context before writing or to check existing content.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative vault path"}
            },
            "required": ["path"]
        }
    },
    {
        "name": "send_slack_message",
        "description": "Post a message to a Slack channel. Use for project updates, async notifications, mission status, and team coordination.",
        "input_schema": {
            "type": "object",
            "properties": {
                "channel": {"type": "string", "description": "Channel name without # (e.g. 'ops', 'encore', 'general')"},
                "message": {"type": "string", "description": "Message text (Slack markdown supported)"},
                "advisor": {"type": "string", "description": "Posting advisor identity (kai/ember/biz/beats/doc/coach/creative/tech/dev). Default: kai"}
            },
            "required": ["channel", "message"]
        }
    },
    {
        "name": "start_mission",
        "description": "Record the start of an autonomous mission. Call this when Leo grants you autonomous execution scope.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name":  {"type": "string", "description": "Short mission name"},
                "scope": {"type": "array",  "items": {"type": "string"}, "description": "List of deliverables/actions in scope"},
                "notes": {"type": "string", "description": "Any constraints or context for this mission"}
            },
            "required": ["name", "scope"]
        }
    },
    {
        "name": "complete_mission",
        "description": "Mark the current mission complete and compile the review briefing.",
        "input_schema": {
            "type": "object",
            "properties": {
                "built":     {"type": "array",  "items": {"type": "object"}, "description": "List of {label, path} for each deliverable created"},
                "decisions": {"type": "array",  "items": {"type": "string"}, "description": "Decisions Leo needs to make on review"}
            },
            "required": ["built"]
        }
    },
    {
        "name": "log_action",
        "description": "Log a governance action to the team changelog. Use when taking any notable action, especially Tier 2+.",
        "input_schema": {
            "type": "object",
            "properties": {
                "action":      {"type": "string",  "description": "What was done"},
                "tier":        {"type": "integer", "description": "Governance tier 1/2/3"},
                "approved_by": {"type": "string",  "description": "How approved (e.g. 'Leo via Slack ✅', 'Autonomous Tier 1')"}
            },
            "required": ["action", "tier", "approved_by"]
        }
    },
        {
            "name": "get_calendar",
            "description": "Get upcoming calendar events across the next N days.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "days":        {"type": "integer", "description": "Days to look ahead (default 7)"},
                    "calendar_id": {"type": "string",  "description": "Calendar ID (default: primary)"}
                }
            }
        },
        {
            "name": "create_event",
            "description": "Create a Google Calendar event.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "title":       {"type": "string", "description": "Event title"},
                    "start":       {"type": "string", "description": "Start ISO 8601 e.g. 2026-04-18T10:00:00"},
                    "end":         {"type": "string", "description": "End ISO 8601"},
                    "description": {"type": "string", "description": "Optional description"},
                    "location":    {"type": "string", "description": "Optional location"},
                    "calendar_id": {"type": "string", "description": "Calendar ID (default: primary)"}
                },
                "required": ["title", "start", "end"]
            }
        },
    {
        "name": "save_session",
        "description": "Save a structured summary of the current conversation session to the knowledge vault. Use when Leo asks to save, document, or archive the session, or when a meaningful conversation has concluded.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title":    {"type": "string", "description": "Short descriptive title for this session (e.g. 'Sprint 6 planning', 'Encore strategy review')"},
                "topics":   {"type": "array", "items": {"type": "string"}, "description": "Key topics discussed"},
                "decisions":{"type": "array", "items": {"type": "string"}, "description": "Decisions made or confirmed"},
                "actions":  {"type": "array", "items": {"type": "string"}, "description": "Action items or next steps"},
                "context":  {"type": "string", "description": "Any important context for the next session with this advisor"},
                "channel":  {"type": "string", "description": "Channel this session is for (defaults to current channel)"}
            },
            "required": ["title", "topics"]
        }
    },
    {
        "name": "log_decision",
        "description": "Log a key decision to the decisions vault. Use when Leo makes a notable choice, sets direction, or commits to a path.",
        "input_schema": {
            "type": "object",
            "properties": {
                "decision": {"type": "string", "description": "What was decided"},
                "context":  {"type": "string", "description": "Why — the reasoning or constraints"},
                "outcome":  {"type": "string", "description": "Expected outcome or what this unlocks"},
                "channel":  {"type": "string", "description": "Which channel/advisor this came from"}
            },
            "required": ["decision", "context"]
        }
    },
    {
        "name": "trigger_n8n_workflow",
        "description": "Trigger an n8n workflow by name. Use to launch automations, scheduled tasks, or any n8n workflow Leo has set up. The workflow runs on the n8n server and returns its output.",
        "input_schema": {
            "type": "object",
            "properties": {
                "workflow": {"type": "string", "description": "Workflow name (as registered in the n8n registry, e.g. 'morning-brief', 'gmail-read')"},
                "payload":  {"type": "object", "description": "Optional JSON payload to pass to the workflow"}
            },
            "required": ["workflow"]
        }
    },
    {
        "name": "list_n8n_workflows",
        "description": "List all registered n8n workflows KAI can trigger.",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "register_n8n_workflow",
        "description": "Register a new n8n workflow webhook URL so KAI can trigger it by name. Use when Leo sets up a new n8n workflow and wants KAI to be able to call it.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name":        {"type": "string", "description": "Short identifier (lowercase, hyphens). e.g. 'morning-brief'"},
                "webhook_url": {"type": "string", "description": "Full n8n webhook URL"},
                "description": {"type": "string", "description": "What this workflow does"}
            },
            "required": ["name", "webhook_url"]
        }
    },
    {
        "name": "list_specialists",
        "description": "List all specialist personas KAI can consult for deep expertise.",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "consult_specialist",
        "description": "Consult a specialist persona for expert input on a specific topic. The specialist reads the question and responds with deep domain expertise. Use when Leo needs specialist-level thinking beyond what the lead advisors cover.",
        "input_schema": {
            "type": "object",
            "properties": {
                "specialist": {"type": "string", "description": "Specialist ID (e.g. 'strategist', 'architect', 'designer', 'researcher', 'copywriter', 'lead-developer', 'pm', 'test-engineer', 'data-engineer', 'graphic-designer')"},
                "question":   {"type": "string", "description": "The question or brief to put to the specialist"},
                "context":    {"type": "string", "description": "Any project context the specialist needs to answer well"}
            },
            "required": ["specialist", "question"]
        }
    },
    {
        "name": "read_email",
        "description": "Read recent emails from the sonicink Gmail inbox. Returns subject, sender, date, and a preview of the most recent messages.",
        "input_schema": {
            "type": "object",
            "properties": {
                "max_results": {"type": "integer", "description": "Max number of emails to return (default 10)"},
                "query":       {"type": "string",  "description": "Gmail search query (e.g. 'is:unread', 'from:john@example.com')"}
            }
        }
    },
    {
        "name": "draft_email",
        "description": "Create an email draft in Gmail. The draft is NOT sent — Leo must review and send manually. Always use this, never send directly.",
        "input_schema": {
            "type": "object",
            "properties": {
                "to":      {"type": "string", "description": "Recipient email address"},
                "subject": {"type": "string", "description": "Email subject"},
                "body":    {"type": "string", "description": "Email body (plain text or simple HTML)"}
            },
            "required": ["to", "subject", "body"]
        }
    }
]


def _slack_token() -> str:
    p = Path("/run/secrets/slack_bot_token")
    return p.read_text().strip() if p.exists() else os.environ.get("SLACK_BOT_TOKEN", "")

ADVISOR_AVATARS = {
    "kai":      "https://kai.sonicink.space/avatar-kai.png",
    "ember":    "https://kai.sonicink.space/avatar-ember.png",
    "beats":    "https://kai.sonicink.space/avatar-beats.png",
    "doc":      "https://kai.sonicink.space/icon-192.png",
    "coach":    "https://kai.sonicink.space/icon-192.png",
    "biz":      "https://kai.sonicink.space/icon-192.png",
    "creative": "https://kai.sonicink.space/icon-192.png",
    "tech":     "https://kai.sonicink.space/icon-192.png",
    "dev":      "https://kai.sonicink.space/icon-192.png",
    "sky":      "https://kai.sonicink.space/avatar-sky.png",
    "roads":    "https://kai.sonicink.space/avatar-roads.png",
}


# ── Token usage tracker ───────────────────────────────────────────────────────
def _track_usage(advisor: str, input_tokens: int, output_tokens: int, provider: str = "anthropic", model: str = "claude-sonnet-4-5"):
    """Append token usage to vault/00_System/token_usage.json"""
    import json, datetime
    try:
        usage_path = Path("/vault/00_System/token_usage.json")
        today = datetime.date.today().isoformat()
        # Cost per provider/model
        COSTS = {
            "claude-sonnet-4-5": (3, 15),
            "claude-sonnet-4-6": (3, 15),
            "claude-opus-4-6":   (15, 75),
            "gpt-4o":            (5, 15),
            "gpt-4o-mini":       (0.15, 0.6),
            "llama3.2":          (0, 0),
            "llama3.1:8b":       (0, 0),
        }
        in_rate, out_rate = COSTS.get(model, (3, 15))
        cost = (input_tokens * in_rate + output_tokens * out_rate) / 1_000_000

        if usage_path.exists():
            data = json.loads(usage_path.read_text())
        else:
            data = {"days": [], "total": {"input": 0, "output": 0, "cost_usd": 0.0, "calls": 0, "by_advisor": {}}}

        # Ensure total exists
        if "total" not in data:
            data["total"] = {"input": 0, "output": 0, "cost_usd": 0.0, "calls": 0, "by_advisor": {}}
        if "by_advisor" not in data["total"]:
            data["total"]["by_advisor"] = {}

        # Update or create today's entry
        day = next((d for d in data["days"] if d["date"] == today), None)
        if day is None:
            day = {"date": today, "input": 0, "output": 0, "cost_usd": 0.0, "calls": 0, "by_advisor": {}}
            data["days"].append(day)

        day["input"] += input_tokens
        day["output"] += output_tokens
        day["cost_usd"] = round(day["cost_usd"] + cost, 6)
        day["calls"] += 1
        day["by_advisor"][advisor] = day["by_advisor"].get(advisor, 0) + 1
        # Track by provider
        if "by_provider" not in day:
            day["by_provider"] = {}
        pkey = f"{provider}/{model}"
        day["by_provider"][pkey] = day["by_provider"].get(pkey, 0) + 1

        data["total"]["input"] += input_tokens
        data["total"]["output"] += output_tokens
        data["total"]["cost_usd"] = round(data["total"]["cost_usd"] + cost, 6)
        data["total"]["calls"] += 1
        data["total"]["by_advisor"][advisor] = data["total"]["by_advisor"].get(advisor, 0) + 1

        usage_path.write_text(json.dumps(data, indent=2))
    except Exception as e:
        print(f"[token-usage] error: {e}")

def execute_tool(tool_name: str, tool_input: dict) -> dict:
    import json as _tj
    from datetime import datetime as _dt2, date as _d2
    try:
        with httpx.Client(timeout=15) as client:
            # ── Workflows ──────────────────────────────────────────────────
            if tool_name == "save_workflow":
                r = client.post(f"{WORKER_URL}/workflows", json=tool_input)
                return r.json()
            elif tool_name == "list_workflows":
                r = client.get(f"{WORKER_URL}/workflows")
                return r.json()
            elif tool_name == "delete_workflow":
                r = client.delete(f"{WORKER_URL}/workflows/{tool_input.get('id','')}")
                return r.json()

            # ── Tasks ──────────────────────────────────────────────────────
            elif tool_name == "create_task":
                r = client.post(f"{WORKER_URL}/tasks", json=tool_input)
                return r.json()

            # ── Projects ───────────────────────────────────────────────────
            elif tool_name == "create_project":
                r = client.post(f"{WORKER_URL}/projects", json=tool_input)
                return r.json()
            elif tool_name == "update_project":
                pid = tool_input.pop("id")
                r = client.patch(f"{WORKER_URL}/projects/{pid}", json=tool_input)
                return r.json()
            elif tool_name == "list_projects":
                r = client.get(f"{WORKER_URL}/projects")
                return r.json()

            # ── Vault ──────────────────────────────────────────────────────
            elif tool_name == "write_to_vault":
                r = client.post(f"{WORKER_URL}/vault/write",
                    params={"path": tool_input["path"], "content": tool_input["content"]})
                result = r.json()
                # Log to mission deliverables if mission active
                _log_mission_deliverable(tool_input["path"], tool_input.get("description",""))
                return result
            elif tool_name == "read_vault":
                r = client.get(f"{WORKER_URL}/vault/read",
                    params={"path": tool_input["path"]})
                return r.json()

            # ── Slack ──────────────────────────────────────────────────────
            elif tool_name == "send_slack_message":
                token = _slack_token()
                if not token:
                    return {"error": "Slack token not configured"}
                advisor = tool_input.get("advisor", "kai")
                channel = tool_input.get("channel", "ops")
                if not channel.startswith("#"):
                    channel = f"#{channel}"
                payload = {
                    "channel": channel,
                    "text": tool_input["message"],
                    "username": advisor.upper() if advisor == "kai" else advisor.capitalize(),
                    "icon_url": ADVISOR_AVATARS.get(advisor, ADVISOR_AVATARS["kai"]),
                }
                r = client.post("https://slack.com/api/chat.postMessage",
                    headers={"Authorization": f"Bearer {token}"},
                    json=payload)
                data = r.json()
                if not data.get("ok"):
                    return {"error": data.get("error", "slack error"), "detail": data}
                return {"ok": True, "channel": channel}

            # ── Mission state ──────────────────────────────────────────────
            elif tool_name == "start_mission":
                mission = {
                    "name": tool_input["name"],
                    "scope": tool_input["scope"],
                    "notes": tool_input.get("notes", ""),
                    "granted": _dt2.utcnow().isoformat(),
                    "status": "in_progress",
                    "deliverables": [],
                }
                mission_file = VAULT_PATH / "00_System" / "active_mission.json"
                mission_file.write_text(_tj.dumps(mission, indent=2))
                return {"ok": True, "mission": tool_input["name"]}
            elif tool_name == "complete_mission":
                mission_file = VAULT_PATH / "00_System" / "active_mission.json"
                if mission_file.exists():
                    mission = _tj.loads(mission_file.read_text())
                    mission["status"] = "review_ready"
                    mission["completed"] = _dt2.utcnow().isoformat()
                    mission["built"] = tool_input.get("built", [])
                    mission["decisions"] = tool_input.get("decisions", [])
                    mission_file.write_text(_tj.dumps(mission, indent=2))
                return {"ok": True, "status": "review_ready"}

            # ── Governance log ─────────────────────────────────────────────

            elif tool_name == "get_calendar":
                # Route through n8n webhook which handles Google OAuth
                days = tool_input.get("days", 7)
                r = client.post("http://kai-n8n:5678/webhook/kai-calendar-events",
                               json={"days": days}, timeout=15)
                events = r.json() if r.status_code == 200 else []
                return {"events": events}

            elif tool_name == "create_event":
                r = client.post(f"{WORKER_URL}/calendar/events", json=tool_input)
                return r.json()
            elif tool_name == "log_action":
                changelog = VAULT_PATH / "00_System" / "team_changelog.md"
                if not changelog.exists():
                    changelog.write_text("# KAI Team Changelog\n\n")
                entry = f"- {_d2.today().isoformat()} | KAI | {tool_input['action']} | Tier {tool_input['tier']} | {tool_input['approved_by']}\n"
                with open(changelog, "a") as f:
                    f.write(entry)
                return {"ok": True}

            # ── Knowledge ──────────────────────────────────────────────────
            elif tool_name == "save_session":
                ch = tool_input.get("channel", "chief")
                return _write_session_summary(
                    channel=ch,
                    title=tool_input["title"],
                    topics=tool_input.get("topics", []),
                    decisions=tool_input.get("decisions", []),
                    actions=tool_input.get("actions", []),
                    context_note=tool_input.get("context", ""),
                )
            elif tool_name == "log_decision":
                ch = tool_input.get("channel", "chief")
                return _write_decision(
                    channel=ch,
                    decision=tool_input["decision"],
                    context=tool_input["context"],
                    outcome=tool_input.get("outcome", ""),
                )

            # ── n8n workflows ──────────────────────────────────────────────
            elif tool_name == "trigger_n8n_workflow":
                return _trigger_n8n(tool_input["workflow"], tool_input.get("payload", {}))
            elif tool_name == "list_n8n_workflows":
                return _list_n8n_workflows()
            elif tool_name == "register_n8n_workflow":
                return _register_n8n_workflow(
                    tool_input["name"], tool_input["webhook_url"],
                    tool_input.get("description", "")
                )

            # ── Specialists ────────────────────────────────────────────────
            elif tool_name == "list_specialists":
                return _list_specialists()
            elif tool_name == "consult_specialist":
                return _consult_specialist(
                    tool_input["specialist"],
                    tool_input["question"],
                    tool_input.get("context", "")
                )

            # ── Email (via n8n) ────────────────────────────────────────────
            elif tool_name == "read_email":
                return _trigger_n8n("gmail-read", {
                    "max_results": tool_input.get("max_results", 10),
                    "query": tool_input.get("query", "")
                })
            elif tool_name == "draft_email":
                return _trigger_n8n("gmail-draft", {
                    "to": tool_input["to"],
                    "subject": tool_input["subject"],
                    "body": tool_input["body"]
                })

    except Exception as e:
        return {"error": str(e)}
    return {"error": f"Unknown tool: {tool_name}"}


# ── n8n Workflow Registry ─────────────────────────────────────────────────────

N8N_REGISTRY_FILE = VAULT_PATH / "00_System" / "n8n_workflows.json"
N8N_BASE = "http://kai-n8n:5678"

def _load_n8n_registry() -> dict:
    import json as _nj
    if N8N_REGISTRY_FILE.exists():
        try:
            return _nj.loads(N8N_REGISTRY_FILE.read_text())
        except Exception:
            pass
    return {}

def _save_n8n_registry(registry: dict):
    import json as _nj
    N8N_REGISTRY_FILE.write_text(_nj.dumps(registry, indent=2))

def _trigger_n8n(workflow: str, payload: dict) -> dict:
    import json as _nj
    registry = _load_n8n_registry()
    entry = registry.get(workflow)
    if not entry:
        return {"error": f"Workflow '{workflow}' not registered. Use list_n8n_workflows or register_n8n_workflow."}
    webhook_url = entry["webhook_url"] if isinstance(entry, dict) else entry
    with httpx.Client(timeout=30) as client:
        r = client.post(webhook_url, json=payload)
        if r.status_code == 200:
            try:
                return {"ok": True, "workflow": workflow, "result": r.json()}
            except Exception:
                return {"ok": True, "workflow": workflow, "result": r.text[:2000]}
        return {"error": f"n8n returned {r.status_code}", "body": r.text[:500]}

def _list_n8n_workflows() -> dict:
    registry = _load_n8n_registry()
    workflows = []
    for name, entry in registry.items():
        if isinstance(entry, dict):
            workflows.append({"name": name, "description": entry.get("description", ""), "url": entry.get("webhook_url", "")})
        else:
            workflows.append({"name": name, "description": "", "url": entry})
    return {"workflows": workflows, "count": len(workflows)}

def _register_n8n_workflow(name: str, webhook_url: str, description: str) -> dict:
    registry = _load_n8n_registry()
    registry[name] = {"webhook_url": webhook_url, "description": description}
    _save_n8n_registry(registry)
    return {"ok": True, "name": name, "registered": True}


# ── Specialist Consultation ────────────────────────────────────────────────────

SPECIALISTS_FILE = VAULT_PATH / "00_System" / "specialists.json"

def _list_specialists() -> dict:
    import json as _sj
    if not SPECIALISTS_FILE.exists():
        return {"specialists": []}
    specialists = _sj.loads(SPECIALISTS_FILE.read_text())
    return {"specialists": [{"id": s["id"], "name": s["name"], "domain": s["domain"]} for s in specialists]}

def _consult_specialist(specialist_id: str, question: str, context: str) -> dict:
    import json as _cj
    if not SPECIALISTS_FILE.exists():
        return {"error": "Specialists registry not found"}

    specialists = _cj.loads(SPECIALISTS_FILE.read_text())
    spec = next((s for s in specialists if s["id"] == specialist_id), None)
    if not spec:
        available = [s["id"] for s in specialists]
        return {"error": f"Specialist '{specialist_id}' not found. Available: {available}"}

    spec_file = VAULT_PATH / spec["file"]
    if not spec_file.exists():
        return {"error": f"Persona file not found: {spec['file']}"}

    persona = spec_file.read_text(encoding="utf-8")

    # Load business profile for context
    bp = VAULT_PATH / "00_System" / "business_profile.md"
    system = ""
    if bp.exists():
        system = (
            "<background_context>\n"
            + bp.read_text(encoding="utf-8")
            + "\n</background_context>\n\n"
        )
    system += persona

    user_msg = question
    if context:
        user_msg = f"Context: {context}\n\nQuestion: {question}"

    try:
        client = get_anthropic_client()
        response = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=1500,
            system=system,
            messages=[{"role": "user", "content": user_msg}]
        )
        reply = response.content[0].text
        _track_usage("specialist", response.usage.input_tokens, response.usage.output_tokens)
        return {
            "specialist": spec["name"],
            "domain": spec["domain"],
            "response": reply,
        }
    except Exception as e:
        return {"error": str(e)}


# ── Knowledge Layer ──────────────────────────────────────────────────────────

def _write_session_summary(channel: str, title: str, topics: list, decisions: list,
                            actions: list, context_note: str) -> dict:
    """Write a structured session summary to vault/60_Council/sessions/{channel}/"""
    import json as _kj
    from datetime import datetime as _kdt
    sessions_dir = COUNCIL_PATH / "sessions" / channel
    sessions_dir.mkdir(parents=True, exist_ok=True)
    ts = _kdt.utcnow()
    filename = ts.strftime("%Y-%m-%dT%H%M") + ".md"
    filepath = sessions_dir / filename

    lines = [
        f"# Session — {channel} — {ts.strftime('%Y-%m-%d')}",
        f"",
        f"**Title:** {title}  ",
        f"**Channel:** {channel}  ",
        f"**Date:** {ts.strftime('%Y-%m-%d %H:%M')} UTC  ",
        f"",
    ]
    if topics:
        lines += ["## Topics", ""] + [f"- {t}" for t in topics] + [""]
    if decisions:
        lines += ["## Decisions", ""] + [f"- {d}" for d in decisions] + [""]
    if actions:
        lines += ["## Action Items", ""] + [f"- [ ] {a}" for a in actions] + [""]
    if context_note:
        lines += ["## Context for Next Session", "", context_note, ""]

    filepath.write_text("\n".join(lines), encoding="utf-8")
    return {"ok": True, "path": f"60_Council/sessions/{channel}/{filename}", "title": title}


def _write_decision(channel: str, decision: str, context: str, outcome: str) -> dict:
    """Append a decision entry to vault/60_Council/decisions/{YYYY-MM}.md"""
    from datetime import datetime as _ddt
    decisions_dir = COUNCIL_PATH / "decisions"
    decisions_dir.mkdir(parents=True, exist_ok=True)
    ts = _ddt.utcnow()
    filename = ts.strftime("%Y-%m") + ".md"
    filepath = decisions_dir / filename

    header = f"# Decisions — {ts.strftime('%Y-%m')}\n\n" if not filepath.exists() else ""
    entry = (
        f"## {ts.strftime('%Y-%m-%d')} — {channel}\n\n"
        f"**Decision:** {decision}  \n"
        f"**Context:** {context}  \n"
    )
    if outcome:
        entry += f"**Outcome:** {outcome}  \n"
    entry += "\n---\n\n"

    with open(filepath, "a", encoding="utf-8") as f:
        if header:
            f.write(header)
        f.write(entry)
    return {"ok": True, "path": f"60_Council/decisions/{filename}", "decision": decision}


def _auto_summarize(channel: str, advisor: str):
    """Background: generate a session summary if history has grown significantly since last auto-summary."""
    try:
        from datetime import datetime as _adt
        history_file = HISTORY_DIR / f"{channel}.jsonl"
        if not history_file.exists():
            return

        # Check marker: last line count at time of previous auto-summary
        marker_file = HISTORY_DIR / f".{channel}.summarized"
        last_count = int(marker_file.read_text().strip()) if marker_file.exists() else 0
        lines = history_file.read_text(encoding="utf-8").strip().splitlines()
        current_count = len(lines)

        # Trigger if 20+ new lines (10 exchanges) since last summary
        if current_count - last_count < 20:
            return

        # Load recent messages since marker
        import json as _aj
        recent = []
        for line in lines[last_count:]:
            try:
                recent.append(_aj.loads(line))
            except Exception:
                pass

        if len(recent) < 10:
            return

        # Build a compact transcript
        transcript_parts = []
        for msg in recent[-30:]:  # cap at 30 entries
            role = "Leo" if msg["role"] == "user" else advisor.upper()
            transcript_parts.append(f"{role}: {msg['content'][:400]}")
        transcript = "\n".join(transcript_parts)

        # Generate summary via Claude
        client = get_anthropic_client()
        response = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=800,
            messages=[{
                "role": "user",
                "content": (
                    f"Summarize this {advisor} conversation session concisely. "
                    f"Return ONLY a JSON object with keys: title (short string), "
                    f"topics (array of strings), decisions (array of strings), "
                    f"actions (array of strings), context (one paragraph for next session).\n\n"
                    f"Transcript:\n{transcript}"
                )
            }]
        )
        import json as _sj, re as _re
        raw = response.content[0].text.strip()
        # Extract JSON from response (may be wrapped in ```json blocks)
        match = _re.search(r"\{.*\}", raw, _re.DOTALL)
        if not match:
            return
        summary = _sj.loads(match.group())

        _write_session_summary(
            channel=channel,
            title=summary.get("title", f"Auto-summary {_adt.utcnow().strftime('%Y-%m-%d')}"),
            topics=summary.get("topics", []),
            decisions=summary.get("decisions", []),
            actions=summary.get("actions", []),
            context_note=summary.get("context", ""),
        )
        # Update marker
        marker_file.write_text(str(current_count))
        _track_usage(advisor, response.usage.input_tokens, response.usage.output_tokens)
    except Exception as e:
        # Non-critical — never fail the main response
        pass


def _log_mission_deliverable(path: str, description: str):
    import json as _mj
    mission_file = VAULT_PATH / "00_System" / "active_mission.json"
    if not mission_file.exists():
        return
    try:
        mission = _mj.loads(mission_file.read_text())
        if mission.get("status") == "in_progress":
            mission.setdefault("deliverables", []).append({"path": path, "description": description})
            mission_file.write_text(_mj.dumps(mission, indent=2))
    except Exception:
        pass




def get_anthropic_client():
    secret_path = Path("/run/secrets/anthropic_api_key")
    api_key = secret_path.read_text().strip() if secret_path.exists() else os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise HTTPException(status_code=503, detail="Anthropic API key not configured")
    return anthropic.Anthropic(api_key=api_key)


# ── Multi-provider model routing ───────────────────────────────────────────────

MODEL_CONFIG_FILE = VAULT_PATH / "00_System" / "model_config.json"

def _load_model_config() -> dict:
    import json as _mcj
    if MODEL_CONFIG_FILE.exists():
        try:
            return _mcj.loads(MODEL_CONFIG_FILE.read_text())
        except Exception:
            pass
    return {}

def _get_advisor_config(advisor: str) -> dict:
    config = _load_model_config()
    return config.get("advisors", {}).get(advisor, {
        "provider": "anthropic",
        "model": "claude-sonnet-4-5",
    })

def _call_ollama(model: str, system: str, messages: list, max_tokens: int = 1024) -> tuple:
    """Call local Ollama. Returns (reply, input_tokens, output_tokens).
    Uses a short system prompt (persona identity only) for CPU inference speed.
    Business profile is NOT injected — not needed for privacy-first advisors.
    Model stays loaded in memory via keep_alive=30m.
    """
    # Extract just the persona identity — first 800 chars after business profile
    if "</background_context>" in system:
        system = system.split("</background_context>", 1)[1].strip()
    # Further cap at 1200 chars to keep prompt short for CPU speed
    if len(system) > 1200:
        system = system[:1200] + "\n[Respond in character per the above.]"

    ollama_msgs = [{"role": "system", "content": system}]
    # Only last 4 history messages to keep context tight
    for m in messages[-4:]:
        c = m["content"] if isinstance(m["content"], str) else str(m["content"])
        ollama_msgs.append({"role": m["role"], "content": c})

    with httpx.Client(timeout=30) as hc:
        r = hc.post("http://kai-ollama:11434/api/chat", json={
            "model": model,
            "messages": ollama_msgs,
            "stream": False,
            "keep_alive": "30m",
            "options": {"num_predict": max_tokens, "temperature": 0.7},
        })
    if r.status_code != 200:
        raise RuntimeError(f"Ollama {r.status_code}: {r.text[:300]}")
    data = r.json()
    reply = data["message"]["content"]
    return reply, data.get("prompt_eval_count", 0), data.get("eval_count", 0)


def _warmup_ollama(model: str = "llama3.2"):
    """Pre-warm Ollama so the model is loaded into RAM. Non-blocking."""
    try:
        with httpx.Client(timeout=5) as hc:
            hc.post("http://kai-ollama:11434/api/generate", json={
                "model": model, "prompt": "", "keep_alive": "30m"
            })
    except Exception:
        pass

def _call_openai(model: str, system: str, messages: list, max_tokens: int = 2048) -> tuple:
    """Call OpenAI API. Returns (reply, input_tokens, output_tokens)."""
    secret_path = Path("/run/secrets/openai_api_key")
    api_key = secret_path.read_text().strip() if secret_path.exists() else os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise RuntimeError("OpenAI API key not configured — add OPENAI_API_KEY secret")

    oai_msgs = []
    if system:
        oai_msgs.append({"role": "system", "content": system})
    for m in messages:
        if isinstance(m["content"], str):
            oai_msgs.append({"role": m["role"], "content": m["content"]})

    with httpx.Client(timeout=60) as hc:
        r = hc.post("https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": model, "messages": oai_msgs, "max_tokens": max_tokens},
        )
    if r.status_code != 200:
        raise RuntimeError(f"OpenAI {r.status_code}: {r.text[:300]}")
    data = r.json()
    reply = data["choices"][0]["message"]["content"]
    usage = data.get("usage", {})
    return reply, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)


def load_persona(advisor: str, channel: str = None) -> str:
    advisor_dir = COUNCIL_PATH / advisor
    persona_file = advisor_dir / f"{advisor.upper()}.md"
    if not persona_file.exists():
        raise HTTPException(status_code=404, detail=f"Persona not found: {advisor}")

    # Always prepend business profile for full session context
    parts = []
    business_profile = VAULT_PATH / "00_System" / "business_profile.md"
    if business_profile.exists():
        profile_text = business_profile.read_text(encoding="utf-8")
        parts.append(
            "<background_context>\n"
            "The following is Leo's business and life profile. "
            "Use it as silent context to inform every response. "
            "Do NOT recite, summarize, or reference this document unless Leo explicitly asks.\n\n"
            + profile_text +
            "\n</background_context>"
        )

    parts.append(persona_file.read_text(encoding="utf-8"))

    context_file = advisor_dir / "context.md"
    if context_file.exists():
        parts.append(context_file.read_text(encoding="utf-8"))

    if channel == "beats-personal" and (advisor_dir / "deep.md").exists():
        parts.append((advisor_dir / "deep.md").read_text(encoding="utf-8"))

    if advisor == "ember" and (advisor_dir / "insights.md").exists():
        insights = (advisor_dir / "insights.md").read_text(encoding="utf-8")
        if insights.strip():
            parts.append(insights)

    return "\n\n---\n\n".join(parts)


def extract_and_strip_insights(text: str) -> tuple[str, list[dict]]:
    """Extract [INSIGHT:...] tags from text, return (clean_text, insights_list)."""
    insights = []
    for match in INSIGHT_PATTERN.finditer(text):
        category = match.group(1).strip()
        content = match.group(2).strip()
        if category in INSIGHT_CATEGORIES and content:
            insights.append({"category": category, "content": content})

    # Strip all insight tags from the reply
    clean = re.sub(r"\[INSIGHT:[A-Za-z]+\].*?(?:\[/INSIGHT\])", "", text, flags=re.DOTALL)
    # Also strip unclosed tags (to end of line)
    clean = re.sub(r"\[INSIGHT:[A-Za-z]+\].*$", "", clean, flags=re.MULTILINE)
    clean = re.sub(r"\[/INSIGHT\]", "", clean)
    clean = clean.strip()

    return clean, insights


def append_insights_to_vault(insights: list[dict]) -> int:
    """Append extracted insights to ember/insights.md. Returns count written."""
    if not insights:
        return 0

    insights_file = COUNCIL_PATH / "ember" / "insights.md"
    if not insights_file.exists():
        return 0

    content = insights_file.read_text(encoding="utf-8")
    date_str = datetime.utcnow().strftime("%Y-%m-%d")

    for item in insights:
        category = item["category"]
        entry = f"- [{date_str}] {item['content']}"

        # Find the category section and append after it
        header = f"## {category}"
        if header in content:
            # Insert after the header line
            content = content.replace(
                header,
                f"{header}\n{entry}",
                1,
            )
        else:
            # Category not found — append new section at end
            content += f"\n\n{header}\n{entry}\n"

    insights_file.write_text(content, encoding="utf-8")
    return len(insights)


class MessageRequest(BaseModel):
    channel: str
    message: str
    user_id: str = ""
    history: list[dict] = []
    thread_ts: str = ""


class ContextUpdateRequest(BaseModel):
    advisor: str
    content: str


@app.get("/health")
def health():
    council_ok = COUNCIL_PATH.exists()
    advisors_present = []
    if council_ok:
        for advisor in set(ADVISOR_CHANNELS.values()):
            if (COUNCIL_PATH / advisor).exists():
                advisors_present.append(advisor)
    return {
        "status": "ok",
        "service": "kai-council-api",
        "council_path_mounted": council_ok,
        "advisors_ready": sorted(advisors_present),
    }


@app.post("/council/message")
def council_message(req: MessageRequest, background_tasks: BackgroundTasks = None):
    import json as _mj
    channel = req.channel.lstrip("#")
    advisor = ADVISOR_CHANNELS.get(channel)
    if not advisor:
        raise HTTPException(status_code=400, detail=f"Unknown channel: {channel}")

    system_prompt = load_persona(advisor, channel)

    messages = req.history[-10:]
    messages.append({"role": "user", "content": req.message})

    total_input_tokens = 0
    total_output_tokens = 0
    raw_reply = ""

    # Determine provider/model — chief always uses Anthropic (tool-use requirement)
    if advisor == "chief":
        adv_cfg = {"provider": "anthropic", "model": "claude-sonnet-4-5"}
    else:
        adv_cfg = _get_advisor_config(advisor)

    provider = adv_cfg.get("provider", "anthropic")
    model    = adv_cfg.get("model", "claude-sonnet-4-5")
    actual_provider = provider
    actual_model    = model

    if provider == "anthropic":
        client = get_anthropic_client()
        tools = KAI_TOOLS if advisor == "chief" else []

        # Agentic loop — handles tool calls
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
                        result = execute_tool(block.name, block.input)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": _mj.dumps(result),
                        })
                messages.append({"role": "user", "content": tool_results})
                continue

            raw_reply = next(
                (b.text for b in response.content if hasattr(b, "text")), ""
            )
            break

    elif provider == "ollama":
        try:
            raw_reply, total_input_tokens, total_output_tokens = _call_ollama(
                model, system_prompt, messages
            )
        except Exception as ollama_err:
            # Fallback to Anthropic
            fallback_model = adv_cfg.get("fallback_model", "claude-sonnet-4-5")
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
            # Fallback to Anthropic
            fallback_model = adv_cfg.get("fallback_model", "claude-sonnet-4-5")
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

    # T1 privacy: insight extraction happens here on the worker, never leaves this process
    insights_logged = 0
    if advisor == "ember":
        clean_reply, insights = extract_and_strip_insights(raw_reply)
        insights_logged = append_insights_to_vault(insights)
    else:
        clean_reply = raw_reply

    # Log conversation history to vault
    _append_history(channel, "user", req.message)
    _append_history(channel, "assistant", clean_reply)

    # Track token usage
    _track_usage(advisor, total_input_tokens, total_output_tokens, actual_provider, actual_model)

    # Auto-summarize in background if history has grown enough
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



@app.post("/message")
def web_message(req: MessageRequest):
    """Web UI alias — nginx strips /council/ prefix."""
    return council_message(req)


# ── Model config API ──────────────────────────────────────────────────────────

@app.get("/models/config")
def get_model_config():
    return _load_model_config()

@app.patch("/models/config/advisor/{advisor_id}")
def update_advisor_model(advisor_id: str, body: dict):
    import json as _cmj
    config = _load_model_config()
    if "advisors" not in config:
        config["advisors"] = {}
    if advisor_id not in config["advisors"]:
        config["advisors"][advisor_id] = {}
    config["advisors"][advisor_id].update(body)
    MODEL_CONFIG_FILE.write_text(_cmj.dumps(config, indent=2))
    return {"ok": True, "advisor": advisor_id, "config": config["advisors"][advisor_id]}

@app.get("/models/status")
def get_model_status():
    """Returns provider availability status."""
    status = {}
    # Anthropic
    secret_path = Path("/run/secrets/anthropic_api_key")
    has_anthropic = secret_path.exists() or bool(os.environ.get("ANTHROPIC_API_KEY"))
    status["anthropic"] = {"available": has_anthropic, "label": "Anthropic Claude"}
    # OpenAI
    oai_path = Path("/run/secrets/openai_api_key")
    has_openai = oai_path.exists() or bool(os.environ.get("OPENAI_API_KEY"))
    status["openai"] = {"available": has_openai, "label": "OpenAI GPT"}
    # Ollama
    try:
        with httpx.Client(timeout=3) as hc:
            r = hc.get("http://kai-ollama:11434/api/tags")
            if r.status_code == 200:
                models = [m["name"] for m in r.json().get("models", [])]
                status["ollama"] = {"available": True, "label": "Ollama (Local)", "models": models}
            else:
                status["ollama"] = {"available": False, "label": "Ollama (Local)", "error": f"HTTP {r.status_code}"}
    except Exception as e:
        status["ollama"] = {"available": False, "label": "Ollama (Local)", "error": str(e)}
    return {"providers": status}


@app.post("/council/context/update")
def update_context(req: ContextUpdateRequest):
    advisor_dir = COUNCIL_PATH / req.advisor
    if not advisor_dir.exists():
        raise HTTPException(status_code=404, detail=f"Advisor not found: {req.advisor}")
    context_file = advisor_dir / "context.md"
    context_file.write_text(req.content, encoding="utf-8")
    return {"status": "updated", "advisor": req.advisor}


@app.get("/council/context/{advisor}")
def get_context(advisor: str):
    advisor_dir = COUNCIL_PATH / advisor
    context_file = advisor_dir / "context.md"
    if not context_file.exists():
        raise HTTPException(status_code=404, detail=f"Context not found for: {advisor}")
    return {"advisor": advisor, "content": context_file.read_text(encoding="utf-8")}


@app.get("/council/insights")
def get_insights():
    insights_file = COUNCIL_PATH / "ember" / "insights.md"
    if not insights_file.exists():
        raise HTTPException(status_code=404, detail="Insights file not found")
    return {"content": insights_file.read_text(encoding="utf-8")}


# ── Conversation History ──────────────────────────────────────────────────────

import json as _json
from datetime import datetime as _dt

HISTORY_DIR = VAULT_PATH / "60_Council" / "_history"


def _history_file(channel: str) -> Path:
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    return HISTORY_DIR / f"{channel}.jsonl"


def _append_history(channel: str, role: str, content: str):
    f = _history_file(channel)
    entry = {"role": role, "content": content, "ts": str(_dt.utcnow().timestamp())}
    with open(f, "a", encoding="utf-8") as fh:
        fh.write(_json.dumps(entry, ensure_ascii=False) + "\n")


@app.get("/history/{channel}")
def get_history(channel: str, limit: int = 50):
    f = _history_file(channel)
    if not f.exists():
        return {"messages": [], "channel": channel}
    lines = f.read_text(encoding="utf-8").strip().splitlines()
    messages = []
    for line in lines[-limit:]:
        try:
            messages.append(_json.loads(line))
        except Exception:
            pass
    return {"messages": messages, "channel": channel}


@app.delete("/history/{channel}")
def clear_history(channel: str):
    f = _history_file(channel)
    if f.exists():
        f.write_text("", encoding="utf-8")
    return {"ok": True, "channel": channel}
