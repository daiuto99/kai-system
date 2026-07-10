import logging
from datetime import date as _date, timedelta as _td
from pathlib import Path
from fastapi import APIRouter

logger = logging.getLogger(__name__)
router = APIRouter()


def _oura_token() -> str:
    p = Path("/run/secrets/oura_token")
    return p.read_text().strip() if p.exists() else ""


@router.get("/oura/today")
def oura_today():
    import httpx
    token = _oura_token()
    if not token:
        return {"error": "oura_token not configured"}

    today = _date.today().isoformat()
    yesterday = (_date.today() - _td(days=1)).isoformat()
    headers = {"Authorization": f"Bearer {token}"}
    base = "https://api.ouraring.com/v2/usercollection"
    result = {}

    try:
        r = httpx.get(f"{base}/daily_readiness", params={"start_date": yesterday, "end_date": today},
                      headers=headers, timeout=15)
        data = r.json().get("data", [])
        if data:
            l = data[-1]  # noqa: E741
            c = l.get("contributors", {})
            result["readiness"] = {"score": l.get("score"), "hrv_balance": c.get("hrv_balance"),
                                   "rhr": c.get("resting_heart_rate"), "recovery_index": c.get("recovery_index")}
    except Exception as e:
        logger.warning("Oura readiness: %s", e)

    try:
        r = httpx.get(f"{base}/daily_sleep", params={"start_date": yesterday, "end_date": today},
                      headers=headers, timeout=15)
        data = r.json().get("data", [])
        if data:
            l = data[-1]  # noqa: E741
            c = l.get("contributors", {})
            result["sleep"] = {"score": l.get("score"), "total": c.get("total_sleep"),
                               "rem": c.get("rem_sleep"), "deep": c.get("deep_sleep"),
                               "efficiency": c.get("efficiency"), "restfulness": c.get("restfulness")}
    except Exception as e:
        logger.warning("Oura sleep: %s", e)

    return result if result else {"error": "no oura data available"}
