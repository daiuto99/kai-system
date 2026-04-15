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
