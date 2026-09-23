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
# #devops channel (Leo 2026-09-13): actionable system messages route here via the
# notify() gateway (audience="devops"), which appends to DEVOPS_QUEUE. This poller
# drains that queue to the channel + a DM push. Read-only surface — no replies parsed.
DEVOPS_CHAN_FILE = "devops_channel.txt"
DEVOPS_QUEUE = os.environ.get("KAI_DEVOPS_QUEUE", "/vault/00_System/devops_queue.jsonl")
# KAI-1449 (Leo 2026-09-15): approval-LIFECYCLE notices (e.g. a tap-approved apply
# landing) route to THIS channel (#kai-approvals) — where Leo tapped — via the
# notify() gateway (audience="approvals") -> APPROVALS_QUEUE, drained below. Keeps
# #devops focused on infra/system alerts only.
APPROVALS_QUEUE = os.environ.get("KAI_APPROVALS_QUEUE", "/vault/00_System/approvals_notice_queue.jsonl")
# KAI-1314: the scheduled morning brief lands here (notify gateway, audience="brief")
# and is DM'd to Leo as a personal digest — DM-only, never a channel post or Telegram.
BRIEF_QUEUE = os.environ.get("KAI_BRIEF_QUEUE", "/vault/00_System/brief_queue.jsonl")
# nginx at :3001 strips ONE /council/ prefix, so gate routes (/council/gate/...) need
# the doubled prefix. The #kai bridge reaches /council/message the same way.
COUNCIL_BASE  = os.environ.get("BUZZ_COUNCIL_BASE", "http://localhost:3001/council/council")
# Worker-api base (mode-lock unlock approvals). nginx njs injects the worker
# credential for /api/* on WEB basic auth — buzz_approve does NOT hold kai_worker_auth.
WORKER_BASE   = os.environ.get("BUZZ_WORKER_BASE", "http://kai-web/api")
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
    r"^\s*(approve|approved|allow|yes|ok|session|1h|reject|rejected|no|deny)\b\s*([\w-]{6,}|\d{1,3})?\s*(?::\s*(.*))?\s*$", re.I)
# Gate approve/reject is binary; allow/session on a gate count as approve.
_APPROVE_VERBS = {"approve", "approved", "allow", "yes", "ok", "session", "1h"}
# Mode-lock unlock verbs map to the three actions request_approval understands.
_ML_ACTION = {"allow": "once", "approve": "once", "approved": "once", "yes": "once", "ok": "once",
              "session": "session", "1h": "session",
              "deny": "deny", "reject": "deny", "rejected": "deny", "no": "deny"}
# On-demand re-surface: Leo asks KAI to re-send everything still pending (for when he
# missed the prompt — meeting, plane, etc.). Gates never expire, so this always works.
_LIST_RE = re.compile(r"^\s*(pending|list|resend|approvals?|status|what'?s? pending|any approvals?)\s*\??\s*$", re.I)


# KAI-1449: the web password must be read FRESH on each request, not cached at
# import. agents_bridge reads WEB_PW once at import; a credential rotation then
# silently 401'd every poll of /api/mode_lock/pending until a restart — a 2-day
# approvals blackout (2026-09-15, nothing surfaced in #kai-approvals). Re-reading
# the small secret file each call (~5s cadence) makes a rotation self-heal on the
# next poll. Falls back to the import-time value only if the file is transiently
# unreadable, so a fumbled read never hard-fails auth.
_WEB_PW_FILE = os.path.expanduser(os.environ.get("KAI_WEB_PW_FILE", "~/kai-system/secrets/kai_web_password.txt"))


def _web_pw() -> str:
    try:
        return open(_WEB_PW_FILE).read().strip()
    except OSError:
        return ab.WEB_PW


def _basic_auth() -> str:
    return base64.b64encode(f"{ab.WEB_USER}:{_web_pw()}".encode()).decode()


def _council_get(path: str) -> dict:
    req = urllib.request.Request(f"{COUNCIL_BASE}{path}",
        headers={"Authorization": f"Basic {_basic_auth()}"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def _gate_resolve_secret() -> str:
    """C2 [SEC] 453162cc — dedicated gate-resolve credential (compose file-secret)."""
    try:
        with open("/run/secrets/gate_resolve_secret") as _f:
            return _f.read().strip()
    except OSError:
        return ""


def _council_resolve(gate_id: str, approved: bool, notes: str) -> dict:
    body = json.dumps({"approved": approved, "notes": notes, "resolver": "leo"}).encode()
    req = urllib.request.Request(f"{COUNCIL_BASE}/gate/{gate_id}/resolve", data=body, method="POST",
        headers={"Authorization": f"Basic {_basic_auth()}", "Content-Type": "application/json",
                 "X-KAI-Gate-Resolve": _gate_resolve_secret()})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def _worker_get(path: str) -> dict:
    req = urllib.request.Request(f"{WORKER_BASE}{path}",
        headers={"Authorization": f"Basic {_basic_auth()}"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def _modelock_resolve(request_id: str, action: str) -> dict:
    """Resolve a mode-lock unlock the same channel-neutral way the Telegram poll loop
    does — POST the decision to the docker-internal action endpoint. action is one of
    once | session | deny (already mapped from Leo's verb)."""
    body = json.dumps({"request_id": request_id, "action": action, "user": "leo"}).encode()
    req = urllib.request.Request(f"{WORKER_BASE}/mode_lock/telegram_action_internal",
        data=body, method="POST",
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
    """-> (token_or_None, verb: str, reason: str) or None if not a decision. token may
    be omitted; the caller binds the item (gate OR mode-lock unlock) from the reply
    e-tag or the single open prompt. Gate approve/reject derives `verb in _APPROVE_VERBS`;
    mode-lock maps `verb` through _ML_ACTION."""
    m = _DECISION_RE.match(content or "")
    if not m:
        return None
    return (m.group(2) or None), m.group(1).lower(), (m.group(3) or "").strip()


# ── Batched approval cards (P-3: one card, N items, resolved per-item) ─────────
# When multiple gates are pending, aggregate them into ONE Buzz card instead of N
# separate cards buried in the DM thread. Each item is addressed by its ordinal —
# `approve 2` / `reject 3: <reason>` — the short handle Leo actually replies with;
# the full id stays available as the power path. Reuses the code-composed Subject
# line of each gate summary (never model-authored — see routes_council_gate.py).

def _subject_of(summary: str) -> str:
    """First 'Subject:' line of a code-composed gate card; else a short fallback."""
    for ln in (summary or "").splitlines():
        s = ln.strip()
        if s.lower().startswith("subject:"):
            return s.split(":", 1)[1].strip() or "(no subject)"
    body = (summary or "").strip()
    return (body.splitlines()[0].strip()[:80] if body else "") or "(no subject)"


def _batch_order(pending) -> list:
    """Stable gid order for a batch card, matching the ordinals shown to Leo."""
    return [g.get("gate_id") for g in pending if g.get("gate_id")]


def compose_batch_card(pending, prefix: str = "") -> str:
    """One card, N items. The ordinal is the reply handle; the id is shown for the
    power path. Aggregates what were N separate cards into a single glanceable card."""
    n = len(pending)
    lines = [f"{prefix}\U0001f510 {n} APPROVALS PENDING \u2014 reply per item"]
    for i, g in enumerate(pending, 1):
        gid = g.get("gate_id", "?")
        lines.append(f"\n{i}. [{g.get('gate_type', 'gate')}] {_subject_of(g.get('summary', ''))}")
        lines.append(f"   id {gid[:8]} \u00b7 reply `approve {i}` or `reject {i}: <reason>`")
    lines.append("\nReply `approve <n>` / `reject <n>: <reason>` per item.")
    return "\n".join(lines)


# ── DM push pointer (bug 5de64f3f) ─────────────────────────────────────────────
# A kind-9 card posted to the kai-approvals CHANNEL does NOT push-notify — it scrolls
# out of view unseen (a go-live gate nearly stranded this way, MIG-3). A NIP-17 DM,
# by contrast, lands on ANY Buzz app (phone/mac/iPad/PC) whether Leo's Mac is open or
# asleep — the same surface the advisor DMs use. So every time a card is posted or
# re-nudged we also DM Leo this short pointer; he resolves in the channel as before.
# Telegram stays the secondary lifeline (only when Buzz/the poller is down).
def compose_dm_pointer(total: int) -> str:
    """One-line push pointer DM'd to Leo when approval cards are (re)posted."""
    n = max(0, int(total))
    return (f"\U0001f510 {n} approval{'' if n == 1 else 's'} pending on Buzz — "
            "open #kai-approvals to approve/reject.")


def _drain_queue(path: str, label: str) -> list:
    """Atomically claim queued records: rename the queue aside, read it, remove it.
    New appends land in a fresh queue for the next cycle so nothing is lost mid-drain.
    Recovers a prior interrupted drain first. Returns [] if empty; never raises."""
    work = path + ".draining"
    recs = []
    try:
        if not os.path.exists(work):
            if not os.path.exists(path):
                return []
            os.rename(path, work)
        with open(work) as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        recs.append(json.loads(line))
                    except Exception:
                        continue
        os.remove(work)
    except Exception as e:
        ab.log("approvals", f"!! {label} drain error: {type(e).__name__}: {e}")
    return recs


def _drain_devops_queue() -> list:
    return _drain_queue(DEVOPS_QUEUE, "devops")


def _drain_approvals_queue() -> list:
    return _drain_queue(APPROVALS_QUEUE, "approvals")


def _drain_brief_queue() -> list:
    return _drain_queue(BRIEF_QUEUE, "brief")


async def run():
    pk = ab.load_or_create_key(KEY_FILE)
    cid = ab.get_channel(CHAN_FILE)
    devops_cid = ab.get_channel(DEVOPS_CHAN_FILE)
    me = ab.xonly(pk)
    send_lock = asyncio.Lock()
    prompt_map: dict[str, str] = {}   # prompt event id -> gate id (reply binding)
    open_gates: set[str] = set()      # prompted + still-unresolved gate ids
    prompted: set[str] = set()        # gate ids we've ever prompted (no double-prompt)
    kind_of: dict[str, str] = {}      # id -> 'gate' | 'modelock' (routes the reply)
    batch = {"sig": None, "last": 0.0, "order": []}  # P-3 batched-card state
    ab.log("approvals", "pubkey", me, "channel", cid, "council", COUNCIL_BASE)

    async def send(text) -> dict:
        ev = ab.sign_event(pk, 9, [["h", cid]], text)
        async with send_lock:  # websockets: at most one concurrent send()
            await ws.send(json.dumps(["EVENT", ev]))
        return ev

    async def send_devops(text) -> dict:
        """Post a system message to the #devops channel (separate from approvals)."""
        ev = ab.sign_event(pk, 9, [["h", devops_cid]], text)
        async with send_lock:
            await ws.send(json.dumps(["EVENT", ev]))
        return ev

    async def prompt_gate(g, prefix=""):
        gid = g.get("gate_id")
        ev = await send(f"{prefix}🔐 APPROVAL — {g.get('gate_type', 'gate')}\n"
                        f"{g.get('summary', '')}\n\n"
                        f"Reply `approve` or `reject: <reason>` (or `approve {gid}`).")
        prompt_map[ev["id"]] = f"gate:{gid}"
        open_gates.add(gid)
        kind_of[gid] = "gate"
        return gid

    async def prompt_unlock(e, prefix=""):
        rid = e.get("request_id")
        ev = await send(f"{prefix}🔓 UNLOCK REQUEST — {e.get('tool', '?')} → {e.get('target') or '(n/a)'}\n"
                        f"{e.get('reason', '')}\n\n"
                        f"Reply `allow` (once), `session` (90m), or `deny` (or `allow {rid}`).")
        prompt_map[ev["id"]] = f"modelock:{rid}"
        open_gates.add(rid)
        kind_of[rid] = "modelock"
        return rid

    async def prompt_batch(pending, prefix=""):
        """Post one batched card for N pending gates; record the ordinal order so a
        `approve <n>` reply binds to the right gate. The e-tag is deliberately NOT
        used for a batch (it is ambiguous across N items) — the ordinal disambiguates."""
        await send(compose_batch_card(pending, prefix))
        order = _batch_order(pending)
        batch["order"] = order
        for gid in order:
            open_gates.add(gid); kind_of[gid] = "gate"
        return order

    # NIP-17 DM push to Leo — the surface that actually notifies (bug 5de64f3f). Sent
    # AS the KAI DM identity (kai_dm.key) so it lands in Leo's existing KAI direct line
    # on any Buzz app. Best-effort: a DM failure NEVER blocks the channel card or the
    # Telegram-dead lifeline (nostr_sdk/key loaded lazily so --selftest stays offline).
    _dm_send = None
    try:
        from nostr_sdk import Keys, PublicKey
        _dm_keys = Keys.parse(open(os.path.join(ab.AGENT_DIR, "kai_dm.key")).read().strip())
        _leo_pub = PublicKey.parse(ab.LEO_PUBKEY)

        async def _dm_send(text):  # noqa: F811 — bound only when deps/key are present
            # now-stamped NIP-59 wrap (shared helper) so the relay accepts it (5de64f3f)
            ev = ab.build_giftwrap_now(_dm_keys, _leo_pub, text)
            async with send_lock:
                await ws.send(json.dumps(["EVENT", ev]))
        ab.log("approvals", "DM push enabled — NIP-17 pointer to Leo")
    except Exception as e:
        ab.log("approvals", f"!! DM push unavailable ({type(e).__name__}: {e}) — channel-only")

    async def notify_leo(total: int) -> None:
        """Push a one-line DM pointer so a (re)posted card actually surfaces on Leo's
        phone/mac/iPad/PC. Best-effort — the channel card already went out regardless."""
        if _dm_send and total > 0:
            try:
                await _dm_send(compose_dm_pointer(total))
                ab.log("approvals", f">> DM push sent ({total} pending)")
            except Exception as e:
                ab.log("approvals", f"!! DM push failed: {e}")

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
                posted = False        # any card newly posted / re-nudged this cycle → DM push
                gate_count = ml_count = 0
                try:
                    data = await asyncio.to_thread(_council_get, "/gate/pending")
                    pend = [g for g in data.get("pending", [])
                            if g.get("gate_id") and not (_ONLY_GATE and g.get("gate_id") != _ONLY_GATE)]
                    gate_count = len(pend)
                    live = {g["gate_id"] for g in pend}
                    if len(pend) >= 2:
                        # P-3: one card, N items — aggregate instead of N separate cards.
                        sig = frozenset(live)
                        due = (time.time() - batch["last"]) >= _RENUDGE_SECONDS
                        if sig != batch["sig"] or due:
                            prefix = "🔁 (still waiting on you) " if (due and sig == batch["sig"]) else ""
                            await prompt_batch(pend, prefix=prefix)
                            batch["sig"] = sig; batch["last"] = time.time()
                            posted = True
                            ab.log("approvals", f">> batch card sent ({len(pend)} items)")
                        for gid in live:
                            last_prompt.pop(gid, None)   # batch owns these ids
                    else:
                        batch["sig"] = None; batch["order"] = []
                        for g in pend:
                            gid = g["gate_id"]
                            last = last_prompt.get(gid)
                            if last is None:
                                await prompt_gate(g)
                                last_prompt[gid] = time.time()
                                posted = True
                                ab.log("approvals", f">> prompt sent for {gid}")
                            elif (time.time() - last) >= _RENUDGE_SECONDS:
                                # Re-nudge on Buzz first — Telegram is only the Buzz-dead lifeline.
                                await prompt_gate(g, prefix="🔁 (still waiting on you) ")
                                last_prompt[gid] = time.time()
                                posted = True
                                ab.log("approvals", f">> re-nudged {gid}")
                    for gid in [k for k in last_prompt
                                if kind_of.get(k, "gate") == "gate" and k not in live]:
                        last_prompt.pop(gid, None)  # gate resolved — forget it
                except Exception as e:
                    ab.log("approvals", f"!! poll error: {e}")

                # Mode-lock unlock requests (worker-api) — Buzz is the PRIMARY unlock
                # approval surface (KAI-1002 item 2). Suppressed during a scoped gate test.
                if not _ONLY_GATE:
                    try:
                        ml = await asyncio.to_thread(_worker_get, "/mode_lock/pending")
                        ml_live = set()
                        for item in ml.get("pending", []):
                            rid = item.get("request_id")
                            if not rid:
                                continue
                            ml_live.add(rid)
                            kind_of[rid] = "modelock"
                            last = last_prompt.get(rid)
                            if last is None:
                                await prompt_unlock(item)
                                last_prompt[rid] = time.time()
                                posted = True
                                ab.log("approvals", f">> unlock prompt sent for {rid}")
                            # NO re-nudge for unlocks (bug: in-session `YES` arms the LOCAL
                            # window but never resolves the worker request, so a re-nudge nags
                            # about an unlock already granted — the exact "(still waiting)" loop
                            # Leo hit). Prompt ONCE; the council's 30-min Telegram escalation
                            # covers a genuinely-away miss. Proper fix (YES → resolve the request)
                            # needs the protected UserPromptSubmit hook = Leo's hand (tracked).
                        ml_count = len(ml_live)
                        for rid in [k for k in last_prompt
                                    if kind_of.get(k) == "modelock" and k not in ml_live]:
                            last_prompt.pop(rid, None); kind_of.pop(rid, None)
                    except Exception as ex:
                        ab.log("approvals", f"!! mode_lock poll error: {ex}")

                # #devops channel (Leo 2026-09-13): drain queued system messages (from the
                # notify() gateway, audience="devops") to the channel + one DM push. These
                # are read-only alerts that need Leo's action — no reply parsing.
                try:
                    devops_recs = await asyncio.to_thread(_drain_devops_queue)
                    for r in devops_recs:
                        title = r.get("title") or r.get("kind") or "system"
                        body = r.get("text") or ""
                        await send_devops(f"🛠️ {title}\n{body}" if body and body != title else f"🛠️ {title}")
                        ab.log("approvals", f">> devops msg posted ({r.get('source','?')})")
                    if devops_recs and _dm_send:
                        n = len(devops_recs)
                        try:
                            await _dm_send(f"🛠️ {n} DevOps item{'s' if n != 1 else ''} need your attention — see #devops.")
                            ab.log("approvals", f">> devops DM push sent ({n})")
                        except Exception as e:
                            ab.log("approvals", f"!! devops DM push failed: {e}")
                except Exception as e:
                    ab.log("approvals", f"!! devops post error: {type(e).__name__}: {e}")

                # #kai-approvals lifecycle notices (KAI-1449, Leo 2026-09-15): drain
                # apply-outcome confirmations (notify gateway, audience="approvals")
                # to THIS channel — where Leo tapped — so an approved apply reports
                # back where he acted (not #devops). Read-only; no reply parsing.
                try:
                    appr_recs = await asyncio.to_thread(_drain_approvals_queue)
                    for r in appr_recs:
                        body = r.get("text") or r.get("title") or "approval update"
                        await send(body)
                        ab.log("approvals", f">> approvals notice posted ({r.get('source','?')})")
                except Exception as e:
                    ab.log("approvals", f"!! approvals notice error: {type(e).__name__}: {e}")

                # KAI-1314: scheduled morning brief → a personal Buzz DM to Leo (never
                # Telegram, never a channel post). Drained from the notify() gateway
                # (audience="brief"). DM-only: a morning digest is Leo's alone. Best-effort;
                # if the DM path is down the record is logged as undelivered, never faked.
                try:
                    brief_recs = await asyncio.to_thread(_drain_brief_queue)
                    for r in brief_recs:
                        body = r.get("text") or r.get("title") or "Morning brief"
                        if _dm_send:
                            try:
                                await _dm_send(body)
                                ab.log("approvals", f">> morning brief DM sent ({r.get('source','?')})")
                            except Exception as e:
                                ab.log("approvals", f"!! brief DM failed: {e}")
                        else:
                            ab.log("approvals", "!! brief undelivered — DM push unavailable")
                except Exception as e:
                    ab.log("approvals", f"!! brief drain error: {type(e).__name__}: {e}")

                # Push surface (bug 5de64f3f): a channel card doesn't notify, so whenever a
                # card was posted or re-nudged this cycle, DM Leo one pointer with the TOTAL
                # pending (gates + unlocks). Best-effort; the channel card already went out.
                if posted:
                    await notify_leo(gate_count + ml_count)
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
                    ml_pend = []
                    if not _ONLY_GATE:
                        try:
                            ml_pend = (await asyncio.to_thread(_worker_get, "/mode_lock/pending")).get("pending", [])
                        except Exception:
                            ml_pend = []
                    if _ONLY_GATE:
                        pend = [x for x in pend if x.get("gate_id") == _ONLY_GATE]
                    total = len(pend) + len(ml_pend)
                    if not total:
                        await send("✅ Nothing pending — you're all caught up.")
                    else:
                        await send(f"You have {total} pending approval(s):")
                        if len(pend) >= 2:
                            await prompt_batch(pend, prefix="🔁 (re-sent) ")
                            batch["sig"] = frozenset(x.get("gate_id") for x in pend if x.get("gate_id"))
                            batch["last"] = time.time()
                        else:
                            for g in pend:
                                await prompt_gate(g, prefix="🔁 (re-sent) ")
                        for item in ml_pend:
                            await prompt_unlock(item, prefix="🔁 (re-sent) ")
                    ab.log("approvals", f"re-surfaced {total} pending on request")
                    continue
                decision = parse_decision(content)
                if not decision:
                    continue
                token, verb, reason = decision
                approved = verb in _APPROVE_VERBS
                # Bind against the LIVE pending list (the API is the source of truth) — the
                # in-memory sets are wiped on every container restart, which silently broke a
                # bare `approve`. Priority: explicit id > reply e-tag > single pending. The
                # live set now spans BOTH gates (council) and mode-lock unlocks (worker-api).
                try:
                    live_gate = [x.get("gate_id") for x in
                        (await asyncio.to_thread(_council_get, "/gate/pending")).get("pending", [])
                        if x.get("gate_id")]
                except Exception:
                    live_gate = [g for g in open_gates if kind_of.get(g, "gate") == "gate"]
                live_ml = []
                if not _ONLY_GATE:
                    try:
                        live_ml = [x.get("request_id") for x in
                            (await asyncio.to_thread(_worker_get, "/mode_lock/pending")).get("pending", [])
                            if x.get("request_id")]
                    except Exception:
                        live_ml = [g for g in open_gates if kind_of.get(g) == "modelock"]
                if _ONLY_GATE:
                    live_gate = [g for g in live_gate if g == _ONLY_GATE]
                gate_set, ml_set = set(live_gate), set(live_ml)
                live_all = list(gate_set) + list(ml_set)

                # Resolve the reply to a specific pending item AND its KIND. Route by the
                # BOUND kind (the id's own namespace, or the prompted item's kind via the
                # reply e-tag), never by list membership — so a gate id and a mode-lock
                # request id can never cross-route even if they were to collide.
                bkind = tid = None
                if token:
                    in_g, in_m = token in gate_set, token in ml_set
                    if in_g and in_m:
                        await send(f"`{token}` is ambiguous across a gate and an unlock — "
                                   "resolve at the keyboard.")
                        continue
                    if in_g:
                        bkind, tid = "gate", token
                    elif in_m:
                        bkind, tid = "modelock", token
                # P-3 ordinal handle from a batch card: `approve 2` -> the 2nd item.
                if tid is None and token and token.isdigit() and batch["order"]:
                    idx = int(token)
                    if 1 <= idx <= len(batch["order"]):
                        cand = batch["order"][idx - 1]
                        if cand in gate_set:
                            bkind, tid = "gate", cand
                if tid is None:
                    for t in ev.get("tags", []):
                        if len(t) > 1 and t[0] == "e":
                            raw = prompt_map.get(t[1])
                            if not raw:
                                continue
                            # Namespaced binding: kind travels WITH the id, immune to any
                            # ID-keyed overwrite. "kind:id" — id may itself contain ':'? no
                            # (gate ids are [\w-]; request ids are hex), so partition is safe.
                            k, _, cand = raw.partition(":")
                            if k == "gate" and cand in gate_set:
                                bkind, tid = "gate", cand; break
                            if k == "modelock" and cand in ml_set:
                                bkind, tid = "modelock", cand; break
                if tid is None and len(live_all) == 1:
                    if gate_set:
                        bkind, tid = "gate", next(iter(gate_set))
                    else:
                        bkind, tid = "modelock", next(iter(ml_set))
                if tid is None:
                    if not live_all:
                        await send("✅ Nothing pending to approve — you're all caught up.")
                    else:
                        await send("Which one? " + str(len(live_all)) + " pending: "
                                   + ", ".join("`" + g + "`" for g in live_all)
                                   + " — reply `approve <id>` / `allow <id>`.")
                    continue

                # Mode-lock unlock — Telegram-free resolution (Buzz is primary).
                if bkind == "modelock":
                    action = _ML_ACTION.get(verb, "deny")
                    try:
                        res = await asyncio.to_thread(_modelock_resolve, tid, action)
                        open_gates.discard(tid)
                        st = res.get("status", action)
                        await send(f"🔓 unlock `{tid}` → {st}.")
                        ab.log("approvals", f"resolved mode_lock {tid} action={action} -> {st}")
                    except urllib.error.HTTPError as e:
                        await send(f"⚠️ couldn't resolve unlock `{tid}` (HTTP {e.code}) — it may already be resolved.")
                        ab.log("approvals", f"!! ml resolve {tid} HTTP {e.code}")
                    except Exception as e:
                        ab.log("approvals", f"!! ml resolve {tid} error: {e}")
                    continue

                # Gate approve/reject (council).
                gid = tid
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

    assert parse_decision("approve") == (None, "approve", "")
    assert parse_decision("yes") == (None, "yes", "")
    assert parse_decision("reject: not on brand") == (None, "reject", "not on brand")
    assert parse_decision("approve abc123") == ("abc123", "approve", "")
    assert parse_decision("reject abc123: nope") == ("abc123", "reject", "nope")
    # P-3 batched-card: short ordinal handle + one-card composition
    assert parse_decision("approve 2") == ("2", "approve", "")
    assert parse_decision("reject 3: nope") == ("3", "reject", "nope")
    assert _subject_of("Subject: Deploy X\nChain: a") == "Deploy X"
    assert _subject_of("no subject line here") == "no subject line here"
    _pend = [
        {"gate_id": "abc123def", "gate_type": "dev_gate", "summary": "Subject: Deploy X\nChain: c"},
        {"gate_id": "zzz999yyy", "gate_type": "devops_gate", "summary": "Subject: Rotate Y"},
    ]
    assert _batch_order(_pend) == ["abc123def", "zzz999yyy"]
    _card = compose_batch_card(_pend)
    assert "2 APPROVALS PENDING" in _card
    assert "Deploy X" in _card and "Rotate Y" in _card
    assert "approve 1" in _card and "approve 2" in _card
    assert "abc123de" in _card  # id8 shown for the power path
    # DM push pointer (bug 5de64f3f): the notify surface, singular/plural, count-safe
    assert "1 approval pending" in compose_dm_pointer(1)
    assert "3 approvals pending" in compose_dm_pointer(3)
    assert "kai-approvals" in compose_dm_pointer(2)
    assert "0 approvals pending" in compose_dm_pointer(0)

    assert parse_decision("allow") == (None, "allow", "")
    assert parse_decision("session") == (None, "session", "")
    assert parse_decision("deny") == (None, "deny", "")
    assert parse_decision("hello there") is None
    assert parse_decision("") is None
    assert _ML_ACTION["allow"] == "once" and _ML_ACTION["session"] == "session" and _ML_ACTION["deny"] == "deny"
    assert ("allow" in _APPROVE_VERBS) and ("deny" not in _APPROVE_VERBS)

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
