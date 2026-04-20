import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from services.todoist import (
    get_inbox, get_today, create_task, update_task,
    complete_task as todoist_complete, delete_task, shape_task, move_to_today
)

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/tasks")
def get_tasks():
    try:
        inbox = [shape_task(t) for t in get_inbox()]
        today = [shape_task(t) for t in get_today()]
        inbox.sort(key=lambda t: t["priority"])
        today.sort(key=lambda t: (t["priority"], t["due"] or "9999"))
        return {"today": today, "inbox": inbox}
    except Exception as e:
        logger.exception("get_tasks error: %s", e)
        return {"today": [], "inbox": [], "error": str(e)}


class TaskCreateRequest(BaseModel):
    content: str
    due_date: str = None
    priority: int = 4
    project_id: str = None
    description: str = ""


@router.post("/tasks")
def api_create_task(req: TaskCreateRequest):
    task = create_task(
        content=req.content,
        due_date=req.due_date,
        priority=req.priority,
        project_id=req.project_id,
        description=req.description,
    )
    return shape_task(task)


class TaskUpdateRequest(BaseModel):
    content: str = None
    due_date: str = None
    priority: int = None
    description: str = None
    move_to_today: bool = False


@router.patch("/tasks/{task_id}")
def api_update_task(task_id: str, req: TaskUpdateRequest):
    if req.move_to_today:
        task = move_to_today(task_id)
    else:
        task = update_task(
            task_id,
            content=req.content,
            due_date=req.due_date,
            priority=req.priority,
            description=req.description,
        )
    return shape_task(task) if task else {"ok": True}


@router.post("/tasks/{task_id}/complete")
def api_complete_task(task_id: str):
    ok = todoist_complete(task_id)
    return {"ok": ok}


@router.delete("/tasks/{task_id}")
def api_delete_task(task_id: str):
    ok = delete_task(task_id)
    return {"ok": ok}
