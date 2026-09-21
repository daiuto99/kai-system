#!/usr/bin/env python3
"""KAI-1476 · [P0] Buzz error-stream watchdog — the net for the whole silent-failure class.

The 2026-09-20 incident's root cause was not one bug — it was that NOTHING watched Buzz's
own error output. All Buzz monitoring proved synthetic liveness: relay_roundtrip echoes a
nonce through an isolated KAIProbe echo channel; buzz_shim_watchdog lists /v1/models. So
two real, user-affecting faults ran with ZERO pages:
  • buzz-relay storage sweep threw NoSuchBucket every 5 min for 8 days (media broken)
  • kai-buzz approvals poller threw 502 + DNS failures for ~24 min (14:36-15:00)
Worse, the two existing watchdogs page audience=dashboard(silent)/personal(Telegram) —
never audience=devops — so even their pages miss the #devops channel Leo actually watches.

This watchdog tails the ERROR output of both Buzz containers on the worker HOST every 2
min and pages #devops (audience='devops', provenance='real') on any qualifying error,
deduped per error-signature per hour so a persistent fault pages once/hour, not every run.
It is the general safety net: the bucket self-heal (KAI-1478) fixes ONE known fault; this
catches the next unknown one. Read-only — it never mutates a container; it only reads logs
and enqueues a page. Heartbeat watched by meta_monitor (KAI-1115).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "shared"))  # notify_gateway lives here

CONTAINERS = ["kai-buzz", "buzz-relay"]
WINDOW = "3m"                 # log lookback per run; cron cadence 2m -> slight overlap, no gaps
MAX_SIGNATURES_PER_RUN = 6   # cap distinct pages per run so a storm can't flood #devops
SCHEMA = "kai.buzz_error_watchdog.v1"

# Lines that are ERROR-shaped but are known-benign noise — never page on these.
_BENIGN = (
    "KAIProbe",
    "__relay_probe",
    "probe_ack",
    "WebSocket connection closed",   # INFO-level relay churn (probe connects/disconnects)
)

# What counts as an error line across the two log dialects:
#   buzz-relay -> structured JSON with "level":"ERROR"
#   kai-buzz   -> plaintext, approvals faults prefixed "!!"; tracebacks; bare ERROR/Exception
_ERROR_MARKERS = ('"level":"error"', "!! ", "traceback", "exception", "critical")
_ERROR_WORD = re.compile(r"\berror\b", re.IGNORECASE)

_VAULT_CANDIDATES = (Path("/home/leo/vault"), Path("/vault"))
STATE_FILENAME = "_buzz_error_watchdog_state.json"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _vault_dir() -> Path:
    for c in _VAULT_CANDIDATES:
        if c.exists():
            return c
    return _VAULT_CANDIDATES[0]


def _state_path() -> Path:
    return _vault_dir() / STATE_FILENAME


def _load_prior() -> dict:
    try:
        return json.loads(_state_path().read_text())
    except Exception:
        return {}


def _write_state(state: dict) -> None:
    p = _state_path()
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(p)


def _is_error(line: str) -> bool:
    low = line.lower()
    if any(b.lower() in low for b in _BENIGN):
        return False
    if any(m in low for m in _ERROR_MARKERS):
        return True
    # bare "ERROR" as a standalone word (log-level column), not substrings like "no error"
    return bool(_ERROR_WORD.search(line)) and "no round-trip" not in low


def _message_of(line: str) -> str:
    """Extract the human message from either dialect for signature grouping."""
    s = line.strip()
    if s.startswith("{"):
        try:
            d = json.loads(s)
            return str(d.get("message") or d.get("target") or s)
        except Exception:
            pass
    if "!! " in s:
        return s.split("!! ", 1)[1]
    return s


def _signature(container: str, line: str) -> str:
    """Normalise a message so the same recurring fault collapses to one signature:
    strip digits, hex ids, quoted request ids, whitespace -> stable hash."""
    msg = _message_of(line).lower()
    msg = re.sub(r"0x[0-9a-f]+", "", msg)
    msg = re.sub(r"[0-9a-f]{8,}", "", msg)   # request ids / hashes
    msg = re.sub(r"\d+", "", msg)            # counters, latencies, ports
    msg = re.sub(r"\s+", " ", msg).strip()
    return hashlib.sha1(f"{container}:{msg[:200]}".encode()).hexdigest()[:12]


def scan_container(container: str) -> dict:
    """Return {signature: {"container","count","sample"}} of error lines in the window."""
    try:
        p = subprocess.run(["docker", "logs", "--since", WINDOW, container],
                           capture_output=True, text=True, timeout=30)
    except Exception as e:
        # A failure to even READ the logs is itself worth surfacing as a synthetic signature.
        sig = _signature(container, f"logscan failed {type(e).__name__}")
        return {sig: {"container": container, "count": 1,
                      "sample": f"[watchdog] could not read logs: {type(e).__name__}: {e}"}}
    found: dict = {}
    for line in (p.stdout or "").splitlines() + (p.stderr or "").splitlines():
        if not line.strip() or not _is_error(line):
            continue
        sig = _signature(container, line)
        if sig not in found:
            found[sig] = {"container": container, "count": 0, "sample": line.strip()[:300]}
        found[sig]["count"] += 1
    return found


def _page(container: str, count: int, sample: str, sig: str, dry_run: bool) -> str:
    title = f"Buzz error stream — {container} logging errors ({count} in {WINDOW})"
    body = (f"{container} emitted {count} error line(s) in the last {WINDOW}. Sample:\n"
            f"{sample}\n\n"
            f"This is the KAI-1476 class — a real Buzz error the synthetic probes cannot see. "
            f"Investigate {container}; if it is a known self-heal (bucket/shim) the matching "
            f"watchdog should also have fired.")
    if dry_run:
        return f"[dry-run] would page #devops: {title}"
    try:
        vault = _vault_dir()
        os.environ.setdefault("KAI_NOTIFY_LOG", str(vault / "00_System" / "notify_log.jsonl"))
        os.environ.setdefault("KAI_NOTIFY_DEDUP", str(vault / "00_System" / "notify_dedup.json"))
        os.environ.setdefault("KAI_DEVOPS_QUEUE", str(vault / "00_System" / "devops_queue.jsonl"))
        from notify_gateway import notify, Event
        bucket = _now().strftime("%Y-%m-%dT%H")  # one page per signature per hour
        res = notify(Event(
            source="buzz_error_watchdog",
            kind="alert",
            title=title,
            body=body,
            audience="dashboard",  # KAI-1489: DevOps activity log, never Leo. A genuinely
                                   # stuck fault is surfaced by the ownership spine, not here.
            actionable=True,
            provenance="real",
            dedup_key=f"buzz_error:{sig}:{bucket}",
        ))
        return f"paged: decision={res.decision} dest={res.destination} delivered={res.delivered}"
    except Exception as e:
        return f"PAGE FAILED: {type(e).__name__}: {e}"


def main(argv: list[str]) -> int:
    dry_run = "--dry-run" in argv
    now = _now()

    all_sigs: dict = {}
    for c in CONTAINERS:
        for sig, info in scan_container(c).items():
            if sig in all_sigs:
                all_sigs[sig]["count"] += info["count"]
            else:
                all_sigs[sig] = info

    prior = _load_prior()
    prior_keys = set(prior.get("error_signature_keys") or [])

    # KAI-1489 persistence gate: a signature seen only ONCE, in a single run, is almost
    # always a transient (e.g. a 3s 502 while a container restarts on deploy) — never
    # worth surfacing. Only route a signature onward when it RECURS: >=2 in-window, or it
    # was already present in the prior run. Kills deploy/restart blips at the source;
    # combined with the notify triage-gate, none of this can reach Leo regardless.
    pages = []
    # Most-frequent signatures first; cap per run so a storm cannot flood the channel.
    ordered = sorted(all_sigs.items(), key=lambda kv: kv[1]["count"], reverse=True)
    qualifying = [(sig, info) for sig, info in ordered
                  if info["count"] >= 2 or sig in prior_keys]
    suppressed_transient = len(ordered) - len(qualifying)
    for sig, info in qualifying[:MAX_SIGNATURES_PER_RUN]:
        pages.append(_page(info["container"], info["count"], info["sample"], sig, dry_run))
    dropped = max(0, len(qualifying) - MAX_SIGNATURES_PER_RUN)
    state = {
        "schema": SCHEMA,
        "last_check": _iso(now),
        "error_signatures": len(all_sigs),
        "error_lines_total": sum(i["count"] for i in all_sigs.values()),
        "signatures_dropped": dropped,
        "suppressed_transient": suppressed_transient,
        "error_signature_keys": sorted(all_sigs.keys()),
        "last_clean": _iso(now) if not all_sigs else prior.get("last_clean"),
        "dry_run": dry_run,
    }
    try:
        _write_state(state)
    except Exception as e:
        print(f"[{_iso(now)}] WARN heartbeat write failed: {e}", flush=True)

    if not all_sigs:
        print(f"[{_iso(now)}] OK buzz error streams clean ({', '.join(CONTAINERS)})", flush=True)
        return 0
    line = (f"[{_iso(now)}] ERRORS {len(all_sigs)} signature(s), "
            f"{state['error_lines_total']} line(s)")
    if dropped:
        line += f" ({dropped} signature(s) over cap, not paged this run)"
    if pages:
        line += " | " + " ; ".join(pages)
    print(line, flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
