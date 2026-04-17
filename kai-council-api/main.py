from fastapi import FastAPI, HTTPException
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
    }
]


def execute_tool(tool_name: str, tool_input: dict) -> dict:
    import json as _tj
    try:
        with httpx.Client(timeout=10) as client:
            if tool_name == "save_workflow":
                r = client.post(f"{WORKER_URL}/workflows", json=tool_input)
                return r.json()
            elif tool_name == "list_workflows":
                r = client.get(f"{WORKER_URL}/workflows")
                return r.json()
            elif tool_name == "delete_workflow":
                wid = tool_input.get("id", "")
                r = client.delete(f"{WORKER_URL}/workflows/{wid}")
                return r.json()
    except Exception as e:
        return {"error": str(e)}
    return {"error": "Unknown tool"}




def get_anthropic_client():
    secret_path = Path("/run/secrets/anthropic_api_key")
    api_key = secret_path.read_text().strip() if secret_path.exists() else os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise HTTPException(status_code=503, detail="Anthropic API key not configured")
    return anthropic.Anthropic(api_key=api_key)


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
def council_message(req: MessageRequest):
    import json as _mj
    channel = req.channel.lstrip("#")
    advisor = ADVISOR_CHANNELS.get(channel)
    if not advisor:
        raise HTTPException(status_code=400, detail=f"Unknown channel: {channel}")

    system_prompt = load_persona(advisor, channel)
    client = get_anthropic_client()

    messages = req.history[-10:]
    messages.append({"role": "user", "content": req.message})

    # Tool use only for KAI (chief)
    tools = KAI_TOOLS if advisor == "chief" else []
    total_input_tokens = 0
    total_output_tokens = 0

    # Agentic loop — handles tool calls
    while True:
        kwargs = dict(
            model="claude-sonnet-4-5",
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
            # Append assistant turn (includes tool_use blocks)
            messages.append({"role": "assistant", "content": response.content})
            # Execute each tool and collect results
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
            continue  # loop back for final reply

        # stop_reason == "end_turn" — extract text
        raw_reply = next(
            (b.text for b in response.content if hasattr(b, "text")),
            ""
        )
        break

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

    return {
        "advisor": advisor,
        "channel": channel,
        "reply": clean_reply,
        "insights_logged": insights_logged,
        "input_tokens": total_input_tokens,
        "output_tokens": total_output_tokens,
    }



@app.post("/message")
def web_message(req: MessageRequest):
    """Web UI alias — nginx strips /council/ prefix."""
    return council_message(req)


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
