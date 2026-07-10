import logging
from fastapi import APIRouter, HTTPException, Body  # noqa: F401
from routes._destructive_audit import DestructiveRequest, audit_before
from pydantic import BaseModel
from services.todoist import (
    get_inbox, get_today, get_week, get_backlog, create_task, update_task, search_tasks,  # noqa: F401
    complete_task as todoist_complete, delete_task, shape_task, move_to_today,
    get_todoist_projects, create_todoist_project, delete_todoist_project
)

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/tasks")
def get_tasks():
    try:
        today   = [shape_task(t) for t in get_today()]
        week    = [shape_task(t) for t in get_week()]
        backlog = [shape_task(t) for t in get_backlog()]
        today.sort(key=lambda t: (t["priority"], t["due"] or "9999"))
        week.sort(key=lambda t: (t["due"] or "9999", t["priority"]))
        backlog.sort(key=lambda t: (t["priority"], t["due"] or "9999"))
        return {"today": today, "week": week, "backlog": backlog}
    except Exception as e:
        logger.exception("get_tasks error: %s", e)
        return {"today": [], "week": [], "backlog": [], "error": str(e)}


class TaskCreateRequest(BaseModel):
    content: str
    due_date: str = None
    priority: int = 4
    project_id: str = None
    description: str = ""


@router.get("/tasks/search")
def api_search_tasks(q: str = ""):
    if not q:
        return []
    return search_tasks(q)


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
def api_delete_task(task_id: str, body: DestructiveRequest = Body(...)):
    audit_before("/tasks/{task_id}", {"task_id": task_id}, body.operator, body.reason)
    ok = delete_task(task_id)
    return {"ok": ok}


class ProjectCreateRequest(BaseModel):
    name: str


@router.get("/tasks/projects")
def list_projects():
    return {"projects": [{"id": p["id"], "name": p["name"]} for p in get_todoist_projects()]}


@router.post("/tasks/projects")
def api_create_project(req: ProjectCreateRequest):
    return create_todoist_project(req.name)


@router.delete("/tasks/projects/{project_id}")
def api_delete_project(project_id: str, body: DestructiveRequest = Body(...)):
    audit_before("/tasks/projects/{project_id}", {"project_id": project_id}, body.operator, body.reason)
    return {"ok": delete_todoist_project(project_id)}
