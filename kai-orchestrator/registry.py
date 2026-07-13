"""Fact Registry read/write path (CONTEXT_SPEC.md §5 Tier 4).

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
What's LIVE here: the original read path plus one trusted seed-ingest write path.
The writer validates a complete batch before touching storage, appends only
``verified`` facts with provenance, and atomically replaces the JSON file.

What's STUBBED, deferred to S7-1: no capture workflow or verify queue; check() is
substring/keyword matching, not semantic; no staleness *policy* beyond the raw
updated_at field Tier 4 sorts on for stalest-first eviction (§6) — S7-1 owns
freshness rules, audit, and the Knowledge Browser UI (§8.29/S7-27).
"""
import fcntl
import hashlib
import json
import os
import re
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path

_REGISTRY_PATH = Path("/vault/00_System/registry/facts.json")
_ADVISOR_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_FACT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_INPUT_FIELDS = {
    "id", "advisor", "project", "task_type", "domain", "key", "value",
    "lifecycle", "source",
}
_IDENTITY_FIELDS = (
    "id", "advisor", "project", "task_type", "domain", "key", "value",
    "lifecycle", "source", "ingested_by",
)


class RegistryValidationError(ValueError):
    """The requested batch is invalid; the registry file was not changed."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _required_text(value, field: str, max_length: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RegistryValidationError(f"{field} must be a non-empty string")
    value = value.strip()
    if len(value) > max_length:
        raise RegistryValidationError(f"{field} exceeds {max_length} characters")
    if any(ord(char) < 32 and char not in "\n\t" for char in value):
        raise RegistryValidationError(f"{field} contains control characters")
    return value


def _optional_scope(value, field: str) -> str | None:
    if value is None:
        return None
    return _required_text(value, field, 128)


def _normalize_fact(
    raw: dict,
    *,
    advisor: str,
    project: str | None,
    task_type: str | None,
    ingested_by: str,
    ingested_at: str,
) -> dict:
    if not isinstance(raw, dict):
        raise RegistryValidationError("each fact must be a JSON object")
    unknown = sorted(set(raw) - _INPUT_FIELDS)
    if unknown:
        raise RegistryValidationError(f"unknown fact fields: {', '.join(unknown)}")

    item_advisor = raw.get("advisor", advisor)
    if item_advisor != advisor:
        raise RegistryValidationError(
            f"fact advisor {item_advisor!r} does not match target advisor {advisor!r}"
        )
    item_project = _optional_scope(raw.get("project", project), "project")
    item_task_type = _optional_scope(raw.get("task_type", task_type), "task_type")
    if project is not None and item_project != project:
        raise RegistryValidationError("fact project does not match the command scope")
    if task_type is not None and item_task_type != task_type:
        raise RegistryValidationError("fact task_type does not match the command scope")

    lifecycle = raw.get("lifecycle", "verified")
    if lifecycle != "verified":
        raise RegistryValidationError("seed-ingest accepts only lifecycle='verified'")

    normalized = {
        "id": raw.get("id"),
        "advisor": advisor,
        "project": item_project,
        "task_type": item_task_type,
        "domain": _required_text(raw.get("domain"), "domain", 128),
        "key": _required_text(raw.get("key"), "key", 256),
        "value": _required_text(raw.get("value"), "value", 16384),
        "lifecycle": "verified",
        "source": _required_text(raw.get("source"), "source", 2048),
        "ingested_at": ingested_at,
        "ingested_by": ingested_by,
        "updated_at": ingested_at,
    }
    if normalized["id"] is None:
        identity = json.dumps(
            {
                k: normalized[k]
                for k in ("advisor", "project", "task_type", "domain", "key", "value", "source")
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        normalized["id"] = f"fact-{hashlib.sha256(identity.encode()).hexdigest()[:20]}"
    elif not isinstance(normalized["id"], str) or not _FACT_ID_RE.fullmatch(normalized["id"]):
        raise RegistryValidationError(
            "id must match [A-Za-z0-9][A-Za-z0-9._:-]{0,127}"
        )
    return normalized


def _same_fact(left: dict, right: dict) -> bool:
    return all(left.get(field) == right.get(field) for field in _IDENTITY_FIELDS)


def append_verified_facts(
    raw_facts: list,
    *,
    advisor: str,
    ingested_by: str,
    project: str | None = None,
    task_type: str | None = None,
    registry_path: Path | str | None = None,
    ingested_at: str | None = None,
) -> dict:
    """Validate and atomically append a trusted seed batch.

    Validation of the incoming batch happens before any filesystem write. A
    stable ID makes exact reruns idempotent; reusing an ID with different
    content rejects the whole batch. Existing legacy facts are preserved
    object-for-object and remain readable by ``facts_for``.
    """
    advisor = _required_text(advisor, "advisor", 64)
    if not _ADVISOR_RE.fullmatch(advisor):
        raise RegistryValidationError(
            "advisor must match [a-z0-9][a-z0-9_-]{0,63}"
        )
    ingested_by = _required_text(ingested_by, "ingested_by", 128)
    project = _optional_scope(project, "project")
    task_type = _optional_scope(task_type, "task_type")
    if not isinstance(raw_facts, list) or not raw_facts:
        raise RegistryValidationError("facts must be a non-empty JSON array")

    timestamp = _required_text(ingested_at or _utc_now(), "ingested_at", 64)
    prepared = [
        _normalize_fact(
            fact,
            advisor=advisor,
            project=project,
            task_type=task_type,
            ingested_by=ingested_by,
            ingested_at=timestamp,
        )
        for fact in raw_facts
    ]
    incoming_ids = [fact["id"] for fact in prepared]
    if len(incoming_ids) != len(set(incoming_ids)):
        raise RegistryValidationError("duplicate fact IDs in input batch")

    path = Path(registry_path) if registry_path is not None else _REGISTRY_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f".{path.name}.lock")

    with lock_path.open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        if path.exists():
            original = path.read_bytes()
            try:
                root = json.loads(original)
            except json.JSONDecodeError as exc:
                raise RegistryValidationError(f"existing registry is invalid JSON: {exc}") from exc
            if not isinstance(root, dict) or not isinstance(root.get("facts"), list):
                raise RegistryValidationError(
                    "existing registry must be an object containing a facts array"
                )
            if not all(isinstance(fact, dict) for fact in root["facts"]):
                raise RegistryValidationError("existing registry facts must all be objects")
            mode = stat.S_IMODE(path.stat().st_mode)
        else:
            original = b""
            root = {
                "_note": "Fact Registry. Seed facts are appended atomically by scripts/ingest.py.",
                "facts": [],
            }
            mode = 0o660

        existing_by_id = {
            fact.get("id"): fact for fact in root["facts"] if isinstance(fact.get("id"), str)
        }
        to_add = []
        already_present = []
        for fact in prepared:
            existing = existing_by_id.get(fact["id"])
            if existing is None:
                to_add.append(fact)
            elif _same_fact(existing, fact):
                already_present.append(fact["id"])
            else:
                raise RegistryValidationError(
                    f"fact id {fact['id']!r} already exists with different content"
                )

        before_sha256 = hashlib.sha256(original).hexdigest() if original else None
        if not to_add:
            return {
                "added": 0,
                "already_present": already_present,
                "facts_before": len(root["facts"]),
                "facts_after": len(root["facts"]),
                "before_sha256": before_sha256,
                "after_sha256": before_sha256,
                "path": str(path),
            }

        root["facts"].extend(to_add)
        encoded = (json.dumps(root, indent=2, ensure_ascii=False) + "\n").encode()
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
        )
        try:
            with os.fdopen(fd, "wb") as tmp_file:
                os.fchmod(tmp_file.fileno(), mode)
                tmp_file.write(encoded)
                tmp_file.flush()
                os.fsync(tmp_file.fileno())
            os.replace(tmp_name, path)
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except Exception:
            try:
                os.unlink(tmp_name)
            except FileNotFoundError:
                pass
            raise

        return {
            "added": len(to_add),
            "added_ids": [fact["id"] for fact in to_add],
            "already_present": already_present,
            "facts_before": len(root["facts"]) - len(to_add),
            "facts_after": len(root["facts"]),
            "before_sha256": before_sha256,
            "after_sha256": hashlib.sha256(encoded).hexdigest(),
            "path": str(path),
        }


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
