"""kai-mcp-api — KAI MCP (Model Context Protocol) server
Exposes KAI tools as MCP-compliant JSON-RPC endpoints consumed by LangGraph agents.
Tools: calendar, tasks, vault_read, knowledge_sessions.
Routes to kai-worker-api (http://kai-worker-api:8001) internally.
"""
import logging
from typing import Any
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s [mcp] %(message)s")
log = logging.getLogger(__name__)

WORKER_URL = "http://kai-worker-api:8001"
app = FastAPI(title="KAI MCP Server", version="1.0.0")

# ── Tool definitions ────────────────────────────────────────────────────────

TOOLS = [
    {
        "name": "get_calendar_events",
        "description": "Get upcoming calendar events for Leo. Returns events for today and the next 7 days.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": "Number of days ahead to fetch (default 7)",
                    "default": 7,
                }
            },
        },
    },
    {
        "name": "get_tasks",
        "description": "Get Leo's Todoist tasks — today's tasks and inbox items.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "vault_read",
        "description": "Read a file from Leo's vault. Use for notes, status files, session logs.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative path within the vault (e.g. '20_Projects/KAI/STATUS.md')",
                }
            },
            "required": ["path"],
        },
    },
    {
        "name": "get_knowledge_sessions",
        "description": "List recent KAI council session summaries stored in the vault.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Number of recent sessions to return (default 10)",
                    "default": 10,
                }
            },
        },
    },
]


# ── Tool handlers ────────────────────────────────────────────────────────────

async def _call_worker(client: httpx.AsyncClient, path: str, params: dict = None) -> Any:
    r = await client.get(f"{WORKER_URL}{path}", params=params, timeout=15)
    r.raise_for_status()
    return r.json()


async def handle_get_calendar_events(args: dict) -> str:
    days = int(args.get("days", 7))
    async with httpx.AsyncClient() as c:
        data = await _call_worker(c, "/calendar/events", {"days": days})
    events = data if isinstance(data, list) else data.get("events", data)
    if not events:
        return "No upcoming calendar events found."
    lines = []
    for e in events[:20]:
        title = e.get("summary") or e.get("title") or "Untitled"
        start = e.get("start") or e.get("date") or ""
        lines.append(f"- {start}: {title}")
    return "\n".join(lines)


async def handle_get_tasks(args: dict) -> str:
    async with httpx.AsyncClient() as c:
        data = await _call_worker(c, "/tasks")
    today = data.get("today", [])
    inbox = data.get("inbox", [])
    lines = []
    if today:
        lines.append(f"TODAY ({len(today)}):")
        for t in today[:15]:
            lines.append(f"  - {t.get('content', t.get('title', '?'))}")
    if inbox:
        lines.append(f"INBOX ({len(inbox)}):")
        for t in inbox[:10]:
            lines.append(f"  - {t.get('content', t.get('title', '?'))}")
    return "\n".join(lines) if lines else "No tasks found."


async def handle_vault_read(args: dict) -> str:
    path = args.get("path", "")
    if not path:
        return "Error: path is required."
    async with httpx.AsyncClient() as c:
        data = await _call_worker(c, "/vault/read", {"path": path})
    return data.get("content", str(data))


async def handle_get_knowledge_sessions(args: dict) -> str:
    limit = int(args.get("limit", 10))
    async with httpx.AsyncClient() as c:
        data = await _call_worker(c, "/knowledge/sessions")
    sessions = data if isinstance(data, list) else data.get("sessions", [])
    if not sessions:
        return "No sessions found."
    lines = []
    for s in sessions[-limit:]:
        date = s.get("date") or s.get("filename", "")
        title = s.get("title", "")
        lines.append(f"- {date}: {title}" if title else f"- {date}")
    return "\n".join(reversed(lines))


HANDLERS = {
    "get_calendar_events":   handle_get_calendar_events,
    "get_tasks":             handle_get_tasks,
    "vault_read":            handle_vault_read,
    "get_knowledge_sessions": handle_get_knowledge_sessions,
}


# ── MCP JSON-RPC handler ─────────────────────────────────────────────────────

def _ok(id_: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "result": result}


def _err(id_: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}


@app.post("/")
async def mcp_handler(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(_err(None, -32700, "Parse error"), status_code=400)

    id_ = body.get("id")
    method = body.get("method", "")
    params = body.get("params", {})

    log.info(f"MCP {method} id={id_}")

    if method == "initialize":
        return JSONResponse(_ok(id_, {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "kai-mcp-api", "version": "1.0.0"},
        }))

    if method == "tools/list":
        return JSONResponse(_ok(id_, {"tools": TOOLS}))

    if method == "tools/call":
        name = params.get("name", "")
        args = params.get("arguments", {})
        handler = HANDLERS.get(name)
        if not handler:
            return JSONResponse(_err(id_, -32601, f"Unknown tool: {name}"))
        try:
            text = await handler(args)
            return JSONResponse(_ok(id_, {"content": [{"type": "text", "text": text}]}))
        except Exception as e:
            log.exception(f"Tool {name} error: {e}")
            return JSONResponse(_err(id_, -32603, str(e)))

    return JSONResponse(_err(id_, -32601, f"Method not found: {method}"))


@app.get("/health")
def health():
    return {"status": "ok", "service": "kai-mcp-api", "tools": len(TOOLS)}
