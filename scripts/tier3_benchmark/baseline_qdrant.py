#!/usr/bin/env python3
"""S7-13a candidate (a): minimal direct-Qdrant baseline.

No extraction pipeline, no memory library — raw chunk embed + upsert + top-k
search, one dedicated scratch collection per advisor namespace (mirrors the
existing per-advisor-collection architecture without touching production
collections). Dependencies: only `requests`, already present on the worker.
"""
import json
import statistics
import sys
import time
from pathlib import Path

import requests

OLLAMA = "http://localhost:11434"
QDRANT = "http://localhost:6333"
EMBED_MODEL = "nomic-embed-text"
COLLECTION_PREFIX = "tier3bench_baseline_"
FIXTURE_DIR = Path(__file__).parent / "fixture"


def embed(text):
    r = requests.post(f"{OLLAMA}/api/embeddings", json={"model": EMBED_MODEL, "prompt": text}, timeout=30)
    r.raise_for_status()
    return r.json()["embedding"]


def collection_name(advisor):
    return f"{COLLECTION_PREFIX}{advisor}"


def ensure_collection(advisor, dim):
    name = collection_name(advisor)
    requests.delete(f"{QDRANT}/collections/{name}", timeout=10)
    r = requests.put(
        f"{QDRANT}/collections/{name}",
        json={"vectors": {"size": dim, "distance": "Cosine"}},
        timeout=10,
    )
    r.raise_for_status()
    return name


def upsert(advisor, doc_id, vector, payload):
    name = collection_name(advisor)
    point_id = abs(hash(doc_id)) % (2**53)
    r = requests.put(
        f"{QDRANT}/collections/{name}/points",
        json={"points": [{"id": point_id, "vector": vector, "payload": {**payload, "doc_id": doc_id}}]},
        timeout=10,
    )
    r.raise_for_status()
    return point_id


def search(advisor, vector, top_k=5):
    name = collection_name(advisor)
    r = requests.post(
        f"{QDRANT}/collections/{name}/points/search",
        json={"vector": vector, "limit": top_k, "with_payload": True},
        timeout=10,
    )
    r.raise_for_status()
    return r.json()["result"]


def delete_point(advisor, point_id):
    name = collection_name(advisor)
    r = requests.post(
        f"{QDRANT}/collections/{name}/points/delete",
        json={"points": [point_id]},
        timeout=10,
    )
    r.raise_for_status()


def main():
    corpus = json.loads((FIXTURE_DIR / "corpus.json").read_text())
    queries = json.loads((FIXTURE_DIR / "queries.json").read_text())

    advisors = sorted(set(d["advisor"] for d in corpus))
    dim = len(embed("dimension probe"))
    for a in advisors:
        ensure_collection(a, dim)

    doc_id_to_point = {}
    ingest_latencies = []
    for d in corpus:
        t0 = time.time()
        vec = embed(d["text"])
        pid = upsert(d["advisor"], d["id"], vec, {"advisor": d["advisor"], "source": d["source"], "timestamp": d["timestamp"], "text": d["text"]})
        ingest_latencies.append(time.time() - t0)
        doc_id_to_point[d["id"]] = (d["advisor"], pid)

    results = {"candidate": "direct_qdrant_baseline", "normal": [], "namespace_leak": [], "stale_contradictory": [], "deletion": {}, "ingest": {}}

    results["ingest"] = {
        "docs": len(corpus),
        "p50_ms": round(statistics.median(ingest_latencies) * 1000, 1),
        "p95_ms": round(sorted(ingest_latencies)[int(len(ingest_latencies) * 0.95) - 1] * 1000, 1) if len(ingest_latencies) > 1 else round(ingest_latencies[0] * 1000, 1),
    }

    query_latencies = []
    for q in queries["normal"]:
        t0 = time.time()
        vec = embed(q["query"])
        hits = search(q["advisor_scope"], vec, top_k=5)
        dt = time.time() - t0
        query_latencies.append(dt)
        top_ids = [h["payload"]["doc_id"] for h in hits]
        results["normal"].append({
            "id": q["id"], "advisor": q["advisor_scope"], "query": q["query"],
            "expected": q["expected_evidence_ids"], "top_hits": top_ids,
            "top_score": hits[0]["score"] if hits else None,
            "correct": bool(set(q["expected_evidence_ids"]) & set(top_ids[:1])),
            "latency_ms": round(dt * 1000, 1),
        })

    for q in queries["namespace_leak"]:
        t0 = time.time()
        vec = embed(q["query"])
        hits = search(q["advisor_scope"], vec, top_k=5)
        dt = time.time() - t0
        query_latencies.append(dt)
        top_ids = [h["payload"]["doc_id"] for h in hits]
        leaked = bool(set(q["forbidden_evidence_ids"]) & set(top_ids))
        results["namespace_leak"].append({
            "id": q["id"], "advisor": q["advisor_scope"], "query": q["query"],
            "forbidden": q["forbidden_evidence_ids"], "returned": top_ids,
            "leaked": leaked, "pass": not leaked, "latency_ms": round(dt * 1000, 1),
        })

    for q in queries["stale_contradictory"]:
        vec = embed(q["query"])
        hits = search(q["advisor_scope"], vec, top_k=5)
        top_ids = [h["payload"]["doc_id"] for h in hits]
        current_rank = top_ids.index(q["current_evidence_id"]) if q["current_evidence_id"] in top_ids else None
        stale_rank = top_ids.index(q["stale_evidence_id"]) if q["stale_evidence_id"] in top_ids else None
        results["stale_contradictory"].append({
            "id": q["id"], "top_hits": top_ids,
            "current_doc_rank": current_rank, "stale_doc_rank": stale_rank,
            "both_returned": current_rank is not None and stale_rank is not None,
            "current_ranked_first": current_rank == 0,
            "distinguishable": False,
            "note": "Raw vector similarity has no recency signal; both stale and current facts return with near-identical scores and no mechanism to prefer the current one.",
        })

    for q in queries["deletion"]:
        vec = embed(q["verify_query"])
        pre_hits = search(q["advisor_scope"], vec, top_k=5)
        pre_ids = [h["payload"]["doc_id"] for h in pre_hits]
        pre_present = q["target_evidence_id"] in pre_ids
        _, point_id = doc_id_to_point[q["target_evidence_id"]]
        delete_point(q["advisor_scope"], point_id)
        time.sleep(0.5)
        post_hits = search(q["advisor_scope"], vec, top_k=5)
        post_ids = [h["payload"]["doc_id"] for h in post_hits]
        post_present = q["target_evidence_id"] in post_ids
        results["deletion"] = {
            "id": q["id"], "pre_present": pre_present, "post_present": post_present,
            "pass": pre_present and not post_present,
        }

    results["query_latency"] = {
        "n": len(query_latencies),
        "p50_ms": round(statistics.median(query_latencies) * 1000, 1),
        "p95_ms": round(sorted(query_latencies)[max(0, int(len(query_latencies) * 0.95) - 1)] * 1000, 1),
    }

    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
