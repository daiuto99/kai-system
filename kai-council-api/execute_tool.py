import json
import logging
import os
from datetime import datetime as _dt2, date as _d2, timedelta as _td2
from pathlib import Path
import httpx
from council_config import WORKER_URL, VAULT_PATH, ADVISOR_AVATARS, _slack_token
from knowledge_layer import _write_session_summary, _write_decision, _log_mission_deliverable

logger = logging.getLogger(__name__)

# n8n
N8N_REGISTRY_FILE = VAULT_PATH / "00_System" / "n8n_workflows.json"
SPECIALISTS_FILE = VAULT_PATH / "00_System" / "specialists.json"
# Plane PM
PLANE_API_TOKEN = open("/run/secrets/plane_api_token").read().strip().split("\n")[0]
PLANE_BASE_URL = "http://172.18.0.1:8090/api/v1"
PLANE_WORKSPACE = "sonicink"



def _load_n8n_registry() -> dict:
    if N8N_REGISTRY_FILE.exists():
        try:
            return json.loads(N8N_REGISTRY_FILE.read_text())
        except Exception as e:
            logger.exception("load_n8n_registry: %s", e)
    return {}


def _save_n8n_registry(registry: dict):
    N8N_REGISTRY_FILE.write_text(json.dumps(registry, indent=2))


def _trigger_n8n(workflow: str, payload: dict) -> dict:
    registry = _load_n8n_registry()
    entry = registry.get(workflow)
    if not entry:
        return {"error": f"Workflow '{workflow}' not registered. Use list_n8n_workflows or register_n8n_workflow."}
    webhook_url = entry["webhook_url"] if isinstance(entry, dict) else entry
    with httpx.Client(timeout=30) as client:
        r = client.post(webhook_url, json=payload)
        if r.status_code == 200:
            try:
                return {"ok": True, "workflow": workflow, "result": r.json()}
            except Exception:
                return {"ok": True, "workflow": workflow, "result": r.text[:2000]}
        return {"error": f"n8n returned {r.status_code}", "body": r.text[:500]}


def _list_n8n_workflows() -> dict:
    registry = _load_n8n_registry()
    workflows = []
    for name, entry in registry.items():
        if isinstance(entry, dict):
            workflows.append({"name": name, "description": entry.get("description", ""), "url": entry.get("webhook_url", "")})
        else:
            workflows.append({"name": name, "description": "", "url": entry})
    return {"workflows": workflows, "count": len(workflows)}


def _register_n8n_workflow(name: str, webhook_url: str, description: str) -> dict:
    registry = _load_n8n_registry()
    registry[name] = {"webhook_url": webhook_url, "description": description}
    _save_n8n_registry(registry)
    return {"ok": True, "name": name, "registered": True}


def _list_specialists() -> dict:
    if not SPECIALISTS_FILE.exists():
        return {"specialists": []}
    specialists = json.loads(SPECIALISTS_FILE.read_text())
    return {"specialists": [{"id": s["id"], "name": s["name"], "domain": s["domain"]} for s in specialists]}


def _consult_specialist(specialist_id: str, question: str, context: str) -> dict:
    from providers import get_anthropic_client
    from council_config import _track_usage
    if not SPECIALISTS_FILE.exists():
        return {"error": "Specialists registry not found"}

    specialists = json.loads(SPECIALISTS_FILE.read_text())
    spec = next((s for s in specialists if s["id"] == specialist_id), None)
    if not spec:
        available = [s["id"] for s in specialists]
        return {"error": f"Specialist '{specialist_id}' not found. Available: {available}"}

    spec_file = VAULT_PATH / spec["file"]
    if not spec_file.exists():
        return {"error": f"Persona file not found: {spec['file']}"}

    persona = spec_file.read_text(encoding="utf-8")
    bp = VAULT_PATH / "00_System" / "business_profile.md"
    system = ""
    if bp.exists():
        system = (
            "<background_context>\n"
            + bp.read_text(encoding="utf-8")
            + "\n</background_context>\n\n"
        )
    system += persona

    user_msg = question
    if context:
        user_msg = f"Context: {context}\n\nQuestion: {question}"

    try:
        client = get_anthropic_client()
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1500,
            system=system,
            messages=[{"role": "user", "content": user_msg}]
        )
        reply = response.content[0].text
        _track_usage("specialist", response.usage.input_tokens, response.usage.output_tokens)
        return {
            "specialist": spec["name"],
            "domain": spec["domain"],
            "response": reply,
        }
    except Exception as e:
        logger.exception("consult_specialist: %s", e)
        return {"error": str(e)}


# ── Category handlers ─────────────────────────────────────────────────────────

def _h_workflows(client, tool_name, ti, advisor):
    if tool_name == "save_workflow":
        return client.post(f"{WORKER_URL}/workflows", json=ti).json()
    if tool_name == "list_workflows":
        return client.get(f"{WORKER_URL}/workflows").json()
    if tool_name == "delete_workflow":
        return client.delete(f"{WORKER_URL}/workflows/{ti.get('id', '')}").json()


def _h_tasks(client, tool_name, ti, advisor):
    if tool_name == "list_tasks":
        return client.get(f"{WORKER_URL}/tasks").json()
    if tool_name == "complete_task":
        return client.post(f"{WORKER_URL}/tasks/{ti['task_id']}/complete").json()
    if tool_name == "create_task":
        return client.post(f"{WORKER_URL}/tasks", json=ti).json()


def _h_projects(client, tool_name, ti, advisor):
    if tool_name == "create_project":
        return client.post(f"{WORKER_URL}/projects", json=ti).json()
    if tool_name == "update_project":
        pid = ti.pop("id")
        return client.patch(f"{WORKER_URL}/projects/{pid}", json=ti).json()
    if tool_name == "list_projects":
        return client.get(f"{WORKER_URL}/projects").json()
    if tool_name == "setup_project":
        r = client.post(f"{WORKER_URL}/projects/setup", json=ti, timeout=30)
        return r.json() if r.status_code == 200 else {"error": f"Worker {r.status_code}: {r.text[:200]}"}


def _h_vault(client, tool_name, ti, advisor):
    if tool_name == "write_to_vault":
        r = client.post(f"{WORKER_URL}/vault/write",
            params={"path": ti["path"], "content": ti["content"]})
        result = r.json()
        _log_mission_deliverable(ti["path"], ti.get("description", ""))
        return result
    if tool_name == "read_vault":
        return client.get(f"{WORKER_URL}/vault/read", params={"path": ti["path"]}).json()


def _h_slack(client, tool_name, ti, advisor):
    if tool_name == "send_slack_message":
        token = _slack_token()
        if not token:
            return {"error": "Slack token not configured"}
        adv = ti.get("advisor", "kai")
        channel = ti.get("channel", "kai")
        if not channel.startswith("#"):
            channel = f"#{channel}"
        payload = {
            "channel": channel,
            "text": ti["message"],
            "username": adv.upper() if adv == "kai" else adv.capitalize(),
            "icon_url": ADVISOR_AVATARS.get(adv, ADVISOR_AVATARS["kai"]),
        }
        r = client.post("https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {token}"},
            json=payload)
        data = r.json()
        if not data.get("ok"):
            return {"error": data.get("error", "slack error"), "detail": data}
        return {"ok": True, "channel": channel}
    if tool_name == "create_slack_channel":
        r = client.post(f"{WORKER_URL}/slack/channels", json=ti, timeout=15)
        return r.json() if r.status_code == 200 else {"error": f"Worker {r.status_code}: {r.text[:200]}"}
    if tool_name == "invite_to_slack_channel":
        channel = ti.get("channel", "")
        emails = list(ti.get("emails", []))
        for cname in ti.get("contact_names", []):
            cr = client.get(f"{WORKER_URL}/contacts/lookup", params={"q": cname}, timeout=5)
            if cr.status_code == 200 and cr.json().get("found"):
                email = cr.json()["contact"].get("email")
                if email:
                    emails.append(email)
        t2r = client.post(
            f"{WORKER_URL}/t2/queue",
            json={
                "action": f"Invite {', '.join(emails or ti.get('contact_names', []))} to #{channel}",
                "detail": f"Emails: {emails}",
                "advisor": "kai",
                "slack_channel": "kai",
            },
            timeout=5,
        )
        return {"queued": True, "emails": emails, "t2": t2r.json() if t2r.status_code == 200 else {}}


def _h_mission(client, tool_name, ti, advisor):
    if tool_name == "start_mission":
        mission = {
            "name": ti["name"],
            "scope": ti["scope"],
            "notes": ti.get("notes", ""),
            "granted": _dt2.utcnow().isoformat(),
            "status": "in_progress",
            "deliverables": [],
        }
        mission_file = VAULT_PATH / "00_System" / "active_mission.json"
        mission_file.write_text(json.dumps(mission, indent=2))
        return {"ok": True, "mission": ti["name"]}
    if tool_name == "complete_mission":
        mission_file = VAULT_PATH / "00_System" / "active_mission.json"
        if mission_file.exists():
            mission = json.loads(mission_file.read_text())
            mission["status"] = "review_ready"
            mission["completed"] = _dt2.utcnow().isoformat()
            mission["built"] = ti.get("built", [])
            mission["decisions"] = ti.get("decisions", [])
            mission_file.write_text(json.dumps(mission, indent=2))
        return {"ok": True, "status": "review_ready"}
    if tool_name == "log_action":
        changelog = VAULT_PATH / "00_System" / "team_changelog.md"
        if not changelog.exists():
            changelog.write_text("# KAI Team Changelog\n\n")
        entry = f"- {_d2.today().isoformat()} | KAI | {ti['action']} | Tier {ti['tier']} | {ti['approved_by']}\n"
        with open(changelog, "a") as f:
            f.write(entry)
        return {"ok": True}


def _h_calendar(client, tool_name, ti, advisor):
    if tool_name == "get_calendar":
        days = ti.get("days", 7)
        gcal_events = []
        try:
            r = client.post("http://kai-n8n:5678/webhook/kai-calendar-events",
                           json={"days": days}, timeout=15)
            if r.status_code == 200:
                for ev in r.json():
                    start = ev.get("start", {})
                    start_str = start.get("dateTime", start.get("date", "")) if isinstance(start, dict) else str(start)
                    end = ev.get("end", {})
                    end_str = end.get("dateTime", end.get("date", "")) if isinstance(end, dict) else str(end)
                    _day = ""
                    try:
                        from zoneinfo import ZoneInfo as _ZI2
                        _dt_parsed = _dt2.fromisoformat(start_str[:10])
                        _day = _dt_parsed.strftime("%A")
                    except Exception:
                        pass
                    gcal_events.append({
                        "start": start_str[:16],
                        "end": end_str[:16],
                        "summary": ev.get("summary", ""),
                        "source": "Google",
                        "day_name": _day,
                    })
        except Exception:
            pass
        ics_events = []
        try:
            r2 = client.get(f"{WORKER_URL}/calendar/ics", params={"days": days}, timeout=15)
            if r2.status_code == 200:
                for ev in r2.json().get("events", []):
                    summary = ev.get("title", ev.get("summary", "")).strip()
                    if summary:
                        _iday = ""
                        try:
                            _idt_parsed = _dt2.fromisoformat(str(ev.get("start", ""))[:10])
                            _iday = _idt_parsed.strftime("%A")
                        except Exception:
                            pass
                        ics_events.append({
                            "start": str(ev.get("start", ""))[:16],
                            "end": str(ev.get("end", ""))[:16],
                            "summary": summary,
                            "calendar": ev.get("calendar", "ICS"),
                            "source": "ICS",
                            "day_name": _iday,
                        })
        except Exception:
            pass
        all_events = sorted(gcal_events + ics_events, key=lambda e: str(e.get("start", "")))
        return {"events": all_events}
    if tool_name == "create_event":
        return client.post(f"{WORKER_URL}/calendar/events", json=ti).json()
    if tool_name == "get_o365_calendar":
        days = ti.get("days", 7)
        r = client.get(f"{WORKER_URL}/calendar/ics", params={"days": days}, timeout=15)
        return r.json() if r.status_code == 200 else {"error": r.text}


def _h_knowledge(client, tool_name, ti, advisor):
    if tool_name == "save_session":
        ch = ti.get("channel", "kai")
        return _write_session_summary(
            channel=ch,
            title=ti["title"],
            topics=ti.get("topics", []),
            decisions=ti.get("decisions", []),
            actions=ti.get("actions", []),
            context_note=ti.get("context", ""),
        )
    if tool_name == "log_decision":
        ch = ti.get("channel", "kai")
        return _write_decision(
            channel=ch,
            decision=ti["decision"],
            context=ti["context"],
            outcome=ti.get("outcome", ""),
        )


def _h_n8n(client, tool_name, ti, advisor):
    if tool_name == "trigger_n8n_workflow":
        return _trigger_n8n(ti["workflow"], ti.get("payload", {}))
    if tool_name == "list_n8n_workflows":
        return _list_n8n_workflows()
    if tool_name == "register_n8n_workflow":
        return _register_n8n_workflow(
            ti["name"], ti["webhook_url"], ti.get("description", "")
        )


def _h_specialists(client, tool_name, ti, advisor):
    if tool_name == "list_specialists":
        return _list_specialists()
    if tool_name == "consult_specialist":
        return _consult_specialist(
            ti["specialist"], ti["question"], ti.get("context", "")
        )


def _h_email(client, tool_name, ti, advisor):
    if tool_name == "read_email":
        return _trigger_n8n("gmail-read", {
            "max_results": ti.get("max_results", 10),
            "query": ti.get("query", "")
        })
    if tool_name == "draft_email":
        return _trigger_n8n("gmail-draft", {
            "to": ti["to"],
            "subject": ti["subject"],
            "body": ti["body"]
        })


def _h_contacts(client, tool_name, ti, advisor):
    if tool_name == "lookup_contact":
        r = client.get(f"{WORKER_URL}/contacts/lookup", params={"q": ti.get("query", "")}, timeout=5)
        return r.json() if r.status_code == 200 else {"error": r.text}
    if tool_name == "add_contact":
        r = client.post(f"{WORKER_URL}/contacts", json=ti, timeout=5)
        return r.json() if r.status_code == 200 else {"error": r.text}
    if tool_name == "list_templates":
        r = client.get(f"{WORKER_URL}/templates", timeout=5)
        return r.json() if r.status_code == 200 else {"error": r.text}
    if tool_name == "lookup_google_contact":
        query = ti.get("query", "")
        n8n_url = "https://n8n.sonicink.space/webhook/kai-contacts-lookup"
        try:
            logger.warning("[contacts] querying n8n for: %s", query)
            resp = httpx.post(n8n_url, json={"query": query}, timeout=15)
            logger.warning("[contacts] status=%s body=%s", resp.status_code, resp.text[:200])
            data = resp.json()
            if isinstance(data, list) and data:
                data = data[0]
            return data
        except Exception as e:
            logger.exception("[contacts] exception: %s", e)
            return {"error": f"Google Contacts lookup failed: {e}"}


def _h_oura(client, tool_name, ti, advisor):
    data_type = ti.get("data_type", "all")
    days = min(ti.get("days", 1), 7)
    oura_token_path = Path("/run/secrets/oura_token")
    oura_token = oura_token_path.read_text().strip() if oura_token_path.exists() else os.environ.get("OURA_TOKEN", "")
    if not oura_token:
        return {"error": "Oura token not configured."}
    from datetime import date, timedelta
    end_date = date.today().isoformat()
    start_date = (date.today() - timedelta(days=days - 1)).isoformat()
    headers = {"Authorization": f"Bearer {oura_token}"}
    base = "https://api.ouraring.com/v2/usercollection"
    result = {}
    try:
        if data_type in ("readiness", "all"):
            r = httpx.get(f"{base}/daily_readiness", params={"start_date": start_date, "end_date": end_date}, headers=headers, timeout=10)
            result["readiness"] = r.json().get("data", [])
        if data_type in ("sleep", "all"):
            r = httpx.get(f"{base}/daily_sleep", params={"start_date": start_date, "end_date": end_date}, headers=headers, timeout=10)
            result["sleep"] = r.json().get("data", [])
        if data_type in ("activity", "all"):
            r = httpx.get(f"{base}/daily_activity", params={"start_date": start_date, "end_date": end_date}, headers=headers, timeout=10)
            result["activity"] = r.json().get("data", [])
        return result
    except Exception as e:
        logger.exception("get_oura_data: %s", e)
        return {"error": f"Oura API error: {e}"}


def _h_wordpress(client, tool_name, ti, advisor):
    import base64 as _b64

    def _wp_creds(site_key):
        wp_sites = json.loads((VAULT_PATH / "00_System" / "wordpress_sites.json").read_text())
        site = wp_sites["sites"].get(site_key)
        if not site:
            available = list(wp_sites["sites"].keys())
            raise ValueError(f"Unknown site: {site_key}. Available: {available}")
        if not site.get("app_password"):
            raise ValueError(f"No app password configured for {site_key}.")
        creds = _b64.b64encode(f"{site['username']}:{site['app_password']}".encode()).decode()
        return site, creds

    if tool_name == "wordpress_get_posts":
        site_key = ti.get("site", "leodaiuto")
        count = min(ti.get("count", 5), 20)
        status = ti.get("status", "any")
        try:
            site, creds = _wp_creds(site_key)
            r = httpx.get(
                f"{site['url']}/wp-json/wp/v2/posts",
                params={"per_page": count, "status": status, "_fields": "id,title,status,date,link,excerpt"},
                headers={"Authorization": f"Basic {creds}"},
                timeout=15
            )
            posts = r.json()
            return {"site": site_key, "url": site["url"], "posts": [
                {"id": p.get("id"), "title": p.get("title", {}).get("rendered", ""),
                 "status": p.get("status"), "date": p.get("date","")[:10],
                 "link": p.get("link"), "excerpt": p.get("excerpt", {}).get("rendered", "")[:200]}
                for p in posts
            ]}
        except Exception as e:
            logger.exception("wordpress_get_posts: %s", e)
            return {"error": f"WordPress get posts failed: {e}"}

    if tool_name == "wordpress_create_post":
        site_key = ti.get("site", "leodaiuto")
        title = ti.get("title", "")
        content_body = ti.get("content", "")
        status = ti.get("status", "draft")
        tags = ti.get("tags", [])
        excerpt = ti.get("excerpt", "")
        try:
            site, creds = _wp_creds(site_key)
            headers = {"Authorization": f"Basic {creds}", "Content-Type": "application/json"}
            tag_ids = []
            for tag_name in tags:
                tr = httpx.get(f"{site['url']}/wp-json/wp/v2/tags", params={"search": tag_name}, headers=headers, timeout=10)
                existing = tr.json()
                if existing:
                    tag_ids.append(existing[0]["id"])
                else:
                    cr = httpx.post(f"{site['url']}/wp-json/wp/v2/tags", json={"name": tag_name}, headers=headers, timeout=10)
                    tag_ids.append(cr.json().get("id"))
            payload = {"title": title, "content": content_body, "status": status, "excerpt": excerpt}
            if tag_ids:
                payload["tags"] = tag_ids
            r = httpx.post(f"{site['url']}/wp-json/wp/v2/posts", json=payload, headers=headers, timeout=20)
            post = r.json()
            return {
                "created": True, "id": post.get("id"), "status": post.get("status"),
                "link": post.get("link"), "title": title, "site": site_key,
                "message": f"Post {'published' if status == 'publish' else 'saved as draft'} on {site['url']}"
            }
        except Exception as e:
            logger.exception("wordpress_create_post: %s", e)
            return {"error": f"WordPress create post failed: {e}"}


def _h_parking_lot(client, tool_name, ti, advisor):
    capture_content = ti.get("content", "")
    source = ti.get("source", "kai-chat")
    try:
        client.post(
            f"{WORKER_URL}/parking-lot/quick",
            json={"text": capture_content, "source": source},
            timeout=10
        )
        return {"saved": True, "message": "Added to your parking lot."}
    except Exception as e:
        logger.exception("add_to_parking_lot: %s", e)
        return {"error": f"Parking lot save failed: {e}"}


def _h_t2(client, tool_name, ti, advisor):
    action = ti.get("action", "")
    detail = ti.get("detail", "")
    slack_channel = ti.get("slack_channel", "kai")
    try:
        resp = client.post(
            f"{WORKER_URL}/t2/queue",
            json={"action": action, "detail": detail, "advisor": advisor, "slack_channel": slack_channel},
            timeout=10
        )
        result = resp.json()
        return {"queued": True, "id": result.get("id"), "message": f"T2 approval requested in Slack. Action ID: {result.get('id')}. React to approve or reject."}
    except Exception as e:
        logger.exception("request_t2_approval: %s", e)
        return {"error": f"T2 queue failed: {e}"}


def _h_web_search(client, tool_name, ti, advisor):
    tavily_key_path = Path("/run/secrets/tavily_api_key")
    tavily_key = tavily_key_path.read_text().strip() if tavily_key_path.exists() else os.environ.get("TAVILY_API_KEY", "")
    if not tavily_key:
        return {"error": "Tavily API key not configured."}
    query = ti.get("query", "")
    max_results = min(ti.get("max_results", 5), 10)
    try:
        resp = httpx.post(
            "https://api.tavily.com/search",
            json={"api_key": tavily_key, "query": query, "max_results": max_results, "search_depth": "basic"},
            timeout=15
        )
        data = resp.json()
        results = data.get("results", [])
        answer = data.get("answer", "")
        return {"query": query, "answer": answer, "results": [{"title": r.get("title",""), "url": r.get("url",""), "content": r.get("content","")[:500]} for r in results]}
    except Exception as e:
        logger.exception("web_search: %s", e)
        return {"error": f"Tavily search failed: {e}"}


def _h_plane(client, tool_name, ti, advisor):
    headers_base = {"X-API-Key": PLANE_API_TOKEN}
    if tool_name == "get_plane_issues":
        project_id = ti.get("project_id", "")
        issue_id = ti.get("issue_id", "")
        state = ti.get("state", "")
        try:
            if issue_id and project_id:
                r = httpx.get(f"{PLANE_BASE_URL}/workspaces/{PLANE_WORKSPACE}/projects/{project_id}/issues/{issue_id}/", headers=headers_base, timeout=10)
                return r.json()
            elif project_id:
                params = {}
                if state:
                    params["state"] = state
                r = httpx.get(f"{PLANE_BASE_URL}/workspaces/{PLANE_WORKSPACE}/projects/{project_id}/issues/", headers=headers_base, params=params, timeout=10)
                data = r.json()
                issues = data.get("results", data) if isinstance(data, dict) else data
                return {"issues": [{"id": i.get("id"), "name": i.get("name"), "state": i.get("state_detail", {}).get("name"), "description": i.get("description_stripped", "")} for i in issues]}
            else:
                r = httpx.get(f"{PLANE_BASE_URL}/workspaces/{PLANE_WORKSPACE}/projects/", headers=headers_base, timeout=10)
                data = r.json()
                projects = data.get("results", data) if isinstance(data, dict) else data
                return {"projects": [{"id": p.get("id"), "name": p.get("name"), "identifier": p.get("identifier")} for p in projects]}
        except Exception as e:
            return {"error": f"Plane get failed: {e}"}
    if tool_name == "update_plane_issue":
        project_id = ti.get("project_id", "")
        issue_id = ti.get("issue_id", "")
        updates = {k: v for k, v in ti.items() if k not in ("project_id", "issue_id")}
        headers = {**headers_base, "Content-Type": "application/json"}
        try:
            r = httpx.patch(f"{PLANE_BASE_URL}/workspaces/{PLANE_WORKSPACE}/projects/{project_id}/issues/{issue_id}/", headers=headers, json=updates, timeout=10)
            return {"updated": True, "issue_id": issue_id, "response": r.json()}
        except Exception as e:
            return {"error": f"Plane update failed: {e}"}
    if tool_name == "create_plane_issue":
        project_id = ti.get("project_id", "")
        payload = {k: v for k, v in ti.items() if k != "project_id"}
        headers = {**headers_base, "Content-Type": "application/json"}
        try:
            r = httpx.post(f"{PLANE_BASE_URL}/workspaces/{PLANE_WORKSPACE}/projects/{project_id}/issues/", headers=headers, json=payload, timeout=10)
            result = r.json()
            return {"created": True, "issue_id": result.get("id"), "name": result.get("name")}
        except Exception as e:
            return {"error": f"Plane create failed: {e}"}


# ── Dispatch registry ─────────────────────────────────────────────────────────

TOOL_REGISTRY = {
    # Workflows
    "save_workflow": _h_workflows,
    "list_workflows": _h_workflows,
    "delete_workflow": _h_workflows,
    # Tasks
    "list_tasks": _h_tasks,
    "complete_task": _h_tasks,
    "create_task": _h_tasks,
    # Projects
    "create_project": _h_projects,
    "update_project": _h_projects,
    "list_projects": _h_projects,
    "setup_project": _h_projects,
    # Vault
    "write_to_vault": _h_vault,
    "read_vault": _h_vault,
    # Slack
    "send_slack_message": _h_slack,
    "create_slack_channel": _h_slack,
    "invite_to_slack_channel": _h_slack,
    # Mission / governance
    "start_mission": _h_mission,
    "complete_mission": _h_mission,
    "log_action": _h_mission,
    # Calendar
    "get_calendar": _h_calendar,
    "create_event": _h_calendar,
    "get_o365_calendar": _h_calendar,
    # Knowledge
    "save_session": _h_knowledge,
    "log_decision": _h_knowledge,
    # n8n
    "trigger_n8n_workflow": _h_n8n,
    "list_n8n_workflows": _h_n8n,
    "register_n8n_workflow": _h_n8n,
    # Specialists
    "list_specialists": _h_specialists,
    "consult_specialist": _h_specialists,
    # Email
    "read_email": _h_email,
    "draft_email": _h_email,
    # Contacts
    "lookup_contact": _h_contacts,
    "add_contact": _h_contacts,
    "list_templates": _h_contacts,
    "lookup_google_contact": _h_contacts,
    # Oura
    "get_oura_data": _h_oura,
    # WordPress
    "wordpress_get_posts": _h_wordpress,
    "wordpress_create_post": _h_wordpress,
    # Parking lot
    "add_to_parking_lot": _h_parking_lot,
    # T2
    "request_t2_approval": _h_t2,
    # Web search
    "web_search": _h_web_search,
    # Plane
    "get_plane_issues": _h_plane,
    "update_plane_issue": _h_plane,
    "create_plane_issue": _h_plane,
}


def execute_tool(tool_name: str, tool_input: dict, advisor: str = "kai") -> dict:
    handler = TOOL_REGISTRY.get(tool_name)
    if not handler:
        return {"error": f"Unknown tool: {tool_name}"}
    try:
        with httpx.Client(timeout=15) as client:
            return handler(client, tool_name, tool_input, advisor)
    except Exception as e:
        logger.exception("execute_tool %s: %s", tool_name, e)
        return {"error": str(e)}
