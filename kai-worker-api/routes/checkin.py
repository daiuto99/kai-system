import json
import logging
import os
import re
from datetime import datetime as _dt
from pathlib import Path
from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel
from config import VAULT_PATH, safe_path
from safe_http import safe_json

logger = logging.getLogger(__name__)
router = APIRouter()

CHECKIN_FILE   = VAULT_PATH / "00_System" / "checkin.json"
WELLBEING_DIR  = VAULT_PATH / "90_Wellbeing"
PENDING_FILE   = VAULT_PATH / "00_System" / "checkin_pending.json"

MORNING_QUESTIONS = [
    {"id": "sleep",     "n": 1, "label": "How did you sleep?",                "type": "scale5",
     "slack": "1. Sleep quality (1=terrible, 5=great):"},
    {"id": "gi",        "n": 2, "label": "Any GI issues overnight?",           "type": "yesno_notes",
     "slack": "2. GI issues overnight? (yes/no + notes if yes):"},
    {"id": "autonomic", "n": 3, "label": "Any autonomic issues overnight?",    "type": "yesno_notes",
     "slack": "3. Autonomic issues overnight? (yes/no + notes if yes):"},
    {"id": "edible",    "n": 4, "label": "Did you take an edible last night?", "type": "yesno",
     "slack": "4. Edible last night? (yes/no):"},
    {"id": "intention", "n": 5, "label": "What's your intention for today?",   "type": "text",
     "slack": "5. Today's intention:"},
    {"id": "energy",    "n": 6, "label": "Energy right now?",                  "type": "scale5",
     "slack": "6. Energy right now (1=drained, 5=energized):"},
]

EVENING_QUESTIONS = [
    {"id": "day_rating", "n": 1, "label": "How was your day overall?",          "type": "scale5",
     "slack": "1. Day rating (1-5):"},
    {"id": "energy_end", "n": 2, "label": "Energy at end of day?",              "type": "scale5",
     "slack": "2. Energy at end of day (1-5):"},
    {"id": "symptoms",   "n": 3, "label": "Any notable symptoms today?",        "type": "yesno_notes",
     "slack": "3. Symptoms today? (yes/no + notes if yes):"},
    {"id": "wins",       "n": 4, "label": "What went well today?",              "type": "text",
     "slack": "4. Wins today:"},
    {"id": "tomorrow",   "n": 5, "label": "What's on your mind for tomorrow?",  "type": "text",
     "slack": "5. On your mind for tomorrow:"},
]

QUESTION_SETS = {"morning": MORNING_QUESTIONS, "evening": EVENING_QUESTIONS}


def _slack_token() -> str:
    p = Path("/run/secrets/slack_bot_token")
    return p.read_text().strip() if p.exists() else os.environ.get("SLACK_BOT_TOKEN", "")


def _slack_post(channel: str, text: str, thread_ts: str = None) -> dict:
    import httpx
    token = _slack_token()
    payload = {
        "channel": channel, "text": text,
        "username": "KAI", "icon_url": "https://kai.sonicink.space/icon-192.png",
    }
    if thread_ts:
        payload["thread_ts"] = thread_ts
    r = httpx.post("https://slack.com/api/chat.postMessage",
                   headers={"Authorization": f"Bearer {token}"},
                   json=payload, timeout=15)
    return safe_json(r)


def _lookup_channel(name: str) -> str | None:
    import httpx
    token = _slack_token()
    r = httpx.get("https://slack.com/api/conversations.list",
                  headers={"Authorization": f"Bearer {token}"},
                  params={"types": "public_channel,private_channel", "limit": 200},
                  timeout=15)
    for ch in safe_json(r).get("channels", []):
        if ch["name"] == name.lstrip("#"):
            return ch["id"]
    return None


def _parse_answers(text: str, questions: list) -> dict:
    """Parse 'N. answer' lines from Leo's reply into a {question_id: value} dict."""
    answers = {}
    lines = text.strip().splitlines()
    for line in lines:
        m = re.match(r'^(\d+)[.\):\s]\s*(.+)', line.strip())
        if not m:
            continue
        n = int(m.group(1))
        val = m.group(2).strip()
        qs = [q for q in questions if q["n"] == n]
        if not qs:
            continue
        q = qs[0]
        if q["type"] == "scale5":
            try:
                answers[q["id"]] = max(1, min(5, int(val.split()[0])))
            except ValueError:
                answers[q["id"]] = val
        elif q["type"] == "yesno":
            answers[q["id"]] = val.lower().startswith("y")
        elif q["type"] == "yesno_notes":
            first = val.split()[0].lower() if val else "no"
            notes = val[len(val.split()[0]):].strip(" ,-") if val else ""
            answers[q["id"]] = {"yes": first.startswith("y"), "notes": notes}
        else:
            answers[q["id"]] = val
    return answers


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


class SendCheckinRequest(BaseModel):
    checkin_type: str  # "morning" | "evening"
    channel: str = "devops"


@router.post("/checkin/send")
def send_checkin_to_slack(req: SendCheckinRequest):
    questions = QUESTION_SETS.get(req.checkin_type)
    if not questions:
        return {"ok": False, "error": "unknown type"}

    today = _dt.utcnow().strftime("%A, %B %-d")
    emoji = "🌅" if req.checkin_type == "morning" else "🌙"
    label = "Morning" if req.checkin_type == "morning" else "Evening"

    header = f"*{label} Check-in* {emoji} — {today}\nReply to this thread with your answers:\n"
    body = "\n".join(q["slack"] for q in questions)
    text = header + "\n" + body

    # Try lookup first; fall back to posting directly to the channel name
    channel_id = _lookup_channel(req.channel) or req.channel

    result = _slack_post(channel_id, text)
    if not result.get("ok"):
        return {"ok": False, "error": result.get("error")}

    # Use the resolved channel ID from the response if available
    channel_id = result.get("channel", channel_id)
    ts = result["ts"]
    pending = {}
    if PENDING_FILE.exists():
        try:
            pending = json.loads(PENDING_FILE.read_text())
        except Exception:
            pass
    pending[req.checkin_type] = {
        "ts": ts, "channel_id": channel_id,
        "date": _dt.utcnow().strftime("%Y-%m-%d"),
    }
    PENDING_FILE.parent.mkdir(parents=True, exist_ok=True)
    PENDING_FILE.write_text(json.dumps(pending, indent=2))
    logger.info("checkin question sent to Slack: %s ts=%s", req.checkin_type, ts)
    return {"ok": True, "ts": ts, "channel": channel_id}


class SlackReplyRequest(BaseModel):
    checkin_type: str
    text: str
    thread_ts: str = ""
    channel_id: str = ""


@router.post("/checkin/slack-reply")
def handle_slack_reply(req: SlackReplyRequest, background_tasks: BackgroundTasks):
    questions = QUESTION_SETS.get(req.checkin_type, [])
    answers = _parse_answers(req.text, questions)
    if not answers:
        return {"ok": False, "error": "no answers parsed"}

    _save_checkin_data(req.checkin_type, answers)

    if req.thread_ts and req.channel_id:
        background_tasks.add_task(
            _slack_post, req.channel_id,
            f"✓ {req.checkin_type.capitalize()} check-in saved. {len(answers)}/{len(questions)} answered.",
            req.thread_ts,
        )

    pending = {}
    if PENDING_FILE.exists():
        try:
            pending = json.loads(PENDING_FILE.read_text())
        except Exception:
            pass
    pending.pop(req.checkin_type, None)
    PENDING_FILE.write_text(json.dumps(pending, indent=2))

    return {"ok": True, "type": req.checkin_type, "answered": len(answers)}


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
