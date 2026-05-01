import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from config import VAULT_PATH

logger = logging.getLogger(__name__)
router = APIRouter()

LOCATION_FILE = VAULT_PATH / "00_System" / "current_location.json"


class LocationUpdate(BaseModel):
    lat: float
    lon: float
    accuracy: float | None = None
    source: str = "ios_shortcut"


@router.post("/location")
def update_location(body: LocationUpdate):
    data = {
        "lat": body.lat,
        "lon": body.lon,
        "accuracy": body.accuracy,
        "source": body.source,
        "updated": datetime.now(ZoneInfo("America/New_York")).isoformat(),
    }
    LOCATION_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOCATION_FILE.write_text(json.dumps(data, indent=2))
    logger.info(f"Location updated: {body.lat},{body.lon} via {body.source}")
    return {"ok": True, "lat": body.lat, "lon": body.lon}


@router.get("/location")
def get_location():
    if not LOCATION_FILE.exists():
        return {"ok": False, "error": "no_location_set"}
    data = json.loads(LOCATION_FILE.read_text())
    return {"ok": True, **data}
