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


def print_sops():
    projects = get_projects()
    print("\n=== ACTIVE SOPs ===")
    found = False
    sop_count = 0
    for p in projects:
        state_map = get_state_map(p["id"])
        for i in get_issues(p["id"]):
            if i.get("name","").startswith("[SOP]"):
                s = state_map.get(i.get("state",""), {})
                if s.get("group") not in ("completed", "cancelled"):
                    print(f"  {i['name']}")
                    desc = i.get("description_stripped","")
                    if desc:
                        for line in desc.strip().split("\n")[:8]:
                            if line.strip():
                                print(f"    {line.strip()}")
                    found = True
                    sop_count += 1
    if not found:
        print("  (none)")
    print("===================")
    return sop_count


def reconcile_state_of_union():
    """Check whether SOTU SESSION BRIEF is consistent with the latest vault session.

    Returns 'reconciled' if drift was detected and reported, 'state_ok' otherwise.
    Also prints the result to stdout as before.
    # Fix 2026-05-19: refactored to return a status string so warmboot manifest
    # can record the reconciliation outcome without capturing stdout.
    # See: ~/sonicink/docs/specs/warmboot-surgical-additions.md §3.3
    """
    import glob, os, re as _re
    session_dir = Path("/home/leo/vault/60_Council/sessions/kai")
    sou_path = Path("/home/leo/vault/70_Knowledge/01_Operations/StateOfTheUnion.md")
    if not session_dir.exists() or not sou_path.exists():
        return "state_ok"
    sessions = sorted(session_dir.glob("*.md"), key=os.path.getmtime, reverse=True)
    if not sessions:
        return "state_ok"
    latest = sessions[0].read_text(errors="replace")
    title_match = _re.search(r"\*\*Title:\*\*\s*(.+)", latest)
    vault_title = title_match.group(1).strip() if title_match else ""
    sou_text = sou_path.read_text()
    brief_match = _re.search(r"## SESSION BRIEF\n(.+)", sou_text)
    brief_line = brief_match.group(1).strip() if brief_match else ""
    sprint_in_vault = _re.search(r"Sprint\s+(\d+)", vault_title)
    sprint_next_in_brief = _re.search(r"Sprint\s+(\d+)[^.]*next", brief_line, _re.IGNORECASE)
    if sprint_in_vault and sprint_next_in_brief and sprint_in_vault.group(1) == sprint_next_in_brief.group(1):
        print(f"[RECONCILED] StateOfTheUnion.md SESSION BRIEF stale — vault confirms Sprint {sprint_in_vault.group(1)} complete. Brief said: {brief_line}")
        return "reconciled"
    else:
        print(f"[STATE OK] Brief and vault consistent.")
        return "state_ok"


def _write_warmboot_manifest(manifest):
    """Write the warmboot manifest to vault.

    Parallel in shape to the close manifest (session_close_log.json), this file
    enables next-session verification of what warmboot loaded, surfaced via
    /session/brief as the authoritative source for welcome block field [4].
    See: ~/sonicink/docs/specs/warmboot-surgical-additions.md §3.3
    """
    manifest_path = Path("/home/leo/vault/00_System/session_warmboot_log.json")
    try:
        manifest_path.write_text(json.dumps(manifest, indent=2))
    except Exception as e:
        print(f"[WARN] Could not write warmboot manifest: {e}")


def warmboot():
    import datetime as _dt
    TODAY = _dt.date.today().isoformat()
    NOW = _dt.datetime.now().replace(microsecond=0).isoformat()

    # Accumulate manifest data as warmboot progresses
    wb = {
        "schema_version": "1.0",
        "date": TODAY,
        "timestamp": NOW,
        "last_close": None,
        "containers": [],
        "containers_down": [],
        "plane": {"projects": 0, "open_issues": 0, "by_project": []},
        "reconciliation": "state_ok",
        "active_sops": 0,
        "overall": "ok",
    }
    partial = False

    # 1. Read last close manifest
    manifest_path = Path("/home/leo/vault/00_System/session_close_log.json")
    print("\n=== LAST CLOSE MANIFEST ===")
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text())
            m_date = manifest.get("date", "unknown")
            m_title = manifest.get("session_title", "?")
            m_steps = manifest.get("steps", [])
            failed = [s["label"] for s in m_steps if s.get("status") == "fail"]
            ok_count = sum(1 for s in m_steps if s.get("status") == "ok")
            stale_tag = f" [STALE — was {m_date}]" if m_date != TODAY else " [TODAY]"
            print(f"  Date:    {m_date}{stale_tag}")
            print(f"  Session: {m_title}")
            print(f"  Steps:   {ok_count}/{len(m_steps)} OK")
            if failed:
                print(f"  FAILED:  {', '.join(failed)}")
                print("  !! Action required — these steps were not verified at close")
                partial = True
            else:
                print("  All close steps verified.")
            # Compute stale: gap > 1 day is the signal
            try:
                close_dt = _dt.date.fromisoformat(m_date)
                close_stale = (_dt.date.today() - close_dt).days > 1
            except Exception:
                close_stale = True
            wb["last_close"] = {
                "date": m_date,
                "overall": manifest.get("overall", "unknown"),
                "failed_steps": failed,
                "stale": close_stale,
            }
            if manifest.get("overall") != "ok":
                partial = True
        except Exception as e:
            print(f"  [WARN] Could not read manifest: {e}")
            partial = True
    else:
        print("  [WARN] No manifest found — first session or close engine not yet run")

    # 2. Container health (HTTP checks)
    print("\n=== CONTAINER HEALTH ===")
    services = [
        ("kai-worker-api",  "http://localhost:8001/health"),
        ("kai-council-api", "http://localhost:8002/health"),
        ("kai-mcp-api",     "http://localhost:8003/health"),
        ("kai-litellm",     "http://localhost:4000/health/liveliness"),
    ]
    for name, url in services:
        try:
            with ur.urlopen(url, timeout=3) as r:
                status = "UP" if r.status == 200 else f"HTTP {r.status}"
        except Exception as ex:
            status = f"DOWN ({type(ex).__name__})"
        mark = "✓" if status == "UP" else "✗"
        print(f"  {mark} {name:<20} {status}")
        wb["containers"].append({"name": name, "status": status})
        if status != "UP":
            wb["containers_down"].append(name)
            partial = True

    # 3. Reconcile SOTU — now returns status string
    reconcile_result = reconcile_state_of_union()
    wb["reconciliation"] = reconcile_result
    if reconcile_result == "reconciled":
        partial = True

    # 4. Plane open issues
    try:
        projects = get_projects()
        print(f"\n=== KAI FACTORY WARMBOOT — {WS} | {len(projects)} projects ===")
        total_open = 0
        by_project = []
        for p in projects:
            state_map = get_state_map(p["id"])
            completed_groups = {"completed", "cancelled"}
            issues = get_issues(p["id"])
            open_issues = [i for i in issues if state_map.get(i.get("state", ""), {}).get("group") not in completed_groups]
            if open_issues:
                print(f"\n[{p['identifier']}] {p['name']} — {len(open_issues)} open")
                for i in open_issues:
                    s = state_map.get(i.get("state", ""), {})
                    state_name = s.get("name", "?")
                    print(f"  • [{state_name:11s}] {i['name'][:56]}")
                    print(f"    {i['id']}")
            total_open += len(open_issues)
            by_project.append({"identifier": p["identifier"], "name": p["name"], "open": len(open_issues)})
        print("\n=================================================")
        wb["plane"]["projects"] = len(projects)
        wb["plane"]["open_issues"] = total_open
        wb["plane"]["by_project"] = by_project
    except Exception as e:
        print(f"[WARN] Plane API unreachable: {e}")
        wb["overall"] = "fail"
        _write_warmboot_manifest(wb)
        sop_count = print_sops()
        wb["active_sops"] = sop_count
        return

    sop_count = print_sops()
    wb["active_sops"] = sop_count

    if wb["overall"] != "fail":
        wb["overall"] = "partial" if partial else "ok"

    _write_warmboot_manifest(wb)


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
