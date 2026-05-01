"""Todoist service layer — all task operations KAI needs."""
import httpx
from pathlib import Path
import os

TODOIST_API = "https://api.todoist.com/api/v1"


def _token() -> str:
    p = Path("/run/secrets/todoist_api_key")
    if p.exists():
        return p.read_text().strip()
    return os.environ.get("TODOIST_API_KEY", "")


def _headers() -> dict:
    return {"Authorization": f"Bearer {_token()}"}


def _get(path: str, params: dict = None) -> dict:
    with httpx.Client(timeout=15) as c:
        r = c.get(f"{TODOIST_API}{path}", headers=_headers(), params=params or {})
        r.raise_for_status()
        return r.json()


def _post(path: str, body: dict) -> dict:
    with httpx.Client(timeout=15) as c:
        r = c.post(f"{TODOIST_API}{path}", headers=_headers(), json=body)
        r.raise_for_status()
        return r.json() if r.content else {}


def _delete(path: str) -> bool:
    with httpx.Client(timeout=15) as c:
        r = c.delete(f"{TODOIST_API}{path}", headers=_headers())
        return r.status_code in (200, 204)


def _all_tasks() -> list:
    return _get("/tasks").get("results", [])

def get_today() -> list:
    """Tasks due today or overdue."""
    from datetime import date
    today = date.today().isoformat()
    return [t for t in _all_tasks() if t.get("due") and t["due"]["date"] <= today]

def get_week() -> list:
    """Tasks due in the next 1-7 days (not today/overdue)."""
    from datetime import date, timedelta
    today = date.today().isoformat()
    in7   = (date.today() + timedelta(days=7)).isoformat()
    return [t for t in _all_tasks() if t.get("due") and today < t["due"]["date"] <= in7]

def get_backlog() -> list:
    """Tasks with no due date or due more than 7 days out."""
    from datetime import date, timedelta
    in7 = (date.today() + timedelta(days=7)).isoformat()
    return [t for t in _all_tasks() if not t.get("due") or t["due"]["date"] > in7]

def get_inbox() -> list:
    return get_backlog()


def create_task(content: str, due_date: str = None, priority: int = 4,
                project_id: str = None, description: str = "") -> dict:
    """priority: 1=urgent, 2=high, 3=medium, 4=normal"""
    body = {"content": content, "priority": priority}
    if due_date:
        body["due_date"] = due_date
    if project_id:
        body["project_id"] = project_id
    if description:
        body["description"] = description
    return _post("/tasks", body)


def update_task(task_id: str, content: str = None, due_date: str = None,
                priority: int = None, description: str = None) -> dict:
    body = {}
    if content is not None:    body["content"] = content
    if due_date is not None:   body["due_date"] = due_date
    if priority is not None:   body["priority"] = priority
    if description is not None: body["description"] = description
    with httpx.Client(timeout=15) as c:
        r = c.post(f"{TODOIST_API}/tasks/{task_id}", headers=_headers(), json=body)
        r.raise_for_status()
        return r.json() if r.content else {}


def complete_task(task_id: str) -> bool:
    with httpx.Client(timeout=15) as c:
        r = c.post(f"{TODOIST_API}/tasks/{task_id}/close", headers=_headers())
        return r.status_code in (200, 204)


def move_to_today(task_id: str) -> dict:
    from datetime import date
    return update_task(task_id, due_date=date.today().isoformat())


def reschedule_task(task_id: str, due_date: str) -> dict:
    return update_task(task_id, due_date=due_date)


def delete_task(task_id: str) -> bool:
    return _delete(f"/tasks/{task_id}")


def search_tasks(query: str) -> list:
    """Search all active tasks by keyword (case-insensitive)."""
    data = _get("/tasks")
    tasks = data.get("results", [])
    q = query.lower()
    return [shape_task(t) for t in tasks if q in t.get("content", "").lower()]

def shape_task(t: dict) -> dict:
    """Normalize a Todoist task for the dashboard."""
    return {
        "id":          t.get("id", ""),
        "content":     t.get("content", ""),
        "description": t.get("description", ""),
        "priority":    t.get("priority", 4),
        "due":         t.get("due", {}).get("date") if t.get("due") else None,
        "project_id":  t.get("project_id", ""),
        "labels":      t.get("labels", []),
        "url":         t.get("url", ""),
    }


def get_todoist_projects() -> list:
    data = _get("/projects")
    return data.get("results", [])


def create_todoist_project(name: str) -> dict:
    return _post("/projects", {"name": name})


def delete_todoist_project(project_id: str) -> bool:
    return _delete(f"/projects/{project_id}")
