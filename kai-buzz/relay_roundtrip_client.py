#!/usr/bin/env python3
"""KAI-1142 · [M-R1] Relay round-trip transport CLIENT (runs INSIDE kai-buzz via docker exec).

The KAI-1110 advisor_dm_probe only exercises the shim shortcut (:4001 -> council). It CANNOT
see the failure that left a real Leo DM unanswered while every monitor stayed green: the relay
silently stopped delivering to the KAI agent's subscription (socket up, container restarts=0),
so the message never reached the agent and no reply ever came back.

This client drives the EXACT path that died:

    probe client  --kind:9-->  relay  -->  KAIProbe agent (agents_bridge, echo backend)
                                              |
    probe client  <--kind:9 ack--  relay  <--+

It posts a kind:9 carrying a fresh nonce into the isolated KAIProbe channel, then asserts the
live agent picked it up and echoed a kind:9 reply carrying that same nonce back THROUGH the
relay, within a latency bound. It is fully isolated from Leo's real DMs (dedicated channel +
dedicated synthetic client/responder identities) and spends no council tokens (echo backend).

Deliberately self-contained (no agents_bridge import -> no secret-file side effects at import):
it only needs the same Nostr crypto the agent uses. Prints a single JSON verdict line to stdout
and exits 0 (healthy round-trip) / 1 (silence or bad ack). The HOST wrapper
(scripts/relay_roundtrip_probe.py) turns that verdict into a heartbeat + a provenance=real page.
"""
import asyncio
import hashlib
import json
import os
import secrets
import sys
import time

import websockets
from coincurve import PrivateKey

RELAY = os.environ.get("RELAY_TAG", "wss://kai-worker.tail7f43c5.ts.net")
CONNECT_URL = os.environ.get("CONNECT_URL", "ws://127.0.0.1:3002")
AGENT_DIR = os.environ.get("BUZZ_AGENT_DIR", "/agent")

CLIENT_KEY_FILE = "kaiprobe_client.key"   # the synthetic "Leo-like" sender
RESPONDER_KEY_FILE = "kaiprobe.key"       # KAIProbe's identity — whose ack we require
CHAN_FILE = "kaiprobe_channel.txt"        # the isolated probe channel (owned by KAIProbe)

PROBE_MARK = "__relay_probe__"
ACK_MARK = "__relay_probe_ack__"
ROUNDTRIP_TIMEOUT_SEC = 30                 # beyond this = silence (the paged failure)


def _load_key(path):
    p = os.path.join(AGENT_DIR, path)
    if os.path.exists(p):
        return PrivateKey(bytes.fromhex(open(p).read().strip()))
    pk = PrivateKey()
    os.makedirs(AGENT_DIR, exist_ok=True)
    open(p, "w").write(pk.secret.hex())
    os.chmod(p, 0o600)
    return pk


def _xonly(pk):
    return pk.public_key.format(compressed=True)[1:].hex()


def _sign(pk, kind, tags, content, created=None):
    pub = _xonly(pk)
    created = created or int(time.time())
    serial = json.dumps([0, pub, created, kind, tags, content], separators=(",", ":"), ensure_ascii=False)
    eid = hashlib.sha256(serial.encode("utf-8")).hexdigest()
    sig = pk.sign_schnorr(bytes.fromhex(eid)).hex()
    return {"id": eid, "pubkey": pub, "created_at": created, "kind": kind, "tags": tags, "content": content, "sig": sig}


async def _authenticate(ws, pk):
    async for raw in ws:
        m = json.loads(raw)
        if m[0] == "AUTH":
            await ws.send(json.dumps(["AUTH", _sign(pk, 22242, [["relay", RELAY], ["challenge", m[1]]], "")]))
        elif m[0] == "OK":
            return


async def _roundtrip() -> dict:
    started = time.time()
    client_pk = _load_key(CLIENT_KEY_FILE)
    responder_pub = _xonly(_load_key(RESPONDER_KEY_FILE))
    chan_path = os.path.join(AGENT_DIR, CHAN_FILE)
    if not os.path.exists(chan_path):
        return {"ok": False, "latency_ms": 0,
                "reason": f"probe channel not provisioned ({CHAN_FILE} missing — KAIProbe never started?)"}
    cid = open(chan_path).read().strip()
    nonce = f"{int(started)}-{secrets.token_hex(6)}"

    try:
        async with websockets.connect(CONNECT_URL, max_size=2 ** 20,
                                      ping_interval=20, ping_timeout=20, close_timeout=10) as ws:
            await asyncio.wait_for(_authenticate(ws, client_pk), timeout=15)
            # Subscribe first so we cannot miss a fast ack, then post the probe message.
            await ws.send(json.dumps(["REQ", "sub", {"kinds": [9], "#h": [cid], "since": int(started) - 5}]))
            await ws.send(json.dumps(["EVENT", _sign(client_pk, 9, [["h", cid]], f"{PROBE_MARK} {nonce}")]))

            saw_reply_wrong_nonce = False
            while True:
                remaining = ROUNDTRIP_TIMEOUT_SEC - (time.time() - started)
                if remaining <= 0:
                    reason = ("ack for THIS nonce never arrived (a reply came but for another nonce — "
                              "stale/duplicate)" if saw_reply_wrong_nonce
                              else f"no agent ack within {ROUNDTRIP_TIMEOUT_SEC}s "
                                   "(relay->agent->relay transport dead — the KAI-1142 silent drop)")
                    return {"ok": False, "latency_ms": int((time.time() - started) * 1000), "reason": reason}
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
                except asyncio.TimeoutError:
                    continue
                m = json.loads(raw)
                if m[0] == "AUTH":
                    await ws.send(json.dumps(["AUTH", _sign(client_pk, 22242,
                                                            [["relay", RELAY], ["challenge", m[1]]], "")]))
                    continue
                if m[0] != "EVENT" or m[1] != "sub":
                    continue
                ev = m[2]
                # Only the live KAIProbe agent's echo counts — never our own posted message.
                if ev.get("kind") != 9 or ev.get("pubkey") != responder_pub:
                    continue
                content = ev.get("content", "")
                if not content.startswith(ACK_MARK):
                    continue
                if nonce in content:
                    return {"ok": True, "latency_ms": int((time.time() - started) * 1000),
                            "reason": "healthy round-trip (agent echoed our nonce through the relay)"}
                saw_reply_wrong_nonce = True
    except Exception as e:
        return {"ok": False, "latency_ms": int((time.time() - started) * 1000),
                "reason": f"transport error: {type(e).__name__}: {e}"}


def main() -> int:
    try:
        result = asyncio.run(asyncio.wait_for(_roundtrip(), timeout=ROUNDTRIP_TIMEOUT_SEC + 15))
    except Exception as e:
        result = {"ok": False, "latency_ms": 0, "reason": f"probe crashed: {type(e).__name__}: {e}"}
    print(json.dumps(result), flush=True)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
