#!/usr/bin/env python3
"""
KAI Knowledge Ingestion Pipeline
Usage:
  python3 ingest.py <file_or_dir> --advisor <name> [--title <title>]
  python3 ingest.py --list              # list collections + vector counts
  python3 ingest.py --clear <advisor>   # delete all vectors for an advisor

Supported: .pdf, .md, .txt, .csv
Embeddings: Ollama nomic-embed-text (768-dim)
Vector DB: Qdrant at localhost:6333
"""
import argparse
import csv
import hashlib
import io
import json
import sys
import urllib.request
import urllib.error
from pathlib import Path

import os
QDRANT = os.environ.get("QDRANT_URL", "http://localhost:6333")
OLLAMA = os.environ.get("OLLAMA_URL", "http://localhost:11434")
CHUNK_SIZE = 400    # words per chunk
CHUNK_OVERLAP = 50  # word overlap between chunks


# ── HTTP helpers ───────────────────────────────────────────────────────────────

def qdrant(method, path, data=None):
    url = f"{QDRANT}{path}"
    body = json.dumps(data).encode() if data else None
    headers = {"Content-Type": "application/json"}
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Qdrant {e.code}: {e.read().decode()[:200]}")


def embed(text: str) -> list[float]:
    body = json.dumps({"model": "nomic-embed-text", "input": text}).encode()
    req = urllib.request.Request(f"{OLLAMA}/api/embed", data=body,
                                  headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())
    embeddings = data.get("embeddings", [])
    if not embeddings:
        raise RuntimeError(f"Ollama embed returned no embeddings: {data}")
    return embeddings[0]


# ── Text extraction ────────────────────────────────────────────────────────────

def extract_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        try:
            import pypdf
            reader = pypdf.PdfReader(str(path))
            return "\n\n".join(page.extract_text() or "" for page in reader.pages)
        except ImportError:
            import subprocess
            result = subprocess.run(["pdftotext", str(path), "-"], capture_output=True, text=True)
            if result.returncode == 0:
                return result.stdout
            raise RuntimeError("pypdf not installed and pdftotext not available. Install pypdf: pip install pypdf")
    elif suffix == ".csv":
        rows = list(csv.DictReader(io.StringIO(path.read_text(encoding="utf-8", errors="replace"))))
        if not rows:
            return path.read_text(encoding="utf-8", errors="replace")
        lines = [" | ".join(f"{k}: {v}" for k, v in r.items() if v and str(v).strip()) for r in rows]
        return "\n".join(lines)
    elif suffix in (".md", ".txt", ".rst"):
        return path.read_text(encoding="utf-8", errors="replace")
    else:
        raise ValueError(f"Unsupported file type: {suffix}")


# ── Chunking ───────────────────────────────────────────────────────────────────

def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    words = text.split()
    chunks = []
    i = 0
    while i < len(words):
        chunk = " ".join(words[i:i + chunk_size])
        if chunk.strip():
            chunks.append(chunk.strip())
        i += chunk_size - overlap
    return chunks


# ── Ingest ─────────────────────────────────────────────────────────────────────

def ingest_file(path: Path, advisor: str, title: str = None, verbose: bool = True):
    title = title or path.stem
    if verbose:
        print(f"  Reading {path.name}...")
    text = extract_text(path)
    if not text.strip():
        print(f"  SKIP {path.name} — empty")
        return 0

    chunks = chunk_text(text)
    if verbose:
        print(f"  {len(chunks)} chunks from {len(text.split())} words")

    points = []
    for i, chunk in enumerate(chunks):
        chunk_id = hashlib.md5(f"{path}:{i}:{chunk[:50]}".encode()).hexdigest()
        point_id = int(chunk_id[:16], 16)
        vec = embed(chunk)
        points.append({
            "id": point_id,
            "vector": vec,
            "payload": {
                "source": str(path),
                "title": title,
                "chunk_index": i,
                "chunk_total": len(chunks),
                "text": chunk,
                "advisor": advisor,
            }
        })
        if verbose and (i + 1) % 10 == 0:
            print(f"    embedded {i + 1}/{len(chunks)}...")

    batch_size = 50
    for b in range(0, len(points), batch_size):
        batch = points[b:b + batch_size]
        qdrant("PUT", f"/collections/{advisor}/points?wait=true", {"points": batch})

    if verbose:
        print(f"  ✓ {len(points)} vectors upserted → collection '{advisor}'")
    return len(points)


def ingest_dir(directory: Path, advisor: str, verbose: bool = True):
    total = 0
    for p in sorted(directory.rglob("*")):
        if p.is_file() and p.suffix.lower() in (".pdf", ".md", ".txt", ".csv", ".rst"):
            try:
                total += ingest_file(p, advisor, verbose=verbose)
            except Exception as e:
                print(f"  ✗ {p.name}: {e}")
    return total


# ── CLI ────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="KAI Knowledge Ingestion")
    parser.add_argument("path", nargs="?", help="File or directory to ingest")
    parser.add_argument("--advisor", help="Target advisor collection (kai, beats, sky, ...)")
    parser.add_argument("--title", help="Document title override")
    parser.add_argument("--list", action="store_true", help="List collections and vector counts")
    parser.add_argument("--clear", metavar="ADVISOR", help="Delete all vectors for an advisor")
    args = parser.parse_args()

    if args.list:
        advisors = ['kai', 'beats', 'sky', 'roads', 'coach', 'ember', 'doc', 'creative', 'dev']
        print("Collection status:")
        for a in advisors:
            try:
                r = qdrant("GET", f"/collections/{a}")
                count = r["result"]["points_count"]
                print(f"  {a:12s} {count:6d} vectors")
            except Exception:
                print(f"  {a:12s}  missing")
        return

    if args.clear:
        advisor = args.clear
        qdrant("POST", f"/collections/{advisor}/points/delete", {"filter": {}})
        print(f"✓ Cleared collection: {advisor}")
        return

    if not args.path:
        parser.print_help()
        sys.exit(1)

    if not args.advisor:
        print("Error: --advisor required")
        sys.exit(1)

    p = Path(args.path)
    if not p.exists():
        print(f"Error: {p} does not exist")
        sys.exit(1)

    print(f"Ingesting {p} → {args.advisor}")
    if p.is_dir():
        total = ingest_dir(p, args.advisor)
    else:
        total = ingest_file(p, args.advisor, title=args.title)
    print(f"Done. {total} vectors total.")


if __name__ == "__main__":
    main()
