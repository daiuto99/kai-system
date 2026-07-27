"""
tailnet_guard — fail-closed target enforcement for the authorized provisioning path (KAI-984).

Security core: decides whether a provisioning target is a dedicated KAI node on the
Tailscale tailnet, and if so returns the single tailnet IP to transport over. It moves NO
secret and performs NO network mutation — it only says allow/deny + which tailnet IP.

Hard rule (Leo, 2026-07-27, hardcoded): a secret may only be copied to dedicated KAI
hardware on the tailnet. Enforced here, FAIL-CLOSED, ALL of:
  1. target is on the enrolled KAI-node allowlist (matched by STABLE Tailscale node ID,
     never by hostname — hostnames duplicate/spoof; see the multiple "localhost" peers),
     and the ID has a valid stable-ID shape;
  2. that exact node ID is present EXACTLY ONCE in the live tailnet (self or peer);
  3. the node is online (strict boolean True; self may omit Online but never be False);
  4. it has EXACTLY ONE tailnet address in 100.64.0.0/10 (Tailscale CGNAT), from a strict
     list[str] TailscaleIPs — a malformed IP element or ambiguity denies;
  5. any missing field / wrong type / parse error / ambiguity => DENY (never raise past the
     boundary, never default-allow).

Hardened 2026-07-27 after Codex round-1 verification (KAI-984 inc1). The allowlist itself is
a lock-class asset (design R1): it must be tamper-protected, its enrollment_status must be
"confirmed" (load refuses anything else), and node enrollment is a separate out-of-band
Leo-approved ceremony. This module only ENFORCES against a confirmed allowlist.
"""
from __future__ import annotations

import ipaddress
import json
import re
from dataclasses import dataclass

# Tailscale CGNAT range — every tailnet IPv4 lives here.
CGNAT_V4 = ipaddress.ip_network("100.64.0.0/10")

# Stable Tailscale node IDs look like "nzkpgsJk1M11CNTRL": start 'n', alphanumeric, non-trivial.
# Use fullmatch (NOT `$`, which permits a terminal newline: "nABCDEF\n" would slip through).
_NODE_ID_RE = re.compile(r"n[0-9A-Za-z]{6,}")

# An allowlist is only usable once Leo has confirmed enrollment (design R1).
_CONFIRMED_ENROLLMENT = "confirmed"


@dataclass(frozen=True)
class GuardDecision:
    allowed: bool
    node: str                     # logical name requested
    node_id: str | None           # enrolled stable Tailscale node ID (if known)
    tailnet_ip: str | None        # verified single 100.64/10 address to transport over (if allowed)
    reason: str                   # human-readable allow/deny reason (audit-safe, no secrets)


def _safe_str(x) -> str:
    try:
        return str(x)
    except Exception:
        try:
            return repr(type(x))
        except Exception:
            return "<unstringifiable>"


def _reject_dupes(pairs):
    """json object_pairs_hook: raise on ANY duplicate key (the allowlist is a trust root —
    a duplicate key could silently swap an enrolled node ID). See design R1."""
    seen = {}
    for k, v in pairs:
        if k in seen:
            raise ValueError(f"duplicate key: {k!r}")
        seen[k] = v
    return seen


def _deny(node, reason: str, node_id: str | None = None) -> GuardDecision:
    return GuardDecision(allowed=False, node=_safe_str(node), node_id=node_id,
                         tailnet_ip=None, reason=reason)


def _valid_node_id(node_id) -> bool:
    return isinstance(node_id, str) and bool(_NODE_ID_RE.fullmatch(node_id))


def _iter_entries(status: dict):
    """Yield (is_self, entry) for the Self entry then each Peer entry."""
    self_entry = status.get("Self")
    if isinstance(self_entry, dict):
        yield True, self_entry
    peers = status.get("Peer")
    if isinstance(peers, dict):
        for p in peers.values():
            if isinstance(p, dict):
                yield False, p


def _sole_cgnat_ip(entry: dict):
    """Return (ip, error). TailscaleIPs must be a list[str]; exactly one 100.64/10 addr.

    error is None on success; otherwise a deny-reason string. Any non-str element, unparsable
    element, zero CGNAT addresses, or MORE THAN ONE CGNAT address => error (fail-closed).
    """
    ips = entry.get("TailscaleIPs")
    if not isinstance(ips, list):
        return None, "target TailscaleIPs is not a list"
    found = []
    for ip in ips:
        if not isinstance(ip, str):
            return None, "target has a non-string address entry"
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return None, "target has an unparsable address entry"
        if addr.version == 4 and addr in CGNAT_V4:
            found.append(str(addr))
    if not found:
        return None, "target has no tailnet (100.64.0.0/10) address"
    if len(found) > 1:
        return None, "target has multiple tailnet addresses (ambiguous transport target)"
    return found[0], None


def _is_online(entry: dict, is_self: bool) -> bool:
    """Strict online check. If the Online key is PRESENT, it must be exactly True (an explicit
    False/null/1/"false" denies — including for Self). Only a genuinely ABSENT Online key gives
    Self (the local caller) the benefit of the doubt; an absent key never helps a peer."""
    if "Online" not in entry:
        return is_self
    return entry["Online"] is True


def evaluate_target(name, allowlist, status) -> GuardDecision:
    """Pure fail-closed decision. `allowlist` maps logical name -> stable Tailscale node ID.

    Returns a GuardDecision; NEVER raises (any unexpected shape => DENY).
    """
    try:
        if not isinstance(name, str) or not name:
            return _deny(name, "invalid target name")
        if not isinstance(allowlist, dict) or not allowlist:
            return _deny(name, "empty or invalid allowlist")
        if not isinstance(status, dict):
            return _deny(name, "invalid tailscale status")

        # 0. the tailnet backend must actually be up, and Self/Peer must be well-shaped.
        if status.get("BackendState") != "Running":
            return _deny(name, "tailscale backend is not Running")
        if "Self" in status and not isinstance(status["Self"], dict):
            return _deny(name, "malformed tailscale status (Self is not an object)")
        peers = status.get("Peer")
        if peers is not None:
            if not isinstance(peers, dict):
                return _deny(name, "malformed tailscale status (Peer is not an object)")
            if any(not isinstance(v, dict) for v in peers.values()):
                return _deny(name, "malformed tailscale status (a peer entry is not an object)")

        # 0b. defense-in-depth: never trust a structurally-dirty allowlist, even passed directly
        #     (in production load_allowlist already guarantees this; belt-and-suspenders).
        ids = list(allowlist.values())
        if any(not _valid_node_id(v) for v in ids) or len(set(ids)) != len(ids):
            return _deny(name, "untrusted allowlist (invalid or duplicate node ids)")

        # 1. target must be on the allowlist, resolved to a valid stable node ID.
        node_id = allowlist.get(name)
        if not _valid_node_id(node_id):
            return _deny(name, "target is not on the KAI-node allowlist (or invalid node id)")

        # 2. that exact node ID must appear EXACTLY ONCE in the live tailnet (match by ID).
        matches = [(is_self, e) for is_self, e in _iter_entries(status) if e.get("ID") == node_id]
        if not matches:
            return _deny(name, "enrolled node ID is not present in the current tailnet", node_id)
        if len(matches) > 1:
            return _deny(name, "enrolled node ID is ambiguous (multiple tailnet entries)", node_id)
        is_self, entry = matches[0]

        # 3. online (strict True; self may omit, never explicit-False).
        if not _is_online(entry, is_self):
            return _deny(name, "target node is not online in the tailnet", node_id)

        # 4. exactly one 100.64/10 tailnet address, from a strict list[str].
        ip, err = _sole_cgnat_ip(entry)
        if err is not None:
            return _deny(name, err, node_id)

        return GuardDecision(
            allowed=True, node=name, node_id=node_id, tailnet_ip=ip,
            reason="target is an enrolled, online KAI tailnet node",
        )
    except Exception as exc:  # fail-closed: never let an unexpected shape become an allow
        return _deny(name, f"guard error (fail-closed): {type(exc).__name__}")


def load_allowlist(path) -> dict:
    """Load the enrolled allowlist JSON and return name->id, or {} (=> deny-all) on ANY problem.

    Requires: {"enrollment_status": "confirmed", "nodes": {name: node_id, ...}} with valid,
    UNIQUE stable node IDs. An unconfirmed enrollment_status (e.g. seeded_pending_leo_confirmation)
    yields {} — nothing provisions until Leo confirms enrollment (design R1).
    """
    try:
        data = json.loads(open(path, encoding="utf-8").read(), object_pairs_hook=_reject_dupes)
        if not isinstance(data, dict):
            return {}
        if data.get("enrollment_status") != _CONFIRMED_ENROLLMENT:
            return {}
        nodes = data.get("nodes")
        if not isinstance(nodes, dict):
            return {}
        clean = {}
        seen_ids = set()
        for name, nid in nodes.items():
            if not isinstance(name, str) or not _valid_node_id(nid):
                return {}          # any bad entry poisons the whole list (fail-closed)
            if nid in seen_ids:
                return {}          # duplicate ID across aliases => ambiguous => deny-all
            seen_ids.add(nid)
            clean[name] = nid
        return clean
    except Exception:
        return {}
