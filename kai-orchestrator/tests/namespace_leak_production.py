#!/usr/bin/env python3
"""CONTEXT_SPEC.md §12.3 Phase 3 acceptance item: re-verify Tier 3 namespace
isolation against the real production Qdrant corpus, not the 19-document
benchmark fixture (docs/reviews/tier3-benchmark/). Runs the actual deployed
context_service._tier3_recall() against actual production collections and
data — the real code path, not a reimplementation.

Run inside the kai-orchestrator container (needs the kai-qdrant/kai-ollama
hostnames and the context_service module on the path):

    docker exec kai-orchestrator python3 /app/tests/namespace_leak_production.py
"""
import json
import sys

import httpx

sys.path.insert(0, "/app")
import context_service as cs  # noqa: E402

# Every non-empty production advisor collection at the time this test was
# written (2026-07-11) — most production collections are still empty (§12.3
# caveat: corpus is genuinely small right now, not synthetically inflated;
# re-run this as ingestion grows). Discovered via GET /collections on kai-qdrant.
NONEMPTY_ADVISORS = ["kai", "beats", "sky", "roads", "copywriter", "doc"]

ADVERSARIAL_ADVISOR_VALUES = [
    "../kai", "kai/../beats", "kai%2F..%2Fbeats", "", "  ", "KAI",
    "kai;beats", "beats' OR '1'='1", "nonexistent-advisor-xyz",
]


def sample_texts(advisor, n=3):
    r = httpx.post(
        f"{cs.QDRANT_URL}/collections/{advisor}/points/scroll",
        json={"limit": n, "with_payload": True, "with_vector": False},
        timeout=10,
    )
    r.raise_for_status()
    return [
        (p["id"], (p.get("payload", {}).get("text") or "").strip())
        for p in r.json()["result"]["points"]
    ]


def main():
    samples = {a: sample_texts(a) for a in NONEMPTY_ADVISORS}
    results = {"cross_advisor_leak": [], "self_recall_sanity": [], "adversarial_advisor_strings": []}

    # 1. Cross-advisor leak: query every OTHER advisor's real Tier 3 path using
    # each advisor's own real content as the message. A hit whose
    # source_collection is the content's true origin advisor is a leak.
    for src, texts in samples.items():
        for point_id, text in texts:
            if not text:
                continue
            for other in NONEMPTY_ADVISORS:
                if other == src:
                    continue
                recall = cs._tier3_recall(other, text)
                hit_collections = sorted({h["source_collection"] for h in recall["hits"]})
                leaked = src in hit_collections
                results["cross_advisor_leak"].append({
                    "source_advisor": src, "source_point_id": point_id,
                    "queried_advisor": other, "hit_collections": hit_collections,
                    "pass": not leaked,
                })

    # 2. Self-recall sanity: an advisor's own real content, queried against its
    # own Tier 3, must only ever surface hits from its own collection.
    for advisor, texts in samples.items():
        for point_id, text in texts:
            if not text:
                continue
            recall = cs._tier3_recall(advisor, text)
            hit_collections = set(h["source_collection"] for h in recall["hits"])
            results["self_recall_sanity"].append({
                "advisor": advisor, "point_id": point_id,
                "hit_count": len(recall["hits"]),
                "hit_collections": sorted(hit_collections),
                "pass": hit_collections <= {advisor},
            })

    # 3. Adversarial advisor identifiers — L4: must be rejected by the
    # allowlist (zero recall), never resolve to an unintended collection or
    # raise past the boundary.
    for val in ADVERSARIAL_ADVISOR_VALUES:
        try:
            recall = cs._tier3_recall(val, "adversarial advisor value probe")
            results["adversarial_advisor_strings"].append({
                "advisor_value": val, "hit_count": len(recall["hits"]),
                "pass": len(recall["hits"]) == 0,
            })
        except Exception as e:
            results["adversarial_advisor_strings"].append({
                "advisor_value": val, "error": str(e), "pass": False,
            })

    all_pass = all(
        r["pass"] for section in results.values() for r in section
    )
    results["summary"] = {
        "cross_advisor_leak": f"{sum(r['pass'] for r in results['cross_advisor_leak'])}/{len(results['cross_advisor_leak'])}",
        "self_recall_sanity": f"{sum(r['pass'] for r in results['self_recall_sanity'])}/{len(results['self_recall_sanity'])}",
        "adversarial_advisor_strings": f"{sum(r['pass'] for r in results['adversarial_advisor_strings'])}/{len(results['adversarial_advisor_strings'])}",
        "all_pass": all_pass,
    }
    print(json.dumps(results, indent=2))
    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    main()
