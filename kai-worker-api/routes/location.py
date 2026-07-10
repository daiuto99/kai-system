import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from fastapi import APIRouter, HTTPException  # noqa: F401
from pydantic import BaseModel
from config import VAULT_PATH

logger = logging.getLogger(__name__)
router = APIRouter()

LOCATION_FILE = VAULT_PATH / "00_System" / "current_location.json"


class LocationUpdate(BaseModel):
    lat: float
    lon: float
    accuracy: float | None = None
    timezone: str | None = None   # e.g. "America/Los_Angeles"
    city: str | None = None       # e.g. "Los Angeles, CA"
    source: str = "ios_shortcut"


def _resolve_timezone(lat: float, lon: float, hint: str | None) -> str:
    """Return IANA timezone string. Uses hint if valid, else tries timezonefinder, else UTC."""
    if hint:
        try:
            ZoneInfo(hint)
            return hint
        except (ZoneInfoNotFoundError, KeyError):
            pass
    try:
        from timezonefinder import TimezoneFinder
        tf = TimezoneFinder()
        tz = tf.timezone_at(lat=lat, lng=lon)
        if tz:
            return tz
    except ImportError:
        pass
    return "UTC"


@router.post("/location")
def update_location(body: LocationUpdate):
    tz = _resolve_timezone(body.lat, body.lon, body.timezone)
    data = {
        "lat": body.lat,
        "lon": body.lon,
        "accuracy": body.accuracy,
        "timezone": tz,
        "city": body.city,
        "source": body.source,
        "updated": datetime.now(ZoneInfo(tz)).isoformat(),
    }
    LOCATION_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOCATION_FILE.write_text(json.dumps(data, indent=2))
    logger.info(f"Location updated: {body.lat},{body.lon} tz={tz} via {body.source}")
    return {"ok": True, "lat": body.lat, "lon": body.lon, "timezone": tz}


@router.get("/location")
def get_location():
    if not LOCATION_FILE.exists():
        return {"ok": False, "error": "no_location_set"}
    data = json.loads(LOCATION_FILE.read_text())
    return {"ok": True, **data}
