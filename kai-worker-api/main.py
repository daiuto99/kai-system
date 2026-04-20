import json
import uuid as _uuid
from pydantic import BaseModel
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path
import os

app = FastAPI(title="kai-worker-api", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

VAULT_PATH = Path("/vault")


@app.get("/health")
def health():
    vault_ok = VAULT_PATH.exists()
    return {
        "status": "ok",
        "service": "kai-worker-api",
        "vault_mounted": vault_ok,
        "vault_path": str(VAULT_PATH),
    }


@app.get("/vault/read")
def read_file(path: str):
    target = VAULT_PATH / path
    if not target.resolve().is_relative_to(VAULT_PATH):
        raise HTTPException(status_code=400, detail="Path traversal not allowed")
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {path}")
    return {"path": path, "content": target.read_text(encoding="utf-8")}


@app.post("/vault/write")
def write_file(path: str, content: str):
    target = VAULT_PATH / path
    if not target.resolve().is_relative_to(VAULT_PATH):
        raise HTTPException(status_code=400, detail="Path traversal not allowed")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return {"status": "written", "path": path}


@app.get("/vault/list")
def list_files(path: str = ""):
    target = VAULT_PATH / path
    if not target.resolve().is_relative_to(VAULT_PATH):
        raise HTTPException(status_code=400, detail="Path traversal not allowed")
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"Path not found: {path}")
    files = [str(f.relative_to(VAULT_PATH)) for f in target.rglob("*") if f.is_file()]
    return {"path": path, "files": sorted(files)}


# ── Focus ─────────────────────────────────────────────────────────────────────

from focus import run_focus_brief, get_todoist_tasks
from pydantic import BaseModel as _BaseModel
from datetime import date as _date


class FocusRequest(_BaseModel):
    kai_focus_channel_id: str


@app.post("/focus/run")
def trigger_focus_brief(req: FocusRequest):
    result = run_focus_brief(req.kai_focus_channel_id)
    return result


@app.get("/focus/today")
def focus_today():
    """Return structured focus stack for the web UI (no Slack post)."""
    try:
        tasks = get_todoist_tasks()
    except Exception:
        return {"top3": [], "next5": [], "remaining": [], "error": "Could not reach Todoist"}

    today = _date.today().isoformat()

    def shape(t):
        return {
            "id":       t.get("id", ""),
            "content":  t.get("content", ""),
            "priority": t.get("priority", 4),
            "due":      t.get("due", {}).get("date") if t.get("due") else None,
            "project":  t.get("project_id", ""),
        }

    # Sort by priority (1=urgent), then due date
    all_tasks = tasks.get("today", []) + tasks.get("overdue", [])

    # get_todoist_tasks returns content strings, not full objects
    # Let's get the raw tasks instead
    try:
        import httpx
        from pathlib import Path as _Path

        def load_secret(name):
            p = _Path(f"/run/secrets/{name}")
            if p.exists():
                return p.read_text().strip()
            return os.environ.get(name.upper(), "")

        token = load_secret("todoist_api_key")
        if not token:
            return {"top3": [], "next5": [], "remaining": []}

        headers = {"Authorization": f"Bearer {token}"}
        today_str = today

        with httpx.Client() as client:
            r = client.get(
                "https://api.todoist.com/api/v1/tasks",
                headers=headers,
                timeout=15.0,
            )
            r.raise_for_status()
            raw = r.json().get("results", [])

        # Filter today + overdue
        relevant = [t for t in raw if t.get("due") and t["due"]["date"] <= today_str]
        # Sort by priority ascending (p1 = most urgent)
        relevant.sort(key=lambda t: (t.get("priority", 4), t.get("due", {}).get("date", "9999")))

        shaped = [shape(t) for t in relevant]
        # Also include tasks without due date, sorted by priority
        no_due = [shape(t) for t in raw if not t.get("due")]
        no_due.sort(key=lambda t: t.get("priority", 4))

        combined = shaped + no_due

        return {
            "top3":      combined[:3],
            "next5":     combined[3:8],
            "remaining": combined[8:],
        }

    except Exception as e:
        return {"top3": [], "next5": [], "remaining": [], "error": str(e)}


# ── Harmony ───────────────────────────────────────────────────────────────────

from harmony import router as harmony_router
app.include_router(harmony_router)


# ── Parking Lot ───────────────────────────────────────────────────────────────

from parking_lot import capture as pl_capture
import re as _re


class ParkingLotRequest(_BaseModel):
    text: str
    channel_id: str
    thread_ts: str
    user_id: str = ""


@app.post("/parking-lot/capture")
def parking_lot_capture(req: ParkingLotRequest):
    result = pl_capture(req.text, req.channel_id, req.thread_ts, req.user_id)
    return result


LOT_DIR   = VAULT_PATH / "50_ParkingLot"
ARCH_DIR  = LOT_DIR / "archived"


def _parse_card(path: Path) -> dict:
    text = path.read_text()
    meta, content_lines, in_fm, fm_done = {}, [], False, False
    for i, line in enumerate(text.strip().splitlines()):
        if i == 0 and line == "---":
            in_fm = True; continue
        if in_fm and line == "---":
            in_fm = False; fm_done = True; continue
        if in_fm:
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip()
        else:
            content_lines.append(line)

    content = "\n".join(content_lines)
    title = meta.get("title", "")
    for line in content_lines:
        if line.startswith("# "):
            title = line[2:].strip(); break

    summary = ""
    past = False
    for line in content_lines:
        if line.startswith("# "): past = True; continue
        if past and line.strip() and not line.startswith("#"):
            summary = line.strip(); break

    urls = _re.findall(r"<(https?://[^>]+)>", content)
    return {
        "slug":    path.stem,
        "title":   title or path.stem,
        "type":    meta.get("type", "item"),
        "date":    meta.get("date", ""),
        "status":  meta.get("status", "captured"),
        "summary": summary,
        "url":     urls[0] if urls else "",
    }




class QuickCaptureRequest(_BaseModel):
    text: str


@app.post("/parking-lot/quick")
def parking_lot_quick(req: QuickCaptureRequest):
    """Quick capture from web UI — no Slack context needed."""
    from datetime import datetime as _datetime
    import re as _re2
    slug = _datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    LOT_DIR.mkdir(parents=True, exist_ok=True)
    # Detect if text contains a URL
    urls = _re2.findall(r"https?://\S+", req.text)
    item_type = "link" if urls else "note"
    content = f"""---
title: {req.text[:60]}
date: {_datetime.utcnow().strftime("%Y-%m-%d")}
type: {item_type}
status: captured
source: web
---

# {req.text[:60]}

{req.text}
"""
    (LOT_DIR / f"{slug}.md").write_text(content, encoding="utf-8")
    return {"ok": True, "slug": slug}


@app.get("/stoic-quote")
def get_stoic_quote():
    """Return a daily-cached stoic quote."""
    import httpx as _hx
    from datetime import date as _d
    import random as _random
    today = _d.today().isoformat()

    _STOIC = [
        {"content": "You have power over your mind, not outside events. Realize this, and you will find strength.", "author": "Marcus Aurelius"},
        {"content": "The impediment to action advances action. What stands in the way becomes the way.", "author": "Marcus Aurelius"},
        {"content": "Waste no more time arguing about what a good man should be. Be one.", "author": "Marcus Aurelius"},
        {"content": "If it is not right, do not do it; if it is not true, do not say it.", "author": "Marcus Aurelius"},
        {"content": "He who fears death will never do anything worthy of a man who is alive.", "author": "Seneca"},
        {"content": "Luck is what happens when preparation meets opportunity.", "author": "Seneca"},
        {"content": "Begin at once to live, and count each separate day as a separate life.", "author": "Seneca"},
        {"content": "No man is free who is not master of himself.", "author": "Epictetus"},
        {"content": "Make the best use of what is in your power, and take the rest as it happens.", "author": "Epictetus"},
        {"content": "He is a wise man who does not grieve for things he has not, but rejoices for those he has.", "author": "Epictetus"},
        {"content": "First say to yourself what you would be; and then do what you have to do.", "author": "Epictetus"},
        {"content": "Difficulties are things that show a person what they are.", "author": "Epictetus"},
        {"content": "Man conquers the world by conquering himself.", "author": "Zeno of Citium"},
        {"content": "Confine yourself to the present.", "author": "Marcus Aurelius"},
        {"content": "Very little is needed to make a happy life; it is all within yourself, in your way of thinking.", "author": "Marcus Aurelius"},
    ]

    # Daily stable selection based on date
    _random.seed(today)
    quote = _random.choice(_STOIC)

    # Try quotable.io for variety (fall back to local on failure)
    try:
        r = _hx.get("https://api.quotable.io/random?tags=stoicism&maxLength=130", timeout=3)
        if r.status_code == 200:
            d = r.json()
            if d.get("content") and d.get("author"):
                return {"content": d["content"], "author": d["author"]}
    except Exception:
        pass

    return quote


@app.get("/parking-lot/og")
def parking_lot_og_image(url: str):
    """Fetch OG image URL for a given URL (for Lot thumbnails)."""
    try:
        headers = {"User-Agent": "Mozilla/5.0 Twitterbot/1.0"}
        with httpx.Client(timeout=5, follow_redirects=True) as client:
            r = client.get(url, headers=headers)
        html = r.text[:80000]
        # og:image — two attribute orderings
        m = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', html, re.I)
        if not m:
            m = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']', html, re.I)
        if m:
            img = m.group(1)
            if img.startswith("/"):
                from urllib.parse import urlparse
                p = urlparse(url)
                img = f"{p.scheme}://{p.netloc}{img}"
            return {"image": img}
        return {"image": ""}
    except Exception:
        return {"image": ""}

@app.get("/parking-lot/list")
def parking_lot_list():
    LOT_DIR.mkdir(parents=True, exist_ok=True)
    items = []
    for f in sorted(LOT_DIR.glob("*.md"), reverse=True):
        try:
            items.append(_parse_card(f))
        except Exception:
            pass
    return {"items": items, "count": len(items)}


class RouteBody(_BaseModel):
    advisor: str


@app.patch("/parking-lot/{slug}")
def parking_lot_edit(slug: str, body: dict):
    """Edit a lot item's title."""
    path = LOT_DIR / f"{slug}.md"
    if not path.exists():
        raise HTTPException(404, "Not found")
    text = path.read_text()
    new_title = body.get("title", "").strip()
    if new_title:
        # Update title in frontmatter if present, else prepend
        import re as _re_edit
        if _re_edit.search(r'^title:', text, _re_edit.MULTILINE):
            text = _re_edit.sub(r'^title:.*$', f'title: {new_title}', text, flags=_re_edit.MULTILINE)
        path.write_text(text)
    return {"ok": True}


@app.delete("/parking-lot/{slug}")
def parking_lot_delete(slug: str):
    """Permanently delete a lot item."""
    path = LOT_DIR / f"{slug}.md"
    if path.exists():
        path.unlink()
    return {"ok": True}


@app.post("/parking-lot/{slug}/route")
def parking_lot_route(slug: str, body: RouteBody):
    path = LOT_DIR / f"{slug}.md"
    if not path.exists():
        raise HTTPException(404, "Capture not found")
    ARCH_DIR.mkdir(parents=True, exist_ok=True)
    dest = ARCH_DIR / f"{slug}.md"
    dest.write_text(path.read_text() + f"\n\n<!-- Routed to #{body.advisor} -->")
    path.unlink()
    return {"ok": True, "routed_to": body.advisor}


@app.post("/parking-lot/{slug}/archive")
def parking_lot_archive(slug: str):
    path = LOT_DIR / f"{slug}.md"
    if not path.exists():
        raise HTTPException(404, "Capture not found")
    ARCH_DIR.mkdir(parents=True, exist_ok=True)
    path.rename(ARCH_DIR / path.name)
    return {"ok": True}


# ── Insights ──────────────────────────────────────────────────────────────────

@app.get("/insights")
def get_insights():
    insights_file = VAULT_PATH / "60_Council" / "ember" / "insights.md"
    if not insights_file.exists():
        return {"insights": []}

    content = insights_file.read_text(encoding="utf-8")
    insights = []
    current_category = None

    for line in content.splitlines():
        if line.startswith("## "):
            current_category = line[3:].strip().rstrip("s")  # "Insights" → "Insight"
        elif line.startswith("- [") and current_category:
            # Format: - [2026-04-15] content
            import re as _re2
            m = _re2.match(r"- \[(\d{4}-\d{2}-\d{2})\] (.+)", line)
            if m:
                insights.append({
                    "category": current_category,
                    "date":     m.group(1),
                    "content":  m.group(2).strip(),
                })

    insights.reverse()  # Newest first
    return {"insights": insights, "count": len(insights)}


# ── Check-In ──────────────────────────────────────────────────────────────

CHECKIN_FILE = VAULT_PATH / "00_System" / "checkin.json"

@app.get("/checkin")
def get_checkin():
    if CHECKIN_FILE.exists():
        return json.loads(CHECKIN_FILE.read_text())
    return {"intent": "", "date": ""}

class CheckInRequest(BaseModel):
    intent: str = ""
    sleep_quality: str = ""    # e.g. "great", "ok", "rough"
    restfulness: str = ""      # free text: how rested Leo feels

@app.post("/checkin")
def save_checkin(req: CheckInRequest):
    from datetime import datetime as _dt
    data = {
        "intent": req.intent,
        "sleep_quality": req.sleep_quality,
        "restfulness": req.restfulness,
        "date": _dt.utcnow().strftime("%Y-%m-%d")
    }
    CHECKIN_FILE.parent.mkdir(parents=True, exist_ok=True)
    CHECKIN_FILE.write_text(json.dumps(data, indent=2))
    return data

# ── Settings ──────────────────────────────────────────────────────────────

SETTINGS_FILE = VAULT_PATH / "00_System" / "ui_settings.json"

@app.get("/settings")
def get_settings():
    if SETTINGS_FILE.exists():
        return json.loads(SETTINGS_FILE.read_text())
    return {"working_on": "", "o365_cal_1": "", "o365_cal_2": ""}

class UISettingsRequest(BaseModel):
    working_on: str = ""
    o365_cal_1: str = ""
    o365_cal_2: str = ""

@app.post("/settings")
def save_settings(req: UISettingsRequest):
    data = req.dict()
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(data, indent=2))
    return data





# ── Projects v2 (STATUS.md) ──────────────────────────────────────────────────

import re as _re_status
import yaml as _yaml

def _parse_status_md(path: Path) -> dict:
    """Parse YAML frontmatter from a STATUS.md file."""
    text = path.read_text(encoding="utf-8")
    m = _re_status.match(r"^---\s*\n(.*?)\n---", text, _re_status.DOTALL)
    if not m:
        return {}
    try:
        return _yaml.safe_load(m.group(1)) or {}
    except Exception:
        return {}

PROJECTS_FILE = VAULT_PATH / "00_System" / "projects.json"
PROJECTS_DIR  = VAULT_PATH / "20_Projects"

@app.get("/projects")
def get_projects_v2():
    """Read project metadata from projects.json + live status from STATUS.md files."""
    # Load base project list
    base = []
    if PROJECTS_FILE.exists():
        base = json.loads(PROJECTS_FILE.read_text())

    result = []
    for project in base:
        if not project.get("active", True):
            continue
        pid = project["id"]
        # Try to find STATUS.md — check both lowercase and original-case folder
        status_data = {}
        for folder_name in [pid, pid.capitalize(), pid.upper()]:
            status_path = PROJECTS_DIR / folder_name / "STATUS.md"
            if status_path.exists():
                status_data = _parse_status_md(status_path)
                break

        entry = {
            "id":           project["id"],
            "name":         project["name"],
            "description":  project.get("description", ""),
            "advisor":      project.get("advisor", "kai"),
            "url":          project.get("url", ""),
            # From STATUS.md (with fallback to projects.json)
            "status":       status_data.get("status",       project.get("status", "green")),
            "version":      status_data.get("version",      None),
            "milestone":    status_data.get("milestone",    project.get("next", "")),
            "milestone_pct":status_data.get("milestone_pct", None),
            "updated":      str(status_data.get("updated", "")),
            "next":         status_data.get("next",         project.get("next", "")),
        }
        result.append(entry)

    return {"projects": result}


# ── Habits ────────────────────────────────────────────────────────────────────

# ── Habits (HabitSync) ───────────────────────────────────────────────────────

@app.get("/habits")
def get_habits_endpoint():
    from datetime import date as _d
    try:
        habits = hs_get_habits()
        return {"habits": habits, "date": _d.today().isoformat()}
    except Exception as e:
        return {"habits": [], "date": _d.today().isoformat(), "error": str(e)}

@app.post("/habits/{habit_id}/complete")
def complete_habit(habit_id: str):
    try:
        result = log_habit(habit_id)
        return {"ok": True, "habit_id": habit_id, **result}
    except Exception as e:
        raise HTTPException(500, str(e))

@app.delete("/habits/{habit_id}/complete")
def uncomplete_habit(habit_id: str):
    try:
        result = unlog_habit(habit_id)
        return {"ok": True, "habit_id": habit_id, **result}
    except Exception as e:
        raise HTTPException(500, str(e))


# ── Weather ───────────────────────────────────────────────────────────────────

@app.get("/weather")
def get_weather():
    import httpx as _httpx
    api_key = os.environ.get("OPENWEATHERMAP_API_KEY", "")
    lat     = os.environ.get("WEATHER_LAT", "")
    lon     = os.environ.get("WEATHER_LON", "")

    if not api_key or not lat or not lon:
        return {"error": "weather_not_configured", "temp": None, "condition": None}

    try:
        url = (
            f"https://api.openweathermap.org/data/2.5/weather"
            f"?lat={lat}&lon={lon}&appid={api_key}&units=imperial"
        )
        with _httpx.Client(timeout=10) as client:
            r = client.get(url)
            r.raise_for_status()
            d = r.json()

        weather_id = d["weather"][0]["id"]
        # Map condition ID to theme
        if weather_id == 800:
            theme = "clear"
        elif weather_id > 800:
            theme = "clouds"
        elif weather_id >= 700:
            theme = "atmosphere"
        elif weather_id >= 600:
            theme = "snow"
        elif weather_id >= 500:
            theme = "rain"
        elif weather_id >= 300:
            theme = "drizzle"
        elif weather_id >= 200:
            theme = "thunderstorm"
        else:
            theme = "clear"

        return {
            "temp":        round(d["main"]["temp"]),
            "feels_like":  round(d["main"]["feels_like"]),
            "condition":   d["weather"][0]["description"],
            "theme":       theme,
            "icon":        d["weather"][0]["icon"],
            "humidity":    d["main"]["humidity"],
            "city":        d.get("name", ""),
        }
    except Exception as e:
        return {"error": str(e), "temp": None, "condition": None}


# ── Quote (daily cached) ──────────────────────────────────────────────────────

DAILY_CACHE_FILE = VAULT_PATH / "00_System" / "daily_cache.json"

@app.get("/quote")
def get_quote():
    import httpx as _httpx
    from datetime import date as _d
    today = _d.today().isoformat()

    # Return cached quote if still today's
    if DAILY_CACHE_FILE.exists():
        cache = json.loads(DAILY_CACHE_FILE.read_text())
        if cache.get("date") == today and cache.get("quote"):
            return cache["quote"]

    # Fetch fresh quote
    try:
        with _httpx.Client(timeout=10) as client:
            r = client.get("https://api.quotable.io/random?maxLength=150")
            r.raise_for_status()
            d = r.json()
        quote = {"content": d["content"], "author": d["author"]}
    except Exception:
        quote = {"content": "The secret of getting ahead is getting started.", "author": "Mark Twain"}

    # Cache it
    DAILY_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    cache = json.loads(DAILY_CACHE_FILE.read_text()) if DAILY_CACHE_FILE.exists() else {}
    cache["date"] = today
    cache["quote"] = quote
    DAILY_CACHE_FILE.write_text(json.dumps(cache, indent=2))

    return quote



# ── Workflows ─────────────────────────────────────────────────────────────────

WORKFLOWS_FILE = VAULT_PATH / "00_System" / "workflows.json"

@app.get("/workflows")
def get_workflows():
    if WORKFLOWS_FILE.exists():
        return {"workflows": json.loads(WORKFLOWS_FILE.read_text())}
    return {"workflows": []}

class WorkflowModel(BaseModel):
    id: str
    label: str
    prompt: str
    send: bool = True
    description: str = ""

@app.post("/workflows")
def upsert_workflow(w: WorkflowModel):
    from datetime import date as _wd
    workflows = json.loads(WORKFLOWS_FILE.read_text()) if WORKFLOWS_FILE.exists() else []
    idx = next((i for i, x in enumerate(workflows) if x["id"] == w.id), None)
    entry = w.dict()
    entry["updated"] = _wd.today().isoformat()
    if idx is not None:
        workflows[idx] = entry
    else:
        entry["created"] = _wd.today().isoformat()
        workflows.append(entry)
    WORKFLOWS_FILE.parent.mkdir(parents=True, exist_ok=True)
    WORKFLOWS_FILE.write_text(json.dumps(workflows, indent=2))
    return {"ok": True, "workflow": entry}

@app.delete("/workflows/{workflow_id}")
def delete_workflow_endpoint(workflow_id: str):
    if not WORKFLOWS_FILE.exists():
        return {"ok": True}
    workflows = [w for w in json.loads(WORKFLOWS_FILE.read_text()) if w["id"] != workflow_id]
    WORKFLOWS_FILE.write_text(json.dumps(workflows, indent=2))
    return {"ok": True}

# ── Tasks (Todoist) ───────────────────────────────────────────────────────────

from services.habitsync import get_habits as hs_get_habits, log_habit, unlog_habit
from services.todoist import (
    get_inbox, get_today, create_task, update_task,
    complete_task as todoist_complete, reschedule_task,
    delete_task, shape_task, move_to_today
)


@app.get("/tasks")
def get_tasks():
    try:
        inbox = [shape_task(t) for t in get_inbox()]
        today = [shape_task(t) for t in get_today()]
        # Sort both by priority
        inbox.sort(key=lambda t: t["priority"])
        today.sort(key=lambda t: (t["priority"], t["due"] or "9999"))
        return {"today": today, "inbox": inbox}
    except Exception as e:
        return {"today": [], "inbox": [], "error": str(e)}


class TaskCreateRequest(BaseModel):
    content: str
    due_date: str = None
    priority: int = 4
    project_id: str = None
    description: str = ""


@app.post("/tasks")
def api_create_task(req: TaskCreateRequest):
    task = create_task(
        content=req.content,
        due_date=req.due_date,
        priority=req.priority,
        project_id=req.project_id,
        description=req.description,
    )
    return shape_task(task)


class TaskUpdateRequest(BaseModel):
    content: str = None
    due_date: str = None
    priority: int = None
    description: str = None
    move_to_today: bool = False


@app.patch("/tasks/{task_id}")
def api_update_task(task_id: str, req: TaskUpdateRequest):
    if req.move_to_today:
        task = move_to_today(task_id)
    else:
        task = update_task(
            task_id,
            content=req.content,
            due_date=req.due_date,
            priority=req.priority,
            description=req.description,
        )
    return shape_task(task) if task else {"ok": True}


@app.post("/tasks/{task_id}/complete")
def api_complete_task(task_id: str):
    ok = todoist_complete(task_id)
    return {"ok": ok}


@app.delete("/tasks/{task_id}")
def api_delete_task(task_id: str):
    ok = delete_task(task_id)
    return {"ok": ok}


class ProjectPatch(BaseModel):
    pinned: bool = None
    status: str = None
    next: str = None
    milestone: str = None
    milestone_pct: int = None

@app.patch("/projects/{project_id}")
def patch_project(project_id: str, body: ProjectPatch):
    if not PROJECTS_FILE.exists():
        raise HTTPException(404, "projects file not found")
    projects = json.loads(PROJECTS_FILE.read_text())
    for p in projects:
        if p["id"] == project_id:
            if body.pinned is not None: p["pinned"] = body.pinned
            if body.status:             p["status"]  = body.status
            if body.next:               p["next"]    = body.next
            if body.milestone:          p["milestone"] = body.milestone
            if body.milestone_pct is not None: p["milestone_pct"] = body.milestone_pct
            PROJECTS_FILE.write_text(json.dumps(projects, indent=2))
            return {"ok": True, "project": p}
    raise HTTPException(404, "project not found")


# ── POST /projects — create new project ─────────────────────────────────────
class ProjectCreate(BaseModel):
    id: str
    name: str
    status: str = "green"
    next: str = ""
    description: str = ""
    url: str = ""
    advisor: str = "kai"
    active: bool = True

@app.post("/projects")
def create_project(body: ProjectCreate):
    projects = json.loads(PROJECTS_FILE.read_text()) if PROJECTS_FILE.exists() else []
    if any(p["id"] == body.id for p in projects):
        raise HTTPException(400, f"project '{body.id}' already exists")
    p = body.dict()
    p.setdefault("pinned", False)
    projects.append(p)
    PROJECTS_FILE.write_text(json.dumps(projects, indent=2))
    return {"ok": True, "project": p}

# ── GET /token-usage — daily + running totals ────────────────────────────────
TOKEN_USAGE_FILE = VAULT_PATH / "00_System" / "token_usage.json"

@app.get("/token-usage")
def get_token_usage():
    if not TOKEN_USAGE_FILE.exists():
        return {"days": [], "total": {"input": 0, "output": 0, "cost_usd": 0.0, "calls": 0}}
    data = json.loads(TOKEN_USAGE_FILE.read_text())
    return data

# ── Google Calendar Integration ───────────────────────────────────────────────
GCAL_CREDS_FILE  = VAULT_PATH / "00_System" / "google_calendar_token.json"
GCAL_CLIENT_FILE = VAULT_PATH / "00_System" / "google_calendar_client.json"
GCAL_SCOPES = ["https://www.googleapis.com/auth/calendar.readonly",
               "https://www.googleapis.com/auth/calendar.events"]

def _gcal_service():
    """Return an authenticated Google Calendar service, or None if not configured."""
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build

        if not GCAL_CREDS_FILE.exists():
            return None
        creds = Credentials.from_authorized_user_file(str(GCAL_CREDS_FILE), GCAL_SCOPES)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            GCAL_CREDS_FILE.write_text(creds.to_json())
        return build("calendar", "v3", credentials=creds)
    except Exception as e:
        print(f"[gcal] service error: {e}")
        return None

@app.get("/calendar/auth-url")
def gcal_auth_url():
    """Step 1: return the URL the user must visit to authorize."""
    try:
        from google_auth_oauthlib.flow import Flow
        if not GCAL_CLIENT_FILE.exists():
            raise HTTPException(400, "google_calendar_client.json not found in vault")
        flow = Flow.from_client_secrets_file(
            str(GCAL_CLIENT_FILE), scopes=GCAL_SCOPES,
            redirect_uri="urn:ietf:wg:oauth:2.0:oob"
        )
        auth_url, _ = flow.authorization_url(prompt="consent", access_type="offline")
        return {"auth_url": auth_url}
    except Exception as e:
        raise HTTPException(500, str(e))

class GCalCodeRequest(BaseModel):
    code: str

@app.post("/calendar/auth-code")
def gcal_auth_code(req: GCalCodeRequest):
    """Step 2: exchange the code for tokens and save to vault."""
    try:
        from google_auth_oauthlib.flow import Flow
        if not GCAL_CLIENT_FILE.exists():
            raise HTTPException(400, "google_calendar_client.json not found in vault")
        flow = Flow.from_client_secrets_file(
            str(GCAL_CLIENT_FILE), scopes=GCAL_SCOPES,
            redirect_uri="urn:ietf:wg:oauth:2.0:oob"
        )
        flow.fetch_token(code=req.code)
        creds = flow.credentials
        GCAL_CREDS_FILE.write_text(creds.to_json())
        return {"ok": True, "message": "Calendar authorized and token saved"}
    except Exception as e:
        raise HTTPException(500, str(e))

@app.get("/calendar/events")
def gcal_events(days: int = 7, calendar_id: str = "primary"):
    """Get upcoming events across the next N days."""
    import datetime
    svc = _gcal_service()
    if not svc:
        return {"events": [], "error": "calendar not configured"}
    now = datetime.datetime.utcnow().isoformat() + "Z"
    end = (datetime.datetime.utcnow() + datetime.timedelta(days=days)).isoformat() + "Z"
    result = svc.events().list(
        calendarId=calendar_id, timeMin=now, timeMax=end,
        maxResults=50, singleEvents=True, orderBy="startTime"
    ).execute()
    events = []
    for e in result.get("items", []):
        start = e["start"].get("dateTime", e["start"].get("date"))
        events.append({
            "id": e["id"],
            "title": e.get("summary", "(no title)"),
            "start": start,
            "end": e["end"].get("dateTime", e["end"].get("date")),
            "location": e.get("location", ""),
            "description": e.get("description", ""),
            "calendar": calendar_id,
        })
    return {"events": events}

class GCalEventCreate(BaseModel):
    title: str
    start: str           # ISO 8601 e.g. "2026-04-18T10:00:00"
    end: str             # ISO 8601
    description: str = ""
    location: str = ""
    calendar_id: str = "primary"

@app.post("/calendar/events")
def gcal_create_event(body: GCalEventCreate):
    svc = _gcal_service()
    if not svc:
        raise HTTPException(503, "calendar not configured")
    event = {
        "summary": body.title,
        "location": body.location,
        "description": body.description,
        "start": {"dateTime": body.start, "timeZone": "America/New_York"},
        "end":   {"dateTime": body.end,   "timeZone": "America/New_York"},
    }
    created = svc.events().insert(calendarId=body.calendar_id, body=event).execute()
    return {"ok": True, "event_id": created["id"], "link": created.get("htmlLink")}


# ── Knowledge Layer ────────────────────────────────────────────────────────────

SESSIONS_DIR = VAULT_PATH / "60_Council" / "sessions"
DECISIONS_DIR = VAULT_PATH / "60_Council" / "decisions"

@app.get("/knowledge/sessions")
def list_all_sessions():
    """List all session summaries grouped by channel."""
    if not SESSIONS_DIR.exists():
        return {"sessions": {}}
    result = {}
    for ch_dir in sorted(SESSIONS_DIR.iterdir()):
        if ch_dir.is_dir():
            files = sorted(ch_dir.glob("*.md"), reverse=True)
            result[ch_dir.name] = []
            for f in files:
                first_line = f.read_text(encoding="utf-8").split("\n")[0].replace("# ", "").strip()
                result[ch_dir.name].append({
                    "filename": f.name,
                    "path": f"60_Council/sessions/{ch_dir.name}/{f.name}",
                    "title": first_line,
                    "channel": ch_dir.name,
                })
    return {"sessions": result}

@app.get("/knowledge/session")
def read_session(path: str):
    """Read a specific session file. path = relative vault path."""
    target = VAULT_PATH / path
    if not target.exists() or not str(target).startswith(str(VAULT_PATH)):
        raise HTTPException(404, "Session not found")
    return {"path": path, "content": target.read_text(encoding="utf-8")}

@app.get("/knowledge/decisions")
def list_decisions():
    """List all decision entries across all monthly files."""
    if not DECISIONS_DIR.exists():
        return {"decisions": [], "files": []}
    files = sorted(DECISIONS_DIR.glob("*.md"), reverse=True)
    all_files = []
    for f in files:
        all_files.append({
            "filename": f.name,
            "path": f"60_Council/decisions/{f.name}",
            "month": f.stem,
        })
    return {"files": all_files}

@app.get("/knowledge/decisions/{month}")
def read_decisions_month(month: str):
    """Read decisions for a given month (YYYY-MM)."""
    target = DECISIONS_DIR / f"{month}.md"
    if not target.exists():
        raise HTTPException(404, f"No decisions for {month}")
    return {"month": month, "content": target.read_text(encoding="utf-8")}


# ── Specialists ────────────────────────────────────────────────────────────────

SPECIALISTS_FILE = VAULT_PATH / "00_System" / "specialists.json"

@app.get("/specialists")
def list_specialists():
    if not SPECIALISTS_FILE.exists():
        return {"specialists": []}
    import json as _sj
    return {"specialists": _sj.loads(SPECIALISTS_FILE.read_text())}

# ── n8n Workflow Registry ──────────────────────────────────────────────────────

N8N_REGISTRY_FILE = VAULT_PATH / "00_System" / "n8n_workflows.json"

@app.get("/n8n/workflows")
def list_n8n_workflows():
    if not N8N_REGISTRY_FILE.exists():
        return {"workflows": {}}
    import json as _nj
    return {"workflows": _nj.loads(N8N_REGISTRY_FILE.read_text())}

@app.post("/n8n/workflows")
def register_n8n_workflow(body: dict):
    import json as _nj
    registry = _nj.loads(N8N_REGISTRY_FILE.read_text()) if N8N_REGISTRY_FILE.exists() else {}
    name = body.get("name")
    if not name:
        raise HTTPException(400, "name required")
    registry[name] = {
        "webhook_url": body.get("webhook_url", ""),
        "description": body.get("description", ""),
    }
    N8N_REGISTRY_FILE.write_text(_nj.dumps(registry, indent=2))
    return {"ok": True, "name": name}


# ── Slack Tier 2 Approval Queue ────────────────────────────────────────────────

import uuid as _uuid
from datetime import datetime as _dt

T2_QUEUE_FILE = VAULT_PATH / "00_System" / "t2_queue.json"


def _t2_load() -> list:
    if T2_QUEUE_FILE.exists():
        return json.loads(T2_QUEUE_FILE.read_text())
    return []


def _t2_save(queue: list):
    T2_QUEUE_FILE.write_text(json.dumps(queue, indent=2))


class T2ActionRequest(BaseModel):
    action: str          # Short description
    detail: str = ""     # Full detail for audit
    advisor: str = "kai" # Which advisor is requesting
    slack_channel: str = "kai" # Which slack channel to post approval request


@app.get("/t2/queue")
def get_t2_queue():
    return {"queue": _t2_load()}


@app.post("/t2/queue")
def create_t2_action(req: T2ActionRequest):
    """Queue a T2 action for Slack approval. Posts a message to Slack."""
    queue = _t2_load()
    action_id = str(_uuid.uuid4())[:8]
    entry = {
        "id": action_id,
        "action": req.action,
        "detail": req.detail,
        "advisor": req.advisor,
        "status": "pending",
        "created_at": _dt.now().isoformat(),
        "slack_ts": None,
        "slack_channel_id": None,
    }

    # Post to Slack for approval
    slack_token = Path("/run/secrets/slack_bot_token").read_text().strip() if Path("/run/secrets/slack_bot_token").exists() else ""
    if slack_token:
        try:
            import httpx as _t2hx
            msg_text = (
                f"⚡ *T2 Action Request* — `{action_id}`\n"
                f"*Advisor:* {req.advisor.upper()}\n"
                f"*Action:* {req.action}\n"
                f"{('*Detail:* ' + req.detail) if req.detail else ''}\n\n"
                f"React with ✅ to approve, ❌ to reject."
            )
            r = _t2hx.post(
                "https://slack.com/api/chat.postMessage",
                headers={"Authorization": f"Bearer {slack_token}"},
                json={"channel": req.slack_channel, "text": msg_text},
                timeout=10,
            )
            d = r.json()
            if d.get("ok"):
                entry["slack_ts"] = d.get("ts")
                entry["slack_channel_id"] = d.get("channel")
        except Exception as e:
            log.error(f"T2 Slack post error: {e}")

    queue.append(entry)
    _t2_save(queue)
    return {"ok": True, "id": action_id, "entry": entry}


@app.post("/t2/approve/{action_id}")
def approve_t2_action(action_id: str):
    """Mark T2 action as approved (called by Slack bot on ✅ reaction)."""
    queue = _t2_load()
    for entry in queue:
        if entry["id"] == action_id:
            entry["status"] = "approved"
            entry["approved_at"] = _dt.now().isoformat()
            _t2_save(queue)
            log.info(f"T2 action {action_id} approved: {entry['action']}")
            return {"ok": True, "entry": entry}
    raise HTTPException(404, f"T2 action {action_id} not found")


@app.post("/t2/reject/{action_id}")
def reject_t2_action(action_id: str):
    """Mark T2 action as rejected."""
    queue = _t2_load()
    for entry in queue:
        if entry["id"] == action_id:
            entry["status"] = "rejected"
            entry["rejected_at"] = _dt.now().isoformat()
            _t2_save(queue)
            return {"ok": True, "entry": entry}
    raise HTTPException(404, f"T2 action {action_id} not found")


# ── Telegram Bot Integration ───────────────────────────────────────────────────

from pathlib import Path as _TGPath
import httpx as _tghttpx

TELEGRAM_API = "https://api.telegram.org"

def _tg_token() -> str:
    p = _TGPath("/run/secrets/telegram_bot_token")
    if p.exists():
        return p.read_text().strip()
    return os.environ.get("TELEGRAM_BOT_TOKEN", "")

def _tg_send(chat_id: int, text: str):
    token = _tg_token()
    if not token:
        return
    try:
        _tghttpx.post(
            f"{TELEGRAM_API}/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
            timeout=15,
        )
    except Exception as e:
        log.error(f"Telegram send error: {e}")


class TelegramUpdate(BaseModel):
    update_id: int
    message: dict | None = None
    callback_query: dict | None = None


@app.post("/telegram/webhook")
async def telegram_webhook(update: TelegramUpdate):
    """Receive Telegram update, route to KAI council, reply via Telegram."""
    msg = update.message
    if not msg:
        return {"ok": True}

    chat_id = msg.get("chat", {}).get("id")
    text = msg.get("text", "").strip()
    username = msg.get("from", {}).get("username", "unknown")

    if not text or not chat_id:
        return {"ok": True}

    # /start command
    if text == "/start":
        _tg_send(chat_id, "🤖 *KAI online.* Send me a message and I'll respond.")
        return {"ok": True}

    log.info(f"Telegram msg from @{username} ({chat_id}): {text[:60]}")

    try:
        # Forward to KAI council chief
        r = _tghttpx.post(
            "http://kai-council-api:8002/message",
            json={"channel": "chief", "message": text, "user_id": f"telegram:{username}"},
            timeout=90,
        )
        r.raise_for_status()
        data = r.json()
        reply = data.get("reply", "No response.")
    except Exception as e:
        log.error(f"Council API error from Telegram: {e}")
        reply = "⚠️ KAI is temporarily unavailable. Try again in a moment."

    _tg_send(chat_id, reply)
    return {"ok": True}


@app.get("/telegram/status")
def telegram_status():
    """Check if Telegram bot is configured and get bot info."""
    token = _tg_token()
    if not token:
        return {"configured": False, "error": "No telegram_bot_token secret"}
    try:
        r = _tghttpx.get(f"{TELEGRAM_API}/bot{token}/getMe", timeout=10)
        if r.status_code == 200:
            bot = r.json().get("result", {})
            return {"configured": True, "bot": bot}
        return {"configured": False, "error": r.text[:200]}
    except Exception as e:
        return {"configured": False, "error": str(e)}


@app.post("/telegram/register-webhook")
def telegram_register_webhook(body: dict):
    """Register the Telegram webhook URL."""
    token = _tg_token()
    if not token:
        raise HTTPException(500, "No telegram_bot_token secret")
    webhook_url = body.get("url", "https://kai.sonicink.space/api/telegram/webhook")
    r = _tghttpx.post(
        f"{TELEGRAM_API}/bot{token}/setWebhook",
        json={"url": webhook_url},
        timeout=15,
    )
    return r.json()


# ── Contacts Registry ──────────────────────────────────────────────────────────

import uuid as _cuuid
from datetime import datetime as _cdt

CONTACTS_FILE = VAULT_PATH / "00_System" / "contacts.json"


def _contacts_load() -> list:
    if CONTACTS_FILE.exists():
        return json.loads(CONTACTS_FILE.read_text())
    return []


def _contacts_save(contacts: list):
    CONTACTS_FILE.write_text(json.dumps(contacts, indent=2))


@app.get("/contacts")
def list_contacts():
    return {"contacts": _contacts_load()}


@app.post("/contacts")
def add_contact(body: dict):
    contacts = _contacts_load()
    name = body.get("name", "").strip()
    if not name:
        raise HTTPException(400, "name required")
    contact = {
        "id": body.get("id") or name.lower().replace(" ", "-"),
        "name": name,
        "aliases": body.get("aliases", [name.lower().split()[0]]),
        "email": body.get("email", ""),
        "slack_id": body.get("slack_id"),
        "role": body.get("role", ""),
        "notes": body.get("notes", ""),
    }
    # Remove duplicate if same id
    contacts = [c for c in contacts if c["id"] != contact["id"]]
    contacts.append(contact)
    _contacts_save(contacts)
    return {"ok": True, "contact": contact}


@app.patch("/contacts/{contact_id}")
def update_contact(contact_id: str, body: dict):
    contacts = _contacts_load()
    for c in contacts:
        if c["id"] == contact_id:
            c.update({k: v for k, v in body.items() if k != "id"})
            _contacts_save(contacts)
            return {"ok": True, "contact": c}
    raise HTTPException(404, f"Contact {contact_id} not found")


@app.get("/contacts/lookup")
def lookup_contact(q: str):
    """Look up a contact by name, alias, or email."""
    contacts = _contacts_load()
    q_lower = q.lower().strip()
    for c in contacts:
        if (q_lower == c["id"] or
            q_lower in [a.lower() for a in c.get("aliases", [])] or
            q_lower in c.get("name", "").lower() or
            q_lower == c.get("email", "").lower()):
            return {"found": True, "contact": c}
    return {"found": False, "query": q}


# ── Slack Channel Management ───────────────────────────────────────────────────

import httpx as _slhx
from pathlib import Path as _slp


def _slack_token() -> str:
    p = _slp("/run/secrets/slack_bot_token")
    return p.read_text().strip() if p.exists() else os.environ.get("SLACK_BOT_TOKEN", "")


def _slack_api(method: str, payload: dict) -> dict:
    token = _slack_token()
    r = _slhx.post(
        f"https://slack.com/api/{method}",
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
        timeout=15,
    )
    return r.json()


def _slack_get(method: str, params: dict) -> dict:
    token = _slack_token()
    r = _slhx.get(
        f"https://slack.com/api/{method}",
        headers={"Authorization": f"Bearer {token}"},
        params=params,
        timeout=15,
    )
    return r.json()


@app.post("/slack/channels")
def create_slack_channel(body: dict):
    """Create a new Slack channel."""
    name = body.get("name", "").lower().replace(" ", "-").replace("_", "-").strip("-")
    if not name:
        raise HTTPException(400, "name required")
    is_private = body.get("private", False)
    result = _slack_api("conversations.create", {"name": name, "is_private": is_private})
    if not result.get("ok"):
        # Channel might already exist
        error = result.get("error", "unknown")
        if error == "name_taken":
            return {"ok": False, "error": "Channel already exists", "name": name}
        raise HTTPException(400, f"Slack error: {error}")
    channel = result["channel"]
    return {"ok": True, "channel_id": channel["id"], "name": channel["name"]}


@app.post("/slack/channels/{channel_name}/invite")
def invite_to_slack_channel(channel_name: str, body: dict):
    """Invite users to a Slack channel by email or user ID."""
    emails = body.get("emails", [])
    user_ids = list(body.get("user_ids", []))

    # Look up emails → user IDs
    not_found = []
    for email in emails:
        res = _slack_get("users.lookupByEmail", {"email": email})
        if res.get("ok"):
            user_ids.append(res["user"]["id"])
        else:
            not_found.append(email)

    if not user_ids:
        raise HTTPException(400, "No valid users found")

    # Find channel ID by name
    channel_id = None
    res = _slack_get("conversations.list", {"types": "public_channel,private_channel", "limit": 200})
    for ch in res.get("channels", []):
        if ch["name"] == channel_name.lstrip("#"):
            channel_id = ch["id"]
            break

    if not channel_id:
        raise HTTPException(404, f"Channel #{channel_name} not found")

    result = _slack_api("conversations.invite", {"channel": channel_id, "users": ",".join(user_ids)})
    return {
        "ok": result.get("ok"),
        "invited": user_ids,
        "not_found_emails": not_found,
        "error": result.get("error") if not result.get("ok") else None,
    }


@app.get("/slack/users/lookup")
def slack_lookup_user(email: str = None, name: str = None):
    """Look up a Slack user by email or display name."""
    if email:
        res = _slack_get("users.lookupByEmail", {"email": email})
        if res.get("ok"):
            u = res["user"]
            return {"found": True, "user_id": u["id"], "name": u["real_name"], "email": email}
        return {"found": False, "error": res.get("error")}
    elif name:
        # Search through member list (expensive — cache contacts)
        res = _slack_get("users.list", {"limit": 200})
        name_lower = name.lower()
        for member in res.get("members", []):
            if name_lower in member.get("real_name", "").lower() or \
               name_lower in member.get("name", "").lower():
                return {"found": True, "user_id": member["id"], "name": member["real_name"]}
        return {"found": False, "name": name}
    raise HTTPException(400, "email or name required")


# ── Project Template System ────────────────────────────────────────────────────

TEMPLATES_PATH = VAULT_PATH / "00_System" / "templates"


def _render_template(content: str, variables: dict) -> str:
    """Replace {{VARIABLE}} placeholders in a template."""
    for key, value in variables.items():
        content = content.replace("{{" + key + "}}", str(value))
    return content


@app.get("/templates")
def list_templates():
    """List all template versions."""
    if not TEMPLATES_PATH.exists():
        return {"versions": []}
    versions = []
    for vdir in sorted(TEMPLATES_PATH.iterdir()):
        if vdir.is_dir():
            manifest_file = vdir / "manifest.json"
            manifest = json.loads(manifest_file.read_text()) if manifest_file.exists() else {}
            versions.append({
                "version": vdir.name,
                "description": manifest.get("description", ""),
                "files": [f.name for f in vdir.iterdir() if f.suffix == ".md"],
                "manifest": manifest,
            })
    return {"versions": versions, "latest": versions[-1]["version"] if versions else None}


@app.get("/templates/{version}/{filename}")
def get_template(version: str, filename: str):
    """Get a specific template file content."""
    tpl_file = TEMPLATES_PATH / version / filename
    if not tpl_file.exists():
        raise HTTPException(404, f"Template {version}/{filename} not found")
    return {"version": version, "filename": filename, "content": tpl_file.read_text()}


@app.post("/templates/{version}")
def create_template_version(version: str, body: dict):
    """Create or update a template version file."""
    vdir = TEMPLATES_PATH / version
    vdir.mkdir(parents=True, exist_ok=True)
    filename = body.get("filename")
    content = body.get("content", "")
    if not filename:
        raise HTTPException(400, "filename required")
    (vdir / filename).write_text(content)
    return {"ok": True, "version": version, "filename": filename}


# ── Full Project Setup Pipeline ────────────────────────────────────────────────

class ProjectSetupRequest(BaseModel):
    id: str                          # slug: "my-project"
    name: str                        # display: "My Project"
    description: str = ""
    advisor: str = "kai"
    status: str = "yellow"
    next: str = ""
    template_version: str = "v1"
    create_slack_channel: bool = True
    slack_channel_name: str = ""     # defaults to project id
    invite_contacts: list = []       # contact IDs or emails to invite (T2 gated)
    url: str = ""


@app.post("/projects/setup")
def setup_project(req: ProjectSetupRequest):
    """
    Full project setup:
    1. Add to projects.json
    2. Create vault/20_Projects/{id}/ directory with template files
    3. Create Slack channel (if requested)
    4. Queue T2 approval for human invites
    Returns a full status report.
    """
    results = {"project_id": req.id, "steps": [], "errors": []}
    today = _cdt.now().strftime("%Y-%m-%d")

    # Step 1: Add to projects.json
    try:
        proj_file = VAULT_PATH / "00_System" / "projects.json"
        projects = json.loads(proj_file.read_text()) if proj_file.exists() else []
        existing = next((p for p in projects if p["id"] == req.id), None)
        if existing:
            results["steps"].append({"step": "projects.json", "status": "skipped", "note": "Already exists"})
        else:
            projects.append({
                "id": req.id,
                "name": req.name,
                "status": req.status,
                "next": req.next or f"Define scope and goals",
                "description": req.description,
                "url": req.url,
                "advisor": req.advisor,
                "active": True,
            })
            proj_file.write_text(json.dumps(projects, indent=2))
            results["steps"].append({"step": "projects.json", "status": "done"})
    except Exception as e:
        results["errors"].append(f"projects.json: {e}")

    # Step 2: Create vault project directory + template files
    slack_channel = req.slack_channel_name or req.id
    template_vars = {
        "PROJECT_NAME": req.name,
        "PROJECT_ID": req.id,
        "DATE": today,
        "ADVISOR": req.advisor,
        "SLACK_CHANNEL": slack_channel,
    }

    proj_dir = VAULT_PATH / "20_Projects" / req.id
    proj_dir.mkdir(parents=True, exist_ok=True)

    tpl_dir = TEMPLATES_PATH / req.template_version
    files_created = []
    if tpl_dir.exists():
        for tpl_file in tpl_dir.glob("*.md"):
            dest = proj_dir / tpl_file.name
            if not dest.exists():
                content = _render_template(tpl_file.read_text(), template_vars)
                dest.write_text(content)
                files_created.append(tpl_file.name)
    results["steps"].append({
        "step": "vault_files",
        "status": "done",
        "path": str(proj_dir),
        "files": files_created,
    })

    # Step 3: Create Slack channel
    channel_id = None
    if req.create_slack_channel:
        try:
            slack_result = _slack_api("conversations.create", {
                "name": slack_channel,
                "is_private": False,
            })
            if slack_result.get("ok"):
                channel_id = slack_result["channel"]["id"]
                results["steps"].append({"step": "slack_channel", "status": "done",
                                          "channel": f"#{slack_channel}", "channel_id": channel_id})
                # Post KAI kickoff message
                _slack_api("chat.postMessage", {
                    "channel": channel_id,
                    "text": f"👋 *{req.name}* project channel is live.\n*Advisor:* {req.advisor.upper()} | *Description:* {req.description or 'TBD'}\n\nI'll be tracking updates here. Drop questions, blockers, and decisions in this channel.",
                    "username": "KAI",
                    "icon_url": "https://kai.sonicink.space/icon-192.png",
                })
            else:
                err = slack_result.get("error", "unknown")
                if err == "name_taken":
                    results["steps"].append({"step": "slack_channel", "status": "skipped",
                                              "note": f"#{slack_channel} already exists"})
                    # Try to find existing channel ID
                    ch_list = _slack_get("conversations.list", {
                        "types": "public_channel,private_channel", "limit": 200
                    })
                    for ch in ch_list.get("channels", []):
                        if ch["name"] == slack_channel:
                            channel_id = ch["id"]
                            break
                else:
                    results["errors"].append(f"slack_channel: {err}")
        except Exception as e:
            results["errors"].append(f"slack_channel: {e}")

    # Step 4: Queue T2 invites for any human contacts
    if req.invite_contacts and channel_id:
        contacts = _contacts_load()
        pending_invites = []
        for contact_ref in req.invite_contacts:
            # Look up by id, alias, name, or treat as email
            match = None
            for c in contacts:
                if (contact_ref == c["id"] or
                    contact_ref.lower() in [a.lower() for a in c.get("aliases", [])] or
                    contact_ref.lower() in c.get("name", "").lower() or
                    contact_ref == c.get("email")):
                    match = c
                    break
            if match:
                pending_invites.append({"name": match["name"], "email": match.get("email"), "slack_id": match.get("slack_id")})
            else:
                pending_invites.append({"name": contact_ref, "email": contact_ref if "@" in contact_ref else None})

        if pending_invites:
            names = ", ".join(p["name"] for p in pending_invites)
            # Queue as T2 action
            import httpx as _t2hx2
            try:
                _t2hx2.post(
                    "http://localhost:8001/t2/queue",
                    json={
                        "action": f"Invite {names} to #{slack_channel}",
                        "detail": f"Project: {req.name} | Channel: #{slack_channel} | People: {names}",
                        "advisor": req.advisor,
                        "slack_channel": "kai",
                    },
                    timeout=5,
                )
                results["steps"].append({
                    "step": "t2_invites",
                    "status": "queued",
                    "people": names,
                    "note": "React ✅ on the Slack approval message to send invites",
                })
            except Exception as e:
                results["errors"].append(f"t2_queue: {e}")

    results["ok"] = len(results["errors"]) == 0
    return results

# ── ICS Calendar Feed (O365 Revolt + PSU) ────────────────────────────────────
import re as _re
from datetime import datetime as _dt, timezone as _tz, timedelta as _td

def _parse_ics(ics_text: str, days: int = 7) -> list:
    """Parse ICS feed. Handles line folding and TZID= datetime formats."""
    # Unfold ICS lines (continuation lines start with space or tab)
    unfolded = []
    for raw in ics_text.splitlines():
        if raw and raw[0] in (" ", "	") and unfolded:
            unfolded[-1] += raw[1:]
        else:
            unfolded.append(raw)

    # TZID offset map — Eastern used by O365, add others as needed
    TZ_OFFSETS = {
        "Eastern Standard Time": -5,
        "Eastern Daylight Time": -4,
        "Central Standard Time": -6,
        "Mountain Standard Time": -7,
        "Pacific Standard Time": -8,
    }

    def _parse_dt(prop: str, val: str):
        """Parse ICS datetime value, return UTC datetime or None."""
        # Determine offset from TZID if present
        offset_h = 0
        tzid_match = _re.search(r"TZID=([^:]+)", prop)
        if tzid_match:
            tzname = tzid_match.group(1)
            offset_h = TZ_OFFSETS.get(tzname, 0)
        if val.endswith("Z"):
            val = val[:-1] + "+00:00"
        try:
            if "T" in val:
                naive = _dt.strptime(val[:15], "%Y%m%dT%H%M%S")
                return (naive - _td(hours=offset_h)).replace(tzinfo=_tz.utc), False
            else:
                return _dt.strptime(val[:8], "%Y%m%d").replace(tzinfo=_tz.utc), True
        except Exception:
            return None, False

    now = _dt.now(_tz.utc)
    cutoff = now + _td(days=days)
    events, current, in_event = [], {}, False

    for line in unfolded:
        if line == "BEGIN:VEVENT":
            in_event, current = True, {}
        elif line == "END:VEVENT" and in_event:
            in_event = False
            start = current.get("start")
            if start and now <= start <= cutoff:
                events.append(current)
        elif in_event:
            if ":" not in line:
                continue
            prop, _, val = line.partition(":")
            prop_name = prop.split(";")[0].upper()
            if prop_name == "SUMMARY":
                current["title"] = val.replace("\,", ",").replace("\n", " ").strip()
            elif prop_name == "DTSTART":
                dt, all_day = _parse_dt(prop, val)
                if dt:
                    current["start"] = dt
                    current["all_day"] = all_day
            elif prop_name == "DTEND":
                dt, _ = _parse_dt(prop, val)
                if dt:
                    current["end"] = dt.isoformat()
            elif prop_name == "LOCATION":
                current["location"] = val.replace("\,", ",").strip()
            elif prop_name == "DESCRIPTION":
                current["preview"] = val.replace("\n", " ")[:120].strip()
            elif prop_name == "ORGANIZER":
                m = _re.search(r"CN=([^;:]+)", prop + ":" + val)
                if m:
                    current["organizer"] = m.group(1)

    events.sort(key=lambda e: e.get("start", now))
    for e in events:
        if isinstance(e.get("start"), _dt):
            e["start"] = e["start"].isoformat()
    return events


ICS_FEEDS_FILE = VAULT_PATH / "00_System" / "ics_feeds.json"

def _load_ics_feeds() -> dict:
    import json as _j
    if ICS_FEEDS_FILE.exists():
        return _j.loads(ICS_FEEDS_FILE.read_text())
    return {}

def _save_ics_feeds(feeds: dict):
    import json as _j
    ICS_FEEDS_FILE.write_text(_j.dumps(feeds, indent=2))


@app.get("/calendar/ics")
def get_ics_calendars(days: int = 7):
    """Fetch all registered ICS feeds and return merged events."""
    import httpx as _hx
    feeds = _load_ics_feeds()
    if not feeds:
        return {"events": [], "accounts": [], "note": "No ICS feeds registered. POST /calendar/ics/register to add one."}
    all_events = []
    errors = []
    for name, url in feeds.items():
        try:
            r = _hx.get(url, timeout=10, follow_redirects=True)
            if r.status_code == 200:
                evts = _parse_ics(r.text, days=days)
                for e in evts:
                    e["account"] = name
                all_events.extend(evts)
            else:
                errors.append(f"{name}: HTTP {r.status_code}")
        except Exception as ex:
            errors.append(f"{name}: {str(ex)}")
    all_events.sort(key=lambda e: e.get("start", ""))
    return {"events": all_events, "accounts": list(feeds.keys()), "count": len(all_events), "days": days, "errors": errors}


class ICSFeedRequest(BaseModel):
    name: str
    url: str

@app.post("/calendar/ics/register")
def register_ics_feed(req: ICSFeedRequest):
    """Register a named ICS feed URL."""
    feeds = _load_ics_feeds()
    feeds[req.name] = req.url
    _save_ics_feeds(feeds)
    return {"ok": True, "name": req.name, "registered": len(feeds)}

@app.delete("/calendar/ics/{name}")
def remove_ics_feed(name: str):
    feeds = _load_ics_feeds()
    if name not in feeds:
        raise HTTPException(status_code=404, detail=f"Feed not found: {name}")
    del feeds[name]
    _save_ics_feeds(feeds)
    return {"ok": True, "removed": name}

@app.get("/calendar/ics/feeds")
def list_ics_feeds():
    feeds = _load_ics_feeds()
    return {"feeds": list(feeds.keys()), "count": len(feeds)}


# ── Advisors ──────────────────────────────────────────────────────────────────

COUNCIL_DIR = VAULT_PATH / "60_Council"

ADVISOR_DISPLAY = {
    "chief": {"name": "KAI (Chief)", "description": "Primary advisor — executive chief of staff", "model": "claude-sonnet-4-6"},
    "ember": {"name": "Ember", "description": "Creative strategist — brand, content, soul", "model": "claude-sonnet-4-6"},
    "doc": {"name": "Doc", "description": "Health advisor — Oura, sleep, recovery, supplements", "model": "claude-sonnet-4-6"},
    "beats": {"name": "Beats", "description": "Music advisor — studio, gear, projects, artists", "model": "claude-sonnet-4-6"},
    "coach": {"name": "Coach", "description": "Performance and mindset coach", "model": "claude-sonnet-4-6"},
    "biz": {"name": "Biz", "description": "Business strategist — finance, ops, growth", "model": "claude-sonnet-4-6"},
    "sky": {"name": "Sky", "description": "Mindfulness and reflection", "model": "claude-sonnet-4-6"},
    "roads": {"name": "Roads", "description": "Travel and logistics", "model": "claude-sonnet-4-6"},
}

@app.get("/advisors")
def list_advisors():
    advisors = []
    if not COUNCIL_DIR.exists():
        return {"advisors": []}
    for d in sorted(COUNCIL_DIR.iterdir()):
        if not d.is_dir() or d.name.startswith("_") or d.name.startswith("."):
            continue
        persona_file = d / f"{d.name.upper()}.md"
        if persona_file.exists():
            display = ADVISOR_DISPLAY.get(d.name, {"name": d.name.title(), "description": "", "model": "claude-sonnet-4-6"})
            advisors.append({
                "id": d.name,
                "name": display["name"],
                "description": display["description"],
                "model": display["model"],
                "has_persona": True,
                "persona_file": str(persona_file.relative_to(VAULT_PATH)),
            })
    return {"advisors": advisors}


@app.get("/advisors/{name}")
def get_advisor(name: str):
    persona_file = COUNCIL_DIR / name / f"{name.upper()}.md"
    if not persona_file.exists():
        raise HTTPException(404, f"Advisor {name} not found")
    return {"name": name, "content": persona_file.read_text(encoding="utf-8")}


class AdvisorUpdateRequest(BaseModel):
    content: str


@app.put("/advisors/{name}")
def update_advisor(name: str, req: AdvisorUpdateRequest):
    persona_file = COUNCIL_DIR / name / f"{name.upper()}.md"
    if not persona_file.exists():
        raise HTTPException(404, f"Advisor {name} not found")
    persona_file.write_text(req.content, encoding="utf-8")
    return {"ok": True, "name": name, "message": f"Persona updated for {name}"}


@app.post("/advisors")
def create_advisor(req: AdvisorUpdateRequest, name: str = ""):
    if not name:
        raise HTTPException(400, "name query parameter required")
    advisor_dir = COUNCIL_DIR / name
    advisor_dir.mkdir(exist_ok=True)
    persona_file = advisor_dir / f"{name.upper()}.md"
    persona_file.write_text(req.content, encoding="utf-8")
    return {"ok": True, "name": name, "created": True}


# ── Wiki ──────────────────────────────────────────────────────────────────────

@app.get("/wiki/tree")
def wiki_tree():
    knowledge_dir = VAULT_PATH / "70_Knowledge"
    if not knowledge_dir.exists():
        return {"tree": []}

    def build_tree(path, rel=""):
        items = []
        try:
            for item in sorted(path.iterdir()):
                rel_path = f"{rel}/{item.name}" if rel else item.name
                if item.name.startswith("."):
                    continue
                if item.is_dir():
                    children = build_tree(item, rel_path)
                    items.append({"type": "dir", "name": item.name, "path": rel_path, "children": children})
                elif item.suffix == ".md":
                    items.append({"type": "file", "name": item.name, "path": rel_path})
        except PermissionError:
            pass
        return items

    return {"tree": build_tree(knowledge_dir)}


@app.get("/wiki/file")
def wiki_file(path: str):
    knowledge_dir = VAULT_PATH / "70_Knowledge"
    full_path = (knowledge_dir / path).resolve()
    if not str(full_path).startswith(str(knowledge_dir.resolve())):
        raise HTTPException(403, "Invalid path")
    if not full_path.exists() or full_path.suffix != ".md":
        raise HTTPException(404, "File not found")
    return {"path": path, "content": full_path.read_text(encoding="utf-8"), "name": full_path.stem}
