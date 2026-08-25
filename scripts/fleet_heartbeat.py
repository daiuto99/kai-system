#!/usr/bin/env python3
"""KAI-1047 · Host fleet heartbeat — KAI knows its machines.

Runs on the worker HOST via cron (it holds the tailnet + ssh keys the
kai-scheduler container deliberately does not). Probes every enrolled node in
the fleet — reachability, last-boot, host-service presence — and writes a
single fleet-state file that the container watchdog and the green-baseline suite
read each cycle. The container-only watch becomes a SUBSET of this model.

Reuses the existing node inventory rather than a parallel host list:
  • security/kai_node_allowlist.json  — logical name -> stable Tailscale node ID
  • security/node_transport.json      — per-node {ssh_user, ssh_key, ...}
  • the kai-tailscale container        — per-node Online / IP / LastSeen (SSOT
                                         for machine-level reachability)

This is a READ-ONLY probe (like green_baseline): it never restarts, mutates, or
provisions anything. The value never touches a secret. Reachability is sourced
from Tailscale's own Online flag (machine up/down), and a node that is online on
the tailnet but not ssh-reachable is recorded as reachable-but-degraded — the
exact 'on but SSH-unreachable after reboot' gap that motivated this ticket.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALLOWLIST = ROOT / "security" / "kai_node_allowlist.json"
TRANSPORT = ROOT / "security" / "node_transport.json"
sys.path.insert(0, str(ROOT / "shared"))  # /shared convention (matches worker-api PYTHONPATH)
import findings  # Findings Contract — no host may be marked bad without a cause

# Vault is the shared surface: /home/leo/vault on the host == /vault in the
# kai-scheduler container. Write to whichever exists so the file authoring works
# from either runtime.
_VAULT_CANDIDATES = (Path("/home/leo/vault"), Path("/vault"))
STATE_FILENAME = "_fleet_state.json"

TAILSCALE_CONTAINER = "kai-tailscale"
HEARTBEAT_INTERVAL_SEC = 180
SCHEMA = "kai.fleet_state.v1"

# The fleet is EVERY enrolled node in the allowlist (derived at runtime, not a
# hardcoded list) — a newly enrolled node is monitored with no code change, and
# the written `expected_hosts` roster lets the readers RED on an incomplete
# fleet rather than silently accepting a truncated state.

SSH_OPTS = [
    "-o", "BatchMode=yes",
    "-o", "ConnectTimeout=6",
    "-o", "IdentitiesOnly=yes",
    "-o", "StrictHostKeyChecking=accept-new",
]

# One shell that reports boot epoch + a couple of host services on either a
# Linux (worker) or macOS (mini) node — no per-node OS config needed.
_REMOTE_PROBE = (
    'if [ -r /proc/stat ]; then '
    '  awk "/^btime/{print \\"boot_epoch=\\" \\$2}" /proc/stat; '
    '  command -v docker >/dev/null 2>&1 && echo docker=1 || echo docker=0; '
    # Deeper Linux-node health (KAI-1240): the reachable-but-degraded signals a
    # bare reachability probe misses. tailscaled DAEMON health (distinct from
    # Tailscale's Online flag — a crashed daemon can leave a stale Online), the
    # local Ollama :11434 endpoint (this node's inference runtime), and root-fs +
    # available-memory pressure. Numeric *_pct signals are parsed into `health`;
    # ollama/tailscaled are 0/1 services. Each guarded so a missing tool degrades
    # THAT signal, never the whole probe.
    '  (pgrep -x tailscaled >/dev/null 2>&1 && echo tailscaled=1 || echo tailscaled=0); '
    # Ollama :11434 — bind-agnostic port-listen check (the mini binds Ollama to its
    # TAILNET ip, not 127.0.0.1, so a localhost curl false-negatives; KAI-1240).
    # `ss` (iproute2, present on Ubuntu) shows the port listening on ANY interface.
    '  (command -v ss >/dev/null 2>&1 && ss -ltn 2>/dev/null | grep -q ":11434 " '
    '     && echo ollama=1 || echo ollama=0); '
    '  df -P / 2>/dev/null | awk "NR==2{gsub(\\"%\\",\\"\\",\\$5); print \\"disk_pct=\\" \\$5}"; '
    '  awk "/^MemTotal:/{t=\\$2} /^MemAvailable:/{a=\\$2} END{if(t>0) printf \\"mem_avail_pct=%d\\n\\", a*100/t}" /proc/meminfo 2>/dev/null; '
    'else '
    # Emit the RAW kern.boottime line and parse it in python (parse_remote_probe).
    # A remote greedy `sed 's/.*sec = ([0-9]+)/…'` matched `usec` and captured the
    # microseconds field instead of the epoch → last_boot read 1970 (KAI-1180).
    '  echo "boottime_raw=$(sysctl -n kern.boottime 2>/dev/null)"; '
    '  (colima status >/dev/null 2>&1 && echo colima=1 || echo colima=0); '
    '  (pgrep -x ollama >/dev/null 2>&1 && echo ollama=1 || echo ollama=0); '
    'fi'
)


# ── pure helpers (unit-tested; no IO) ─────────────────────────────────────────

def _iso(epoch: float | int | None) -> str | None:
    if not epoch:
        return None
    return datetime.fromtimestamp(int(epoch), tz=timezone.utc).isoformat().replace("+00:00", "Z")


def parse_remote_probe(text: str) -> dict:
    """Parse the k=v lines emitted by _REMOTE_PROBE into {boot_epoch, services, health}.

    services: strict 0/1 booleans (docker, colima, ollama, tailscaled).
    health:   numeric `*_pct` gauges (disk_pct, mem_avail_pct) — a separate map
              so a percentage can never be mistaken for a boolean service. A
              non-integer value for a *_pct key is dropped (untrusted), so a
              malformed gauge reads as ABSENT (which the degrade verdict treats
              as unknown → not silent-green), never as a bogus number.
    """
    boot_epoch = None
    services: dict[str, bool] = {}
    health: dict[str, int] = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip()
        if k == "boot_epoch":
            try:
                boot_epoch = int(v) if v else None
            except ValueError:
                boot_epoch = None
        elif k == "boottime_raw":
            # macOS `sysctl -n kern.boottime` → "{ sec = 1787275622, usec = 938717 } …".
            # `\bsec` refuses to match inside `usec`, so we always grab the real
            # epoch and never the microseconds field (KAI-1180). Linux never emits
            # this key, so it can't clobber a good /proc/stat boot_epoch.
            if boot_epoch is None:
                m = re.search(r"\bsec = (\d+)", v)
                boot_epoch = int(m.group(1)) if m else None
        elif k.endswith("_pct"):
            try:
                health[k] = int(v)
            except (ValueError, TypeError):
                pass  # untrusted gauge → absent → unknown (never a bogus number)
        elif v in ("0", "1"):
            services[k] = v == "1"
    return {"boot_epoch": boot_epoch, "services": services, "health": health}


def build_host_entry(name: str, node_id: str, ts_peer: dict | None,
                     probe: dict | None, now_epoch: int,
                     ssh_expected: bool) -> dict:
    """Assemble one host's fleet entry from its tailscale peer + ssh probe.

    ts_peer: {online, ips, last_seen, hostname} or None if the node is absent
             from `tailscale status` entirely (never-seen / not enrolled).
    probe:   parsed _REMOTE_PROBE output, or None if the ssh probe did not run
             or failed. ssh_ok is True only when a probe with a boot_epoch came
             back.
    ssh_expected: True when this node has transport wiring (ssh SHOULD work). An
             online node whose ssh SHOULD work but doesn't is the real 'on but
             ssh-unreachable' gap (the reader treats it as RED); a node without
             wiring (ssh intentionally off) is not.
    """
    ts_peer = ts_peer or {}
    online = bool(ts_peer.get("online"))
    boot_epoch = (probe or {}).get("boot_epoch")
    ssh_ok = bool(probe and boot_epoch)
    entry = {
        "node_id": node_id,
        "tailnet_online": online,
        # reachable = up on the tailnet OR ssh-answering. ssh_ok overrides a
        # flapped Tailscale Online flag so a napping-but-ssh-reachable node
        # (e.g. the mini) no longer false-pages as offline. [KAI-1176]
        "reachable": online or ssh_ok,
        "ssh_ok": ssh_ok,
        "ssh_expected": ssh_expected,
        "ips": ts_peer.get("ips") or [],
        "tailscale_last_seen": ts_peer.get("last_seen"),
        "boot_epoch": boot_epoch,
        "last_boot": _iso(boot_epoch),
        "services": (probe or {}).get("services") or {},
        "health": (probe or {}).get("health") or {},
        "last_probe": _iso(now_epoch),
    }
    if not (online or ssh_ok):
        entry["degraded"] = "offline (tailnet unreachable, ssh down)"
    elif online and not ssh_ok:
        entry["degraded"] = ("online but ssh-unreachable (boot/services blind)"
                             if ssh_expected else
                             "online, ssh intentionally off (no transport wiring)")
    return entry


def summarize(state: dict) -> str:
    """One-line human summary — 'are all my machines up?'."""
    hosts = state.get("hosts", {})
    up = [n for n, h in hosts.items() if h.get("reachable")]
    down = [n for n, h in hosts.items() if not h.get("reachable")]
    degraded = [n for n, h in hosts.items() if h.get("reachable") and not h.get("ssh_ok")]
    parts = [f"{len(up)}/{len(hosts)} reachable"]
    if down:
        parts.append("DOWN: " + ", ".join(sorted(down)))
    if degraded:
        parts.append("degraded: " + ", ".join(sorted(degraded)))
    return " · ".join(parts)


# ── IO (not unit-tested; exercised live on the worker) ────────────────────────

def _vault_dir() -> Path:
    for c in _VAULT_CANDIDATES:
        if c.is_dir():
            return c
    return _VAULT_CANDIDATES[0]


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


_TRANSPORT_REQUIRED_KEYS = ("ssh_user", "ssh_key", "remote_secrets_dir")


def _transport_valid(data: dict) -> bool:
    """A transport inventory is valid only if it is non-empty AND every real
    (non-underscore) entry is a well-formed record. A zeroed/truncated file that
    still parses as {} or drops the required keys is NOT 'loaded' — else a wired
    host silently becomes ssh-not-expected and reads GREEN while ssh-blind."""
    if not isinstance(data, dict):
        return False
    entries = {k: v for k, v in data.items() if not k.startswith("_")}
    if not entries:
        return False
    return all(isinstance(v, dict) and all(v.get(r) for r in _TRANSPORT_REQUIRED_KEYS)
               for v in entries.values())


def _load_json_checked(path: Path, validator=None) -> tuple[dict, bool]:
    """Load a REQUIRED inventory file; return (data, ok). ok=False on any read/parse
    failure OR failed validation, so the reader REDs on lost/corrupt config
    visibility rather than silently treating every host as ssh-not-expected."""
    try:
        data = json.loads(path.read_text())
    except Exception:
        return {}, False
    if not isinstance(data, dict):
        return {}, False
    if validator is not None and not validator(data):
        return data, False
    return data, True


def _tailscale_status() -> dict:
    """node_id -> {online, ips, last_seen, hostname} via the kai-tailscale container."""
    out = subprocess.run(
        ["docker", "exec", TAILSCALE_CONTAINER, "tailscale", "status", "--json"],
        text=True, capture_output=True, timeout=20, check=False,
    )
    if out.returncode:
        return {}
    data = json.loads(out.stdout)
    peers: dict[str, dict] = {}

    def _rec(node: dict) -> dict:
        return {
            # Strict: only a real JSON boolean True counts as online (a malformed
            # "false"/"0" string must never read reachable).
            "online": node.get("Online") is True,
            "ips": node.get("TailscaleIPs") or [],
            "last_seen": node.get("LastSeen"),
            "hostname": node.get("HostName"),
        }

    self_node = data.get("Self") or {}
    if self_node.get("ID"):
        # Report the REAL Self.Online — do not fake it. A tailnet-disconnected
        # worker must not read reachable (KAI-1046 class). If the tailscale
        # container is itself down, this whole call fails -> empty map -> the
        # node is absent -> the reader REDs on an incomplete roster.
        peers[self_node["ID"]] = _rec(self_node)
    for _, p in (data.get("Peer") or {}).items():
        if p.get("ID"):
            peers[p["ID"]] = _rec(p)
    return peers


def _first_ip(ips: list[str]) -> str | None:
    for ip in ips:
        if ":" not in ip:  # prefer IPv4
            return ip
    return ips[0] if ips else None


def _probe_host(name: str, node_id: str, self_id: str, ip: str | None,
                transport: dict) -> dict | None:
    """Return parsed probe for one host, or None if it could not be probed."""
    # Self (the worker we run on): read locally, no ssh.
    if node_id == self_id:
        try:
            stat = Path("/proc/stat").read_text()
            btime = next((l.split()[1] for l in stat.splitlines()
                          if l.startswith("btime")), None)
        except Exception:
            btime = None
        docker_ok = subprocess.run(["docker", "info"], capture_output=True,
                                   timeout=8, check=False).returncode == 0
        return {"boot_epoch": int(btime) if btime else None,
                "services": {"docker": docker_ok}}

    wiring = transport.get(name) or {}
    ssh_user, ssh_key = wiring.get("ssh_user"), wiring.get("ssh_key")
    if not (ssh_user and ssh_key and ip):
        return None  # no transport wiring / no IP — cannot ssh-probe
    cmd = ["ssh", *SSH_OPTS, "-i", ssh_key, f"{ssh_user}@{ip}", _REMOTE_PROBE]
    try:
        out = subprocess.run(cmd, text=True, capture_output=True, timeout=12, check=False)
    except Exception:
        return None
    if out.returncode:
        return None
    return parse_remote_probe(out.stdout)


def collect_fleet_state() -> dict:
    now = int(time.time())
    # Roster is EVERY enrolled node (data-driven). expected_hosts lets readers
    # RED on a truncated/incomplete fleet instead of accepting it.
    allowlist = _load_json(ALLOWLIST).get("nodes", {})
    transport, transport_ok = _load_json_checked(TRANSPORT, validator=_transport_valid)
    peers = _tailscale_status()
    self_id = next((nid for nid, rec in peers.items()
                    if rec.get("hostname") == "kai-worker"), None) or \
        allowlist.get("kai-worker")

    roster = sorted(allowlist.keys())
    self_host = next((n for n, nid in allowlist.items() if nid == self_id), None)
    hosts: dict[str, dict] = {}
    for name in roster:
        node_id = allowlist.get(name)
        if not node_id:
            continue
        ts_peer = peers.get(node_id)
        ip = _first_ip((ts_peer or {}).get("ips") or [])
        # ssh is 'expected' for a node that has transport wiring OR is self
        # (self boot is read locally). A wired node that is online yet ssh-dead
        # is the real gap; an unwired node (e.g. mac-mini, Remote-Login off) is
        # not treated as a fault.
        is_self = (node_id == self_id)
        ssh_expected = (name in transport) or is_self
        probe = None
        if is_self:
            # Self is the host executing this probe: read boot locally and treat the
            # machine as UP when that succeeds. Tailscale's Self.Online is unreliable
            # (often false / zero LastSeen for self), so it must NOT drive self
            # reachability. A genuine self tailnet-death makes `tailscale status`
            # fail -> self absent from peers -> caught as roster-incomplete RED.
            probe = _probe_host(name, node_id, self_id, ip, transport)
            ts_peer = dict(ts_peer or {})
            ts_peer["online"] = bool(probe and probe.get("boot_epoch"))
        elif ssh_expected and ip:
            # Probe a wired host even when Tailscale reports it offline: the
            # Online flag flaps for napping nodes (e.g. the mini) while ssh to
            # the tailnet IP still answers. ssh-success => reachable, so a
            # Tailscale flap no longer false-pages a host that is actually up;
            # a genuinely-off host ssh-fails and still reads offline. Mirrors
            # the self-host rule above. [KAI-1176]
            probe = _probe_host(name, node_id, self_id, ip, transport)
        elif ts_peer and ts_peer.get("online"):
            probe = _probe_host(name, node_id, self_id, ip, transport)
        hosts[name] = build_host_entry(name, node_id, ts_peer, probe, now, ssh_expected)

    return {
        "schema": SCHEMA,
        "updated": _iso(now),
        "updated_epoch": now,
        "heartbeat_interval_sec": HEARTBEAT_INTERVAL_SEC,
        # Affirmative config-visibility flag the readers require (New-A): a
        # failed transport read => False => readers RED (ssh-expected unknown).
        "transport_loaded": transport_ok,
        "expected_hosts": roster,
        "self_host": self_host,
        "hosts": hosts,
    }


def _apply_status(hosts: dict) -> dict:
    """Project each host into a Findings-Contract-shaped finding: a bad status
    (offline / degraded) MUST carry a cause. The existing `degraded` string IS
    the cause — this just names the status so the contract can enforce it, and a
    future bad host that ever lacks a reason gets stamped not-yet-diagnosed
    instead of shipping as a bare, causeless alarm."""
    for h in hosts.values():
        if not h.get("reachable"):
            h["status"] = "offline"
            if h.get("degraded") and not h.get("cause"):
                h["cause"] = h["degraded"]
        elif h.get("ssh_expected") and not h.get("ssh_ok"):
            h["status"] = "degraded"
            if h.get("degraded") and not h.get("cause"):
                h["cause"] = h["degraded"]
        else:
            h["status"] = "ok"
    return hosts


def main() -> int:
    vault = _vault_dir()
    state_path = vault / STATE_FILENAME
    state = collect_fleet_state()

    # Findings Contract: no host may be published offline/degraded without a
    # cause. enforce_causes stamps not-yet-diagnosed on any bad host missing
    # one; assert_contract fail-closes before writing a bare, causeless alarm.
    _apply_status(state["hosts"])
    state["undiagnosed_hosts"] = findings.enforce_causes(state["hosts"])
    findings.assert_contract(state["hosts"])

    # Reboot detection is the watchdog's job (it compares the always-present
    # boot_epoch to a persisted seen-map — durable across watchdog gaps). The
    # heartbeat is a pure writer.
    tmp = state_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(state_path)  # atomic

    print(f"fleet_heartbeat: {summarize(state)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
