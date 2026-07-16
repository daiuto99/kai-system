"""Read-only per-task audit composition over existing orchestrator records."""
import json
import re

from db import get_conn

_TASK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def _json(value, default):
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _row(row):
    return dict(row) if row is not None else None


def get_task_trail(task_id: str) -> dict:
    """Compose existing assembly, turn, workflow, event, and gate records by task id."""
    if not _TASK_ID.fullmatch(task_id or ""):
        raise ValueError("task_id must be 1-128 URL-safe characters")

    task_device = f"task:{task_id}"
    specialist_prefix = task_device + ":consult:"
    conn = get_conn()
    try:
        conversations = conn.execute(
            "SELECT id, advisor, device, key_tuple FROM conversations "
            "WHERE device=? OR device LIKE ? ORDER BY id",
            (task_device, specialist_prefix + "%"),
        ).fetchall()
        conversation_ids = [row["id"] for row in conversations]
        if conversation_ids:
            placeholders = ",".join("?" for _ in conversation_ids)
            assembly_rows = conn.execute(
                f"SELECT package_id, ts, conversation_id, key_tuple, tiers, budget "
                f"FROM assembly_log WHERE conversation_id IN ({placeholders}) ORDER BY ts",
                conversation_ids,
            ).fetchall()
            turn_rows = conn.execute(
                f"SELECT id, conversation_id, role, content, package_id, created_at "
                f"FROM turns WHERE conversation_id IN ({placeholders}) ORDER BY created_at",
                conversation_ids,
            ).fetchall()
        else:
            assembly_rows, turn_rows = [], []

        assembly = []
        for row in assembly_rows:
            entry = _row(row)
            entry["key_tuple"] = _json(entry["key_tuple"], [])
            entry["tiers"] = _json(entry["tiers"], {})
            entry["budget"] = _json(entry["budget"], {})
            assembly.append(entry)

        turns_by_conversation = {cid: [] for cid in conversation_ids}
        for row in turn_rows:
            turns_by_conversation.setdefault(row["conversation_id"], []).append(_row(row))

        package_by_conversation = {entry["conversation_id"]: entry["package_id"] for entry in assembly}
        main = next((row for row in conversations if row["device"] == task_device), None)
        main_turns = turns_by_conversation.get(main["id"], []) if main else []
        request = next((turn for turn in main_turns if turn["role"] == "user"), None)
        response = next((turn for turn in reversed(main_turns) if turn["role"] == "assistant"), None)

        delegations = []
        for conversation in conversations:
            device = conversation["device"]
            if not device.startswith(specialist_prefix):
                continue
            specialist = device.removeprefix(specialist_prefix)
            turns = turns_by_conversation.get(conversation["id"], [])
            question = next((turn["content"] for turn in turns if turn["role"] == "user"), None)
            answer = next((turn["content"] for turn in reversed(turns) if turn["role"] == "assistant"), None)
            delegations.append({"tool": "consult_specialist", "specialist": specialist,
                                "package_id": package_by_conversation.get(conversation["id"]),
                                "question": question, "answer": answer})

        job = conn.execute("SELECT * FROM jobs WHERE id=?", (task_id,)).fetchone()
        steps = conn.execute("SELECT * FROM steps WHERE job_id=? ORDER BY created_at", (task_id,)).fetchall()
        events = conn.execute("SELECT * FROM events WHERE job_id=? ORDER BY created_at", (task_id,)).fetchall()
        gates = conn.execute("SELECT * FROM gates WHERE job_id=? ORDER BY opened_at", (task_id,)).fetchall()
    finally:
        conn.close()

    return {"task_id": task_id,
            "source_status": {
                "assembly_log": {"available": True, "records": len(assembly)},
                "model_io_turns": {"available": True, "records": len(turn_rows)},
                "workflow_tables": {"available": True, "job_found": job is not None},
                "tool_log": {"available": False, "reason": "active council path has no durable per-invocation tool log; consult_specialist is derived from its task-scoped specialist conversation"},
            },
            "request": request, "response": response, "assembly": assembly,
            "delegations": delegations, "job": _row(job),
            "steps": [_row(row) for row in steps], "events": [_row(row) for row in events],
            "gates": [_row(row) for row in gates]}
