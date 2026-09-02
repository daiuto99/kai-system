"""Buzz emergency recovery — restart the Buzz app-tier containers to bring the
primary back online.

Pure logic with an INJECTED docker client so it unit-tests without a live
daemon. This is the on-demand twin of the autonomous services-custodian sweep
(which restarts a down, non-looping container every */15 min); this path is what
a Telegram `/recover` fires when Leo is away from the keyboard.

Scope is fixed to the Buzz APP tier — never the data tier (postgres/redis/minio,
where a blind restart is riskier and rarely the fix) and never an arbitrary
container: an unknown name is dropped, not restarted. Honest by construction: a
per-service docker failure is reported as an error on THAT service, never a faked
ok, and never aborts recovery of the others.
"""
from __future__ import annotations

# The user-facing Buzz stack: the app itself, the advisor-backend shim (whose
# orphaning once caused an 11-day DM outage — KAI-1108), and the message relay.
BUZZ_RECOVERY_SERVICES = ("kai-buzz", "kai-buzz-shim", "buzz-relay")


def recover_buzz(client, targets=None, only_if_down: bool = False) -> list[dict]:
    """Restart the requested Buzz app-tier containers via `client` (a docker
    SDK client). Returns one result dict per service: service, before, after,
    action. `targets` is filtered to BUZZ_RECOVERY_SERVICES (unknown names are
    dropped). With only_if_down=True a running container is left alone."""
    names = [t for t in (targets or BUZZ_RECOVERY_SERVICES) if t in BUZZ_RECOVERY_SERVICES]
    actions: list[dict] = []
    for name in names:
        rec = {"service": name, "before": None, "after": None, "action": None}
        try:
            c = client.containers.get(name)
            before = getattr(c, "status", None) or "unknown"
            rec["before"] = before
            if only_if_down and before == "running":
                rec["action"] = "skipped (already running)"
                rec["after"] = before
            else:
                c.restart()
                try:
                    c.reload()  # refresh .status after the restart
                except Exception:
                    pass
                rec["after"] = getattr(c, "status", None) or "restarted"
                rec["action"] = "restarted"
        except Exception as exc:  # NotFound or docker error — honest per-service failure
            rec["before"] = rec["before"] or "not_found"
            rec["action"] = f"error: {type(exc).__name__}"
        actions.append(rec)
    return actions
