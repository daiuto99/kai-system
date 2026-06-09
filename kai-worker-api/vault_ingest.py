"""Embed + upsert helper used by Sprint A dispatch handlers.

Mirrors the chunking + embedding behavior of kai-council-api/ingest.py so
worker-side writes (share, summarize forward) land in the same collections
the council reads.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import urllib.request as ur
from pathlib import Path

logger = logging.getLogger(__name__)

QDRANT = os.environ.get("QDRANT_URL", "http://kai-qdrant:6333")
OLLAMA = os.environ.get("OLLAMA_URL", "http://kai-ollama:11434")
CHUNK_SIZE = 400
CHUNK_OVERLAP = 50


def _embed(text: str) -> list[float]:
    body = json.dumps({"model": "nomic-embed-text", "input": text}).encode()
    req = ur.Request(f"{OLLAMA}/api/embed", data=body,
                     headers={"Content-Type": "application/json"}, method="POST")
    with ur.urlopen(req, timeout=30) as r:
        data = json.loads(r.read())
    embeddings = data.get("embeddings", [])
    if not embeddings or not embeddings[0]:
        raise RuntimeError(f"ollama embed returned nothing: {data}")
    return embeddings[0]


def _chunks(text: str) -> list[str]:
    words = text.split()
    if not words:
        return []
    chunks = []
    step = CHUNK_SIZE - CHUNK_OVERLAP
    for i in range(0, len(words), step):
        ch = " ".join(words[i:i + CHUNK_SIZE])
        chunks.append(ch)
        if i + CHUNK_SIZE >= len(words):
            break
    return chunks


def _ensure_collection(advisor: str) -> None:
    try:
        ur.urlopen(f"{QDRANT}/collections/{advisor}", timeout=5)
        return
    except Exception:
        pass
    body = json.dumps({
        "vectors": {"size": 768, "distance": "Cosine"},
    }).encode()
    req = ur.Request(f"{QDRANT}/collections/{advisor}", data=body,
                     headers={"Content-Type": "application/json"}, method="PUT")
    try:
        ur.urlopen(req, timeout=10)
    except Exception as e:
        logger.warning("ensure_collection %s: %s", advisor, e)


def upsert_md(advisor: str, md_path: Path, *, title: str, source_url: str = "") -> int:
    """Embed the md's body, upsert one point per chunk. Returns chunk count."""
    text = md_path.read_text(encoding="utf-8")
    # Strip frontmatter from embedding body
    body = text
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end > 0:
            body = text[end + 5:]
    chunks = _chunks(body)
    if not chunks:
        return 0
    _ensure_collection(advisor)
    points = []
    fname = md_path.name
    base_hash = hashlib.sha1(f"{advisor}:{fname}".encode()).hexdigest()
    for i, ch in enumerate(chunks):
        try:
            vec = _embed(ch)
        except Exception as e:
            logger.warning("embed failed for chunk %d of %s: %s", i, fname, e)
            continue
        point_id = int(hashlib.sha1(f"{base_hash}:{i}".encode()).hexdigest()[:15], 16)
        points.append({
            "id": point_id,
            "vector": vec,
            "payload": {
                "title": title,
                "source_url": source_url,
                "filename": fname,
                "chunk_index": i,
                "chunk_total": len(chunks),
                "text": ch[:8000],
            },
        })
    if not points:
        return 0
    body = json.dumps({"points": points}).encode()
    req = ur.Request(f"{QDRANT}/collections/{advisor}/points?wait=true",
                     data=body,
                     headers={"Content-Type": "application/json"}, method="PUT")
    with ur.urlopen(req, timeout=30) as r:
        r.read()
    return len(points)
