#!/usr/bin/env python3
"""S1-B4 (audit #03) — daily alert-channel delivery heartbeat.

Proves the Telegram alert channel can actually DELIVER (not just that the API
accepts a request): sends a silent, uniquely-tokened heartbeat to each allowed
chat via the notify() gateway, requires a message_id readback as the receipt,
then deletes it so Leo's DM carries no daily noise (Rule B). Stamps
~/backups/.alert_heartbeat, which the green_baseline `alert_delivery` probe reads
(RED on a FAILED/stale receipt). Fail-soft; exits non-zero on a failed receipt so
the cron log carries the verdict. The off-box 2nd-channel watcher (mac-mini) is the
Sprint-1 companion piece, blocked until the mini is up.
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "shared"))

# Rule A: the gateway resolves its log path at import time, so point it at the host
# vault (the host-cron env has no /vault mount) BEFORE importing notify_gateway.
for _v in (Path("/home/leo/vault"), Path("/vault")):
    if _v.exists():
        os.environ.setdefault("KAI_NOTIFY_LOG", str(_v / "00_System" / "notify_log.jsonl"))
        os.environ.setdefault("KAI_NOTIFY_DEDUP", str(_v / "00_System" / "notify_dedup.json"))
        break

STAMP = Path.home() / "backups" / ".alert_heartbeat"


def _stamp(text: str) -> None:
    try:
        STAMP.parent.mkdir(parents=True, exist_ok=True)
        STAMP.write_text(text)
    except Exception:
        pass


def main() -> int:
    ts = datetime.now(timezone.utc).isoformat()
    token = f"hb-{int(time.time())}"
    try:
        from notify_gateway import send_message, delete_message, _chat_ids
    except Exception as e:
        _stamp(f"FAIL {ts} import:{type(e).__name__}")
        print(f"FAIL import: {type(e).__name__}: {e}")
        return 1

    chats = _chat_ids()
    if not chats:
        _stamp(f"FAIL {ts} no-chat-ids")
        print("FAIL: no allowed chat ids")
        return 1

    receipts, failures = [], []
    for cid in chats:
        res = send_message(cid, f"[heartbeat] KAI alert channel OK · {token}",
                           reason="alert_delivery_heartbeat", disable_notification=True)
        mid = res.get("message_id")
        if res.get("delivered") and mid is not None:
            receipts.append(mid)
            delete_message(cid, mid, reason="heartbeat-cleanup")
        else:
            failures.append(str(cid))

    if failures or not receipts:
        _stamp(f"FAIL {ts} chats_failed={','.join(failures) or 'none'}")
        print(f"FAIL delivery receipt: chats_failed={failures}")
        return 1

    _stamp(f"OK {ts} mids={','.join(map(str, receipts))}")
    print(f"OK delivery-verified receipts={receipts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
