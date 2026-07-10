"""Memory Service — Phase 1 (docs/CONTEXT_SPEC.md §4/§5/§8/§13).

Conversation store + Tier 1 verbatim turns + Tier 2 rolling summary + assembly
log, behind the assemble()/record_turn() contract. Tiers 3-5 (semantic recall,
verified facts, standing context) are Phase 3 scope — not built here.
"""
import json
import logging
from pathlib import Path

from db import get_conn, new_id, now_iso

logger = logging.getLogger(__name__)

TIER1_MAX_TURNS = 10
TIER1_CHAR_CAP = 3000 * 4    # §6: 3,000-token ceiling, char/4 estimate (real tokenizer is §15 open Q2)
TIER2_CHAR_CAP = 400 * 4     # §6: 400-token ceiling for the rolling summary
COMPACTION_TRIGGER_TURNS = 10  # §5 Tier 2 mechanics: fold evicted turns once this many accumulate

_SLACK_TOKEN_FILE = Path("/run/wp_secrets/slack_bot_token.txt")
_INVARIANTS_FILE = Path("/vault/00_System/invariants.json")


def _key_tuple(key: dict) -> str:
    return json.dumps(
        [key.get("advisor"), key.get("device"), key.get("place"), key.get("thread")],
        sort_keys=False,
    )


def _get_or_create_conversation(conn, key: dict) -> str:
    kt = _key_tuple(key)
    row = conn.execute("SELECT id FROM conversations WHERE key_tuple=?", (kt,)).fetchone()
    if row:
        return row["id"]
    cid = new_id()
    ts = now_iso()
    conn.execute(
        "INSERT INTO conversations (id, key_tuple, advisor, device, place, thread, "
        "turns_since_compaction, summary, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (cid, kt, key.get("advisor"), key.get("device"), key.get("place"), key.get("thread"),
         0, "", ts, ts),
    )
    return cid


def _post_slack_devops(text: str) -> None:
    """Minimal standalone Slack poster — mirrors main.py's _post_slack for the
    inv_context_t1 CRITICAL alert (§8). Kept local to avoid importing main.py."""
    try:
        token = _SLACK_TOKEN_FILE.read_text().strip() if _SLACK_TOKEN_FILE.exists() else ""
        if not token:
            logger.warning("No Slack token — cannot post inv_context_t1 alert")
            return
        import httpx
        httpx.post(
            "https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {token}"},
            json={"channel": "devops", "text": text, "username": "kai-orchestrator",
                  "icon_emoji": ":rotating_light:"},
            timeout=10,
        )
    except Exception as e:
        logger.exception("_post_slack_devops failed: %s", e)


def _write_invariant_state(name: str, passed: bool, detail: str) -> None:
    """Read-merge-write into the shared invariants.json (§8) — same shape the
    scheduler writes, so Health Board / ops_state pick this up without any
    scheduler-side changes."""
    try:
        _INVARIANTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        data = {}
        if _INVARIANTS_FILE.exists():
            try:
                data = json.loads(_INVARIANTS_FILE.read_text())
            except Exception:
                data = {}
        data.setdefault("invariants", {})[name] = {
            "pass": passed, "detail": detail, "checked_at": now_iso(),
        }
        _INVARIANTS_FILE.write_text(json.dumps(data, indent=2))
    except Exception as e:
        logger.exception("_write_invariant_state(%s) failed: %s", name, e)


def assemble(key: dict, message: str, task_type: str = None, project: str = None) -> dict:
    """§4.1 assemble(). Phase 1 scope: Tier 1 (verbatim) + Tier 2 (rolling summary).
    Tiers 3-5 are not built yet — omitted from the package, not faked."""
    conn = get_conn()
    try:
        cid = _get_or_create_conversation(conn, key)
        conv = conn.execute("SELECT * FROM conversations WHERE id=?", (cid,)).fetchone()

        turns_available = conn.execute(
            "SELECT COUNT(*) FROM turns WHERE conversation_id=?", (cid,)
        ).fetchone()[0]

        rows = conn.execute(
            "SELECT role, content FROM turns WHERE conversation_id=? ORDER BY created_at DESC LIMIT ?",
            (cid, TIER1_MAX_TURNS),
        ).fetchall()
        included = list(reversed([dict(r) for r in rows]))

        # Tier 1 budget enforcement (§6): oldest dropped first, logged in the assembly log (F3).
        total_chars = sum(len(t["content"]) for t in included)
        truncated = False
        while included and total_chars > TIER1_CHAR_CAP:
            dropped = included.pop(0)
            total_chars -= len(dropped["content"])
            truncated = True

        summary = conv["summary"] or ""

        package_id = new_id()
        ts = now_iso()
        tiers = {
            "t1": {"turns_included": len(included), "turns_available": turns_available,
                   "tokens": total_chars // 4, "truncated": truncated},
            "t2": {"present": bool(summary), "tokens": len(summary) // 4,
                   "last_compaction_ts": conv["last_compaction_ts"]},
        }
        budget = {"ceiling": (TIER1_CHAR_CAP + TIER2_CHAR_CAP) // 4, "used": tiers["t1"]["tokens"] + tiers["t2"]["tokens"]}

        conn.execute(
            "INSERT INTO assembly_log (package_id, ts, conversation_id, key_tuple, tiers, budget) "
            "VALUES (?,?,?,?,?,?)",
            (package_id, ts, cid, conv["key_tuple"], json.dumps(tiers), json.dumps(budget)),
        )
        conn.commit()

        # inv_context_t1 (§8): populated store + empty T1 on this package = CRITICAL.
        if turns_available > 0 and len(included) == 0:
            detail = f"conversation {cid} (key={conv['key_tuple']}) has {turns_available} stored turns but package {package_id} assembled 0"
            logger.critical("inv_context_t1 CRITICAL: %s", detail)
            _write_invariant_state("inv_context_t1", False, detail)
            _post_slack_devops(f":rotating_light: *inv_context_t1 CRITICAL* — {detail}")
        else:
            _write_invariant_state("inv_context_t1", True,
                                    f"last checked: package {package_id}, t1.turns_included={len(included)}")

        return {
            "package_id": package_id,
            "key": key,
            "conversation_id": cid,
            "messages": [{"role": t["role"], "content": t["content"]} for t in included],
            "summary": summary,
            "budget_report": tiers,
        }
    finally:
        conn.close()


def record_turn(key: dict, role: str, content: str, package_id: str = None, turn_id: str = None) -> dict:
    """§4.1 record_turn(). Idempotent on turn_id (Telegram redelivers)."""
    conn = get_conn()
    try:
        cid = _get_or_create_conversation(conn, key)

        if turn_id:
            existing = conn.execute("SELECT id FROM turns WHERE id=?", (turn_id,)).fetchone()
            if existing:
                conn.commit()
                return {"turn_id": turn_id, "conversation_id": cid, "deduped": True}

        tid = turn_id or new_id()
        conn.execute(
            "INSERT INTO turns (id, conversation_id, role, content, package_id, created_at) VALUES (?,?,?,?,?,?)",
            (tid, cid, role, content, package_id, now_iso()),
        )
        conn.execute(
            "UPDATE conversations SET turns_since_compaction = turns_since_compaction + 1, updated_at=? WHERE id=?",
            (now_iso(), cid),
        )
        conn.commit()

        conv = conn.execute("SELECT * FROM conversations WHERE id=?", (cid,)).fetchone()
        if conv["turns_since_compaction"] >= COMPACTION_TRIGGER_TURNS:
            _compact(conn, cid)

        return {"turn_id": tid, "conversation_id": cid, "deduped": False}
    finally:
        conn.close()


def _compact(conn, conversation_id: str) -> None:
    """§5 Tier 2 mechanics: fold turns evicted past Tier 1's window into the
    rolling summary. Async in spirit (called from record_turn, off the model's
    own response path); falls back to truncation if the compactor errors."""
    conv = conn.execute("SELECT * FROM conversations WHERE id=?", (conversation_id,)).fetchone()
    rows = conn.execute(
        "SELECT role, content FROM turns WHERE conversation_id=? ORDER BY created_at ASC",
        (conversation_id,),
    ).fetchall()
    if len(rows) <= TIER1_MAX_TURNS:
        conn.execute("UPDATE conversations SET turns_since_compaction=0 WHERE id=?", (conversation_id,))
        conn.commit()
        return

    to_fold = [dict(r) for r in rows[: len(rows) - TIER1_MAX_TURNS]]
    old_summary = conv["summary"] or ""
    try:
        new_summary = _summarize_with_ollama(old_summary, to_fold)
    except Exception as e:
        logger.warning("Tier 2 compaction fallback (compactor unavailable): %s", e)
        new_summary = _summarize_fallback(old_summary, to_fold)

    conn.execute(
        "UPDATE conversations SET summary=?, turns_since_compaction=0, last_compaction_ts=? WHERE id=?",
        (new_summary[:TIER2_CHAR_CAP], now_iso(), conversation_id),
    )
    conn.commit()


def _summarize_with_ollama(old_summary: str, turns: list) -> str:
    """§8.30 fast tier — local Ollama does compaction, free and private."""
    import httpx
    transcript = "\n".join(f"{t['role']}: {t['content']}" for t in turns)
    prompt = (
        "Fold the following new turns into the running summary. Output ONE paragraph, "
        "concise, keeping concrete facts and decisions. No preamble.\n\n"
        f"Existing summary: {old_summary or '(none yet)'}\n\n"
        f"New turns:\n{transcript}\n\nUpdated summary:"
    )
    r = httpx.post(
        "http://kai-ollama:11434/api/generate",
        json={"model": "qwen2.5:3b", "prompt": prompt, "stream": False},
        timeout=30,
    )
    r.raise_for_status()
    text = (r.json().get("response") or "").strip()
    return text or _summarize_fallback(old_summary, turns)


def _summarize_fallback(old_summary: str, turns: list) -> str:
    """§5: synchronous truncation fallback when the compactor is behind/unavailable."""
    snippet = " / ".join(t["content"][:80] for t in turns[-3:])
    return (old_summary + " " + snippet).strip()[:TIER2_CHAR_CAP]


def get_conversation(key: dict, limit: int = 50) -> dict:
    """Read API for clients that want to render history (§13: dashboard swap target)."""
    conn = get_conn()
    try:
        kt = _key_tuple(key)
        row = conn.execute("SELECT id FROM conversations WHERE key_tuple=?", (kt,)).fetchone()
        if not row:
            return {"turns": []}
        rows = conn.execute(
            "SELECT role, content, created_at FROM turns WHERE conversation_id=? ORDER BY created_at DESC LIMIT ?",
            (row["id"], limit),
        ).fetchall()
        return {"turns": list(reversed([dict(r) for r in rows]))}
    finally:
        conn.close()


def check_inv_context_t1(sample: int = 50) -> dict:
    """On-demand invariant check over the last N packages — same rule assemble()
    enforces live; exposed separately so it can be polled independent of traffic."""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT package_id, conversation_id, tiers FROM assembly_log ORDER BY ts DESC LIMIT ?",
            (sample,),
        ).fetchall()
        violations = []
        for r in rows:
            tiers = json.loads(r["tiers"])
            t1 = tiers.get("t1", {})
            if t1.get("turns_available", 0) > 0 and t1.get("turns_included", 0) == 0:
                violations.append({"package_id": r["package_id"], "conversation_id": r["conversation_id"]})
        return {"ok": len(violations) == 0, "checked": len(rows), "violations": violations}
    finally:
        conn.close()


def import_legacy_history(channel: str, advisor: str, device: str, jsonl_path: Path) -> dict:
    """§13 Phase 1: one-time import of an existing `_history/{channel}.jsonl` as
    seed turns, then the JSONL is frozen read-only (not deleted, not written to
    further by this service)."""
    if not jsonl_path.exists():
        return {"ok": False, "error": "not_found"}
    key = {"advisor": advisor, "device": device, "place": None, "thread": None}
    conn = get_conn()
    try:
        cid = _get_or_create_conversation(conn, key)
        existing = conn.execute(
            "SELECT COUNT(*) FROM turns WHERE conversation_id=?", (cid,)
        ).fetchone()[0]
        if existing:
            return {"ok": False, "error": "already_seeded", "existing_turns": existing}
        imported = 0
        for line in jsonl_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            role = rec.get("role")
            content = rec.get("content")
            if role not in ("user", "assistant") or not content:
                continue
            conn.execute(
                "INSERT INTO turns (id, conversation_id, role, content, package_id, created_at) VALUES (?,?,?,?,?,?)",
                (new_id(), cid, role, content, None, rec.get("ts") or now_iso()),
            )
            imported += 1
        conn.execute("UPDATE conversations SET updated_at=? WHERE id=?", (now_iso(), cid))
        conn.commit()
        return {"ok": True, "conversation_id": cid, "imported": imported}
    finally:
        conn.close()
