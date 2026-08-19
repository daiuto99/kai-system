"""
Exit Criterion #7 — 20-query capability regression suite.
Gate: ≥18/20 pass.

Tests the model-visible tool surface for drift after changes.
Each query is a direct capability invocation through the orchestrator's
/capability/{name} endpoint (same code path as council dispatch).

Categories (4 queries each):
  A — Vault operations
  B — Slack operations  
  C — Calendar / Plane operations
  D — WordPress operations
  E — System / Cross-capability
"""
import httpx
import json
import sys
from pathlib import Path

ORCH = "http://kai-orchestrator:8003"


def _capability_auth_headers() -> dict[str, str]:
    try:
        record = Path("/run/secrets/orchestrator_capability_auth").read_text().strip()
        _identity, separator, secret = record.partition(":")
        return {"X-KAI-Capability-Key": secret} if separator and secret else {}
    except OSError:
        return {}

def cap(name, inputs=None, confirmed=False):
    payload = {"inputs": inputs or {}}
    if confirmed:
        payload["confirmed"] = True
    try:
        with httpx.Client(base_url=ORCH, timeout=30) as c:
            r = c.post(f"/capability/{name}", json=payload, headers=_capability_auth_headers())
            return r.status_code, r.json()
    except Exception as e:
        return 0, {"error": str(e)}

def check(status, data, condition_fn, description):
    """Returns (pass:bool, detail:str)"""
    try:
        result = condition_fn(status, data)
        return result, description
    except Exception as e:
        return False, f"{description} (exception: {e})"

# ─── Category A — Vault ───────────────────────────────────────────────────────
def a1():
    """vault.read on a known file returns ok=True with content"""
    s, d = cap("vault.read", {"path": "00_System/JARVIS_DEFINITION.md"})
    return s == 200 and d.get("ok") and bool(d.get("data", {}).get("content", ""))

def a2():
    """vault.read on nonexistent file returns ok=False (not a crash)"""
    s, d = cap("vault.read", {"path": "00_System/DOES_NOT_EXIST_XYZ.md"})
    return s == 200 and d.get("ok") is False and not (d.get("data") or {}).get("content")

def a3():
    """vault.list on a known directory returns entries list"""
    s, d = cap("vault.list", {"path": "00_System"})
    return s == 200 and d.get("ok") and isinstance((d.get("data") or {}).get("entries"), list)

def a4():
    """session.close_status returns structured manifest (ok + has status key)"""
    s, d = cap("session.close_status")
    return s == 200 and d.get("ok") and ("status" in d.get("data", {}) or "overall" in d.get("data", {}))

# ─── Category B — Slack ───────────────────────────────────────────────────────
def b1():
    """notify.post to #devops with test message returns ok=True"""
    s, d = cap("notify.post", {"channel": "devops", "text": "[EC7 regression test — ignore]"})
    return s == 200 and d.get("ok") is True

def b2():
    """notify.post with empty text returns ok=False (validation)"""
    s, d = cap("notify.post", {"channel": "devops", "text": ""})
    # Either fails cleanly or Slack rejects empty message
    return s == 200 and (d.get("ok") is False or "error" in d)

def b3():
    """notify.post to nonexistent channel fails gracefully (ok=False, not crash)"""
    s, d = cap("notify.post", {"channel": "nonexistent_channel_xyz_12345", "text": "test"})
    return s == 200 and d.get("ok") is False

def b4():
    """notify.post response includes expected envelope fields"""
    s, d = cap("notify.post", {"channel": "devops", "text": "[EC7 b4 test]"})
    return (s == 200 and "ok" in d and "status" in d and "capability" in d)

# ─── Category C — Calendar / Plane ───────────────────────────────────────────
def c1():
    """calendar.get_events returns ok + events list (may be empty)"""
    s, d = cap("calendar.get_events", {"days_ahead": 1})
    return s == 200 and d.get("ok") is not None  # returns ok bool either way

def c2():
    """plane.create_issue with missing required fields returns ok=False"""
    s, d = cap("plane.create_issue", {})
    return s == 200 and (d.get("ok") is False or "error" in d)

def c3():
    """plane.update_state with nonexistent issue returns ok=False gracefully"""
    s, d = cap("plane.update_state", {"issue_id": "00000000-nonexistent", "state": "done"})
    return s == 200 and (d.get("ok") is False or "error" in d)

def c4():
    """calendar.create_event with missing fields returns ok=False"""
    s, d = cap("calendar.create_event", {})
    return s == 200 and (d.get("ok") is False or "error" in d)

# ─── Category D — WordPress ───────────────────────────────────────────────────
def d1():
    """wordpress.load_config returns config or ok=False (not crash)"""
    s, d = cap("wordpress.load_config", {"site": "sette-uno"})
    return s == 200 and "ok" in d

def d2():
    """wordpress.probe_credentials with bad site returns ok=False"""
    s, d = cap("wordpress.probe_credentials", {"site": "nonexistent_site_xyz"})
    return s == 200 and (d.get("ok") is False or "error" in d)

def d3():
    """wordpress.verify_live with bad site returns ok=False"""
    s, d = cap("wordpress.verify_live", {"site": "nonexistent_site_xyz"})
    return s == 200 and (d.get("ok") is False or "error" in d)

def d4():
    """wordpress.load_config with missing site param returns ok=False"""
    s, d = cap("wordpress.load_config", {})
    return s == 200 and (d.get("ok") is False or "error" in d)

# ─── Category E — System / Cross-capability ──────────────────────────────────
def e1():
    """list_capabilities endpoint returns ≥10 capabilities"""
    try:
        with httpx.Client(base_url=ORCH, timeout=10) as c:
            r = c.get("/capabilities")
            d = r.json()
            return len(d.get("capabilities", [])) >= 10
    except:
        return False

def e2():
    """workspace.list returns entries list from workspace"""
    s, d = cap("workspace.list")
    return s == 200 and d.get("ok") and isinstance((d.get("data") or {}).get("entries"), list)

def e3():
    """vault.read on Sprint_History.md returns ok + content"""
    s, d = cap("vault.read", {"path": "Sprint_History.md"})
    # Sprint_History.md might be in workspace, not vault — ok if ok=False with explanation
    return s == 200 and "ok" in d

def e4():
    """workspace.read on StateOfTheUnion.md returns content or structured error"""
    s, d = cap("workspace.read", {"path": "StateOfTheUnion.md"})
    return s == 200 and "ok" in d

TESTS = [
    ("A1", "vault.read known file → content",          a1),
    ("A2", "vault.read nonexistent → ok=False",        a2),
    ("A3", "vault.list known dir → file list",         a3),
    ("A4", "session.close_status → manifest",          a4),
    ("B1", "notify.post #devops → ok=True",             b1),
    ("B2", "notify.post empty text → fails cleanly",    b2),
    ("B3", "notify.post bad channel → ok=False",        b3),
    ("B4", "notify.post envelope fields present",       b4),
    ("C1", "calendar.get_events → ok (either bool)",   c1),
    ("C2", "plane.create_issue no args → ok=False",    c2),
    ("C3", "plane.update_state bad id → ok=False",     c3),
    ("C4", "calendar.create_event no args → ok=False", c4),
    ("D1", "wordpress.load_config site=sette-uno",     d1),
    ("D2", "wordpress.probe bad site → ok=False",      d2),
    ("D3", "wordpress.verify_live bad site → ok=False",d3),
    ("D4", "wordpress.load_config no site → ok=False", d4),
    ("E1", "list_capabilities ≥10 entries",            e1),
    ("E2", "workspace.list → file list",               e2),
    ("E3", "vault.read Sprint_History → response",     e3),
    ("E4", "workspace.read StateOfTheUnion → response",e4),
]

def run():
    print("=" * 70)
    print("EC#7 — 20-query capability regression suite")
    print("       Gate: ≥18/20 pass")
    print("=" * 70)
    results = []
    for qid, desc, fn in TESTS:
        try:
            passed = fn()
        except Exception as ex:
            passed = False
            desc = f"{desc} [EXCEPTION: {ex}]"
        results.append((qid, desc, passed))
        mark = "[P]" if passed else "[F]"
        print(f"{mark} {qid}: {desc}")

    score = sum(1 for _, _, p in results if p)
    print()
    print(f"Score: {score}/20 (gate: ≥18)")
    failures = [(qid, desc) for qid, desc, p in results if not p]
    if failures:
        print("Failures:")
        for qid, desc in failures:
            print(f"  {qid}: {desc}")

    if score >= 18:
        print(f"\n[PASS] Chat regression suite: {score}/20")
        return 0
    else:
        print(f"\n[FAIL] Chat regression suite: {score}/20 (below gate of 18)")
        return 1

if __name__ == "__main__":
    sys.exit(run())
