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
import json
from pathlib import Path

from autonomy_decisions import classify

_SITES_JSON = Path("/vault/00_System/wordpress_sites.json")

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
    "wordpress.update_page": {"classification": "mutating", "rule": "requires_approval", "reason": "Edits an existing WordPress draft page (drafts-only; refuses non-draft)"},
    # Draft-only rule (Leo, 2026-07-26): KAI never publishes WordPress content — every
    # new page stays a draft; Leo publishes manually in WP until he lifts this. Hard-
    # disabled for ALL callers (not just requires_approval). Lift only on Leo's direction
    # by restoring rule="requires_approval".
    "wordpress.publish":     {"classification": "mutating", "rule": "never", "reason": "Draft-only rule (Leo 2026-07-26): KAI never publishes; Leo publishes manually until confidence"},
    "wordpress.purge_varnish": {"classification": "mutating", "rule": "requires_approval", "reason": "Changes public cache state"},
    "wordpress.set_front_page": {"classification": "mutating", "rule": "never", "reason": "Draft-only rule (Leo 2026-07-26): setting a live homepage is a publish action — disabled until Leo lifts it"},
    "wordpress.set_option":  {"classification": "mutating", "rule": "requires_approval", "reason": "Changes WordPress configuration"},
    "hostops.place_secret": {"classification": "mutating", "rule": "contextual", "reason": "Delegates to org-model autonomy"},
    "hostops.deploy_plugin": {"classification": "mutating", "rule": "contextual", "reason": "Delegates to org-model autonomy"},
    "hostops.provision":    {"classification": "mutating", "rule": "contextual", "reason": "Delegates to org-model autonomy"},

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

_VALID_RULES = {"allow", "requires_approval", "contextual", "never"}


def hostops_action(capability_name: str, inputs: dict | None = None) -> dict:
    """Build trusted hostops context; ownership never comes from request input."""
    inputs = inputs or {}
    site = str(inputs.get("site", ""))
    owner = "unknown"
    try:
        owner = str(json.loads(_SITES_JSON.read_text()).get("sites", {}).get(site, {}).get("owner", "unknown"))
    except (OSError, ValueError):
        pass
    return {"op": capability_name.removeprefix("hostops."),
            "target": inputs.get("secret_name") or inputs.get("plugin") or "",
            "site": site, "owner": owner, "risk": inputs.get("risk", ""),
            "external_party": owner.lower() != "leo"}


def check_policy(capability_name: str, caller: str, inputs: dict | None = None) -> tuple[str, str | None]:
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

    if rule == "contextual":
        decision = classify(hostops_action(capability_name, inputs))
        return ("allow", decision.reason) if decision.mode == "autonomous" else ("requires_approval", decision.reason)

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
