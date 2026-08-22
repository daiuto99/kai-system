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
from nostr_sdk import Keys, NostrSigner, PublicKey, EventBuilder, Event, UnwrappedGift, gift_wrap

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
    rumor = EventBuilder.private_msg_rumor(receiver_pub, text).build(KAI_KEYS.public_key())
    wrap = await gift_wrap(KAI_SIGNER, receiver_pub, rumor, [])
    return json.loads(wrap.as_json())


async def run():
    pk = ab.load_or_create_key("kai_dm.key")            # coincurve key, for NIP-42 auth
    send_lock = asyncio.Lock()

    async def send_dm(ws, receiver_pub, text):
        w = await _wrap_json(receiver_pub, text)
        async with send_lock:
            await ws.send(json.dumps(["EVENT", w]))

    ab.log("kai-dm", "KAI DM agent · pubkey", KAI_PUB_HEX, "· connect", ab.CONNECT_URL)
    async with websockets.connect(ab.CONNECT_URL, max_size=2 ** 20) as ws:
        await ab.authenticate(ws, pk)
        async with send_lock:
            await ws.send(json.dumps(["REQ", "dm", {"kinds": [GIFT_WRAP_KIND], "#p": [KAI_PUB_HEX],
                                                    "since": int(time.time()) - LOOKBACK}]))
        # one-time intro DM so Sky appears as a contact/conversation in Leo's client
        if not os.path.exists(INTRO_MARKER):
            try:
                await send_dm(ws, LEO_PUB, INTRO)
                open(INTRO_MARKER, "w").write(str(int(time.time())))
                ab.log("kai-dm", "intro DM sent to Leo")
            except Exception as e:
                ab.log("kai-dm", f"intro send failed: {e}")
        ab.log("kai-dm", "online — listening for Leo's DMs")
        seen = set()
        async for raw in ws:
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


if __name__ == "__main__":
    asyncio.run(run())
