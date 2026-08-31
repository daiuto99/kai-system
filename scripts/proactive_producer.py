#!/usr/bin/env python3
"""Proactive producer — P-4a (KAI-1283 stage). Feeds Leo-facing custodian Findings
onto the proactive PULL queue (/t2/queue, kind="finding"), via the pure bridge in
shared/proactive_queue.py.

SCOPE (reconciled 2026-08-31 per the SCOPE RECONCILIATION LAW):
  * REUSE the producers — CurrencyCustodian already emits WP core/plugin update
    Findings; this does not re-derive them.
  * REUSE the store — cards go to the existing /t2/queue; no new queue.
  * REUSE the bridge — shared/proactive_queue maps Finding -> card and dedups.

INFRA STAYS DEVOPS-OWNED. Leo is NEVER paged for infra (feedback_devops_owns_infra):
disk/updates/backups/services/security/fleet Findings keep routing to the silent DevOps
Plane queue. Only genuinely Leo-facing domains land on his pull surface. Slice 1 = WP
client-site maintenance (currency/wp_fleet_stale) — a gated, client-approval action that
IS Leo's call. The allowlist below is how new Leo-facing sources (content calendar,
inbox) get added later — extend it, don't fork this.

Push is DEFERRED: the bridge stamps notify=False, so cards are created silently — a
pull-only surface until Leo is actively using the system.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for p in (str(_HERE), str(_HERE.parent / "shared")):
    if p not in sys.path:
        sys.path.insert(0, p)

import proactive_queue as bridge  # the pure Finding->card bridge

# The (domain, check) pairs that belong on LEO's pull queue. Everything not listed
# here stays DevOps-owned and silent. Grow this as content-calendar / inbox producers
# land — never widen it to an infra domain.
LEO_FACING = frozenset({
    ("currency", "wp_fleet_stale"),   # WP client-site core/plugin updates (gated, Leo-approves)
})


def _default_assessors() -> list:
    """The custodians whose Findings can contain Leo-facing items. REUSE, don't rebuild:
    CurrencyCustodian is the existing WP-currency producer. Returns assess callables."""
    from devops_currency_custodian import CurrencyCustodian
    return [CurrencyCustodian().assess]


def collect_leo_findings(assessors=None) -> list:
    """Run the assessors (read-only) and keep only Findings on the LEO_FACING allowlist.
    A broken assessor is skipped, never fatal (mirrors the sweep's per-custodian guard)."""
    findings = []
    for assess in (assessors if assessors is not None else _default_assessors()):
        try:
            for f in (assess() or []):
                if (bridge._get(f, "domain"), bridge._get(f, "check")) in LEO_FACING:
                    findings.append(f)
        except Exception as e:  # never let one bad source sink the producer
            print(f"[WARN] assessor failed: {type(e).__name__}: {e}", file=sys.stderr)
    return findings


def produce(get_queue, post_card, assessors=None) -> dict:
    """Pure orchestration (injectable transports so it is unit-testable):
    collect Leo-facing findings -> dedup vs the live queue -> post each new card.
    Returns a summary. Never raises for an individual post — records the outcome."""
    findings = collect_leo_findings(assessors)
    existing = get_queue() or []
    cards = bridge.dedup_new(findings, existing)
    posted, errors = [], []
    for card in cards:
        try:
            res = post_card(card)
            posted.append({"dedup_key": card["dedup_key"], "result": res})
        except Exception as e:
            errors.append({"dedup_key": card["dedup_key"], "error": f"{type(e).__name__}: {e}"})
    return {"leo_findings": len(findings), "new_cards": len(cards),
            "posted": len(posted), "errors": errors}


# ── live HTTP transports (authed edge) ───────────────────────────────────────────
def _live_transports():
    import httpx
    # Worker-api binds the tailnet IP, not localhost (matches the green-baseline probe).
    base = os.environ.get("KAI_WORKER_URL", "http://100.78.94.80:8001")
    auth = None
    for cand in ("/home/leo/kai-system/secrets/kai_worker_auth.txt",
                 os.path.expanduser("~/.kai/secrets/kai_worker_auth.txt")):
        if Path(cand).exists():
            u, _, p = Path(cand).read_text().strip().partition(":")
            auth = (u, p)
            break

    def get_queue():
        r = httpx.get(f"{base}/t2/queue", params={"kind": "finding"}, auth=auth, timeout=10)
        r.raise_for_status()
        return r.json().get("queue", [])

    def post_card(card):
        r = httpx.post(f"{base}/t2/queue", json=card, auth=auth, timeout=10)
        r.raise_for_status()
        return r.json()

    return get_queue, post_card


def main() -> int:
    get_queue, post_card = _live_transports()
    summary = produce(get_queue, post_card)
    print(f"proactive_producer: {summary['new_cards']} new card(s) posted "
          f"({summary['leo_findings']} leo-facing finding(s)); errors={len(summary['errors'])}")
    for e in summary["errors"]:
        print(f"  ERROR {e['dedup_key']}: {e['error']}", file=sys.stderr)
    return 1 if summary["errors"] else 0


# ── selftest — no worker, no HTTP; injected transports + fake findings ───────────
def _selftest() -> int:
    class F:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    wp = F(domain="currency", check="wp_fleet_stale", severity="warn",
           diagnosis="wp_fleet: 2 component(s) stale — 3 core + 5 plugin update(s) available",
           disposition="structural",
           proposed_action="apply available WP core/plugin updates via the gated host-ops path",
           dedup_key="currency-wp_fleet", detail={})
    # NOT Leo-facing: py_deps is a dev/DevOps concern — must be filtered out.
    py = F(domain="currency", check="py_deps_stale", severity="warn", diagnosis="py stale",
           disposition="structural", proposed_action="bump", dedup_key="currency-py_deps", detail={})

    def assessors():
        return [lambda: [wp, py]]

    # 1. only the WP finding is collected; py_deps is filtered by the allowlist.
    leo = collect_leo_findings(assessors())
    assert len(leo) == 1 and bridge._get(leo[0], "check") == "wp_fleet_stale"

    # 2. first run posts one silent card; the posted body is code-composed + notify=False.
    store, posted = [], []

    def get_queue():
        return list(store)

    def post_card(card):
        assert card["notify"] is False and card["kind"] == "finding"
        entry = {**card, "id": "x", "status": "pending"}
        store.append(entry)
        posted.append(card)
        return {"ok": True, "id": "x"}

    s1 = produce(get_queue, post_card, assessors())
    assert s1["new_cards"] == 1 and s1["posted"] == 1 and not s1["errors"], s1

    # 3. second run is idempotent — the pending card dedups, nothing re-posts.
    s2 = produce(get_queue, post_card, assessors())
    assert s2["new_cards"] == 0 and s2["posted"] == 0, s2
    assert len(store) == 1  # still exactly one card

    print("proactive_producer selftest: PASS (3 checks)")
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    raise SystemExit(main())
