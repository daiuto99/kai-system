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

# ── Policy table ──────────────────────────────────────────────────────────────
# Every capability must be explicitly listed. An absent entry is a denial, not
# an implicit permission. Caller identity is authenticated at the router.

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
    "wordpress.create_page": {"rule": "requires_approval", "reason": "Creates a WordPress page"},
    "wordpress.publish":     {"rule": "requires_approval", "reason": "Publishes public WordPress content"},
    "wordpress.purge_varnish": {"rule": "requires_approval", "reason": "Changes public cache state"},
    "wordpress.set_front_page": {"rule": "requires_approval", "reason": "Changes a public homepage"},
    "wordpress.set_option":  {"rule": "requires_approval", "reason": "Changes WordPress configuration"},

    # ── Self-modification — disabled unless a separate approval path is built ──
    "self_modify.apply":        {"rule": "never", "reason": "Self-modify is disabled"},
    "self_modify.commit":       {"rule": "never", "reason": "Self-modify is disabled"},
    "self_modify.propose":      {"rule": "never", "reason": "Self-modify is disabled"},
    "self_modify.update_plane": {"rule": "never", "reason": "Self-modify is disabled"},
    "self_modify.verify":       {"rule": "never", "reason": "Self-modify is disabled"},

    # ── Read-only ops — always allowed autonomously ───────────────────────────
    "vault.read":            {"rule": "allow"},
    "vault.list":            {"rule": "allow"},
    "workspace.read":        {"rule": "allow"},
    "workspace.list":        {"rule": "allow"},
    "session.close_status":  {"rule": "allow"},
    "calendar.get_events":   {"rule": "allow"},
    "registry.check":        {"rule": "allow"},
    "registry.get":          {"rule": "allow"},
    "wordpress.load_config": {"rule": "allow"},
    "wordpress.probe_credentials": {"rule": "allow"},
    "wordpress.verify_live": {"rule": "allow"},

    # ── Comms — allowed autonomous, subject to rate limit gate ───────────────
    "slack.post":            {"rule": "allow"},

    # ── Plane — autonomous updates allowed; creation allowed ──────────────────
    "plane.update_state":    {"rule": "allow"},
    "plane.create_issue":    {"rule": "allow"},
    "council.gate":          {"rule": "allow"},

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
      action — one of "allow", "block_never", "block_unlisted",
               "requires_approval"
      reason — human-readable explanation, or None when action is "allow"

    `caller` is retained for audit-call compatibility. It is an authenticated
    identity supplied by the router, never request-body input.
    """
    policy = AUTONOMY_POLICIES.get(capability_name)
    if policy is None:
        return "block_unlisted", f"'{capability_name}' has no explicit autonomy policy and is denied."
    rule   = policy.get("rule", "never")
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

    if rule == "requires_approval":
        return "requires_approval", (
            f"'{capability_name}' requires authenticated confirmation. "
            + (f"Reason: {note}. " if note else "")
            + "Resend with confirmed=true using an authenticated capability credential."
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
