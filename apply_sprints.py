#!/usr/bin/env python3
"""Reconcile the KAI board to the adopted 6-sprint cycle plan.
Adds stage labels (project-scoped), re-tags 129 issues, creates 3 new tickets,
closes 14 stale. DRY-RUN unless --apply. Project-aware (KAI + WP)."""
import sys, json
sys.path.insert(0, "/home/leo/kai-system")
import sync_plane_state as S

APPLY = "--apply" in sys.argv
MAP = json.load(open("/home/leo/kai-system/board_map.json"))
BOARD = json.load(open("/home/leo/kai-system/board_snapshot.json"))

STAGE_LABEL = {
    "S1_STABILITY": "stage:c1-stability", "S2_SECURITY": "stage:c2-security",
    "S3_USABILITY": "stage:c3-usability", "S4_WORDPRESS": "stage:c4-wordpress",
    "S5_AUTONOMY": "stage:c5-autonomy",  "S6_PROACTIVE": "stage:c6-proactive",
    "DEFER_DESIGN": "stage:v2-design", "DEFER_FLEET": "stage:v2-fleet",
    "DEFER_RESEARCH": "stage:v2-research",
}
COLOR = {"stage:c1-stability": "#3fb98a", "stage:c2-security": "#5b8fe0",
    "stage:c3-usability": "#a982e0", "stage:c4-wordpress": "#e6a94a",
    "stage:c5-autonomy": "#35c2c9", "stage:c6-proactive": "#e0708f",
    "stage:v2-design": "#94a3b8", "stage:v2-fleet": "#94a3b8", "stage:v2-research": "#94a3b8"}
NEW_TICKETS = [
    {"name": "[C1] Architecture & capability watch — monthly executive brief with recommendations",
     "label": "stage:c1-stability", "priority": "high",
     "desc": "Track new capabilities, CVEs, tooling and dependency changes that affect KAI (positively or negatively); surface a monthly executive summary to Leo with recommendations. Tech-scout custodian feeds it."},
    {"name": "[C3] Complete dashboard redesign", "label": "stage:c3-usability", "priority": "high",
     "desc": "Full redesign of the KAI dashboard surface."},
    {"name": "[C3] Advisor knowledge & content verification — advisors use, extend, and deepen domain expertise",
     "label": "stage:c3-usability", "priority": "high",
     "desc": "Verify advisors demonstrably use their knowledge, add to it appropriately, and continually grow more knowledgeable in their domain over time."}]

projects = {p["identifier"]: p["id"] for p in S.get_projects()}
# id -> project id (KAI default; WP for WP-project issues)
id2pid = {}
for pr in BOARD["projects"]:
    pid = projects.get(pr["identifier"])
    for i in pr["issues"]:
        id2pid[i["id"]] = pid

# per-project label cache
_labels = {}   # pid -> {name: id}
def _load_labels(pid):
    if pid in _labels: return
    d = {}
    for l in S.req("GET", f"projects/{pid}/labels/").get("results", []):
        d[l["name"]] = l["id"]
    _labels[pid] = d
def label_names(pid):
    _load_labels(pid); return {v: k for k, v in _labels[pid].items()}
def ensure_label(pid, name):
    _load_labels(pid)
    if name in _labels[pid]: return _labels[pid][name]
    if not APPLY:
        _labels[pid][name] = f"DRY::{name}"; print(f"  [dry] CREATE label {name} in {pid[:8]}"); return _labels[pid][name]
    r = S.req("POST", f"projects/{pid}/labels/", {"name": name, "color": COLOR.get(name, "#94a3b8")})
    _labels[pid][name] = r["id"]; print(f"  created label {name} in {pid[:8]}"); return r["id"]

# per-project Done state
_done = {}
def done_state(pid):
    if pid not in _done:
        sm = S.get_state_map(pid)
        _done[pid] = next((sid for sid, st in sm.items() if st["name"].lower() == "done"), None)
    return _done[pid]

print(f"MODE: {'APPLY' if APPLY else 'DRY-RUN'} · projects {list(projects)}")
from collections import Counter
tag_counts, closed, created, errors = Counter(), 0, [], []

for iid, bucket in MAP.items():
    pid = id2pid.get(iid)
    if not pid:
        errors.append((iid, "no project")); continue
    try:
        if bucket == "CLOSE_STALE":
            if APPLY: S.req("PATCH", f"projects/{pid}/issues/{iid}/", {"state": done_state(pid)})
            closed += 1; continue
        target = STAGE_LABEL[bucket]
        tid = ensure_label(pid, target)
        iss = S.req("GET", f"projects/{pid}/issues/{iid}/")
        names = label_names(pid)
        keep = [lid for lid in iss.get("labels", []) if not names.get(lid, "").startswith("stage:")]
        newlabels = keep + [tid]
        if APPLY: S.req("PATCH", f"projects/{pid}/issues/{iid}/", {"labels": newlabels})
        tag_counts[target] += 1
    except Exception as e:
        errors.append((iid, str(e)[:60]))

kpid = projects["KAI"]
for t in NEW_TICKETS:
    lid = ensure_label(kpid, t["label"])
    body = {"name": t["name"], "priority": t["priority"], "description_html": f"<p>{t['desc']}</p>",
            "labels": [lid] if APPLY else []}
    if APPLY:
        try:
            r = S.req("POST", f"projects/{kpid}/issues/", body); created.append(r.get("sequence_id", r.get("id","?")))
        except Exception as e: errors.append(("NEW:"+t["name"][:30], str(e)[:60]))
    else: print(f"  [dry] CREATE {t['name'][:56]}")

print("\n=== RESULT ===")
for k in sorted(tag_counts): print(f"  {k}: {tag_counts[k]}")
print(f"  closed(Done): {closed} · created: {created if APPLY else len(NEW_TICKETS)}")
print(f"  tagged+closed: {sum(tag_counts.values())+closed} (expect 129)")
if errors: print("  ERRORS:", errors[:8], f"... {len(errors)} total")
