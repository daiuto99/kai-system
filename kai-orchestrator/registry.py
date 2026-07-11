"""Fact Registry read path (CONTEXT_SPEC.md §5 Tier 4 / MEG §8.23 registry.get/registry.check).

S7-1 (Plane 532c0d4a) — the ticket that builds the full registry (ingest workflow,
verify-lifecycle queue per §8.29, semantic check(), the Knowledge Browser) — is still
Backlog, not built. This module is the minimal file-backed read path Tier 4 assembly
needs now, built against the storage location S7-1 already named
(`/vault/00_System/registry/facts.json`, MEG line 425) so S7-1's eventual build
inherits this schema rather than migrating off a different one.

What's LIVE here: file-backed storage, domain+key exact-match get() (verified-only,
most-recently-updated wins on multiple matches), substring/keyword check(), advisor
scoping, facts_for() the Tier 4 assembly feed (advisor + optional project/task_type
match, §5).
What's STUBBED, deferred to S7-1: no write path or verify-queue (facts.json is
hand-seeded, not populated by a capture workflow); check() is substring/keyword
matching, not semantic; no staleness *policy* beyond the raw updated_at field Tier 4
sorts on for stalest-first eviction (§6) — S7-1 owns freshness rules, audit, and the
Knowledge Browser UI (§8.29/S7-27).
"""
import json
from pathlib import Path

_REGISTRY_PATH = Path("/vault/00_System/registry/facts.json")


def _load() -> list:
    if not _REGISTRY_PATH.exists():
        return []
    try:
        return json.loads(_REGISTRY_PATH.read_text()).get("facts", [])
    except Exception:
        return []


def get(domain: str, key: str, advisor: str = None, project: str = None) -> dict | None:
    """Exact domain+key lookup, verified-lifecycle only. Multiple matches (e.g. an
    advisor-scoped and a project-scoped fact under the same domain/key) resolve to
    the most recently updated."""
    candidates = [
        f for f in _load()
        if f.get("domain") == domain and f.get("key") == key
        and f.get("lifecycle") == "verified"
        and (advisor is None or f.get("advisor") in (None, advisor))
        and (project is None or f.get("project") in (None, project))
    ]
    if not candidates:
        return None
    return sorted(candidates, key=lambda f: f.get("updated_at", ""), reverse=True)[0]


def check(claim: str, advisor: str = None) -> list:
    """Best-effort substring/keyword match of `claim` against verified fact
    key+value text. Stubbed matching (see module docstring) until S7-1 builds real
    semantic check() — good enough for a verifier to ask 'do we have a fact that
    bears on this claim' and get a candidate list, not a guaranteed-complete answer."""
    if not claim or not claim.strip():
        return []
    needle = claim.lower()
    out = []
    for f in _load():
        if f.get("lifecycle") != "verified":
            continue
        if advisor is not None and f.get("advisor") not in (None, advisor):
            continue
        haystack = f"{f.get('key','')} {f.get('value','')}".lower()
        if needle in haystack or any(w in haystack for w in needle.split() if len(w) > 4):
            out.append(f)
    return out


def facts_for(advisor: str, project: str = None, task_type: str = None) -> list:
    """All verified facts matching advisor (+ optional project/task_type) — the
    Tier 4 assembly feed (§5: 'only entries matching the task/project'). A fact with
    project=None (or task_type=None) is advisor-general and always matches; a fact
    with a project/task_type set only matches an assemble() call naming the same
    one."""
    if not advisor:
        return []
    out = []
    for f in _load():
        if f.get("lifecycle") != "verified":
            continue
        if f.get("advisor") not in (None, advisor):
            continue
        if project is not None and f.get("project") not in (None, project):
            continue
        if task_type is not None and f.get("task_type") not in (None, task_type):
            continue
        out.append(f)
    return out
