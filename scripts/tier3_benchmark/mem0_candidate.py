#!/usr/bin/env python3
"""S7-13a candidate (b): Mem0 OSS, self-hosted, local-pinned.

Extraction LLM: Ollama qwen2.5:7b. Embeddings: Ollama nomic-embed-text.
Vector store: ONE dedicated Qdrant collection (tier3bench_mem0), not shared
with any existing KAI collection. Advisor isolation is via Mem0's own
agent_id filter on a single shared collection -- this is the actual
adoption pattern (§12.2 criterion 4: dedicated collection, not per-advisor
collections) and the actual thing §12.2 criterion 2 flags as unverified.
"""
import json
import statistics
import time
from pathlib import Path

from mem0 import Memory

QDRANT_COLLECTION = "tier3bench_mem0"
FIXTURE_DIR = Path(__file__).parent / "fixture"

CONFIG = {
    "vector_store": {
        "provider": "qdrant",
        "config": {
            "collection_name": QDRANT_COLLECTION,
            "host": "localhost",
            "port": 6333,
            "embedding_model_dims": 768,
        },
    },
    "llm": {
        "provider": "ollama",
        "config": {
            "model": "qwen2.5:7b",
            "ollama_base_url": "http://localhost:11434",
            "temperature": 0.0,
        },
    },
    "embedder": {
        "provider": "ollama",
        "config": {
            "model": "nomic-embed-text",
            "ollama_base_url": "http://localhost:11434",
        },
    },
}


def main():
    corpus = json.loads((FIXTURE_DIR / "corpus.json").read_text())
    queries = json.loads((FIXTURE_DIR / "queries.json").read_text())

    import requests as _r
    _r.delete("http://localhost:6333/collections/" + QDRANT_COLLECTION, timeout=10)

    m = Memory.from_config(CONFIG)

    doc_id_to_memory_id = {}
    ingest_latencies = []
    extraction_samples = []
    for d in corpus:
        t0 = time.time()
        res = m.add(
            d["text"],
            agent_id=d["advisor"],
            metadata={"doc_id": d["id"], "source": d["source"], "timestamp": d["timestamp"]},
            infer=True,
        )
        dt = time.time() - t0
        ingest_latencies.append(dt)
        added = res.get("results", []) if isinstance(res, dict) else []
        mem_id = added[0]["id"] if added else None
        doc_id_to_memory_id[d["id"]] = mem_id
        extraction_samples.append({
            "doc_id": d["id"], "advisor": d["advisor"], "source_text": d["text"],
            "extracted_memories": [a.get("memory") for a in added],
            "n_memories_extracted": len(added),
        })

    results = {"candidate": "mem0_oss_local", "normal": [], "namespace_leak": [], "stale_contradictory": [], "deletion": {}, "ingest": {}, "extraction_samples": extraction_samples}

    results["ingest"] = {
        "docs": len(corpus),
        "p50_ms": round(statistics.median(ingest_latencies) * 1000, 1),
        "p95_ms": round(sorted(ingest_latencies)[max(0, int(len(ingest_latencies) * 0.95) - 1)] * 1000, 1),
    }

    def evidence_ids_from_hits(hits):
        return [h.get("metadata", {}).get("doc_id") for h in hits if h.get("metadata")]

    query_latencies = []
    for q in queries["normal"]:
        t0 = time.time()
        res = m.search(q["query"], filters={"agent_id": q["advisor_scope"]}, top_k=5)
        dt = time.time() - t0
        query_latencies.append(dt)
        hits = res.get("results", []) if isinstance(res, dict) else res
        top_ids = evidence_ids_from_hits(hits)
        results["normal"].append({
            "id": q["id"], "advisor": q["advisor_scope"], "query": q["query"],
            "expected": q["expected_evidence_ids"], "top_hits": top_ids,
            "top_score": hits[0].get("score") if hits else None,
            "raw_memory_texts": [h.get("memory") for h in hits[:3]],
            "correct": bool(set(q["expected_evidence_ids"]) & set(top_ids[:1])),
            "latency_ms": round(dt * 1000, 1),
        })

    for q in queries["namespace_leak"]:
        t0 = time.time()
        res = m.search(q["query"], filters={"agent_id": q["advisor_scope"]}, top_k=5)
        dt = time.time() - t0
        query_latencies.append(dt)
        hits = res.get("results", []) if isinstance(res, dict) else res
        top_ids = evidence_ids_from_hits(hits)
        leaked = bool(set(q["forbidden_evidence_ids"]) & set(top_ids))
        results["namespace_leak"].append({
            "id": q["id"], "advisor": q["advisor_scope"], "query": q["query"],
            "forbidden": q["forbidden_evidence_ids"], "returned": top_ids,
            "leaked": leaked, "pass": not leaked, "latency_ms": round(dt * 1000, 1),
        })

    for q in queries["stale_contradictory"]:
        res = m.search(q["query"], filters={"agent_id": q["advisor_scope"]}, top_k=5)
        hits = res.get("results", []) if isinstance(res, dict) else res
        top_ids = evidence_ids_from_hits(hits)
        current_rank = top_ids.index(q["current_evidence_id"]) if q["current_evidence_id"] in top_ids else None
        stale_rank = top_ids.index(q["stale_evidence_id"]) if q["stale_evidence_id"] in top_ids else None
        results["stale_contradictory"].append({
            "id": q["id"], "top_hits": top_ids,
            "raw_memory_texts": [h.get("memory") for h in hits],
            "current_doc_rank": current_rank, "stale_doc_rank": stale_rank,
            "both_returned": current_rank is not None and stale_rank is not None,
            "current_ranked_first": current_rank == 0,
        })

    for q in queries["deletion"]:
        pre_res = m.search(q["verify_query"], filters={"agent_id": q["advisor_scope"]}, top_k=5)
        pre_hits = pre_res.get("results", []) if isinstance(pre_res, dict) else pre_res
        pre_ids = evidence_ids_from_hits(pre_hits)
        pre_present = q["target_evidence_id"] in pre_ids
        mem_id = doc_id_to_memory_id.get(q["target_evidence_id"])
        deleted_ok = False
        if mem_id:
            m.delete(mem_id)
            deleted_ok = True
        time.sleep(0.5)
        post_res = m.search(q["verify_query"], filters={"agent_id": q["advisor_scope"]}, top_k=5)
        post_hits = post_res.get("results", []) if isinstance(post_res, dict) else post_res
        post_ids = evidence_ids_from_hits(post_hits)
        post_present = q["target_evidence_id"] in post_ids
        results["deletion"] = {
            "id": q["id"], "memory_id_found": mem_id is not None, "delete_call_ok": deleted_ok,
            "pre_present": pre_present, "post_present": post_present,
            "pass": pre_present and deleted_ok and not post_present,
        }

    results["query_latency"] = {
        "n": len(query_latencies),
        "p50_ms": round(statistics.median(query_latencies) * 1000, 1),
        "p95_ms": round(sorted(query_latencies)[max(0, int(len(query_latencies) * 0.95) - 1)] * 1000, 1),
    }

    print(json.dumps(results, indent=2, default=str))


if __name__ == "__main__":
    main()
