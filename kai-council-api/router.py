import json
import logging
import re
from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel
import httpx
from council_config import ADVISOR_CHANNELS, WORKER_URL, ORCHESTRATOR_URL, _track_usage, _check_rate_limit, _worker_auth
from complexity import _classify_complexity, _get_advisor_config
from persona import load_persona
import function_map as fm
from insights import extract_and_strip_insights, append_insights_to_vault
from execute_tool import execute_tool
from providers import get_anthropic_client, _call_ollama, _call_openai, _call_litellm

logger = logging.getLogger(__name__)
router = APIRouter()


class MessageRequest(BaseModel):
    channel: str
    message: str
    user_id: str = ""
    history: list = []
    thread_ts: str = ""
    attachments: list = []  # [{type, media_type, data (base64), filename}]
    privacy_mode: bool = False
    trigger_source: str = ""  # e.g. "slack:dm", "telegram:dm", "dashboard:chat:kai"


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
    {"name": "list_tasks", "description": "List Leo's active tasks from Todoist — today's tasks and inbox. Use this before answering any question about what's on his plate, what's due, or how many tasks exist.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "complete_task", "description": "Mark a Todoist task as complete by its task ID.", "input_schema": {"type": "object", "properties": {"task_id": {"type": "string", "description": "The Todoist task ID to mark complete"}}, "required": ["task_id"]}},
    {"name": "create_task", "description": "Create a task in Todoist.", "input_schema": {"type": "object", "properties": {"content": {"type": "string"}, "due_date": {"type": "string"}, "priority": {"type": "integer"}, "description": {"type": "string"}}, "required": ["content"]}},
    {"name": "search_tasks", "description": "Search Leo's tasks by keyword. Use this to find a task ID before updating, rescheduling, or completing a task when you don't have the ID.", "input_schema": {"type": "object", "properties": {"query": {"type": "string", "description": "Keyword to search task content"}}, "required": ["query"]}},
    {"name": "update_task", "description": "Update an existing Todoist task — change its content, due date, or priority. Pass due_date as empty string to clear it.", "input_schema": {"type": "object", "properties": {"task_id": {"type": "string"}, "content": {"type": "string"}, "due_date": {"type": "string", "description": "ISO date YYYY-MM-DD or empty string to clear"}, "priority": {"type": "integer", "description": "1=urgent 2=high 3=medium 4=normal"}}, "required": ["task_id"]}},
    {"name": "delete_task", "description": "Permanently delete a Todoist task by its ID.", "input_schema": {"type": "object", "properties": {"task_id": {"type": "string"}}, "required": ["task_id"]}},
    {"name": "reschedule_task", "description": "Move a Todoist task to today or a specific date.", "input_schema": {"type": "object", "properties": {"task_id": {"type": "string"}, "due_date": {"type": "string", "description": "ISO date YYYY-MM-DD"}, "move_to_today": {"type": "boolean"}}, "required": ["task_id"]}},
    {"name": "list_todoist_projects", "description": "List all Todoist projects.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "create_todoist_project", "description": "Create a new project in Todoist.", "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},
    {"name": "delete_todoist_project", "description": "Delete a Todoist project by ID. Use list_todoist_projects first to get the ID.", "input_schema": {"type": "object", "properties": {"project_id": {"type": "string"}}, "required": ["project_id"]}},
    {"name": "create_project", "description": "Create a new project in KAI.", "input_schema": {"type": "object", "properties": {"id": {"type": "string"}, "name": {"type": "string"}, "description": {"type": "string"}, "status": {"type": "string", "enum": ["green","yellow","red"]}, "next": {"type": "string"}, "advisor": {"type": "string"}}, "required": ["id", "name", "description", "status"]}},
    {"name": "update_project", "description": "Update a project's status, next action, milestone, or pinned state.", "input_schema": {"type": "object", "properties": {"id": {"type": "string"}, "status": {"type": "string"}, "next": {"type": "string"}, "milestone": {"type": "string"}, "milestone_pct": {"type": "integer"}, "pinned": {"type": "boolean", "description": "true to pin, false to unpin"}}, "required": ["id"]}},
    {"name": "delete_project", "description": "Delete a KAI project by its ID.", "input_schema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}},
    {"name": "teardown_project", "description": "Full project teardown: removes from dashboard registry, archives Slack channel, moves vault folder to archived/. Use this when Leo says to delete or remove a project from everywhere.", "input_schema": {"type": "object", "properties": {"id": {"type": "string", "description": "project slug to tear down"}}, "required": ["id"]}},
    {"name": "list_projects", "description": "List all current projects with their status.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "write_to_vault", "description": "Write a document to the vault.", "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}, "description": {"type": "string"}}, "required": ["path", "content"]}},
    {"name": "read_vault", "description": "Read a file from the vault.", "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
    {"name": "read_workspace", "description": "Read a file from the Mac workspace mirror (~/sonicink/). Use this when Leo references logos, documents, markdown files, or any file in the sonicink workspace that is not in the vault. path is relative to the workspace root, e.g. wordpress/logos/revolt.png or wordpress/md/company_summaries.md. If the file is not found, tell Leo the workspace may need a sync: rsync -az --exclude=.git ~/sonicink/ kai:~/sonicink/", "input_schema": {"type": "object", "properties": {"path": {"type": "string", "description": "File path relative to workspace root (~/sonicink/)"}}, "required": ["path"]}},
    {"name": "list_workspace", "description": "List files in a directory of the Mac workspace mirror (~/sonicink/). Use to discover what logos, docs, or assets are available before reading them. path is relative to workspace root, e.g. wordpress/logos or wordpress/md.", "input_schema": {"type": "object", "properties": {"path": {"type": "string", "description": "Directory path relative to workspace root. Defaults to root if omitted."}}, "required":[]}},
    {"name": "send_slack_message", "description": "Post a message to a Slack channel.", "input_schema": {"type": "object", "properties": {"channel": {"type": "string"}, "message": {"type": "string"}, "advisor": {"type": "string"}}, "required": ["channel", "message"]}},
    {"name": "deliver_asset", "description": "Send a design asset, deliverable, spec doc, wireframe, or any generated file to Leo via Slack DM. The file is versioned and persisted in the vault under vault/60_Council/<advisor>/deliverables/<slug>/v<n>.<ext>. Use this anytime Creative, Dev, or any other advisor produces a file artifact (logo, moodboard, comp, spec, wireframe, doc). The message is posted from KAI with a 'Beats says:' / 'Dev says:' attribution prefix; the file is attached so it lands in Leo's Slack Files panel.", "input_schema": {"type": "object", "properties": {"advisor": {"type": "string", "description": "Which advisor produced the asset (e.g. creative, dev, beats). Used for attribution prefix + vault path."}, "context": {"type": "string", "description": "One-line context that goes in the DM message (e.g. 'Homepage moodboard v3 — incorporated warmer palette')."}, "source_path": {"type": "string", "description": "Path to the source file (absolute, or relative to /vault)."}, "slug": {"type": "string", "description": "Asset slug (lowercase, hyphenated). Defaults to source filename stem."}, "ext": {"type": "string", "description": "File extension without dot. Defaults to source file's suffix."}}, "required": ["advisor", "context", "source_path"]}},
    {"name": "get_advisor_recent_dms", "description": "Read recent direct-DM exchanges between Leo and Sky or Roads. Use this when Leo asks 'what's Sky been telling me lately' or wants to surface anything from a direct-advisor conversation. Returns the last N {user_id, message, reply, ts} entries.", "input_schema": {"type": "object", "properties": {"advisor": {"type": "string", "description": "sky or roads"}, "n": {"type": "integer", "description": "How many recent exchanges to return. Defaults to 20."}}, "required": ["advisor"]}},
    {"name": "start_mission", "description": "Record the start of an autonomous mission.", "input_schema": {"type": "object", "properties": {"name": {"type": "string"}, "scope": {"type": "array", "items": {"type": "string"}}, "notes": {"type": "string"}}, "required": ["name", "scope"]}},
    {"name": "complete_mission", "description": "Mark the current mission complete and compile the review briefing.", "input_schema": {"type": "object", "properties": {"built": {"type": "array", "items": {"type": "object"}}, "decisions": {"type": "array", "items": {"type": "string"}}}, "required": ["built"]}},
    {"name": "log_action", "description": "Log a governance action.", "input_schema": {"type": "object", "properties": {"action": {"type": "string"}, "tier": {"type": "integer"}, "approved_by": {"type": "string"}}, "required": ["action", "tier", "approved_by"]}},
    {"name": "get_calendar", "description": "Get upcoming calendar events across the next N days.", "input_schema": {"type": "object", "properties": {"days": {"type": "integer"}, "calendar_id": {"type": "string"}}}},
    {"name": "create_event", "description": "Create a Google Calendar event.", "input_schema": {"type": "object", "properties": {"title": {"type": "string"}, "start": {"type": "string"}, "end": {"type": "string"}, "description": {"type": "string"}, "location": {"type": "string"}, "calendar_id": {"type": "string"}}, "required": ["title", "start", "end"]}},
    {"name": "save_session", "description": "Save a structured summary of the current conversation session.", "input_schema": {"type": "object", "properties": {"title": {"type": "string"}, "topics": {"type": "array", "items": {"type": "string"}}, "decisions": {"type": "array", "items": {"type": "string"}}, "actions": {"type": "array", "items": {"type": "string"}}, "context": {"type": "string"}, "channel": {"type": "string"}}, "required": ["title", "topics"]}},
    {"name": "log_decision", "description": "Log a key decision to the decisions vault.", "input_schema": {"type": "object", "properties": {"decision": {"type": "string"}, "context": {"type": "string"}, "outcome": {"type": "string"}, "channel": {"type": "string"}}, "required": ["decision", "context"]}},
    {"name": "ingest_knowledge", "description": "Ingest files from a knowledge folder into an advisor's memory. Use when Leo says he added notes, a new document, or wants to update what an advisor knows. Defaults to the current advisor's folder.", "input_schema": {"type": "object", "properties": {"advisor": {"type": "string", "description": "Which advisor collection to update (beats, sky, roads, ember, doc, kai, etc.)"}, "path": {"type": "string", "description": "Optional specific file or folder path to ingest. Defaults to ~/vault/60_Council/<advisor>/knowledge"}}, "required": []}},
    {"name": "list_knowledge", "description": "List all advisor knowledge collections and how many items are in each.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "clear_knowledge", "description": "Clear all vectors from an advisor knowledge collection. Use only when explicitly asked to wipe and rebuild.", "input_schema": {"type": "object", "properties": {"advisor": {"type": "string"}}, "required": ["advisor"]}},
    {"name": "trigger_n8n_workflow", "description": "Trigger an n8n workflow by name.", "input_schema": {"type": "object", "properties": {"workflow": {"type": "string"}, "payload": {"type": "object"}}, "required": ["workflow"]}},
    {"name": "list_n8n_workflows", "description": "List all registered n8n workflows KAI can trigger.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "register_n8n_workflow", "description": "Register a new n8n workflow webhook URL.", "input_schema": {"type": "object", "properties": {"name": {"type": "string"}, "webhook_url": {"type": "string"}, "description": {"type": "string"}}, "required": ["name", "webhook_url"]}},
    {"name": "list_specialists", "description": "List all specialist personas.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "consult_specialist", "description": "Brief a team member to execute work in their domain. For directors (creative, dev, devops): this is the execution mechanism — not optional input, how work gets produced. Call this instead of doing the work yourself. copywriter=copy/taglines/messaging, designer=UI/web/interface, graphic-designer=visual assets, strategist=positioning/brand strategy, architect=system design, researcher=deep research, pm=planning. For KAI as orchestrator: use this to engage any advisor or specialist. Use list_specialists to see all available.", "input_schema": {"type": "object", "properties": {"specialist": {"type": "string"}, "question": {"type": "string"}, "context": {"type": "string"}}, "required": ["specialist", "question"]}},
    {"name": "read_email", "description": "Read recent emails from Gmail.", "input_schema": {"type": "object", "properties": {"max_results": {"type": "integer"}, "query": {"type": "string"}}}},
    {"name": "draft_email", "description": "Create an email draft in Gmail.", "input_schema": {"type": "object", "properties": {"to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"}}, "required": ["to", "subject", "body"]}},
    {"name": "setup_project", "description": "Full project creation pipeline: creates vault folder, Slack channel, Gmail drafts for external invitees. project_type='active' (default) registers on dashboard + creates Slack channel; project_type='idea' stores in vault/20_Projects/ideas/ only — no dashboard entry, no Slack. BEHAVIOR: (1) For any person mentioned, call lookup_contact first, then lookup_google_contact if not found — never ask Leo for an email you can resolve. (2) Default file upload location is Slack unless Leo says otherwise. (3) If both lookups return nothing, use whatever info Leo provided and proceed. Never ask for clarification — act and report.", "input_schema": {"type": "object", "properties": {"id": {"type": "string", "description": "short slug, e.g. project-x"}, "name": {"type": "string"}, "description": {"type": "string"}, "advisor": {"type": "string"}, "project_type": {"type": "string", "enum": ["active", "idea"], "description": "active = live project on dashboard with Slack; idea = stored in ideas/ folder only"}, "status": {"type": "string", "enum": ["green","yellow","red"]}, "next": {"type": "string"}, "create_slack_channel": {"type": "boolean", "default": True}, "slack_channel_name": {"type": "string"}, "invite_contacts": {"type": "array", "items": {"type": "string"}, "description": "Internal contacts by name or ID"}, "external_invites": {"type": "array", "items": {"type": "object", "properties": {"email": {"type": "string"}, "name": {"type": "string"}}, "required": ["email"]}, "description": "External collaborators — Gmail drafts sent to each"}, "file_request_message": {"type": "string", "description": "Message to include in invite and Slack welcome about file uploads. Default: drop files in the Slack channel."}}, "required": ["id", "name"]}},
    {"name": "create_slack_channel", "description": "Create a new Slack channel.", "input_schema": {"type": "object", "properties": {"name": {"type": "string"}, "private": {"type": "boolean"}}, "required": ["name"]}},
    {"name": "invite_to_slack_channel", "description": "Invite people to a Slack channel (Tier 2).", "input_schema": {"type": "object", "properties": {"channel": {"type": "string"}, "emails": {"type": "array", "items": {"type": "string"}}, "contact_names": {"type": "array", "items": {"type": "string"}}}, "required": ["channel"]}},
    {"name": "lookup_contact", "description": "Look up a person in the contacts registry.", "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "add_contact", "description": "Add a new person to the contacts registry.", "input_schema": {"type": "object", "properties": {"name": {"type": "string"}, "email": {"type": "string"}, "role": {"type": "string"}, "slack_id": {"type": "string"}, "aliases": {"type": "array", "items": {"type": "string"}}}, "required": ["name"]}},
    {"name": "get_o365_calendar", "description": "Fetch calendar events from Revolt and/or Penn State O365.", "input_schema": {"type": "object", "properties": {"days": {"type": "integer"}}}},
    {"name": "web_search", "description": "Search the web for current information using Tavily.", "input_schema": {"type": "object", "properties": {"query": {"type": "string"}, "max_results": {"type": "integer"}}, "required": ["query"]}},
    {"name": "lookup_google_contact", "description": "Search Leo's Google Contacts.", "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "request_t2_approval", "description": "Queue any action that requires Leo's explicit approval via Slack.", "input_schema": {"type": "object", "properties": {"action": {"type": "string"}, "detail": {"type": "string"}, "slack_channel": {"type": "string"}}, "required": ["action", "detail"]}},
    {"name": "run_capability", "description": "Execute any registered KAI orchestrator capability by name. Use list_capabilities first to see available names and whether confirmation is required. For destructive capabilities (vault.write, session.close, workspace.sync) you must pass confirmed=true. Examples: run_capability('vault.read', {'path': 'file.md'}), run_capability('slack.post', {'channel': 'kai-system', 'text': 'hello'}). Returns ok, data, error fields.", "input_schema": {"type": "object", "properties": {"capability": {"type": "string", "description": "Capability name, e.g. vault.read, vault.write, workspace.sync, slack.post"}, "inputs": {"type": "object", "description": "Inputs specific to the capability (path, content, channel, etc.)"}, "confirmed": {"type": "boolean", "description": "Set true to confirm destructive operations (vault.write, session.close, workspace.sync)"}}, "required": ["capability"]}},
    {"name": "list_capabilities", "description": "List all registered KAI orchestrator capabilities — names, whether they are destructive, read-only, and rate limits. Use before run_capability to check what's available and whether confirmation is required.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "get_system_health", "description": "Get live KAI system state: failing invariants, backup status, cron health. Use this before answering any question about system status, or whenever Leo reports failures or alerts. Returns real-time data from the invariants engine.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "run_backup_now", "description": "Trigger the Plane DB + vault backup immediately. Also restores the cron entry if it is missing. Use when Leo reports backup failures or when get_system_health shows backup is stale.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "restore_backup_cron", "description": "Restore the backup cron entry if it is missing. Idempotent — safe to call even if cron is already present.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "get_oura_data", "description": "Fetch Leo's Oura Ring health data.", "input_schema": {"type": "object", "properties": {"data_type": {"type": "string", "enum": ["readiness", "sleep", "activity", "all"]}, "days": {"type": "integer"}}, "required": ["data_type"]}},
    {"name": "wordpress_get_posts", "description": "Fetch recent posts from a WordPress site.", "input_schema": {"type": "object", "properties": {"site": {"type": "string"}, "count": {"type": "integer"}, "status": {"type": "string"}}, "required": ["site"]}},
    {"name": "wordpress_create_post", "description": "Create a WordPress post (draft by default).", "input_schema": {"type": "object", "properties": {"site": {"type": "string"}, "title": {"type": "string"}, "content": {"type": "string"}, "status": {"type": "string"}, "tags": {"type": "array", "items": {"type": "string"}}, "excerpt": {"type": "string"}}, "required": ["site", "title", "content"]}},
    {"name": "wordpress_create_page", "description": "Create a WordPress page. Use template=kai-blank for full design control. IMPORTANT: content must be body HTML only — never include <!DOCTYPE>, <html>, <head>, or <body> tags. The tool will hard-reject full HTML documents. Include a unique UUID comment in your content (e.g. <!-- kai-build:uuid -->) so wordpress_verify_live can confirm it is rendering. Status: draft or publish.", "input_schema": {"type": "object", "properties": {"site": {"type": "string"}, "title": {"type": "string"}, "content": {"type": "string", "description": "Body HTML only — no DOCTYPE, html, head, or body tags. Include a UUID comment marker for live verification."}, "status": {"type": "string", "enum": ["draft", "publish"]}, "slug": {"type": "string"}, "template": {"type": "string", "description": "Use kai-blank for full design control, empty string for default theme template"}}, "required": ["site", "title", "content"]}},
    {"name": "wordpress_get_pages", "description": "List all pages on a WordPress site.", "input_schema": {"type": "object", "properties": {"site": {"type": "string"}, "count": {"type": "integer"}, "status": {"type": "string"}}, "required": ["site"]}},
    {"name": "wordpress_update_post", "description": "Update an existing WordPress post or page by ID.", "input_schema": {"type": "object", "properties": {"site": {"type": "string"}, "post_id": {"type": "integer"}, "title": {"type": "string"}, "content": {"type": "string"}, "status": {"type": "string"}, "post_type": {"type": "string", "enum": ["posts", "pages"]}}, "required": ["site", "post_id"]}},
    {"name": "wordpress_publish", "description": "Publish a draft post or page on a WordPress site.", "input_schema": {"type": "object", "properties": {"site": {"type": "string"}, "post_id": {"type": "integer"}, "post_type": {"type": "string", "enum": ["posts", "pages"]}}, "required": ["site", "post_id"]}},
    {"name": "wordpress_get_post", "description": "Get the full content of a specific WordPress post or page by ID.", "input_schema": {"type": "object", "properties": {"site": {"type": "string"}, "post_id": {"type": "integer"}, "post_type": {"type": "string", "enum": ["posts", "pages"]}}, "required": ["site", "post_id"]}},
    {"name": "wordpress_get_site_info", "description": "Get site title, description, and a full list of existing pages. Call this before designing or updating a site.", "input_schema": {"type": "object", "properties": {"site": {"type": "string"}}, "required": ["site"]}},
    {"name": "wordpress_set_custom_css", "description": "Set site-wide custom CSS. Used to apply global typography, color variables, and base styles across all pages.", "input_schema": {"type": "object", "properties": {"site": {"type": "string"}, "css": {"type": "string"}}, "required": ["site", "css"]}},
    {"name": "wordpress_list_sites", "description": "List all registered WordPress sites with IDs, URLs, and business associations.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "wordpress_create_task", "description": "Create a new tracked WordPress build task. Returns a task_id that must be passed to every subsequent WP build tool call to enforce the required state sequence: Dev review → Coming Soon off → homepage set → Creative review → content written → content verified → cache purged → live verified → DevOps review → complete.", "input_schema": {"type": "object", "properties": {"site": {"type": "string"}, "type": {"type": "string", "enum": ["page", "post", "redesign", "css"]}, "title": {"type": "string"}, "brief": {"type": "string"}, "priority": {"type": "string", "enum": ["high", "normal", "low"]}}, "required": ["site", "type", "title", "brief"]}},
    {"name": "wordpress_complete_task", "description": "Advance a task from devops_approved to complete. Only valid after DevOps council review. Call this as the final step of a build.", "input_schema": {"type": "object", "properties": {"task_id": {"type": "string"}}, "required": ["task_id"]}},
    {"name": "wordpress_request_council", "description": "Request a real council review for a WP build task. Routes to the dev, creative, or devops specialist, gets their actual direction, stamps a consultation record proving the council was consulted, and returns the raw direction. ALWAYS call this before wordpress_council_review. Dev: call at created state. Creative: call at homepage_set. DevOps: call at live_verified.", "input_schema": {"type": "object", "properties": {"task_id": {"type": "string"}, "council": {"type": "string", "enum": ["dev", "creative", "devops"]}}, "required": ["task_id", "council"]}},
    {"name": "wordpress_council_review", "description": "Record a council direction and advance the task state. REQUIRES a token from wordpress_request_council — tokens are single-use and task+council specific. council must be 'dev', 'creative', or 'devops'. direction is the council's actual advice, permanently logged and fed into future reviews.", "input_schema": {"type": "object", "properties": {"task_id": {"type": "string"}, "council": {"type": "string", "enum": ["dev", "creative", "devops"]}, "direction": {"type": "string", "description": "The council's full direction for this build — logged permanently"}, "token": {"type": "string", "description": "Consultation token returned by wordpress_request_council — required, single-use"}, "approved": {"type": "boolean", "description": "True to approve and advance state, false to reject and fail the task"}}, "required": ["task_id", "council", "direction", "token"]}},
    {"name": "wordpress_review_feedback", "description": "Attach Leo's feedback to a council's direction on a task. This is the learning signal — feedback is stored alongside the council direction and injected into future council prompts so the system improves over time.", "input_schema": {"type": "object", "properties": {"task_id": {"type": "string"}, "council": {"type": "string", "enum": ["dev", "creative", "devops"]}, "feedback": {"type": "string", "description": "Leo's assessment of the council's direction — what was right, wrong, or missing"}}, "required": ["task_id", "council", "feedback"]}},
    {"name": "wordpress_get_council_history", "description": "Return past direction and Leo's feedback for a council across all tasks. Use this to understand how a council has directed past builds and what Leo said about it — the learning record.", "input_schema": {"type": "object", "properties": {"council": {"type": "string", "enum": ["dev", "creative", "devops"]}, "limit": {"type": "integer", "description": "Max records to return, default 10"}}, "required": ["council"]}},
    {"name": "wordpress_get_task", "description": "Get the full state history, transitions, and council reviews for a specific task.", "input_schema": {"type": "object", "properties": {"task_id": {"type": "string"}}, "required": ["task_id"]}},
    {"name": "wordpress_list_tasks", "description": "List WordPress build tasks, optionally filtered by site or state.", "input_schema": {"type": "object", "properties": {"site": {"type": "string"}, "state": {"type": "string", "enum": ["created", "dev_approved", "cs_disabled", "homepage_set", "creative_approved", "content_written", "content_verified", "cache_purged", "live_verified", "devops_approved", "complete", "failed"]}, "limit": {"type": "integer"}}, "required": []}},
    {"name": "wordpress_read_file", "description": "Read a file from a WordPress site's webroot via SSH (cs.html, .htaccess, mu-plugins, theme files). Path is relative to public_html. Use to inspect static files that bypass the REST API. Max 512KB.", "input_schema": {"type": "object", "properties": {"site": {"type": "string"}, "path": {"type": "string", "description": "Path relative to public_html, e.g. 'cs.html' or 'wp-content/mu-plugins/kai-blank-canvas.php'"}}, "required": ["site", "path"]}},
    {"name": "wordpress_write_file", "description": "Write/overwrite a file in a WordPress site's webroot via SSH. Auto-backs-up the prior version as {path}.bak_{timestamp}. Atomic via mktemp+mv. Path sandboxed to public_html. Use for cs.html, .htaccess, mu-plugins, etc. Max 512KB.", "input_schema": {"type": "object", "properties": {"site": {"type": "string"}, "path": {"type": "string"}, "content": {"type": "string"}}, "required": ["site", "path", "content"]}},
    {"name": "wordpress_list_files", "description": "List files in a directory inside a WordPress site's webroot. Path is relative to public_html; empty path lists the webroot itself. Returns ls -la style lines.", "input_schema": {"type": "object", "properties": {"site": {"type": "string"}, "path": {"type": "string"}}, "required": ["site"]}},
    {"name": "wordpress_delete_file", "description": "Delete a file in a WordPress site's webroot. Backs up to {path}.bak_{timestamp} before deletion so recovery is possible. Path sandboxed to public_html.", "input_schema": {"type": "object", "properties": {"site": {"type": "string"}, "path": {"type": "string"}}, "required": ["site", "path"]}},
    {"name": "wordpress_purge_varnish", "description": "Purge Cloudways Varnish cache for a WordPress site. Purges both the custom domain and the Cloudways FQDN host keys. Call after any page publish or file write.", "input_schema": {"type": "object", "properties": {"site": {"type": "string"}, "url_path": {"type": "string", "description": "URL path to purge, default '/'"}}, "required": ["site"]}},
    {"name": "wordpress_update_settings", "description": "Update WordPress site settings via the REST API. Use to set page_on_front (page ID), show_on_front ('page' or 'posts'), blogname, blogdescription, or any registered WP option. Call this to make a page the front page.", "input_schema": {"type": "object", "properties": {"site": {"type": "string"}, "show_on_front": {"type": "string", "enum": ["page", "posts"]}, "page_on_front": {"type": "integer", "description": "Page ID to use as front page — set show_on_front=page too"}, "page_for_posts": {"type": "integer"}, "blogname": {"type": "string"}, "blogdescription": {"type": "string"}}, "required": ["site"]}},
    {"name": "wordpress_set_option", "description": "Set any WordPress option via WP-CLI over SSH. Use to toggle kai_cs_active (1=coming soon active, 0=disabled so KAI-built pages show). Call wordpress_set_option site=X option_name=kai_cs_active option_value=0 before setting a KAI page as front page.", "input_schema": {"type": "object", "properties": {"site": {"type": "string"}, "option_name": {"type": "string"}, "option_value": {"type": "string"}}, "required": ["site", "option_name", "option_value"]}},
    {"name": "wordpress_get_page_content", "description": "Read the raw stored content of a WordPress page by ID. Returns the content as stored in the database (edit context), not the rendered HTML. Use this to verify WP actually accepted the content you wrote, separate from what is rendering live.", "input_schema": {"type": "object", "properties": {"site": {"type": "string"}, "page_id": {"type": "integer"}}, "required": ["site", "page_id"]}},
    {"name": "wordpress_verify_live", "description": "Fetch the live public URL of a WordPress site and verify what is actually rendering. Returns HTTP status, page title, whether a content marker is present, and whether Coming Soon is active. Always call this after publishing a page. Pass the UUID marker you embedded in your content to confirm it is live.", "input_schema": {"type": "object", "properties": {"site": {"type": "string"}, "url_path": {"type": "string", "description": "URL path to check, default /"}, "marker": {"type": "string", "description": "UUID string embedded in your page content — verify_live will confirm it appears in the live response"}}, "required": ["site"]}},

    {"name": "get_plane_issues", "description": "List projects, or list/fetch issues in Plane PM. Omit all args to list projects. Pass project_id to list issues. Pass project_id + issue_id for a specific issue.", "input_schema": {"type": "object", "properties": {"project_id": {"type": "string"}, "issue_id": {"type": "string"}, "state": {"type": "string"}}}},
    {"name": "update_plane_issue", "description": "Update a Plane issue — change state, add description, update name, etc.", "input_schema": {"type": "object", "properties": {"project_id": {"type": "string"}, "issue_id": {"type": "string"}, "name": {"type": "string"}, "description_html": {"type": "string"}, "state": {"type": "string"}}, "required": ["project_id", "issue_id"]}},
    {"name": "create_plane_issue", "description": "Create a new issue in Plane PM.", "input_schema": {"type": "object", "properties": {"project_id": {"type": "string"}, "name": {"type": "string"}, "description_html": {"type": "string"}, "priority": {"type": "string"}}, "required": ["project_id", "name"]}},
    {"name": "wordpress_override", "description": "Force a WP build task to a target state, bypassing the normal gate sequence. USE ONLY when the protocol cannot be followed for a legitimate reason. Requires an explicit reason — vague reasons are rejected. Every override is permanently logged with the skipped gates identified. Override frequency is surfaced in audit reports to tune the protocol.", "input_schema": {"type": "object", "properties": {"task_id": {"type": "string"}, "target_state": {"type": "string", "enum": ["dev_approved", "cs_disabled", "homepage_set", "creative_approved", "content_written", "content_verified", "cache_purged", "live_verified", "devops_approved", "complete", "failed"]}, "reason": {"type": "string", "description": "Explicit reason for the override — required and logged permanently"}, "authorized_by": {"type": "string", "description": "Who authorized this override, default leo"}}, "required": ["task_id", "target_state", "reason"]}},
    {"name": "wordpress_audit_report", "description": "Return audit data for the WP enforcement system. report_type=task_history: full timeline for one task (transitions, council reviews, consultations, overrides). report_type=override_frequency: which gates are being skipped most — signal to tune the protocol. report_type=council_effectiveness: Leo feedback patterns per council — signal to tune persona rules.", "input_schema": {"type": "object", "properties": {"task_id": {"type": "string", "description": "Required for task_history report type"}, "report_type": {"type": "string", "enum": ["task_history", "override_frequency", "council_effectiveness"]}}, "required": ["report_type"]}},
    {"name": "add_to_parking_lot", "description": "Save an item to Leo's Parking Lot. Only call this when Leo explicitly asks to save or capture something. Do not use it to defer answering a question or handle topics you are uncertain about.", "input_schema": {"type": "object", "properties": {"content": {"type": "string"}, "source": {"type": "string"}}, "required": ["content"]}},
    {"name": "list_templates", "description": "List available project template versions.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "submit_job", "description": "Submit a job to the KAI orchestrator for tracked async execution. Use for any multi-step work that should be tracked, retried, and reported. Pass workflow='capability_chain' with a chain array for ad-hoc capability sequences. Pass workflow='wordpress.publish_homepage' for WP homepage builds. Omit workflow and pass intent to let the orchestrator infer the type.", "input_schema": {"type": "object", "properties": {"workflow": {"type": "string", "description": "Workflow type, e.g. capability_chain, wordpress.publish_homepage. Omit to infer from intent."}, "intent": {"type": "string", "description": "Natural language intent used to infer workflow type when workflow is not specified."}, "title": {"type": "string", "description": "Human-readable job title shown in Slack notifications."}, "inputs": {"type": "object", "description": "Inputs for the workflow. For capability_chain, include a chain array."}}}},
    {"name": "get_job_status", "description": "Get the current status of a submitted job, including step-level detail.", "input_schema": {"type": "object", "properties": {"job_id": {"type": "string", "description": "The job ID returned by submit_job."}}, "required": ["job_id"]}},
    {"name": "list_jobs", "description": "List recent orchestrator jobs. Optionally filter by status: queued, running, succeeded, failed_permanent, cancelled.", "input_schema": {"type": "object", "properties": {"limit": {"type": "integer", "description": "Max results (default 10)."}, "status": {"type": "string", "description": "Filter by status."}}}},
]

DIRECTOR_TOOLS = [t for t in KAI_TOOLS if t["name"] == "consult_specialist"]



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
                timeout=10,
                auth=_worker_auth(),  # Bug 48f85706: worker authenticates all routes
            )
            _track_usage(advisor, 0, 0, "anthropic", "auto-capture",
                         trigger_source="council:auto_capture")
            return {"reply": "Saved to your parking lot.", "advisor": advisor, "model": "auto-capture", "usage": {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0}}
        except Exception as e:
            logger.exception("auto_capture: %s", e)
    return None


def _run_agentic_loop(messages: list, tools: list, model: str, system_prompt: str, advisor: str,
                       cache_breakpoint_chars: int = 0) -> tuple:
    """Run Anthropic agentic loop. Returns (reply, input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens)."""
    client = get_anthropic_client()
    total_input_tokens = 0
    total_output_tokens = 0
    total_cache_read_tokens = 0
    total_cache_creation_tokens = 0
    raw_reply = ""

    # Prompt caching (CONTEXT_SPEC §7): cache_breakpoint_chars is the length of
    # the Memory Service package's Tier 5 stable_text (context_service.
    # tier5_standing_context(), §3/§13) — an explicit index, not a substring
    # search. Everything after it (datetime, system_state, session_memory,
    # conversation_summary, Tier 3/4 recall/facts, Tier 1 messages) stays
    # uncached by construction.
    if cache_breakpoint_chars > 0:
        system: list | str = [
            {"type": "text", "text": system_prompt[:cache_breakpoint_chars], "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": system_prompt[cache_breakpoint_chars:].strip()},
        ]
    else:
        system = system_prompt

    while True:
        kwargs = dict(
            model=model,
            max_tokens=2048,
            system=system,
            messages=messages,
        )
        if tools:
            cached_tools = [*tools]
            if cached_tools:
                cached_tools[-1] = {**cached_tools[-1], "cache_control": {"type": "ephemeral"}}
            kwargs["tools"] = cached_tools

        response = client.messages.create(**kwargs)
        total_input_tokens          += response.usage.input_tokens
        total_output_tokens         += response.usage.output_tokens
        total_cache_read_tokens     += getattr(response.usage, "cache_read_input_tokens", 0) or 0
        total_cache_creation_tokens += getattr(response.usage, "cache_creation_input_tokens", 0) or 0

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

    return raw_reply, total_input_tokens, total_output_tokens, total_cache_read_tokens, total_cache_creation_tokens



def _maybe_resolve_gate(message: str, user_id: str) -> str | None:
    """Detect approve/reject gate commands and resolve the gate."""
    msg = message.strip()
    approve_m = re.match(r'^approve\s+([\w\-]{4,})\s*$', msg, re.IGNORECASE)
    reject_m  = re.match(r'^reject\s+([\w\-]{4,}):\s*(.+)$', msg, re.IGNORECASE)
    if not approve_m and not reject_m:
        return None
    try:
        from routes_council_gate import (
            _GATES_STORE,
            _fire_callback,
            _persist_gate_record,
            _update_gate,
        )
        from datetime import datetime, timezone
        if approve_m:
            gate_id = approve_m.group(1)
            approved, notes = True, "Approved by Leo"
        else:
            gate_id = reject_m.group(1)
            approved, notes = False, reject_m.group(2).strip()

        entry = _GATES_STORE.get(gate_id)
        if entry is None:
            return f"Gate {gate_id} not found — it may have expired or already been resolved."
        if entry["status"] not in ("pending_leo", "processing"):
            return f"Gate {gate_id} is already in state {entry['status']}."

        resolution = {
            "approved":   approved,
            "notes":      notes,
            "advisor":    user_id,
            "resolved_at": datetime.now(timezone.utc).isoformat(),
        }
        entry = _update_gate(gate_id, status="resolved", resolution=resolution)
        _persist_gate_record(gate_id, entry["gate_type"], entry["brief"], resolution)
        _fire_callback(entry["callback_url"], resolution)

        action = "approved ✓" if approved else "rejected ✗"
        return f"Gate {gate_id} {action}. Workflow {'will continue.' if approved else f'stopped. Reason: {notes}'}"
    except Exception as e:
        logger.exception("Gate resolve via message failed: %s", e)
        return f"Gate resolve failed: {e}"

@router.post("/council/message")
def council_message(req: MessageRequest, background_tasks: BackgroundTasks = None):
    channel = req.channel.lstrip("#")
    advisor = ADVISOR_CHANNELS.get(channel)
    if not advisor:
        raise HTTPException(status_code=400, detail=f"Unknown channel: {channel}")

    # Gate approval detection — Leo types "approve gate_id" or "reject gate_id: reason"
    _gate_result = _maybe_resolve_gate(req.message, req.user_id or "leo")
    if _gate_result:
        return {"advisor": "kai", "channel": channel, "reply": _gate_result,
                "insights_logged": 0, "input_tokens": 0, "output_tokens": 0,
                "provider": "system", "model": "gate-resolver"}

    # Try auto-capture shortcut
    auto = _handle_auto_capture(req.message, advisor)
    if auto:
        return auto

    # Rate limit check — tiered budget (S5R-19)
    # Non-interactive sources (scheduler, orchestrator, webhooks) are explicitly labelled;
    # unlabelled traffic defaults to "interactive" so it is never silently exempted from
    # the interactive budget cap (H-1 / S5R-29).
    _NON_INTERACTIVE = ("scheduler:", "orchestrator:", "webhook:", "n8n:", "cron:")
    _traffic_type = (
        "alert"
        if req.trigger_source and req.trigger_source.startswith(_NON_INTERACTIVE)
        else "interactive"
    )
    rl = _check_rate_limit(advisor, traffic_type=_traffic_type)
    if rl["blocked"]:
        return {"advisor": advisor, "channel": channel, "reply": rl["reason"],
                "insights_logged": 0, "input_tokens": 0, "output_tokens": 0,
                "provider": "rate-limit", "model": "rate-limit"}

    if req.history:
        raise HTTPException(
            status_code=400,
            detail="history is server-owned — see CONTEXT_SPEC §4.1. Do not send a history "
                   "field; the service assembles context server-side via assemble()/record_turn().",
        )

    # Memory Service (CONTEXT_SPEC §4/§5/§13) — single assemble() call now
    # carries all five tiers: server-owned conversation state (Tier 1+2,
    # replacing client-supplied history — BUG e5e54431/F1), Tier 3 recall,
    # Tier 4 facts, and Tier 5 standing context (persona/voice, KEYSTONE/org/
    # style, datetime, system_state) — migrated off the local
    # persona.assemble_prompt() path per §3 ("persona.py ceases to be an
    # assembly point"). Disclosed tradeoff: orchestrator downtime now fails
    # the whole turn (no persona either), where previously persona assembly
    # was local and survived a Memory Service outage degraded-but-functional.
    # That's the intended effect of consolidating onto one assembly path, not
    # a silent regression — Tier 1-4 already had this dependency; this
    # extends it to persona/voice too, one motion instead of two.
    _device = req.trigger_source or req.user_id or f"unknown:{channel}"
    _conv_key = {"advisor": advisor, "device": _device, "place": None, "thread": req.thread_ts or None}
    try:
        _assemble_resp = httpx.post(
            f"{ORCHESTRATOR_URL}/context/assemble",
            json={"key": _conv_key, "message": req.message, "channel": channel},
            timeout=15,
        )
    except httpx.HTTPError as e:
        logger.exception("context.assemble unreachable: %s", e)
        raise HTTPException(status_code=502, detail=f"Memory Service unreachable: {e}")
    if _assemble_resp.status_code == 404:
        raise HTTPException(status_code=404,
                             detail=_assemble_resp.json().get("detail", f"Persona not found: {advisor}"))
    _assemble_resp.raise_for_status()
    _package = _assemble_resp.json()["package"]

    system_prompt = _package["stable_text"] + (
        "\n\n---\n\n" + _package["volatile_text"] if _package.get("volatile_text") else ""
    )
    _cache_breakpoint_chars = len(_package["stable_text"])
    messages = list(_package["messages"])

    # KAI is PM; when she receives a message, classify the domain via the
    # function map and surface the matched advisor as a hint. KAI decides
    # whether to consult_specialist — this just stops her from improvising
    # which advisor owns the domain.
    if advisor == "kai":
        _dh = fm.get_advisor_for_domain(req.message or "")
        if _dh.get("advisor"):
            system_prompt += (
                f"\n\n<domain_hint>"
                f"\nThis message matches domain '{_dh['domain']}' "
                f"(keyword: '{_dh['matched_keyword']}'). "
                f"Primary advisor for this domain: {_dh['advisor']}. "
                f"Use consult_specialist or pull in {_dh['advisor']} if domain knowledge is needed."
                f"\n</domain_hint>"
            )
            logger.info("router: domain_hint channel=%s domain=%s advisor=%s kw=%s",
                        channel, _dh["domain"], _dh["advisor"], _dh["matched_keyword"])

    if _package.get("summary"):
        system_prompt += f"\n\n<conversation_summary>\n{_package['summary']}\n</conversation_summary>"
    # Tier 4 verified facts (CONTEXT_SPEC §5/§10) — placed before Tier 3 recall
    # so a registry fact reads as authoritative ahead of a conflicting recalled
    # snippet; facts_text carries its own <trust_rubric> stating that precedence
    # explicitly (a position convention isn't reliable enough on its own). Per
    # §7 Tier 4 should sit in the STABLE block (before the cache breakpoint,
    # since verified facts change rarely) — it is not yet: cache_breakpoint_chars
    # is computed from Tier 5's stable_text before facts_text is appended, so
    # facts_text lands in the volatile tail like Tier 2/3 today. Known deviation
    # from §7, not a bug (v1.5) — moving it into the stable prefix is tracked as
    # the Tier 4 cache-shaping follow-on, out of scope for the Tier 5 increment.
    if _package.get("facts_text"):
        system_prompt += f"\n\n{_package['facts_text']}"
    # Tier 3 semantic recall (CONTEXT_SPEC §5/§10) — assembled server-side by
    # context_service.assemble(), already relevance-gated, budget-capped, and
    # wrapped in <recalled trust="untrusted"> provenance markers. Replaces the
    # prior ad-hoc _query_qdrant()/<knowledge_context> path, which bypassed the
    # assembly log and had no provenance marking (L7 — one path through the
    # Memory Service interface, not two).
    if _package.get("recall_text"):
        system_prompt += f"\n\n{_package['recall_text']}"

    if req.attachments:
        import base64 as _b64
        user_content = []
        for att in req.attachments:
            if att.get("type") == "document":
                user_content.append({
                    "type": "document",
                    "source": {"type": "base64", "media_type": att["media_type"], "data": att["data"]},
                })
            elif att.get("type") == "image":
                user_content.append({
                    "type": "image",
                    "source": {"type": "base64", "media_type": att["media_type"], "data": att["data"]},
                })
        if req.message.strip() and req.message.strip() not in ("[Photo attached]",):
            user_content.append({"type": "text", "text": req.message})
        elif not any(c["type"] == "text" for c in user_content):
            user_content.append({"type": "text", "text": req.message or "Please review the attached file."})
        messages.append({"role": "user", "content": user_content})
    else:
        messages.append({"role": "user", "content": req.message})

    if _package is not None:
        try:
            httpx.post(
                f"{ORCHESTRATOR_URL}/context/turn",
                json={"key": _conv_key, "role": "user", "content": req.message,
                      "package_id": _package["package_id"]},
                timeout=10,
            )
        except Exception as e:
            logger.exception("context.record_turn (user) failed: %s", e)

    total_input_tokens = 0
    total_output_tokens = 0
    total_cache_read_tokens = 0
    total_cache_creation_tokens = 0
    raw_reply = ""

    # Privacy mode: ember/doc always local, or explicit flag
    PRIVACY_ADVISORS = {"ember", "doc"}
    _force_privacy = advisor in PRIVACY_ADVISORS or req.privacy_mode

    # Determine provider/model with complexity routing
    complexity = _classify_complexity(req.message)
    if advisor == "kai":
        if complexity == "deep":
            kai_model = "claude-opus-4-6"
        elif complexity == "simple":
            kai_model = "claude-haiku-4-5-20251001"
        else:
            kai_model = "claude-sonnet-4-6"
        adv_cfg = {"provider": "anthropic", "model": kai_model}
    else:
        adv_cfg = _get_advisor_config(advisor)
        if adv_cfg.get("provider") == "anthropic":
            if complexity == "deep":
                adv_cfg = dict(adv_cfg, model="claude-opus-4-6")
            elif complexity == "simple":
                adv_cfg = dict(adv_cfg, model="claude-haiku-4-5-20251001")

    if _force_privacy:
        adv_cfg = {"provider": "ollama", "model": "qwen2.5:3b"}

    # Budget degradation (S5R-19): interactive sub-budget exhausted → cap to Haiku
    if rl.get("degrade") and adv_cfg.get("provider") == "anthropic":
        adv_cfg = dict(adv_cfg, model="claude-haiku-4-5-20251001")

    provider = adv_cfg.get("provider", "anthropic")
    model    = adv_cfg.get("model", "claude-sonnet-4-6")
    actual_provider = provider
    actual_model    = model

    if provider == "anthropic":
        tools = KAI_TOOLS if advisor == "kai" else []
        raw_reply, total_input_tokens, total_output_tokens, total_cache_read_tokens, total_cache_creation_tokens = _run_agentic_loop(
            messages, tools, model, system_prompt, advisor, cache_breakpoint_chars=_cache_breakpoint_chars
        )

    elif provider == "ollama":
        try:
            raw_reply, total_input_tokens, total_output_tokens = _call_ollama(
                model, system_prompt, messages
            )
        except Exception as ollama_err:
            logger.exception("ollama fallback: %s", ollama_err)
            if _force_privacy:
                return {"advisor": advisor, "channel": channel,
                        "reply": "Privacy mode is active for this advisor — local model is required but currently unavailable. Please try again shortly.",
                        "insights_logged": 0, "input_tokens": 0, "output_tokens": 0,
                        "provider": "privacy-error", "model": model}
            fallback_model = adv_cfg.get("fallback_model", "claude-sonnet-4-6")
            actual_provider = "anthropic"
            actual_model = fallback_model
            client = get_anthropic_client()
            response = client.messages.create(
                model=fallback_model, max_tokens=2048,
                system=system_prompt + f"\n\n[Note: Local model unavailable ({ollama_err}), using cloud fallback]",
                messages=messages,
            )
            total_input_tokens          = response.usage.input_tokens
            total_output_tokens         = response.usage.output_tokens
            total_cache_read_tokens     = getattr(response.usage, "cache_read_input_tokens", 0) or 0
            total_cache_creation_tokens = getattr(response.usage, "cache_creation_input_tokens", 0) or 0
            raw_reply = next((b.text for b in response.content if hasattr(b, "text")), "")

    elif provider in ("openai", "litellm", "gemini"):
        try:
            raw_reply, total_input_tokens, total_output_tokens = _call_litellm(
                model, system_prompt, messages
            )
        except Exception as oai_err:
            logger.exception("litellm fallback: %s", oai_err)
            fallback_model = adv_cfg.get("fallback_model", "claude-sonnet-4-6")
            actual_provider = "anthropic"
            actual_model = fallback_model
            client = get_anthropic_client()
            response = client.messages.create(
                model=fallback_model, max_tokens=2048,
                system=system_prompt + f"\n\n[Note: LiteLLM unavailable ({oai_err}), using Anthropic fallback]",
                messages=messages,
            )
            total_input_tokens          = response.usage.input_tokens
            total_output_tokens         = response.usage.output_tokens
            total_cache_read_tokens     = getattr(response.usage, "cache_read_input_tokens", 0) or 0
            total_cache_creation_tokens = getattr(response.usage, "cache_creation_input_tokens", 0) or 0
            raw_reply = next((b.text for b in response.content if hasattr(b, "text")), "")
    else:
        raise HTTPException(400, f"Unknown provider: {provider}")

    # Cache shape logging (CONTEXT_SPEC §7/§8) — Anthropic-specific (cache_control
    # is an Anthropic feature; the stable/volatile ordering costs nothing on other
    # providers but there's nothing to correlate cache tokens against there).
    if _package is not None and provider == "anthropic":
        try:
            httpx.post(
                f"{ORCHESTRATOR_URL}/context/cache-shape",
                json={
                    "package_id": _package["package_id"],
                    "stable_prefix_hash": _package["stable_prefix_hash"],
                    "cache_breakpoint_after": _cache_breakpoint_chars,
                    "cache_read_tokens": total_cache_read_tokens,
                    "cache_creation_tokens": total_cache_creation_tokens,
                },
                timeout=10,
            )
        except Exception as e:
            logger.exception("context.cache_shape recording failed: %s", e)

    # Insight extraction
    insights_logged = 0
    if advisor == "ember":
        clean_reply, insights = extract_and_strip_insights(raw_reply)
        insights_logged = append_insights_to_vault(insights)
    else:
        clean_reply = raw_reply

    from insights import strip_markdown
    clean_reply = strip_markdown(clean_reply)

    if _package is not None:
        try:
            httpx.post(
                f"{ORCHESTRATOR_URL}/context/turn",
                json={"key": _conv_key, "role": "assistant", "content": clean_reply,
                      "package_id": _package["package_id"]},
                timeout=10,
            )
        except Exception as e:
            logger.exception("context.record_turn (assistant) failed: %s", e)

    # Track token usage
    effective_trigger = req.trigger_source or f"council:message:{channel}"
    _track_usage(advisor, total_input_tokens, total_output_tokens, actual_provider, actual_model,
                 trigger_source=effective_trigger,
                 cache_read_tokens=total_cache_read_tokens,
                 cache_creation_tokens=total_cache_creation_tokens)

    return {
        "advisor": advisor,
        "channel": channel,
        "reply": clean_reply,
        "package_id": _package["package_id"] if _package else None,
        "insights_logged": insights_logged,
        "input_tokens": total_input_tokens,
        "output_tokens": total_output_tokens,
        "provider": actual_provider,
        "model": actual_model,
        "cache_read_tokens": total_cache_read_tokens,
        "cache_creation_tokens": total_cache_creation_tokens,
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


@router.post("/council/ingest")
def ingest_file_endpoint(body: dict):
    """Called by worker API after saving a file to vault — runs ingest.py on the path."""
    import subprocess, os
    path = body.get("path")
    advisor = body.get("advisor", "kai")
    if not path:
        raise HTTPException(status_code=400, detail="path required")
    env = {**os.environ, "QDRANT_URL": "http://kai-qdrant:6333", "OLLAMA_URL": "http://kai-ollama:11434"}
    result = subprocess.run(
        ["python3", "/app/ingest.py", path, "--advisor", advisor],
        capture_output=True, text=True, timeout=300, env=env
    )
    if result.returncode != 0:
        return {"ok": False, "error": result.stderr[:500]}
    lines = [l for l in result.stdout.strip().splitlines() if l.strip()]
    return {"ok": True, "advisor": advisor, "path": path, "summary": lines[-1] if lines else "done"}


@router.get("/internal/invariants/persona_check")
def internal_persona_check():
    """Diagnostic for KAI-458 Slice A persona-assembly invariant.

    §3/§13 Tier 5 migration note: persona assembly now happens in the
    orchestrator process (context_service.tier5_standing_context()), not this
    one — the prior in-process logging.Handler capture of "persona"/
    "load_context" loggers would go silently blind to real degraded-mode
    warnings now that they're raised on the other side of the network. This
    calls the orchestrator's /context/persona endpoint directly per advisor
    and reads its `warnings` field instead, which is how tier5_standing_context()
    now surfaces degraded-mode signals (org_model/system_state load failures,
    budget truncation) — explicit response data, not a log-capture hack that
    doesn't cross a process boundary.
    """
    advisors = ["kai", "dev", "creative", "doc", "coach", "sky", "roads", "ember", "beats"]
    blocks_to_check = [
        "<background_context>",
        "<organization_structure>",
        "<build_profile>",
        "<org_model>",
        "<session_memory>",
        "<date_reference>",
        "<current_datetime>",
    ]

    results: dict[str, dict] = {}
    for advisor in advisors:
        try:
            r = httpx.get(f"{ORCHESTRATOR_URL}/context/persona", params={"advisor": advisor}, timeout=30)
            if r.status_code == 404:
                results[advisor] = {"load_ok": False, "error": f"HTTP 404: {r.json().get('detail', 'Persona not found')}"}
                continue
            r.raise_for_status()
            data = r.json()
            prompt = data["stable_text"] + (
                "\n\n---\n\n" + data["volatile_text"] if data.get("volatile_text") else ""
            )
            results[advisor] = {
                "load_ok": True,
                "size": len(prompt),
                "blocks_present": {b: (b in prompt) for b in blocks_to_check},
                "warnings": data.get("warnings", []),
            }
        except Exception as e:
            results[advisor] = {"load_ok": False, "error": f"{type(e).__name__}: {e}"}

    return {"ok": True, "results": results}
