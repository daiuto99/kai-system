#!/usr/bin/env python3
"""KAI-1110 · [M-R1] Advisor DM synthetic probe — prove the advisor path by USING it.

The KAI-1108 class of failure: a monitor asserts health at write-time (the shim's
`:4001/v1/models` liveness check stays green) while the REAL path — a DM landing on
the council and coming back with an answer — is dead. Leo's advisor DMs went silently
unanswered for 11 days because nothing re-derived health from observed round-trip
behavior. This probe does exactly that, on a cron cadence:

  every 15 min:  POST a synthetic DM through the REAL path
                 (kai-buzz-shim :4001 /v1/chat/completions  ->  council /council/message)
                 assert a real, non-error assistant reply came back within a latency bound
                 write a heartbeat file (the EXIT DRILL asserts this file is fresh)
                 on silence / bad content -> notify() pages Leo (audience='personal')

Design pins (why this is not just another green check):
  • The probe TRAFFIC is synthetic, but the outage PAGE is provenance='real'. The
    notify gateway suppresses provenance='synthetic' events — a synthetic-stamped
    alarm would never reach Leo's phone and would silently re-create KAI-1108.
  • The probe hits /v1/chat/completions with NO tools and NO channel context, so the
    shim returns plain text and NEVER publishes into one of Leo's real Buzz DMs
    (the publish path requires both a shell tool and a channel uuid — see the shim).
  • Read-only w.r.t. the system: it sends one chat request and writes its own heartbeat
    file. It never restarts, mutates, or provisions anything (mirrors fleet_heartbeat).

Runs on the worker HOST via cron (same as fleet_heartbeat.py). Rotates the four public
advisors across cycles so all of kai/sky/roads/coach get exercised each hour without 4x
cost; the dominant failure mode (shim or council down) is shared across all four and is
caught on the very next cycle regardless of which advisor is up.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "shared"))  # /shared convention (notify_gateway lives here)

SHIM_URL = "http://localhost:4001/v1/chat/completions"
SHIM_KEY = "buzz-eval"  # the shim ignores the key value (tailnet-gated); sent for parity with real clients
ADVISORS = ["kai", "sky", "roads", "coach"]  # public advisor models the shim serves
REQUEST_TIMEOUT_SEC = 90          # council -> litellm can be slow; beyond this = silence (a page)
LATENCY_WARN_MS = 60_000          # a reply slower than this is healthy-but-slow (recorded, not paged)
MIN_REPLY_CHARS = 8               # a real council answer is never a couple of chars
SCHEMA = "kai.advisor_probe.v1"

# Reply strings that mean the path is broken even on an HTTP 200 (the shim's own
# sentinels + generic backend-failure markers). Matched case-insensitively.
BAD_REPLY_SENTINELS = ("(no reply)", "backend error", "no response", "traceback (most recent call last)")

# Same vault-either-runtime convention as fleet_heartbeat.py.
_VAULT_CANDIDATES = (Path("/home/leo/vault"), Path("/vault"))
STATE_FILENAME = "_advisor_probe_state.json"


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
    """Atomic write so a reader never sees a half-written heartbeat."""
    p = _state_path()
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(p)


def _pick_advisor(now: datetime) -> str:
    """Deterministic rotation: one advisor per 15-min slot, full coverage each hour."""
    slot = (now.hour * 60 + now.minute) // 15
    return ADVISORS[slot % len(ADVISORS)]


def probe_once(advisor: str) -> dict:
    """Send one synthetic DM through the real path. Returns a result dict; never raises."""
    prompt = ("[synthetic advisor probe] Reply with a one-line acknowledgement that you are "
              "reachable. This is an automated health check, not a real request.")
    body = json.dumps({
        "model": advisor,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 120,
        "temperature": 0.2,
    }).encode()
    req = urllib.request.Request(SHIM_URL, data=body, method="POST", headers={
        "Authorization": f"Bearer {SHIM_KEY}",
        "Content-Type": "application/json",
    })
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SEC) as r:
            payload = json.loads(r.read())
        latency_ms = int((time.time() - started) * 1000)
    except Exception as e:
        latency_ms = int((time.time() - started) * 1000)
        return {"advisor": advisor, "ok": False, "latency_ms": latency_ms,
                "reply": None, "reason": f"no round-trip: {type(e).__name__}: {e}"}

    try:
        reply = (payload["choices"][0]["message"]["content"] or "").strip()
    except Exception:
        return {"advisor": advisor, "ok": False, "latency_ms": latency_ms,
                "reply": None, "reason": f"malformed response: {json.dumps(payload)[:200]}"}

    low = reply.lower()
    if len(reply) < MIN_REPLY_CHARS:
        return {"advisor": advisor, "ok": False, "latency_ms": latency_ms,
                "reply": reply, "reason": f"reply too short ({len(reply)} chars)"}
    for bad in BAD_REPLY_SENTINELS:
        if bad in low:
            return {"advisor": advisor, "ok": False, "latency_ms": latency_ms,
                    "reply": reply, "reason": f"error sentinel in reply: {bad!r}"}
    return {"advisor": advisor, "ok": True, "latency_ms": latency_ms,
            "reply": reply, "reason": "healthy round-trip"}


def _page_leo(result: dict, consecutive: int, dry_run: bool) -> str:
    """Page Leo about a REAL advisor-path outage. provenance defaults to 'real' — do NOT
    stamp it synthetic or the gateway suppresses it (the KAI-1108 trap)."""
    advisor = result["advisor"]
    title = f"Advisor DM path DOWN — {advisor} ({consecutive} consecutive fail)"
    body = (f"Synthetic probe through kai-buzz-shim :4001 -> council failed.\n"
            f"advisor: {advisor}\n"
            f"reason: {result['reason']}\n"
            f"latency: {result['latency_ms']} ms\n"
            f"This is the KAI-1108 failure class — Leo's real advisor DMs are likely unanswered.")
    if dry_run:
        return f"[dry-run] would page: {title}"
    try:
        # notify_gateway's audit log (Rule A) defaults to the CONTAINER vault path
        # (/vault/...), which is unwritable from this host-cron context — the send would
        # deliver but leave no audit trail. Point it at the host vault (same physical file
        # as the container's bind mount) before the module reads _LOG_PATH at import.
        import os
        os.environ.setdefault("KAI_NOTIFY_LOG", str(_vault_dir() / "00_System" / "notify_log.jsonl"))
        from notify_gateway import notify, Event
        # Hour-bucketed dedup: at most one page per advisor per hour during a sustained
        # outage (persistent but not spammy). notify() marks a key delivered only on
        # success, so a transient send failure self-heals next cycle.
        bucket = _now().strftime("%Y-%m-%dT%H")
        res = notify(Event(
            source="advisor_dm_probe",
            kind="alert",
            title=title,
            body=body,
            audience="personal",   # personal-consequence -> reaches Leo's Telegram
            actionable=True,
            provenance="real",     # explicit: the outage is real even though the probe DM is synthetic
            dedup_key=f"advisor_probe_down:{advisor}:{bucket}",
        ))
        return f"paged: decision={res.decision} dest={res.destination} delivered={res.delivered}"
    except Exception as e:
        return f"PAGE FAILED: {type(e).__name__}: {e}"


def main(argv: list[str]) -> int:
    dry_run = "--dry-run" in argv
    now = _now()
    # allow forcing a specific advisor for testing: --advisor kai
    advisor = None
    if "--advisor" in argv:
        i = argv.index("--advisor")
        if i + 1 < len(argv):
            advisor = argv[i + 1]
    advisor = advisor or _pick_advisor(now)

    result = probe_once(advisor)
    prior = _load_prior()
    consecutive = 0 if result["ok"] else int(prior.get("consecutive_failures", 0)) + 1

    page_line = None
    if not result["ok"]:
        page_line = _page_leo(result, consecutive, dry_run)

    state = {
        "schema": SCHEMA,
        "last_probe": _iso(now),
        "advisor": advisor,
        "ok": result["ok"],
        "reason": result["reason"],
        "latency_ms": result["latency_ms"],
        "slow": result["ok"] and result["latency_ms"] > LATENCY_WARN_MS,
        "reply_excerpt": (result["reply"] or "")[:200],
        "consecutive_failures": consecutive,
        "last_ok": _iso(now) if result["ok"] else prior.get("last_ok"),
        "paged": bool(page_line and page_line.startswith("paged")),
        "dry_run": dry_run,
    }
    # Never let a heartbeat-write failure swallow the probe result silently.
    try:
        _write_state(state)
    except Exception as e:
        print(f"[{_iso(now)}] WARN heartbeat write failed: {e}", flush=True)

    status = "OK" if result["ok"] else "FAIL"
    line = f"[{_iso(now)}] {status} advisor={advisor} {result['latency_ms']}ms — {result['reason']}"
    if page_line:
        line += f" | {page_line}"
    print(line, flush=True)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
