#!/usr/bin/env python3
"""
S3-3: Chat Regression Suite — 20 queries across 5 categories.
Runs against the live council-api at localhost:8002.
Stdlib only (no third-party deps).

Usage:
    python3 chat_regression.py
    python3 chat_regression.py --json           # write regression_results.json
    python3 chat_regression.py --category wordpress
"""
import argparse
import json
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime

COUNCIL_API = "http://localhost:8002/council/message"
TIMEOUT     = 90

# ── 20 queries × 5 categories ─────────────────────────────────────────────────
QUERIES = [
    # ── Category 1: identity ─────────────────────────────────────────────────
    {"category": "identity", "channel": "kai",
     "q": "Who are you and what is your role in the KAI system?"},
    {"category": "identity", "channel": "kai",
     "q": "What capabilities do you have as the Lead Systems Engineer?"},
    {"category": "identity", "channel": "kai",
     "q": "What is the JARVIS architecture and what problem does it solve?"},
    {"category": "identity", "channel": "kai",
     "q": "How do you handle a task that Leo hasn't explicitly approved?"},

    # ── Category 2: system_status ─────────────────────────────────────────────
    {"category": "system_status", "channel": "kai",
     "q": "What is ORCHESTRATOR_HANDLES_WP and what does it change?"},
    {"category": "system_status", "channel": "kai",
     "q": "What WP sites are configured in the system?"},
    {"category": "system_status", "channel": "kai",
     "q": "What services make up the KAI stack? Name them."},
    {"category": "system_status", "channel": "kai",
     "q": "What is the council gate mechanism and when does it fire?"},

    # ── Category 3: wordpress ─────────────────────────────────────────────────
    {"category": "wordpress", "channel": "kai",
     "q": "Walk me through the 13-step homepage publish workflow."},
    {"category": "wordpress", "channel": "kai",
     "q": "What happened with the sette-uno.com E2E run in Sprint 2?"},
    {"category": "wordpress", "channel": "kai",
     "q": "What is the override endpoint and when should it be used?"},
    {"category": "wordpress", "channel": "kai",
     "q": "Why does verify_live fall back to the WP REST API?"},

    # ── Category 4: creative ──────────────────────────────────────────────────
    {"category": "creative", "channel": "creative",
     "q": "What is the SonicInk visual direction in three sentences?"},
    {"category": "creative", "channel": "creative",
     "q": "How would you describe Leo's design taste to a new designer?"},
    {"category": "creative", "channel": "creative",
     "q": "What makes a homepage layout feel right for a SonicInk brand?"},
    {"category": "creative", "channel": "creative",
     "q": "What should we avoid in typography for SonicInk sites?"},

    # ── Category 5: devops ────────────────────────────────────────────────────
    {"category": "devops", "channel": "devops",
     "q": "What would you check first if the Slack scheduler stopped posting?"},
    {"category": "devops", "channel": "devops",
     "q": "How is the orchestrator database backed up and where do backups live?"},
    {"category": "devops", "channel": "devops",
     "q": "What is the safest way to restart the council-api container?"},
    {"category": "devops", "channel": "devops",
     "q": "What does the gate-poller thread do and what is its fallback interval?"},
]


def _post(url: str, payload: dict, timeout: int) -> tuple[int, dict | None, str]:
    """Returns (status_code, json_data_or_None, error_str). Retries once on 5xx."""
    body = json.dumps(payload).encode()

    def _attempt() -> tuple[int, dict | None, str]:
        req = urllib.request.Request(url, data=body,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode())
                return resp.status, data, ""
        except urllib.error.HTTPError as e:
            return e.code, None, f"HTTP {e.code}"
        except TimeoutError:
            return 0, None, f"timeout after {timeout}s"
        except Exception as exc:
            return 0, None, str(exc)

    status, data, err = _attempt()
    if status >= 500:
        time.sleep(3)
        status, data, err = _attempt()
    return status, data, err


def run_query(q: dict, idx: int, total: int) -> dict:
    cat   = q["category"]
    ch    = q["channel"]
    msg   = q["q"]
    label = f"[{idx:02d}/{total}] [{cat:15s}]"
    print(f"{label} {msg[:65]}...")

    start = time.time()
    status, data, err = _post(
        COUNCIL_API,
        {"channel": ch, "message": msg, "user_id": "regression-suite", "history": []},
        TIMEOUT,
    )
    elapsed = round(time.time() - start, 2)

    if status != 200 or data is None:
        reason = err or f"HTTP {status}"
        print(f"{'':>20} FAIL — {reason}")
        return {"query": msg, "category": cat, "channel": ch,
                "passed": False, "reason": reason, "elapsed": elapsed}

    reply = data.get("reply", "")
    model = data.get("model", "?")

    if not reply or len(reply.strip()) < 20:
        reason = f"reply too short ({len(reply)} chars)"
        print(f"{'':>20} FAIL — {reason}")
        return {"query": msg, "category": cat, "channel": ch,
                "passed": False, "reason": reason,
                "reply_preview": reply[:80], "elapsed": elapsed}

    low = reply.lower().strip()
    if low.startswith(("error", "exception", "traceback")):
        reason = f"error-looking reply: {reply[:60]}"
        print(f"{'':>20} FAIL — {reason}")
        return {"query": msg, "category": cat, "channel": ch,
                "passed": False, "reason": reason, "elapsed": elapsed}

    print(f"{'':>20} PASS — {len(reply)} chars, {elapsed}s, model={model}")
    return {"query": msg, "category": cat, "channel": ch,
            "passed": True, "chars": len(reply), "elapsed": elapsed,
            "model": model, "reply_preview": reply[:120]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="Write regression_results.json")
    ap.add_argument("--category", help="Run only this category")
    args = ap.parse_args()

    queries = QUERIES
    if args.category:
        queries = [q for q in QUERIES if q["category"] == args.category]
        if not queries:
            print(f"Unknown category: {args.category}. "
                  f"Options: {sorted({q['category'] for q in QUERIES})}")
            sys.exit(1)

    print(f"\n{'='*70}")
    print(f"  KAI Chat Regression Suite — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"  {len(queries)} queries | {args.category or 'all 5 categories'}")
    print(f"{'='*70}\n")

    results = []
    for i, q in enumerate(queries, 1):
        result = run_query(q, i, len(queries))
        results.append(result)
        time.sleep(1.5)

    # ── Summary ───────────────────────────────────────────────────────────────
    passed = [r for r in results if r["passed"]]
    failed = [r for r in results if not r["passed"]]

    by_cat: dict[str, dict] = {}
    for r in results:
        c = r["category"]
        by_cat.setdefault(c, {"pass": 0, "fail": 0})
        by_cat[c]["pass" if r["passed"] else "fail"] += 1

    print(f"\n{'='*70}")
    print(f"  RESULTS: {len(passed)}/{len(queries)} passed")
    print(f"{'='*70}")
    for cat, counts in by_cat.items():
        status = "✓" if counts["fail"] == 0 else "✗"
        print(f"  {status} {cat:20s} {counts['pass']}/{counts['pass']+counts['fail']}")

    if failed:
        print(f"\n  FAILURES:")
        for r in failed:
            print(f"    [{r['category']}] {r['query'][:60]}")
            print(f"      reason: {r['reason']}")

    avg_elapsed = round(sum(r["elapsed"] for r in results) / len(results), 2) if results else 0
    print(f"\n  Avg response time: {avg_elapsed}s")
    print(f"{'='*70}\n")

    if args.json:
        out = {
            "run_at": datetime.now().isoformat(),
            "total": len(queries),
            "passed": len(passed),
            "failed": len(failed),
            "avg_elapsed": avg_elapsed,
            "results": results,
        }
        out_path = "/home/leo/kai-system/regression_results.json"
        with open(out_path, "w") as f:
            json.dump(out, f, indent=2)
        print(f"Results written to {out_path}")

    sys.exit(0 if not failed else 1)


if __name__ == "__main__":
    main()
