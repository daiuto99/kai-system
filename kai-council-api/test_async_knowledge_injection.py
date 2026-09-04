#!/usr/bin/env python3
"""[C3/KAI-bc55d9a4] Deterministic proof that the async advisor path now injects
the advisor's curated knowledge into the mini shim call. Mocks the knowledge
endpoint + the shim POST (no mini, no cloud) and asserts the shim RECEIVES the
curated block prepended to the message — the change (a) contract, independent of
mini latency. Run inside kai-council-api: docker exec ... python3 /app/test_async_knowledge_injection.py
"""
import json
import shutil
from pathlib import Path

import router

SENTINEL = "<knowledge trust=\"curated\">CURATED-BLOCK-SENTINEL-9f3a</knowledge>"
TEST_ADVISOR = "knowledge_injection_test"  # throwaway dm_log dir; cleaned up below
captured = {}


class _Resp:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        pass


def _fake_get(url, params=None, timeout=None):
    captured["get_url"] = url
    captured["get_params"] = params
    return _Resp({"ok": True, "knowledge_text": SENTINEL, "hits": [{"x": 1}]})


def _fake_post(url, json=None, timeout=None):
    captured["post_url"] = url
    captured["post_json"] = json
    return _Resp({"reply": "ok (mocked shim)"})


def main() -> int:
    router.httpx.get = _fake_get
    router.httpx.post = _fake_post
    try:
        router._run_hermes_async(TEST_ADVISOR, "what is the meridian catalog code?", "unit-user")
    finally:
        # remove the throwaway dm_log dir this writes
        d = Path("/vault") / "60_Council" / TEST_ADVISOR
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)

    ok = True
    checks = []

    hit_knowledge_endpoint = str(captured.get("get_url", "")).endswith("/context/knowledge")
    checks.append(("fetched curated knowledge from /context/knowledge", hit_knowledge_endpoint))
    ok &= hit_knowledge_endpoint

    shim_msg = (captured.get("post_json") or {}).get("message", "")
    injected = SENTINEL in shim_msg
    checks.append(("shim message CONTAINS the curated knowledge block", injected))
    ok &= injected

    orig_present = "meridian catalog code" in shim_msg.lower()
    checks.append(("shim message still carries the original question", orig_present))
    ok &= orig_present

    posted_to_shim = str(captured.get("post_url", "")).endswith("/advisor")
    checks.append(("posted to the advisor shim", posted_to_shim))
    ok &= posted_to_shim

    for label, passed in checks:
        print(f"  [{'PASS' if passed else 'FAIL'}] {label}")
    print("RESULT " + json.dumps({"ok": bool(ok), "knowledge_len": len(shim_msg)}))
    return 0 if ok else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
