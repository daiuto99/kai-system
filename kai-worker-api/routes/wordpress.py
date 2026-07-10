import base64
import json
import logging
import os
from pathlib import Path
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Body
from routes._destructive_audit import DestructiveRequest, audit_before
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter()

VAULT_PATH = Path("/vault")
WP_SITES_FILE = VAULT_PATH / "00_System" / "wordpress_sites.json"
WP_TASKS_FILE = VAULT_PATH / "00_System" / "wp_task_queue.json"


# ── helpers ──────────────────────────────────────────────────────────────────

def _atomic_write_json(path: Path, data) -> None:
    # Write to sibling tmp then os.replace for crash-safe atomicity.
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, path)


def _safe_json(response):
    # Returns parsed JSON, or an error dict with body preview if the response
    # body is non-JSON (e.g., origin returned an HTML error page).
    try:
        return response.json()
    except (ValueError, json.JSONDecodeError):
        return {
            "_error": "non_json_response",
            "_status_code": response.status_code,
            "_body_preview": response.text[:200],
        }


# Secrets-dir fallback chain — covers all containers (council/orchestrator mount
# /run/wp_secrets; worker mounts /home/leo/kai-system/secrets directly).
SECRETS_CANDIDATES = (
    Path("/run/wp_secrets"),
    Path("/home/leo/kai-system/secrets"),
)


def _load_kai_app_password(slug: str) -> str:
    for base in SECRETS_CANDIDATES:
        p = base / f"wp_{slug}_kai_app_password.txt"
        if p.exists():
            return p.read_text().strip()
    raise HTTPException(
        500,
        f"wp_{slug}_kai_app_password.txt missing from all secrets dirs: "
        f"{[str(c) for c in SECRETS_CANDIDATES]}",
    )


def _write_kai_app_password(slug: str, pw: str) -> Path:
    # Write to the first writable secrets dir. The kai-worker-api mount is
    # read-only by design; this path will typically fail inside the container
    # and the host CLI (scripts/wp_add_site.sh) is the canonical onboarding tool.
    for base in SECRETS_CANDIDATES:
        if not base.exists():
            continue
        p = base / f"wp_{slug}_kai_app_password.txt"
        try:
            p.write_text(pw)
            try:
                p.chmod(0o600)
            except Exception:
                pass
            return p
        except (PermissionError, OSError):
            continue
    raise HTTPException(
        500,
        "Secrets dir is read-only inside this container. "
        "Run scripts/wp_add_site.sh on the host to onboard the site, then call this endpoint with metadata only.",
    )


def _load_sites() -> dict:
    if not WP_SITES_FILE.exists():
        return {}
    sites = json.loads(WP_SITES_FILE.read_text()).get("sites", {})
    for slug, s in sites.items():
        s["_slug"] = slug
    return sites


def _save_sites(sites: dict):
    # Strip injected _slug field before persisting.
    clean = {k: {kk: vv for kk, vv in v.items() if kk != "_slug"} for k, v in sites.items()}
    _atomic_write_json(WP_SITES_FILE, {"sites": clean})


def _get_site(site_id: str) -> dict:
    sites = _load_sites()
    site = sites.get(site_id)
    if not site:
        raise HTTPException(404, f"Site '{site_id}' not found. Known sites: {list(sites.keys())}")
    # Verify a credential exists in the secrets dir (don't read it here).
    if not any((c / f"wp_{site_id}_kai_app_password.txt").exists() for c in SECRETS_CANDIDATES):
        raise HTTPException(
            400,
            f"No app password file for '{site_id}' in any secrets dir. "
            "Run scripts/wp_add_site.sh to onboard.",
        )
    return site


def _base_url(site: dict) -> str:
    fqdn = site.get("cloudways_fqdn")
    return f"https://{fqdn}" if fqdn else site["url"]


def _verify_for(site: dict) -> bool:
    # Cloudways FQDNs do not have a valid TLS cert for the subdomain.
    return not bool(site.get("cloudways_fqdn"))


def _auth_header(site: dict) -> dict:
    pw = _load_kai_app_password(site["_slug"])
    token = base64.b64encode(f"kai:{pw}".encode()).decode()
    return {"Authorization": f"Basic {token}", "Content-Type": "application/json"}


def _load_tasks() -> list:
    if not WP_TASKS_FILE.exists():
        return []
    return json.loads(WP_TASKS_FILE.read_text())


def _save_tasks(tasks: list):
    _atomic_write_json(WP_TASKS_FILE, tasks)


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
    # Password goes to secrets dir; JSON holds metadata only.
    pw_path = _write_kai_app_password(req.site_id, req.app_password)
    sites[req.site_id] = {
        "url": req.url.rstrip("/"),
        "username": req.username,
        "description": req.description,
        "business": req.business,
        "blank_canvas_installed": False,
    }
    _save_sites(sites)
    return {"ok": True, "site_id": req.site_id, "url": req.url, "secret_file": str(pw_path)}


class SiteUpdateRequest(BaseModel):
    # Whitelist of safe fields. Credential / identity fields (app_password,
    # username, url) are intentionally excluded — rotate via add_site flow.
    description: Optional[str] = None
    business: Optional[str] = None
    blank_canvas_installed: Optional[bool] = None


@router.patch("/wordpress/sites/{site_id}")
def update_site(site_id: str, req: SiteUpdateRequest):
    sites = _load_sites()
    if site_id not in sites:
        raise HTTPException(404, f"Site '{site_id}' not found")
    updates = {k: v for k, v in req.dict().items() if v is not None}
    if not updates:
        return {"ok": True, "site_id": site_id, "updated_fields": []}
    sites[site_id].update(updates)
    _save_sites(sites)
    return {"ok": True, "site_id": site_id, "updated_fields": list(updates.keys())}


# ── posts ────────────────────────────────────────────────────────────────────

@router.get("/wordpress/{site_id}/posts")
def get_posts(site_id: str, count: int = 10, status: str = "any", page_type: str = "posts"):
    site = _get_site(site_id)
    endpoint = "pages" if page_type == "pages" else "posts"
    try:
        with httpx.Client(timeout=20, follow_redirects=True, verify=_verify_for(site)) as client:
            params = {"per_page": min(count, 50),
                      "_fields": "id,title,status,date,modified,link,excerpt,slug,template"}
            if status and status != "any":
                params["status"] = status
            r = client.get(
                f"{_base_url(site)}/wp-json/wp/v2/{endpoint}",
                params=params,
                headers=_auth_header(site),
            )
            if r.status_code != 200:
                return {"error": f"WP returned {r.status_code}", "body": r.text[:300]}
            items = _safe_json(r)
            if isinstance(items, dict) and items.get("_error"):
                return {"error": "non-JSON response from origin", **items}
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
        with httpx.Client(timeout=20, follow_redirects=True, verify=_verify_for(site)) as client:
            r = client.get(
                f"{_base_url(site)}/wp-json/wp/v2/{endpoint}/{post_id}",
                headers=_auth_header(site),
            )
            if r.status_code != 200:
                return {"error": f"WP returned {r.status_code}"}
            p = _safe_json(r)
            if isinstance(p, dict) and p.get("_error"):
                return {"error": "non-JSON response from origin", **p}
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
        with httpx.Client(timeout=30, follow_redirects=True, verify=_verify_for(site)) as client:
            tag_ids = []
            if req.tags and endpoint == "posts":
                for tag_name in req.tags:
                    tr = client.get(f"{_base_url(site)}/wp-json/wp/v2/tags",
                                    params={"search": tag_name}, headers=headers, timeout=10)
                    existing = _safe_json(tr) if tr.status_code == 200 else []
                    if isinstance(existing, list) and existing:
                        tag_ids.append(existing[0]["id"])
                    else:
                        cr = client.post(f"{_base_url(site)}/wp-json/wp/v2/tags",
                                         json={"name": tag_name}, headers=headers, timeout=10)
                        if cr.status_code in (200, 201):
                            cj = _safe_json(cr)
                            if isinstance(cj, dict) and cj.get("id") is not None:
                                tag_ids.append(cj["id"])

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

            r = client.post(f"{_base_url(site)}/wp-json/wp/v2/{endpoint}",
                            json=payload, headers=headers, timeout=30)
            if r.status_code not in (200, 201):
                return {"error": f"WP returned {r.status_code}", "body": r.text[:500]}
            p = _safe_json(r)
            if isinstance(p, dict) and p.get("_error"):
                return {"error": "non-JSON response from origin", **p}
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
        with httpx.Client(timeout=30, follow_redirects=True, verify=_verify_for(site)) as client:
            r = client.patch(
                f"{_base_url(site)}/wp-json/wp/v2/{endpoint}/{post_id}",
                json=payload,
                headers=_auth_header(site),
            )
            if r.status_code not in (200, 201):
                return {"error": f"WP returned {r.status_code}", "body": r.text[:300]}
            p = _safe_json(r)
            if isinstance(p, dict) and p.get("_error"):
                return {"error": "non-JSON response from origin", **p}
            return {"updated": True, "id": p.get("id"), "status": p.get("status"), "link": p.get("link")}
    except Exception as e:
        logger.exception("update_post %s/%s: %s", site_id, post_id, e)
        return {"error": str(e)}


@router.post("/wordpress/{site_id}/posts/{post_id}/publish")
def publish_post(site_id: str, post_id: int, post_type: str = "posts"):
    site = _get_site(site_id)
    endpoint = "pages" if post_type == "pages" else "posts"
    try:
        with httpx.Client(timeout=20, follow_redirects=True, verify=_verify_for(site)) as client:
            r = client.patch(
                f"{_base_url(site)}/wp-json/wp/v2/{endpoint}/{post_id}",
                json={"status": "publish"},
                headers=_auth_header(site),
            )
            if r.status_code not in (200, 201):
                return {"error": f"WP returned {r.status_code}"}
            p = _safe_json(r)
            if isinstance(p, dict) and p.get("_error"):
                return {"error": "non-JSON response from origin", **p}
            return {"published": True, "id": p.get("id"), "link": p.get("link"), "status": p.get("status")}
    except Exception as e:
        logger.exception("publish_post %s/%s: %s", site_id, post_id, e)
        return {"error": str(e)}


@router.delete("/wordpress/{site_id}/posts/{post_id}")
def delete_post(site_id: str, post_id: int, body: DestructiveRequest = Body(...), post_type: str = "posts", force: bool = False):
    audit_before("/wordpress/{site_id}/posts/{post_id}", {"site_id": site_id, "post_id": post_id, "force": force}, body.operator, body.reason)
    site = _get_site(site_id)
    endpoint = "pages" if post_type == "pages" else "posts"
    try:
        with httpx.Client(timeout=20, follow_redirects=True, verify=_verify_for(site)) as client:
            r = client.delete(
                f"{_base_url(site)}/wp-json/wp/v2/{endpoint}/{post_id}",
                params={"force": str(force).lower()},
                headers=_auth_header(site),
            )
            if r.status_code not in (200, 201):
                return {"ok": False, "status_code": r.status_code, "body": r.text[:300]}
            data = _safe_json(r)
            # WP REST: force=true returns {"deleted": true, "previous": {...}};
            # force=false returns the post object with status=trash.
            if isinstance(data, dict) and data.get("deleted") is True:
                prev = data.get("previous", {}) or {}
                return {
                    "ok": True,
                    "id": post_id,
                    "permanent": True,
                    "previous_status": prev.get("status"),
                }
            status = data.get("status") if isinstance(data, dict) else None
            return {
                "ok": True,
                "id": post_id,
                "permanent": False,
                "trashed": status == "trash",
                "status": status,
            }
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
        with httpx.Client(timeout=20, follow_redirects=True, verify=_verify_for(site)) as client:
            r = client.post(
                f"{_base_url(site)}/wp-json/wp/v2/settings",
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
        with httpx.Client(timeout=20, follow_redirects=True, verify=_verify_for(site)) as client:
            root_r = client.get(f"{_base_url(site)}/wp-json/", timeout=10)
            d_raw = _safe_json(root_r) if root_r.status_code == 200 else {}
            d = d_raw if isinstance(d_raw, dict) and not d_raw.get("_error") else {}
            pages_r = client.get(
                f"{_base_url(site)}/wp-json/wp/v2/pages",
                params={"per_page": 50, "_fields": "id,title,slug,status,link,template"},
                headers=_auth_header(site),
            )
            pages_raw = _safe_json(pages_r) if pages_r.status_code == 200 else []
            pages = pages_raw if isinstance(pages_raw, list) else []
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
        with httpx.Client(timeout=20, follow_redirects=True, verify=_verify_for(site)) as client:
            r = client.get(
                f"{_base_url(site)}/wp-json/wp/v2/menus",
                headers=_auth_header(site),
            )
            if r.status_code == 404:
                return {"menus": [], "note": "WP REST menus endpoint not available — may need WP Menus REST API plugin"}
            if r.status_code != 200:
                return {"error": f"WP returned {r.status_code}"}
            menus = _safe_json(r)
            if isinstance(menus, dict) and menus.get("_error"):
                return {"error": "non-JSON response from origin", **menus}
            return {"menus": menus}
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
    import datetime, uuid  # noqa: E401
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
