#!/usr/bin/env python3
"""CUR-1 — read-only system currency scanner (worker).

Answers one question per component: "is it current, and if not how stale?"
Writes freshness_state.json (worker SSOT). READ-ONLY by construction: no
mutations, no restarts, no package installs, no Plane writes — only its own
state file.

HONESTY RULE (no-theater): a layer or component with no live reader, or whose
reader raises, is recorded status="not-checked" with the reason — NEVER a
faked "fresh". Green must be earned by a real reading.

Layers: os_apt, container_images, tls_certs, wp_fleet (CUR-3, report-only).
Later phases add: py_deps/npm_deps + CVE (CUR-2).
"""
from __future__ import annotations

import json
import re
import socket
import ssl
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "shared"))  # /shared convention (matches worker-api PYTHONPATH)
import findings  # Findings Contract — enforce cause-or-not-yet-diagnosed before publishing
STATE_DIR = ROOT / "shared" / "currency"
STATE_FILE = STATE_DIR / "freshness_state.json"
TLS_CONFIG = STATE_DIR / "tls_endpoints.json"  # optional: ["host:port", ...]
HOST = socket.gethostname()

FRESH = "fresh"
STALE = "stale"
NOT_CHECKED = "not-checked"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run(args, timeout=25):
    r = subprocess.run(args, text=True, capture_output=True, timeout=timeout, check=False)
    return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()


def _age_days(iso_ts: str) -> float | None:
    """Whole/fractional days since an ISO/RFC3339 timestamp, or None if unparseable."""
    if not iso_ts:
        return None
    s = iso_ts.strip()
    # docker emits e.g. 2026-06-30T12:00:00.123456789Z or with +00:00
    s = re.sub(r"\.\d+", "", s)
    s = s.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return round((datetime.now(timezone.utc) - dt).total_seconds() / 86400.0, 1)


# ---------------------------------------------------------------------------
# os_apt — worker package currency (security vs regular pending) + auto-upgrades
# ---------------------------------------------------------------------------
def read_os_apt() -> dict:
    checked = now_iso()
    try:
        regular = security = None
        # apt-check emits "regular;security" on stderr — the canonical source
        code, out, err = _run(["/usr/lib/update-notifier/apt-check"], timeout=30)
        blob = err or out
        m = re.match(r"^\s*(\d+)\s*;\s*(\d+)\s*$", blob)
        if m:
            regular, security = int(m.group(1)), int(m.group(2))
        else:
            # fallback: simulate an upgrade and count Inst lines
            code, out, _ = _run(["apt-get", "-s", "upgrade"], timeout=40)
            insts = [ln for ln in out.splitlines() if ln.startswith("Inst ")]
            regular = len(insts)
            security = sum(1 for ln in insts if "security" in ln.lower())

        # is unattended-upgrades actually enforcing security patches?
        auto_enforced = False
        cfg = Path("/etc/apt/apt.conf.d/20auto-upgrades")
        if cfg.exists():
            txt = cfg.read_text(errors="ignore")
            auto_enforced = ('Update-Package-Lists "1"' in txt
                             and 'Unattended-Upgrade "1"' in txt)

        status = FRESH if (security == 0) else STALE
        detail = f"{security} security / {regular} regular pending"
        if not auto_enforced:
            detail += " · unattended-upgrades NOT enforced"

        # Signal->cause discipline: a stale reading never ships without a verified
        # cause (or an explicit "not yet diagnosed"). Diagnose WHY they're pending:
        # unattended-upgrades only does conservative `apt upgrade` and refuses to
        # install new packages — so security items that require new deps sit until
        # a gated `apt full-upgrade`.
        cause = None
        if security and security > 0:
            _, up_out, _ = _run(["apt-get", "-s", "upgrade"], timeout=40)
            kept = 0
            m2 = re.search(r"(\d+)\s+not upgraded", up_out)
            if m2:
                kept = int(m2.group(1))
            _, dist_out, _ = _run(["apt-get", "-s", "dist-upgrade"], timeout=40)
            m3 = re.search(r"(\d+)\s+newly installed", dist_out)
            new_pkgs = int(m3.group(1)) if m3 else 0
            touches_docker = "docker-ce" in dist_out or "containerd" in dist_out
            if new_pkgs > 0 or kept > 0:
                cause = (f"held back by unattended-upgrades policy (it won't install new packages); "
                         f"clearing needs a gated `apt full-upgrade` — pulls in {new_pkgs} new package(s)"
                         + (" incl docker-ce (restarts containers)" if touches_docker else ""))
            else:
                cause = "not yet diagnosed"

        comp = {
            "name": "apt packages",
            "current": security == 0,
            "security_pending": security,
            "regular_pending": regular,
            "auto_upgrades_enforced": auto_enforced,
            "risk_tier": "auto",  # OS security patches auto-apply (unattended-upgrades)
            "status": status,
            "checked_at": checked,
        }
        if cause:
            comp["cause"] = cause
            detail += f" · cause: {cause}"
        return {"status": status, "checked_at": checked, "detail": detail, "components": [comp]}
    except Exception as exc:  # honest: reader failed -> not-checked, never green
        return {"status": NOT_CHECKED, "checked_at": checked,
                "detail": f"reader error: {type(exc).__name__}: {exc}", "components": []}


# ---------------------------------------------------------------------------
# container_images — installed image age per running container.
# Registry-latest comparison is NOT-CHECKED in CUR-1 (needs a registry pull;
# added in a later phase). We report real installed age, honestly labelled.
# ---------------------------------------------------------------------------
def read_container_images() -> dict:
    checked = now_iso()
    try:
        code, out, err = _run(["docker", "ps", "--format", "{{.Names}}\t{{.Image}}"], timeout=25)
        if code != 0:
            return {"status": NOT_CHECKED, "checked_at": checked,
                    "detail": f"docker ps failed: {err[:160]}", "components": []}
        comps = []
        for line in out.splitlines():
            if "\t" not in line:
                continue
            name, image = line.split("\t", 1)
            c2, created, _ = _run(["docker", "image", "inspect", image, "--format", "{{.Created}}"], timeout=20)
            age = _age_days(created) if c2 == 0 else None
            comps.append({
                "name": name,
                "image": image,
                "installed_age_days": age,
                "latest": None,               # registry comparison: not yet a live reader
                "current": None,              # unknown until registry check lands
                "risk_tier": "gated",         # image bumps are gated (Leo approves)
                "status": NOT_CHECKED,
                "note": "installed age only; registry-latest comparison not yet implemented",
                "checked_at": checked,
            })
        oldest = max((c["installed_age_days"] or 0) for c in comps) if comps else 0
        return {
            "status": NOT_CHECKED,  # honest: no registry reader yet, so currency is unknown
            "checked_at": checked,
            "detail": f"{len(comps)} running container(s); oldest image {oldest}d old; registry comparison not-checked",
            "components": comps,
        }
    except Exception as exc:
        return {"status": NOT_CHECKED, "checked_at": checked,
                "detail": f"reader error: {type(exc).__name__}: {exc}", "components": []}


# ---------------------------------------------------------------------------
# tls_certs — days-to-expiry for a configured endpoint list.
# Worker services are plain http on the tailnet (no TLS), so with no endpoints
# configured this honestly reports not-checked rather than a fake pass.
# ---------------------------------------------------------------------------
def _cert_days_left(host: str, port: int, timeout=8) -> float:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with socket.create_connection((host, port), timeout=timeout) as sock:
        with ctx.wrap_socket(sock, server_hostname=host) as ssock:
            cert = ssock.getpeercert()
    not_after = cert.get("notAfter")
    exp = datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
    return round((exp - datetime.now(timezone.utc)).total_seconds() / 86400.0, 1)


def read_tls_certs(threshold_days=21) -> dict:
    checked = now_iso()
    endpoints = []
    if TLS_CONFIG.exists():
        try:
            endpoints = json.loads(TLS_CONFIG.read_text())
        except Exception:
            endpoints = []
    if not endpoints:
        return {"status": NOT_CHECKED, "checked_at": checked,
                "detail": "no TLS endpoints configured (worker services are http on tailnet); "
                          f"populate {TLS_CONFIG.name} to enable",
                "components": []}
    comps = []
    worst = FRESH
    for ep in endpoints:
        host, _, port = str(ep).partition(":")
        port = int(port or 443)
        try:
            days = _cert_days_left(host, port)
            st = FRESH if days > threshold_days else STALE
            if st == STALE:
                worst = STALE
            comps.append({"name": ep, "days_to_expiry": days, "current": days > threshold_days,
                          "risk_tier": "notify", "status": st, "checked_at": checked})
        except Exception as exc:
            comps.append({"name": ep, "days_to_expiry": None, "current": None,
                          "risk_tier": "notify", "status": NOT_CHECKED,
                          "note": f"probe failed: {type(exc).__name__}", "checked_at": checked})
    return {"status": worst if comps else NOT_CHECKED, "checked_at": checked,
            "detail": f"{len(comps)} endpoint(s), threshold {threshold_days}d", "components": comps}


# ── CUR-3: WordPress fleet currency (core + plugin update availability) ────────
# Read-only by construction: SSH to each Cloudways app via the master operator and
# run ONLY `wp core check-update` + `wp plugin list --update=available`. WP updates
# are NEVER applied — this reader reports availability, nothing more. The remote
# script is a module constant; the sole substitution is the site's sys user,
# validated against ^[a-z0-9]+$ (no model/DB string reaches the shell).
_CLOUDWAYS_HOST = "master_vvbwxpwpcc@134.209.166.23"
_WP_SYSUSER_RE = re.compile(r"^[a-z0-9]+$")
_WP_CURRENCY_SCRIPT = r"""
cd "$HOME/applications/__SYSUSER__/public_html" 2>/dev/null || { echo "__CURERR__:cd_failed"; exit 3; }
WP="wp --skip-plugins --skip-themes"
printf '\n===CORE===\n'; $WP core check-update --format=count 2>&1
printf '\n===PLUGINS===\n'; $WP plugin list --update=available --format=count 2>&1
printf '\n===DONE===\n'
"""


def _cloudways_key():
    """Resolve the master SSH key from the container secret path or the host path."""
    for p in ("/run/secrets/cloudways_ssh_key", str(ROOT / "secrets" / "cloudways_ssh_key")):
        if Path(p).exists():
            return p
    return None


def _wp_sites() -> dict:
    for p in ("/vault/00_System/wordpress_sites.json", "/home/leo/vault/00_System/wordpress_sites.json"):
        if Path(p).exists():
            try:
                return json.loads(Path(p).read_text()).get("sites", {})
            except (OSError, ValueError):
                return {}
    return {}


def _wp_section_int(raw: str, marker: str):
    """Parse the integer value printed under `===MARKER===` in the remote output."""
    token = f"==={marker}==="
    idx = raw.find(token)
    if idx < 0:
        return None
    tail = raw[idx + len(token):].lstrip("\n")
    line = tail.split("\n", 1)[0].strip()
    return int(line) if line.isdigit() else None


def read_wp_fleet(sites: dict | None = None, runner=None, key: str | None = None) -> dict:
    """Report WP core + plugin update AVAILABILITY across the Cloudways fleet.

    Report-only: no update is ever applied. A site that cannot be read is recorded
    NOT_CHECKED with a reason (never a faked "current"). `sites`/`runner`/`key` are
    injectable for tests; production resolves them live.
    """
    checked = now_iso()
    sites = _wp_sites() if sites is None else sites
    key = _cloudways_key() if key is None else key
    if not sites or key is None:
        return {"status": NOT_CHECKED, "checked_at": checked,
                "detail": "cloudways key or wordpress_sites.json unavailable — cannot read WP fleet",
                "components": []}
    ssh_opts = ["-i", key, "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
                "-o", "LogLevel=ERROR", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
    run = runner or (lambda argv: subprocess.run(argv, capture_output=True, text=True, timeout=60))
    comps, worst, total_updates = [], FRESH, 0
    for slug, meta in sites.items():
        sysuser = str(meta.get("cloudways_sys_user", ""))
        if not _WP_SYSUSER_RE.match(sysuser):
            comps.append({"name": slug, "status": NOT_CHECKED, "checked_at": checked,
                          "note": "missing or invalid cloudways sys user"})
            worst = worst if worst != FRESH else NOT_CHECKED
            continue
        script = _WP_CURRENCY_SCRIPT.replace("__SYSUSER__", sysuser)
        try:
            r = run(["ssh"] + ssh_opts + [_CLOUDWAYS_HOST, script])
            out = r.stdout or ""
            rc = r.returncode
        except subprocess.TimeoutExpired:
            out, rc = "", -1
        if "__CURERR__:cd_failed" in out or "===DONE===" not in out:
            comps.append({"name": slug, "status": NOT_CHECKED, "checked_at": checked,
                          "note": f"unscannable (rc={rc})"})
            worst = worst if worst != FRESH else NOT_CHECKED
            continue
        core = _wp_section_int(out, "CORE")
        plugins = _wp_section_int(out, "PLUGINS")
        if core is None or plugins is None:
            comps.append({"name": slug, "status": NOT_CHECKED, "checked_at": checked,
                          "note": "update counts unparseable"})
            worst = worst if worst != FRESH else NOT_CHECKED
            continue
        n = core + plugins
        total_updates += n
        st = FRESH if n == 0 else STALE
        comp = {"name": slug, "core_updates": core, "plugin_updates": plugins,
                "current": n == 0, "risk_tier": "dashboard", "status": st, "checked_at": checked}
        if st == STALE:
            comp["cause"] = (f"{core} core + {plugins} plugin update(s) available; "
                             "report-only — WP updates are never auto-applied")
            worst = STALE
        comps.append(comp)
    current_n = sum(1 for c in comps if c.get("current"))
    detail = f"{current_n}/{len(sites)} sites current; {total_updates} update(s) available fleet-wide"
    layer = {"status": worst if comps else NOT_CHECKED, "checked_at": checked,
             "detail": detail, "components": comps}
    if worst == STALE:
        layer["cause"] = (f"{total_updates} WP core/plugin update(s) available across the fleet; "
                          "report-only by policy (KAI never auto-applies WP updates)")
    return layer


def main():
    layers = {
        "os_apt": read_os_apt(),
        "container_images": read_container_images(),
        "tls_certs": read_tls_certs(),
        "wp_fleet": read_wp_fleet(),
    }
    # Findings Contract: no bad-status finding may be published without a cause.
    # Anything undiagnosed is stamped not-yet-diagnosed (honest), never dropped.
    undiagnosed = findings.enforce_causes(layers)
    findings.assert_contract(layers)  # fail-closed: refuse to write a bare alarm
    counts = {FRESH: 0, STALE: 0, NOT_CHECKED: 0}
    for layer in layers.values():
        counts[layer["status"]] = counts.get(layer["status"], 0) + 1
    state = {
        "generated_at": now_iso(),
        "host": HOST,
        "scanner": "currency_scan.py (CUR-1 + CUR-3 wp_fleet)",
        "layers": layers,
        "rollup": {
            "fresh": counts[FRESH],
            "stale": counts[STALE],
            "not_checked": counts[NOT_CHECKED],
            "undiagnosed": undiagnosed,
            "total": len(layers),
        },
    }
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(STATE_FILE)
    print(json.dumps(state, indent=2))


if __name__ == "__main__":
    main()
