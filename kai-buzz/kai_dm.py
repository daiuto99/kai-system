#!/usr/bin/env python3
"""KAI as a NIP-17 DM agent — the always-on 1:1 advisor DM (ported verbatim from sky_dm.py,
the shipped proof). KAI appears as a direct-message contact, answered server-side by the real
council orchestrator, so a DM lands whether Leo Mac is open or asleep. Retires the channel-based
KAI (agents_bridge.py KAI): one implementation per path (M-R2).

Hybrid design (deliberate):
  - transport = raw WebSocket + NIP-42 auth via agents_bridge (the working :3002-proxy
    path that correctly splits the connect-URL from the relay tag),
  - crypto = nostr-sdk for NIP-17 gift-wrap/unwrap (vetted; never hand-rolled).
"""
import asyncio, json, time, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "libs"))
import websockets
import agents_bridge as ab
from nostr_sdk import Keys, NostrSigner, PublicKey, EventBuilder, Event, UnwrappedGift

GIFT_WRAP_KIND = 1059
LOOKBACK = 172800  # 2 days — NIP-17 gift-wrap created_at is randomized into the past

_secret_hex = open(os.path.join(ab.AGENT_DIR, "kai_dm.key")).read().strip()
KAI_KEYS = Keys.parse(_secret_hex)
KAI_SIGNER = NostrSigner.keys(KAI_KEYS)
KAI_PUB_HEX = KAI_KEYS.public_key().to_hex()
LEO_PUB = PublicKey.parse(ab.LEO_PUBKEY)
INTRO_MARKER = os.path.join(ab.AGENT_DIR, "kai_dm_intro_sent")

INTRO = ("It's KAI — now a 1:1 direct message, always on and answering from the server (not "
         "your laptop). Message me here and I answer whether your Mac is open or asleep. Projects "
         "and group threads stay as channels — this is your direct line to me.")


async def _wrap_json(receiver_pub, text):
    # now-stamped NIP-59 wrap so the buzz-relay's tight created_at window accepts it (5de64f3f)
    return ab.build_giftwrap_now(KAI_KEYS, receiver_pub, text)


# ── self-healing reliability (KAI-1548) — ports the proven agents_bridge KAI-1142 loop ──
WS_PING_INTERVAL = 20        # send a ping this often; a missed pong -> ConnectionClosed -> reconnect
WS_PING_TIMEOUT = 20         # (detects a truly dead/half-open socket instead of blocking forever)
IDLE_RESUB_SEC = 50          # after this much inbound silence, re-arm the REQ — recovers a
                             # subscription the relay silently dropped, without a full reconnect
RECONNECT_BACKOFF_SEC = 3    # pause before reconnecting (no hot loop on a hard error)
_HB = {"name": "KAI-DM", "heartbeat": True}   # ab._heartbeat -> /vault/00_System/buzz_agent_KAI-DM_heartbeat


async def run():
    pk = ab.load_or_create_key("kai_dm.key")            # coincurve key, for NIP-42 auth
    send_lock = asyncio.Lock()

    async def send_dm(ws, receiver_pub, text):
        w = await _wrap_json(receiver_pub, text)
        async with send_lock:
            await ws.send(json.dumps(["EVENT", w]))

    async def arm_req(ws):
        # NIP-17 gift-wrap created_at is randomized up to LOOKBACK into the past, so the
        # window can't be tightened on reconnect — always look back LOOKBACK and let the
        # cross-reconnect `seen` set dedup. This is what makes a gap message survivable.
        async with send_lock:
            await ws.send(json.dumps(["REQ", "dm", {"kinds": [GIFT_WRAP_KIND], "#p": [KAI_PUB_HEX],
                                                    "since": int(time.time()) - LOOKBACK}]))

    ab.log("kai-dm", "KAI DM agent · pubkey", KAI_PUB_HEX, "· connect", ab.CONNECT_URL)
    seen = set()             # dedup across reconnects — a backfilled message is never re-answered
    first = True
    while True:              # KAI-1548 self-healing reconnect loop: a dropped, half-open, or
                             # silently-idle relay link now reconnects + re-subscribes + backfills
                             # instead of dying quietly or blocking forever in `async for` (the
                             # 2026-09 silent-deaf gap on Leo's real DM path).
        try:
            async with websockets.connect(ab.CONNECT_URL, max_size=2 ** 20,
                                          ping_interval=WS_PING_INTERVAL,
                                          ping_timeout=WS_PING_TIMEOUT) as ws:
                await ab.authenticate(ws, pk)
                await arm_req(ws)
                # one-time intro DM so KAI appears as a contact/conversation in Leo's client
                if first and not os.path.exists(INTRO_MARKER):
                    try:
                        await send_dm(ws, LEO_PUB, INTRO)
                        open(INTRO_MARKER, "w").write(str(int(time.time())))
                        ab.log("kai-dm", "intro DM sent to Leo")
                    except Exception as e:
                        ab.log("kai-dm", f"intro send failed: {e}")
                ab.log("kai-dm", "online — listening for Leo's DMs" if first else "reconnected — backfilling")
                first = False
                ab._heartbeat(_HB)
                while True:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=IDLE_RESUB_SEC)
                    except asyncio.TimeoutError:
                        # inbound silence: stamp liveness + re-arm the REQ (recovers a
                        # subscription the relay dropped without ever sending a CLOSE).
                        ab._heartbeat(_HB)
                        await arm_req(ws)
                        continue
                    ab._heartbeat(_HB)
                    m = json.loads(raw)
                    if m[0] == "AUTH":
                        async with send_lock:
                            await ws.send(json.dumps(["AUTH", ab.sign_event(
                                pk, 22242, [["relay", ab.RELAY], ["challenge", m[1]]], "")]))
                        continue
                    if m[0] != "EVENT" or m[1] != "dm":
                        continue
                    wrap_ev = m[2]
                    if wrap_ev.get("id") in seen:
                        continue
                    seen.add(wrap_ev["id"])
                    try:
                        uw = await UnwrappedGift.from_gift_wrap(KAI_SIGNER, Event.from_json(json.dumps(wrap_ev)))
                        sender_hex = uw.sender().to_hex()
                        text = uw.rumor().content()
                    except Exception as e:
                        ab.log("kai-dm", f"unwrap failed: {e}")
                        continue
                    if sender_hex == KAI_PUB_HEX:
                        continue    # skip our own self-copies
                    ab.log("kai-dm", f"<< {sender_hex[:8]}: {text[:80]}")
                    try:
                        reply = await asyncio.to_thread(ab.call_council, "kai", text, "kai-dm:" + sender_hex[:16])
                    except ab.BackendError:
                        reply = "Hit a transient backend hiccup and couldn't process that — resend it and I'll pick right up; nothing was lost."
                    except Exception as e:
                        reply = f"(KAI ran into an error handling that: {e})"
                    try:
                        await send_dm(ws, uw.sender(), reply)
                        ab.log("kai-dm", f">> {reply[:100]}")
                    except Exception as e:
                        ab.log("kai-dm", f"reply send failed: {e}")
        except Exception as e:
            # ANY link loss (dead socket via missed pong, relay CLOSE, network blip) lands here
            # and reconnects with backfill — never a silent death. `seen` persists for dedup.
            ab.log("kai-dm", f"link lost ({type(e).__name__}: {e}) — reconnecting in {RECONNECT_BACKOFF_SEC}s")
            await asyncio.sleep(RECONNECT_BACKOFF_SEC)


if __name__ == "__main__":
    asyncio.run(run())
