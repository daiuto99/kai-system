#!/usr/bin/env python3
"""COMMS Phase 1 enforcement — the notify() gateway is the single Telegram chokepoint.

Fails loud (exit 1) if any module other than the gateway issues a raw Telegram
sendMessage. This is the CI/deploy check named in docs/COMMS_DELIVERY_ARCHITECTURE
_2026-07.md §5 ("no module outside the gateway may call a raw Telegram send").

Scope (P1): the outbound *send* — `api.telegram.org/.../sendMessage`. editMessageText
and answerCallbackQuery (UI plumbing on an already-sent card, not a new voice) are
out of P1's "send" scope and owned by Phase 2's tap round-trip.

Runtime note: kai-scheduler's invariant engine cannot read the source tree (it mounts
only .git/config, KAI-882 L18), so enforcement is a repo/deploy check, not a container
invariant. Run it in deploy verification and (optionally) CI. Exit 0 = chokepoint intact.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Modules allowed to contain the literal sendMessage URL: the gateway (the one
# real transport) and this checker itself (it names the pattern it hunts for).
GATEWAY = "shared/notify_gateway.py"
SELF = "scripts/check_notify_chokepoint.py"

# Explicitly allowlisted raw sends, each with a rationale. No silent exemptions.
ALLOWLIST = {
    # Shadow-only daily brief on the hermes-skills path — a SEPARATE deploy surface
    # that does not mount /shared, so it cannot import the gateway. Currently
    # shadow-only (not sending). Phase 3 converts it to a dashboard pointer.
    "hermes-skills/daily_brief/scripts/build_brief.py": "hermes path; shadow-only; no /shared mount (Phase 3)",
}

# Match a raw Telegram send: the sendMessage endpoint on api.telegram.org.
PATTERN = re.compile(r"api\.telegram\.org/bot[^\"'\s]*?/sendMessage|/sendMessage")
TELEGRAM_HINT = re.compile(r"api\.telegram\.org")


def main() -> int:
    violations: list[str] = []
    for path in ROOT.rglob("*.py"):
        rel = path.relative_to(ROOT).as_posix()
        if rel in (GATEWAY, SELF):
            continue
        if "/node_modules/" in rel or "/.git/" in rel or "/_archived/" in rel:
            continue
        # Test files reference the URL in redaction/assertion strings but never send
        # (transport is stubbed/mocked) — they are not a chokepoint concern.
        if "/tests/" in rel or path.name.startswith("test_") or path.name.endswith("_test.py"):
            continue
        try:
            text = path.read_text()
        except Exception:
            continue
        # A raw send == a sendMessage call that also references the telegram host,
        # OR the fully-qualified endpoint. This avoids flagging the gateway wrappers'
        # unrelated identifiers while catching real raw posts.
        for i, line in enumerate(text.splitlines(), 1):
            if "sendMessage" in line and (TELEGRAM_HINT.search(line) or "sendMessage\"" in line or "sendMessage'" in line):
                if rel in ALLOWLIST:
                    print(f"  ALLOWLISTED  {rel}:{i}  — {ALLOWLIST[rel]}")
                    break
                violations.append(f"{rel}:{i}: {line.strip()[:100]}")

    if violations:
        print("\n❌ CHOKEPOINT VIOLATION — raw Telegram sendMessage outside the gateway:")
        for v in violations:
            print(f"   {v}")
        print(f"\nRoute these through shared/notify_gateway.py "
              f"(send_telegram / send_message / notify).")
        return 1

    print("✅ notify() chokepoint intact — no raw Telegram sendMessage outside the gateway.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
