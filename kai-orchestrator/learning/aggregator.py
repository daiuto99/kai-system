"""
S6-2 Events aggregator — classifies system patterns into structured JSON for
the proposal generator (S6-3).

Pattern types: wrong_gate | broken_tool | transport_failure | workflow_logic
Weekly cron: background thread fires Monday 07:00 UTC.
"""

import datetime
import json
import logging
import pathlib
import threading
import time

log = logging.getLogger(__name__)

_VAULT_LEARNING = pathlib.Path("/vault/60_Council/learning")
_VAULT_REVIEWS  = pathlib.Path("/vault/60_Council/reviews")


# ── helpers ──────────────────────────────────────────────────────────────────

def _iso_week(dt: datetime.datetime) -> str:
    return dt.strftime("%Y-W%W")


def _classify_step_error(capability: str | None, err_text: str) -> str:
    err = err_text.lower()
    cap = (capability or "").lower()
    if "no such file or directory" in err:
        return "broken_tool"
    if "ssh" in err or "purge" in cap or "transport" in cap:
        return "transport_failure"
    if "marker_not_found" in err or "verify" in cap:
        return "workflow_logic"
    if "council_notify_failed" in err or "status_code" in err:
        return "broken_tool"
    return "workflow_logic"


# ── core aggregation ─────────────────────────────────────────────────────────

def run_aggregation() -> pathlib.Path:
    """Collect and classify system patterns; write JSON to vault/60_Council/learning/."""
    import sys
    sys.path.insert(0, "/app")
    from db import get_conn

    now  = datetime.datetime.utcnow()
    week = _iso_week(now)
    _VAULT_LEARNING.mkdir(parents=True, exist_ok=True)
    output_path = _VAULT_LEARNING / f"{week}-patterns.json"

    conn = get_conn()
    raw_patterns: list[dict] = []

    try:
        # 1. Failed steps ---------------------------------------------------------
        failed = conn.execute("""
            SELECT s.name, s.capability, s.error, s.retry_count,
                   j.type AS job_type, s.created_at
            FROM steps s
            JOIN jobs j ON j.id = s.job_id
            WHERE s.status IN ('failed_permanent', 'failed_recoverable')
              AND s.error IS NOT NULL
            ORDER BY s.created_at DESC
            LIMIT 200
        """).fetchall()

        buckets: dict[tuple, list] = {}
        for row in failed:
            err_raw = row["error"] or ""
            try:
                err_obj = json.loads(err_raw)
                err_sig = err_obj.get("type", err_raw[:80])
            except Exception:
                err_sig = err_raw[:80]
            cap = row["capability"] or "unknown"
            buckets.setdefault((cap, err_sig), []).append({
                "step": row["name"],
                "job_type": row["job_type"],
                "retry_count": row["retry_count"],
                "at": row["created_at"],
            })

        for (cap, err_sig), evidence in buckets.items():
            raw_patterns.append({
                "pattern_type": _classify_step_error(cap, err_sig),
                "capability": cap,
                "error_signature": err_sig,
                "evidence_count": len(evidence),
                "evidence": evidence[:10],
            })

        # 2. Overrides (manual gate bypasses) ------------------------------------
        overrides = conn.execute("""
            SELECT step_name, reason, operator, bug_filed, created_at
            FROM overrides ORDER BY created_at DESC LIMIT 100
        """).fetchall()

        ov_buckets: dict[str, list] = {}
        for row in overrides:
            ov_buckets.setdefault(row["step_name"], []).append({
                "reason": row["reason"],
                "operator": row["operator"],
                "bug_filed": row["bug_filed"],
                "at": row["created_at"],
            })

        for step_name, evidence in ov_buckets.items():
            raw_patterns.append({
                "pattern_type": "wrong_gate",
                "capability": step_name,
                "error_signature": "manual_override",
                "evidence_count": len(evidence),
                "evidence": evidence[:10],
            })

        # 3. High-retry capabilities in workflow_metrics -------------------------
        high_retry = conn.execute("""
            SELECT capability, step_name,
                   COUNT(*) AS n,
                   SUM(retry_count) AS total_retries,
                   AVG(latency_ms) AS avg_ms
            FROM workflow_metrics
            WHERE retry_count > 0 AND capability IS NOT NULL
            GROUP BY capability
            HAVING SUM(retry_count) > 0
            ORDER BY total_retries DESC LIMIT 20
        """).fetchall()

        for row in high_retry:
            cap = row["capability"]
            ptype = "transport_failure" if "transport" in cap else "broken_tool"
            raw_patterns.append({
                "pattern_type": ptype,
                "capability": cap,
                "error_signature": "high_retry_rate",
                "evidence_count": int(row["total_retries"]),
                "evidence": [{
                    "step": row["step_name"],
                    "calls": int(row["n"]),
                    "total_retries": int(row["total_retries"]),
                    "avg_ms": round(row["avg_ms"]) if row["avg_ms"] else None,
                }],
            })

    finally:
        conn.close()

    # 4. Peer review high/critical findings ─────────────────────────────────────
    if _VAULT_REVIEWS.exists():
        for rf in sorted(_VAULT_REVIEWS.glob("*.json"))[-30:]:
            try:
                data = json.loads(rf.read_text())
                for f in data.get("findings", []):
                    if f.get("severity") in ("high", "critical"):
                        raw_patterns.append({
                            "pattern_type": "workflow_logic",
                            "capability": f"peer_review.{data.get('reviewer','unknown')}",
                            "error_signature": f.get("title", f.get("description",""))[:80],
                            "evidence_count": 1,
                            "evidence": [{
                                "topic": data.get("topic"),
                                "severity": f.get("severity"),
                                "recommendation": f.get("recommendation","")[:120],
                                "file": rf.name,
                            }],
                        })
            except Exception as exc:
                log.debug("Skipping review %s: %s", rf.name, exc)

    # 5. Gate outcomes (council gate audit files) ─────────────────────────────
    _VAULT_GATES = pathlib.Path("/vault/00_System/gates")
    if _VAULT_GATES.exists():
        for gf in sorted(_VAULT_GATES.glob("*_audit.json"), reverse=True)[:50]:
            try:
                data = json.loads(gf.read_text())
                resolution = data.get("resolution", {})
                approved   = resolution.get("approved", None)
                notes      = resolution.get("notes", "")
                gate_type  = data.get("gate_type", "unknown")
                resolver   = resolution.get("advisor", "unknown")
                # Only patterns where Leo rejected or gave substantive feedback
                if approved is False or (notes and len(notes) > 20):
                    raw_patterns.append({
                        "pattern_type": "gate_outcome",
                        "capability":   f"gate.{gate_type}",
                        "error_signature": "rejected" if not approved else "approved_with_notes",
                        "evidence_count": 1,
                        "evidence": [{
                            "gate_id":   data.get("gate_id", "")[:12],
                            "gate_type": gate_type,
                            "approved":  approved,
                            "resolver":  resolver,
                            "notes":     notes[:200],
                            "brief_summary": str(data.get("brief", {}))[:150],
                            "at":        data.get("resolution", {}).get("resolved_at", ""),
                        }],
                    })
            except Exception as exc:
                log.debug("Skipping gate %s: %s", gf.name, exc)

    # 6. Merge duplicates ────────────────────────────────────────────────────────

    merged: dict[tuple, dict] = {}
    for p in raw_patterns:
        key = (p["pattern_type"], p["capability"], p["error_signature"])
        if key in merged:
            merged[key]["evidence_count"] += p["evidence_count"]
            merged[key]["evidence"].extend(p["evidence"])
        else:
            merged[key] = dict(p)

    patterns = sorted(merged.values(), key=lambda x: x["evidence_count"], reverse=True)

    output = {
        "week": week,
        "generated_at": now.isoformat() + "Z",
        "total_patterns": len(patterns),
        "patterns": patterns,
    }
    output_path.write_text(json.dumps(output, indent=2))
    log.info("aggregator: %d patterns -> %s", len(patterns), output_path)
    return output_path


# ── weekly background loop ────────────────────────────────────────────────────

def _due(last_run: datetime.datetime | None) -> bool:
    now = datetime.datetime.utcnow()
    if now.weekday() != 0 or now.hour < 7:   # Monday 07:00+ UTC
        return False
    if last_run is None:
        return True
    return _iso_week(now) != _iso_week(last_run)


def start_learning_loop():
    """Background thread: runs aggregation weekly on Monday 07:00 UTC."""
    def _loop():
        last_run: datetime.datetime | None = None
        while True:
            try:
                if _due(last_run):
                    log.info("learning-loop: weekly aggregation triggered")
                    path = run_aggregation()
                    # S6-3: auto-generate proposals after aggregation
                    try:
                        from learning.proposer import generate_proposals
                        result = generate_proposals(path)
                        log.info("learning-loop: proposals generated — %s qualifying, slack=%s",
                                 result.get("qualifying_patterns"), result.get("slack_posted"))
                    except Exception:
                        log.exception("learning-loop: proposal generation failed")
                    last_run = datetime.datetime.utcnow()
            except Exception:
                log.exception("learning-loop aggregation error")
            time.sleep(3600)  # check every hour

    threading.Thread(target=_loop, daemon=True, name="learning-loop").start()
    log.info("S6-2 learning loop started — fires Monday 07:00 UTC")
