import json
import logging
from datetime import datetime as _dt
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from config import VAULT_PATH, safe_path

logger = logging.getLogger(__name__)
router = APIRouter()

CHECKIN_FILE   = VAULT_PATH / "00_System" / "checkin.json"
WELLBEING_DIR  = VAULT_PATH / "90_Wellbeing"

# AR-2 (KAI-1243): the Slack send/reply routes + their thread-pending machinery
# were removed — the morning/evening check-in jobs that drove them were retired
# from the scheduler, so that surface was dead code. The dashboard read/save
# routes below (GET/POST /checkin, /checkin/questions, /checkin/history) remain.

MORNING_QUESTIONS = [
    {"id": "sleep",     "n": 1, "label": "How did you sleep?",                "type": "scale5"},
    {"id": "gi",        "n": 2, "label": "Any GI issues overnight?",           "type": "yesno_notes"},
    {"id": "autonomic", "n": 3, "label": "Any autonomic issues overnight?",    "type": "yesno_notes"},
    {"id": "edible",    "n": 4, "label": "Did you take an edible last night?", "type": "yesno"},
    {"id": "intention", "n": 5, "label": "What's your intention for today?",   "type": "text"},
    {"id": "energy",    "n": 6, "label": "Energy right now?",                  "type": "scale5"},
]

EVENING_QUESTIONS = [
    {"id": "day_rating", "n": 1, "label": "How was your day overall?",          "type": "scale5"},
    {"id": "energy_end", "n": 2, "label": "Energy at end of day?",              "type": "scale5"},
    {"id": "symptoms",   "n": 3, "label": "Any notable symptoms today?",        "type": "yesno_notes"},
    {"id": "wins",       "n": 4, "label": "What went well today?",              "type": "text"},
    {"id": "tomorrow",   "n": 5, "label": "What's on your mind for tomorrow?",  "type": "text"},
]

QUESTION_SETS = {"morning": MORNING_QUESTIONS, "evening": EVENING_QUESTIONS}


def _save_checkin_data(checkin_type: str, answers: dict):
    if ".." in checkin_type or "/" in checkin_type:  # H-2: explicit guard before suffix-append safe_path
        raise HTTPException(400, "Invalid checkin_type")
    today = _dt.utcnow().strftime("%Y-%m-%d")
    CHECKIN_FILE.parent.mkdir(parents=True, exist_ok=True)

    existing = {}
    if CHECKIN_FILE.exists():
        try:
            existing = json.loads(CHECKIN_FILE.read_text())
        except Exception:
            pass
    existing[checkin_type] = answers
    existing["date"] = today
    CHECKIN_FILE.write_text(json.dumps(existing, indent=2))

    WELLBEING_DIR.mkdir(parents=True, exist_ok=True)
    vault_path = safe_path(WELLBEING_DIR, f"{today}-{checkin_type}.md")
    if vault_path is None:
        raise HTTPException(400, "Invalid checkin_type")
    questions = QUESTION_SETS.get(checkin_type, [])
    lines = [f"---\ndate: {today}\ntype: {checkin_type}\n---\n"]
    for q in questions:
        val = answers.get(q["id"], "")
        if isinstance(val, dict):
            display = ("Yes" if val.get("yes") else "No")
            if val.get("notes"):
                display += f" — {val['notes']}"
        elif isinstance(val, bool):
            display = "Yes" if val else "No"
        else:
            display = str(val) if val != "" else "(blank)"
        lines.append(f"## {q['label']}\n{display}\n")
    vault_path.write_text("\n".join(lines))
    logger.info("checkin saved: %s %s", today, checkin_type)


@router.get("/checkin")
def get_checkin():
    if CHECKIN_FILE.exists():
        return json.loads(CHECKIN_FILE.read_text())
    return {"date": ""}


@router.get("/checkin/questions/{checkin_type}")
def get_questions(checkin_type: str):
    if ".." in checkin_type:
        raise HTTPException(400, "Invalid type")
    if checkin_type not in QUESTION_SETS:
        return {"questions": []}
    return {"questions": QUESTION_SETS[checkin_type], "type": checkin_type}


class CheckInRequest(BaseModel):
    checkin_type: str = "morning"
    answers: dict = {}


@router.post("/checkin")
def save_checkin(req: CheckInRequest):
    _save_checkin_data(req.checkin_type, req.answers)
    return {"ok": True, "type": req.checkin_type, "date": _dt.utcnow().strftime("%Y-%m-%d")}


@router.get("/checkin/history")
def get_checkin_history(limit: int = 14):
    WELLBEING_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(WELLBEING_DIR.glob("*.md"), reverse=True)[:limit * 2]
    entries = []
    seen_dates = {}
    for f in files:
        parts = f.stem.split("-")
        if len(parts) >= 5:
            date = "-".join(parts[:3])
            checkin_type = parts[3] if len(parts) > 3 else "morning"
        else:
            date = f.stem
            checkin_type = "morning"
        if date not in seen_dates:
            seen_dates[date] = {}
        seen_dates[date][checkin_type] = f.read_text()[:500]
    for date, types in list(seen_dates.items())[:limit]:
        entries.append({"date": date, "types": list(types.keys())})
    return {"entries": entries}
