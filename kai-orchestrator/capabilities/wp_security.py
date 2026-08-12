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

import httpx

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
_WPSCAN_API = "https://wpscan.com/api/v3"
_WPSCAN_CACHE = Path("/vault/00_System/wpscan_cache.json")
_WPSCAN_TTL_H = 24
# Free tier is 25 lookups/day; a fleet-deduped run needs far fewer. Cap below 25
# with headroom, and cache for 24h so repeated daily runs cost ~0 fresh calls.
_WPSCAN_MAX_LOOKUPS = 20
# Statuses that are not third-party plugins we can look up (mu-plugins, drop-ins).
_SKIP_PLUGIN_STATUSES = {"must-use", "dropin"}

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


# ── WPScan CVE enumeration (KAI-1072) ────────────────────────────────────────

def _ver_tuple(v: str) -> tuple[int, ...]:
    parts = []
    for p in str(v).split("."):
        # LEADING digits only — stop at the first non-digit so a pre-release
        # suffix (e.g. "13-beta1") parses as 13, not 131.
        lead = ""
        for ch in p:
            if not ch.isdigit():
                break
            lead += ch
        parts.append(int(lead) if lead else 0)
    return tuple(parts)


def _ver_lt(a: str, b: str) -> bool:
    """True if version a < version b (dotted-numeric compare, zero-padded)."""
    if not a or not b:
        return False
    ta, tb = _ver_tuple(a), _ver_tuple(b)
    n = max(len(ta), len(tb))
    ta += (0,) * (n - len(ta))
    tb += (0,) * (n - len(tb))
    return ta < tb


def _installed_components(sections: dict) -> list[tuple[str, str, str]]:
    """[(kind, slug, version)] of third-party plugins/themes worth a CVE lookup.
    Skips mu-plugins, drop-ins, versionless entries, and custom kai-* slugs (not
    in the WPScan DB — they would only burn lookup budget on 404s)."""
    out: list[tuple[str, str, str]] = []
    for kind, sec in (("plugin", "PLUGINS"), ("theme", "THEMES")):
        try:
            items = json.loads(sections.get(sec, "") or "[]")
        except ValueError:
            continue
        for it in items:
            slug = (it.get("name") or "").strip()
            ver = (it.get("version") or "").strip()
            status = (it.get("status") or "").strip()
            if not slug or not ver or status in _SKIP_PLUGIN_STATUSES or slug.startswith("kai-"):
                continue
            out.append((kind, slug, ver))
    return out


def _load_wpscan_cache() -> dict:
    try:
        return json.loads(_WPSCAN_CACHE.read_text())
    except (OSError, ValueError):
        return {}


def _cache_fresh(entry: dict) -> bool:
    try:
        ts = datetime.fromisoformat(entry["fetched_at"])
        return (datetime.now(timezone.utc) - ts).total_seconds() < _WPSCAN_TTL_H * 3600
    except Exception:
        return False


def _cve_findings(scanned_sections: dict, token: str) -> tuple[list[dict], str]:
    """Query WPScan for CVEs in installed plugins/themes across the fleet.

    Deduped per unique slug (one lookup serves every site running it) and cached
    24h, so a daily run stays well inside the free-tier 25/day budget. A vuln
    applies to a site when its installed version is below ``fixed_in`` (or the
    vuln is unfixed). Rate-limit / budget exhaustion is reported, not silently
    swallowed."""
    comp_sites: dict[tuple[str, str], set] = {}
    for site, sec in scanned_sections.items():
        for kind, slug, ver in _installed_components(sec):
            comp_sites.setdefault((kind, slug), set()).add((site, ver))

    cache = _load_wpscan_cache()
    # Per-CALENDAR-DAY budget persisted in the cache: the free tier resets daily,
    # so the cap must span every run in a day (test/manual triggers included), not
    # just one run. Reset when the UTC date rolls over.
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    budget = cache.get("_budget") if isinstance(cache.get("_budget"), dict) else None
    if not budget or budget.get("date") != today:
        budget = {"date": today, "count": 0}
    headers = {"Authorization": f"Token token={token}"}
    findings: list[dict] = []
    queried = cached_hits = 0
    ratelimited = False

    for (kind, slug), site_vers in sorted(comp_sites.items()):
        ckey = f"{kind}:{slug}"
        entry = cache.get(ckey)
        if not (entry and _cache_fresh(entry)):
            if budget["count"] >= _WPSCAN_MAX_LOOKUPS:
                ratelimited = True
                continue
            budget["count"] += 1  # count the network attempt against the daily budget
            try:
                r = httpx.get(f"{_WPSCAN_API}/{kind}s/{slug}", headers=headers, timeout=15)
            except Exception as e:
                _log.warning("wpscan lookup failed %s: %s", ckey, e)
                continue
            if r.status_code == 404:
                entry = {"fetched_at": _now(), "status": "notfound", "vulns": []}
            elif r.status_code == 200:
                try:
                    body = r.json().get(slug, {}) or {}
                except ValueError:
                    _log.warning("wpscan %s -> 200 with non-JSON body; skipping", ckey)
                    continue
                entry = {"fetched_at": _now(), "status": "ok", "vulns": [
                    {"id": v.get("id"), "title": v.get("title"), "fixed_in": v.get("fixed_in"),
                     "cve": (v.get("references") or {}).get("cve") or []}
                    for v in body.get("vulnerabilities", [])
                ]}
                queried += 1
            elif r.status_code in (401, 403, 429):
                ratelimited = True  # bad token / forbidden / rate-limited — don't cache
                continue
            else:
                _log.warning("wpscan %s -> HTTP %s", ckey, r.status_code)
                continue
            cache[ckey] = entry
        else:
            cached_hits += 1

        vulns = entry.get("vulns", [])
        if not vulns:
            continue
        # Aggregate fleet-wide: one finding per vulnerable component (a plugin
        # update is one action, not one-per-site). Collect affected sites + the
        # union of applicable vulns across the versions in play.
        affected: dict[str, list] = {}          # site -> applicable vulns
        applic_all: dict = {}                    # stable vuln key -> vuln (dedup union)
        for site, ver in site_vers:
            applic = [v for v in vulns if v.get("fixed_in") is None or _ver_lt(ver, v["fixed_in"])]
            if not applic:
                continue
            affected[site] = applic
            for v in applic:
                # Dedup by WPScan vuln id (stable); fall back to (title, CVEs) so a
                # missing/duplicate title can never collapse two distinct vulns.
                vkey = v.get("id") or (v.get("title"), tuple(v.get("cve") or []))
                applic_all[vkey] = v
        if not affected:
            continue
        sites_list = sorted(affected)
        vers = sorted({ver for site, ver in site_vers if site in affected})
        union = list(applic_all.values())
        cves = sorted({f"CVE-{c}" for v in union for c in (v.get("cve") or [])})
        titles = "; ".join((v.get("title") or "")[:80] for v in union[:3])
        more = f" (+{len(union) - 3} more)" if len(union) > 3 else ""
        detail = (f"{slug} {'/'.join(vers)} on {len(sites_list)} site(s) "
                  f"({', '.join(sites_list)}): {len(union)} known vuln(s) — {titles}{more}")
        if cves:
            detail += " [" + ", ".join(cves[:6]) + "]"
        findings.append({"site": "fleet", "type": f"{kind}_cve:{slug}",
                         "severity": "high", "detail": detail})

    cache["_budget"] = budget  # persist the daily counter across runs
    try:
        _WPSCAN_CACHE.write_text(json.dumps(cache))
    except OSError as e:
        _log.warning("wpscan cache write failed: %s", e)

    note = (f"queried {queried} new + {cached_hits} cached slug(s); "
            f"{len(findings)} CVE finding(s); budget {budget['count']}/{_WPSCAN_MAX_LOOKUPS} today")
    if ratelimited:
        note += " — rate-limit/budget hit, PARTIAL"
    return findings, note


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
    scanned_sections: dict[str, dict] = {}

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

        scanned_sections[site_key] = sections  # for the fleet-wide CVE pass below

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

    # Fleet-wide CVE pass (KAI-1072): one deduped, cached WPScan lookup per unique
    # plugin/theme, evaluated against each site's installed version.
    cve_note = "skipped: no WPScan API token (add secrets/wpscan_api_token.txt)"
    token = _wpscan_token()
    if token and scanned_sections:
        try:
            cve_f, cve_note = _cve_findings(scanned_sections, token)
            for f in cve_f:
                all_findings.append(f)
                current_keys.add(f"{f['site']}:{f['type']}")
        except Exception as e:
            _log.exception("wpscan enumeration failed: %s", e)
            cve_note = f"error: {type(e).__name__}: {e}"

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
            "cve_check": cve_note,
        },
    )
