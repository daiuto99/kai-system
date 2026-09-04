#!/usr/bin/env python3
"""[C3/KAI-bc55d9a4] Advisor knowledge-use journey heartbeat.

W-1 witness (docs/TRUST_INVARIANT_EXTERNAL_WITNESS_DESIGN.md), same template as
alert_delivery_heartbeat: runs the `advisor_knowledge` journey (plant a random
codeword into an advisor's own Qdrant collection, drive a real turn, demand the
reply surface it — proof the curated-knowledge recall path actually delivered
the advisor's knowledge into inference), then writes a three-state stamp to
~/backups/.knowledge_heartbeat which the green_baseline `advisor_knowledge`
check reads back.

Kept off the inline baseline path (like alert_delivery) so the baseline/CI does
not make a live cloud LLM call every run — the daily heartbeat pays that cost
once and stamps the receipt. Exits non-zero unless GREEN, so the cron log
carries the verdict.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "shared"))

import journeys  # noqa: E402
from witness import Verdict  # noqa: E402

STAMP = Path.home() / "backups" / ".knowledge_heartbeat"        # written ONLY on GREEN
STAMP_LAST = Path.home() / "backups" / ".knowledge_heartbeat.last"  # last non-GREEN, observability
ATTEMPTS = 3  # absorb single-turn LLM flakiness before recording a non-GREEN run


def _stamp(path: Path, line: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(line + "\n")
    except OSError as e:
        print(f"stamp write failed ({path.name}): {e}", file=sys.stderr)


def _read_failing_since() -> str | None:
    """The failing_since=<ISO> token from a prior .last stamp, if any — the start
    of the current unbroken failing streak (preserved across failed runs)."""
    try:
        for tok in STAMP_LAST.read_text().split():
            if tok.startswith("failing_since="):
                return tok.split("=", 1)[1]
    except OSError:
        pass
    return None


def main() -> int:
    ts = datetime.now(timezone.utc).isoformat()
    last = None
    for attempt in range(ATTEMPTS):
        res = journeys.advisor_knowledge()
        if res.verdict is Verdict.GREEN and res.receipt is not None:
            # GREEN refreshes the freshness stamp the baseline keys on, and CLEARS
            # the failing streak so a later break restarts the clock cleanly.
            _stamp(STAMP, f"GREEN {ts} witness={res.receipt.minted_by} "
                          f"nonce={res.receipt.nonce} ref={res.receipt.raw_ref}")
            try:
                STAMP_LAST.unlink()
            except OSError:
                pass
            print(f"GREEN knowledge-use witnessed (attempt {attempt + 1}, receipt {res.receipt.raw_ref})")
            return 0
        last = res
    # No GREEN across all attempts. Do NOT touch the GREEN stamp — let it age so a
    # persistent break trips the baseline's 48h RED (F2). Preserve failing_since
    # so the baseline can also block a NEVER-green deploy whose recall has been
    # broken past the grace window.
    failing_since = _read_failing_since() or ts
    _stamp(STAMP_LAST, f"{last.verdict.name} {ts} failing_since={failing_since} {last.reason}")
    print(f"{last.verdict.name} knowledge-use after {ATTEMPTS} attempts "
          f"(failing_since={failing_since}): {last.reason}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
