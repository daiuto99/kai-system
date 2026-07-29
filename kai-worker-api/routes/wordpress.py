import base64
import json
import logging
import os
from pathlib import Path
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Body, Header
from routes._destructive_audit import DestructiveRequest, audit_before
from pydantic import BaseModel
from wp_write_guard import WorkflowOnlyWriteViolation, assert_canonical_caller

logger = logging.getLogger(__name__)
router = APIRouter()

VAULT_PATH = Path("/vault")
WP_SITES_FILE = VAULT_PATH / "00_System" / "wordpress_sites.json"
WP_TASKS_FILE = VAULT_PATH / "00_System" / "wp_task_queue.json"


_WP_WRITE_TOKEN_PATHS = (Path("/run/secrets/wp_write_token"),
                        Path("/home/leo/kai-system/secrets/wp_write_token.txt"))


def _expected_wp_write_token() -> str:
    for _p in _WP_WRITE_TOKEN_PATHS:
        if _p.exists():
            return _p.read_text().strip()
    return ""


def _preflight_write(action: str, write_token: Optional[str] = None) -> None:
    # WP-20.6: worker WP writes require the workflow write token. Fail closed:
    # a missing secret or a caller (e.g. council) without the token is rejected.
    expected = _expected_wp_write_token()
    if not expected or write_token != expected:
        try:
            from wp_write_guard import _alert_devops
            _alert_devops("non-workflow caller (missing/invalid WP write token)", action)
        except Exception:
            logger.error("WP write token rejected for %s; devops alert failed", action)
        raise HTTPException(status_code=403,
                            detail="WP writes require the workflow write token (WP-20.6)")
    assert_canonical_caller(__file__, action)


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


class WPWritePreflightRequest(BaseModel):
    caller: str
    action: str

@router.post("/wordpress/write-preflight")
def wordpress_write_preflight(req: WPWritePreflightRequest):
    try:
        caller = assert_canonical_caller(req.caller, req.action)
    except WorkflowOnlyWriteViolation as exc:
        raise HTTPException(403, str(exc)) from exc
    return {"ok": True, "caller": caller, "action": req.action}


@router.post("/wordpress/{site_id}/posts")
def create_post(site_id: str, req: PostCreateRequest, x_wp_write_token: Optional[str] = Header(default=None, alias="X-Wp-Write-Token")):
    _preflight_write("create_post", x_wp_write_token)
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
def update_post(site_id: str, post_id: int, req: PostUpdateRequest, x_wp_write_token: Optional[str] = Header(default=None, alias="X-Wp-Write-Token")):
    _preflight_write("update_post", x_wp_write_token)
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
def publish_post(site_id: str, post_id: int, post_type: str = "posts", x_wp_write_token: Optional[str] = Header(default=None, alias="X-Wp-Write-Token")):
    _preflight_write("publish_post", x_wp_write_token)
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
def delete_post(site_id: str, post_id: int, body: DestructiveRequest = Body(...), post_type: str = "posts", force: bool = False, x_wp_write_token: Optional[str] = Header(default=None, alias="X-Wp-Write-Token")):
    _preflight_write("delete_post", x_wp_write_token)
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
def set_custom_css(site_id: str, req: CustomCSSRequest, x_wp_write_token: Optional[str] = Header(default=None, alias="X-Wp-Write-Token")):
    _preflight_write("set_custom_css", x_wp_write_token)
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
    import datetime
    import uuid
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


# ── MAINTAIN health board (WP-20.6a) ──────────────────────────────────────────
# Read-only per-property status. Invariant: this endpoint performs NO writes and
# opens NO new mutation path — it only reads live sources already on disk.
# Dimensions with no automated reader are reported honestly as not-yet-automated
# rather than faked green (no-theater floor). Scope: docs/WP_DASHBOARD_SCOPE_2026-07-28.md.

WP_BRAND_RESULT_FILE = VAULT_PATH / "00_System" / "wp_brand_consistency_result.json"
WP_PROPERTIES_DIR = VAULT_PATH / "60_Council" / "properties"
WP_DRIFT_STATE_FILE = VAULT_PATH / "00_System" / "wp_drift_state.json"


def _load_drift_state() -> dict:
    """Per-slug persisted brand-drift results (WP-20.2b). Read-only, never raises."""
    if WP_DRIFT_STATE_FILE.exists():
        try:
            return json.loads(WP_DRIFT_STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _read_build_profile(slug: str) -> dict:
    """Read a property's BUILD_PROFILE §9 machine-readable JSON block (read-only).

    Returns {"present": bool, "fonts": [...], "palette_count": int}. Never raises.
    """
    import re
    bp = WP_PROPERTIES_DIR / slug / "BUILD_PROFILE.md"
    if not bp.exists():
        return {"present": False, "fonts": [], "palette_count": 0}
    try:
        text = bp.read_text(encoding="utf-8")
        blocks = re.findall(r"```json\s*(.*?)```", text, re.DOTALL)
        if not blocks:
            return {"present": True, "fonts": [], "palette_count": 0}
        tokens = json.loads(blocks[-1])
        return {
            "present": True,
            "fonts": tokens.get("fonts", []) or [],
            "palette_count": len(tokens.get("palette", []) or []),
        }
    except Exception:
        # A malformed profile still counts as "present"; we just can't parse tokens.
        return {"present": True, "fonts": [], "palette_count": 0}


def _brand_sync_map():
    """Return (per-slug last brand-consistency result, as_of date str) or ({}, None)."""
    import datetime
    if not WP_BRAND_RESULT_FILE.exists():
        return {}, None
    try:
        data = json.loads(WP_BRAND_RESULT_FILE.read_text(encoding="utf-8"))
        as_of = datetime.datetime.utcfromtimestamp(
            WP_BRAND_RESULT_FILE.stat().st_mtime).strftime("%Y-%m-%d")
        return {r.get("site"): r for r in data if isinstance(r, dict)}, as_of
    except Exception:
        return {}, None


@router.get("/wordpress/health")
def wordpress_health():
    """MAINTAIN health board (WP-20.6a) — read-only per-property status.

    Aggregates ONLY live on-disk sources. No writes, no side effects, no new
    mutation path (the WP-20.3/.4 anti-bypass invariant). Per property:
      - brand_profile  : LIVE  — per-property BUILD_PROFILE presence + tokens (WP-20.1)
      - brand_sync     : LIVE  — last wp_brand_consistency run + file date
      - drift          : not_tracked  — detector is inline at write chokepoint; no persisted per-property result yet (WP-20.2b)
      - standards_floor: manual_gate  — WCAG/perf/security/content via LSE gate; no automated checker exists
      - backup         : not_wired    — Cloudways backup freshness not yet integrated
    """
    sites = _load_sites()
    brand_map, brand_as_of = _brand_sync_map()
    drift_state = _load_drift_state()
    rows = []
    for slug, v in sites.items():
        bp = _read_build_profile(slug)
        bs = brand_map.get(slug, {})
        ds = drift_state.get(slug)
        rows.append({
            "slug": slug,
            "url": v.get("url", ""),
            "business": v.get("business", ""),
            "provisioned": v.get("provisioned", False),
            "brand_profile": bp,
            "brand_sync": {
                "present": bool(bs),
                "logo_set": bool(bs.get("site_icon_set")),
                "coming_soon_updated": bool(bs.get("cs_page_updated")),
                "as_of": brand_as_of,
            },
            "drift": (
                {"status": ds.get("status", "no_check"),
                 "checked_at": ds.get("checked_at"),
                 "highs": ds.get("highs"), "warns": ds.get("warns"),
                 "summary": ds.get("summary", ""),
                 "detail": ds.get("summary", "")}
                if ds else
                {"status": "no_check",
                 "detail": "no drift scan run yet — click Scan (WP-20.2b, live homepage read)"}
            ),
            "standards_floor": _standards_row(ds),
            "backup": {"status": "not_wired",
                       "detail": "Cloudways backup-freshness blocked — API token stale (403); needs key refresh (bug f4a0f291)"},
        })
    return {
        "properties": rows,
        "count": len(rows),
        "legend": {
            "live": ["brand_profile", "brand_sync", "drift", "standards_floor (security+content)"],
            "not_automated": ["standards_floor (WCAG/CWV)", "backup"],
        },
        "note": ("MAINTAIN board (WP-20.6a + drift WP-20.2b). Read-only. Drift is a live "
                 "homepage scan (click Scan). standards_floor security+content is computed from the same live scan (WCAG/CWV stay manual); backup is blocked on a stale Cloudways key — "
                 "shown honestly as not-automated, never faked."),
    }


def _compute_standards(resp) -> dict:
    """WP-20.6 standards-floor reader — the sub-dimensions computable from a single
    homepage fetch: security posture + content hygiene. WCAG 2.2 AA and Core Web
    Vitals are NOT computed here (they need Lighthouse/axe) and are reported as
    not_automated, never faked. `resp` is an httpx.Response after redirects.
    """
    try:
        low = (resp.text or "").lower()
        headers = {k.lower(): v for k, v in resp.headers.items()}
        checks = {
            "https": str(resp.url).lower().startswith("https"),
            "hsts": "strict-transport-security" in headers,
            "x_content_type_options": "x-content-type-options" in headers,
            "no_lorem": "lorem ipsum" not in low,
            "has_title": "<title" in low and "<title></title>" not in low.replace(" ", ""),
            "og_tags": ("og:title" in low or "og:image" in low),
            "meta_description": 'name="description"' in low,
        }
        hard = ["https", "no_lorem", "has_title"]          # a launchable floor
        soft = ["hsts", "x_content_type_options", "og_tags", "meta_description"]
        issues = [k for k in hard if not checks[k]]
        advisory = [k for k in soft if not checks[k]]
        return {
            "checked": True,
            "checks": checks,
            "issues": issues,
            "advisory": advisory,
            "computed_status": "issues" if issues else ("advisory" if advisory else "pass"),
            "not_automated": ["wcag_2.2_aa", "core_web_vitals"],
        }
    except Exception as e:
        return {"checked": False, "computed_status": "error", "detail": type(e).__name__}


def _standards_row(ds: dict) -> dict:
    """Build the MAINTAIN standards_floor cell from persisted scan state (honest)."""
    st = (ds or {}).get("standards")
    if not st or not st.get("checked"):
        return {"status": "no_check",
                "detail": "run Scan to compute security + content; WCAG 2.2 AA / Core Web Vitals stay manual (need Lighthouse/axe)"}
    if st.get("issues"):
        tail = "hard-floor issues: " + ", ".join(st["issues"])
    elif st.get("advisory"):
        tail = "advisory gaps: " + ", ".join(st["advisory"])
    else:
        tail = "all computed checks pass"
    return {
        "status": st.get("computed_status", "no_check"),
        "checks": st.get("checks", {}),
        "issues": st.get("issues", []),
        "advisory": st.get("advisory", []),
        "not_automated": st.get("not_automated", []),
        "detail": "security+content computed from live homepage; WCAG 2.2 AA + Core Web Vitals still manual. " + tail,
    }


@router.post("/wordpress/drift/scan")
def wordpress_drift_scan():
    """WP-20.2b — run the brand-drift detector against each property's LIVE homepage
    and persist per-property results to wp_drift_state.json.

    Reads the public homepage (HTTP GET) and the internal brand profile only, then
    writes an INTERNAL vault state file. Performs NO WordPress mutation — the
    WP-20.3/.4 anti-bypass chokepoint is untouched. Properties with no BUILD_PROFILE
    are recorded as not-checkable (honest) without a network fetch. Per-property
    failures are captured, never fatal to the whole scan.
    """
    import datetime
    import brand_drift  # importable in this container: /shared on PYTHONPATH
    sites = _load_sites()
    now = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    state = {}
    for slug, v in sites.items():
        entry = {"checked_at": now}
        if not _read_build_profile(slug).get("present"):
            entry.update({"checked": False, "drift": False, "status": "no_profile",
                          "summary": "no BUILD_PROFILE — not checkable"})
            state[slug] = entry
            continue
        url = v.get("url", "")
        try:
            r = httpx.get(url, timeout=10, follow_redirects=True)
            r.raise_for_status()
            result = brand_drift.detect(slug, r.text)
            findings = result.get("findings", []) or []
            entry.update({
                "checked": bool(result.get("checked")),
                "drift": bool(result.get("drift")),
                "status": "drift" if result.get("drift") else "clean",
                "highs": sum(1 for f in findings if f.get("severity") == "high"),
                "warns": sum(1 for f in findings if f.get("severity") == "warn"),
                "summary": result.get("summary", ""),
            })
            entry["standards"] = _compute_standards(r)
        except Exception as e:
            entry.update({"checked": False, "drift": False, "status": "fetch_failed",
                          "summary": f"could not read {url}: {type(e).__name__}"})
        state[slug] = entry
    _atomic_write_json(WP_DRIFT_STATE_FILE, state)
    return {"ok": True, "scanned": len(state), "checked_at": now, "state": state}


# ── WP-20.6b/c — dashboard BUILD launcher over the governed drafts-only workflow ──
# The dashboard NEVER writes to WordPress directly. A BUILD action starts the
# orchestrator's wordpress.build_page_draft workflow (dev gate + creative gate +
# WP write chokepoint + brand-drift check, status=draft, no publish, no homepage
# overwrite). This route is a launcher over that chokepoint — the WP-20.6 §2
# "invents no new write path" invariant, restated for the dashboard.

_ORCH_URL = os.environ.get("ORCHESTRATOR_URL", "http://kai-orchestrator:8003")


class BuildDraftRequest(BaseModel):
    page_title: str
    page_content: Optional[str] = None
    brief_path: Optional[str] = None


@router.post("/wordpress/{site_id}/build-draft")
def build_draft(site_id: str, req: BuildDraftRequest):
    """Launch the governed drafts-only page workflow for a property.

    Routes through wordpress.build_page_draft on the orchestrator — the same
    chokepoint as publish, minus the publish/homepage steps. Produces a DRAFT
    only; both human gates (dev + creative) must be approved before any write.
    Returns the job_id so the dashboard can poll gate/step status.
    """
    site = _get_site(site_id)  # validates the property exists + has creds; site_id is the slug
    inputs = {"site": site_id, "page_title": req.page_title}
    if req.page_content:
        inputs["page_content"] = req.page_content
    if req.brief_path:
        inputs["brief_path"] = req.brief_path
    payload = {"type": "wordpress.build_page_draft", "inputs": inputs}
    try:
        with httpx.Client(timeout=30) as client:
            r = client.post(f"{_ORCH_URL}/workflows/run", json=payload)
    except httpx.RequestError as e:
        logger.exception("orchestrator unreachable for build-draft")
        raise HTTPException(502, f"orchestrator unreachable: {e}")
    if r.status_code != 200:
        raise HTTPException(502, f"orchestrator returned {r.status_code}: {r.text[:200]}")
    body = _safe_json(r)
    if isinstance(body, dict) and body.get("_error"):
        raise HTTPException(502, "orchestrator returned non-JSON response")
    if isinstance(body, dict) and body.get("error"):
        raise HTTPException(400, body["error"])
    return {"job_id": body.get("job_id"), "status": body.get("status"),
            "site": site_id, "url": site.get("url")}


@router.get("/wordpress/build-draft/{job_id}")
def build_draft_status(job_id: str):
    """Poll a build-draft job for the dashboard.

    Returns step/gate status ONLY — never the raw orchestrator step results,
    which carry WP creds in cleartext (see the redaction bug filed under
    WP-20.6d). Keeping this surface free of secrets is deliberate.
    """
    try:
        with httpx.Client(timeout=15) as client:
            r = client.get(f"{_ORCH_URL}/jobs/{job_id}")
    except httpx.RequestError as e:
        raise HTTPException(502, f"orchestrator unreachable: {e}")
    if r.status_code != 200:
        raise HTTPException(502, f"orchestrator returned {r.status_code}")
    data = _safe_json(r)
    if isinstance(data, dict) and data.get("_error"):
        raise HTTPException(502, "orchestrator returned non-JSON response")
    job = data.get("job", {}) or {}
    steps = [
        {"name": s.get("name"), "status": s.get("status"),
         "capability": s.get("capability") or "approval_gate"}
        for s in (data.get("steps", []) or [])
    ]
    awaiting = next((s["name"] for s in steps
                     if s["status"] in ("awaiting_gate", "pending_leo")), None)
    wrote = any(s["name"] == "create_page_draft" and s["status"] == "succeeded"
                for s in steps)
    return {"job_id": job_id, "status": job.get("status"),
            "awaiting_gate": awaiting, "draft_written": wrote, "steps": steps}


# ── WP-20.6c — dashboard EDIT launcher over the governed drafts-only edit workflow ──
# Same chokepoint as BUILD, but targets an existing DRAFT page via
# wordpress.edit_page_draft (which refuses to touch a published/live page).
class EditDraftRequest(BaseModel):
    page_id: int
    page_content: str
    page_title: Optional[str] = None
    brief_path: Optional[str] = None


@router.post("/wordpress/{site_id}/edit-draft")
def edit_draft(site_id: str, req: EditDraftRequest):
    """Launch the governed drafts-only EDIT workflow for an existing draft page.

    Routes through wordpress.edit_page_draft on the orchestrator (dev + creative
    gates, brand-drift, drafts-only guard — refuses non-draft pages). Returns the
    job_id so the dashboard can poll gate/step status via /wordpress/build-draft/{job_id}.
    """
    site = _get_site(site_id)  # validates the property exists + has creds; site_id is the slug
    inputs = {"site": site_id, "page_id": req.page_id, "page_content": req.page_content}
    if req.page_title:
        inputs["page_title"] = req.page_title
    if req.brief_path:
        inputs["brief_path"] = req.brief_path
    payload = {"type": "wordpress.edit_page_draft", "inputs": inputs}
    try:
        with httpx.Client(timeout=30) as client:
            r = client.post(f"{_ORCH_URL}/workflows/run", json=payload)
    except httpx.RequestError as e:
        logger.exception("orchestrator unreachable for edit-draft")
        raise HTTPException(502, f"orchestrator unreachable: {e}")
    if r.status_code != 200:
        raise HTTPException(502, f"orchestrator returned {r.status_code}: {r.text[:200]}")
    body = _safe_json(r)
    if isinstance(body, dict) and body.get("_error"):
        raise HTTPException(502, "orchestrator returned non-JSON response")
    if isinstance(body, dict) and body.get("error"):
        raise HTTPException(400, body["error"])
    return {"job_id": body.get("job_id"), "status": body.get("status"),
            "site": site_id, "page_id": req.page_id, "url": site.get("url")}
