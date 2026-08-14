"""CUR-1 — System Currency read surface.

Serves the freshness_state.json written by scripts/currency_scan.py (which runs
on the HOST — it needs host apt + docker; the container only reads the result
over the read-only /shared mount). Honest by construction: if no scan has run,
this reports not-checked, never a synthetic green.
"""
import json
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException

router = APIRouter()

STATE_FILE = Path(os.environ.get("SHARED_DIR", "/shared")) / "currency" / "freshness_state.json"


@router.get("/currency/state")
def currency_state():
    """Return the latest system-currency freshness map (read-only)."""
    if not STATE_FILE.exists():
        return {
            "status": "not-checked",
            "detail": "no currency scan has run yet",
            "layers": {},
            "rollup": {"fresh": 0, "stale": 0, "not_checked": 0, "total": 0},
        }
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"currency state unreadable: {exc}")
