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

KAI-1142 self-healing (2026-08-18):
  * ROOT CAUSE of a real Leo DM going unanswered while every monitor stayed green: a single
    fire-and-forget `websockets.connect` with no reconnect. When the relay silently stopped
    delivering to the agent's subscription (socket still "up", container `restarts=0`), the
    agent blocked forever in `async for` receiving nothing — no council call, no reply, no
    exit, no restart. run_agent is now a SELF-HEALING loop: explicit ws ping keepalive
    (detects a truly dead socket), a periodic REQ re-arm (recovers a silently-dropped
    subscription without churn), reconnect-with-backoff on any link loss, and BACKFILL on
    reconnect (`since = last_seen - slack`, not `since = now`) so a message that arrived
    during a gap is not lost. A cross-reconnect `seen` set stops backfilled messages from
    being answered twice.
  * The answered-sender and channel-member pubkeys are now per-agent (member_key_file /
    answer_key_file), and a cheap `echo` backend was added, so the KAI-1142 relay round-trip
    probe can drive the REAL agent transport through a dedicated, isolated probe channel
    (KAIProbe) without ever touching Leo's real DMs or calling the council.
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

# KAI-1142: self-healing relay link tunables.
WS_PING_INTERVAL = 20      # ws-level keepalive ping cadence (detects a dead TCP/WS socket)
WS_PING_TIMEOUT = 20       # missed-pong -> ConnectionClosed -> reconnect
IDLE_RESUB_SEC = 50        # after this much inbound silence, re-arm the REQ (recover a
                           # silently-dropped subscription without forcing a reconnect)
BACKFILL_SLACK_SEC = 180   # on reconnect, look back this far so a gap message is not lost
RECONNECT_BACKOFF_SEC = 3  # pause before reconnecting (prevents a hot loop on a hard error)
MAX_CLOSED_STRIKES = 3     # consecutive relay CLOSEDs before forcing a full reconnect (fresh AUTH)
PENDING_ACK_TIMEOUT_SEC = 30  # wait this long for the relay's OK on a reply before assuming it
                              # landed — bounds re-answering if a relay never acks (belt & braces)
# Where the always-on agents stamp a liveness heartbeat (container vault bind mount). meta_monitor
# watches KAI's file so KAI's OWN loop being alive is monitored directly — the round-trip probe
# exercises a sibling identity, so this closes the "green probe, dead KAI subscription" gap.
AGENT_HEARTBEAT_DIR = os.environ.get("BUZZ_AGENT_HEARTBEAT_DIR", "/vault/00_System")

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
        # KAI-1142: repointed to a NEW server identity (kai_dm.key) on a FRESH channel
        # (kai_dm_channel.txt). The prior identity/channel (kai.key on the archived "kai"
        # channel, c7caaeee, archived 2026-08-04) was dead, and Leo's app had drifted to a
        # desktop-app-bound "Kai" (fbbccadd) that only answers while his laptop is open — the
        # real cause of the unanswered DM. This is the ALWAYS-ON, council-backed "Kai" DM Leo
        # re-adds as a contact. kai.key stays reserved for the separate approvals poller.
        "name": "KAI", "key": "kai_dm.key", "chan_file": "kai_dm_channel.txt",
        "chan_name": "kai", "about": "KAI — your assistant. Real orchestrator, full context.",
        "avatar": "kai_avatar.png", "backend": "council", "council_channel": "kai",
        "heartbeat": True,  # meta_monitor watches this agent's own loop liveness (KAI-1142)
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
    {
        # KAI-1142: internal round-trip liveness probe. NOT a real advisor — a dedicated,
        # isolated private channel whose only member/answered-sender is a synthetic probe
        # client (kaiprobe_client.key), replying via the cheap `echo` backend (no council
        # call). Exercises the EXACT relay->agent->relay transport whose silent death left a
        # real Leo DM unanswered, without touching Leo's DMs or spending council tokens.
        "name": "KAIProbe", "key": "kaiprobe.key", "chan_file": "kaiprobe_channel.txt",
        "chan_name": "kaiprobe", "about": "Internal round-trip liveness probe (not a real advisor).",
        "avatar": None, "backend": "echo", "council_channel": "kai",
        "member_key_file": "kaiprobe_client.key", "answer_key_file": "kaiprobe_client.key",
        "probe": True,
    },
]


def log(name, *a):
    print(f"[{time.strftime('%H:%M:%S')}][{name}]", *a, flush=True)


def _heartbeat(cfg):
    """Best-effort liveness stamp for agents that opt in (`heartbeat: True`). Written once per
    recv-loop iteration — on every message and every idle cycle — so meta_monitor can assert the
    agent's own event loop is alive independent of the round-trip probe. Never raises."""
    if not cfg.get("heartbeat"):
        return
    try:
        os.makedirs(AGENT_HEARTBEAT_DIR, exist_ok=True)
        path = os.path.join(AGENT_HEARTBEAT_DIR, f"buzz_agent_{cfg['name']}_heartbeat")
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            f.write(str(int(time.time())))
        os.replace(tmp, path)
    except Exception:
        pass


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
    if cfg["backend"] == "echo":
        # KAI-1142: round-trip liveness echo. Deliberately NO council call — this backend
        # exists only to prove the relay->agent->relay transport is alive. Echo the client's
        # message back verbatim so the probe can confirm its own nonce round-tripped (and so
        # a stale/duplicate reply is rejected).
        return f"__relay_probe_ack__ {text.strip()}"
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


async def _serve(cfg, ws, pk, cid, member_pub, answer_pub, avatar, state):
    """One connected session: (re)establish the channel/subscription and pump events until
    the link drops. Raises on any link loss so the caller reconnects. `state` carries the
    cross-reconnect dedup set + last-seen watermark so backfill never re-answers a message."""
    join = cfg.get("join")

    async def ensure():
        """Idempotently (re)assert channel ownership, profile, and the served member. Safe to
        repeat: on a brand-new channel the first attempt can race the create commit and be
        rejected ('restricted: not a channel member'), so this is also re-run on a CLOSED sub."""
        if join:
            return
        await ws.send(json.dumps(["EVENT", sign_event(pk, 9007,
            [["h", cid], ["name", cfg["chan_name"]], ["visibility", "private"], ["about", cfg["about"]]], "")]))
        await asyncio.sleep(1.5)  # let the channel commit before member/profile writes
        prof = {"name": cfg["name"], "display_name": cfg["name"], "about": cfg["about"]}
        if avatar:
            prof["picture"] = avatar
        await ws.send(json.dumps(["EVENT", sign_event(pk, 0, [], json.dumps(prof))]))
        # add the served human (Leo, or the synthetic probe client) as a member
        await ws.send(json.dumps(["EVENT", sign_event(pk, 9000, [["h", cid], ["p", member_pub], ["role", "member"]], "")]))

    async def resub():
        # Cold start: only take live messages (since=now). Otherwise look back so a message
        # that arrived while we were disconnected/unsubscribed is not lost (the silent drop).
        since = int(time.time()) if state["first"] else max(0, state["last_seen"] - BACKFILL_SLACK_SEC)
        await ws.send(json.dumps(["REQ", "sub", {"kinds": [9], "#h": [cid], "since": since}]))
        return since

    await authenticate(ws, pk)
    await ensure()
    since = await resub()
    log(cfg["name"], "online — listening" if state["first"] else f"reconnected — backfill since={since}")
    state["first"] = False
    seen = state["seen"]
    closed_strikes = 0
    # reply_event_id -> (inbound_id, inbound_created, sent_ts). An inbound message is marked
    # answered ONLY when the relay ACKs our reply (OK <reply_id> true) — a local ws.send success
    # only means the frame left this process, not that the relay persisted it. Un-acked replies
    # are swept to answered after a bounded timeout so a silent/ack-less relay can't loop us.
    pending = {}

    def _mark_answered(inbound_id, created):
        seen.add(inbound_id)
        state["last_seen"] = max(state["last_seen"], created)

    while True:
        _heartbeat(cfg)  # stamp liveness every iteration (message or idle) — meta_monitor watches it
        # Bound the un-acked window: if the relay never OK'd a reply, assume it landed rather than
        # re-answer forever. Timeout < IDLE_RESUB_SEC so a sweep always precedes an idle replay.
        if pending:
            now = int(time.time())
            for rid in [r for r, (_, _, ts) in pending.items() if now - ts > PENDING_ACK_TIMEOUT_SEC]:
                iid, created, _ = pending.pop(rid)
                log(cfg["name"], f"~~ no relay OK for reply {rid[:8]} in {PENDING_ACK_TIMEOUT_SEC}s — assuming delivered")
                _mark_answered(iid, created)
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=IDLE_RESUB_SEC)
        except asyncio.TimeoutError:
            # Inbound silence. A live socket is proven by ws ping/pong; a SILENTLY-DROPPED
            # subscription (socket up, no delivery) is the KAI-1142 signature, so re-arm the
            # REQ. Cheap + idempotent (replayed events are deduped by `seen`); no reconnect
            # churn on a healthy-but-idle channel.
            await resub()
            continue
        m = json.loads(raw)
        if m[0] == "AUTH":
            await ws.send(json.dumps(["AUTH", sign_event(pk, 22242, [["relay", RELAY], ["challenge", m[1]]], "")]))
        elif m[0] == "CLOSED":
            log(cfg["name"], "CLOSED", m[1:])
            # A fresh channel can reject the first subscription because the create/owner-member
            # row has not committed yet. Re-assert ownership+membership, then re-arm the sub so
            # provisioning self-heals within seconds. But if the relay keeps rejecting us (stale
            # auth/membership), stop re-arming in place after a few strikes and force a full
            # reconnect (fresh AUTH + ensure) rather than looping here forever.
            closed_strikes += 1
            if closed_strikes >= MAX_CLOSED_STRIKES:
                raise ConnectionError(f"repeated CLOSED x{closed_strikes} — forcing reconnect")
            await asyncio.sleep(2)
            await ensure()
            await resub()
        elif m[0] == "OK":
            closed_strikes = 0
            # ["OK", <event_id>, <accepted bool>, <msg>]. Correlate against a pending reply:
            # only on relay-accepted do we mark the inbound answered. A rejected reply leaves the
            # inbound unseen so the reconnect/idle path re-answers it.
            rid = m[1] if len(m) > 1 else None
            if rid in pending:
                iid, created, _ = pending.pop(rid)
                if len(m) > 2 and m[2]:
                    _mark_answered(iid, created)
                else:
                    log(cfg["name"], "reply REJECTED by relay — will retry", m[1:])
            else:
                log(cfg["name"], "OK", m[1:])
        elif m[0] == "NOTICE":
            log(cfg["name"], "NOTICE", m[1:])
        elif m[0] == "EVENT" and m[1] == "sub":
            closed_strikes = 0
            ev = m[2]
            # Only ever answer the served human — never another agent. In a shared room this
            # is what stops two agents (Roads + Sky) from replying to each other forever.
            if ev.get("kind") != 9 or ev.get("pubkey") != answer_pub or ev["id"] in seen:
                continue
            try:
                created = int(ev.get("created_at", state["last_seen"]))
            except (TypeError, ValueError):
                created = state["last_seen"]
            # Shared rooms: only answer when this agent is addressed by name. A message we will
            # NEVER answer is marked seen immediately (intentional non-answer) and advances the
            # watermark so it isn't replayed.
            if cfg.get("addressed_only"):
                low = ev.get("content", "").lower()
                if not any(a in low for a in cfg.get("aliases", [])):
                    _mark_answered(ev["id"], created)
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
            # Publish the reply, then mark the inbound answered ONLY when the relay ACKs it (the
            # OK handler above). We register the pending correlation BEFORE the send so an OK that
            # races back is never missed. If the send raises (link died mid-reply), the inbound is
            # neither pending nor seen, so reconnect backfill re-delivers and we answer — never a
            # silently lost reply (the exact KAI-1142 failure). The recv loop is sequential, so no
            # replay is processed while a message is mid-flight.
            reply_ev = sign_event(pk, 9, [["h", cid], ["e", ev["id"]], ["p", ev["pubkey"]]], reply)
            pending[reply_ev["id"]] = (ev["id"], created, int(time.time()))
            try:
                await ws.send(json.dumps(["EVENT", reply_ev]))
            except Exception:
                pending.pop(reply_ev["id"], None)  # send failed -> not answered; let backfill retry
                raise
            log(cfg["name"], f">> {reply[:100]}")


async def run_agent(cfg):
    pk = load_or_create_key(cfg["key"])
    cid = get_channel(cfg["chan_file"])
    me = xonly(pk)
    join = cfg.get("join")  # join a channel we don't own (a shared room): subscribe + reply only
    # Which human this agent serves: Leo by default; the probe serves its synthetic client.
    member_pub = xonly(load_or_create_key(cfg["member_key_file"])) if cfg.get("member_key_file") else LEO_PUBKEY
    answer_pub = xonly(load_or_create_key(cfg["answer_key_file"])) if cfg.get("answer_key_file") else LEO_PUBKEY
    avatar = None
    if not join and cfg.get("avatar"):
        avatar = await asyncio.to_thread(upload_avatar, pk, cfg["avatar"])
    log(cfg["name"], "pubkey", me, "channel", cid, "connect", CONNECT_URL, "tag", RELAY,
        "answers", answer_pub[:8])
    # Cross-reconnect state: dedup set + last-seen watermark survive reconnects so backfill
    # never re-answers a message already handled, and never loses one that arrived in a gap.
    state = {"seen": set(), "last_seen": int(time.time()), "first": True}
    while True:  # KAI-1142: self-healing reconnect loop — a dropped/half-open/silently-idle
                 # relay link now reconnects + backfills instead of dying quietly or blocking
                 # forever on a dead socket (the root cause of the unanswered Leo DM).
        try:
            async with websockets.connect(CONNECT_URL, max_size=2 ** 20,
                                          ping_interval=WS_PING_INTERVAL,
                                          ping_timeout=WS_PING_TIMEOUT,
                                          close_timeout=10) as ws:
                await _serve(cfg, ws, pk, cid, member_pub, answer_pub, avatar, state)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log(cfg["name"], f"~~ link lost ({type(e).__name__}: {e}) — reconnecting in {RECONNECT_BACKOFF_SEC}s")
            await asyncio.sleep(RECONNECT_BACKOFF_SEC)


async def main():
    name = sys.argv[1] if len(sys.argv) > 1 else None
    cfgs = [c for c in AGENTS if name is None or c["name"].lower() == name.lower()]
    if not cfgs:
        print("no agent named", name, "— options:", [c["name"] for c in AGENTS]); return
    await asyncio.gather(*(run_agent(c) for c in cfgs))


if __name__ == "__main__":
    asyncio.run(main())
