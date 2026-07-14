import hashlib
import json
import logging
import os
from datetime import datetime as _dt
from pathlib import Path

import httpx
from watchdog import _worker_auth
from fastapi import APIRouter, BackgroundTasks, HTTPException

logger = logging.getLogger(__name__)
router = APIRouter()

VAULT = Path("/vault")
COUNCIL_PATH = VAULT / "60_Council"
QDRANT = os.environ.get("QDRANT_URL", "http://kai-qdrant:6333")
OLLAMA = os.environ.get("OLLAMA_URL", "http://kai-ollama:11434")
COUNCIL_API = os.environ.get("COUNCIL_API_URL", "http://kai-council-api:8002")

SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".md", ".txt"}

CATEGORIES = [
    {"id": "web_design",    "label": "Web Design"},
    {"id": "ui_ux",         "label": "UI / UX"},
    {"id": "typography",    "label": "Typography"},
    {"id": "logo",          "label": "Logo"},
    {"id": "marketing",     "label": "Marketing"},
    {"id": "color_palette", "label": "Color Palette"},
    {"id": "tone_voice",    "label": "Tone / Voice"},
    {"id": "content_copy",  "label": "Content & Copy"},
    {"id": "positioning",   "label": "Positioning"},
]

CATEGORY_ALIASES = {
    "1": "web_design", "web": "web_design", "web design": "web_design",
    "2": "ui_ux", "ui": "ui_ux", "ux": "ui_ux", "ui/ux": "ui_ux", "uiux": "ui_ux",
    "3": "typography", "type": "typography", "typography": "typography",
    "4": "logo", "logo": "logo",
    "5": "marketing", "marketing": "marketing",
    "6": "color_palette", "color": "color_palette", "colour": "color_palette",
    "palette": "color_palette", "color palette": "color_palette",
    "7": "tone_voice", "tone": "tone_voice", "voice": "tone_voice",
    "8": "content_copy", "content": "content_copy", "copy": "content_copy", "wording": "content_copy",
    "9": "positioning", "position": "positioning",
}


SPECIALISTS_FILE = VAULT / "00_System" / "specialists.json"
_SPECIALIST_IDS: set | None = None

def _is_specialist(advisor_id: str) -> bool:
    global _SPECIALIST_IDS
    if _SPECIALIST_IDS is None:
        try:
            _SPECIALIST_IDS = {s["id"] for s in json.loads(SPECIALISTS_FILE.read_text())}
        except Exception:
            _SPECIALIST_IDS = set()
    return advisor_id in _SPECIALIST_IDS

def _resources_dir(advisor_id: str) -> Path:
    if _is_specialist(advisor_id):
        return COUNCIL_PATH / "specialists" / advisor_id / "resources"
    return COUNCIL_PATH / advisor_id / "resources"

def _examples_dir(advisor_id: str) -> Path:
    if _is_specialist(advisor_id):
        return COUNCIL_PATH / "specialists" / advisor_id / "knowledge" / "examples"
    return COUNCIL_PATH / advisor_id / "knowledge" / "examples"
# In-memory sessions: { advisor: session_dict }
_sessions: dict = {}


# ── Slack helpers (optional — only fires when channel_id provided) ──────────

def _slack_token() -> str:
    p = Path("/run/secrets/slack_bot_token")
    return p.read_text().strip() if p.exists() else os.environ.get("SLACK_BOT_TOKEN", "")


def _post_slack(channel_id: str, text: str):
    if not channel_id:
        return
    token = _slack_token()
    if not token:
        return
    try:
        httpx.post(
            "https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {token}"},
            json={"channel": channel_id, "text": text,
                  "username": "Creative", "icon_url": "https://kai.sonicink.space/avatar-creative.png"},
            timeout=10,
        )
    except Exception as e:
        logger.error("_post_slack: %s", e)


# ── Qdrant helpers ─────────────────────────────────────────────────────────────

def _embed(text: str) -> list:
    try:
        r = httpx.post(f"{OLLAMA}/api/embed",
            json={"model": "nomic-embed-text", "input": text}, timeout=30)
        return r.json().get("embeddings", [[]])[0]
    except Exception as e:
        logger.error("_embed: %s", e)
        return []


def _upsert_points(advisor: str, points: list):
    try:
        httpx.put(f"{QDRANT}/collections/{advisor}/points?wait=true",
            json={"points": points}, timeout=60)
    except Exception as e:
        logger.error("_upsert_points: %s", e)


# ── Parsing helpers ────────────────────────────────────────────────────────────

def _parse_verdict(text: str) -> str | None:
    t = text.lower().strip()
    if any(w in t for w in ["reference", "ref", "good", "yes", "follow", "direction"]):
        return "reference"
    if any(w in t for w in ["avoid", "no", "not", "bad", "don't", "dont", "reject"]):
        return "avoid"
    return None


def _parse_categories(text: str) -> list:
    found = []
    t = text.lower()
    for kw, cat_id in CATEGORY_ALIASES.items():
        if kw in t and cat_id not in found:
            found.append(cat_id)
    return list(dict.fromkeys(found))


def _parse_annotations(verdict: str, text: str) -> dict:
    positive, negative = [], []
    sentences = [s.strip() for s in text.replace(";", ".").split(".") if s.strip()]
    neg_words = {"don't", "dont", "not", "no", "bad", "hate", "avoid", "wrong", "dislike",
                 "too", "over", "busy", "cold", "heavy", "weak", "poor", "missing"}
    for s in sentences:
        words = set(s.lower().split())
        if words & neg_words:
            negative.append(s)
        else:
            positive.append(s)
    if verdict == "avoid":
        negative, positive = positive + negative, []
    return {"positive": [p for p in positive if p], "negative": [n for n in negative if n]}


# ── Clarifying questions via Creative ─────────────────────────────────────────

def _get_clarifying_questions(session: dict) -> list[str]:
    try:
        cat_labels = [c["label"] for c in CATEGORIES if c["id"] in session.get("categories", [])]
        ctx = (
            f"You are reviewing a design example called '{session['filename']}' for Leo. "
            f"He has told you:\n"
            f"- Verdict: {session['verdict']}\n"
            f"- Categories: {', '.join(cat_labels) or 'general'}\n"
            f"- Notes: {session['notes']}\n\n"
            "Ask exactly 1-2 concise clarifying questions that would help you better understand "
            "his taste and intent. Focus on specifics: is it the typeface or the sizing? "
            "The grid or the overall feel? SonicInk-specific or general direction? "
            "Return ONLY the questions, one per line, no numbering, no preamble."
        )
        r = httpx.post(
            f"{COUNCIL_API}/council",
            json={"channel": "creative", "message": ctx, "user_id": "intake", "history": []},
            timeout=30,
            auth=_worker_auth(),
        )
        reply = r.json().get("reply", "").strip()
        questions = [q.strip() for q in reply.splitlines() if q.strip() and "?" in q]
        return questions[:2]
    except Exception as e:
        logger.error("_get_clarifying_questions: %s", e)
        return []


# ── Completion ─────────────────────────────────────────────────────────────────

def _ingest_annotation(advisor: str, session: dict):
    annotations = session.get("annotations", {})
    source = session["filename"]
    verdict = session["verdict"]
    date = session["started_at"][:10]
    categories = session.get("categories", [])

    points = []
    for sentiment, items in annotations.items():
        for item in items:
            if not item.strip():
                continue
            vec = _embed(item)
            if not vec:
                continue
            chunk_id = hashlib.md5(f"{source}:{sentiment}:{item}".encode()).hexdigest()
            points.append({
                "id": int(chunk_id[:16], 16),
                "vector": vec,
                "payload": {
                    "text": item, "type": "design_example",
                    "sentiment": sentiment, "categories": categories,
                    "source_file": source, "date": date,
                    "verdict": verdict, "advisor": advisor,
                }
            })

    notes = session.get("notes", "")
    if notes:
        vec = _embed(notes)
        if vec:
            chunk_id = hashlib.md5(f"{source}:notes:{notes[:50]}".encode()).hexdigest()
            points.append({
                "id": int(chunk_id[:16], 16),
                "vector": vec,
                "payload": {
                    "text": notes, "type": "design_example",
                    "sentiment": "notes", "categories": categories,
                    "source_file": source, "date": date,
                    "verdict": verdict, "advisor": advisor,
                }
            })

    if points:
        _upsert_points(advisor, points)


def _write_annotation(advisor: str, session: dict):
    examples_dir = _examples_dir(advisor)
    examples_dir.mkdir(parents=True, exist_ok=True)
    date = session["started_at"][:10]
    stem = Path(session["filename"]).stem
    out = examples_dir / f"{date}_{stem}.json"
    data = {
        "source": session["filename"],
        "date": date,
        "advisor": advisor,
        "verdict": session["verdict"],
        "categories": session.get("categories", []),
        "annotations": session.get("annotations", {}),
        "clarifications": session.get("clarifications", []),
        "notes": session.get("notes", ""),
    }
    out.write_text(json.dumps(data, indent=2))
    return str(out)


def _move_to_processed(advisor: str, filename: str):
    resources_dir = _resources_dir(advisor)
    processed_dir = resources_dir / "processed"
    processed_dir.mkdir(exist_ok=True)
    src = resources_dir / filename
    if src.exists():
        src.rename(processed_dir / filename)


def _complete_intake(advisor: str, channel_id: str = "") -> dict:
    session = _sessions.get(advisor)
    if not session:
        return {}
    session["annotations"] = _parse_annotations(session["verdict"], session["notes"])
    _write_annotation(advisor, session)
    _ingest_annotation(advisor, session)
    _move_to_processed(advisor, session["filename"])

    summary = {
        "filename": session["filename"],
        "verdict": session["verdict"],
        "categories": session.get("categories", []),
        "annotations": session.get("annotations", {}),
        "clarifications": session.get("clarifications", []),
        "queue_remaining": len(session.get("queue", [])),
    }
    _sessions.pop(advisor, None)

    if channel_id:
        cat_labels = [c["label"] for c in CATEGORIES if c["id"] in summary["categories"]]
        pos = summary["annotations"].get("positive", [])
        neg = summary["annotations"].get("negative", [])
        parts = []
        if pos: parts.append(f"*Like:* {'; '.join(pos[:3])}")
        if neg: parts.append(f"*Avoid:* {'; '.join(neg[:3])}")
        _post_slack(channel_id,
            f"✓ *{summary['filename']}* saved as *{summary['verdict']}* "
            f"under _{', '.join(cat_labels)}_.\n" + ("\n".join(parts) if parts else ""))

    return summary


# ── API endpoints ──────────────────────────────────────────────────────────────

@router.get("/intake/resources/{advisor}")
def list_resources(advisor: str):
    if ".." in advisor:
        raise HTTPException(400, "Invalid advisor")
    resources_dir = _resources_dir(advisor)
    resources_dir.mkdir(parents=True, exist_ok=True)
    processed_dir = resources_dir / "processed"
    files = [
        {"name": f.name, "size": f.stat().st_size, "ext": f.suffix.lower()}
        for f in sorted(resources_dir.iterdir())
        if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS
    ]
    processed = len(list(processed_dir.glob("*"))) if processed_dir.exists() else 0
    return {"files": files, "processed": processed, "categories": CATEGORIES}


@router.post("/intake/start/{advisor}")
def start_intake(advisor: str, body: dict):
    filename = body.get("filename", "")
    channel_id = body.get("channel_id", "")
    if not filename:
        return {"ok": False, "error": "filename required"}

    resources_dir = _resources_dir(advisor)
    # Build queue from remaining unprocessed files
    all_files = [
        f.name for f in sorted(resources_dir.iterdir())
        if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS and f.name != filename
    ]

    _sessions[advisor] = {
        "filename": filename,
        "stage": "q1",
        "verdict": None,
        "categories": [],
        "notes": "",
        "clarifications": [],
        "clarifying_questions": [],
        "clarifying_index": 0,
        "channel_id": channel_id,
        "started_at": _dt.utcnow().isoformat(),
        "queue": all_files,
    }

    prompt = f"Is this a *reference* example (direction to follow) or an *avoid* example (what not to do)?"
    _post_slack(channel_id, f"Starting Creative intake for *{filename}*\n\n{prompt}")
    return {"ok": True, "stage": "q1", "prompt": prompt}


@router.get("/intake/active/{advisor}")
def intake_active(advisor: str):
    session = _sessions.get(advisor)
    if not session:
        return {"active": False, "stage": None}
    return {
        "active": True,
        "stage": session["stage"],
        "filename": session["filename"],
        "verdict": session.get("verdict"),
        "categories": session.get("categories", []),
        "notes": session.get("notes", ""),
        "clarifying_questions": session.get("clarifying_questions", []),
        "clarifying_index": session.get("clarifying_index", 0),
    }


@router.post("/intake/reply/{advisor}")
def intake_reply(advisor: str, body: dict):
    text = body.get("text", "").strip()
    channel_id = body.get("channel_id", "")

    session = _sessions.get(advisor)
    if not session:
        return {"ok": False, "error": "no active intake"}

    if not channel_id:
        channel_id = session.get("channel_id", "")

    stage = session["stage"]

    if stage == "q1":
        verdict = _parse_verdict(text)
        if not verdict:
            return {"ok": False, "stage": "q1", "error": "Reply 'reference' or 'avoid'",
                    "prompt": "Is this a reference example (direction to follow) or an avoid example (what not to do)?"}
        session["verdict"] = verdict
        session["stage"] = "q2"
        prompt = "What category applies? Select all that apply."
        _post_slack(channel_id, prompt)
        return {"ok": True, "stage": "q2", "prompt": prompt, "categories": CATEGORIES}

    elif stage == "q2":
        cats = _parse_categories(text)
        if not cats:
            return {"ok": False, "stage": "q2", "error": "Pick at least one category",
                    "prompt": "What category applies?", "categories": CATEGORIES}
        session["categories"] = cats
        session["stage"] = "q3"
        cat_labels = [c["label"] for c in CATEGORIES if c["id"] in cats]
        prompt = f"Walk me through what you like or don't like about this {', '.join(cat_labels)} example — be as specific as you want."
        _post_slack(channel_id, prompt)
        return {"ok": True, "stage": "q3", "prompt": prompt}

    elif stage == "q3":
        session["notes"] = text
        # Generate clarifying questions synchronously
        questions = _get_clarifying_questions(session)
        session["clarifying_questions"] = questions
        session["clarifying_index"] = 0
        if questions:
            session["stage"] = "clarifying"
            _post_slack(channel_id, questions[0])
            return {"ok": True, "stage": "clarifying",
                    "current_question": questions[0],
                    "question_index": 0,
                    "question_total": len(questions)}
        else:
            summary = _complete_intake(advisor, channel_id)
            return {"ok": True, "stage": "done", "summary": summary}

    elif stage == "clarifying":
        session["clarifications"].append(text)
        idx = session["clarifying_index"] + 1
        session["clarifying_index"] = idx
        questions = session.get("clarifying_questions", [])
        if idx < len(questions):
            session["stage"] = "clarifying"
            _post_slack(channel_id, questions[idx])
            return {"ok": True, "stage": "clarifying",
                    "current_question": questions[idx],
                    "question_index": idx,
                    "question_total": len(questions)}
        else:
            summary = _complete_intake(advisor, channel_id)
            return {"ok": True, "stage": "done", "summary": summary}

    return {"ok": False, "error": f"unknown stage: {stage}"}


@router.post("/intake/scan")
def scan_resources(body: dict, background_tasks: BackgroundTasks):
    """Slack-driven: scan resources and start intake for first file, posting to Slack."""
    advisor = body.get("advisor", "creative")
    channel_id = body.get("channel_id", "")

    resources_dir = _resources_dir(advisor)
    resources_dir.mkdir(parents=True, exist_ok=True)
    files = [
        f.name for f in sorted(resources_dir.iterdir())
        if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS
    ]

    if not files:
        _post_slack(channel_id,
            f"No files found in Creative's resources folder.\n"
            f"Drop files into `~/vault/60_Council/{advisor}/resources/` and try again.")
        return {"found": 0, "files": []}

    result = start_intake(advisor, {"filename": files[0], "channel_id": channel_id})
    if len(files) > 1:
        _post_slack(channel_id, f"_{len(files) - 1} more file(s) queued after this one._")
    return {"found": len(files), "files": files, "started": files[0]}


@router.delete("/intake/cancel/{advisor}")
def cancel_intake(advisor: str):
    _sessions.pop(advisor, None)
    return {"ok": True, "cancelled": advisor}
