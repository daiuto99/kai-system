"""Three-state trust verdict + external-witness receipt contract (W-1).

The false-green root cause (Fable, 2026-08-30): gates declare health by reading
back a signal the system generated itself, and absence of a receipt collapses to
GREEN. This module makes that structurally impossible for any verdict that opts
into it:

  * A verdict is GREEN / UNKNOWN / RED; a fresh Result is UNKNOWN by construction.
  * GREEN is reachable ONLY through witnessed(), and ONLY when the witness fn
    returns a valid Receipt whose `minted_by` is NOT the code under test (i.e. a
    party on the OTHER side of the boundary). A check that only reads a
    system-authored signal returns UNKNOWN/RED — never GREEN.

UNKNOWN is load-bearing: no external receipt => amber, never green. A gate clears
only on GREEN; UNKNOWN and RED both block.

Design: docs/TRUST_INVARIANT_EXTERNAL_WITNESS_DESIGN.md
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional


class Verdict(Enum):
    RED = "red"
    UNKNOWN = "unknown"
    GREEN = "green"

    @property
    def is_green(self) -> bool:
        return self is Verdict.GREEN

    @property
    def blocks_close(self) -> bool:
        # Only GREEN clears a gate. UNKNOWN (amber) and RED both block — the
        # whole point: absence of an external witness must NOT pass.
        return self is not Verdict.GREEN


@dataclass(frozen=True)
class Receipt:
    """Proof minted by a party on the OTHER side of the boundary under test.

    `minted_by` names that external party and MUST differ from the code under
    test, or it is not a witness (it is a self-report). `raw_ref` is the
    unforgeable external handle (a relay event id, a Telegram message_id, an
    origin-REST object id). `nonce` correlates the receipt to THIS request so a
    stale/echoed/ack message cannot satisfy it.
    """
    witness_id: str
    boundary: str
    minted_by: str
    observed_at: str
    raw_ref: str
    nonce: Optional[str] = None


class SelfCertificationError(Exception):
    """Raised when code tries to mint GREEN from a receipt it minted itself.

    This is a programming error, not a health verdict — it must fail loud so a
    self-certifying gate can never silently pass as GREEN."""


@dataclass
class Result:
    verdict: Verdict = Verdict.UNKNOWN
    reason: str = "no external witness"
    receipt: Optional[Receipt] = None

    @property
    def is_green(self) -> bool:
        return self.verdict.is_green

    @property
    def blocks_close(self) -> bool:
        return self.verdict.blocks_close


def witnessed(
    under_test: str,
    fn: Callable[[], Optional[Receipt]],
    *,
    expect_nonce: Optional[str] = None,
) -> Result:
    """Run a witness fn and grant GREEN only on a valid EXTERNAL Receipt.

    under_test : identifier of the code/boundary being tested — the thing that
                 must NOT be the receipt's minter.
    fn         : performs the real journey and returns a Receipt observed from
                 the external side, or None if no external effect was observed.
    expect_nonce : if given, the receipt's nonce must match verbatim (anti-echo).

    Returns Result. GREEN requires a well-formed, externally-minted, nonce-correct
    Receipt. Anything else is UNKNOWN or RED — never GREEN.
    """
    try:
        r = fn()
    except Exception as e:  # a witness that blows up is a RED signal, not green
        return Result(Verdict.RED, f"witness raised: {type(e).__name__}: {e}")

    if r is None:
        return Result(Verdict.UNKNOWN, "no receipt — external effect not observed")
    if not isinstance(r, Receipt) or not str(r.raw_ref).strip():
        return Result(Verdict.UNKNOWN, "malformed receipt — no external handle")
    if not str(r.minted_by).strip() or r.minted_by == under_test:
        # The code under test cannot witness itself. Fail loud.
        raise SelfCertificationError(
            f"receipt minted_by {r.minted_by!r} is the code under test "
            f"{under_test!r} — a self-report can never be GREEN"
        )
    if expect_nonce is not None and r.nonce != expect_nonce:
        return Result(
            Verdict.RED,
            f"nonce mismatch — echo/stale/ack (want {expect_nonce!r}, got {r.nonce!r})",
            r,
        )
    return Result(Verdict.GREEN, "witnessed", r)


def self_report(reason: str = "self-reported signal — not externally witnessed") -> Result:
    """Explicit constructor for a system-authored signal (a port answered, a
    stamp is fresh, a 200 came back). It can NEVER be GREEN — it is UNKNOWN by
    construction. Use this instead of returning a bare truthy bool from a check
    that only reads the system's own output."""
    return Result(Verdict.UNKNOWN, reason)
