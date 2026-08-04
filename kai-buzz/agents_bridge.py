#!/usr/bin/env python3
"""
KAI advisors as first-class Buzz (Nostr) agents.  KAI-984 comms spike (24e49013).

Runs multiple agents in one process, each with its own Nostr identity, its own PRIVATE
Buzz channel, a kind:0 profile (name + avatar), and a backend:
  - KAI   -> the REAL council orchestrator (POST /council/message, claude-sonnet, full agentic loop)
  - Ember -> local inference (litellm qwen-mid -> the Mac mini), private local advisor

Everything stays on Leo's tailnet. Deps: websockets, coincurve (under agent/libs).

KAI-1020 hardening (2026-08-03):
  * Transient backend failures (nginx-injected worker-auth 401, 5xx, conn-reset) no longer
    surface as KAI's own voice and no longer silently drop the turn — call_council retries
    with backoff, and only an exhausted retry emits an honest, in-voice "resend" line.
  * Each Buzz channel now carries its own server-owned council thread (thread_ts = the Nostr
    channel id), so #kai and #reclamation no longer share (and clobber) one conversation.
"""
import asyncio, json, hashlib, time, os, sys, base64, urllib.request, urllib.error
import websockets
from coincurve import PrivateKey

# Auth 'relay' tag must match the relay's configured RELAY_URL (community host).
RELAY = os.environ.get("RELAY_TAG", "wss://kai-worker.tail7f43c5.ts.net")
# Actual TCP target: the local nginx proxy that forces Host=<community host>.
CONNECT_URL = os.environ.get("CONNECT_URL", "ws://127.0.0.1:3002")
RELAY_HOST = os.environ.get("RELAY_HOST", "kai-worker.tail7f43c5.ts.net")
MEDIA_UPLOAD_URL = os.environ.get("BUZZ_MEDIA_URL", "http://localhost:3000/media/upload")
AGENT_DIR = os.environ.get("BUZZ_AGENT_DIR", os.path.expanduser("~/buzz-eval/agent"))

LITELLM_URL = os.environ.get("LITELLM_URL", "http://localhost:4000/v1/chat/completions")
LITELLM_KEY = open(os.path.expanduser(os.environ.get("LITELLM_KEY_FILE", "~/kai-system/secrets/litellm_master_key.txt"))).read().strip()
COUNCIL_URL = os.environ.get("BUZZ_COUNCIL_URL", "http://localhost:3001/council/message")
WEB_USER = os.environ.get("BUZZ_WEB_USER", "kai")
WEB_PW = open(os.path.expanduser(os.environ.get("KAI_WEB_PW_FILE", "~/kai-system/secrets/kai_web_password.txt"))).read().strip()

# KAI-1020: transient-failure retry. The live 401 recovered on the very next turn, so a
# short bounded backoff fully masks the nginx-injected-worker-auth hiccup without ever
# dropping the turn or speaking the raw error.
RETRY_STATUSES = {401, 403, 429, 500, 502, 503, 504}
RETRY_BACKOFF = (2, 4, 8)  # seconds; len == max retries after the first attempt

LEO_PUBKEY = "0aba761fc4d63a1c69118af62e6d62c85179bee15afadac5a974252fed7a4b44"

EMBER_SYSTEM = (
    "You are Ember, Leo's private AI advisor running locally on his own Mac mini via KAI's "
    "self-hosted inference gateway — nothing you say leaves his network. You're in a private "
    "Buzz channel. Be concise, warm, and direct; a few sentences unless asked for more."
)


class BackendError(Exception):
    """A backend call failed after exhausting retries — the turn was NOT answered.

    Raised (not returned) so the caller can tell a real reply apart from a failure and
    emit an honest, in-voice recovery line instead of speaking the raw error string.
    """


AGENTS = [
    {
        "name": "KAI", "key": "kai.key", "chan_file": "kai_channel.txt",
        "chan_name": "kai", "about": "KAI — your assistant. Real orchestrator, full context.",
        "avatar": "kai_avatar.png", "backend": "council", "council_channel": "kai",
    },
    {
        "name": "Sky", "key": "sky.key", "chan_file": "sky_channel.txt",
        "chan_name": "sky", "about": "Sky — Studio 71 collaborator: sessions, signal flow, gear routing. DM 1:1.",
        "avatar": "sky_avatar.png", "backend": "council", "council_channel": "sky",
    },
    {
        "name": "Roads", "key": "roads.key", "chan_file": "roads_channel.txt",
        "chan_name": "roads", "about": "Roads — your guitar tech & gear authority. DM 1:1.",
        "avatar": "roads_avatar.png", "backend": "council", "council_channel": "roads",
    },
    {
        "name": "Coach", "key": "coach.key", "chan_file": "coach_channel.txt",
        "chan_name": "coach", "about": "Coach — fitness, recovery & accountability. DM 1:1.",
        "avatar": "coach_avatar.png", "backend": "council", "council_channel": "coach",
    },
    {
        # Shared gear room (KAI-owned). Roads answers here via the council; join-only
        # so it doesn't re-own the channel or overwrite Roads's 1:1 profile.
        "name": "GearTalk", "key": "roads.key", "chan_file": "geartalk_channel.txt",
        "chan_name": "GearTalk", "about": "GearTalk — shared gear room.",
        "avatar": "roads_avatar.png", "backend": "council", "council_channel": "roads",
        "join": True, "addressed_only": True, "aliases": ["roads"],
    },
    {
        # Second voice in the same gear room: Sky (Studio 71) via the council.
        "name": "GearTalkSky", "key": "sky.key", "chan_file": "geartalk_channel.txt",
        "chan_name": "GearTalk", "about": "GearTalk — shared gear room.",
        "avatar": "sky_avatar.png", "backend": "council", "council_channel": "sky",
        "join": True, "addressed_only": True, "aliases": ["sky"],
    },
]


def log(name, *a):
    print(f"[{time.strftime('%H:%M:%S')}][{name}]", *a, flush=True)


def load_or_create_key(path):
    os.makedirs(AGENT_DIR, exist_ok=True)
    p = os.path.join(AGENT_DIR, path)
    if os.path.exists(p):
        return PrivateKey(bytes.fromhex(open(p).read().strip()))
    pk = PrivateKey()
    open(p, "w").write(pk.secret.hex()); os.chmod(p, 0o600)
    return pk


def xonly(pk):
    return pk.public_key.format(compressed=True)[1:].hex()


def sign_event(pk, kind, tags, content, created=None):
    pub = xonly(pk)
    created = created or int(time.time())
    serial = json.dumps([0, pub, created, kind, tags, content], separators=(",", ":"), ensure_ascii=False)
    eid = hashlib.sha256(serial.encode("utf-8")).hexdigest()
    sig = pk.sign_schnorr(bytes.fromhex(eid)).hex()
    return {"id": eid, "pubkey": pub, "created_at": created, "kind": kind, "tags": tags, "content": content, "sig": sig}


def get_channel(chan_file):
    p = os.path.join(AGENT_DIR, chan_file)
    if os.path.exists(p):
        return open(p).read().strip()
    import uuid
    cid = str(uuid.uuid4())
    open(p, "w").write(cid)
    return cid


def upload_avatar(pk, path):
    """Best-effort Blossom (BUD-02/BUD-11) upload -> returns a relay /media URL or None."""
    full = os.path.join(AGENT_DIR, path)
    if not os.path.exists(full):
        return None
    try:
        data = open(full, "rb").read()
        sha = hashlib.sha256(data).hexdigest()
        auth = sign_event(pk, 24242, [["t", "upload"], ["x", sha],
                                      ["expiration", str(int(time.time()) + 300)]], "Upload avatar")
        hdr = "Nostr " + base64.b64encode(json.dumps(auth).encode()).decode()
        req = urllib.request.Request(MEDIA_UPLOAD_URL, data=data, method="PUT",
                                     headers={"Authorization": hdr, "Content-Type": "image/png"})
        with urllib.request.urlopen(req, timeout=20) as r:
            desc = json.loads(r.read())
        url = desc.get("url") or f"http://{RELAY_HOST}/media/{sha}.png"
        return url
    except Exception as e:
        print("avatar upload failed:", e, flush=True)
        return f"http://{RELAY_HOST}/media/{hashlib.sha256(open(full,'rb').read()).hexdigest()}.png"


def call_litellm(model, system, text):
    body = json.dumps({"model": model, "messages": [
        {"role": "system", "content": system}, {"role": "user", "content": text}],
        "max_tokens": 500, "temperature": 0.6}).encode()
    req = urllib.request.Request(LITELLM_URL, data=body, method="POST",
        headers={"Authorization": f"Bearer {LITELLM_KEY}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())["choices"][0]["message"]["content"].strip()


def call_council(council_channel, text, thread_ts=""):
    """POST to the council with a stable per-channel thread key and bounded retry.

    thread_ts scopes the server-owned conversation (CONTEXT_SPEC _conv_key.thread) so each
    Buzz channel keeps its OWN thread. Retries transient failures (the injected-worker-auth
    401 seen in KAI-1020, plus 5xx / connection resets); raises BackendError only when every
    attempt is exhausted, so the turn is never silently dropped and the error never becomes
    KAI's reply.
    """
    body = json.dumps({"channel": council_channel, "message": text,
                       "user_id": "leo", "thread_ts": thread_ts,
                       "trigger_source": "webhook:buzz-eval"}).encode()
    basic = base64.b64encode(f"{WEB_USER}:{WEB_PW}".encode()).decode()
    attempts = len(RETRY_BACKOFF) + 1
    last = None
    for i in range(attempts):
        try:
            req = urllib.request.Request(COUNCIL_URL, data=body, method="POST",
                headers={"Authorization": f"Basic {basic}", "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=180) as r:
                return json.loads(r.read()).get("reply", "(no reply)")
        except urllib.error.HTTPError as e:
            last = e
            if e.code not in RETRY_STATUSES or i == attempts - 1:
                raise BackendError(f"HTTP {e.code} after {i + 1} attempt(s)") from e
        except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
            last = e
            if i == attempts - 1:
                raise BackendError(f"{type(e).__name__} after {i + 1} attempt(s)") from e
        time.sleep(RETRY_BACKOFF[i])
    raise BackendError(f"unreachable retry exhaustion: {last}")  # defensive; loop always returns/raises


def backend_reply(cfg, text, thread_ts=""):
    if cfg["backend"] == "council":
        msg = text
        cf = cfg.get("context_file")
        if cf:
            try:
                brief = open(os.path.join(AGENT_DIR, cf)).read()
                msg = (
                    "[PROJECT CONTEXT — You are KAI, Leo's thinking partner brainstorming "
                    "The Reclamation Project with him. The full brief is below; treat it as the "
                    "working context for this whole conversation. Build on the ongoing thread, "
                    "be substantive, honest, and specific to Leo — not generic. Your role (per the "
                    "brief) is to help Leo hear Leo more clearly, never to optimize or dictate.]\n\n"
                    "<reclamation_brief>\n" + brief + "\n</reclamation_brief>\n\n"
                    "Leo's message: " + text
                )
            except Exception:
                pass
        return call_council(cfg["council_channel"], msg, thread_ts=thread_ts)
    return call_litellm(cfg["model"], cfg["system"], text)


async def authenticate(ws, pk):
    async for raw in ws:
        m = json.loads(raw)
        if m[0] == "AUTH":
            await ws.send(json.dumps(["AUTH", sign_event(pk, 22242, [["relay", RELAY], ["challenge", m[1]]], "")]))
        elif m[0] == "OK":
            return


async def run_agent(cfg):
    pk = load_or_create_key(cfg["key"])
    cid = get_channel(cfg["chan_file"])
    me = xonly(pk)
    join = cfg.get("join")  # join a channel we don't own (a shared room): subscribe + reply only
    avatar = None if join else await asyncio.to_thread(upload_avatar, pk, cfg["avatar"])
    log(cfg["name"], "pubkey", me, "channel", cid, "connect", CONNECT_URL, "tag", RELAY)
    async with websockets.connect(CONNECT_URL, max_size=2 ** 20) as ws:
        await authenticate(ws, pk)
        if not join:
            # ensure private channel first (client-chosen UUID via h-tag; idempotent)
            await ws.send(json.dumps(["EVENT", sign_event(pk, 9007,
                [["h", cid], ["name", cfg["chan_name"]], ["visibility", "private"], ["about", cfg["about"]]], "")]))
            await asyncio.sleep(1.0)  # let the channel commit before member/profile writes
            # profile (kind:0): name + avatar
            prof = {"name": cfg["name"], "display_name": cfg["name"], "about": cfg["about"]}
            if avatar:
                prof["picture"] = avatar
            await ws.send(json.dumps(["EVENT", sign_event(pk, 0, [], json.dumps(prof))]))
            # add Leo as a member
            await ws.send(json.dumps(["EVENT", sign_event(pk, 9000, [["h", cid], ["p", LEO_PUBKEY], ["role", "member"]], "")]))
        # subscribe to live human messages
        await ws.send(json.dumps(["REQ", "sub", {"kinds": [9], "#h": [cid], "since": int(time.time())}]))
        log(cfg["name"], "online — listening")
        seen = set()
        async for raw in ws:
            m = json.loads(raw)
            if m[0] == "AUTH":
                await ws.send(json.dumps(["AUTH", sign_event(pk, 22242, [["relay", RELAY], ["challenge", m[1]]], "")]))
            elif m[0] in ("OK", "CLOSED", "NOTICE"):
                log(cfg["name"], m[0], m[1:])
            elif m[0] == "EVENT" and m[1] == "sub":
                ev = m[2]
                # Only ever answer Leo — never another agent. In a shared room this is
                # what stops two agents (Roads + Sky) from replying to each other forever.
                if ev["kind"] != 9 or ev["pubkey"] != LEO_PUBKEY or ev["id"] in seen:
                    continue
                seen.add(ev["id"])
                # Shared rooms: only answer when this agent is addressed by name.
                if cfg.get("addressed_only"):
                    low = ev.get("content", "").lower()
                    if not any(a in low for a in cfg.get("aliases", [])):
                        continue
                log(cfg["name"], f"<< {ev['pubkey'][:8]}: {ev.get('content','')[:80]}")
                try:
                    reply = await asyncio.to_thread(backend_reply, cfg, ev.get("content", ""), cid)
                except BackendError as e:
                    # KAI-1020: retries exhausted. Never speak the raw error, never drop the
                    # turn silently — say so honestly and ask Leo to resend so the thread stays whole.
                    log(cfg["name"], f"!! backend failed: {e}")
                    reply = ("I hit a transient backend hiccup and couldn't process that one — "
                             "our thread is intact, so just resend it and I'll pick right up.")
                except Exception as e:
                    log(cfg["name"], f"!! unexpected error: {e}")
                    reply = ("Something went wrong on my end handling that — resend it and I'll "
                             "try again; nothing in our thread was lost.")
                await ws.send(json.dumps(["EVENT", sign_event(pk, 9,
                    [["h", cid], ["e", ev["id"]], ["p", ev["pubkey"]]], reply)]))
                log(cfg["name"], f">> {reply[:100]}")


async def main():
    name = sys.argv[1] if len(sys.argv) > 1 else None
    cfgs = [c for c in AGENTS if name is None or c["name"].lower() == name.lower()]
    if not cfgs:
        print("no agent named", name, "— options:", [c["name"] for c in AGENTS]); return
    await asyncio.gather(*(run_agent(c) for c in cfgs))


if __name__ == "__main__":
    asyncio.run(main())
