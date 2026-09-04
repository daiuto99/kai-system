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

Second journey: advisor_knowledge [C3/KAI-bc55d9a4] — does the advisor
demonstrably USE its own domain knowledge? A random codeword is planted into the
advisor's Qdrant collection (and nowhere else), then a real council turn asks for
it back. GREEN only if the reply surfaces the codeword — proof the Tier-3 recall
wiring pulled the advisor's collection into the prompt. The LLM reply mints the
receipt; the recall-assembly code under test cannot forge it.

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
KNOWLEDGE_UNDER_TEST = "council-recall-assembly"  # Tier-3 recall wiring — the code under test


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


def advisor_knowledge(target: str = "kai", timeout: int = 120) -> Result:
    """Prove the advisor demonstrably USES its own knowledge. Targets kai — the
    synchronous cloud advisor — so the proof is deterministic and fast enough to
    gate a close; the async mini advisors (sky/roads) share the same curated-
    knowledge recall wiring, proven out-of-band via their dm_log. knowledge_witness.py
    (inside kai-council-api, on the live Qdrant/Ollama/router path) plants a random
    codeword into the advisor's collection, drives a real /council/message turn
    asking for it, and cleans up. GREEN only when the reply surfaces the codeword —
    a value that lived nowhere but the collection this run, so the recall-assembly
    code under test cannot forge it; the LLM reply mints the receipt."""
    try:
        out = subprocess.run(
            ["docker", "exec", "-e", f"ADVISOR_WITNESS_TARGET={target}",
             "-e", f"ADVISOR_WITNESS_TIMEOUT={timeout}",
             "kai-council-api", "python3", "/app/knowledge_witness.py"],
            capture_output=True, text=True, timeout=timeout + 90,
        )
    except subprocess.TimeoutExpired:
        return Result(Verdict.RED, f"knowledge witness process hung > {timeout + 90}s")

    line = next((l for l in out.stdout.splitlines() if l.startswith("WITNESS_RESULT ")), None)
    if line is None:
        return Result(Verdict.UNKNOWN,
                      f"no witness result emitted (rc={out.returncode}); {out.stderr[-160:]}")
    obs = json.loads(line[len("WITNESS_RESULT "):])

    nonce = obs.get("nonce")

    def fn():
        # Receipt ONLY if the reply actually carried the planted codeword.
        # observed=false -> None -> UNKNOWN (a reply without the nonce is not green).
        if not obs.get("observed"):
            return None
        return Receipt(
            witness_id=f"advisor_knowledge:{target}",
            boundary=obs.get("boundary", "council-recall->advisor-llm"),
            minted_by=obs.get("minted_by", "advisor-llm-reply"),
            observed_at=str(obs.get("elapsed_s", "")),
            raw_ref=str(obs.get("raw_ref") or ""),
            nonce=nonce,
        )

    res = witnessed(KNOWLEDGE_UNDER_TEST, fn, expect_nonce=nonce)
    if res.verdict is not Verdict.GREEN and obs.get("reason"):
        res.reason = obs["reason"]
    return res


REGISTRY = {
    "advisor_answer": advisor_answer,
    "advisor_knowledge": advisor_knowledge,
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
