#!/usr/bin/env python3
"""KAI-1142 · [M-R1] Relay round-trip probe — HOST wrapper (cron).

Proves Leo's ACTUAL comms transport, the leg KAI-1110 cannot see: a message posted THROUGH the
Buzz relay is picked up by the live channel-bound agent and a reply comes back THROUGH the relay.
On 2026-08-18 a real Leo DM went unanswered while every monitor stayed green because the only
advisor probe (KAI-1110) exercised the shim shortcut (:4001 -> council), not this transport.

Split, mirroring the house pattern:
  • The relay round-trip itself runs INSIDE kai-buzz (that container has websockets + coincurve +
    the probe key dir + the same CONNECT_URL the agents use) via `docker exec` — same shape as
    telegram_inbound_probe.py. It prints a JSON verdict.
  • THIS host wrapper (same host-cron context as advisor_dm_probe.py / fleet_heartbeat.py) turns
    that verdict into a meta_monitor-compatible heartbeat file and, on failure, a provenance=real
    page. notify_gateway + the host vault live here, not in the container.

Design pins (why this is not just another green check):
  • The probe TRAFFIC is synthetic, but the outage PAGE is provenance='real' — the notify gateway
    suppresses provenance='synthetic', so a synthetic-stamped alarm would silently re-create the
    exact KAI-1108/KAI-1142 blindness. (Same trap advisor_dm_probe documents.)
  • Fully isolated from Leo: a dedicated probe channel + dedicated synthetic client/responder
    identities (kaiprobe*.key), and the KAIProbe agent uses a cheap echo backend — the probe never
    lands in a real Buzz DM and spends no council tokens.
  • The heartbeat file is watched by meta_monitor.py (KAI-1115): a deleted/dead probe cannot die
    silently — its staleness pages one level up.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "shared"))  # /shared convention (notify_gateway lives here)

CONTAINER = "kai-buzz"
CLIENT_PATH = "/app/relay_roundtrip_client.py"
EXEC_TIMEOUT_SEC = 75             # generous cover for the client's own 30s round-trip bound
SCHEMA = "kai.relay_roundtrip_probe.v1"

# Same vault-either-runtime convention as advisor_dm_probe.py / fleet_heartbeat.py.
_VAULT_CANDIDATES = (Path("/home/leo/vault"), Path("/vault"))
STATE_FILENAME = "_relay_roundtrip_state.json"


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
    """Atomic write so a reader (meta_monitor) never sees a half-written heartbeat."""
    p = _state_path()
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(p)


def probe_once() -> dict:
    """Run the in-container round-trip client and normalise its verdict. Never raises."""
    started = time.time()
    try:
        proc = subprocess.run(
            ["docker", "exec", CONTAINER, "python3", CLIENT_PATH],
            capture_output=True, text=True, timeout=EXEC_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "latency_ms": int((time.time() - started) * 1000),
                "reason": f"docker exec timed out after {EXEC_TIMEOUT_SEC}s (kai-buzz wedged?)"}
    except Exception as e:
        return {"ok": False, "latency_ms": int((time.time() - started) * 1000),
                "reason": f"could not run client: {type(e).__name__}: {e}"}

    # The client prints exactly one JSON verdict line on stdout (last non-empty line).
    line = ""
    for ln in reversed((proc.stdout or "").splitlines()):
        if ln.strip():
            line = ln.strip()
            break
    if not line:
        stderr = (proc.stderr or "").strip().splitlines()
        tail = stderr[-1] if stderr else f"exit={proc.returncode}"
        return {"ok": False, "latency_ms": int((time.time() - started) * 1000),
                "reason": f"client produced no verdict ({tail})"}
    try:
        v = json.loads(line)
    except Exception:
        return {"ok": False, "latency_ms": int((time.time() - started) * 1000),
                "reason": f"unparseable client verdict: {line[:160]}"}
    return {"ok": bool(v.get("ok")),
            "latency_ms": int(v.get("latency_ms") or (time.time() - started) * 1000),
            "reason": v.get("reason", "no reason")}


def _page_leo(result: dict, consecutive: int, dry_run: bool) -> str:
    """Page Leo about a REAL comms-transport outage. provenance defaults to 'real' — do NOT
    stamp it synthetic or the gateway suppresses it (the KAI-1108/KAI-1142 trap)."""
    title = f"Leo↔KAI comms path DOWN — relay round-trip failed ({consecutive} consecutive)"
    body = (f"The real Buzz transport (relay -> channel-bound agent -> relay) did NOT round-trip.\n"
            f"reason: {result['reason']}\n"
            f"latency: {result['latency_ms']} ms\n"
            f"This is the KAI-1142 failure class — a message you send KAI can go unanswered while "
            f"shim/council probes stay green. Check kai-buzz (agent link) and buzz-relay.")
    if dry_run:
        return f"[dry-run] would page: {title}"
    try:
        import os
        # notify_gateway's audit log defaults to the container vault path; point it at the host
        # vault (same physical file as the container bind mount) before the module reads it.
        os.environ.setdefault("KAI_NOTIFY_LOG", str(_vault_dir() / "00_System" / "notify_log.jsonl"))
        from notify_gateway import notify, Event
        bucket = _now().strftime("%Y-%m-%dT%H")  # hour-bucketed dedup: persistent, not spammy
        res = notify(Event(
            source="relay_roundtrip_probe",
            kind="alert",
            title=title,
            body=body,
            audience="personal",   # personal-consequence -> reaches Leo
            actionable=True,
            provenance="real",     # the outage is real even though the probe traffic is synthetic
            dedup_key=f"relay_roundtrip_down:{bucket}",
        ))
        return f"paged: decision={res.decision} dest={res.destination} delivered={res.delivered}"
    except Exception as e:
        return f"PAGE FAILED: {type(e).__name__}: {e}"


def main(argv: list[str]) -> int:
    dry_run = "--dry-run" in argv
    now = _now()

    result = probe_once()
    prior = _load_prior()
    consecutive = 0 if result["ok"] else int(prior.get("consecutive_failures", 0)) + 1

    page_line = None
    if not result["ok"]:
        page_line = _page_leo(result, consecutive, dry_run)

    state = {
        "schema": SCHEMA,
        "last_probe": _iso(now),
        "ok": result["ok"],
        "reason": result["reason"],
        "latency_ms": result["latency_ms"],
        "consecutive_failures": consecutive,
        "last_ok": _iso(now) if result["ok"] else prior.get("last_ok"),
        "paged": bool(page_line and page_line.startswith("paged")),
        "dry_run": dry_run,
    }
    try:
        _write_state(state)
    except Exception as e:
        print(f"[{_iso(now)}] WARN heartbeat write failed: {e}", flush=True)

    status = "OK" if result["ok"] else "FAIL"
    line = f"[{_iso(now)}] {status} relay-roundtrip {result['latency_ms']}ms — {result['reason']}"
    if page_line:
        line += f" | {page_line}"
    print(line, flush=True)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
