import json
import logging
from datetime import datetime as _dt
from fastapi import APIRouter
from council_config import VAULT_PATH

logger = logging.getLogger(__name__)
router = APIRouter()

HISTORY_DIR = VAULT_PATH / "60_Council" / "_history"


def _history_file(channel: str):
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    return HISTORY_DIR / f"{channel}.jsonl"


def _append_history(channel: str, role: str, content: str):
    f = _history_file(channel)
    entry = {"role": role, "content": content, "ts": str(_dt.utcnow().timestamp())}
    with open(f, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


@router.get("/history/{channel}")
def get_history(channel: str, limit: int = 50):
    f = _history_file(channel)
    if not f.exists():
        return {"messages": [], "channel": channel}
    lines = f.read_text(encoding="utf-8").strip().splitlines()
    messages = []
    for line in lines[-limit:]:
        try:
            messages.append(json.loads(line))
        except Exception as e:
            logger.exception("parse history line: %s", e)
    return {"messages": messages, "channel": channel}


@router.delete("/history/{channel}")
def clear_history(channel: str):
    f = _history_file(channel)
    if f.exists():
        f.write_text("", encoding="utf-8")
    return {"ok": True, "channel": channel}
