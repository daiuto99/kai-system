"""
tailnet_guard — fail-closed target enforcement for the authorized provisioning path (KAI-984).

Security core: decides whether a provisioning target is a dedicated KAI node on the
Tailscale tailnet, and if so returns the tailnet IP to transport over. It moves NO
secret and performs NO network mutation — it only says allow/deny + which tailnet IP.

Hard rule (Leo, 2026-07-27, hardcoded): a secret may only be copied to dedicated KAI
hardware on the tailnet. Enforced here, fail-closed, ALL of:
  1. target is on the enrolled KAI-node allowlist (matched by STABLE Tailscale node ID,
     never by hostname — hostnames duplicate/spoof; see the multiple "localhost" peers);
  2. that exact node ID is present in the live tailnet (self or peer);
  3. the node is online (self is implicitly online);
  4. it has a tailnet address in 100.64.0.0/10 (Tailscale CGNAT range);
  5. any missing field / parse error / ambiguity => DENY (never raise past the boundary).

The allowlist itself is a lock-class asset (design R1): it must be tamper-protected and
node enrollment is a separate out-of-band Leo-approved ceremony. This module only ENFORCES
against whatever allowlist it is handed; protecting that file is a separate increment.
"""
from __future__ import annotations

import ipaddress
import json
from dataclasses import dataclass

# Tailscale CGNAT range — every tailnet IPv4 lives here.
CGNAT_V4 = ipaddress.ip_network("100.64.0.0/10")


@dataclass(frozen=True)
class GuardDecision:
    allowed: bool
    node: str                     # logical name requested
    node_id: str | None           # enrolled stable Tailscale node ID (if known)
    tailnet_ip: str | None        # verified 100.64/10 address to transport over (if allowed)
    reason: str                   # human-readable allow/deny reason (audit-safe, no secrets)


def _deny(node: str, reason: str, node_id: str | None = None) -> GuardDecision:
    return GuardDecision(allowed=False, node=node, node_id=node_id, tailnet_ip=None, reason=reason)


def _iter_entries(status: dict):
    """Yield the Self entry then each Peer entry from a `tailscale status --json` dict."""
    self_entry = status.get("Self")
    if isinstance(self_entry, dict):
        yield True, self_entry
    peers = status.get("Peer")
    if isinstance(peers, dict):
        for p in peers.values():
            if isinstance(p, dict):
                yield False, p


def _first_cgnat_ip(entry: dict) -> str | None:
    for ip in entry.get("TailscaleIPs") or []:
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            continue
        if addr.version == 4 and addr in CGNAT_V4:
            return str(addr)
    return None


def evaluate_target(name: str, allowlist: dict, status: dict) -> GuardDecision:
    """Pure fail-closed decision. `allowlist` maps logical name -> stable Tailscale node ID.

    Returns a GuardDecision; NEVER raises (any unexpected shape => DENY).
    """
    try:
        if not isinstance(name, str) or not name:
            return _deny(str(name), "invalid target name")
        if not isinstance(allowlist, dict) or not allowlist:
            return _deny(name, "empty or invalid allowlist")
        if not isinstance(status, dict):
            return _deny(name, "invalid tailscale status")

        # 1. target must be on the enrolled allowlist, resolved to a stable node ID.
        node_id = allowlist.get(name)
        if not node_id or not isinstance(node_id, str):
            return _deny(name, "target is not on the KAI-node allowlist")

        # 2. that exact node ID must be present in the live tailnet (match by ID, NOT hostname).
        match = None
        is_self = False
        for entry_is_self, entry in _iter_entries(status):
            if entry.get("ID") == node_id:
                match = entry
                is_self = entry_is_self
                break
        if match is None:
            return _deny(name, "enrolled node ID is not present in the current tailnet", node_id)

        # 3. online (Self is implicitly online; the local node is the caller).
        if not is_self and not bool(match.get("Online")):
            return _deny(name, "target node is offline in the tailnet", node_id)

        # 4. must carry a 100.64/10 tailnet address to transport over.
        ip = _first_cgnat_ip(match)
        if ip is None:
            return _deny(name, "target has no tailnet (100.64.0.0/10) address", node_id)

        return GuardDecision(
            allowed=True, node=name, node_id=node_id, tailnet_ip=ip,
            reason="target is an enrolled, online KAI tailnet node",
        )
    except Exception as exc:  # fail-closed: never let an unexpected shape become an allow
        return _deny(str(name), f"guard error (fail-closed): {type(exc).__name__}")


def load_allowlist(path) -> dict:
    """Load the enrolled allowlist JSON: {"nodes": {logical_name: node_id, ...}}.

    Returns the name->id map, or {} on any error (=> everything denies, fail-closed).
    """
    try:
        data = json.loads(open(path, encoding="utf-8").read())
        nodes = data.get("nodes", {})
        return {k: v for k, v in nodes.items() if isinstance(k, str) and isinstance(v, str)}
    except Exception:
        return {}
