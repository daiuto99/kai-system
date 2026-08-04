#!/usr/bin/env python3
"""
Provision a new Buzz channel (= a KAI project/initiative).  KAI invokes this directly —
the tailnet-native replacement for the retired n8n "Slack-Channel-Create" flow ([ROGUE] 069af1be).

  python3 buzz_provision.py "<name>" "<about>"        # create + add Leo, print the channel id
  python3 buzz_provision.py "<name>" "<about>" <uuid>  # idempotent re-run with a fixed id

The channel is created under KAI's own Nostr identity (kai.key) so KAI owns the project surface.
Reuses agents_bridge's signing/auth primitives (one source of truth).

Correctness (learned from the self-test): a PRIVATE channel's add-user (kind:9000) is owner-only and
is rejected if it races ahead of the group's ownership commit — so we AWAIT the create OK, let it
commit, THEN add Leo, and finally VERIFY Leo is in the relay-signed member list (kind:39002) before
reporting success. Exit 0 only when Leo is a confirmed member; non-zero otherwise.
"""
import asyncio, json, sys, uuid
import websockets
import agents_bridge as ab


async def _wait_ok(ws, pk, eid, timeout=10):
    """Read until the OK for event `eid`; answer any AUTH challenge in between. -> (accepted, msg)."""
    async def _loop():
        async for raw in ws:
            m = json.loads(raw)
            if m[0] == "AUTH":
                await ws.send(json.dumps(["AUTH", ab.sign_event(
                    pk, 22242, [["relay", ab.RELAY], ["challenge", m[1]]], "")]))
            elif m[0] == "OK" and m[1] == eid:
                return (bool(m[2]) if len(m) > 2 else False, m[3] if len(m) > 3 else "")
        return (False, "connection closed")
    return await asyncio.wait_for(_loop(), timeout)


async def _leo_is_member(ws, cid, timeout=8):
    """Read the relay-signed member list (kind:39002, d=cid) and confirm Leo's pubkey is present."""
    await ws.send(json.dumps(["REQ", "members", {"kinds": [39002], "#d": [cid]}]))
    async def _loop():
        async for raw in ws:
            m = json.loads(raw)
            if m[0] == "EVENT" and m[1] == "members":
                ptags = [t[1] for t in m[2].get("tags", []) if len(t) > 1 and t[0] == "p"]
                return ab.LEO_PUBKEY in ptags
            if m[0] == "CLOSED" and m[1] == "members":
                return False
    return await asyncio.wait_for(_loop(), timeout)


async def provision(name, about, cid):
    pk = ab.load_or_create_key("kai.key")  # KAI owns provisioned channels
    async with websockets.connect(ab.CONNECT_URL, max_size=2 ** 20) as ws:
        await ab.authenticate(ws, pk)

        # 1) create the private group (client-chosen UUID via h-tag; idempotent)
        ev = ab.sign_event(pk, 9007,
            [["h", cid], ["name", name], ["visibility", "private"], ["about", about]], "")
        await ws.send(json.dumps(["EVENT", ev]))
        ok, msg = await _wait_ok(ws, pk, ev["id"])
        if not ok and "duplicate" not in msg.lower():
            raise RuntimeError(f"group create rejected: {msg}")
        print(f"create: {'ok' if ok else 'exists'} {msg}".strip())

        # 2) let ownership commit before the owner-only member write (the 1s gap the self-test missed)
        await asyncio.sleep(1.2)

        # 3) add Leo as a member — retry once if it still races the ownership commit
        for attempt in range(2):
            ev = ab.sign_event(pk, 9000, [["h", cid], ["p", ab.LEO_PUBKEY], ["role", "member"]], "")
            await ws.send(json.dumps(["EVENT", ev]))
            ok, msg = await _wait_ok(ws, pk, ev["id"])
            if ok or "already" in msg.lower():
                break
            if attempt == 0:
                await asyncio.sleep(1.2)
                continue
            raise RuntimeError(f"add-Leo rejected: {msg}")
        print(f"add-Leo: ok {msg}".strip())

        # 4) VERIFY: Leo is in the relay-signed member list — the real functional gate
        if not await _leo_is_member(ws, cid):
            raise RuntimeError("verification failed: Leo not present in kind:39002 member list")
    return True


def main():
    if len(sys.argv) < 3:
        print('usage: buzz_provision.py "<name>" "<about>" [uuid]', file=sys.stderr)
        sys.exit(2)
    name, about = sys.argv[1], sys.argv[2]
    cid = sys.argv[3] if len(sys.argv) > 3 else str(uuid.uuid4())
    try:
        asyncio.run(asyncio.wait_for(provision(name, about, cid), timeout=40))
    except Exception as e:
        print(f"provision failed: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"CHANNEL_ID {cid}")
    print(f"provisioned + verified #{name} (Leo is a member)")
    sys.exit(0)


if __name__ == "__main__":
    main()
