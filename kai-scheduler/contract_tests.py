"""KAI-459 Layer 2 — endpoint contract tests.

Pulls /openapi.json from worker-api + council-api, exercises every GET
endpoint that can be safely called without state mutation, and verifies the
service does not 5xx. Writes structured result JSON to vault for the
inv_endpoint_contracts invariant to read.

Tier 1 scope: GET-only smoke tests catching 5xx regressions. Future tiers
can add schemathesis property-based input synthesis + response-schema
validation against the declared OpenAPI types.
"""
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from worker_auth import worker_auth

log = logging.getLogger(__name__)

SERVICES = {
    "worker":  "http://kai-worker-api:8001",
    "council": "http://kai-council-api:8002",
}

# Endpoints whose synthesis is too complex OR that have unwanted side effects
# (firing alerts, calling external paid services, triggering jobs) and so are
# excluded from automated contract testing.
SKIP_PATH_SUBSTRINGS = (
    "/internal/",            # internal probes used by invariants directly
    "/inbox/scan",           # triggers council calls
    "/sprint-a/expire-stale",# fires expiry workflow
    "/wordpress/",           # external WP calls — tested separately
    "/calendar/ics",         # external calendar dep
    "/health/",              # often heavyweight aggregates
)

# Per-type default values for synthesizing required query params
DEFAULTS = {"integer": 1, "number": 1, "boolean": True, "string": "x"}


def fetch_schema(name: str, base_url: str) -> dict:
    r = httpx.get(f"{base_url}/openapi.json", timeout=10, auth=worker_auth())
    r.raise_for_status()
    return r.json()


def _synthesize_query(params: list) -> dict:
    qs = {}
    for p in params:
        if p.get("in") != "query":
            continue
        schema_obj = p.get("schema", {}) or {}
        default = schema_obj.get("default")
        if default is not None:
            qs[p["name"]] = default
        elif p.get("required"):
            qs[p["name"]] = DEFAULTS.get(schema_obj.get("type", "string"), "x")
    return qs


def test_service(name: str, base_url: str, schema: dict) -> list:
    results = []
    for path, methods in schema.get("paths", {}).items():
        if any(skip in path for skip in SKIP_PATH_SUBSTRINGS):
            results.append({"service": name, "path": path, "method": "GET",
                            "status": "skipped", "reason": "skip-list"})
            continue
        if "get" not in methods:
            continue
        op = methods["get"]
        params = op.get("parameters", []) or []
        required_path = [p for p in params if p.get("in") == "path" and p.get("required")]
        if required_path:
            results.append({"service": name, "path": path, "method": "GET",
                            "status": "skipped", "reason": "path-params"})
            continue
        qs = _synthesize_query(params)
        try:
            r = httpx.get(f"{base_url}{path}", params=qs, timeout=15, auth=worker_auth())
            if r.status_code >= 500:
                results.append({"service": name, "path": path, "method": "GET",
                                "status": "fail", "code": r.status_code,
                                "detail": r.text[:200]})
            else:
                results.append({"service": name, "path": path, "method": "GET",
                                "status": "pass", "code": r.status_code})
        except Exception as e:
            results.append({"service": name, "path": path, "method": "GET",
                            "status": "error", "detail": f"{type(e).__name__}: {e}"[:200]})
    return results


def run() -> dict:
    start = time.time()
    all_results = []
    for name, url in SERVICES.items():
        try:
            schema = fetch_schema(name, url)
        except Exception as e:
            all_results.append({"service": name, "path": "/openapi.json", "method": "GET",
                                "status": "error", "detail": f"schema fetch failed: {e}"})
            continue
        all_results.extend(test_service(name, url, schema))

    elapsed = round(time.time() - start, 2)
    payload = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_s": elapsed,
        "summary": {
            "total":   len(all_results),
            "pass":    sum(1 for r in all_results if r["status"] == "pass"),
            "fail":    sum(1 for r in all_results if r["status"] == "fail"),
            "error":   sum(1 for r in all_results if r["status"] == "error"),
            "skipped": sum(1 for r in all_results if r["status"] == "skipped"),
        },
        "results": all_results,
    }
    out = Path("/vault/00_System/contract_test_results.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2))
    return payload


if __name__ == "__main__":
    p = run()
    print(json.dumps(p["summary"], indent=2))
