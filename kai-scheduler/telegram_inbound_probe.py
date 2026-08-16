#!/usr/bin/env python3
"""KAI-1111 · [M-R1] Telegram inbound round-trip probe — prove the emergency inbound path by USING it.

The KAI-1108 class of failure: a monitor asserts health at write-time while the REAL
path is dead. Zero inbound traffic exists in retained Telegram logs today, so rot on the
inbound path Leo uses from his phone is *undetectable* — the first sign of an outage would
be an unanswered message during a real emergency. This probe re-derives health from
observed round-trip behaviour, on a daily cadence:

  every day:  send a synthetic message through the REAL council-delivery core the live
              long-poll loop uses (scheduler.deliver_council_reply -> kai-council-api
              /council/message), assert a real, non-error assistant reply came back within
              a latency bound; assert Telegram outbound/token liveness via getMe; write a
              heartbeat file (the EXIT PROOF reads the green streak from it); on
              silence / bad content / dead outbound -> notify() pages Leo (audience='personal').

Why this is faithful, not theatre:
  • It calls the SAME `deliver_council_reply` the real loop calls — not a reimplementation
    that would silently drift from the live path (that drift IS the KAI-1108 trap).
  • It passes a NO-OP `send`, so the generated reply is asserted from the return value and
    NOTHING is ever delivered into a real Telegram thread — the probe is invisible to Leo
    on success (silent-notify principle). The outbound HALF is proven separately by getMe
    (token valid + api.telegram.org reachable) without emitting a user-visible message.
  • The probe TRAFFIC is synthetic, but the outage PAGE is provenance='real'. The notify
    gateway suppresses provenance='synthetic' events — a synthetic-stamped alarm would
    never reach Leo's phone and would silently re-create KAI-1108.
  • Read-only w.r.t. the system: one council request + its own heartbeat file. It never
    restarts, mutates, or provisions anything (mirrors advisor_dm_probe / fleet_heartbeat).

Honest residual (accepted — Telegram is emergency-only, and a true device round-trip is
impossible without a dedicated userbot account, which does not exist): this does NOT
exercise Telegram's own getUpdates transport nor the allowlist/routing string-parsing.
It exercises the infra-rot-prone core (council reachability, worker_auth, reply health) +
outbound token liveness. A fuller device-transport probe is a separate ticket.

Runs INSIDE the kai-scheduler container (via `docker exec` on a host cron) so it has the
council internal DNS, the bot-token secret, and the scheduler module on its path.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# Inside the kai-scheduler container: /app holds the scheduler module, /shared holds
# notify_gateway (both already on sys.path — see the image build).
sys.path.insert(0, "/app")

from scheduler import deliver_council_reply, load_secret  # the REAL council-delivery core

PROBE_CHANNEL = "kai"             # default public advisor, same as an un-prefixed Telegram DM
REQUEST_TIMEOUT_SEC = 190         # council's agentic loop can run long; scheduler uses 180s
LATENCY_WARN_MS = 60_000          # a reply slower than this is healthy-but-slow (recorded, not paged)
GETME_TIMEOUT_SEC = 10
SCHEMA = "kai.telegram_inbound_probe.v1"
STATE_PATH = Path("/vault/_telegram_inbound_probe_state.json")

# Reply strings that mean the path is broken even though a string came back. The live
# core returns "⚠️ KAI error — …" on every council failure mode and "No response." when
# the council answers with an empty body; both mean the inbound path did not truly work.
BAD_REPLY_PREFIXES = ("⚠️ kai error", "traceback (most recent call last)")
BAD_REPLY_EXACT = ("no response.", "(no reply)")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _load_prior() -> dict:
    try:
        return json.loads(STATE_PATH.read_text())
    except Exception:
        return {}


def _write_state(state: dict) -> None:
    """Atomic write so a reader never sees a half-written heartbeat."""
    tmp = STATE_PATH.with_suffix(STATE_PATH.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(STATE_PATH)


def _getme_ok(token: str) -> tuple[bool, str]:
    """Prove Telegram outbound reachability + token validity without emitting a message."""
    try:
        req = urllib.request.Request(f"https://api.telegram.org/bot{token}/getMe")
        with urllib.request.urlopen(req, timeout=GETME_TIMEOUT_SEC) as r:
            payload = json.loads(r.read())
        if payload.get("ok"):
            return True, "getMe ok"
        return False, f"getMe not ok: {json.dumps(payload)[:120]}"
    except Exception as e:
        return False, f"getMe unreachable: {type(e).__name__}: {e}"


def probe_once() -> dict:
    """Send one synthetic message through the real council-delivery core. Never raises."""
    token = load_secret("telegram_bot_token")
    if not token:
        return {"ok": False, "latency_ms": 0, "reply": None,
                "reason": "no telegram_bot_token — inbound path is disabled"}

    prompt = ("[synthetic inbound probe] Reply with a one-line acknowledgement that you are "
              "reachable. This is an automated health check of the Telegram inbound path, "
              "not a real request.")

    # NO-OP send: the reply is taken from the return value; nothing lands in a real thread.
    # chat_id is a non-routable sentinel so even a future send-path bug can't reach Leo.
    started = time.time()
    try:
        reply = deliver_council_reply(
            token, 0, PROBE_CHANNEL, prompt, "inbound_probe", [],
            send=lambda *a, **k: None,
        )
    except Exception as e:
        latency_ms = int((time.time() - started) * 1000)
        return {"ok": False, "latency_ms": latency_ms, "reply": None,
                "reason": f"council-delivery core raised: {type(e).__name__}: {e}"}
    latency_ms = int((time.time() - started) * 1000)

    reply = (reply or "").strip()
    low = reply.lower()
    if not reply:
        return {"ok": False, "latency_ms": latency_ms, "reply": reply,
                "reason": "empty reply (dead/unanswered inbound path)"}
    if low in BAD_REPLY_EXACT or low.startswith(BAD_REPLY_PREFIXES):
        return {"ok": False, "latency_ms": latency_ms, "reply": reply,
                "reason": f"error/dead sentinel in reply: {reply[:60]!r}"}

    # Inbound half is healthy — now prove Telegram outbound/token liveness.
    ok_out, out_reason = _getme_ok(token)
    if not ok_out:
        return {"ok": False, "latency_ms": latency_ms, "reply": reply,
                "reason": f"inbound ok but outbound dead: {out_reason}"}

    return {"ok": True, "latency_ms": latency_ms, "reply": reply,
            "reason": "healthy round-trip (council reply + outbound live)"}


# notify() decisions under which Leo IS (or already has been this hour) alerted about the
# outage. `delivered` = it went out now; `suppressed_dedup` = an identical page already
# reached him this hour. Anything else (send_failed / dashboard_only / suppressed_synthetic)
# means the alarm did NOT reach his phone — a silent page is itself the KAI-1108 failure and
# must read as NOT-alerted so it never masquerades as a successful escalation.
_ALERTED_DECISIONS = frozenset({"delivered", "suppressed_dedup"})


def _page_leo(result: dict, consecutive: int, dry_run: bool) -> tuple[bool, str]:
    """Page Leo about a REAL inbound-path outage. provenance='real' so the gateway does NOT
    suppress it (the KAI-1108 trap). Returns (alerted, line) where `alerted` is True ONLY if
    the page actually reached Leo (or was intentionally deduped after reaching him this hour)."""
    title = f"Telegram inbound path DOWN ({consecutive} consecutive fail)"
    body = (f"Synthetic probe through the real council-delivery core failed.\n"
            f"reason: {result['reason']}\n"
            f"latency: {result['latency_ms']} ms\n"
            f"This is the KAI-1108 failure class — the inbound path Leo uses from his phone "
            f"(Telegram = emergency-only) is likely dead.")
    if dry_run:
        return False, f"[dry-run] would page: {title}"
    try:
        # In-container the notify audit log lives on the mounted vault and is writable;
        # pin it explicitly for parity with advisor_dm_probe.
        os.environ.setdefault("KAI_NOTIFY_LOG", "/vault/00_System/notify_log.jsonl")
        from notify_gateway import notify, Event
        bucket = _now().strftime("%Y-%m-%dT%H")  # at most one page per hour during a sustained outage
        res = notify(Event(
            source="telegram_inbound_probe",
            kind="alert",
            title=title,
            body=body,
            audience="personal",   # personal-consequence -> reaches Leo's Telegram
            actionable=True,
            provenance="real",     # the outage is real even though the probe message is synthetic
            dedup_key=f"tg_inbound_down:{bucket}",
        ))
        alerted = res.decision in _ALERTED_DECISIONS
        tag = "paged" if alerted else "PAGE NOT DELIVERED"
        return alerted, (f"{tag}: decision={res.decision} dest={res.destination} "
                         f"delivered={res.delivered}")
    except Exception as e:
        return False, f"PAGE FAILED: {type(e).__name__}: {e}"


def main(argv: list[str]) -> int:
    dry_run = "--dry-run" in argv
    now = _now()

    result = probe_once()
    prior = _load_prior()
    consecutive = 0 if result["ok"] else int(prior.get("consecutive_failures", 0)) + 1
    # EXIT PROOF (KAI-1111): 3 consecutive daily greens. Machine-readable streak.
    green_streak = int(prior.get("green_streak", 0)) + 1 if result["ok"] else 0

    page_line = None
    alerted = False
    if not result["ok"]:
        alerted, page_line = _page_leo(result, consecutive, dry_run)

    state = {
        "schema": SCHEMA,
        "last_probe": _iso(now),
        "ok": result["ok"],
        "reason": result["reason"],
        "latency_ms": result["latency_ms"],
        "slow": result["ok"] and result["latency_ms"] > LATENCY_WARN_MS,
        "reply_excerpt": (result["reply"] or "")[:200],
        "consecutive_failures": consecutive,
        "green_streak": green_streak,
        "exit_proof_met": green_streak >= 3,
        "last_ok": _iso(now) if result["ok"] else prior.get("last_ok"),
        "paged": alerted,
        "dry_run": dry_run,
    }
    try:
        _write_state(state)
    except Exception as e:
        print(f"[{_iso(now)}] WARN heartbeat write failed: {e}", flush=True)

    status = "OK" if result["ok"] else "FAIL"
    line = (f"[{_iso(now)}] {status} {result['latency_ms']}ms streak={green_streak} "
            f"— {result['reason']}")
    if page_line:
        line += f" | {page_line}"
    print(line, flush=True)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
