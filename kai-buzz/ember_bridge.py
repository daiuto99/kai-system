#!/usr/bin/env python3
"""
Ember — KAI advisor as a first-class Buzz (Nostr) agent.  KAI-984 comms spike (24e49013).

Ember gets its OWN Nostr keypair, joins a PRIVATE Buzz channel, and on every human
message calls KAI's local litellm gateway (qwen-mid -> 71-kai-mini, fully on the tailnet)
and posts the reply back into the channel. Nothing leaves Leo's infra.

Modes:
  bridge   run Ember (daemon): ensure private channel, add allowed members, answer messages
  test     post one message as a throwaway 'tester' identity and wait for Ember's reply
  add PUBKEY_HEX   add a member (e.g. Leo's desktop pubkey) to the channel

Deps: websockets, coincurve  (installed under agent/libs)
"""
import asyncio, json, hashlib, time, os, sys, urllib.request
import websockets
from coincurve import PrivateKey

CONNECT_URL = os.environ.get("CONNECT_URL", os.environ.get("RELAY_URL", "ws://127.0.0.1:3002"))
RELAY_TAG = os.environ.get("RELAY_TAG", "wss://kai-worker.tail7f43c5.ts.net")
LITELLM_URL = os.environ.get("LITELLM_URL", "http://localhost:4000/v1/chat/completions")
LITELLM_KEY = open(os.path.expanduser(os.environ.get("LITELLM_KEY_FILE", "~/kai-system/secrets/litellm_master_key.txt"))).read().strip()
MODEL = os.environ.get("EMBER_MODEL", "qwen-mid")
CHANNEL_NAME = "ember-lab"

AGENT_DIR = os.environ.get("BUZZ_AGENT_DIR", os.path.expanduser("~/buzz-eval/agent"))
EMBER_KEY = os.path.join(AGENT_DIR, "ember.key")
TESTER_KEY = os.path.join(AGENT_DIR, "tester.key")
CHANNEL_FILE = os.path.join(AGENT_DIR, "channel.txt")

SYSTEM_PROMPT = (
    "You are Ember, Leo's private AI advisor. You run locally on Leo's own hardware "
    "(a Mac mini on his Tailscale network) via KAI's self-hosted inference gateway — "
    "nothing you say leaves his infrastructure. You are speaking inside a private Buzz "
    "channel. Be concise, direct, and useful. Keep replies to a few sentences unless asked for more."
)


def load_or_create_key(path):
    os.makedirs(AGENT_DIR, exist_ok=True)
    if os.path.exists(path):
        return PrivateKey(bytes.fromhex(open(path).read().strip()))
    pk = PrivateKey()
    with open(path, "w") as f:
        f.write(pk.secret.hex())
    os.chmod(path, 0o600)
    return pk


def xonly(pk):
    return pk.public_key.format(compressed=True)[1:].hex()


def sign_event(pk, kind, tags, content):
    pub = xonly(pk)
    created = int(time.time())
    serial = json.dumps([0, pub, created, kind, tags, content], separators=(",", ":"), ensure_ascii=False)
    eid = hashlib.sha256(serial.encode("utf-8")).hexdigest()
    sig = pk.sign_schnorr(bytes.fromhex(eid)).hex()
    return {"id": eid, "pubkey": pub, "created_at": created, "kind": kind, "tags": tags, "content": content, "sig": sig}


def bech32_to_hex(s):
    """Decode an npub (bech32) to 32-byte hex. Pass hex through unchanged."""
    s = s.strip()
    if not s.startswith("npub1"):
        return s
    charset = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
    data = [charset.index(c) for c in s[s.rfind("1") + 1:]][:-6]  # drop 6-char checksum
    acc = bits = 0
    out = []
    for v in data:
        acc = (acc << 5) | v
        bits += 5
        while bits >= 8:
            bits -= 8
            out.append((acc >> bits) & 0xFF)
    return bytes(out).hex()


def new_uuid():
    import uuid
    return str(uuid.uuid4())


def get_channel(create=False):
    if os.path.exists(CHANNEL_FILE):
        return open(CHANNEL_FILE).read().strip()
    if create:
        cid = new_uuid()
        with open(CHANNEL_FILE, "w") as f:
            f.write(cid)
        return cid
    return None


def call_litellm(user_text):
    body = json.dumps({
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_text},
        ],
        "max_tokens": 400,
        "temperature": 0.6,
    }).encode()
    req = urllib.request.Request(
        LITELLM_URL, data=body,
        headers={"Authorization": f"Bearer {LITELLM_KEY}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=90) as r:
        d = json.loads(r.read())
    return d["choices"][0]["message"]["content"].strip()


def log(*a):
    print(f"[{time.strftime('%H:%M:%S')}]", *a, flush=True)


async def authenticate(ws, pk):
    """Wait for the relay's NIP-42 challenge, answer it, return once OK."""
    while True:
        raw = await ws.recv()
        msg = json.loads(raw)
        if msg[0] == "AUTH":
            ev = sign_event(pk, 22242, [["relay", RELAY_TAG], ["challenge", msg[1]]], "")
            await ws.send(json.dumps(["AUTH", ev]))
        elif msg[0] == "OK":
            log("authed:", msg)
            return
        elif msg[0] == "NOTICE":
            log("notice:", msg)


async def run_bridge():
    pk = load_or_create_key(EMBER_KEY)
    tester = load_or_create_key(TESTER_KEY)
    cid = get_channel(create=True)
    log("Ember pubkey:", xonly(pk))
    log("channel:", cid)
    async with websockets.connect(CONNECT_URL, max_size=2 ** 20) as ws:
        await authenticate(ws, pk)
        # create the private channel (idempotent: relay reuses the h-tag UUID)
        await ws.send(json.dumps(["EVENT", sign_event(pk, 9007,
            [["h", cid], ["name", CHANNEL_NAME], ["visibility", "private"],
             ["about", "Private channel for talking to Ember (KAI advisor)."]], "")]))
        # add the tester identity as a member so it can post
        await ws.send(json.dumps(["EVENT", sign_event(pk, 9000,
            [["h", cid], ["p", xonly(tester)], ["role", "member"]], "")]))
        # subscribe to live human messages in the channel
        await ws.send(json.dumps(["REQ", "ember", {"kinds": [9], "#h": [cid], "since": int(time.time())}]))
        log("Ember online — listening for messages in the channel")
        me = xonly(pk)
        seen = set()
        async for raw in ws:
            msg = json.loads(raw)
            if msg[0] == "AUTH":
                ev = sign_event(pk, 22242, [["relay", RELAY_TAG], ["challenge", msg[1]]], "")
                await ws.send(json.dumps(["AUTH", ev]))
            elif msg[0] == "EVENT" and msg[1] == "ember":
                ev = msg[2]
                if ev["kind"] != 9 or ev["pubkey"] == me or ev["id"] in seen:
                    continue
                seen.add(ev["id"])
                text = ev.get("content", "")
                log(f"<< {ev['pubkey'][:8]}: {text}")
                try:
                    reply = await asyncio.to_thread(call_litellm, text)
                except Exception as e:
                    reply = f"(Ember error reaching local model: {e})"
                out = sign_event(pk, 9, [["h", cid], ["e", ev["id"]], ["p", ev["pubkey"]]], reply)
                await ws.send(json.dumps(["EVENT", out]))
                log(f">> Ember: {reply[:120]}")
            elif msg[0] in ("OK", "CLOSED", "NOTICE"):
                log(msg[0], msg[1:])


async def run_test():
    pk = load_or_create_key(TESTER_KEY)
    ember_pub = xonly(load_or_create_key(EMBER_KEY))
    cid = get_channel(create=False)
    if not cid:
        log("no channel yet — start the bridge first"); return
    async with websockets.connect(CONNECT_URL, max_size=2 ** 20) as ws:
        await authenticate(ws, pk)
        await ws.send(json.dumps(["REQ", "t", {"kinds": [9], "#h": [cid], "since": int(time.time())}]))
        q = "Ember, are you online? In one or two sentences, tell me what hardware you run on and confirm this conversation stays on my network."
        await ws.send(json.dumps(["EVENT", sign_event(pk, 9, [["h", cid]], q)]))
        log("tester asked:", q)

        async def wait_reply():
            async for raw in ws:
                msg = json.loads(raw)
                if msg[0] == "AUTH":
                    ev = sign_event(pk, 22242, [["relay", RELAY_TAG], ["challenge", msg[1]]], "")
                    await ws.send(json.dumps(["AUTH", ev]))
                elif msg[0] == "EVENT" and msg[2].get("pubkey") == ember_pub and msg[2]["kind"] == 9:
                    log("EMBER REPLIED >>>", msg[2]["content"])
                    return
                elif msg[0] in ("OK", "CLOSED", "NOTICE"):
                    log(msg[0], msg[1:])
        try:
            await asyncio.wait_for(wait_reply(), timeout=90)
        except asyncio.TimeoutError:
            log("TIMEOUT — no reply from Ember in 90s")


async def run_add(pubkey_in):
    pubkey_hex = bech32_to_hex(pubkey_in)
    log("adding pubkey:", pubkey_hex)
    pk = load_or_create_key(EMBER_KEY)
    cid = get_channel(create=False)
    ev = sign_event(pk, 9000, [["h", cid], ["p", pubkey_hex], ["role", "member"]], "")
    async with websockets.connect(CONNECT_URL, max_size=2 ** 20) as ws:
        await authenticate(ws, pk)
        await ws.send(json.dumps(["EVENT", ev]))

        async def wait_ok():
            async for raw in ws:
                msg = json.loads(raw)
                if msg[0] == "AUTH":
                    a = sign_event(pk, 22242, [["relay", RELAY_TAG], ["challenge", msg[1]]], "")
                    await ws.send(json.dumps(["AUTH", a]))
                elif msg[0] == "OK" and msg[1] == ev["id"]:
                    log("add result:", "ACCEPTED" if msg[2] else "REJECTED", msg[3] or "")
                    return
        try:
            await asyncio.wait_for(wait_ok(), timeout=15)
        except asyncio.TimeoutError:
            log("no OK received for add (timeout)")
    log("done: added", pubkey_hex, "to channel", cid)


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "bridge"
    if mode == "bridge":
        asyncio.run(run_bridge())
    elif mode == "test":
        asyncio.run(run_test())
    elif mode == "add":
        asyncio.run(run_add(sys.argv[2]))
    else:
        print("usage: ember_bridge.py [bridge|test|add PUBKEY]")
