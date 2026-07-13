#!/usr/bin/env python3
"""Read one M0 assembly-log row by package_id from inside kai-orchestrator."""
import json
import sqlite3
import sys
import uuid

DB_URI = "file:/data/orchestrator/orchestrator.db?mode=ro"


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: m0_read_assembly_log.py <package_id>")
    try:
        package_id = str(uuid.UUID(sys.argv[1]))
    except ValueError as exc:
        raise SystemExit(f"invalid package_id: {exc}") from exc

    conn = sqlite3.connect(DB_URI, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT package_id, ts, conversation_id, key_tuple, tiers, budget, "
            "threat_scan, stable_prefix_hash, cache_breakpoint_after, "
            "cache_read_tokens, cache_creation_tokens "
            "FROM assembly_log WHERE package_id=?",
            (package_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise SystemExit(f"assembly log not found for package_id {package_id}")

    out = dict(row)
    for field in ("key_tuple", "tiers", "budget", "threat_scan"):
        if out.get(field):
            out[field] = json.loads(out[field])
    print(json.dumps(out, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
