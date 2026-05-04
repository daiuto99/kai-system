import base64
import json
import logging
from pathlib import Path
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter()

VAULT_PATH = Path("/vault")
WP_SITES_FILE = VAULT_PATH / "00_System" / "wordpress_sites.json"
WP_TASKS_FILE = VAULT_PATH / "00_System" / "wp_task_queue.json"


# ── helpers ──────────────────────────────────────────────────────────────────

def _load_sites() -> dict:
    if not WP_SITES_FILE.exists():
        return {}
    return json.loads(WP_SITES_FILE.read_text()).get("sites", {})


def _save_sites(sites: dict):
    WP_SITES_FILE.write_text(json.dumps({"sites": sites}, indent=2))


def _get_site(site_id: str) -> dict:
    sites = _load_sites()
    site = sites.get(site_id)
    if not site:
        raise HTTPException(404, f"Site '{site_id}' not found. Known sites: {list(sites.keys())}")
    if not site.get("app_password"):
        raise HTTPException(400, f"No app_password configured for '{site_id}'")
    return site


def _auth_header(site: dict) -> dict:
    token = base64.b64encode(
        f"{site['username']}:{site['app_password']}".encode()
    ).decode()
    return {"Authorization": f"Basic {token}", "Content-Type": "application/json"}


def _load_tasks() -> list:
    if not WP_TASKS_FILE.exists():
        return []
    return json.loads(WP_TASKS_FILE.read_text())


def _save_tasks(tasks: list):
    WP_TASKS_FILE.write_text(json.dumps(tasks, indent=2))


# ── sites ────────────────────────────────────────────────────────────────────

@router.get("/wordpress/sites")
def list_sites():
    sites = _load_sites()
    return {
        "sites": [
            {
                "id": k,
                "url": v["url"],
                "description": v.get("description", ""),
                "business": v.get("business", ""),
                "blank_canvas_installed": v.get("blank_canvas_installed", False),
            }
            for k, v in sites.items()
        ],
        "count": len(sites),
    }


class SiteAddRequest(BaseModel):
    site_id: str
    url: str
    username: str
    app_password: str
    description: str = ""
    business: str = ""


@router.post("/wordpress/sites")
def add_site(req: SiteAddRequest):
    sites = _load_sites()
    if req.site_id in sites:
        raise HTTPException(409, f"Site '{req.site_id}' already exists")
    sites[req.site_id] = {
        "url": req.url.rstrip("/"),
        "username": req.username,
        "app_password": req.app_password,
        "description": req.description,
        "business": req.business,
        "blank_canvas_installed": False,
    }
    _save_sites(sites)
    return {"ok": True, "site_id": req.site_id, "url": req.url}


@router.patch("/wordpress/sites/{site_id}")
def update_site(site_id: str, req: dict):
    sites = _load_sites()
    if site_id not in sites:
        raise HTTPException(404, f"Site '{site_id}' not found")
    sites[site_id].update(req)
    _save_sites(sites)
    return {"ok": True, "site_id": site_id}


# ── posts ────────────────────────────────────────────────────────────────────

@router.get("/wordpress/{site_id}/posts")
def get_posts(site_id: str, count: int = 10, status: str = "any", page_type: str = "posts"):
    site = _get_site(site_id)
    endpoint = "pages" if page_type == "pages" else "posts"
    try:
        with httpx.Client(timeout=20, follow_redirects=True) as client:
            params = {"per_page": min(count, 50),
                      "_fields": "id,title,status,date,modified,link,excerpt,slug,template"}
            if status and status != "any":
                params["status"] = status
            r = client.get(
                f"{site['url']}/wp-json/wp/v2/{endpoint}",
                params=params,
                headers=_auth_header(site),
            )
            if r.status_code != 200:
                return {"error": f"WP returned {r.status_code}", "body": r.text[:300]}
            items = r.json()
            return {
                "site": site_id,
                "url": site["url"],
                "type": endpoint,
                "items": [
                    {
                        "id": p.get("id"),
                        "title": p.get("title", {}).get("rendered", ""),
                        "status": p.get("status"),
                        "date": p.get("date", "")[:10],
                        "modified": p.get("modified", "")[:10],
                        "link": p.get("link"),
                        "slug": p.get("slug"),
                        "template": p.get("template", ""),
                        "excerpt": p.get("excerpt", {}).get("rendered", "")[:200],
                    }
                    for p in items
                ],
                "count": len(items),
            }
    except Exception as e:
        logger.exception("get_posts %s: %s", site_id, e)
        return {"error": str(e)}


@router.get("/wordpress/{site_id}/posts/{post_id}")
def get_post(site_id: str, post_id: int, post_type: str = "posts"):
    site = _get_site(site_id)
    endpoint = "pages" if post_type == "pages" else "posts"
    try:
        with httpx.Client(timeout=20, follow_redirects=True) as client:
            r = client.get(
                f"{site['url']}/wp-json/wp/v2/{endpoint}/{post_id}",
                headers=_auth_header(site),
            )
            if r.status_code != 200:
                return {"error": f"WP returned {r.status_code}"}
            p = r.json()
            return {
                "id": p.get("id"),
                "title": p.get("title", {}).get("rendered", ""),
                "content": p.get("content", {}).get("rendered", ""),
                "status": p.get("status"),
                "date": p.get("date", "")[:10],
                "link": p.get("link"),
                "slug": p.get("slug"),
                "template": p.get("template", ""),
            }
    except Exception as e:
        logger.exception("get_post %s/%s: %s", site_id, post_id, e)
        return {"error": str(e)}


class PostCreateRequest(BaseModel):
    title: str
    content: str
    status: str = "draft"
    slug: Optional[str] = None
    excerpt: Optional[str] = None
    tags: Optional[list] = None
    categories: Optional[list] = None
    template: str = ""
    post_type: str = "posts"


@router.post("/wordpress/{site_id}/posts")
def create_post(site_id: str, req: PostCreateRequest):
    site = _get_site(site_id)
    endpoint = "pages" if req.post_type == "pages" else "posts"
    headers = _auth_header(site)
    try:
        with httpx.Client(timeout=30, follow_redirects=True) as client:
            tag_ids = []
            if req.tags and endpoint == "posts":
                for tag_name in req.tags:
                    tr = client.get(f"{site['url']}/wp-json/wp/v2/tags",
                                    params={"search": tag_name}, headers=headers, timeout=10)
                    existing = tr.json() if tr.status_code == 200 else []
                    if existing:
                        tag_ids.append(existing[0]["id"])
                    else:
                        cr = client.post(f"{site['url']}/wp-json/wp/v2/tags",
                                         json={"name": tag_name}, headers=headers, timeout=10)
                        if cr.status_code in (200, 201):
                            tag_ids.append(cr.json().get("id"))

            payload = {
                "title": req.title,
                "content": req.content,
                "status": req.status,
                "template": req.template,
            }
            if req.excerpt:
                payload["excerpt"] = req.excerpt
            if req.slug:
                payload["slug"] = req.slug
            if tag_ids:
                payload["tags"] = tag_ids

            r = client.post(f"{site['url']}/wp-json/wp/v2/{endpoint}",
                            json=payload, headers=headers, timeout=30)
            if r.status_code not in (200, 201):
                return {"error": f"WP returned {r.status_code}", "body": r.text[:500]}
            p = r.json()
            return {
                "created": True,
                "id": p.get("id"),
                "status": p.get("status"),
                "link": p.get("link"),
                "slug": p.get("slug"),
                "title": req.title,
                "site": site_id,
                "type": endpoint,
                "message": f"{'Published' if req.status == 'publish' else 'Draft saved'} on {site['url']}",
            }
    except Exception as e:
        logger.exception("create_post %s: %s", site_id, e)
        return {"error": str(e)}


class PostUpdateRequest(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    status: Optional[str] = None
    excerpt: Optional[str] = None
    slug: Optional[str] = None
    template: Optional[str] = None
    post_type: str = "posts"


@router.patch("/wordpress/{site_id}/posts/{post_id}")
def update_post(site_id: str, post_id: int, req: PostUpdateRequest):
    site = _get_site(site_id)
    endpoint = "pages" if req.post_type == "pages" else "posts"
    payload = {k: v for k, v in req.dict(exclude={"post_type"}).items() if v is not None}
    try:
        with httpx.Client(timeout=30, follow_redirects=True) as client:
            r = client.patch(
                f"{site['url']}/wp-json/wp/v2/{endpoint}/{post_id}",
                json=payload,
                headers=_auth_header(site),
            )
            if r.status_code not in (200, 201):
                return {"error": f"WP returned {r.status_code}", "body": r.text[:300]}
            p = r.json()
            return {"updated": True, "id": p.get("id"), "status": p.get("status"), "link": p.get("link")}
    except Exception as e:
        logger.exception("update_post %s/%s: %s", site_id, post_id, e)
        return {"error": str(e)}


@router.post("/wordpress/{site_id}/posts/{post_id}/publish")
def publish_post(site_id: str, post_id: int, post_type: str = "posts"):
    site = _get_site(site_id)
    endpoint = "pages" if post_type == "pages" else "posts"
    try:
        with httpx.Client(timeout=20, follow_redirects=True) as client:
            r = client.patch(
                f"{site['url']}/wp-json/wp/v2/{endpoint}/{post_id}",
                json={"status": "publish"},
                headers=_auth_header(site),
            )
            if r.status_code not in (200, 201):
                return {"error": f"WP returned {r.status_code}"}
            p = r.json()
            return {"published": True, "id": p.get("id"), "link": p.get("link"), "status": p.get("status")}
    except Exception as e:
        logger.exception("publish_post %s/%s: %s", site_id, post_id, e)
        return {"error": str(e)}


@router.delete("/wordpress/{site_id}/posts/{post_id}")
def delete_post(site_id: str, post_id: int, post_type: str = "posts", force: bool = False):
    site = _get_site(site_id)
    endpoint = "pages" if post_type == "pages" else "posts"
    try:
        with httpx.Client(timeout=20, follow_redirects=True) as client:
            r = client.delete(
                f"{site['url']}/wp-json/wp/v2/{endpoint}/{post_id}",
                params={"force": str(force).lower()},
                headers=_auth_header(site),
            )
            return {"deleted": r.status_code in (200, 201), "status_code": r.status_code}
    except Exception as e:
        logger.exception("delete_post %s/%s: %s", site_id, post_id, e)
        return {"error": str(e)}


# ── site-level settings ───────────────────────────────────────────────────────

class CustomCSSRequest(BaseModel):
    css: str


@router.put("/wordpress/{site_id}/custom-css")
def set_custom_css(site_id: str, req: CustomCSSRequest):
    site = _get_site(site_id)
    try:
        with httpx.Client(timeout=20, follow_redirects=True) as client:
            r = client.post(
                f"{site['url']}/wp-json/wp/v2/settings",
                json={"custom_css": req.css},
                headers=_auth_header(site),
            )
            if r.status_code not in (200, 201):
                return {"error": f"WP returned {r.status_code}", "body": r.text[:300]}
            return {"ok": True, "site": site_id, "message": "Custom CSS updated"}
    except Exception as e:
        logger.exception("set_custom_css %s: %s", site_id, e)
        return {"error": str(e)}


@router.get("/wordpress/{site_id}/site-info")
def get_site_info(site_id: str):
    site = _get_site(site_id)
    try:
        with httpx.Client(timeout=20, follow_redirects=True) as client:
            root_r = client.get(f"{site['url']}/wp-json/", timeout=10)
            d = root_r.json() if root_r.status_code == 200 else {}
            pages_r = client.get(
                f"{site['url']}/wp-json/wp/v2/pages",
                params={"per_page": 50, "_fields": "id,title,slug,status,link,template"},
                headers=_auth_header(site),
            )
            pages = pages_r.json() if pages_r.status_code == 200 else []
            return {
                "site": site_id,
                "url": site["url"],
                "title": d.get("name", ""),
                "description": d.get("description", ""),
                "pages": [{"id": p["id"], "title": p["title"]["rendered"], "slug": p["slug"],
                           "status": p["status"], "link": p["link"],
                           "template": p.get("template","")} for p in pages],
                "page_count": len(pages),
            }
    except Exception as e:
        logger.exception("get_site_info %s: %s", site_id, e)
        return {"error": str(e)}


# ── navigation menus ──────────────────────────────────────────────────────────

@router.get("/wordpress/{site_id}/menus")
def get_menus(site_id: str):
    site = _get_site(site_id)
    try:
        with httpx.Client(timeout=20, follow_redirects=True) as client:
            r = client.get(
                f"{site['url']}/wp-json/wp/v2/menus",
                headers=_auth_header(site),
            )
            if r.status_code == 404:
                return {"menus": [], "note": "WP REST menus endpoint not available — may need WP Menus REST API plugin"}
            if r.status_code != 200:
                return {"error": f"WP returned {r.status_code}"}
            return {"menus": r.json()}
    except Exception as e:
        logger.exception("get_menus %s: %s", site_id, e)
        return {"error": str(e)}


# ── task queue ────────────────────────────────────────────────────────────────

@router.get("/wordpress/tasks")
def get_wp_tasks(status: Optional[str] = None, site: Optional[str] = None):
    tasks = _load_tasks()
    if status:
        tasks = [t for t in tasks if t.get("status") == status]
    if site:
        tasks = [t for t in tasks if t.get("site") == site]
    return {"tasks": tasks, "count": len(tasks)}


class WPTaskRequest(BaseModel):
    site: str
    type: str
    title: str
    brief: str
    priority: str = "normal"


@router.post("/wordpress/tasks")
def create_wp_task(req: WPTaskRequest):
    import datetime, uuid
    tasks = _load_tasks()
    task = {
        "id": f"wpt-{uuid.uuid4().hex[:8]}",
        "site": req.site,
        "type": req.type,
        "title": req.title,
        "brief": req.brief,
        "priority": req.priority,
        "status": "pending",
        "created": datetime.date.today().isoformat(),
        "result": None,
    }
    tasks.append(task)
    _save_tasks(tasks)
    return {"ok": True, "task": task}


@router.patch("/wordpress/tasks/{task_id}")
def update_wp_task(task_id: str, updates: dict):
    tasks = _load_tasks()
    for t in tasks:
        if t["id"] == task_id:
            t.update(updates)
            _save_tasks(tasks)
            return {"ok": True, "task": t}
    raise HTTPException(404, f"Task '{task_id}' not found")
