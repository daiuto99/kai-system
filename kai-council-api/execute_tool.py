import json
import re
import logging
import os
from datetime import datetime as _dt2, date as _d2, timedelta as _td2
from pathlib import Path
import httpx
from council_config import WORKER_URL, VAULT_PATH, ADVISOR_AVATARS, _slack_token, _worker_auth
from knowledge_layer import _write_session_summary, _write_decision, _log_mission_deliverable
from usage_tracker import track_api_call
import function_map as fm

logger = logging.getLogger(__name__)

# n8n
N8N_REGISTRY_FILE = VAULT_PATH / "00_System" / "n8n_workflows.json"
# Plane PM
PLANE_API_TOKEN = open("/run/secrets/plane_api_token").read().strip().split("\n")[0]
PLANE_BASE_URL = "http://172.18.0.1:8090/api/v1"
PLANE_WORKSPACE = "sonicink"
from council_config import ORCHESTRATOR_URL as _ORCH_URL


def _capability_auth_headers() -> dict[str, str]:
    """Attach the dedicated router credential; an absent file fails closed."""
    try:
        record = Path("/run/secrets/orchestrator_capability_auth").read_text().strip()
    except OSError:
        return {}
    _identity, separator, secret = record.partition(":")
    if not separator:
        return {}
    return {"X-KAI-Capability-Key": secret} if secret else {}



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
    return {"specialists": [
        {"id": s["id"], "name": s["name"], "domain": s["domain"]}
        for s in fm.list_specialists()
    ]}


def _consult_specialist(specialist_id: str, question: str, context: str,
                        active_project: str | None = None, audit_task_id: str | None = None) -> dict:
    """Consult through the Memory Service; never assemble specialist context locally."""
    from council_config import _track_usage
    from router import _run_agentic_loop

    spec = fm.get_specialist(specialist_id)
    if not spec:
        available = [s["id"] for s in fm.list_specialists()]
        return {"error": f"Specialist '{specialist_id}' not found. Available: {available}"}

    user_msg = question if not context else f"Context: {context}\n\nQuestion: {question}"
    consult_device = (f"task:{audit_task_id}:consult:{specialist_id}"
                      if audit_task_id else f"consult:{specialist_id}")
    assemble_body = {
        "key": {"advisor": specialist_id, "device": consult_device,
                "place": None, "thread": None},
        "message": user_msg,
        "task_type": "specialist_consult",
        "channel": "consult",
    }
    # §7.2 decision: project scope is inherited only from the message boundary.
    # No project means globals + the specialist's own memory, never a prompt.
    if active_project:
        assemble_body["project"] = active_project
    try:
        assembled = httpx.post(f"{_ORCH_URL}/context/assemble", json=assemble_body, timeout=15)
        assembled.raise_for_status()
        package = assembled.json()["package"]
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        logger.exception("consult_specialist memory assembly failed: %s", exc)
        return {"error": f"Memory Service assembly failed: {exc}"}

    try:
        httpx.post(f"{_ORCH_URL}/context/turn", json={
            "key": assemble_body["key"], "role": "user", "content": user_msg,
            "package_id": package["package_id"],
        }, timeout=10)
    except httpx.HTTPError:
        logger.exception("consult_specialist context user turn recording failed")

    system = package["stable_text"]
    if package.get("volatile_text"):
        system += "\n\n---\n\n" + package["volatile_text"]
    if package.get("facts_text"):
        system += "\n\n" + package["facts_text"]
    if package.get("recall_text"):
        system += "\n\n" + package["recall_text"]

    try:
        messages = [*package.get("messages", []), {"role": "user", "content": user_msg}]
        # Specialists are end-of-chain — no tools, no further delegation
        reply, input_tokens, output_tokens, cache_read_tok, cache_creation_tok = _run_agentic_loop(
            messages, [], "claude-sonnet-4-6", system, specialist_id
        )
        _track_usage("specialist", input_tokens, output_tokens,
                     trigger_source=f"tool:consult_specialist:{specialist_id}",
                     cache_read_tokens=cache_read_tok,
                     cache_creation_tokens=cache_creation_tok)
        try:
            httpx.post(f"{_ORCH_URL}/context/turn", json={
                "key": assemble_body["key"], "role": "assistant", "content": reply,
                "package_id": package["package_id"],
            }, timeout=10)
        except httpx.HTTPError:
            logger.exception("consult_specialist context assistant turn recording failed")
        return {
            "specialist": spec["name"],
            "domain": spec["domain"],
            "response": reply,
            "assembly": {
                "package_id": package["package_id"],
                "project_scope": active_project,
                "tier3_hits": package["budget_report"]["t3"]["hits"],
                "tier4_fact_ids": package["budget_report"]["t4"]["facts"],
            },
            "_instruction": "INTERNAL USE ONLY. Use this response to inform your own decisions and actions. Do NOT relay the specialist's questions, gates, or blockers to Leo. Resolve every item yourself using vault tools, workspace tools, and professional judgment. Only tell Leo what you built or decided — not what the specialist asked.",
        }
    except Exception as e:
        logger.exception("consult_specialist: %s", e)
        return {"error": str(e)}

def _h_workflows(client, tool_name, ti, advisor):

    if tool_name == "list_capabilities":
        try:
            r = httpx.get(f"{_ORCH_URL}/capabilities", timeout=10)
            data = r.json()
            caps = data.get("capabilities", [])
            lines = []
            for c in caps:
                flags = []
                if c.get("destructive"):
                    flags.append("destructive — requires confirmed=true")
                if c.get("read_only"):
                    flags.append("read-only")
                if c.get("rate_limit"):
                    rl = c["rate_limit"]
                    flags.append(f"rate-limited: {rl['max']}/{rl['window']}s")
                flag_str = " | ".join(flags) if flags else "safe"
                lines.append(f"  {c['name']} [{flag_str}]")
            return {"capabilities": "\n".join(lines), "count": data.get("count", len(caps))}
        except Exception as e:
            return {"error": str(e)}

    if tool_name == "run_capability":
        capability = ti.get("capability", "")
        inputs = ti.get("inputs", {})
        confirmed = ti.get("confirmed", False)
        if not capability:
            return {"ok": False, "error": "capability name required"}
        try:
            payload = {"inputs": inputs}
            if confirmed:
                payload["confirmed"] = True
            r = httpx.post(
                f"{_ORCH_URL}/capability/{capability}", json=payload, timeout=60,
                headers=_capability_auth_headers(),
            )
            return r.json()
        except Exception as e:
            return {"ok": False, "error": str(e)}

    if tool_name == "save_workflow":
        return client.post(f"{WORKER_URL}/workflows", json=ti).json()
    if tool_name == "list_workflows":
        return client.get(f"{WORKER_URL}/workflows").json()
    if tool_name == "delete_workflow":
        return client.delete(f"{WORKER_URL}/workflows/{ti.get('id', '')}").json()


def _h_tasks(client, tool_name, ti, advisor):
    if tool_name == "search_tasks":
        return client.get(f"{WORKER_URL}/tasks/search", params={"q": ti.get("query", "")}).json()
    if tool_name == "list_tasks":
        return client.get(f"{WORKER_URL}/tasks").json()
    if tool_name == "complete_task":
        r = client.post(f"{WORKER_URL}/tasks/{ti['task_id']}/complete").json()
        if r.get("ok"):
            return {"ok": True, "status": "Task completed and removed from Todoist active list."}
        return {"ok": False, "error": "Failed to complete task"}
    if tool_name == "create_task":
        return client.post(f"{WORKER_URL}/tasks", json=ti).json()
    if tool_name == "update_task":
        task_id = ti.pop("task_id")
        return client.patch(f"{WORKER_URL}/tasks/{task_id}", json=ti).json()
    if tool_name == "delete_task":
        return client.delete(f"{WORKER_URL}/tasks/{ti['task_id']}").json()
    if tool_name == "reschedule_task":
        task_id = ti.pop("task_id")
        payload = {"due_date": ti.get("due_date", ""), "move_to_today": ti.get("move_to_today", False)}
        return client.patch(f"{WORKER_URL}/tasks/{task_id}", json=payload).json()
    if tool_name == "list_todoist_projects":
        return client.get(f"{WORKER_URL}/tasks/projects").json()
    if tool_name == "create_todoist_project":
        return client.post(f"{WORKER_URL}/tasks/projects", json=ti).json()
    if tool_name == "delete_todoist_project":
        return client.delete(f"{WORKER_URL}/tasks/projects/{ti['project_id']}").json()


def _h_projects(client, tool_name, ti, advisor):
    if tool_name == "create_project":
        return client.post(f"{WORKER_URL}/projects", json=ti).json()
    if tool_name == "update_project":
        pid = ti.pop("id")
        return client.patch(f"{WORKER_URL}/projects/{pid}", json=ti).json()
    if tool_name == "list_projects":
        return client.get(f"{WORKER_URL}/projects").json()
    if tool_name == "delete_project":
        return client.delete(f"{WORKER_URL}/projects/{ti['id']}").json()
    if tool_name == "setup_project":
        r = client.post(f"{WORKER_URL}/projects/setup", json=ti, timeout=30)
        return r.json() if r.status_code == 200 else {"error": f"Worker {r.status_code}: {r.text[:200]}"}
    if tool_name == "teardown_project":
        r = client.post(f"{WORKER_URL}/projects/{ti['id']}/teardown", timeout=30)
        return r.json() if r.status_code == 200 else {"error": f"Worker {r.status_code}: {r.text[:200]}"}


def _h_vault(client, tool_name, ti, advisor):
    if tool_name == "write_to_vault":
        r = client.post(f"{WORKER_URL}/vault/write",
            params={"path": ti["path"], "content": ti["content"]})
        result = r.json()
        _log_mission_deliverable(ti["path"], ti.get("description", ""))
        return result
    if tool_name == "read_vault":
        r = client.get(f"{WORKER_URL}/vault/read", params={"path": ti["path"]})
        if r.status_code == 404:
            return {"error": "File not found in vault: " + ti["path"]}
        try:
            return r.json()
        except Exception:
            return {"error": "vault/read non-JSON (status " + str(r.status_code) + "): " + r.text[:200]}
    if tool_name == "read_workspace":
        r = client.get(f"{WORKER_URL}/workspace/read", params={"path": ti["path"]})
        if r.status_code == 404:
            return {"error": "File not found in workspace: " + ti["path"] + ". Workspace may need a sync."}
        try:
            return r.json()
        except Exception as e:
            return {"error": "workspace/read non-JSON (status " + str(r.status_code) + "): " + r.text[:200]}
    if tool_name == "list_workspace":
        p = ti.get("path", "")
        r = client.get(f"{WORKER_URL}/workspace/list", params={"path": p})
        if r.status_code == 404:
            return {"error": "Directory not found in workspace: " + p}
        try:
            return r.json()
        except Exception as e:
            return {"error": "workspace/list non-JSON (status " + str(r.status_code) + "): " + r.text[:200]}


def _h_slack(client, tool_name, ti, advisor):
    if tool_name == "send_slack_message":
        token = _slack_token()
        if not token:
            return {"error": "Slack token not configured"}
        from council_config import ADVISOR_LABELS
        adv = ti.get("advisor", "kai")
        channel = ti.get("channel", "kai")
        if not channel.startswith("#"):
            channel = f"#{channel}"
        # Self-posting advisors keep their identity; everyone else is relayed by KAI
        if adv in ADVISOR_AVATARS:
            username = "KAI" if adv == "kai" else adv.capitalize()
            icon_url = ADVISOR_AVATARS[adv]
            text = ti["message"]
        else:
            username = "KAI"
            icon_url = ADVISOR_AVATARS["kai"]
            label = ADVISOR_LABELS.get(adv, adv.capitalize())
            text = f"{label} says:\n{ti['message']}"
        # AR-5.3: rerouted to Telegram (sole surface). channel/username/icon ignored.
        from tg_alert import tg_alert
        if tg_alert(text):
            return {"ok": True, "surface": "telegram"}
        return {"error": "telegram send failed"}
    if tool_name == "deliver_asset":
        r = client.post(f"{WORKER_URL}/assets/deliver", json=ti, timeout=120)
        return r.json() if r.status_code == 200 else {"error": f"Worker {r.status_code}: {r.text[:200]}"}
    if tool_name == "get_advisor_recent_dms":
        adv = ti.get("advisor", "")
        n = ti.get("n", 20)
        r = client.get(f"{WORKER_URL}/council/advisor/{adv}/recent_dms", params={"n": n}, timeout=15)
        return r.json() if r.status_code == 200 else {"error": f"Worker {r.status_code}: {r.text[:200]}"}
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



def _h_ingest(client, tool_name, ti, advisor):
    import subprocess, shlex
    if tool_name == "ingest_knowledge":
        target = ti.get("path", f"/vault/60_Council/{ti.get('advisor', advisor)}/knowledge")
        target_advisor = ti.get("advisor", advisor)
        env = {**__import__("os").environ, "QDRANT_URL": "http://kai-qdrant:6333", "OLLAMA_URL": "http://kai-ollama:11434"}
        result = subprocess.run(
            ["python3", "/app/ingest.py", target, "--advisor", target_advisor],
            capture_output=True, text=True, timeout=300, env=env
        )
        if result.returncode != 0:
            return {"error": result.stderr[:500]}
        lines = [l for l in result.stdout.strip().splitlines() if l.strip()]
        return {"status": "ok", "summary": lines[-1] if lines else "done", "output": result.stdout.strip()}
    if tool_name == "list_knowledge":
        env = {**__import__("os").environ, "QDRANT_URL": "http://kai-qdrant:6333", "OLLAMA_URL": "http://kai-ollama:11434"}
        result = subprocess.run(
            ["python3", "/app/ingest.py", "--list"],
            capture_output=True, text=True, timeout=30, env=env
        )
        return {"status": "ok", "output": result.stdout.strip()}
    if tool_name == "clear_knowledge":
        target_advisor = ti.get("advisor", advisor)
        env = {**__import__("os").environ, "QDRANT_URL": "http://kai-qdrant:6333", "OLLAMA_URL": "http://kai-ollama:11434"}
        result = subprocess.run(
            ["python3", "/app/ingest.py", "--clear", target_advisor],
            capture_output=True, text=True, timeout=30, env=env
        )
        return {"status": "ok", "output": result.stdout.strip()}

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
            ti["specialist"], ti["question"], ti.get("context", ""),
            active_project=ti.get("_active_project"), audit_task_id=ti.get("_audit_task_id"),
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
    import uuid as _uuid
    import datetime as _dt

    def _wp_creds(site_key):
        wp_sites = json.loads((VAULT_PATH / "00_System" / "wordpress_sites.json").read_text())
        sites = wp_sites["sites"]
        resolved_key = site_key

        # Exact match first
        site = sites.get(site_key)

        # Strip TLD and retry
        if not site:
            normalized = re.sub(r'\.(com|org|net|space|io|co|dev|app|site|us|uk|ca)$', '', site_key, flags=re.IGNORECASE)
            if normalized != site_key:
                site = sites.get(normalized)
                if site:
                    resolved_key = normalized

        # Substring match: find any key that the input contains or that contains the input
        if not site:
            for k, v in sites.items():
                if site_key in k or k in site_key:
                    site = v
                    resolved_key = k
                    break

        if not site:
            available = list(sites.keys())
            raise ValueError(f"Unknown site: {site_key!r}. Available: {available}. Check wordpress_sites.json.")

        site_key = resolved_key
        # Read kai_app_password from Docker secrets volume; fall back to JSON field
        wp_secrets_path = Path("/run/wp_secrets")
        secret_file = wp_secrets_path / f"wp_{site_key}_kai_app_password.txt"
        if secret_file.exists():
            kai_pw = secret_file.read_text().strip()
        else:
            kai_pw = site.get("kai_app_password", "")
        if kai_pw:
            user, pw = "kai", kai_pw
        elif site.get("app_password"):
            user, pw = site.get("username", ""), site["app_password"]
        else:
            raise ValueError(f"No app password configured for {site_key}. Secret file not found: {secret_file}")
        creds = _b64.b64encode(f"{user}:{pw}".encode()).decode()
        fqdn = site.get("cloudways_fqdn")
        base_url = f"https://{fqdn}" if fqdn else site["url"]
        verify = False if fqdn else True  # Cloudways FQDNs have no valid TLS cert
        return site, creds, base_url, verify

    def _hdrs(creds):
        return {"Authorization": f"Basic {creds}", "Content-Type": "application/json"}

    def _shape_item(p):
        return {
            "id": p.get("id"),
            "title": p.get("title", {}).get("rendered", ""),
            "status": p.get("status"),
            "date": p.get("date", "")[:10],
            "link": p.get("link"),
            "slug": p.get("slug", ""),
            "excerpt": p.get("excerpt", {}).get("rendered", "")[:200],
        }

    # ── Cloudways SSH/file helpers ───────────────────────────────────────
    import subprocess as _sp
    import shlex as _sx
    CLOUDWAYS_HOST = "134.209.166.23"
    CLOUDWAYS_USER = "master_vvbwxpwpcc"
    SSH_KEY = "/run/secrets/cloudways_ssh_key"
    WEBROOT_BASE = "/home/1623875.cloudwaysapps.com"
    MAX_FILE_BYTES = 524288  # 512 KB cap

    def _site_root(site):
        u = site.get("cloudways_sys_user")
        if not u:
            raise ValueError("site has no cloudways_sys_user")
        return f"{WEBROOT_BASE}/{u}/public_html"

    def _sandbox(site, rel):
        if not rel:
            raise ValueError("path required")
        if rel.startswith("/"):
            raise ValueError(f"absolute path not allowed: {rel}")
        parts = rel.replace("\\", "/").split("/")
        if ".." in parts:
            raise ValueError(f"path traversal not allowed: {rel}")
        return f"{_site_root(site)}/{rel.lstrip('/')}"

    def _ssh(cmd, stdin_bytes=None, timeout=30):
        args = ["ssh", "-i", SSH_KEY,
                "-o", "StrictHostKeyChecking=no",
                "-o", "UserKnownHostsFile=/dev/null",
                "-o", "LogLevel=ERROR",
                "-o", "BatchMode=yes",
                "-o", "ConnectTimeout=10",
                f"{CLOUDWAYS_USER}@{CLOUDWAYS_HOST}", cmd]
        r = _sp.run(args, capture_output=True, timeout=timeout, input=stdin_bytes)
        return r.returncode, r.stdout, r.stderr

    if tool_name == "wordpress_read_file":
        site_key = ti.get("site")
        rel = ti.get("path", "")
        try:
            site, _, _, _ = _wp_creds(site_key)
            abs_p = _sandbox(site, rel)
            # check size first
            rc, out, err = _ssh(f"stat -c%s {_sx.quote(abs_p)} 2>/dev/null || echo MISSING")
            size_s = out.decode().strip()
            if size_s == "MISSING":
                return {"error": f"File not found: {rel}"}
            if int(size_s) > MAX_FILE_BYTES:
                return {"error": f"File too large ({size_s} bytes; max {MAX_FILE_BYTES})"}
            rc, out, err = _ssh(f"cat {_sx.quote(abs_p)}", timeout=20)
            if rc != 0:
                return {"error": f"ssh cat failed: {err.decode()[:200]}"}
            return {"site": site_key, "path": rel, "size": int(size_s), "content": out.decode("utf-8", errors="replace")}
        except Exception as e:
            logger.exception("wordpress_read_file: %s", e)
            return {"error": str(e)}

    if tool_name == "wordpress_write_file":
        site_key = ti.get("site")
        rel = ti.get("path", "")
        content_str = ti.get("content", "")
        try:
            site, _, _, _ = _wp_creds(site_key)
            abs_p = _sandbox(site, rel)
            content_b = content_str.encode("utf-8")
            if len(content_b) > MAX_FILE_BYTES:
                return {"error": f"Content too large ({len(content_b)} bytes; max {MAX_FILE_BYTES})"}
            ts = _dt.datetime.utcnow().strftime("%Y%m%d-%H%M%S")
            backup = f"{abs_p}.bak_{ts}"
            # 1. Backup current file if present
            backup_made = False
            rc, _o, _e = _ssh(f"test -f {_sx.quote(abs_p)}")
            if rc == 0:
                rc2, _, err2 = _ssh(f"cp -p {_sx.quote(abs_p)} {_sx.quote(backup)}")
                if rc2 != 0:
                    return {"error": f"backup failed: {err2.decode()[:200]}"}
                backup_made = True
            # 2. Write via ssh-cat to a sibling tmp in the same directory, then mv (atomic same-fs)
            import posixpath as _pp
            _dir = _pp.dirname(abs_p) or _site_root(site)
            remote_tmp = f"{_dir}/.kai_wp_{_uuid.uuid4().hex[:10]}.tmp"
            rc3, _, err3 = _ssh(f"cat > {_sx.quote(remote_tmp)}", stdin_bytes=content_b, timeout=30)
            if rc3 != 0:
                _ssh(f"rm -f {_sx.quote(remote_tmp)}")
                return {"error": f"ssh cat write failed: {err3.decode()[:200]}"}
            rc4, out4, err4 = _ssh(f"mv {_sx.quote(remote_tmp)} {_sx.quote(abs_p)} && stat -c%s {_sx.quote(abs_p)}")
            if rc4 != 0:
                _ssh(f"rm -f {_sx.quote(remote_tmp)}")
                return {"error": f"mv failed: {err4.decode()[:200]}"}
            return {"site": site_key, "path": rel,
                    "bytes_written": int(out4.decode().strip()),
                    "backup": backup if backup_made else None}
        except Exception as e:
            logger.exception("wordpress_write_file: %s", e)
            return {"error": str(e)}

    if tool_name == "wordpress_list_files":
        site_key = ti.get("site")
        rel = ti.get("path", "")
        try:
            site, _, _, _ = _wp_creds(site_key)
            # allow empty path = webroot
            if rel:
                abs_p = _sandbox(site, rel)
            else:
                abs_p = _site_root(site)
            rc, out, err = _ssh(f"ls -la --time-style=long-iso {_sx.quote(abs_p)} 2>&1", timeout=15)
            if rc != 0:
                return {"error": f"ssh ls failed: {out.decode()[:200]} {err.decode()[:200]}"}
            lines = [l for l in out.decode().splitlines() if l and not l.startswith("total ")]
            return {"site": site_key, "path": rel or ".", "abs_path": abs_p, "listing": lines}
        except Exception as e:
            logger.exception("wordpress_list_files: %s", e)
            return {"error": str(e)}

    if tool_name == "wordpress_delete_file":
        site_key = ti.get("site")
        rel = ti.get("path", "")
        try:
            site, _, _, _ = _wp_creds(site_key)
            abs_p = _sandbox(site, rel)
            ts = _dt.datetime.utcnow().strftime("%Y%m%d-%H%M%S")
            backup = f"{abs_p}.bak_{ts}"
            # back up then delete
            script = (
                f"if [ ! -e {_sx.quote(abs_p)} ]; then echo MISSING; exit 2; fi && "
                f"cp -p {_sx.quote(abs_p)} {_sx.quote(backup)} && rm {_sx.quote(abs_p)} && echo OK"
            )
            rc, out, err = _ssh(script, timeout=20)
            tag = out.decode().strip()
            if tag == "MISSING":
                return {"error": f"File not found: {rel}"}
            if rc != 0 or tag != "OK":
                return {"error": f"ssh delete failed: {err.decode()[:200]}"}
            return {"site": site_key, "path": rel, "deleted": True, "backup": backup}
        except Exception as e:
            logger.exception("wordpress_delete_file: %s", e)
            return {"error": str(e)}

    if tool_name == "wordpress_purge_varnish":
        _task_id = ti.get("task_id")
        if not _task_id:
            return {"blocked": "task_id is required. Call wordpress_create_task first to start a tracked build. Every WP build must be tracked."}
        site_key = ti.get("site")
        url_path = ti.get("url_path", "/")
        try:
            site, _, _, _ = _wp_creds(site_key)
            host_custom = site.get("url", "").replace("https://", "").replace("http://", "").rstrip("/")
            host_fqdn = site.get("cloudways_fqdn", "")
            results = {}
            for host in filter(None, [host_custom, host_fqdn]):
                cmd = f"curl -s -o /dev/null -w '%{{http_code}}' -X PURGE -H 'Host: {host}' http://localhost:8080{url_path}"
                rc, out, err = _ssh(cmd, timeout=15)
                results[host] = out.decode().strip() if rc == 0 else f"ssh_err: {err.decode()[:80]}"
            return {"site": site_key, "url_path": url_path, "purges": results}
        except Exception as e:
            logger.exception("wordpress_purge_varnish: %s", e)
            return {"error": str(e)}

    if tool_name == "wordpress_list_sites":
        wp_sites = json.loads((VAULT_PATH / "00_System" / "wordpress_sites.json").read_text())
        return {
            "sites": [
                {"id": k, "url": v["url"], "description": v.get("description", ""),
                 "business": v.get("business", ""), "blank_canvas": v.get("blank_canvas_installed", False)}
                for k, v in wp_sites["sites"].items()
            ],
            "count": len(wp_sites["sites"]),
        }

    if tool_name == "wordpress_get_posts":
        site_key = ti.get("site", "leodaiuto")
        count = min(ti.get("count", 5), 20)
        status = ti.get("status", "any")
        try:
            site, creds, base_url, verify = _wp_creds(site_key)
            wp_params = {"per_page": count, "_fields": "id,title,status,date,link,slug,excerpt"}
            if status and status != "any":
                wp_params["status"] = status
            r = httpx.get(f"{base_url}/wp-json/wp/v2/posts",
                params=wp_params, headers=_hdrs(creds), follow_redirects=True, timeout=15, verify=verify)
            posts = r.json() if r.status_code == 200 else []
            return {"site": site_key, "url": site["url"], "posts": [_shape_item(p) for p in posts]}
        except Exception as e:
            logger.exception("wordpress_get_posts: %s", e)
            return {"error": f"WordPress get posts failed: {e}"}

    if tool_name == "wordpress_get_pages":
        site_key = ti.get("site", "leodaiuto")
        count = min(ti.get("count", 20), 50)
        status = ti.get("status", "any")
        try:
            site, creds, base_url, verify = _wp_creds(site_key)
            wp_params = {"per_page": count, "_fields": "id,title,status,date,modified,link,slug,template"}
            if status and status != "any":
                wp_params["status"] = status
            r = httpx.get(f"{base_url}/wp-json/wp/v2/pages",
                params=wp_params, headers=_hdrs(creds), follow_redirects=True, timeout=15, verify=verify)
            pages = r.json() if r.status_code == 200 else []
            return {"site": site_key, "url": site["url"], "pages": [_shape_item(p) for p in pages], "count": len(pages)}
        except Exception as e:
            logger.exception("wordpress_get_pages: %s", e)
            return {"error": f"WordPress get pages failed: {e}"}

    if tool_name == "wordpress_get_post":
        site_key = ti.get("site", "leodaiuto")
        post_id = ti.get("post_id")
        post_type = ti.get("post_type", "posts")
        endpoint = "pages" if post_type == "pages" else "posts"
        try:
            site, creds, base_url, verify = _wp_creds(site_key)
            r = httpx.get(f"{base_url}/wp-json/wp/v2/{endpoint}/{post_id}",
                headers=_hdrs(creds), follow_redirects=True, timeout=15, verify=verify)
            if r.status_code != 200:
                return {"error": f"WP returned {r.status_code}"}
            p = r.json()
            return {
                "id": p.get("id"), "title": p.get("title", {}).get("rendered", ""),
                "content": p.get("content", {}).get("rendered", ""),
                "status": p.get("status"), "link": p.get("link"),
                "slug": p.get("slug"), "template": p.get("template", ""),
            }
        except Exception as e:
            logger.exception("wordpress_get_post: %s", e)
            return {"error": str(e)}

    if tool_name == "wordpress_get_site_info":
        site_key = ti.get("site", "leodaiuto")
        try:
            site, creds, base_url, verify = _wp_creds(site_key)
            hdrs = _hdrs(creds)
            root_r = httpx.get(f"{base_url}/wp-json/", follow_redirects=True, timeout=10, verify=verify)
            d = root_r.json() if root_r.status_code == 200 else {}
            pages_r = httpx.get(f"{base_url}/wp-json/wp/v2/pages",
                params={"per_page": 50, "_fields": "id,title,slug,status,link,template"},
                headers=hdrs, follow_redirects=True, timeout=15, verify=verify)
            pages = pages_r.json() if pages_r.status_code == 200 else []
            return {
                "site": site_key, "url": site["url"],
                "title": d.get("name", ""), "description": d.get("description", ""),
                "pages": [{"id": p["id"], "title": p["title"]["rendered"],
                           "slug": p["slug"], "status": p["status"],
                           "link": p["link"], "template": p.get("template", "")}
                          for p in pages],
                "page_count": len(pages),
            }
        except Exception as e:
            logger.exception("wordpress_get_site_info: %s", e)
            return {"error": str(e)}

    if tool_name == "wordpress_create_post":
        site_key = ti.get("site", "leodaiuto")
        title = ti.get("title", "")
        content_body = ti.get("content", "")
        status = ti.get("status", "draft")
        tags = ti.get("tags", [])
        excerpt = ti.get("excerpt", "")
        try:
            site, creds, base_url, verify = _wp_creds(site_key)
            hdrs = _hdrs(creds)
            tag_ids = []
            for tag_name in tags:
                tr = httpx.get(f"{base_url}/wp-json/wp/v2/tags",
                    params={"search": tag_name}, headers=hdrs, follow_redirects=True, timeout=10, verify=verify)
                existing = tr.json() if tr.status_code == 200 else []
                if existing:
                    tag_ids.append(existing[0]["id"])
                else:
                    cr = httpx.post(f"{base_url}/wp-json/wp/v2/tags",
                        json={"name": tag_name}, headers=hdrs, follow_redirects=True, timeout=10, verify=verify)
                    if cr.status_code in (200, 201):
                        tag_ids.append(cr.json().get("id"))
            payload = {"title": title, "content": content_body, "status": status, "excerpt": excerpt}
            if tag_ids:
                payload["tags"] = tag_ids
            r = httpx.post(f"{base_url}/wp-json/wp/v2/posts",
                json=payload, headers=hdrs, follow_redirects=True, timeout=20, verify=verify)
            post = r.json()
            return {
                "created": True, "id": post.get("id"), "status": post.get("status"),
                "link": post.get("link"), "title": title, "site": site_key,
                "message": f"Post {'published' if status == 'publish' else 'saved as draft'} on {site['url']}",
            }
        except Exception as e:
            logger.exception("wordpress_create_post: %s", e)
            return {"error": f"WordPress create post failed: {e}"}

    if tool_name == "wordpress_create_page":
        _task_id = ti.get("task_id")
        if not _task_id:
            return {"blocked": "task_id is required. Call wordpress_create_task first to start a tracked build. Every WP build must be tracked."}
        site_key = ti.get("site", "leodaiuto")
        title = ti.get("title", "")
        content_body = ti.get("content", "")
        status = ti.get("status", "draft")
        slug = ti.get("slug", "")
        template = ti.get("template", "")
        import re as _re
        if _re.search(r'<(!DOCTYPE|html|head|body)[\s>]', content_body, _re.IGNORECASE):
            return {"error": "content_format_error", "message": "Content must be body HTML only. Do not include <!DOCTYPE>, <html>, <head>, or <body> tags. Pass clean block content or shortcodes only."}
        try:
            site, creds, base_url, verify = _wp_creds(site_key)
            payload = {"title": title, "content": content_body, "status": status, "template": template}
            if slug:
                payload["slug"] = slug
            r = httpx.post(f"{base_url}/wp-json/wp/v2/pages",
                json=payload, headers=_hdrs(creds), follow_redirects=True, timeout=30, verify=verify)
            if r.status_code not in (200, 201):
                return {"error": f"WP returned {r.status_code}", "body": r.text[:300]}
            p = r.json()
            return {
                "created": True, "id": p.get("id"), "status": p.get("status"),
                "link": p.get("link"), "slug": p.get("slug"), "title": title,
                "site": site_key, "template": template,
                "message": f"Page {'published' if status == 'publish' else 'saved as draft'} on {site['url']}",
            }
        except Exception as e:
            logger.exception("wordpress_create_page: %s", e)
            return {"error": str(e)}

    if tool_name == "wordpress_update_post":
        _task_id = ti.get("task_id")
        if not _task_id:
            return {"blocked": "task_id is required. Call wordpress_create_task first to start a tracked build. Every WP build must be tracked."}
        site_key = ti.get("site", "leodaiuto")
        post_id = ti.get("post_id")
        post_type = ti.get("post_type", "posts")
        endpoint = "pages" if post_type == "pages" else "posts"
        payload = {k: ti[k] for k in ("title", "content", "status", "excerpt", "slug", "template") if k in ti}
        import re as _re
        if "content" in payload and _re.search(r'<(!DOCTYPE|html|head|body)[\s>]', payload["content"], _re.IGNORECASE):
            return {"error": "content_format_error", "message": "Content must be body HTML only. Do not include <!DOCTYPE>, <html>, <head>, or <body> tags."}
        try:
            site, creds, base_url, verify = _wp_creds(site_key)
            r = httpx.patch(f"{base_url}/wp-json/wp/v2/{endpoint}/{post_id}",
                json=payload, headers=_hdrs(creds), follow_redirects=True, timeout=30, verify=verify)
            if r.status_code not in (200, 201):
                return {"error": f"WP returned {r.status_code}", "body": r.text[:300]}
            p = r.json()
            return {"updated": True, "id": p.get("id"), "status": p.get("status"), "link": p.get("link")}
        except Exception as e:
            logger.exception("wordpress_update_post: %s", e)
            return {"error": str(e)}

    if tool_name == "wordpress_publish":
        site_key = ti.get("site", "leodaiuto")
        post_id = ti.get("post_id")
        post_type = ti.get("post_type", "posts")
        endpoint = "pages" if post_type == "pages" else "posts"
        try:
            site, creds, base_url, verify = _wp_creds(site_key)
            r = httpx.patch(f"{base_url}/wp-json/wp/v2/{endpoint}/{post_id}",
                json={"status": "publish"}, headers=_hdrs(creds), follow_redirects=True, timeout=20, verify=verify)
            if r.status_code not in (200, 201):
                return {"error": f"WP returned {r.status_code}"}
            p = r.json()
            return {"published": True, "id": p.get("id"), "link": p.get("link"), "site": site_key}
        except Exception as e:
            logger.exception("wordpress_publish: %s", e)
            return {"error": str(e)}

    if tool_name == "wordpress_set_custom_css":
        site_key = ti.get("site", "leodaiuto")
        css = ti.get("css", "")
        try:
            site, creds, base_url, verify = _wp_creds(site_key)
            webroot  = _site_root(site)
            mu_dir   = f"{webroot}/wp-content/mu-plugins"
            css_path = f"{webroot}/wp-content/kai-custom.css"
            mu_path  = f"{mu_dir}/kai-custom-css.php"
            mu_php = (
                '<?php\n'
                'add_action("wp_head", function() {\n'
                '    $f = dirname(__DIR__) . "/kai-custom.css";\n'
                '    if (file_exists($f)) echo "<style>" . file_get_contents($f) . "</style>\\n";\n'
                '}, 100);\n'
            )
            _ssh(f"mkdir -p {_sx.quote(mu_dir)}")
            rc1, _, e1 = _ssh(f"cat > {_sx.quote(mu_path)}", stdin_bytes=mu_php.encode(), timeout=20)
            if rc1 != 0:
                return {"error": f"write mu-plugin failed: {e1.decode()[:200]}"}
            rc2, _, e2 = _ssh(f"cat > {_sx.quote(css_path)}", stdin_bytes=css.encode(), timeout=20)
            if rc2 != 0:
                return {"error": f"write CSS failed: {e2.decode()[:200]}"}
            return {"ok": True, "site": site_key, "message": "Custom CSS updated via mu-plugin"}
        except Exception as e:
            logger.exception("wordpress_set_custom_css: %s", e)
            return {"error": str(e)}

    if tool_name == "wordpress_create_task":
        import uuid as _uuid
        return {"ok": True, "task_id": "wpt-" + _uuid.uuid4().hex[:8], "state": "created",
                "note": "orchestrator-managed — task tracking is now in kai-orchestrator"}

    if tool_name == "wordpress_complete_task":
        return {"ok": True, "state": "complete",
                "note": "orchestrator-managed — use orchestrator job state for completion"}

    if tool_name == "wordpress_upload_media":
        import posixpath as _pp
        site_key = ti.get("site", "leodaiuto")
        rel_path = ti.get("path", "")
        filename = ti.get("filename", _pp.basename(rel_path) if rel_path else "upload.png")
        mime     = ti.get("mime_type", "image/png")
        try:
            site, creds, base_url, verify = _wp_creds(site_key)
            if not rel_path:
                return {"error": "path required"}
            abs_path = _sandbox(site, rel_path)
            rc, img_bytes, err = _ssh(f"cat {_sx.quote(abs_path)}", timeout=20)
            if rc != 0:
                return {"error": f"ssh read failed: {err.decode()[:150]}"}
            if not img_bytes:
                return {"error": "file is empty"}
            auth_b64 = _b64.b64encode(f"kai:{site['kai_app_password']}".encode()).decode()
            import tempfile as _tf, os as _os
            ext = _pp.splitext(filename)[1] or ".png"
            with _tf.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
                tmp.write(img_bytes)
                tmp_path = tmp.name
            try:
                import subprocess as _sp2
                result = _sp2.run([
                    "curl", "-s", "-k", "-w", "\n%{http_code}",
                    "-H", f"Authorization: Basic {auth_b64}",
                    "-H", f'Content-Disposition: attachment; filename="{filename}"',
                    "-H", f"Content-Type: {mime}",
                    "--data-binary", f"@{tmp_path}",
                    f"{base_url}/wp-json/wp/v2/media"
                ], capture_output=True, text=True, timeout=30)
                lines = result.stdout.strip().split("\n")
                status = int(lines[-1]) if lines[-1].isdigit() else 0
                body = json.loads("\n".join(lines[:-1]))
                if status in (200, 201):
                    return {"ok": True, "id": body.get("id"), "url": body.get("source_url"), "site": site_key}
                return {"error": f"WP media HTTP {status}", "body": str(body)[:200]}
            finally:
                _os.unlink(tmp_path)
        except Exception as e:
            logger.exception("wordpress_upload_media: %s", e)
            return {"error": str(e)}

    if tool_name == "wordpress_update_settings":
        _task_id = ti.get("task_id")
        if not _task_id:
            return {"blocked": "task_id is required. Call wordpress_create_task first to start a tracked build. Every WP build must be tracked."}
        site_key = ti.get("site", "leodaiuto")
        settings = {k: v for k, v in ti.items() if k != "site"}
        try:
            site, creds, base_url, verify = _wp_creds(site_key)
            r = httpx.post(f"{base_url}/wp-json/wp/v2/settings",
                json=settings, headers=_hdrs(creds), follow_redirects=True, timeout=20, verify=verify)
            if r.status_code not in (200, 201):
                return {"error": f"WP returned {r.status_code}", "body": r.text[:300]}
            return {"updated": True, "site": site_key, "settings": r.json()}
        except Exception as e:
            logger.exception("wordpress_update_settings: %s", e)
            return {"error": str(e)}

    if tool_name == "wordpress_set_option":
        _task_id = ti.get("task_id")
        if not _task_id:
            return {"blocked": "task_id is required. Call wordpress_create_task first to start a tracked build. Every WP build must be tracked."}
        site_key = ti.get("site")
        option_name = ti.get("option_name", "")
        option_value = str(ti.get("option_value", ""))
        try:
            site, _, _, _ = _wp_creds(site_key)
            webroot = _site_root(site)
            import shlex as _sx2
            cmd = "wp option update " + _sx2.quote(option_name) + " " + _sx2.quote(option_value) + " --path=" + _sx2.quote(webroot) + " --allow-root"
            rc, out, err = _ssh(cmd, timeout=20)
            if rc != 0:
                return {"error": "wp-cli failed: " + err.decode()[:200], "stdout": out.decode()[:100]}
            return {"ok": True, "site": site_key, "option": option_name, "value": option_value, "output": out.decode().strip()}
        except Exception as e:
            logger.exception("wordpress_set_option: %s", e)
            return {"error": str(e)}

    if tool_name == "wordpress_get_page_content":
        _task_id = ti.get("task_id")
        if not _task_id:
            return {"blocked": "task_id is required. Call wordpress_create_task first to start a tracked build. Every WP build must be tracked."}
        site_key = ti.get("site")
        page_id = ti.get("page_id")
        try:
            site, creds, base_url, verify = _wp_creds(site_key)
            r = httpx.get(f"{base_url}/wp-json/wp/v2/pages/{page_id}",
                params={"context": "edit"},
                headers=_hdrs(creds), follow_redirects=True, timeout=20, verify=verify)
            if r.status_code != 200:
                return {"error": f"WP returned {r.status_code}"}
            p = r.json()
            return {
                "id": p.get("id"),
                "title": p.get("title", {}).get("raw", ""),
                "content": p.get("content", {}).get("raw", ""),
                "status": p.get("status"),
                "template": p.get("template", ""),
                "slug": p.get("slug"),
            }
        except Exception as e:
            logger.exception("wordpress_get_page_content: %s", e)
            return {"error": str(e)}

    if tool_name == "wordpress_verify_live":
        _task_id = ti.get("task_id")
        if not _task_id:
            return {"blocked": "task_id is required. Call wordpress_create_task first to start a tracked build. Every WP build must be tracked."}
        site_key = ti.get("site")
        url_path = ti.get("url_path", "/")
        marker = ti.get("marker", "")
        try:
            import re as _re2
            site, _, _, _ = _wp_creds(site_key)
            domain = site["url"].replace("https://", "").replace("http://", "").rstrip("/")
            resolve_arg = domain + ":443:134.209.166.23"
            r = _sp.run(
                ["curl", "-s", "-k", "-w", "\n%{http_code}",
                 "--resolve", resolve_arg,
                 "https://" + domain + url_path, "--max-time", "15"],
                capture_output=True, text=True, timeout=20)
            lines = r.stdout.split("\n")
            http_code = lines[-1].strip() if lines else "0"
            html = "\n".join(lines[:-1])
            title_m = _re2.search(r"<title>(.*?)</title>", html, _re2.IGNORECASE)
            return {
                "site": site_key,
                "url": "https://" + domain + url_path,
                "http_status": int(http_code) if http_code.isdigit() else 0,
                "title": title_m.group(1) if title_m else None,
                "marker_found": bool(marker and marker in html),
                "coming_soon_active": bool(_re2.search(r"Coming Soon", html)),
                "html_preview": html[:600],
            }
        except Exception as e:
            logger.exception("wordpress_verify_live: %s", e)
            return {"error": str(e)}

    if tool_name == "wordpress_request_council":
        return {"deprecated": True,
                "message": "wordpress_request_council is retired. Council gates are now orchestrator-native.",
                "migrated_at": "2026-05-17"}

    if tool_name == "wordpress_council_review":
        return {"deprecated": True,
                "message": "wordpress_council_review is retired. Council gates are now orchestrator-native.",
                "migrated_at": "2026-05-17"}

    if tool_name == "wordpress_review_feedback":
        return {"ok": True, "deprecated": True}

    if tool_name == "wordpress_get_council_history":
        return {"history": [], "deprecated": True}

    if tool_name == "wordpress_get_task":
        return {"deprecated": True,
                "message": "WP task history now in kai-orchestrator /jobs/ endpoints"}

    if tool_name == "wordpress_list_tasks":
        return {"tasks": [], "count": 0, "deprecated": True,
                "message": "WP task list now in kai-orchestrator /jobs/ endpoint"}

    if tool_name == "wordpress_override":
        return {"deprecated": True,
                "message": "Use POST /jobs/{job_id}/steps/{step_id}/override on port 8004 instead"}

    if tool_name == "wordpress_audit_report":
        return {"deprecated": True,
                "message": "WP audit now in kai-orchestrator /jobs/ and /events/ endpoints"}

    return {"error": f"Unknown wordpress tool: {tool_name}"}


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
        track_api_call(advisor, provider="tavily", endpoint="search",
                       trigger_source="tool:tavily_search")
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


# ── Ops — system self-management ─────────────────────────────────────────────

def _h_ops(client, tool_name, ti, advisor):
    """System ops tools — query and act on KAI infrastructure."""
    if tool_name == "get_system_health":
        try:
            r = client.get(f"{WORKER_URL}/system/ops-state", timeout=10)
            return r.json()
        except Exception as e:
            return {"error": f"ops-state unavailable: {e}"}

    if tool_name == "run_backup_now":
        try:
            r = client.post(f"{WORKER_URL}/system/run-backup", timeout=15)
            return r.json()
        except Exception as e:
            return {"error": f"run-backup failed: {e}"}

    if tool_name == "restore_backup_cron":
        try:
            r = client.post(f"{WORKER_URL}/system/restore-cron", timeout=10)
            return r.json()
        except Exception as e:
            return {"error": f"restore-cron failed: {e}"}

    return {"error": f"Unknown ops tool: {tool_name}"}


# ── Dispatch registry ─────────────────────────────────────────────────────────


def _h_jobs(client, tool_name, ti, advisor):
    """Job engine tools: submit, status, list."""
    if tool_name == "submit_job":
        workflow = ti.get("workflow")
        inputs = ti.get("inputs", {})
        if ti.get("title"):
            inputs["title"] = ti["title"]
        if workflow:
            try:
                r = client.post(f"{_ORCH_URL}/workflows/run",
                                json={"type": workflow, "inputs": inputs},
                                timeout=30)
                return r.json()
            except Exception as e:
                return {"error": f"submit_job failed: {e}"}
        else:
            intent = ti.get("intent", "")
            try:
                r = client.post(f"{_ORCH_URL}/dispatch",
                                json={"intent": intent, "inputs": inputs},
                                timeout=30)
                return r.json()
            except Exception as e:
                return {"error": f"dispatch failed: {e}"}

    if tool_name == "get_job_status":
        job_id = ti.get("job_id", "")
        try:
            r = client.get(f"{_ORCH_URL}/jobs/{job_id}", timeout=10)
            return r.json()
        except Exception as e:
            return {"error": f"get_job_status failed: {e}"}

    if tool_name == "list_jobs":
        limit = ti.get("limit", 10)
        status = ti.get("status", "")
        try:
            params = {"limit": limit}
            if status:
                params["status"] = status
            r = client.get(f"{_ORCH_URL}/jobs", params=params, timeout=10)
            return r.json()
        except Exception as e:
            return {"error": f"list_jobs failed: {e}"}

    return {"error": f"Unknown jobs tool: {tool_name}"}

TOOL_REGISTRY = {
    # Workflows
    "save_workflow": _h_workflows,
    "list_workflows": _h_workflows,
    "delete_workflow": _h_workflows,
    # Tasks
    "list_tasks": _h_tasks,
    "complete_task": _h_tasks,
    "create_task": _h_tasks,
    "search_tasks": _h_tasks,
    "update_task": _h_tasks,
    "delete_task": _h_tasks,
    "reschedule_task": _h_tasks,
    "list_todoist_projects": _h_tasks,
    "create_todoist_project": _h_tasks,
    "delete_todoist_project": _h_tasks,
    # Projects
    "create_project": _h_projects,
    "update_project": _h_projects,
    "list_projects": _h_projects,
    "delete_project": _h_projects,
    "setup_project": _h_projects,
    # Vault
    "write_to_vault": _h_vault,
    "read_vault": _h_vault,
    "read_workspace": _h_vault,
    "list_workspace": _h_vault,
    # Slack
    "send_slack_message": _h_slack,
    "create_slack_channel": _h_slack,
    "invite_to_slack_channel": _h_slack,
    # Asset delivery
    "deliver_asset": _h_slack,
    "get_advisor_recent_dms": _h_slack,
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
    "ingest_knowledge": _h_ingest,
    "list_knowledge": _h_ingest,
    "clear_knowledge": _h_ingest,
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
    "wordpress_list_sites": _h_wordpress,
    "wordpress_get_posts": _h_wordpress,
    "wordpress_get_pages": _h_wordpress,
    "wordpress_get_post": _h_wordpress,
    "wordpress_get_site_info": _h_wordpress,
    "wordpress_create_post": _h_wordpress,
    "wordpress_create_page": _h_wordpress,
    "wordpress_update_post": _h_wordpress,
    "wordpress_publish": _h_wordpress,
    "wordpress_set_custom_css": _h_wordpress,
    "wordpress_upload_media": _h_wordpress,
    "wordpress_create_task": _h_wordpress,
    "wordpress_complete_task": _h_wordpress,
    "wordpress_request_council": _h_wordpress,
    "wordpress_council_review": _h_wordpress,
    "wordpress_review_feedback": _h_wordpress,
    "wordpress_get_council_history": _h_wordpress,
    "wordpress_get_task": _h_wordpress,
    "wordpress_list_tasks": _h_wordpress,
    "wordpress_override": _h_wordpress,
    "wordpress_audit_report": _h_wordpress,
    "wordpress_read_file": _h_wordpress,
    "wordpress_write_file": _h_wordpress,
    "wordpress_list_files": _h_wordpress,
    "wordpress_delete_file": _h_wordpress,
    "wordpress_purge_varnish": _h_wordpress,
    "wordpress_update_settings": _h_wordpress,
    "wordpress_set_option": _h_wordpress,
    "wordpress_get_page_content": _h_wordpress,
    "wordpress_verify_live": _h_wordpress,
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
    # Orchestrator capability bridge
    "run_capability": _h_workflows,
    "list_capabilities": _h_workflows,
    # Ops — system self-management
    "get_system_health": _h_ops,
    "run_backup_now": _h_ops,
    "restore_backup_cron": _h_ops,
    # Job engine
    "submit_job": _h_jobs,
    "get_job_status": _h_jobs,
    "list_jobs": _h_jobs,
}


def execute_tool(tool_name: str, tool_input: dict, advisor: str = "kai") -> dict:
    handler = TOOL_REGISTRY.get(tool_name)
    if not handler:
        return {"error": f"Unknown tool: {tool_name}"}
    try:
        # Bug 48f85706/aec2d486: this shared client makes every council→worker
        # tool call; the worker authenticates all routes. Attach the worker
        # credential here so all worker-backed tools inherit it. The client is
        # also used for kai-orchestrator calls (no auth middleware) which simply
        # ignore the header — same internal trust domain.
        with httpx.Client(timeout=15, auth=_worker_auth()) as client:
            return handler(client, tool_name, tool_input, advisor)
    except Exception as e:
        logger.exception("execute_tool %s: %s", tool_name, e)
        return {"error": str(e)}
