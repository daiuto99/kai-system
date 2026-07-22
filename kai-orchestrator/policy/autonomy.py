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
    "vault.write":           {"classification": "destructive", "rule": "requires_approval",
                              "reason": "Writes to persistent vault storage"},
    "session.close":         {"classification": "destructive", "rule": "requires_approval",
                              "reason": "Terminates the active session and posts close report"},
    "workspace.sync":        {"classification": "destructive", "rule": "requires_approval",
                              "reason": "Triggers Syncthing rescan — can overwrite workspace state"},
    "calendar.create_event": {"classification": "mutating", "rule": "requires_approval",
                              "reason": "Creates a real calendar event visible to Leo"},
    "wordpress.create_page": {"classification": "mutating", "rule": "requires_approval", "reason": "Creates a WordPress page"},
    "wordpress.publish":     {"classification": "mutating", "rule": "requires_approval", "reason": "Publishes public WordPress content"},
    "wordpress.purge_varnish": {"classification": "mutating", "rule": "requires_approval", "reason": "Changes public cache state"},
    "wordpress.set_front_page": {"classification": "mutating", "rule": "requires_approval", "reason": "Changes a public homepage"},
    "wordpress.set_option":  {"classification": "mutating", "rule": "requires_approval", "reason": "Changes WordPress configuration"},
    "hostops.place_secret": {"classification": "mutating", "rule": "requires_approval", "reason": "Places an app secret on a production host"},
    "hostops.deploy_plugin": {"classification": "mutating", "rule": "requires_approval", "reason": "Deploys an allowlisted plugin to a production host"},
    "hostops.provision":    {"classification": "mutating", "rule": "requires_approval", "reason": "Mints + installs a per-app deploy key and publish-gate secret (production-host credential)"},

    # ── Self-modification — disabled unless a separate approval path is built ──
    "self_modify.apply":        {"classification": "mutating", "rule": "never", "reason": "Self-modify is disabled"},
    "self_modify.commit":       {"classification": "mutating", "rule": "never", "reason": "Self-modify is disabled"},
    "self_modify.propose":      {"classification": "mutating", "rule": "never", "reason": "Self-modify is disabled"},
    "self_modify.update_plane": {"classification": "mutating", "rule": "never", "reason": "Self-modify is disabled"},
    "self_modify.verify":       {"classification": "mutating", "rule": "never", "reason": "Self-modify is disabled"},

    # ── Read-only ops — always allowed autonomously ───────────────────────────
    "vault.read":            {"classification": "read_only", "rule": "allow"},
    "vault.list":            {"classification": "read_only", "rule": "allow"},
    "workspace.read":        {"classification": "read_only", "rule": "allow"},
    "workspace.list":        {"classification": "read_only", "rule": "allow"},
    "session.close_status":  {"classification": "read_only", "rule": "allow"},
    "calendar.get_events":   {"classification": "read_only", "rule": "allow"},
    "registry.check":        {"classification": "read_only", "rule": "allow"},
    "registry.get":          {"classification": "read_only", "rule": "allow"},
    "wordpress.load_config": {"classification": "read_only", "rule": "allow"},
    "wordpress.probe_credentials": {"classification": "read_only", "rule": "allow"},
    "wordpress.get_front_page": {"classification": "read_only", "rule": "allow"},
    "wordpress.verify_live": {"classification": "read_only", "rule": "allow"},
    "hostops.status":       {"classification": "read_only", "rule": "allow"},
    "hostops.verify":       {"classification": "read_only", "rule": "allow"},

    # ── Comms — allowed autonomous, subject to rate limit gate ───────────────
    "slack.post":            {"classification": "mutating", "rule": "allow"},

    # ── Plane — autonomous updates allowed; creation allowed ──────────────────
    "plane.update_state":    {"classification": "mutating", "rule": "allow"},
    "plane.create_issue":    {"classification": "mutating", "rule": "allow"},
    "council.gate":          {"classification": "mutating", "rule": "allow"},

    # ── Model Peer — autonomous reviews always allowed ───────────────────────
    "model_peer.codex.review":   {"classification": "read_only", "rule": "allow"},
    "model_peer.chatgpt.review": {"classification": "read_only", "rule": "allow"},
    "model_peer.get_review":     {"classification": "read_only", "rule": "allow"},

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
