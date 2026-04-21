#!/usr/bin/env python3
"""
sync_plane_state.py — KAI session warm-boot and issue sync tool.

Usage:
  python3 sync_plane_state.py warmboot                            # open issues for session context
  python3 sync_plane_state.py list                                # all projects + issues
  python3 sync_plane_state.py get <issue_id_or_name>             # fetch specific issue
  python3 sync_plane_state.py update <issue_id> <state> [notes]  # update state + append discovery

State values: backlog, todo, in progress, done, cancelled
"""

import sys, json, datetime
import urllib.request as ur
from pathlib import Path

API_TOKEN = Path("/home/leo/kai-system/secrets/plane_api_token.txt").read_text().strip().split("\n")[0]
BASE = "http://localhost:8090/api/v1"
WS = "sonicink"
HEADERS = {"X-API-Key": API_TOKEN, "Content-Type": "application/json"}


def req(method, path, body=None):
    url = f"{BASE}/workspaces/{WS}/{path}"
    data = json.dumps(body).encode() if body else None
    r = ur.Request(url, data=data, headers=HEADERS, method=method)
    with ur.urlopen(r, timeout=10) as resp:
        return json.loads(resp.read())


def get_projects():
    d = req("GET", "projects/")
    return d.get("results", d) if isinstance(d, dict) else d


def get_issues(pid):
    d = req("GET", f"projects/{pid}/issues/?per_page=100")
    return d.get("results", d) if isinstance(d, dict) else d


def get_state_map(pid):
    d = req("GET", f"projects/{pid}/states/")
    states = d.get("results", d) if isinstance(d, dict) else d
    return {s["id"]: s for s in states}


def find_issue(issue_id):
    for p in get_projects():
        for i in get_issues(p["id"]):
            if i.get("id") == issue_id or issue_id.lower() in i.get("name", "").lower():
                return p, i
    return None, None


def list_all():
    for p in get_projects():
        state_map = get_state_map(p["id"])
        print(f"\n{'='*60}\nPROJECT: {p['name']} ({p['identifier']}) — {p['id']}\n{'='*60}")
        for i in get_issues(p["id"]):
            s = state_map.get(i.get("state", ""), {})
            state_name = s.get("name", "?")
            print(f"  [{state_name:12s}] {i['name'][:56]}\n             {i['id']}")


def get_issue(issue_id):
    project, issue = find_issue(issue_id)
    if not issue:
        print(f"Not found: {issue_id}"); sys.exit(1)
    state_map = get_state_map(project["id"])
    s = state_map.get(issue.get("state", ""), {})
    print(json.dumps({
        "project": project["name"], "project_id": project["id"],
        "id": issue["id"], "name": issue["name"],
        "state": s.get("name"), "state_group": s.get("group"),
        "priority": issue.get("priority"),
        "description": issue.get("description_stripped", ""),
        "description_html": issue.get("description_html", ""),
    }, indent=2))


def update_issue(issue_id, new_state, notes=None):
    project, issue = find_issue(issue_id)
    if not issue:
        print(f"Not found: {issue_id}"); sys.exit(1)
    state_map = get_state_map(project["id"])
    states_by_name = {s["name"].lower(): sid for sid, s in state_map.items()}
    aliases = {"done": "done", "open": "todo", "in progress": "in progress", "wip": "in progress", "backlog": "backlog"}
    resolved = aliases.get(new_state.lower(), new_state.lower())
    sid = states_by_name.get(resolved)
    if not sid:
        print(f"Unknown state '{new_state}'. Options: {list(states_by_name.keys())}"); sys.exit(1)
    payload = {"state": sid}
    if notes:
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        existing = issue.get("description_html", "") or ""
        payload["description_html"] = existing + f"<p><strong>Discovery ({ts}):</strong><br>{notes}</p>"
    req("PATCH", f"projects/{project['id']}/issues/{issue['id']}/", payload)
    print(f"Updated '{issue['name']}' → {new_state}" + (" + notes" if notes else ""))


def warmboot():
    projects = get_projects()
    print(f"\n=== KAI FACTORY WARMBOOT — {WS} | {len(projects)} projects ===")
    for p in projects:
        state_map = get_state_map(p["id"])
        completed_groups = {"completed", "cancelled"}
        issues = get_issues(p["id"])
        open_issues = [i for i in issues if state_map.get(i.get("state",""), {}).get("group") not in completed_groups]
        if open_issues:
            print(f"\n[{p['identifier']}] {p['name']} — {len(open_issues)} open")
            for i in open_issues:
                s = state_map.get(i.get("state", ""), {})
                state_name = s.get("name", "?")
                print(f"  • [{state_name:11s}] {i['name'][:56]}")
                print(f"    {i['id']}")
    print("\n=================================================")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "warmboot"
    if cmd == "list":
        list_all()
    elif cmd == "get" and len(sys.argv) > 2:
        get_issue(sys.argv[2])
    elif cmd == "update" and len(sys.argv) > 3:
        update_issue(sys.argv[2], sys.argv[3], " ".join(sys.argv[4:]) if len(sys.argv) > 4 else None)
    elif cmd == "warmboot":
        warmboot()
    else:
        print(__doc__)
