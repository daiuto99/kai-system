import logging
from datetime import date as _d
from fastapi import APIRouter, HTTPException
from services.habitsync import get_habits as hs_get_habits, log_habit, unlog_habit

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
