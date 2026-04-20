import json
import logging
from datetime import datetime as _dt
from fastapi import APIRouter
from pydantic import BaseModel
from config import VAULT_PATH

logger = logging.getLogger(__name__)
router = APIRouter()

CHECKIN_FILE = VAULT_PATH / "00_System" / "checkin.json"


@router.get("/checkin")
def get_checkin():
    if CHECKIN_FILE.exists():
        return json.loads(CHECKIN_FILE.read_text())
    return {"intent": "", "date": ""}


class CheckInRequest(BaseModel):
    intent: str = ""
    sleep_quality: str = ""
    restfulness: str = ""


@router.post("/checkin")
def save_checkin(req: CheckInRequest):
    data = {
        "intent": req.intent,
        "sleep_quality": req.sleep_quality,
        "restfulness": req.restfulness,
        "date": _dt.utcnow().strftime("%Y-%m-%d")
    }
    CHECKIN_FILE.parent.mkdir(parents=True, exist_ok=True)
    CHECKIN_FILE.write_text(json.dumps(data, indent=2))
    return data
