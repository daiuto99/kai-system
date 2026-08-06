"""KAI-1047 · Shared fleet evaluation — ONE source of truth for the fleet verdict.

Imported by all three fleet surfaces so they cannot drift:
  • scripts/fleet_heartbeat.py  (host writer)   — roster/ssh-expected helpers
  • kai-scheduler/watchdog.py   (container read) — check_fleet + reboot surfacing
  • scripts/green_baseline.py   (host gate)      — session-start fleet gate

Pure functions only — no IO, no heavy imports — so it loads in every runtime
(the scheduler container mounts this dir at /shared; the host processes add
kai-system/shared to sys.path).

PRIME DIRECTIVE (KAI-1046 class): a real machine outage OR lost monitoring
visibility must NEVER read ok/GREEN. Every non-healthy branch below returns
False. The only ok branch is: fresh heartbeat, full roster, every host
reachable, and every ssh-EXPECTED host actually ssh-reachable.
"""
from __future__ import annotations

FLEET_MAX_AGE_SEC = 600          # ~3 missed 180s heartbeats before 'stale' (visibility lost)
FUTURE_TOLERANCE_SEC = 120       # clock-skew allowance; beyond this a future stamp is corrupt

# Every host entry MUST carry these as real booleans. A partial/legacy/corrupt
# entry (missing a field, or a string like "false" that is truthy) is untrusted
# state and reads RED — never a silent GREEN.
_REQUIRED_HOST_BOOLS = ("reachable", "ssh_ok", "ssh_expected")
_KNOWN_SCHEMAS = ("kai.fleet_state.v1",)  # exact allowlist — a 'corrupt' suffix must NOT pass


def fleet_verdict(state: dict, now_epoch: int,
                  max_age_sec: int = FLEET_MAX_AGE_SEC) -> tuple[bool, str]:
    """Return (ok, detail). ok is True ONLY for a fully-healthy, fresh, well-formed fleet."""
    if not state:
        return False, "fleet-state missing — host heartbeat has never run (visibility lost)"

    schema = state.get("schema")
    if schema not in _KNOWN_SCHEMAS:
        return False, f"fleet-state schema {schema!r} not recognized — untrusted state"

    updated = state.get("updated_epoch")
    if not isinstance(updated, (int, float)) or isinstance(updated, bool):
        return False, "fleet-state malformed — missing/invalid updated_epoch"
    age = now_epoch - updated  # no truncation: a sub-second future stamp must still trip the bound
    if age > max_age_sec:
        return False, f"fleet heartbeat STALE ({age}s > {max_age_sec}s) — host cron dead? (visibility lost)"
    if age < -FUTURE_TOLERANCE_SEC:
        return False, f"fleet heartbeat timestamp in the FUTURE ({age}s) — clock skew/corrupt (untrusted)"

    # Config-visibility gate (New-A): the writer affirmatively confirms it read
    # the transport inventory. Absent/false => ssh-expected is unknown, so an
    # ssh-blind wired host could read healthy — refuse to trust the fleet.
    if state.get("transport_loaded") is not True:
        return False, "transport inventory unreadable/unconfirmed — ssh-expected unknown (config visibility lost)"

    hosts = state.get("hosts")
    if not isinstance(hosts, dict):
        return False, "fleet-state 'hosts' is not an object — malformed (visibility lost)"
    expected = state.get("expected_hosts")
    if not isinstance(expected, list) or not all(isinstance(x, str) for x in expected):
        return False, "fleet-state 'expected_hosts' is not a list of host names — malformed"
    if not expected:
        return False, "fleet-state has no expected_hosts roster — malformed (visibility lost)"
    missing = sorted(h for h in expected if h not in hosts)
    if missing:
        return False, "fleet roster INCOMPLETE — missing host(s): " + ", ".join(missing)

    # Strict schema gate (New-B): every rostered host must carry real booleans.
    # All health evaluation below iterates the ROSTER (not hosts.items()) so an
    # extra, non-rostered, unvalidated key can never influence the verdict.
    for name in expected:
        h = hosts.get(name)
        if not isinstance(h, dict):
            return False, f"malformed host entry for {name} (not an object) — untrusted state"
        for field in _REQUIRED_HOST_BOOLS:
            if not isinstance(h.get(field), bool):
                return False, f"malformed host entry for {name}: '{field}' is not a bool — untrusted state"

    down = sorted(n for n in expected if not hosts[n]["reachable"])
    if down:
        parts = [f"{n} (last seen {hosts[n].get('tailscale_last_seen') or '?'})" for n in down]
        return False, "host(s) OFFLINE: " + ", ".join(parts)

    # Online but ssh-EXPECTED and failing = boot/services blind on a node that
    # should answer — the exact 'on but SSH-unreachable after reboot' gap. RED.
    ssh_broken = sorted(n for n in expected if hosts[n]["ssh_expected"] and not hosts[n]["ssh_ok"])
    if ssh_broken:
        return False, ("host(s) ONLINE but SSH-unreachable (boot/services blind): "
                       + ", ".join(ssh_broken))

    # Online, ssh NOT expected (e.g. intentional Remote-Login-off) — note only.
    no_ssh = sorted(n for n in expected if not hosts[n]["ssh_expected"] and not hosts[n]["ssh_ok"])
    detail = f"{len(expected)} hosts reachable, heartbeat {age}s fresh"
    if no_ssh:
        detail += f"; ssh-off (expected/no-transport): {', '.join(no_ssh)}"
    return True, detail


def compute_reboots(hosts: dict, seen: dict) -> tuple[list, dict]:
    """Detect reboots by comparing each host's boot_epoch to a persisted seen-map.

    Durable by construction (fixes the ephemeral-reboot-event class): boot_epoch
    lives in every heartbeat file, and `seen` persists the last KNOWN boot per
    host — so a reboot is caught whenever the watchdog next runs, even across a
    watchdog outage, and a probe failure (boot_epoch=None) never erases the
    baseline. First observation seeds the baseline silently (not a reboot).

    Returns (fresh_events, updated_seen).
    """
    fresh, updated = [], dict(seen)
    for name, h in (hosts or {}).items():
        be = (h or {}).get("boot_epoch")
        if not be:
            continue  # unknown this cycle — do NOT touch the baseline
        prev = seen.get(name)
        if prev is None:
            updated[name] = be            # seed baseline, silent first observation
        elif be > prev:
            fresh.append({
                "host": name,
                "prev_boot_epoch": prev,
                "new_boot_epoch": be,
                "new_boot": (h or {}).get("last_boot"),
            })
            updated[name] = be
    return fresh, updated
