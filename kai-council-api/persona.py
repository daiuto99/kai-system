import logging

import httpx
from fastapi import HTTPException

from council_config import ORCHESTRATOR_URL

logger = logging.getLogger(__name__)


def load_persona(advisor: str, channel: str = None) -> str:
    """CONTEXT_SPEC §3/§13 Tier 5 migration: persona.py ceases to be an
    assembly point. The former assemble_prompt()/_register() local-file
    assembly logic now lives in kai-orchestrator/context_service.py as
    tier5_standing_context(); this is a thin client of that Memory Service
    endpoint, keeping the same string-return contract for callers
    (graphs/nodes.py, graphs/bug_nodes.py, internal_persona_check).
    """
    try:
        params = {"advisor": advisor}
        if channel:
            params["channel"] = channel
        r = httpx.get(f"{ORCHESTRATOR_URL}/context/persona", params=params, timeout=15)
    except httpx.HTTPError as e:
        logger.error("load_persona: Memory Service unreachable (advisor=%s): %s", advisor, e)
        raise HTTPException(status_code=502, detail=f"Memory Service unreachable: {e}")

    if r.status_code == 404:
        raise HTTPException(status_code=404, detail=f"Persona not found: {advisor}")
    r.raise_for_status()

    data = r.json()
    stable_text = data.get("stable_text", "")
    volatile_text = data.get("volatile_text", "")
    return stable_text + ("\n\n---\n\n" + volatile_text if volatile_text else "")
