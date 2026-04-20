import json
import logging
from fastapi import APIRouter
from pydantic import BaseModel
from config import VAULT_PATH

logger = logging.getLogger(__name__)
router = APIRouter()

SETTINGS_FILE = VAULT_PATH / "00_System" / "ui_settings.json"


@router.get("/settings")
def get_settings():
    if SETTINGS_FILE.exists():
        return json.loads(SETTINGS_FILE.read_text())
    return {"working_on": "", "o365_cal_1": "", "o365_cal_2": ""}


class UISettingsRequest(BaseModel):
    working_on: str = ""
    o365_cal_1: str = ""
    o365_cal_2: str = ""


@router.post("/settings")
def save_settings(req: UISettingsRequest):
    data = req.dict()
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(data, indent=2))
    return data
