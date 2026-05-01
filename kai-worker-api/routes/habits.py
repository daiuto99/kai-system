import logging
from datetime import date as _d
from fastapi import APIRouter, HTTPException
import json, os
from pydantic import BaseModel
from services.habitsync import get_habits as hs_get_habits, log_habit, unlog_habit, create_habit as hs_create_habit, update_habit as hs_update_habit

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/habits")
def get_habits_endpoint():
    try:
        habits = hs_get_habits()
        return {"habits": habits, "date": _d.today().isoformat()}
    except Exception as e:
        logger.exception("get_habits error: %s", e)
        return {"habits": [], "date": _d.today().isoformat(), "error": str(e)}


@router.post("/habits/{habit_id}/complete")
def complete_habit(habit_id: str):
    try:
        result = log_habit(habit_id)
        return {"ok": True, "habit_id": habit_id, **result}
    except Exception as e:
        logger.exception("complete_habit error: %s", e)
        raise HTTPException(500, str(e))


@router.delete("/habits/{habit_id}/complete")
def uncomplete_habit(habit_id: str):
    try:
        result = unlog_habit(habit_id)
        return {"ok": True, "habit_id": habit_id, **result}
    except Exception as e:
        logger.exception("uncomplete_habit error: %s", e)
        raise HTTPException(500, str(e))

ICONS_PATH = "/vault/00_System/habit_icons.json"


class HabitCreate(BaseModel):
    name: str


class IconsUpdate(BaseModel):
    icons: dict


@router.post("/habits")
def create_habit_endpoint(body: HabitCreate):
    try:
        habit = hs_create_habit(body.name.strip())
        return habit
    except Exception as e:
        logger.exception("create_habit error: %s", e)
        raise HTTPException(500, str(e))


@router.get("/habits/icons")
def get_habit_icons():
    try:
        if os.path.exists(ICONS_PATH):
            with open(ICONS_PATH) as f:
                return json.load(f)
    except Exception:
        pass
    return {}


@router.put("/habits/icons")
def put_habit_icons(body: IconsUpdate):
    try:
        os.makedirs(os.path.dirname(ICONS_PATH), exist_ok=True)
        with open(ICONS_PATH, "w") as f:
            json.dump(body.icons, f)
        return {"ok": True}
    except Exception as e:
        logger.exception("put_habit_icons error: %s", e)
        raise HTTPException(500, str(e))


class HabitUpdate(BaseModel):
    emoji: str = ""
    name: str = ""

@router.patch("/habits/{habit_id}")
def update_habit_endpoint(habit_id: str, body: HabitUpdate):
    try:
        habit = hs_update_habit(habit_id, body.emoji.strip(), body.name.strip())
        return habit
    except Exception as e:
        logger.exception("update_habit error: %s", e)
        raise HTTPException(500, str(e))
