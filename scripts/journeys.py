#!/usr/bin/env python3
"""[W-1] Journey registry — the single source of GREEN.

A journey enters at Leo's door and reads at Leo's door, and returns a
witness.Result whose GREEN is reachable ONLY through an external, unforgeable
receipt (see shared/witness.py). This is the replacement for the self-asserting
green_baseline product checks: the 27 proxies become diagnostics; these journeys
grant (or withhold) green.

First journey: advisor_answer — does a real question DM'd to the always-on Sky
bridge come back, over the relay, as a nonce-bearing ANSWER (not the ack)? The
relay mints the receipt; council/mini cannot forge it.

Run:  python3 scripts/journeys.py [journey...]   (default: all)
Exit: 0 if every run journey is GREEN; 1 otherwise (UNKNOWN/amber and RED block).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "shared"))
from witness import Receipt, Result, Verdict, witnessed  # noqa: E402

ADVISOR_UNDER_TEST = "council-advisor-path"  # council + mini + bridge — the code under test


def advisor_answer(target: str = "sky", timeout: int = 200) -> Result:
    """Drive the always-on advisor bridge across the relay and demand a
    nonce-bearing answer. The relay-round-trip runs in kai-buzz (it holds the
    NIP-17 machinery); THIS layer, off that container, holds the verdict
    authority. GREEN only on a relay-minted receipt carrying the fresh nonce."""
    try:
        out = subprocess.run(
            ["docker", "exec", "-e", f"ADVISOR_WITNESS_TARGET={target}",
             "-e", f"ADVISOR_WITNESS_TIMEOUT={timeout}",
             "kai-buzz", "python3", "/app/advisor_witness.py"],
            capture_output=True, text=True, timeout=timeout + 60,
        )
    except subprocess.TimeoutExpired:
        return Result(Verdict.RED, f"advisor witness process hung > {timeout + 60}s")

    line = next((l for l in out.stdout.splitlines() if l.startswith("WITNESS_RESULT ")), None)
    if line is None:
        return Result(Verdict.UNKNOWN,
                      f"no witness result emitted (rc={out.returncode}); {out.stderr[-160:]}")
    obs = json.loads(line[len("WITNESS_RESULT "):])

    nonce = obs.get("nonce")

    def fn():
        # Return a Receipt ONLY if the relay actually observed a nonce-bearing
        # reply. observed=false -> None -> UNKNOWN (the ack alone is NOT green).
        if not obs.get("observed"):
            return None
        return Receipt(
            witness_id=f"advisor_answer:{target}",
            boundary=obs.get("boundary", "buzz-relay"),
            minted_by=obs.get("minted_by", "buzz-relay"),
            observed_at=str(obs.get("elapsed_s", "")),
            raw_ref=str(obs.get("raw_ref") or ""),
            nonce=obs.get("nonce"),
        )

    res = witnessed(ADVISOR_UNDER_TEST, fn, expect_nonce=nonce)
    if res.verdict is not Verdict.GREEN and obs.get("reason"):
        res.reason = obs["reason"]
    return res


REGISTRY = {
    "advisor_answer": advisor_answer,
}


def main(argv):
    names = argv or list(REGISTRY)
    worst = Verdict.GREEN
    order = {Verdict.GREEN: 0, Verdict.UNKNOWN: 1, Verdict.RED: 2}
    for name in names:
        fn = REGISTRY.get(name)
        if fn is None:
            print(f"[journey] {name}: UNKNOWN — no such journey")
            worst = Verdict.UNKNOWN
            continue
        res = fn()
        print(f"[journey] {name}: {res.verdict.value.upper()} — {res.reason}"
              + (f" (receipt {res.receipt.raw_ref})" if res.receipt else ""))
        if order[res.verdict] > order[worst]:
            worst = res.verdict
    print(f"\nJOURNEYS: worst = {worst.value.upper()} "
          + ("(all green)" if worst is Verdict.GREEN else "(NOT all green — close blocks)"))
    return 0 if worst is Verdict.GREEN else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
