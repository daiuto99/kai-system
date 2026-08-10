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


def _structural_check(state: dict, now_epoch: int, max_age_sec: int):
    """Shared VISIBILITY/INTEGRITY gate — the checks that mean 'monitoring is
    blind or the state is untrusted'. These are RED for BOTH consumers (the
    watchdog alarm and the green-baseline gate) because a blind monitor must
    never read healthy (KAI-1046 class). Returns (None, detail) on failure, or
    (expected, hosts) on success.
    """
    if not state:
        return None, "fleet-state missing — host heartbeat has never run (visibility lost)"
    schema = state.get("schema")
    if schema not in _KNOWN_SCHEMAS:
        return None, f"fleet-state schema {schema!r} not recognized — untrusted state"
    updated = state.get("updated_epoch")
    if not isinstance(updated, (int, float)) or isinstance(updated, bool):
        return None, "fleet-state malformed — missing/invalid updated_epoch"
    age = now_epoch - updated  # no truncation: a sub-second future stamp must still trip the bound
    if age > max_age_sec:
        return None, f"fleet heartbeat STALE ({age}s > {max_age_sec}s) — host cron dead? (visibility lost)"
    if age < -FUTURE_TOLERANCE_SEC:
        return None, f"fleet heartbeat timestamp in the FUTURE ({age}s) — clock skew/corrupt (untrusted)"
    if state.get("transport_loaded") is not True:
        return None, "transport inventory unreadable/unconfirmed — ssh-expected unknown (config visibility lost)"
    hosts = state.get("hosts")
    if not isinstance(hosts, dict):
        return None, "fleet-state 'hosts' is not an object — malformed (visibility lost)"
    expected = state.get("expected_hosts")
    if not isinstance(expected, list) or not all(isinstance(x, str) for x in expected):
        return None, "fleet-state 'expected_hosts' is not a list of host names — malformed"
    if not expected:
        return None, "fleet-state has no expected_hosts roster — malformed (visibility lost)"
    missing = sorted(h for h in expected if h not in hosts)
    if missing:
        return None, "fleet roster INCOMPLETE — missing host(s): " + ", ".join(missing)
    # Every rostered host must carry real booleans (iterate the ROSTER, so an
    # extra non-rostered unvalidated key can never influence the verdict).
    for name in expected:
        h = hosts.get(name)
        if not isinstance(h, dict):
            return None, f"malformed host entry for {name} (not an object) — untrusted state"
        for field in _REQUIRED_HOST_BOOLS:
            if not isinstance(h.get(field), bool):
                return None, f"malformed host entry for {name}: '{field}' is not a bool — untrusted state"
    return expected, hosts


def fleet_verdict(state: dict, now_epoch: int,
                  max_age_sec: int = FLEET_MAX_AGE_SEC) -> tuple[bool, str]:
    """STRICT verdict for the WATCHDOG alarm — ANY host offline or ssh-blind is
    RED (it must page, never silent). ok True only for a fully-healthy fleet."""
    expected, hosts = _structural_check(state, now_epoch, max_age_sec)
    if expected is None:
        return False, hosts  # hosts holds the failure detail

    down = sorted(n for n in expected if not hosts[n]["reachable"])
    if down:
        parts = [f"{n} (last seen {hosts[n].get('tailscale_last_seen') or '?'})" for n in down]
        return False, "host(s) OFFLINE: " + ", ".join(parts)
    ssh_broken = sorted(n for n in expected if hosts[n]["ssh_expected"] and not hosts[n]["ssh_ok"])
    if ssh_broken:
        return False, ("host(s) ONLINE but SSH-unreachable (boot/services blind): "
                       + ", ".join(ssh_broken))
    no_ssh = sorted(n for n in expected if not hosts[n]["ssh_expected"] and not hosts[n]["ssh_ok"])
    detail = f"{len(expected)} hosts reachable"
    if no_ssh:
        detail += f"; ssh-off (expected/no-transport): {', '.join(no_ssh)}"
    return True, detail


def fleet_gate_verdict(state: dict, now_epoch: int, self_host,
                       max_age_sec: int = FLEET_MAX_AGE_SEC,
                       muted=None) -> tuple[bool, str]:
    """LENIENT verdict for the green-baseline session/push GATE. Hard-fails on
    LOST VISIBILITY (same structural gate) or the SPINE (self_host) being down —
    but a NON-spine node offline/ssh-blind is a printed WARNING, not a gate
    failure. Rationale: a flapping aux inference node must not block pushing
    unrelated code for days; the WATCHDOG (fleet_verdict) still pages on it, so
    it is never silent. self_host is the spine node name (e.g. 'kai-worker')."""
    expected, hosts = _structural_check(state, now_epoch, max_age_sec)
    if expected is None:
        return False, hosts

    # The spine (self) must be up + not ssh-blind — that IS a gate failure.
    if self_host in expected:
        if not hosts[self_host]["reachable"]:
            return False, f"SPINE host {self_host} OFFLINE — visibility/spine down"
        if hosts[self_host]["ssh_expected"] and not hosts[self_host]["ssh_ok"]:
            return False, f"SPINE host {self_host} ssh-unreachable (boot/services blind)"

    peers_down = sorted(n for n in expected if n != self_host and not hosts[n]["reachable"])
    peers_blind = sorted(n for n in expected if n != self_host
                         and hosts[n]["ssh_expected"] and not hosts[n]["ssh_ok"])
    # A node inside an active maintenance window is NOT paging (the watchdog
    # suppresses its page); label it "muted" so this gate and the watchdog agree.
    muted_set = set(muted or ())
    detail = f"{len(expected)} hosts; spine {self_host} OK"
    if peers_down:
        paging = [n for n in peers_down if n not in muted_set]
        muted_down = [n for n in peers_down if n in muted_set]
        if paging:
            detail += f"; WARN offline (watchdog paging): {', '.join(paging)}"
        if muted_down:
            detail += f"; offline (muted: maintenance window): {', '.join(muted_down)}"
    if peers_blind:
        blind = [n for n in peers_blind if n not in muted_set]
        muted_blind = [n for n in peers_blind if n in muted_set]
        if blind:
            detail += f"; WARN ssh-blind: {', '.join(blind)}"
        if muted_blind:
            detail += f"; ssh-blind (muted: maintenance window): {', '.join(muted_blind)}"
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


# -- KAI cutover maintenance window (node-scoped page mute) --------------------
def maint_suppresses_page(state: dict, now_epoch: int, muted,
                          max_age_sec: int = FLEET_MAX_AGE_SEC) -> tuple[bool, list]:
    """Decide whether the STRICT fleet page may be muted by an operator
    maintenance window. Returns (suppress, problem_hosts).

    suppress is True ONLY when the fleet is RED purely because one or more
    *muted* hosts are offline/ssh-blind. It is False -- i.e. the page still
    fires -- for EVERY case that could hide a real loss of safety:
      * lost visibility / stale heartbeat / untrusted state (structural RED),
      * any non-muted host down or ssh-blind (especially the spine),
      * a healthy fleet (nothing to suppress).
    `muted` is the collection of host names under an active window. Pure
    function (mirrors fleet_verdict's own down/ssh-blind logic) so the watchdog
    and its tests share one definition. See watchdog._fleet_muted_now.
    """
    expected, hosts = _structural_check(state, now_epoch, max_age_sec)
    if expected is None:
        return False, []  # blind monitor MUST page -- never masked by a window
    problem = sorted(
        n for n in expected
        if (not hosts[n]["reachable"])
        or (hosts[n]["ssh_expected"] and not hosts[n]["ssh_ok"])
    )
    if not problem:
        return False, []  # healthy -- fleet_verdict is ok anyway, nothing to mute
    muted_set = set(muted or ())
    if all(n in muted_set for n in problem):
        return True, problem
    return False, problem
