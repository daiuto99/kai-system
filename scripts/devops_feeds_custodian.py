#!/usr/bin/env python3
"""Data-feed custodian — KAI-1487 (Stage 1 "or KAI flags it").

Every feed Leo relies on for the morning brief — calendar, email, tasks, Oura — is
swept for stale / empty-broken / expired each run. A feed that is unreachable,
returns an error, declares a bad feed_status, or is empty when emptiness means
death is FLAGGED as a structural Finding (a triaged Plane item + dashboard) through
the devops_ownership spine — never a silent WARN. This is the measurable half of the
Stage 1 exit: "every feed current or KAI flags it, every morning."

The incident this closes (2026-09-12 → 09-21): /calendar/events and /gmail/messages
returned HTTP 200 with 0 items for DAYS behind an expired direct-Google token,
surfaced only as a soft green_baseline WARN nobody was paged on. The honest
feed_status field (KAI-1484) makes that state detectable; this custodian is what
turns detection into an owned, flagged Finding.

Host-side (like the runner). Read-only: it fetches feed endpoints and classifies —
it never mutates a feed. No auto-remediation: a dead read-feed (an expired OAuth
token) needs a human, so every finding is STRUCTURAL.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path

# devops_ownership (Finding/dispatch spine) lives in shared/ — make this module
# self-sufficient so assess() imports it whether run by the runner, a test, or standalone.
_SHARED = Path(__file__).resolve().parent.parent / "shared"
if _SHARED.is_dir() and str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))

WORKER_API = f"http://{os.environ.get('KAI_TAILNET_IP', '100.78.94.80')}:8001"
SECRETS = Path(os.environ.get("KAI_SECRETS_DIR", "/home/leo/kai-system/secrets"))

# feed_status values (KAI-1484 honest say-so) that mean the feed is NOT serving truth.
_BAD_FEED_STATUS = frozenset({"auth_failed", "not_configured", "stale", "expired"})


@dataclass(frozen=True)
class Feed:
    name: str            # "calendar" | "email" | "tasks" | "oura"
    path: str            # endpoint under WORKER_API
    content_key: str     # the key whose payload is the feed's data
    empty_ok: bool       # True: a legitimately empty feed is fine; False: empty == broken


# The feeds Leo relies on. empty_ok distinguishes a feed that is naturally empty
# sometimes (no events today, inbox-zero) from one that is ALWAYS populated when
# healthy (Oura always has today's readiness/sleep once synced).
FEEDS = (
    Feed("calendar", "/calendar/events?days=7", "events",    True),
    Feed("email",    "/gmail/messages",         "emails",    True),
    Feed("tasks",    "/tasks",                  "today",     True),
    Feed("oura",     "/oura/today",             "readiness", False),
)


def _worker_auth() -> str:
    try:
        return (SECRETS / "kai_worker_auth.txt").read_text().strip()
    except OSError:
        return ""


def fetch_feed(path: str, *, timeout: float = 15.0):
    """GET a feed endpoint; return the parsed dict, or None if unreachable/unparseable.
    Impure — monkeypatched in tests so the pure classifier runs in isolation."""
    auth = _worker_auth()
    headers = {}
    if auth:
        headers["Authorization"] = "Basic " + base64.b64encode(auth.encode()).decode()
    req = urllib.request.Request(f"{WORKER_API}{path}", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


# ── pure classification (unit-tested) ──────────────────────────────────────────

def feed_broken(resp, feed: "Feed") -> tuple[bool, str]:
    """Pure verdict: is this feed failing to serve truth? Returns (broken, reason).
    Broken when: unreachable / non-dict, an explicit error field, a bad feed_status,
    or the content key is missing / empty AND this feed is never legitimately empty."""
    if not isinstance(resp, dict):
        return True, "unreachable / no response"
    err = resp.get("error")
    if err:
        return True, f"error: {str(err)[:120]}"
    fs = resp.get("feed_status")
    if fs in _BAD_FEED_STATUS:
        return True, f"feed_status={fs}"
    content = resp.get(feed.content_key)
    if content is None:
        return True, f"missing '{feed.content_key}' in response"
    if not feed.empty_ok and not content:
        return True, f"empty '{feed.content_key}' (this feed is always populated when healthy)"
    return False, ""


def _severity(reason: str) -> str:
    """Unreachable / auth-dead / error => crit; a softer empty/stale case => warn."""
    hard = ("unreachable", "auth_failed", "not_configured", "expired", "error:")
    return "crit" if any(h in reason for h in hard) else "warn"


class DataFeedsCustodian:
    """Sweeps Leo-facing data feeds for freshness; flags any not serving truth."""
    domain = "feeds"

    def assess(self) -> list:
        from devops_ownership import Finding, STRUCTURAL
        findings = []
        for feed in FEEDS:
            broken, reason = feed_broken(fetch_feed(feed.path), feed)
            if broken:
                findings.append(Finding(
                    domain="feeds", check=feed.name, severity=_severity(reason),
                    diagnosis=f"{feed.name} feed not serving truth: {reason}",
                    disposition=STRUCTURAL,
                    proposed_action=(f"investigate the {feed.name} feed ({feed.path}) — it is "
                                     f"stale/empty/expired and Leo's morning brief depends on it"),
                    dedup_key=f"feed-broken-{feed.name}",
                    detail={"path": feed.path, "reason": reason}))
        return findings

    def remediate_safe(self, f) -> str:
        # No safe auto-remediation for a read feed (a dead OAuth token needs a human).
        # assess() only ever emits STRUCTURAL findings, so this is never dispatched.
        return "no auto-remediation for a read feed (structural only)"


if __name__ == "__main__":
    _found = DataFeedsCustodian().assess()
    for _f in _found:
        print(_f.severity, _f.check, "-", _f.diagnosis)
    print(f"{len(_found)} feed(s) flagged")
