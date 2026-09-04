#!/usr/bin/env python3
"""[C3][KAI-bc55d9a4] Advisor knowledge-use witness.

Proves — with an unforgeable receipt — that an advisor demonstrably USES its
own domain knowledge at inference time, ADDS to it, and can be tracked growing
DEEPER over time. Runs INSIDE the kai-council-api container so it reaches the
same kai-qdrant / kai-ollama / council router the live recall path uses.

The proof (why it cannot false-green, per shared/witness.py):
  1. USE  — plant a random codeword that exists NOWHERE but this advisor's
            Qdrant collection (freshly embedded this run), then drive a REAL
            /council/message turn asking for it. The reply can only contain the
            codeword if Tier-3 recall (context_service.assemble) actually wired
            the advisor's collection into the system prompt and the model read
            it. The question never carries the codeword, so an echo is
            impossible; a broken recall path yields a reply WITHOUT the nonce.
  2. ADD  — the plant goes through the same embed+upsert path ingest.py uses;
            the collection's points_count must rise by the vectors added.
  3. DEEPEN — each run appends the advisor's real (post-cleanup) vector count to
            a persistent ledger in the vault, so growth over time is measurable,
            not asserted.
Cleanup deletes the planted point by its witness tag and re-checks the count so
the advisor's real memory is never polluted.

Emits one line:  WITNESS_RESULT {json}
Env: ADVISOR_WITNESS_TARGET (default sky), ADVISOR_WITNESS_TIMEOUT (default 120).
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

# The witness always runs INSIDE kai-council-api, where Qdrant/Ollama live at
# their docker-network names (not localhost). ingest.py reads these at import,
# so set them before importing — mirrors the env _h_ingest passes to ingest.py.
os.environ.setdefault("QDRANT_URL", "http://kai-qdrant:6333")
os.environ.setdefault("OLLAMA_URL", "http://kai-ollama:11434")

# Reuse the live ingestion helpers — same Qdrant/Ollama the real path uses.
from ingest import embed, qdrant  # noqa: E402  (/app on PYTHONPATH inside container)

COUNCIL_URL = os.environ.get("COUNCIL_URL", "http://localhost:8002")
LEDGER_PATH = os.environ.get(
    "KNOWLEDGE_WITNESS_LEDGER", "/vault/00_System/knowledge_witness_ledger.jsonl"
)


def _collection_count(advisor: str):
    """Live points_count for an advisor collection, or None if it doesn't exist."""
    try:
        r = qdrant("GET", f"/collections/{advisor}")
    except RuntimeError as e:
        if "404" in str(e):
            return None
        raise
    return r["result"]["points_count"]


_AUTH_FILES = (
    "/run/secrets/kai_worker_auth",
    "/run/wp_secrets/kai_worker_auth.txt",
    "/home/leo/kai-system/secrets/kai_worker_auth.txt",
)


def _auth_header() -> str:
    """Basic-auth header for the council boundary — same credential main.py's
    BasicAuthMiddleware expects. The value is used only in the header, never
    logged or emitted (credential-transport law)."""
    import base64
    for path in _AUTH_FILES:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                cred = fh.read().strip()
        except OSError:
            continue
        if ":" in cred:
            return "Basic " + base64.b64encode(cred.encode()).decode()
    raise RuntimeError("council credential not found in any known auth file")


def _council_turn(channel: str, message: str, timeout: int, conv_tag: str) -> dict:
    # conv_tag is unique per run so each witness turn lands in a FRESH
    # conversation key (advisor/device/place/thread) — otherwise prior runs'
    # refusals accumulate as Tier-1 history and the advisor just repeats "no".
    body = json.dumps({
        "channel": channel,
        "message": message,
        "user_id": f"knowledge-witness-{conv_tag}",
        "trigger_source": f"witness:knowledge:{conv_tag}",
    }).encode()
    req = urllib.request.Request(
        f"{COUNCIL_URL}/council/message", data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": _auth_header()}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _deepen(advisor: str, current_count: int) -> dict:
    """Append the real (post-cleanup) count and report growth vs the first sample."""
    samples = []
    try:
        with open(LEDGER_PATH, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("advisor") == advisor and isinstance(row.get("count"), int):
                    samples.append(row["count"])
    except FileNotFoundError:
        pass
    first = samples[0] if samples else current_count
    row = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "advisor": advisor,
        "count": current_count,
    }
    try:
        os.makedirs(os.path.dirname(LEDGER_PATH), exist_ok=True)
        with open(LEDGER_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
        ledger_ok = True
    except OSError:
        ledger_ok = False
    return {
        "first_count": first,
        "current_count": current_count,
        "growth_since_first": current_count - first,
        "samples": len(samples) + (1 if ledger_ok else 0),
        "ledger_ok": ledger_ok,
    }


def _nonce_present(advisor: str, nonce: str):
    """True/False if a point tagged witness_nonce==nonce is/ isn't in the
    collection; None if the check itself errored (treated as 'not verified')."""
    try:
        r = qdrant("POST", f"/collections/{advisor}/points/scroll", {
            "filter": {"must": [{"key": "witness_nonce", "match": {"value": nonce}}]},
            "limit": 1,
        })
    except RuntimeError:
        return None
    return len(r.get("result", {}).get("points", [])) > 0


def _cleanup(advisor: str, nonce: str) -> bool:
    """Delete the planted point by its witness tag and CONFIRM absence by tag
    (not by count — a count check races concurrent real ingestion). Retries."""
    for _ in range(3):
        try:
            qdrant("POST", f"/collections/{advisor}/points/delete?wait=true",
                   {"filter": {"must": [{"key": "witness_nonce", "match": {"value": nonce}}]}})
        except RuntimeError:
            continue
        if _nonce_present(advisor, nonce) is False:
            return True
    return False


def run(advisor: str, timeout: int) -> dict:
    t0 = time.time()
    nonce = "KNOW-" + secrets.token_hex(5).upper()
    # F4: the conversation identity is a SEPARATE random tag, never the nonce, so
    # the nonce reaches the system under test through EXACTLY ONE channel — the
    # Qdrant payload. If it later shows up in the reply, recall is the only path
    # it could have taken; the unforgeability invariant holds.
    conv_tag = secrets.token_hex(6)
    point_id = int(hashlib.md5(f"knowledge-witness:{nonce}".encode()).hexdigest()[:16], 16)

    base = _collection_count(advisor)
    if base is None:
        return {"observed": False, "nonce": nonce,
                "reason": f"advisor collection '{advisor}' does not exist — nothing to recall from"}

    recall_observed = False
    reply = ""
    model = ""
    add_delta = 0
    put_attempted = False
    cleanup_ok = False
    try:
        # 1) ADD — plant a NEUTRAL domain fact (no imperative) via the real
        #     embed+upsert path. An instruction-shaped payload would correctly
        #     trip the injection guard, proving the guard, not knowledge USE.
        fact = (
            f"Knowledge-base reference entry for {advisor}. The internal catalog "
            f"code for the Meridian reference archive is {nonce}. The Meridian "
            f"reference archive is filed in {advisor}'s records under this catalog code."
        )
        vec = embed(fact)
        # Mark BEFORE the PUT (F3): if Qdrant commits the point but the client
        # times out / loses the response, the point still exists — cleanup by
        # tag is idempotent, so it must run for any ATTEMPTED put, not only a
        # confirmed one.
        put_attempted = True
        qdrant("PUT", f"/collections/{advisor}/points?wait=true", {"points": [{
            "id": point_id,
            "vector": vec,
            "payload": {
                "source": "knowledge_witness",
                "title": "knowledge-witness",
                "text": fact,
                "advisor": advisor,
                "witness_nonce": nonce,
            },
        }]})
        after_plant = _collection_count(advisor)
        add_delta = (after_plant or 0) - base

        if add_delta >= 1:
            # 2) USE — drive a real council turn; the reply can only carry the
            #     codeword if the advisor's collection reached the prompt.
            resp = _council_turn(
                advisor,
                "Quick lookup from your knowledge base: what is the internal "
                "catalog code for the Meridian reference archive? Reply with "
                "just the code.",
                timeout,
                conv_tag,
            )
            reply = str(resp.get("reply", ""))
            model = str(resp.get("model", ""))
            recall_observed = nonce in reply
    finally:
        # 3) Cleanup wraps the PUT (F3): any planted vector is removed and its
        #     absence confirmed by tag, even if the PUT response was lost or the
        #     turn/count check threw. Runs for any ATTEMPTED put (idempotent).
        if put_attempted:
            cleanup_ok = _cleanup(advisor, nonce)

    # GREEN requires BOTH: the recall proved AND the planted vector is gone. A
    # cleanup failure leaves state polluted, so it must not pass as green (F3).
    green = recall_observed and cleanup_ok

    deepen = _deepen(advisor, base)  # ledger the REAL (pre-plant) baseline count

    if green:
        reason = f"advisor '{advisor}' recalled planted codeword; cleanup verified (add_delta={add_delta})"
    elif add_delta < 1:
        reason = f"plant failed — add_delta={add_delta}"
    elif not recall_observed:
        reason = (f"reply did NOT surface the planted codeword — recall path not proven "
                  f"(reply[:120]={reply[:120]!r})")
    else:
        reason = "recall proven BUT cleanup FAILED — planted vector may persist; withholding green"
    return {
        "observed": green,
        "recall_observed": recall_observed,
        "nonce": nonce,
        "boundary": "council-recall->advisor-llm",
        "minted_by": "advisor-llm-reply",
        "raw_ref": f"{model}:{hashlib.md5(reply.encode()).hexdigest()[:12]}" if reply else "",
        "elapsed_s": round(time.time() - t0, 1),
        "add_delta": add_delta,
        "count": base,
        "cleanup_ok": cleanup_ok,
        "deepen": deepen,
        "model": model,
        "reason": reason,
    }


def main() -> int:
    # Default kai — the SYNCHRONOUS advisor (F5). An async target (sky/roads)
    # only acks on /council/message; its real reply lands later in the dm_log,
    # so a same-call observe would delete the vector before the mini fetched it.
    # The async curated-knowledge injection is proven deterministically by
    # test_async_knowledge_injection.py.
    advisor = os.environ.get("ADVISOR_WITNESS_TARGET", "kai")
    timeout = int(os.environ.get("ADVISOR_WITNESS_TIMEOUT", "120"))
    try:
        obs = run(advisor, timeout)
    except Exception as e:  # noqa: BLE001 — surface as a RED-able observation
        obs = {"observed": False, "nonce": None,
               "reason": f"witness raised: {type(e).__name__}: {e}"}
    print("WITNESS_RESULT " + json.dumps(obs))
    return 0 if obs.get("observed") else 1


if __name__ == "__main__":
    sys.exit(main())
