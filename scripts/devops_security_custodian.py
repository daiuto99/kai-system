#!/usr/bin/env python3
"""Security custodian — KAI-47 Phase 2 (KAI-52).

DevOps owns the security surface. Modeled on the disk custodian, but the honest
reality of the security domain is that almost nothing is safe to auto-mutate — you
WATCH and ESCALATE, you don't auto-rotate/renew/redeploy security-critical state.
So this custodian makes every security check OWNED and CONTINUOUSLY watched (the
green baseline only runs at session-start; this runs on the 15-min cron), routing
each non-green verdict to the queue or the gate. The one genuinely-safe AUTO is
repairing a host secret file whose permissions drifted off owner-only.

Ground-truth reframes of the design's §4 (verified 2026-08-24, KAI-52):
  - public TLS certs are CLOUDFLARE-managed (cloudflare-tunnel; no certbot/acme/cron).
    KAI does NOT renew them. A near-expiry reading means Cloudflare's own renewal is
    failing → STRUCTURAL (investigate the tunnel/DNS), never a KAI cert-issue action.
  - token/key ROTATION is decision-class, and codex OAuth is DO-NOT-TOUCH (stays on
    the ChatGPT subscription until the system is stable — [[project_codex_chatgpt_subscription]]).
    So auth-expiry → STRUCTURAL (Leo-authorized rotation via the KAI-984 rail), not auto.
  - the threat monitor (KAI-985) is Leo-blocked on Ubiquiti local-admin access
    ([[project_ubiquiti_monitoring_gate]]) — cannot be autonomously deployed here.

Reuses the proven green_baseline security checks verbatim (no reinvented detection):
each check's own verdict string IS the diagnosis. L18: nothing here logs a secret.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

_KAI_ROOT = Path(os.environ.get("KAI_SYSTEM_ROOT", "/home/leo/kai-system"))
for _p in (str(_KAI_ROOT), str(_KAI_ROOT / "scripts"), str(_KAI_ROOT / "shared")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Host secret directories whose files must stay owner-only (leo-owned, 0600).
SECRET_DIRS = (_KAI_ROOT / "secrets", Path.home() / ".kai" / "secrets")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── the reused baseline checks + how DevOps owns each non-green verdict ──────────
# (check_attr, our check name, disposition, proposed_action)
def _security_check_specs():
    from devops_ownership import STRUCTURAL, DECISION
    return [
        ("check_public_tls", "cert_expiry", STRUCTURAL,
         "certs are Cloudflare-managed — a near-expiry reading means Cloudflare renewal is failing; investigate the tunnel/DNS (KAI does not issue these certs)"),
        ("check_tailscale_key_expiry", "tailscale_key", STRUCTURAL,
         "re-authenticate the tailnet node key (Leo/tailscale-admin action) before it expires"),
        ("check_codex_verifier_auth", "codex_auth", STRUCTURAL,
         "codex OAuth expiring — re-auth on the ChatGPT subscription (DO NOT flip to metered api-key; standing decision)"),
        ("check_cloudways_auth", "cloudways_auth", STRUCTURAL,
         "Cloudways API token failing — rotate via the KAI-984 authorized-execution rail (Leo-authorized)"),
        ("check_credential_registry", "credential_registry", STRUCTURAL,
         "credential-surface drift — register the unregistered secret, or restore a missing runtime-critical credential"),
        ("check_source_drift", "source_drift", STRUCTURAL,
         "source integrity / whitespace drift detected — investigate the working tree"),
        ("check_secret_permissions", "secret_permissions", STRUCTURAL,
         "a runtime (docker) secret is group/world-readable — fix at the secret definition/source (not host-chmod-fixable)"),
        ("check_jobs_secret_leak", "jobs_secret_leak", DECISION,
         "a live credential is being served in cleartext via /jobs — rotate the exposed credential (KAI-984 rail) and scrub the rows; genuine Leo decision"),
    ]


# ── pure verdict mapping (unit-tested) ─────────────────────────────────────────

def verdict_severity(returned: str | None, raised: bool) -> str | None:
    """Map a green_baseline check's outcome to a Finding severity.
    raised (RuntimeError) -> 'crit' (a RED check). A returned string containing
    'WARN' -> 'warn'. Anything else (a clean GREEN string) -> None (no Finding)."""
    if raised:
        return "crit"
    if returned and "WARN" in returned:
        return "warn"
    return None


def _run_check(fn):
    """Return (severity, detail). Never raises — a check that errors is itself a
    warn Finding (a security check that can't run is not silence)."""
    try:
        out = fn()
        return verdict_severity(out, False), (out or "").strip()
    except Exception as e:
        # A RED check raises RuntimeError by design; any other error still means the
        # check could not certify the surface — treat as crit, carry the reason.
        return "crit", f"{type(e).__name__}: {e}"


# ── the one safe AUTO: host secret file permission repair ──────────────────────

def bad_perm_secret_files() -> list[str]:
    """Host secret files that are group/world-readable (drifted off owner-only).
    Follows symlinks (the .kai/*.txt are symlinks into kai-system/secrets)."""
    bad = []
    for d in SECRET_DIRS:
        if not d.exists():
            continue
        for f in sorted(d.glob("*.txt")):
            try:
                if f.is_file() and (f.stat().st_mode & 0o077):
                    bad.append(str(f.resolve()))
            except Exception:
                continue
    return sorted(set(bad))


class SecurityCustodian:
    domain = "security"

    def assess(self) -> list:
        from devops_ownership import Finding, AUTO
        import green_baseline as gb
        findings = []

        # 1) Own every non-green security baseline verdict.
        for attr, name, disp, action in _security_check_specs():
            fn = getattr(gb, attr, None)
            if fn is None:
                continue
            sev, detail = _run_check(fn)
            if sev is None:
                continue  # green — healthy
            findings.append(Finding(
                domain="security", check=name, severity=sev,
                diagnosis=detail or f"{name} not-green",
                disposition=disp, proposed_action=action,
                dedup_key=f"security-{name}", detail={"verdict": detail[:400]}))

        # 2) The one safe AUTO: a host secret file that drifted off owner-only.
        bad = bad_perm_secret_files()
        if bad:
            findings.append(Finding(
                domain="security", check="host_secret_perms", severity="crit",
                diagnosis=f"{len(bad)} host secret file(s) are group/world-readable (must be owner-only 0600)",
                disposition=AUTO,
                proposed_action="chmod 0600 the drifted host secret file(s)",
                dedup_key="security-host-secret-perms",
                detail={"files": [os.path.basename(p) for p in bad]}))  # names only, never values
        return findings

    def remediate_safe(self, f) -> str:
        if f.check != "host_secret_perms":
            return f"no safe remediation for security/{f.check}"
        fixed, failed = [], []
        for f2 in bad_perm_secret_files():
            try:
                os.chmod(f2, 0o600)
                fixed.append(os.path.basename(f2))
            except Exception as e:
                failed.append(f"{os.path.basename(f2)}: {type(e).__name__}")
        remaining = len(bad_perm_secret_files())
        return f"chmod 0600 fixed {fixed}; failed {failed}; still-bad {remaining}"
