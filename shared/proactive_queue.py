#!/usr/bin/env python3
"""Proactive queue bridge — P-4a (KAI-1283 stage).

The scope-reconciliation for P-4a (SCOPE RECONCILIATION LAW, 2026-08-31) found that
the PRODUCERS already exist (the DevOps custodians emit `Finding`s; CurrencyCustodian
already turns stale WP core/plugin updates into a Finding) and a persisted approve/
reject store already exists (/t2/queue). What did NOT exist is a Leo-FACING proactive
PULL queue: today custodian Findings route silently to a DevOps Plane queue, never to
a surface Leo pulls. This module is the missing bridge — it maps an existing `Finding`
into a /t2/queue card of kind "finding", so a produced-but-invisible signal becomes a
card Leo can pull and act on.

Deliberately thin — REUSE, don't rebuild:
  * NO new Finding model (shared/devops_ownership.Finding is the input).
  * NO new queue store (the card is posted to the existing /t2/queue).
  * NO new custodian (CurrencyCustodian et al. already produce the Findings).

Push is DEFERRED (Leo, 2026-08-31): a finding-card is created with notify=False so it
never fires the Telegram/DM push. It is a PULL item until Leo is actively using the
system and the scheduled brief is flipped live (separate, deferred ticket).

This file has NO import-time dependency on the worker app, so its mapping logic is
unit-testable in isolation (run `python3 proactive_queue.py --selftest`).
"""
from __future__ import annotations

from typing import Any


# The card kind the /t2 layer recognizes for an informational, code-composed proactive
# item (as opposed to kind="hostops_gate", which is bound to a gated action to resolve).
FINDING_KIND = "finding"

# Only these disposition classes belong on Leo's proactive PULL surface. `auto` findings
# are remediated by the custodian itself and never need Leo — surfacing them would be
# exactly the busy-work the JARVIS definition forbids.
_LEO_FACING_DISPOSITIONS = frozenset({"structural", "decision"})


def is_leo_facing(finding: Any) -> bool:
    """A Finding belongs on Leo's proactive pull queue iff it needs a human — i.e. its
    disposition is structural (triage) or decision (approve), never auto (self-healed)."""
    disp = _get(finding, "disposition")
    return disp in _LEO_FACING_DISPOSITIONS


def compose_card(finding: Any) -> dict:
    """Map a `Finding` (dataclass or dict) into the /t2/queue create body for a
    kind="finding" proactive card. 100% code-composed from Finding fields — no model
    text — mirroring the P-3 code-composed-ask discipline (routes_council_gate).

    Returns a dict shaped for POST /t2/queue (T2ActionRequest fields), with notify=False
    so creation is silent (deferred-push). dedup_key rides through as the card's stable
    identity so a re-run does not double-post the same finding.
    """
    domain = _get(finding, "domain") or "unknown"
    check = _get(finding, "check") or "unknown"
    diagnosis = _get(finding, "diagnosis") or ""
    proposed = _get(finding, "proposed_action") or ""
    dedup_key = _get(finding, "dedup_key") or f"{domain}/{check}"
    disposition = _get(finding, "disposition") or "structural"

    # Subject: a terse, code-composed one-liner (never model-authored).
    subject = f"[{domain}/{check}] {proposed}".strip()
    # Detail: the diagnosis (root cause) is the body; disposition tells Leo the ask type.
    ask = "approve/deny" if disposition == "decision" else "review/dismiss"
    detail = f"{diagnosis}\n\nProposed: {proposed}\nAsk: {ask}"

    return {
        "action": subject,
        "detail": detail,
        "advisor": "kai",
        "kind": FINDING_KIND,
        "dedup_key": dedup_key,
        "notify": False,  # DEFERRED-PUSH: pull-only until Leo is actively using the system
    }


def dedup_new(findings: list, existing_queue: list) -> list:
    """Return only the Leo-facing findings whose dedup_key is not already a pending card
    in the queue — so re-running the producer never double-posts. Pure; no I/O."""
    pending_keys = {
        e.get("dedup_key")
        for e in existing_queue
        if e.get("kind") == FINDING_KIND and e.get("status") == "pending" and e.get("dedup_key")
    }
    out = []
    for f in findings:
        if not is_leo_facing(f):
            continue
        card = compose_card(f)
        if card["dedup_key"] in pending_keys:
            continue
        pending_keys.add(card["dedup_key"])  # guard against dups WITHIN this batch too
        out.append(card)
    return out


def _get(obj: Any, name: str):
    """Read a field from a Finding whether it is a dataclass/object or a plain dict."""
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


# ── selftest — runs with no worker deps ──────────────────────────────────────────
def _selftest() -> int:
    class F:  # a stand-in Finding (dataclass shape) so the test needs no import
        def __init__(self, **kw):
            self.__dict__.update(kw)

    wp = F(domain="wp_fleet", check="updates", severity="warn",
           diagnosis="3 core + 5 plugin update(s) available across the fleet; report-only",
           disposition="structural",
           proposed_action="apply available WP core/plugin updates via the gated host-ops path",
           dedup_key="wp_fleet/updates")
    auto = F(domain="storage", check="disk", severity="warn", diagnosis="tmp bloat",
             disposition="auto", proposed_action="reclaimed 2G", dedup_key="storage/disk")
    dec = F(domain="security", check="rotate", severity="crit", diagnosis="key aged 400d",
            disposition="decision", proposed_action="rotate the credential", dedup_key="sec/rotate")

    # 1. auto is filtered out; structural + decision are Leo-facing.
    assert is_leo_facing(wp) and is_leo_facing(dec) and not is_leo_facing(auto)

    # 2. card is 100% code-composed, silent (notify False), correct kind + dedup.
    card = compose_card(wp)
    assert card["kind"] == FINDING_KIND and card["notify"] is False
    assert card["dedup_key"] == "wp_fleet/updates"
    assert card["action"].startswith("[wp_fleet/updates]")
    assert "Ask: review/dismiss" in card["detail"]
    assert "Ask: approve/deny" in compose_card(dec)["detail"]

    # 3. dedup: auto dropped; an already-pending key is skipped; in-batch dups collapse.
    existing = [{"kind": FINDING_KIND, "status": "pending", "dedup_key": "sec/rotate"}]
    fresh = dedup_new([wp, auto, dec, wp], existing)
    keys = sorted(c["dedup_key"] for c in fresh)
    assert keys == ["wp_fleet/updates"], keys  # dec already pending; auto filtered; wp once

    # 4. dict-shaped Finding works identically (bridge is source-agnostic).
    assert compose_card({"domain": "inbox", "check": "triage", "diagnosis": "d",
                         "disposition": "structural", "proposed_action": "route it",
                         "dedup_key": "inbox/triage"})["dedup_key"] == "inbox/triage"

    print("proactive_queue selftest: PASS (4 checks)")
    return 0


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    print("usage: proactive_queue.py --selftest")
