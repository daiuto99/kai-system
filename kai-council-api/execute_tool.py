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


def execute_tool(tool_name: str, tool_input: dict, advisor: str = "kai") -> dict:
    try:
        with httpx.Client(timeout=15) as client:
            # Workflows
            if tool_name == "save_workflow":
                r = client.post(f"{WORKER_URL}/workflows", json=tool_input)
                return r.json()
            elif tool_name == "list_workflows":
                r = client.get(f"{WORKER_URL}/workflows")
                return r.json()
            elif tool_name == "delete_workflow":
                r = client.delete(f"{WORKER_URL}/workflows/{tool_input.get('id','')}")
                return r.json()

            # Tasks
            elif tool_name == "list_tasks":
                r = client.get(f"{WORKER_URL}/tasks")
                return r.json()
            elif tool_name == "complete_task":
                task_id = tool_input["task_id"]
                r = client.post(f"{WORKER_URL}/tasks/{task_id}/complete")
                return r.json()
            elif tool_name == "create_task":
                r = client.post(f"{WORKER_URL}/tasks", json=tool_input)
                return r.json()

            # Projects
            elif tool_name == "create_project":
                r = client.post(f"{WORKER_URL}/projects", json=tool_input)
                return r.json()
            elif tool_name == "update_project":
                pid = tool_input.pop("id")
                r = client.patch(f"{WORKER_URL}/projects/{pid}", json=tool_input)
                return r.json()
            elif tool_name == "list_projects":
                r = client.get(f"{WORKER_URL}/projects")
                return r.json()

            # Vault
            elif tool_name == "write_to_vault":
                r = client.post(f"{WORKER_URL}/vault/write",
                    params={"path": tool_input["path"], "content": tool_input["content"]})
                result = r.json()
                _log_mission_deliverable(tool_input["path"], tool_input.get("description", ""))
                return result
            elif tool_name == "read_vault":
                r = client.get(f"{WORKER_URL}/vault/read",
                    params={"path": tool_input["path"]})
                return r.json()

            # Slack
            elif tool_name == "send_slack_message":
                token = _slack_token()
                if not token:
                    return {"error": "Slack token not configured"}
                adv = tool_input.get("advisor", "kai")
                channel = tool_input.get("channel", "kai")
                if not channel.startswith("#"):
                    channel = f"#{channel}"
                payload = {
                    "channel": channel,
                    "text": tool_input["message"],
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

            # Mission state
            elif tool_name == "start_mission":
                mission = {
                    "name": tool_input["name"],
                    "scope": tool_input["scope"],
                    "notes": tool_input.get("notes", ""),
                    "granted": _dt2.utcnow().isoformat(),
                    "status": "in_progress",
                    "deliverables": [],
                }
                mission_file = VAULT_PATH / "00_System" / "active_mission.json"
                mission_file.write_text(json.dumps(mission, indent=2))
                return {"ok": True, "mission": tool_input["name"]}
            elif tool_name == "complete_mission":
                mission_file = VAULT_PATH / "00_System" / "active_mission.json"
                if mission_file.exists():
                    mission = json.loads(mission_file.read_text())
                    mission["status"] = "review_ready"
                    mission["completed"] = _dt2.utcnow().isoformat()
                    mission["built"] = tool_input.get("built", [])
                    mission["decisions"] = tool_input.get("decisions", [])
                    mission_file.write_text(json.dumps(mission, indent=2))
                return {"ok": True, "status": "review_ready"}

            # Governance log
            elif tool_name == "log_action":
                changelog = VAULT_PATH / "00_System" / "team_changelog.md"
                if not changelog.exists():
                    changelog.write_text("# KAI Team Changelog\n\n")
                entry = f"- {_d2.today().isoformat()} | KAI | {tool_input['action']} | Tier {tool_input['tier']} | {tool_input['approved_by']}\n"
                with open(changelog, "a") as f:
                    f.write(entry)
                return {"ok": True}

            # Calendar
            elif tool_name == "get_calendar":
                days = tool_input.get("days", 7)
                # Google Calendar (whitelisted calendars via n8n)
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
                # ICS feeds (Revolt + PSU O365)
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
            elif tool_name == "create_event":
                r = client.post(f"{WORKER_URL}/calendar/events", json=tool_input)
                return r.json()

            # Knowledge
            elif tool_name == "save_session":
                ch = tool_input.get("channel", "kai")
                return _write_session_summary(
                    channel=ch,
                    title=tool_input["title"],
                    topics=tool_input.get("topics", []),
                    decisions=tool_input.get("decisions", []),
                    actions=tool_input.get("actions", []),
                    context_note=tool_input.get("context", ""),
                )
            elif tool_name == "log_decision":
                ch = tool_input.get("channel", "kai")
                return _write_decision(
                    channel=ch,
                    decision=tool_input["decision"],
                    context=tool_input["context"],
                    outcome=tool_input.get("outcome", ""),
                )

            # n8n workflows
            elif tool_name == "trigger_n8n_workflow":
                return _trigger_n8n(tool_input["workflow"], tool_input.get("payload", {}))
            elif tool_name == "list_n8n_workflows":
                return _list_n8n_workflows()
            elif tool_name == "register_n8n_workflow":
                return _register_n8n_workflow(
                    tool_input["name"], tool_input["webhook_url"],
                    tool_input.get("description", "")
                )

            # Specialists
            elif tool_name == "list_specialists":
                return _list_specialists()
            elif tool_name == "consult_specialist":
                return _consult_specialist(
                    tool_input["specialist"],
                    tool_input["question"],
                    tool_input.get("context", "")
                )

            # Email (via n8n)
            elif tool_name == "read_email":
                return _trigger_n8n("gmail-read", {
                    "max_results": tool_input.get("max_results", 10),
                    "query": tool_input.get("query", "")
                })
            elif tool_name == "draft_email":
                return _trigger_n8n("gmail-draft", {
                    "to": tool_input["to"],
                    "subject": tool_input["subject"],
                    "body": tool_input["body"]
                })

            # Project setup (fixed WORKER_API -> WORKER_URL)
            elif tool_name == "setup_project":
                r = client.post(f"{WORKER_URL}/projects/setup", json=tool_input, timeout=30)
                return r.json() if r.status_code == 200 else {"error": f"Worker {r.status_code}: {r.text[:200]}"}

            # Slack channel management (fixed WORKER_API -> WORKER_URL)
            elif tool_name == "create_slack_channel":
                r = client.post(f"{WORKER_URL}/slack/channels", json=tool_input, timeout=15)
                return r.json() if r.status_code == 200 else {"error": f"Worker {r.status_code}: {r.text[:200]}"}

            elif tool_name == "invite_to_slack_channel":
                channel = tool_input.get("channel", "")
                emails = list(tool_input.get("emails", []))
                for cname in tool_input.get("contact_names", []):
                    cr = client.get(f"{WORKER_URL}/contacts/lookup", params={"q": cname}, timeout=5)
                    if cr.status_code == 200 and cr.json().get("found"):
                        email = cr.json()["contact"].get("email")
                        if email:
                            emails.append(email)
                # Queue as T2 action (fixed WORKER_API -> WORKER_URL)
                t2r = client.post(
                    f"{WORKER_URL}/t2/queue",
                    json={
                        "action": f"Invite {', '.join(emails or tool_input.get('contact_names', []))} to #{channel}",
                        "detail": f"Emails: {emails}",
                        "advisor": "kai",
                        "slack_channel": "kai",
                    },
                    timeout=5,
                )
                return {"queued": True, "emails": emails, "t2": t2r.json() if t2r.status_code == 200 else {}}

            # Contacts (fixed WORKER_API -> WORKER_URL)
            elif tool_name == "lookup_contact":
                r = client.get(f"{WORKER_URL}/contacts/lookup", params={"q": tool_input.get("query", "")}, timeout=5)
                return r.json() if r.status_code == 200 else {"error": r.text}
            elif tool_name == "add_contact":
                r = client.post(f"{WORKER_URL}/contacts", json=tool_input, timeout=5)
                return r.json() if r.status_code == 200 else {"error": r.text}
            elif tool_name == "list_templates":
                r = client.get(f"{WORKER_URL}/templates", timeout=5)
                return r.json() if r.status_code == 200 else {"error": r.text}

            # O365 calendar
            elif tool_name == "get_o365_calendar":
                days = tool_input.get("days", 7)
                r = client.get(f"{WORKER_URL}/calendar/ics", params={"days": days}, timeout=15)
                return r.json() if r.status_code == 200 else {"error": r.text}

            # Web search
            elif tool_name == "web_search":
                tavily_key_path = Path("/run/secrets/tavily_api_key")
                tavily_key = tavily_key_path.read_text().strip() if tavily_key_path.exists() else os.environ.get("TAVILY_API_KEY", "")
                if not tavily_key:
                    return {"error": "Tavily API key not configured."}
                query = tool_input.get("query", "")
                max_results = min(tool_input.get("max_results", 5), 10)
                try:
                    resp = httpx.post(
                        "https://api.tavily.com/search",
                        json={"api_key": tavily_key, "query": query, "max_results": max_results, "search_depth": "basic"},
                        timeout=15
                    )
                    data = resp.json()
                    results = data.get("results", [])
                    answer = data.get("answer", "")
                    out = {"query": query, "answer": answer, "results": [{"title": r.get("title",""), "url": r.get("url",""), "content": r.get("content","")[:500]} for r in results]}
                    return out
                except Exception as e:
                    logger.exception("web_search: %s", e)
                    return {"error": f"Tavily search failed: {e}"}

            # Google contacts
            elif tool_name == "lookup_google_contact":
                query = tool_input.get("query", "")
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

            # Oura
            elif tool_name == "get_oura_data":
                data_type = tool_input.get("data_type", "all")
                days = min(tool_input.get("days", 1), 7)
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

            # WordPress
            elif tool_name == "wordpress_get_posts":
                site_key = tool_input.get("site", "leodaiuto")
                count = min(tool_input.get("count", 5), 20)
                status = tool_input.get("status", "any")
                try:
                    wp_sites = json.loads((VAULT_PATH / "00_System" / "wordpress_sites.json").read_text())
                    site = wp_sites["sites"].get(site_key)
                    if not site:
                        return {"error": f"Unknown site: {site_key}. Available: {list(wp_sites['sites'].keys())}"}
                    if not site.get("app_password"):
                        return {"error": f"No app password configured for {site_key}."}
                    import base64 as _b64
                    creds = _b64.b64encode(f"{site['username']}:{site['app_password']}".encode()).decode()
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

            elif tool_name == "wordpress_create_post":
                site_key = tool_input.get("site", "leodaiuto")
                title = tool_input.get("title", "")
                content_body = tool_input.get("content", "")
                status = tool_input.get("status", "draft")
                tags = tool_input.get("tags", [])
                excerpt = tool_input.get("excerpt", "")
                try:
                    wp_sites = json.loads((VAULT_PATH / "00_System" / "wordpress_sites.json").read_text())
                    site = wp_sites["sites"].get(site_key)
                    if not site:
                        return {"error": f"Unknown site: {site_key}"}
                    if not site.get("app_password"):
                        return {"error": f"No app password for {site_key}."}
                    import base64 as _b64
                    creds = _b64.b64encode(f"{site['username']}:{site['app_password']}".encode()).decode()
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

            # Parking lot
            elif tool_name == "add_to_parking_lot":
                capture_content = tool_input.get("content", "")
                source = tool_input.get("source", "kai-chat")
                try:
                    resp = client.post(
                        f"{WORKER_URL}/parking-lot/quick",
                        json={"text": capture_content, "source": source},
                        timeout=10
                    )
                    return {"saved": True, "message": "Added to your parking lot."}
                except Exception as e:
                    logger.exception("add_to_parking_lot: %s", e)
                    return {"error": f"Parking lot save failed: {e}"}

            # T2 approval
            elif tool_name == "request_t2_approval":
                action = tool_input.get("action", "")
                detail = tool_input.get("detail", "")
                slack_channel = tool_input.get("slack_channel", "kai")
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


            # Plane PM tools
            elif tool_name == "get_plane_issues":
                project_id = tool_input.get("project_id", "")
                issue_id = tool_input.get("issue_id", "")
                state = tool_input.get("state", "")
                headers = {"X-API-Key": PLANE_API_TOKEN}
                try:
                    if issue_id and project_id:
                        r = httpx.get(f"{PLANE_BASE_URL}/workspaces/{PLANE_WORKSPACE}/projects/{project_id}/issues/{issue_id}/", headers=headers, timeout=10)
                        return r.json()
                    elif project_id:
                        params = {}
                        if state:
                            params["state"] = state
                        r = httpx.get(f"{PLANE_BASE_URL}/workspaces/{PLANE_WORKSPACE}/projects/{project_id}/issues/", headers=headers, params=params, timeout=10)
                        data = r.json()
                        issues = data.get("results", data) if isinstance(data, dict) else data
                        return {"issues": [{"id": i.get("id"), "name": i.get("name"), "state": i.get("state_detail", {}).get("name"), "description": i.get("description_stripped", "")} for i in issues]}
                    else:
                        # List all projects
                        r = httpx.get(f"{PLANE_BASE_URL}/workspaces/{PLANE_WORKSPACE}/projects/", headers=headers, timeout=10)
                        data = r.json()
                        projects = data.get("results", data) if isinstance(data, dict) else data
                        return {"projects": [{"id": p.get("id"), "name": p.get("name"), "identifier": p.get("identifier")} for p in projects]}
                except Exception as e:
                    return {"error": f"Plane get failed: {e}"}

            elif tool_name == "update_plane_issue":
                project_id = tool_input.get("project_id", "")
                issue_id = tool_input.get("issue_id", "")
                updates = {k: v for k, v in tool_input.items() if k not in ("project_id", "issue_id")}
                headers = {"X-API-Key": PLANE_API_TOKEN, "Content-Type": "application/json"}
                try:
                    r = httpx.patch(f"{PLANE_BASE_URL}/workspaces/{PLANE_WORKSPACE}/projects/{project_id}/issues/{issue_id}/", headers=headers, json=updates, timeout=10)
                    return {"updated": True, "issue_id": issue_id, "response": r.json()}
                except Exception as e:
                    return {"error": f"Plane update failed: {e}"}

            elif tool_name == "create_plane_issue":
                project_id = tool_input.get("project_id", "")
                payload = {k: v for k, v in tool_input.items() if k != "project_id"}
                headers = {"X-API-Key": PLANE_API_TOKEN, "Content-Type": "application/json"}
                try:
                    r = httpx.post(f"{PLANE_BASE_URL}/workspaces/{PLANE_WORKSPACE}/projects/{project_id}/issues/", headers=headers, json=payload, timeout=10)
                    result = r.json()
                    return {"created": True, "issue_id": result.get("id"), "name": result.get("name")}
                except Exception as e:
                    return {"error": f"Plane create failed: {e}"}

    except Exception as e:
        logger.exception("execute_tool %s: %s", tool_name, e)
        return {"error": str(e)}
    return {"error": f"Unknown tool: {tool_name}"}
