"""KAI-owned self-hosted WordPress security scan (KAI-1068).

A read-only, scheduled sweep of the Cloudways WP fleet that replaces reliance on
Cloudways' paid/noisy malware alerts. Runs entirely on KAI infrastructure, reuses
the orchestrator's existing Cloudways SSH substrate, and is silent on a clean
fleet (JARVIS silent-notify): a clean scan produces no alert and files no bug.

Per app, over one SSH round-trip (``transports.cloudways_ssh_scan``), it collects
and diffs against a stored per-site baseline:

  * file integrity   — ``wp core verify-checksums`` (the benign Cloudways
                       ``wp-salt.php should not exist`` warning is allowlisted).
  * siteurl / home   — must match the seeded baseline (hijack detector).
  * admin accounts   — a login not present at seed time is a rogue-admin finding.
  * user count       — drift vs. baseline.
  * autoloaded opts  — any option carrying ``<script`` / ``base64_decode`` /
                       ``eval(`` / ``gzinflate`` is an injection.
  * script/iframe in published posts — IDs not in the seeded allowlist.
  * comment volume   — comments are disabled fleet-wide (2026-08-12); non-zero
                       is itself an anomaly.
  * plugin/theme CVEs (WPScan free API) — SKIPPED until a token is provisioned
                       at ``secrets/wpscan_api_token.txt`` (logged, not a finding).

Findings file a deduped Plane ``[BUG]`` (urgent for compromise indicators, high
otherwise); the scheduler owns the operator alert. First scan of a site SEEDS the
baseline and reports no findings for it. Fail-closed: a site that cannot be
scanned is reported as an anomaly, never silently skipped.
"""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from models import CapabilityResult
from transports import cloudways_ssh_scan
from . import capability

_log = logging.getLogger(__name__)

_SITES_JSON = Path("/vault/00_System/wordpress_sites.json")
_BASELINE_PATH = Path("/vault/00_System/wp_security_baseline.json")
_WPSCAN_TOKEN_PATHS = (
    Path("/run/secrets/wpscan_api_token"),
    Path("/home/leo/kai-system/secrets/wpscan_api_token.txt"),
)

# Checksum output lines that are benign on every Cloudways app.
_BENIGN_CHECKSUM = (
    "Success: WordPress installation verifies against checksums.",
    "Warning: File should not exist: wp-salt.php",
)

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_sites() -> dict:
    """Every WP entry in the map. A site missing cloudways_sys_user is NOT filtered
    out — it flows through and fails closed as an 'unscannable' finding rather than
    disappearing silently."""
    raw = json.loads(_SITES_JSON.read_text())
    sites = raw.get("sites", raw)
    return {k: v for k, v in sites.items()
            if isinstance(v, dict) and (v.get("cloudways_sys_user") or v.get("url"))}


def _load_baseline() -> dict:
    empty = {"version": 1, "sites": {}, "open_findings": {}}
    try:
        return json.loads(_BASELINE_PATH.read_text())
    except FileNotFoundError:
        return empty
    except (OSError, ValueError) as e:
        # A CORRUPT baseline is loud: re-seeding from empty would rebaseline the
        # fleet and could mask a live compromise. Absolute checks still fire on the
        # re-seed, but an operator must know the drift history was lost.
        _log.error("wp_security: baseline unreadable (%s) — re-seeding from empty; "
                   "drift history lost, investigate", e)
        return empty


def _save_baseline(baseline: dict) -> None:
    baseline["updated_at"] = _now()
    tmp = _BASELINE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(baseline, indent=2, sort_keys=True))
    tmp.replace(_BASELINE_PATH)


def _wpscan_token() -> str | None:
    for p in _WPSCAN_TOKEN_PATHS:
        try:
            tok = p.read_text().strip()
            if tok:
                return tok
        except OSError:
            continue
    return None


def _parse_sections(raw: str) -> dict[str, str]:
    """Split transport stdout on ``===NAME===`` markers into {name: body}."""
    sections: dict[str, str] = {}
    current = None
    buf: list[str] = []
    for line in raw.splitlines():
        if line.startswith("===") and line.endswith("===") and len(line) > 6:
            if current is not None:
                sections[current] = "\n".join(buf).strip()
            current = line.strip("=")
            buf = []
        elif current is not None:
            buf.append(line)
    if current is not None:
        sections[current] = "\n".join(buf).strip()
    return sections


def _nonempty_lines(text: str) -> list[str]:
    return [ln.strip() for ln in text.splitlines() if ln.strip()]


def _scan_incomplete(sections: dict) -> str | None:
    """Return a reason string if a load-bearing section is missing or errored.

    Guards against an SSH round-trip that reaches ``===DONE===`` but where an
    individual wp command failed (e.g. printed ``Error:``). Such a scan must be
    reported unscannable — never used to seed a bogus baseline or to read as
    'clean' by omission (fail-closed)."""
    for name in ("SITEURL", "HOME"):
        val = sections.get(name, "").strip()
        if not val or val.startswith("Error"):
            return f"{name} unreadable: {val[:80]!r}"
    checksums = sections.get("CHECKSUMS", "").strip()
    if not checksums:
        return "CHECKSUMS section empty (verify-checksums did not run)"
    # An `Error:` line means verify-checksums itself failed to run (unscannable),
    # distinct from a `Warning: ...doesn't verify...` line which IS a real modified
    # core finding evaluated downstream.
    if any(ln.startswith("Error") for ln in _nonempty_lines(checksums)):
        return f"verify-checksums errored: {checksums[:80]!r}"
    # USERCOUNT/COMMENTS are load-bearing counters: an error string here would
    # silently defeat drift/comment detection, so treat a non-numeric value as an
    # unscannable site rather than seeding/comparing garbage.
    for name in ("USERCOUNT", "COMMENTS"):
        val = sections.get(name, "").strip()
        if not val.isdigit():
            return f"{name} not numeric: {val[:80]!r}"
    # ADMINS drives rogue-admin detection; an errored enumeration must not seed.
    if any(ln.startswith(("Error", "Warning")) for ln in _nonempty_lines(sections.get("ADMINS", ""))):
        return "ADMINS enumeration errored"
    return None


def _seed(site_key: str, sections: dict) -> dict:
    """Record the clean-state fingerprint for a site's first scan."""
    admins = [a for a in _nonempty_lines(sections.get("ADMINS", ""))]
    return {
        "seeded_at": _now(),
        "siteurl": sections.get("SITEURL", "").strip(),
        "home": sections.get("HOME", "").strip(),
        "admins": sorted(set(admins)),
        "user_count": sections.get("USERCOUNT", "").strip(),
        "known_script_post_ids": sorted(set(_nonempty_lines(sections.get("SCRIPTPOSTS", "")))),
    }


def _evaluate(site_key: str, base: dict | None, sections: dict) -> list[dict]:
    """Return findings for a site. ABSOLUTE compromise indicators (injection,
    comments, modified core) are evaluated on EVERY scan — including the first
    (``base is None``) — so a site that is already compromised when first seen is
    still flagged rather than baselined as normal. RELATIVE checks (hijack, rogue
    admin, drift) only run once a baseline exists."""
    findings: list[dict] = []

    def add(ftype, severity, detail):
        findings.append({"site": site_key, "type": ftype, "severity": severity, "detail": detail})

    # ── Relative checks — require a baseline to diff against ──
    if base is not None:
        siteurl = sections.get("SITEURL", "").strip()
        home = sections.get("HOME", "").strip()
        if siteurl and base.get("siteurl") and siteurl != base["siteurl"]:
            add("siteurl_hijack", "critical", f"siteurl {base['siteurl']!r} -> {siteurl!r}")
        if home and base.get("home") and home != base["home"]:
            add("home_hijack", "critical", f"home {base['home']!r} -> {home!r}")

        admins_now = set(_nonempty_lines(sections.get("ADMINS", "")))
        rogue = sorted(admins_now - set(base.get("admins", [])))
        if rogue:
            add("rogue_admin", "critical", f"unrecognised admin account(s): {', '.join(rogue)}")

        uc_now = sections.get("USERCOUNT", "").strip()
        if uc_now and base.get("user_count") and uc_now != base["user_count"]:
            add("user_count_drift", "high", f"user count {base['user_count']} -> {uc_now}")

    # ── Absolute checks — run on every scan, seed included ──
    autoload = _nonempty_lines(sections.get("AUTOLOAD", ""))
    if autoload:
        add("autoload_injection", "critical",
            f"suspicious autoloaded option(s): {', '.join(autoload[:8])}")

    if base is not None:
        posts_now = set(_nonempty_lines(sections.get("SCRIPTPOSTS", "")))
        new_posts = sorted(posts_now - set(base.get("known_script_post_ids", [])))
        if new_posts:
            add("script_in_posts", "high",
                f"published post(s) with <script>/<iframe> not in allowlist: IDs {', '.join(new_posts)}")

    comments = sections.get("COMMENTS", "").strip()
    if comments.isdigit() and int(comments) > 0:
        add("comments_present", "high",
            f"{comments} comment(s) present — comments are disabled fleet-wide")

    checksum_lines = [ln for ln in _nonempty_lines(sections.get("CHECKSUMS", ""))
                      if ln not in _BENIGN_CHECKSUM]
    if checksum_lines:
        add("core_modified", "high",
            "core checksum anomalies: " + " | ".join(checksum_lines[:6]))

    return findings


@capability("wordpress.security_scan")
def security_scan(**_) -> CapabilityResult:
    """Scan the whole Cloudways WP fleet read-only; seed/diff a baseline; file
    deduped Plane bugs for new findings; return a per-site summary."""
    try:
        sites = _load_sites()
    except Exception as e:
        return CapabilityResult(ok=False, status="failed_final",
                                error={"message": f"cannot load site map: {e}"})

    baseline = _load_baseline()
    base_sites = baseline.setdefault("sites", {})
    open_findings = baseline.setdefault("open_findings", {})

    all_findings: list[dict] = []
    seeded: list[str] = []
    errors: list[dict] = []
    current_keys: set[str] = set()

    for site_key, rec in sorted(sites.items()):
        sysuser = rec.get("cloudways_sys_user", "")
        resp = cloudways_ssh_scan.scan(sysuser)
        if not resp.ok:
            f = {"site": site_key, "type": "unscannable", "severity": "high",
                 "detail": f"scan failed (fail-closed): {resp.error}"}
            errors.append(f)
            all_findings.append(f)
            current_keys.add(f"{site_key}:unscannable")
            continue

        sections = _parse_sections((resp.data or {}).get("raw", ""))

        incomplete = _scan_incomplete(sections)
        if incomplete:
            f = {"site": site_key, "type": "unscannable", "severity": "high",
                 "detail": f"incomplete scan (fail-closed): {incomplete}"}
            errors.append(f)
            all_findings.append(f)
            current_keys.add(f"{site_key}:unscannable")
            continue

        if site_key not in base_sites:
            base_sites[site_key] = _seed(site_key, sections)
            seeded.append(site_key)
            # Seed still runs ABSOLUTE checks: a site already compromised on first
            # scan must not be baselined as clean.
            for f in _evaluate(site_key, None, sections):
                all_findings.append(f)
                current_keys.add(f"{f['site']}:{f['type']}")
            continue

        for f in _evaluate(site_key, base_sites[site_key], sections):
            all_findings.append(f)
            current_keys.add(f"{f['site']}:{f['type']}")

    # Dedup: a finding is `new` the first time it appears and is then recorded so
    # subsequent scans mark it not-new. The scheduler owns Plane filing + alerting
    # (this container has no Plane token) and acts on the `new` flag.
    new_count = 0
    for f in all_findings:
        key = f"{f['site']}:{f['type']}"
        current_keys.add(key)
        if key not in open_findings:
            open_findings[key] = {"first_seen": _now(), "detail": f["detail"]}
            f["new"] = True
            new_count += 1
        else:
            f["new"] = False

    # Resolve: clear findings that are no longer present.
    resolved = [k for k in list(open_findings) if k not in current_keys]
    for k in resolved:
        del open_findings[k]

    _save_baseline(baseline)

    cve = "skipped: no WPScan API token (add secrets/wpscan_api_token.txt)"
    if _wpscan_token():
        cve = "token present (CVE enumeration not yet implemented — v1)"

    return CapabilityResult(
        ok=True,
        status="succeeded",
        data={
            "scanned": len(sites),
            "seeded": seeded,
            "findings": all_findings,
            "new_findings": new_count,
            "resolved": resolved,
            "clean": len(all_findings) == 0,
            "errors": errors,
            "cve_check": cve,
        },
    )
