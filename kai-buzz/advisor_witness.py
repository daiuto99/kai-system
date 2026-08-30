#!/usr/bin/env python3
"""[W-1] Advisor external witness — the honest replacement for advisor_dm_probe.py.

Drives the REAL always-on advisor path across the RELAY boundary. A dedicated
witness identity (distinct from any advisor and from council) gift-wraps a NIP-17
DM to Sky's npub carrying a fresh nonce, then reads the RELAY for a reply event
from Sky whose content carries that nonce. The relay mints the event id; council
and the mini cannot forge it, and the nonce defeats the ack/echo (the "Sky is on
it…" ack does not contain the token).

Emits ONE JSON line: the raw external observation. The trusted witness layer
(shared/witness.py, run off-box) turns this into a Verdict — GREEN only on a
relay-minted reply carrying the nonce; observed=false -> UNKNOWN; ack-only ->
never green.

ACCEPTANCE (design finding 2, 2026-08-30): against the live system today this
returns observed=false, because the async answer never comes back over Buzz —
only the ack does. That RED is the proof the witness catches the real break.

Runs inside kai-buzz (has the relay machinery); the caller runs it via
`docker exec` from the host/off-box so the verdict authority stays external.
"""
import asyncio
import json
import os
import secrets
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "libs"))
import websockets  # noqa: E402
import agents_bridge as ab  # noqa: E402
from nostr_sdk import (  # noqa: E402
    Keys,
    NostrSigner,
    PublicKey,
    EventBuilder,
    Event,
    UnwrappedGift,
    gift_wrap,
)

GIFT_WRAP_KIND = 1059
TARGET = os.environ.get("ADVISOR_WITNESS_TARGET", "sky")
TIMEOUT = int(os.environ.get("ADVISOR_WITNESS_TIMEOUT", "200"))  # > cold mini turn


def _emit(obj):
    print("WITNESS_RESULT " + json.dumps(obj), flush=True)


async def run():
    nonce = secrets.token_hex(4).upper()
    token = f"WITNESS-{nonce}"
    prompt = (
        f"System check. Reply with exactly this token on its own line and nothing "
        f"else: {token}"
    )

    # Dedicated witness identity (its own key — NOT an advisor, NOT council).
    wit_ck = ab.load_or_create_key("advisor_witness.key")  # coincurve, for NIP-42
    wit_hex = open(os.path.join(ab.AGENT_DIR, "advisor_witness.key")).read().strip()
    WIT_KEYS = Keys.parse(wit_hex)
    WIT_SIGNER = NostrSigner.keys(WIT_KEYS)
    WIT_PUB_HEX = WIT_KEYS.public_key().to_hex()

    # Target advisor's public key (read its key file; we DM its npub).
    tgt_hex = open(os.path.join(ab.AGENT_DIR, f"{TARGET}.key")).read().strip()
    TGT_PUB = Keys.parse(tgt_hex).public_key()
    TGT_PUB_HEX = TGT_PUB.to_hex()

    async def wrap_json(receiver_pub, text):
        rumor = EventBuilder.private_msg_rumor(receiver_pub, text).build(WIT_KEYS.public_key())
        wrap = await gift_wrap(WIT_SIGNER, receiver_pub, rumor, [])
        return json.loads(wrap.as_json())

    started = time.time()
    async with websockets.connect(ab.CONNECT_URL, max_size=2 ** 20) as ws:
        await ab.authenticate(ws, wit_ck)
        # subscribe to gift-wraps addressed to the witness
        await ws.send(json.dumps(["REQ", "wit", {"kinds": [GIFT_WRAP_KIND],
                                                 "#p": [WIT_PUB_HEX],
                                                 "since": int(started) - 5}]))
        # send the nonce prompt to the advisor
        w = await wrap_json(TGT_PUB, prompt)
        await ws.send(json.dumps(["EVENT", w]))

        seen = set()
        saw_ack = False
        deadline = started + TIMEOUT
        while time.time() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=deadline - time.time())
            except asyncio.TimeoutError:
                break
            m = json.loads(raw)
            if m[0] == "AUTH":
                await ws.send(json.dumps(["AUTH", ab.sign_event(
                    wit_ck, 22242, [["relay", ab.RELAY], ["challenge", m[1]]], "")]))
                continue
            if m[0] != "EVENT" or m[1] != "wit":
                continue
            ev = m[2]
            if ev.get("id") in seen:
                continue
            seen.add(ev["id"])
            try:
                uw = await UnwrappedGift.from_gift_wrap(WIT_SIGNER, Event.from_json(json.dumps(ev)))
                sender_hex = uw.sender().to_hex()
                content = uw.rumor().content()
            except Exception:
                continue
            if sender_hex != TGT_PUB_HEX:
                continue  # only the advisor's replies count
            if token in content:
                # RELAY-MINTED receipt carrying the nonce -> the answer really came back
                return _emit({
                    "observed": True, "minted_by": "buzz-relay", "boundary": "buzz-relay",
                    "raw_ref": ev.get("id"), "nonce": token, "target": TARGET,
                    "reply_excerpt": content[:160],
                    "elapsed_s": round(time.time() - started, 1),
                })
            # a reply that does NOT carry the token (the "…is on it" ack) — keep waiting
            saw_ack = True

    reason = ("ack-only, no nonce-bearing answer returned over Buzz within "
              f"{TIMEOUT}s" if saw_ack else f"no reply from {TARGET} within {TIMEOUT}s")
    _emit({"observed": False, "minted_by": "buzz-relay", "boundary": "buzz-relay",
           "raw_ref": None, "nonce": token, "target": TARGET,
           "saw_ack": saw_ack, "reason": reason,
           "elapsed_s": round(time.time() - started, 1)})


if __name__ == "__main__":
    asyncio.run(run())
