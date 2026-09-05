"""KAI-e9ad1421 — registry <-> Tier 3 collection-allowlist drift guard.

specialists.json is the authoritative specialist registry; context_service's
_VALID_COLLECTIONS is the §4.2/L4 allowlist a Tier 3 (Qdrant) recall is
validated against. A specialist that is in the registry but not in the
allowlist gets NO recall — silently, with only a log warning.

Today that gap is deliberate for `wordpress`: it has no production Qdrant
collection until WP Foundation / M-E seeds one, so the allowlist correctly
omits it. The forget-shaped risk is the seeding moment: the collection lands
and the allowlist entry does not, leaving the specialist permanently mute.

These tests enforce the pairing so that can't happen silently:
  * every registry specialist must be either allowlisted or explicitly waived
    in _UNSEEDED_SPECIALISTS (declared drift, never silent drift);
  * a waived name must not already be allowlisted (the waiver must be removed
    in the same change that allowlists it);
  * against live Qdrant, every registry specialist that HAS a collection must
    be allowlisted — the assertion the ticket asks for.
"""
import json
import os
from pathlib import Path

import pytest

import context_service

SPECIALISTS_PATH = Path(os.environ.get("VAULT_PATH", "/vault")) / "00_System" / "specialists.json"


def _registry_ids():
    try:
        data = json.loads(SPECIALISTS_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return sorted({s["id"] for s in data if isinstance(s, dict) and s.get("id")})


def _live_collections():
    """Collection names live in Qdrant, or None if Qdrant is unreachable."""
    try:
        import httpx

        with httpx.Client(timeout=5) as hc:
            r = hc.get(f"{context_service.QDRANT_URL}/collections")
            if r.status_code != 200:
                return None
            return {c["name"] for c in r.json().get("result", {}).get("collections", [])}
    except Exception:
        return None


def test_every_registry_specialist_is_allowlisted_or_explicitly_waived():
    ids = _registry_ids()
    if ids is None:
        pytest.skip(f"specialists.json not readable at {SPECIALISTS_PATH}")

    undeclared = [
        sid for sid in ids
        if sid not in context_service._VALID_COLLECTIONS
        and sid not in context_service._UNSEEDED_SPECIALISTS
    ]
    assert not undeclared, (
        "registry specialists are neither in _VALID_COLLECTIONS nor waived in "
        f"_UNSEEDED_SPECIALISTS, so their Tier 3 recall silently returns empty: {undeclared}. "
        "Seed the Qdrant collection and add the allowlist entry together, or waive the name."
    )


def test_waiver_and_allowlist_are_mutually_exclusive():
    """A name is waived (no collection) or allowlisted (collection exists) — never both.
    Being in both means a seeding change added the entry and forgot to drop the waiver,
    leaving a stale claim that the specialist is unseeded."""
    overlap = sorted(context_service._UNSEEDED_SPECIALISTS & context_service._VALID_COLLECTIONS)
    assert not overlap, (
        f"names are both allowlisted and waived as unseeded: {overlap}. "
        "Remove them from _UNSEEDED_SPECIALISTS in the change that allowlists them."
    )


def test_registry_specialists_with_a_live_collection_are_allowlisted():
    """The ticket's assertion: every registry specialist that HAS a Qdrant collection
    must be in _VALID_COLLECTIONS. Fires the moment WP seeding creates the `wordpress`
    collection without adding its allowlist entry."""
    ids = _registry_ids()
    if ids is None:
        pytest.skip(f"specialists.json not readable at {SPECIALISTS_PATH}")
    live = _live_collections()
    if live is None:
        pytest.skip(f"Qdrant not reachable at {context_service.QDRANT_URL}")

    seeded_but_unallowlisted = [
        sid for sid in ids
        if sid in live and sid not in context_service._VALID_COLLECTIONS
    ]
    assert not seeded_but_unallowlisted, (
        "these specialists have a live Qdrant collection but are not in _VALID_COLLECTIONS, "
        f"so their Tier 3 recall silently returns empty: {seeded_but_unallowlisted}"
    )


def test_waived_names_have_no_live_collection():
    """Converse of the above: a waived name must not already be seeded — if it is,
    the waiver is stale and the allowlist entry is the missing half of the pair."""
    live = _live_collections()
    if live is None:
        pytest.skip(f"Qdrant not reachable at {context_service.QDRANT_URL}")

    stale = sorted(context_service._UNSEEDED_SPECIALISTS & live)
    assert not stale, (
        f"waived-as-unseeded names have a live Qdrant collection: {stale}. "
        "Add them to _VALID_COLLECTIONS and remove the waiver in the same change."
    )
