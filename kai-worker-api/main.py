import json
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

@app.post("/checkin")
def save_checkin(req: CheckInRequest):
    from datetime import datetime as _dt
    data = {"intent": req.intent, "date": _dt.utcnow().strftime("%Y-%m-%d")}
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
