"""
Anthropic Admin Cost/Usage API bridge — the "All Anthropic Charges" panel.

Exposes GET /anthropic/billing?days=N. Reads an Admin API key (sk-ant-admin01-...)
from a Docker secret slot; until the key is present it returns {configured: False}
so the dashboard renders an "add key" state instead of erroring.

Auth to the worker is handled globally by BasicAuthMiddleware in main.py.

Build-vs-Run split: the Cost API groups spend by workspace. A workspace->bucket
map (optional JSON slot) tags each workspace as "build" (Claude Code dev sessions)
or "run" (council/Buzz). Unmapped workspaces show as "unclassified" until Leo maps
them. See docs/KAI_MASTER_EXECUTION_GUIDE.md and Plane ticket
[DASHBOARD] All Anthropic Charges panel.
"""
import json
import datetime as _dt
from pathlib import Path

import httpx
from fastapi import APIRouter, Query

router = APIRouter()

_ADMIN_KEY_FILES = (
    Path("/run/secrets/anthropic_admin_key"),
    Path("/home/leo/kai-system/secrets/anthropic_admin_key.txt"),
)
_COST_MAP_FILES = (
    Path("/run/secrets/anthropic_cost_map"),
    Path("/home/leo/kai-system/secrets/anthropic_cost_map.json"),
)
_BASE = "https://api.anthropic.com/v1/organizations"
_VERSION = "2023-06-01"
_SLOT_HINT = ("Mint an Admin key (sk-ant-admin01-...) at console.anthropic.com -> "
              "Settings -> Admin keys, write it to kai-system/secrets/anthropic_admin_key.txt "
              "on the worker, then `docker compose up -d kai-worker-api`.")


def _load_admin_key():
    for p in _ADMIN_KEY_FILES:
        try:
            if p.exists():
                v = p.read_text().strip()
                if v:
                    return v
        except Exception:
            pass
    return None


def _load_cost_map():
    for p in _COST_MAP_FILES:
        try:
            if p.exists():
                return json.loads(p.read_text() or "{}")
        except Exception:
            pass
    return {}


def _classify(ws_id, cost_map):
    key = ws_id if ws_id is not None else "default"
    return cost_map.get(key) or cost_map.get(ws_id) or "unclassified"


def _as_cents(result):
    # Cost API reports USD as a decimal string in cents; field name has drifted
    # across the beta, so accept the first present of these.
    for f in ("amount", "cost", "cost_usd", "value"):
        v = result.get(f)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                return 0.0
    return 0.0


def _paged_get(client, url, params, headers):
    """Follow has_more/next_page; yield each bucket dict."""
    page = None
    for _ in range(50):  # hard stop; 31d daily is a few pages at most
        p = dict(params)
        if page:
            p["page"] = page
        r = client.get(url, params=p, headers=headers, timeout=30.0)
        r.raise_for_status()
        body = r.json()
        for bucket in (body.get("data") or []):
            yield bucket
        if body.get("has_more") and body.get("next_page"):
            page = body["next_page"]
        else:
            break


@router.get("/anthropic/billing")
def anthropic_billing(days: int = Query(30, ge=1, le=180)):
    key = _load_admin_key()
    if not key:
        return {
            "configured": False,
            "reason": "no admin key in slot",
            "slot": "kai-system/secrets/anthropic_admin_key.txt",
            "hint": _SLOT_HINT,
        }

    cost_map = _load_cost_map()
    end = _dt.datetime.now(_dt.timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0) + _dt.timedelta(days=1)
    start = end - _dt.timedelta(days=days + 1)
    starting_at = start.strftime("%Y-%m-%dT00:00:00Z")
    ending_at = end.strftime("%Y-%m-%dT00:00:00Z")
    headers = {
        "x-api-key": key,
        "anthropic-version": _VERSION,
        "User-Agent": "KAI-dashboard/1.0 (https://kai.sonicink.space)",
    }

    buckets = {"build": 0.0, "run": 0.0, "unclassified": 0.0}
    by_workspace = {}     # ws_id/"default" -> {bucket, cents}
    by_model = {}         # model -> cents
    total_cents = 0.0

    try:
        with httpx.Client() as client:
            # --- COST report: workspace + description grouping (daily only) ---
            for b in _paged_get(client, f"{_BASE}/cost_report",
                                {"starting_at": starting_at, "ending_at": ending_at,
                                 "group_by[]": ["workspace_id", "description"]},
                                headers):
                for res in (b.get("results") or []):
                    cents = _as_cents(res)
                    total_cents += cents
                    ws = res.get("workspace_id")
                    bucket = _classify(ws, cost_map)
                    buckets[bucket] = buckets.get(bucket, 0.0) + cents
                    wkey = ws if ws is not None else "default"
                    row = by_workspace.setdefault(wkey, {"bucket": bucket, "cents": 0.0})
                    row["cents"] += cents
                    model = res.get("model") or (res.get("description") or "").strip() or "other"
                    by_model[model] = by_model.get(model, 0.0) + cents
    except httpx.HTTPStatusError as e:
        code = e.response.status_code
        detail = "invalid admin key" if code == 401 else (
            "admin key lacks org access, or this is an individual (non-org) account"
            if code == 403 else f"HTTP {code}")
        return {"configured": True, "error": detail, "http_status": code,
                "range_days": days}
    except Exception as e:  # network/parse
        return {"configured": True, "error": f"{type(e).__name__}: {e}",
                "range_days": days}

    def _fmt(d):
        return sorted(({"key": k, "usd": round(v / 100.0, 2)}
                       for k, v in d.items()), key=lambda x: -x["usd"])

    return {
        "configured": True,
        "range_days": days,
        "starting_at": starting_at,
        "ending_at": ending_at,
        "total_usd": round(total_cents / 100.0, 2),
        "buckets": {k: round(v / 100.0, 2) for k, v in buckets.items()},
        "by_workspace": sorted(
            ({"workspace": k, "bucket": r["bucket"], "usd": round(r["cents"] / 100.0, 2)}
             for k, r in by_workspace.items()), key=lambda x: -x["usd"]),
        "by_model": _fmt(by_model),
        "unmapped": [k for k, r in by_workspace.items() if r["bucket"] == "unclassified"],
        "cost_map_slot": "kai-system/secrets/anthropic_cost_map.json",
    }
