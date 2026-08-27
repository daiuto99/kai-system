import logging
import os
from pathlib import Path
from datetime import date as _date
from fastapi import APIRouter
from pydantic import BaseModel
from config import VAULT_PATH, load_secret
from focus import run_focus_brief, get_todoist_tasks

logger = logging.getLogger(__name__)
router = APIRouter()

DAILY_CACHE_FILE = VAULT_PATH / "00_System" / "daily_cache.json"


class FocusRequest(BaseModel):
    kai_focus_channel_id: str


@router.post("/focus/run")
def trigger_focus_brief(req: FocusRequest):
    result = run_focus_brief(req.kai_focus_channel_id)
    return result


@router.get("/focus/today")
def focus_today():
    """Return structured focus stack for the web UI (no notification post)."""
    today = _date.today().isoformat()

    def shape(t):
        return {
            "id":       t.get("id", ""),
            "content":  t.get("content", ""),
            "priority": t.get("priority", 4),
            "due":      t.get("due", {}).get("date") if t.get("due") else None,
            "project":  t.get("project_id", ""),
        }

    try:
        import httpx

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

        relevant = [t for t in raw if t.get("due") and t["due"]["date"] <= today_str]
        relevant.sort(key=lambda t: (t.get("priority", 4), t.get("due", {}).get("date", "9999")))

        shaped = [shape(t) for t in relevant]
        no_due = [shape(t) for t in raw if not t.get("due")]
        no_due.sort(key=lambda t: t.get("priority", 4))

        combined = shaped + no_due

        return {
            "top3":      combined[:3],
            "next5":     combined[3:8],
            "remaining": combined[8:],
        }

    except Exception as e:
        logger.exception("focus/today error: %s", e)
        return {"top3": [], "next5": [], "remaining": [], "error": str(e)}


@router.get("/stoic-quote")
def get_stoic_quote():
    import httpx as _hx
    import random as _random

    today = _date.today().isoformat()

    _STOIC = [
        {"content": "You have power over your mind, not outside events. Realize this, and you will find strength.", "author": "Marcus Aurelius"},
        {"content": "The impediment to action advances action. What stands in the way becomes the way.", "author": "Marcus Aurelius"},
        {"content": "Waste no more time arguing about what a good man should be. Be one.", "author": "Marcus Aurelius"},
        {"content": "If it is not right, do not do it; if it is not true, do not say it.", "author": "Marcus Aurelius"},
        {"content": "He who fears death will never do anything worthy of a man who is alive.", "author": "Seneca"},
        {"content": "Luck is what happens when preparation meets opportunity.", "author": "Seneca"},
        {"content": "Begin at once to live, and count each separate day as a separate life.", "author": "Seneca"},
        {"content": "No man is free who is not master of himself.", "author": "Epictetus"},
        {"content": "Make the best use of what is in your power, and take the rest as it happens.", "author": "Epictetus"},
        {"content": "He is a wise man who does not grieve for things he has not, but rejoices for those he has.", "author": "Epictetus"},
        {"content": "First say to yourself what you would be; and then do what you have to do.", "author": "Epictetus"},
        {"content": "Difficulties are things that show a person what they are.", "author": "Epictetus"},
        {"content": "Man conquers the world by conquering himself.", "author": "Zeno of Citium"},
        {"content": "Confine yourself to the present.", "author": "Marcus Aurelius"},
        {"content": "Very little is needed to make a happy life; it is all within yourself, in your way of thinking.", "author": "Marcus Aurelius"},
    ]

    _random.seed(today)
    quote = _random.choice(_STOIC)

    try:
        r = _hx.get("https://api.quotable.io/random?tags=stoicism&maxLength=130", timeout=3)
        if r.status_code == 200:
            d = r.json()
            if d.get("content") and d.get("author"):
                return {"content": d["content"], "author": d["author"]}
    except Exception as e:
        logger.exception("stoic-quote remote fetch: %s", e)

    return quote


@router.get("/weather")
def get_weather():
    import httpx as _httpx
    api_key = os.environ.get("OPENWEATHERMAP_API_KEY", "")
    lat     = os.environ.get("WEATHER_LAT", "")
    lon     = os.environ.get("WEATHER_LON", "")

    if not api_key or not lat or not lon:
        return {"error": "weather_not_configured", "temp": None, "condition": None}

    try:
        url = (
            f"https://api.openweathermap.org/data/2.5/weather"
            f"?lat={lat}&lon={lon}&appid={api_key}&units=imperial"
        )
        with _httpx.Client(timeout=10) as client:
            r = client.get(url)
            r.raise_for_status()
            d = r.json()

        weather_id = d["weather"][0]["id"]
        if weather_id == 800:
            theme = "clear"
        elif weather_id > 800:
            theme = "clouds"
        elif weather_id >= 700:
            theme = "atmosphere"
        elif weather_id >= 600:
            theme = "snow"
        elif weather_id >= 500:
            theme = "rain"
        elif weather_id >= 300:
            theme = "drizzle"
        elif weather_id >= 200:
            theme = "thunderstorm"
        else:
            theme = "clear"

        return {
            "temp":        round(d["main"]["temp"]),
            "feels_like":  round(d["main"]["feels_like"]),
            "condition":   d["weather"][0]["description"],
            "theme":       theme,
            "icon":        d["weather"][0]["icon"],
            "humidity":    d["main"]["humidity"],
            "city":        d.get("name", ""),
        }
    except Exception as e:
        logger.exception("weather error: %s", e)
        return {"error": str(e), "temp": None, "condition": None}


@router.get("/quote")
def get_quote():
    import json
    import httpx as _httpx
    from datetime import date as _d
    today = _d.today().isoformat()

    if DAILY_CACHE_FILE.exists():
        cache = json.loads(DAILY_CACHE_FILE.read_text())
        if cache.get("date") == today and cache.get("quote"):
            return cache["quote"]

    try:
        with _httpx.Client(timeout=10) as client:
            r = client.get("https://api.quotable.io/random?maxLength=150")
            r.raise_for_status()
            d = r.json()
        quote = {"content": d["content"], "author": d["author"]}
    except Exception as e:
        logger.exception("quote fetch error: %s", e)
        quote = {"content": "The secret of getting ahead is getting started.", "author": "Mark Twain"}

    DAILY_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    cache = json.loads(DAILY_CACHE_FILE.read_text()) if DAILY_CACHE_FILE.exists() else {}
    cache["date"] = today
    cache["quote"] = quote
    DAILY_CACHE_FILE.write_text(json.dumps(cache, indent=2))

    return quote
