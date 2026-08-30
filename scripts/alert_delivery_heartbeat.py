#!/usr/bin/env python3
"""S1-B4 (audit #03) — daily alert-channel delivery heartbeat.

W-1 REFERENCE WITNESS (docs/TRUST_INVARIANT_EXTERNAL_WITNESS_DESIGN.md, build
order #2). This is the one already-honest check in green_baseline, re-cast under
the Receipt contract so it is the template every other journey copies:

  * It proves the Telegram alert channel can actually DELIVER (not that the API
    accepted a request) by requiring a **message_id readback** — an id minted by
    Telegram, a party on the OTHER side of the notify->Leo boundary. The code
    under test cannot forge it, so it is a real external receipt.
  * Delivery is run through shared/witness.witnessed(): GREEN is reachable ONLY
    when Telegram returns that receipt for THIS run's nonce. A delivery failure
    (no receipt for a chat) is RED; an inability to attempt is UNKNOWN. Absence
    of a receipt never collapses to GREEN.
  * The receipt is written to ~/backups/.alert_heartbeat as a three-state stamp
    (GREEN|UNKNOWN|RED ...), which the green_baseline `alert_delivery` check
    reads back. Behaviour is unchanged: still silent, still deletes the message
    (Rule B), still exits non-zero unless the channel is GREEN so the cron log
    carries the verdict.

No behaviour change to delivery — it now returns a Receipt, not a bool.
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "shared"))

from witness import Receipt, Verdict, witnessed  # noqa: E402  (after sys.path)

# Rule A: the gateway resolves its log path at import time, so point it at the host
# vault (the host-cron env has no /vault mount) BEFORE importing notify_gateway.
for _v in (Path("/home/leo/vault"), Path("/vault")):
    if _v.exists():
        os.environ.setdefault("KAI_NOTIFY_LOG", str(_v / "00_System" / "notify_log.jsonl"))
        os.environ.setdefault("KAI_NOTIFY_DEDUP", str(_v / "00_System" / "notify_dedup.json"))
        break

STAMP = Path.home() / "backups" / ".alert_heartbeat"
UNDER_TEST = "alert_delivery_heartbeat"   # the code that must NOT mint its own receipt
WITNESS = "telegram"                       # the party on the other side of the boundary


class DeliveryFailed(Exception):
    """A chat affirmatively failed its delivery receipt — a RED signal, not amber."""


def _stamp(text: str) -> None:
    try:
        STAMP.parent.mkdir(parents=True, exist_ok=True)
        STAMP.write_text(text)
    except Exception:
        pass


def _deliver(token: str, ts: str) -> Receipt:
    """Perform the real journey and return Telegram's receipt for THIS nonce.

    Raises DeliveryFailed if a chat delivers no message_id (RED). The receipt's
    minted_by is Telegram, not this script, so witnessed() can grant GREEN."""
    from notify_gateway import send_message, delete_message, _chat_ids

    chats = _chat_ids()
    if not chats:
        raise DeliveryFailed("no allowed chat ids")

    mids = []
    for cid in chats:
        res = send_message(
            cid, f"[heartbeat] KAI alert channel OK · {token}",
            reason=UNDER_TEST, disable_notification=True,
        )
        mid = res.get("message_id")
        if res.get("delivered") and mid is not None:
            mids.append(mid)
            delete_message(cid, mid, reason="heartbeat-cleanup")
        else:
            raise DeliveryFailed(f"chat {cid} returned no message_id receipt")

    return Receipt(
        witness_id=token,
        boundary="alert_delivery->telegram",
        minted_by=WITNESS,           # Telegram assigned the message_id, not this code
        observed_at=ts,
        raw_ref=",".join(map(str, mids)),
        nonce=token,                 # anti-echo: the receipt must be for THIS run
    )


def main() -> int:
    ts = datetime.now(timezone.utc).isoformat()
    token = f"hb-{int(time.time())}"

    result = witnessed(UNDER_TEST, lambda: _deliver(token, ts), expect_nonce=token)

    if result.verdict is Verdict.GREEN and result.receipt is not None:
        _stamp(f"GREEN {ts} witness={result.receipt.minted_by} "
               f"nonce={result.receipt.nonce} mids={result.receipt.raw_ref}")
        print(f"GREEN delivery-witnessed receipts={result.receipt.raw_ref} "
              f"(minted_by={result.receipt.minted_by})")
        return 0

    # UNKNOWN (could not attest) or RED (affirmative failure) — both non-GREEN,
    # both exit non-zero so the cron log and the baseline see the real verdict.
    _stamp(f"{result.verdict.name} {ts} {result.reason}")
    print(f"{result.verdict.name} alert delivery: {result.reason}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
