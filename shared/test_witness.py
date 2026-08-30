"""Self-tests for shared/witness.py (W-1). Run: python3 shared/test_witness.py

The load-bearing test is test_self_report_can_never_be_green + the
minted_by==under_test SelfCertificationError: a signal the system produced
itself must not be able to reach GREEN.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from witness import (  # noqa: E402
    Receipt,
    Result,
    SelfCertificationError,
    Verdict,
    self_report,
    witnessed,
)

FAILS = []


def check(name, cond):
    if cond:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name}")
        FAILS.append(name)


def ext_receipt(nonce=None, minted_by="buzz-relay", raw_ref="evt_abc123"):
    return Receipt(
        witness_id="advisor.answer",
        boundary="buzz-relay",
        minted_by=minted_by,
        observed_at="2026-08-30T00:00:00+00:00",
        raw_ref=raw_ref,
        nonce=nonce,
    )


# 1. Default construction is UNKNOWN, and UNKNOWN blocks the close.
r = Result()
check("fresh Result defaults to UNKNOWN", r.verdict is Verdict.UNKNOWN)
check("UNKNOWN blocks the close", r.blocks_close is True)
check("UNKNOWN is not green", r.is_green is False)

# 2. A valid external receipt promotes to GREEN.
res = witnessed("council-api", lambda: ext_receipt())
check("external receipt -> GREEN", res.verdict is Verdict.GREEN)
check("GREEN does not block close", res.blocks_close is False)
check("GREEN carries the receipt", res.receipt is not None and res.receipt.raw_ref == "evt_abc123")

# 3. No external effect observed -> UNKNOWN (never green).
res = witnessed("council-api", lambda: None)
check("no receipt -> UNKNOWN", res.verdict is Verdict.UNKNOWN)
check("no receipt blocks close", res.blocks_close is True)

# 4. Malformed receipt (no external handle) -> UNKNOWN.
res = witnessed("council-api", lambda: ext_receipt(raw_ref="   "))
check("empty raw_ref -> UNKNOWN", res.verdict is Verdict.UNKNOWN)

# 5. THE INVARIANT: code cannot witness itself. minted_by == under_test raises.
raised = False
try:
    witnessed("council-api", lambda: ext_receipt(minted_by="council-api"))
except SelfCertificationError:
    raised = True
check("minted_by == under_test raises SelfCertificationError", raised)

# 6. Nonce mismatch (echo/stale/ack) -> RED, not green.
res = witnessed("council-api", lambda: ext_receipt(nonce="OLD"), expect_nonce="NEW")
check("nonce mismatch -> RED", res.verdict is Verdict.RED)
check("nonce mismatch is not green", res.is_green is False)

# 7. Nonce match -> GREEN.
res = witnessed("council-api", lambda: ext_receipt(nonce="NEW"), expect_nonce="NEW")
check("nonce match -> GREEN", res.verdict is Verdict.GREEN)

# 8. A witness that raises -> RED (a broken witness is not green).
def boom():
    raise RuntimeError("relay unreachable")

res = witnessed("council-api", boom)
check("witness raises -> RED", res.verdict is Verdict.RED)

# 9. self_report can NEVER be green — the whole point.
res = self_report()
check("self_report -> UNKNOWN", res.verdict is Verdict.UNKNOWN)
check("self_report blocks close", res.blocks_close is True)
check("self_report is never green", res.is_green is False)

# 10. Verdict semantics.
check("GREEN.is_green", Verdict.GREEN.is_green is True)
check("RED blocks close", Verdict.RED.blocks_close is True)
check("GREEN clears close", Verdict.GREEN.blocks_close is False)

if FAILS:
    print(f"\nWITNESS TESTS FAILED: {len(FAILS)} — {', '.join(FAILS)}")
    sys.exit(1)
print("\nwitness: ALL PASS")
