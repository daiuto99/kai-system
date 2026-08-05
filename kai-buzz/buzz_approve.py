#!/usr/bin/env python3
"""Buzz-primary gate approvals (b19bf598).

Sends KAI's pending_leo approval prompts to a dedicated PRIVATE Buzz channel and
resolves each gate from Leo's reply — accepting approve/reject ONLY from Leo's
pinned Nostr identity, with a local BIP340 schnorr signature check (defense in
depth on top of the relay's NIP-42 auth + members-only channel enforcement).

UX: Leo replies with a bare verb — `approve` / `yes` / `reject: reason` / `no`.
The gate is bound from (1) an explicit id if given, else (2) the reply's e-tag
(reply to the prompt), else (3) the single open prompt. No id-typing required.

Decoupled by design: a spike-side HOST process (sibling of buzz_provision.py), NOT
baked into the deployed orchestrator. Telegram stays the live approval surface
until Buzz is formally adopted (flip: start this + set GATE_APPROVAL_SURFACE=buzz).

Run:  python3 buzz_approve.py             # poll pending gates + listen for Leo
      python3 buzz_approve.py --selftest  # offline crypto/parse self-test, no network
"""
import asyncio, json, re, time, base64, hashlib, urllib.request, urllib.error, sys, os
# Deps (websockets, coincurve) live under agent/libs — the watchdog exports PYTHONPATH
# to it, but insert it here too so this runs standalone (selftest, manual, adoption flip).
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "libs"))
import websockets
from coincurve import PublicKeyXOnly
import agents_bridge as ab

KEY_FILE   = "kai.key"                     # KAI owns the approvals channel
CHAN_FILE  = "approvals_channel.txt"
CHAN_NAME  = "kai-approvals"
CHAN_ABOUT = "KAI approval gates — reply `approve` or `reject: reason`."
# nginx at :3001 strips ONE /council/ prefix, so gate routes (/council/gate/...) need
# the doubled prefix. The #kai bridge reaches /council/message the same way.
COUNCIL_BASE  = os.environ.get("BUZZ_COUNCIL_BASE", "http://localhost:3001/council/council")
POLL_SECONDS  = int(os.environ.get("BUZZ_APPROVAL_POLL_SECONDS", "5"))
# Liveness heartbeat: written every poll cycle to a vault path the council-api reads
# (host ~/vault == container /vault). The council only skips the Telegram backup when
# this is fresh — a dead poller means Telegram fires immediately (true lifeline).
HEARTBEAT_PATH = os.environ.get("BUZZ_APPROVAL_HEARTBEAT", "/home/leo/vault/00_System/buzz_approve_heartbeat")
# Freshness window: reject a Leo-signed decision whose created_at is older than this
# (anti-replay, on top of the relay 'since=now' filter, the 'seen' set, and resolve's
# 409-on-already-resolved).
_MAX_DECISION_AGE = int(os.environ.get("BUZZ_APPROVAL_MAX_AGE", "900"))
# Re-nudge an unresolved gate on Buzz every this-many seconds (default 20 min).
# Buzz keeps reminding; Telegram is only the lifeline when the poller is dead.
_RENUDGE_SECONDS = int(os.environ.get("BUZZ_APPROVAL_RENUDGE_SECONDS", "1200"))
# Scoped-test guard: if set, ONLY this gate id is prompted AND only it may be resolved.
_ONLY_GATE = os.environ.get("BUZZ_APPROVAL_ONLY", "").strip()

# <verb> [gate-token] [: reason]. gate-token optional — bound from reply/single-open.
_DECISION_RE = re.compile(
    r"^\s*(approve|approved|yes|ok|reject|rejected|no|deny)\b\s*([\w-]{6,})?\s*(?::\s*(.*))?\s*$", re.I)
_APPROVE_VERBS = {"approve", "approved", "yes", "ok"}
# On-demand re-surface: Leo asks KAI to re-send everything still pending (for when he
# missed the prompt — meeting, plane, etc.). Gates never expire, so this always works.
_LIST_RE = re.compile(r"^\s*(pending|list|resend|approvals?|status|what'?s? pending|any approvals?)\s*\??\s*$", re.I)


def _basic_auth() -> str:
    return base64.b64encode(f"{ab.WEB_USER}:{ab.WEB_PW}".encode()).decode()


def _council_get(path: str) -> dict:
    req = urllib.request.Request(f"{COUNCIL_BASE}{path}",
        headers={"Authorization": f"Basic {_basic_auth()}"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def _council_resolve(gate_id: str, approved: bool, notes: str) -> dict:
    body = json.dumps({"approved": approved, "notes": notes, "resolver": "leo"}).encode()
    req = urllib.request.Request(f"{COUNCIL_BASE}/gate/{gate_id}/resolve", data=body, method="POST",
        headers={"Authorization": f"Basic {_basic_auth()}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def _event_id(ev: dict) -> str:
    """Recompute the Nostr event id exactly as agents_bridge.sign_event does."""
    serial = json.dumps([0, ev["pubkey"], ev["created_at"], ev["kind"], ev["tags"], ev["content"]],
                        separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(serial.encode("utf-8")).hexdigest()


def verify_leo(ev: dict) -> bool:
    """True ONLY if ev is a well-formed Nostr event whose recomputed id matches the
    claimed id, whose schnorr signature verifies against the author pubkey, AND whose
    author is exactly Leo's pinned pubkey. Any malformation → False (fail closed)."""
    try:
        if ev.get("pubkey") != ab.LEO_PUBKEY:
            return False
        if not all(k in ev for k in ("id", "sig", "created_at", "kind", "tags", "content")):
            return False
        if _event_id(ev) != ev["id"]:
            return False
        return bool(PublicKeyXOnly(bytes.fromhex(ev["pubkey"])).verify(
            bytes.fromhex(ev["sig"]), bytes.fromhex(ev["id"])))
    except Exception:
        return False


def parse_decision(content: str):
    """-> (gate_token_or_None, approved: bool, reason: str) or None if not a decision.
    gate_token may be omitted; the caller binds the gate from the reply e-tag or the
    single open prompt."""
    m = _DECISION_RE.match(content or "")
    if not m:
        return None
    verb = m.group(1).lower()
    return (m.group(2) or None), verb in _APPROVE_VERBS, (m.group(3) or "").strip()


async def run():
    pk = ab.load_or_create_key(KEY_FILE)
    cid = ab.get_channel(CHAN_FILE)
    me = ab.xonly(pk)
    send_lock = asyncio.Lock()
    prompt_map: dict[str, str] = {}   # prompt event id -> gate id (reply binding)
    open_gates: set[str] = set()      # prompted + still-unresolved gate ids
    prompted: set[str] = set()        # gate ids we've ever prompted (no double-prompt)
    ab.log("approvals", "pubkey", me, "channel", cid, "council", COUNCIL_BASE)

    async def send(text) -> dict:
        ev = ab.sign_event(pk, 9, [["h", cid]], text)
        async with send_lock:  # websockets: at most one concurrent send()
            await ws.send(json.dumps(["EVENT", ev]))
        return ev

    async def prompt_gate(g, prefix=""):
        gid = g.get("gate_id")
        ev = await send(f"{prefix}🔐 APPROVAL — {g.get('gate_type', 'gate')}\n"
                        f"{g.get('summary', '')}\n\n"
                        f"Reply `approve` or `reject: <reason>` (or `approve {gid}`).")
        prompt_map[ev["id"]] = gid
        open_gates.add(gid)
        return gid

    async with websockets.connect(ab.CONNECT_URL, max_size=2 ** 20) as ws:
        await ab.authenticate(ws, pk)
        async with send_lock:
            await ws.send(json.dumps(["EVENT", ab.sign_event(pk, 9007,
                [["h", cid], ["name", CHAN_NAME], ["visibility", "private"], ["about", CHAN_ABOUT]], "")]))
        await asyncio.sleep(1.0)
        async with send_lock:
            await ws.send(json.dumps(["EVENT", ab.sign_event(pk, 9000,
                [["h", cid], ["p", ab.LEO_PUBKEY], ["role", "member"]], "")]))
            await ws.send(json.dumps(["REQ", "sub", {"kinds": [9], "#h": [cid], "since": int(time.time())}]))
        ab.log("approvals", "online — polling pending gates + listening for Leo")

        def _beat():
            try:
                tmp = HEARTBEAT_PATH + ".tmp"
                with open(tmp, "w") as fh:
                    fh.write(str(int(time.time())))
                os.replace(tmp, HEARTBEAT_PATH)
            except Exception as e:
                ab.log("approvals", f"!! heartbeat write failed: {e}")

        last_prompt: dict[str, float] = {}   # gate id -> last time we posted it
        async def poller():
            while True:
                await asyncio.to_thread(_beat)   # prove liveness BEFORE work each cycle
                try:
                    data = await asyncio.to_thread(_council_get, "/gate/pending")
                    live = set()
                    for g in data.get("pending", []):
                        gid = g.get("gate_id")
                        if not gid or (_ONLY_GATE and gid != _ONLY_GATE):
                            continue
                        live.add(gid)
                        last = last_prompt.get(gid)
                        if last is None:
                            await prompt_gate(g)
                            last_prompt[gid] = time.time()
                            ab.log("approvals", f">> prompt sent for {gid}")
                        elif (time.time() - last) >= _RENUDGE_SECONDS:
                            # Re-nudge on Buzz first — Telegram is only the Buzz-dead lifeline.
                            await prompt_gate(g, prefix="🔁 (still waiting on you) ")
                            last_prompt[gid] = time.time()
                            ab.log("approvals", f">> re-nudged {gid}")
                    for gid in [k for k in last_prompt if k not in live]:
                        last_prompt.pop(gid, None)  # resolved — forget it
                except Exception as e:
                    ab.log("approvals", f"!! poll error: {e}")
                await asyncio.sleep(POLL_SECONDS)

        async def listener():
            seen: set[str] = set()
            async for raw in ws:
                m = json.loads(raw)
                if m[0] == "AUTH":
                    async with send_lock:
                        await ws.send(json.dumps(["AUTH", ab.sign_event(
                            pk, 22242, [["relay", ab.RELAY], ["challenge", m[1]]], "")]))
                    continue
                if m[0] != "EVENT" or m[1] != "sub":
                    continue
                ev = m[2]
                if ev.get("kind") != 9 or ev.get("id") in seen or ev.get("pubkey") == me:
                    continue
                seen.add(ev["id"])
                # SECURITY GATE: only Leo's cryptographically-verified identity may approve.
                if not verify_leo(ev):
                    ab.log("approvals", f"IGNORED non-Leo/invalid event from {ev.get('pubkey', '?')[:8]}")
                    continue
                # Anti-replay freshness.
                try:
                    age = abs(time.time() - int(ev.get("created_at", 0)))
                except (TypeError, ValueError):
                    age = _MAX_DECISION_AGE + 1
                if age > _MAX_DECISION_AGE:
                    ab.log("approvals", f"IGNORED stale decision (age {int(age)}s)")
                    continue
                content = ev.get("content", "")
                # On-demand re-surface: Leo asks what's pending (for when he missed a prompt —
                # meeting, plane). Gates never expire, so the open ones are always still there.
                if _LIST_RE.match(content):
                    try:
                        pend = (await asyncio.to_thread(_council_get, "/gate/pending")).get("pending", [])
                    except Exception:
                        await send("⚠️ Couldn't reach the council to check pending approvals — try again shortly.")
                        continue
                    if _ONLY_GATE:
                        pend = [x for x in pend if x.get("gate_id") == _ONLY_GATE]
                    if not pend:
                        await send("✅ Nothing pending — you're all caught up.")
                    else:
                        await send(f"You have {len(pend)} pending approval(s):")
                        for g in pend:
                            await prompt_gate(g, prefix="🔁 (re-sent) ")
                    ab.log("approvals", f"re-surfaced {len(pend)} pending on request")
                    continue
                decision = parse_decision(content)
                if not decision:
                    continue
                token, approved, reason = decision
                # Bind against the LIVE pending list (council is the source of truth) — the
                # in-memory sets are wiped on every container restart, which silently broke a
                # bare `approve`. Priority: explicit id > reply e-tag > single pending.
                try:
                    live_pending = [x.get("gate_id") for x in
                        (await asyncio.to_thread(_council_get, "/gate/pending")).get("pending", [])
                        if x.get("gate_id")]
                except Exception:
                    live_pending = list(open_gates)  # fall back to memory if council unreachable
                if _ONLY_GATE:
                    live_pending = [g for g in live_pending if g == _ONLY_GATE]

                gid = token if (token and token in live_pending) else None
                if gid is None:
                    for t in ev.get("tags", []):
                        if len(t) > 1 and t[0] == "e" and prompt_map.get(t[1]) in live_pending:
                            gid = prompt_map[t[1]]; break
                if gid is None and len(live_pending) == 1:
                    gid = live_pending[0]
                if gid is None:
                    if not live_pending:
                        await send("✅ Nothing pending to approve — you're all caught up.")
                    else:
                        await send("Which one? " + str(len(live_pending)) + " pending: "
                                   + ", ".join("`" + g + "`" for g in live_pending)
                                   + " — reply `approve <id>`.")
                    continue
                if _ONLY_GATE and gid != _ONLY_GATE:
                    ab.log("approvals", f"IGNORED decision for {gid[:12]} (scoped test locked to {_ONLY_GATE[:12]})")
                    continue
                try:
                    res = await asyncio.to_thread(_council_resolve, gid, approved, reason or "via Buzz")
                    open_gates.discard(gid)
                    await send(f"{'✅ approved' if approved else '🛑 rejected'} `{gid}`.")
                    ab.log("approvals", f"resolved {gid} approved={approved} -> {res.get('status')}")
                except urllib.error.HTTPError as e:
                    await send(f"⚠️ couldn't resolve `{gid}` (HTTP {e.code}) — it may already be resolved.")
                    ab.log("approvals", f"!! resolve {gid} HTTP {e.code}")
                except Exception as e:
                    ab.log("approvals", f"!! resolve {gid} error: {e}")

        await asyncio.gather(poller(), listener())


def _selftest():
    """Offline: prove the crypto identity gate + parser without any network."""
    from coincurve import PrivateKey

    assert parse_decision("approve") == (None, True, "")
    assert parse_decision("yes") == (None, True, "")
    assert parse_decision("reject: not on brand") == (None, False, "not on brand")
    assert parse_decision("approve abc123") == ("abc123", True, "")
    assert parse_decision("reject abc123: nope") == ("abc123", False, "nope")
    assert parse_decision("hello there") is None
    assert parse_decision("") is None

    assert _LIST_RE.match("pending") and _LIST_RE.match("resend") and _LIST_RE.match("what's pending?")
    assert _LIST_RE.match("list") and _LIST_RE.match("approvals")
    assert not _LIST_RE.match("approve abc123") and not _LIST_RE.match("reject: nope")

    leo = PrivateKey()
    orig = ab.LEO_PUBKEY
    try:
        ab.LEO_PUBKEY = ab.xonly(leo)
        good = ab.sign_event(leo, 9, [["h", "chan"]], "approve")
        assert verify_leo(good), "valid Leo-signed event must verify"
        bad = dict(good); bad["content"] = "reject"
        assert not verify_leo(bad), "tampered content must fail"
        stranger = PrivateKey()
        ab.LEO_PUBKEY = ab.xonly(stranger)
        s_ev = ab.sign_event(stranger, 9, [["h", "chan"]], "approve")
        ab.LEO_PUBKEY = ab.xonly(leo)  # re-pin Leo; stranger event must now fail author check
        assert not verify_leo(s_ev), "non-Leo author must fail"
        badsig = dict(good); badsig["sig"] = "00" * 64
        assert not verify_leo(badsig), "invalid signature must fail"
    finally:
        ab.LEO_PUBKEY = orig
    print("SELFTEST OK — bare-verb parser + identity/schnorr gate verified")


def main():
    if "--selftest" in sys.argv:
        _selftest(); return
    asyncio.run(run())


if __name__ == "__main__":
    main()
