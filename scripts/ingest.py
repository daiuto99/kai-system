#!/usr/bin/env python3
"""
KAI Knowledge Ingestion Pipeline
Usage:
  python3 ingest.py <file_or_dir> --advisor <name> [--title <title>]
  python3 ingest.py --facts <facts.json> --advisor <name> --ingested-by <name>
                    [--project <project>] [--task-type <type>]
  python3 ingest.py --list              # list collections + vector counts
  python3 ingest.py --clear <advisor>   # delete all vectors for an advisor

Supported: .pdf, .md, .txt, .csv
Embeddings: Ollama nomic-embed-text (768-dim)
Vector DB: Qdrant at localhost:6333
"""
import argparse
import csv
import hashlib
import importlib.util
import io
import json
import os
import re
import sys
import urllib.request
import urllib.error
from pathlib import Path

QDRANT = os.environ.get("QDRANT_URL", "http://localhost:6333")
OLLAMA = os.environ.get("OLLAMA_URL", "http://localhost:11434")
CHUNK_SIZE = 400    # words per chunk
CHUNK_OVERLAP = 50  # word overlap between chunks
ADVISOR_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


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


def validate_advisor(advisor: str) -> str:
    if not isinstance(advisor, str) or not ADVISOR_RE.fullmatch(advisor):
        raise ValueError("advisor must match [a-z0-9][a-z0-9_-]{0,63}")
    return advisor


def ensure_collection(advisor: str, vector_size: int) -> None:
    """Create a new advisor collection only when it does not already exist."""
    try:
        qdrant("GET", f"/collections/{advisor}")
        return
    except RuntimeError as exc:
        if "Qdrant 404:" not in str(exc):
            raise
    qdrant(
        "PUT",
        f"/collections/{advisor}",
        {"vectors": {"size": vector_size, "distance": "Cosine"}},
    )


def _registry_module():
    path = Path(__file__).resolve().parents[1] / "kai-orchestrator" / "registry.py"
    spec = importlib.util.spec_from_file_location("kai_fact_registry", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load Fact Registry writer from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _default_registry_path() -> Path:
    configured = os.environ.get("FACT_REGISTRY_PATH")
    if configured:
        return Path(configured)
    host_path = Path("/home/leo/vault/00_System/registry/facts.json")
    if host_path.parent.exists():
        return host_path
    return Path("/vault/00_System/registry/facts.json")


def ingest_facts(
    path: Path,
    *,
    advisor: str,
    ingested_by: str,
    project: str = None,
    task_type: str = None,
    registry_path: Path = None,
    registry_module=None,
) -> dict:
    registry = registry_module or _registry_module()
    try:
        root = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise registry.RegistryValidationError(f"invalid fact input JSON: {exc}") from exc
    if not isinstance(root, dict) or set(root) != {"facts"}:
        raise registry.RegistryValidationError(
            "fact input must be a JSON object containing only a facts array"
        )
    return registry.append_verified_facts(
        root["facts"],
        advisor=advisor,
        project=project,
        task_type=task_type,
        ingested_by=ingested_by,
        registry_path=registry_path or _default_registry_path(),
    )


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
    advisor = validate_advisor(advisor)
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

    if points:
        ensure_collection(advisor, len(points[0]["vector"]))

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
    parser.add_argument("--facts", metavar="JSON", help="Append a verified Fact Registry batch")
    parser.add_argument("--project", help="Optional project scope for every fact in the batch")
    parser.add_argument("--task-type", help="Optional task_type scope for every fact in the batch")
    parser.add_argument("--ingested-by", help="Required provenance actor for --facts")
    parser.add_argument(
        "--registry-path",
        help="Fact Registry path override (tests only; defaults to the live vault path)",
    )
    parser.add_argument("--list", action="store_true", help="List collections and vector counts")
    parser.add_argument("--clear", metavar="ADVISOR", help="Delete all vectors for an advisor")
    args = parser.parse_args()

    if args.facts:
        if args.path or args.list or args.clear or args.title:
            parser.error("--facts cannot be combined with prose path, --list, --clear, or --title")
        if not args.advisor:
            parser.error("--advisor is required with --facts")
        if not args.ingested_by:
            parser.error("--ingested-by is required with --facts")
        facts_path = Path(args.facts)
        if not facts_path.is_file():
            parser.error(f"fact input does not exist: {facts_path}")
        try:
            registry = _registry_module()
        except Exception as exc:
            print(f"Error: Fact Registry writer unavailable: {exc}", file=sys.stderr)
            sys.exit(1)
        try:
            advisor = validate_advisor(args.advisor)
            result = ingest_facts(
                facts_path,
                advisor=advisor,
                project=args.project,
                task_type=args.task_type,
                ingested_by=args.ingested_by,
                registry_path=Path(args.registry_path) if args.registry_path else None,
                registry_module=registry,
            )
        except registry.RegistryValidationError as exc:
            print(f"Error: fact ingest rejected; registry unchanged: {exc}", file=sys.stderr)
            sys.exit(2)
        except Exception as exc:
            print(f"Error: fact ingest failed: {exc}", file=sys.stderr)
            sys.exit(1)
        print(json.dumps({"ok": True, **result}, indent=2))
        return

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
        advisor = validate_advisor(args.clear)
        qdrant("POST", f"/collections/{advisor}/points/delete", {"filter": {}})
        print(f"✓ Cleared collection: {advisor}")
        return

    if not args.path:
        parser.print_help()
        sys.exit(1)

    if not args.advisor:
        print("Error: --advisor required")
        sys.exit(1)

    try:
        validate_advisor(args.advisor)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(2)

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
