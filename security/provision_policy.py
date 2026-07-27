"""
provision_policy — pure authorization decision for the authorized provisioning path (KAI-984).

This is the second security layer (increment 2), composed ON TOP of the Codex-verified
`tailnet_guard`. It bounds BOTH dimensions of a provisioning request, fail-closed:
  - WHICH SECRET may move: only names on an explicit provisionable allowlist. Even a wrongly
    approved request cannot exfiltrate an arbitrary secret — only these named ones.
  - WHERE it may go: only a dedicated KAI tailnet node, per `tailnet_guard.evaluate_target`.

It is PURE: no secret values, no I/O, no gate, no transport. The stateful capability
(gate + server-side read + tailnet transport + audit) calls `authorize_provision` FIRST and
proceeds only on allowed=True. Keeping the decision pure makes it verifiable in isolation.
"""
from __future__ import annotations

from dataclasses import dataclass

import tailnet_guard

# The ONLY secrets the provisioning path may ever move. An approved request for anything
# else denies — this bounds the blast radius to exactly the brief's three dependencies.
# (Design R2: never widen this without a fresh review.)
PROVISIONABLE_SECRETS = frozenset({
    "todoist_api_key",
    "anthropic_api_key",
    "slack_bot_token",
})

# Defensive: a provisionable secret name is a bare identifier — no path parts, no separators.
_SAFE_NAME = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")


@dataclass(frozen=True)
class ProvisionDecision:
    allowed: bool
    node: str
    secret_name: str
    tailnet_ip: str | None      # verified transport target (if allowed)
    node_id: str | None
    reason: str


def _safe_name(x) -> str:
    try:
        return str(x)
    except Exception:
        return "<unstringifiable>"


def _valid_secret_name(name) -> bool:
    """A provisionable secret name must be EXACTLY a str (no subclasses — a hostile str subclass
    can spoof equality/iteration), a non-empty bare identifier, on the module allowlist.

    The allowlist is the MODULE constant PROVISIONABLE_SECRETS — it is never caller-supplied,
    so a caller cannot widen the policy (design R2)."""
    return (
        type(name) is str                       # exact type: reject str subclasses
        and 0 < len(name) <= 64
        and all(c in _SAFE_NAME for c in name)
        and name in PROVISIONABLE_SECRETS
    )


def authorize_provision(node, secret_name, node_allowlist, tailscale_status) -> ProvisionDecision:
    """Pure fail-closed authorization for provisioning `secret_name` to `node`.

    Returns a ProvisionDecision; NEVER raises (any unexpected shape => DENY). Handles NO secret
    value — only the NAME. allowed=True means: the name is on the MODULE provisionable allowlist
    AND the node passed the tailnet guard; tailnet_ip is the verified transport target. The
    allowlist is NOT a parameter — it cannot be widened by a caller.
    """
    try:
        node_s = _safe_name(node)
        name_s = _safe_name(secret_name)

        # 1. WHICH secret — must be an allowlisted, well-formed, exact-str name (module policy).
        if not _valid_secret_name(secret_name):
            return ProvisionDecision(False, node_s, name_s, None, None,
                                     "secret is not on the provisionable allowlist")

        # 2. WHERE — the node must pass the (Codex-verified) tailnet guard.
        gd = tailnet_guard.evaluate_target(node, node_allowlist, tailscale_status)
        if not gd.allowed:
            return ProvisionDecision(False, node_s, name_s, None, gd.node_id,
                                     f"target denied: {gd.reason}")

        return ProvisionDecision(
            allowed=True, node=gd.node, secret_name=secret_name,
            tailnet_ip=gd.tailnet_ip, node_id=gd.node_id,
            reason="provisionable secret to an enrolled KAI tailnet node",
        )
    except Exception as exc:  # fail-closed
        return ProvisionDecision(False, _safe_name(node), _safe_name(secret_name), None, None,
                                 f"policy error (fail-closed): {type(exc).__name__}")
