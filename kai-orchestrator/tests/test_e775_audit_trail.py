import json
import sqlite3

import audit_trail


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE conversations (id TEXT PRIMARY KEY, advisor TEXT, device TEXT, key_tuple TEXT);
        CREATE TABLE assembly_log (package_id TEXT PRIMARY KEY, ts TEXT, conversation_id TEXT,
            key_tuple TEXT, tiers TEXT, budget TEXT);
        CREATE TABLE turns (id TEXT PRIMARY KEY, conversation_id TEXT, role TEXT, content TEXT,
            package_id TEXT, created_at TEXT);
        CREATE TABLE jobs (id TEXT PRIMARY KEY, type TEXT, inputs TEXT, status TEXT,
            current_step TEXT, approval_policy TEXT, artifacts TEXT, error_summary TEXT,
            created_at TEXT, updated_at TEXT);
        CREATE TABLE steps (id TEXT PRIMARY KEY, job_id TEXT, name TEXT, capability TEXT, input TEXT,
            status TEXT, started_at TEXT, completed_at TEXT, result TEXT, verification TEXT,
            retry_count INTEGER, error TEXT, created_at TEXT, updated_at TEXT);
        CREATE TABLE events (id TEXT PRIMARY KEY, job_id TEXT, step_id TEXT, type TEXT, payload TEXT,
            created_at TEXT);
        CREATE TABLE gates (id TEXT PRIMARY KEY, job_id TEXT, step_id TEXT, gate_type TEXT, brief TEXT,
            callback_url TEXT, status TEXT, resolution TEXT, opened_at TEXT, resolved_at TEXT);
    """)
    return conn


def test_task_trail_composes_existing_turn_assembly_and_workflow_sources(monkeypatch):
    conn = _conn()
    task_id = "audit-demo-1"
    main_key = json.dumps(["kai", f"task:{task_id}", None, None])
    consult_key = json.dumps(["architect", f"task:{task_id}:consult:architect", None, None])
    tiers = json.dumps({"t3": {"hits": []}, "t4": {"facts": ["seeded-fact"]}, "t5": {"blocks": ["persona"]}})
    conn.executemany("INSERT INTO conversations VALUES (?,?,?,?)", [
        ("c-main", "kai", f"task:{task_id}", main_key),
        ("c-consult", "architect", f"task:{task_id}:consult:architect", consult_key),
    ])
    conn.executemany("INSERT INTO assembly_log VALUES (?,?,?,?,?,?)", [
        ("pkg-main", "2026-07-16T19:00:00Z", "c-main", main_key, tiers, "{}"),
        ("pkg-consult", "2026-07-16T19:00:01Z", "c-consult", consult_key, tiers, "{}"),
    ])
    conn.executemany("INSERT INTO turns VALUES (?,?,?,?,?,?)", [
        ("t1", "c-main", "user", "Cross-domain request", "pkg-main", "2026-07-16T19:00:00Z"),
        ("t2", "c-main", "assistant", "KAI answer", "pkg-main", "2026-07-16T19:00:02Z"),
        ("t3", "c-consult", "user", "Review architecture", "pkg-consult", "2026-07-16T19:00:01Z"),
        ("t4", "c-consult", "assistant", "Architect answer", "pkg-consult", "2026-07-16T19:00:02Z"),
    ])
    conn.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?)", (task_id, "demo", "{}", "succeeded", None, "auto", None, None, "t", "t"))
    conn.execute("INSERT INTO gates VALUES (?,?,?,?,?,?,?,?,?,?)", ("g1", task_id, "s1", "dev", "review", "callback", "approved", "ok", "t", "t"))
    monkeypatch.setattr(audit_trail, "get_conn", lambda: conn)

    trail = audit_trail.get_task_trail(task_id)

    assert trail["request"]["content"] == "Cross-domain request"
    assert trail["delegations"] == [{"tool": "consult_specialist", "specialist": "architect", "package_id": "pkg-consult", "question": "Review architecture", "answer": "Architect answer"}]
    assert trail["assembly"][1]["tiers"]["t4"]["facts"] == ["seeded-fact"]
    assert trail["gates"][0]["id"] == "g1"
    assert trail["source_status"]["tool_log"]["available"] is False
