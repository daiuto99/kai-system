#!/usr/bin/env python3
"""Capture a diagnosed incident as a structured, deduped, durable record routed
through the DevOps ownership spine (KAI-1514, Leo-directed 2026-09-28).

Replaces ad-hoc hand-written prose tickets. An incident becomes a contract-validated
STRUCTURAL Finding with a full, untruncated schema, deduped by dedup_key, so
"why did X fail on <date>" is answerable from the record.

Usage:
  python3 scripts/capture_incident.py incident.json
  cat incident.json | python3 scripts/capture_incident.py -

Incident schema (JSON):
  domain        subsystem, e.g. "comms" | "storage" | "data"          (required-ish)
  check         specific area, e.g. "answer-path"                      (required-ish)
  severity      "warn" | "crit"
  summary       one-line title
  symptom       what the observer/Leo saw
  evidence      list | dict | str — the durable evidence (logs, commands, readings)
  root_cause    verified cause, or omit/blank -> stamped not-yet-diagnosed
  fix           what was done / proposed
  verification  how it was verified (or "pending")
  status        "open" | "fixed" | "mitigated" | "monitoring"
  source        "real-use" | "forensic" | "monitor" | "leo-report"
  dedup_key     stable key (idempotent refresh on re-capture)
  tickets       optional list of related KAI-#### refs
  commits       optional list of commit hashes
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "shared"))
from devops_ownership import capture_incident  # noqa: E402


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: capture_incident.py <incident.json | ->", file=sys.stderr)
        return 2
    raw = sys.stdin.read() if sys.argv[1] == "-" else Path(sys.argv[1]).read_text()
    inc = json.loads(raw)
    rec = capture_incident(inc)
    print(json.dumps(rec, indent=2, default=str))
    return 0 if rec.get("handled") else 1


if __name__ == "__main__":
    sys.exit(main())
