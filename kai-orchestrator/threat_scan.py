"""Promptware defense — threat-pattern registry loader + scanner (CONTEXT_SPEC.md §10).

Single source of truth for instruction-impersonation / system-prompt-mimicry /
tool-call-syntax / delimiter-forgery detection. Consumed at load time by
context_service.py's Tier 3 recall; the same registry file is the intended
consumer for future ingest-time scanning (S7-3) and tool-result guards — no
per-consumer pattern copies (§10 point 3).

A hit here is detection/logging only — the structural defense against a
recalled payload breaking out of its <recalled> wrapper is the delimiter
escaping applied in context_service.py, not this scanner.
"""
import json
import re
from pathlib import Path

_REGISTRY_PATH = Path(__file__).parent / "threat_patterns.json"
_compiled = None


def load_patterns() -> list:
    global _compiled
    if _compiled is None:
        data = json.loads(_REGISTRY_PATH.read_text())
        _compiled = [
            {**p, "_re": re.compile(p["pattern"], re.IGNORECASE | re.MULTILINE)}
            for p in data["patterns"]
        ]
    return _compiled


def scan_content(text: str, source: str = "") -> list:
    """Returns hit dicts: {pattern_id, category, severity, source, snippet}."""
    if not text:
        return []
    hits = []
    for p in load_patterns():
        m = p["_re"].search(text)
        if m:
            hits.append({
                "pattern_id": p["id"],
                "category": p["category"],
                "severity": p["severity"],
                "source": source,
                "snippet": text[max(0, m.start() - 20):m.end() + 20],
            })
    return hits
