"""
KAI Autonomy Policy Registry — S4-5.

AUTONOMY_POLICIES maps capability names to rules consulted before every
autonomous execution. Rules:
  allow              — autonomous caller may proceed
  requires_approval  — autonomous caller is blocked; admin caller passes through
  never              — blocked for all callers regardless of confirmation

check_policy() is the single call site used by the capability router.
"""
from __future__ import annotations

# ── Callers ───────────────────────────────────────────────────────────────────
# Callers passed in the `caller` field of the capability request body.
# Unknown callers default to CALLER_ADMIN (backwards-compatible).
CALLER_AUTONOMOUS = "kai_autonomous"
CALLER_ADMIN      = "admin"

# ── Policy table ──────────────────────────────────────────────────────────────
# Default for any capability not listed: {"rule": "allow"}
# This is the conservative default — add explicit entries for sensitive caps.

AUTONOMY_POLICIES: dict[str, dict] = {
    # ── Write / mutate ops — autonomous requires human approval ──────────────
    "vault.write":           {"rule": "requires_approval",
                              "reason": "Writes to persistent vault storage"},
    "session.close":         {"rule": "requires_approval",
                              "reason": "Terminates the active session and posts close report"},
    "workspace.sync":        {"rule": "requires_approval",
                              "reason": "Triggers Syncthing rescan — can overwrite workspace state"},
    "calendar.create_event": {"rule": "requires_approval",
                              "reason": "Creates a real calendar event visible to Leo"},

    # ── Read-only ops — always allowed autonomously ───────────────────────────
    "vault.read":            {"rule": "allow"},
    "vault.list":            {"rule": "allow"},
    "workspace.read":        {"rule": "allow"},
    "workspace.list":        {"rule": "allow"},
    "session.close_status":  {"rule": "allow"},
    "calendar.get_events":   {"rule": "allow"},

    # ── Comms — allowed autonomous, subject to rate limit gate ───────────────
    "slack.post":            {"rule": "allow"},
    "telegram.send":         {"rule": "allow"},

    # ── Plane — autonomous updates allowed; creation allowed ──────────────────
    "plane.update_state":    {"rule": "allow"},
    "plane.create_issue":    {"rule": "allow"},

    # ── Model Peer — autonomous reviews always allowed ───────────────────────
    "model_peer.codex.review":   {"rule": "allow"},
    "model_peer.chatgpt.review": {"rule": "allow"},
    "model_peer.get_review":     {"rule": "allow"},

    # ── Never — permanently disabled for all callers (reserved) ──────────────
    # (none currently — table exists for future hard-blocks)
}


# ── Engine ────────────────────────────────────────────────────────────────────

_VALID_RULES = {"allow", "requires_approval", "never"}


def check_policy(capability_name: str, caller: str) -> tuple[str, str | None]:
    """Consult the autonomy policy for a given capability + caller.

    Returns (action, reason):
      action — one of "allow", "block_never", "block_autonomous"
      reason — human-readable explanation, or None when action is "allow"

    Callers:
      "kai_autonomous"  — KAI acting without a human in the loop
      "admin"           — human or trusted admin service (default / backwards-compat)
    """
    policy = AUTONOMY_POLICIES.get(capability_name, {"rule": "allow"})
    rule   = policy.get("rule", "allow")
    note   = policy.get("reason", "")

    if rule not in _VALID_RULES:
        # Unknown rule — fail safe: treat as requires_approval
        rule = "requires_approval"
        note = f"Unknown policy rule for '{capability_name}' — defaulting to requires_approval"

    if rule == "never":
        return "block_never", (
            f"'{capability_name}' is permanently disabled by autonomy policy. "
            + (f"Reason: {note}" if note else "")
        )

    if rule == "requires_approval" and caller == CALLER_AUTONOMOUS:
        return "block_autonomous", (
            f"'{capability_name}' requires human approval for autonomous execution. "
            + (f"Reason: {note}. " if note else "")
            + f"Resend with caller='{CALLER_ADMIN}' to explicitly approve."
        )

    return "allow", None


def list_policies() -> list[dict]:
    """Return the full policy table as a list — used by /capabilities endpoint."""
    return [
        {
            "capability": name,
            "rule":       p.get("rule", "allow"),
            "reason":     p.get("reason", ""),
        }
        for name, p in sorted(AUTONOMY_POLICIES.items())
    ]
