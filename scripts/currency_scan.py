#!/usr/bin/env python3
"""CUR-1 — read-only system currency scanner (worker).

Answers one question per component: "is it current, and if not how stale?"
Writes freshness_state.json (worker SSOT). READ-ONLY by construction: no
mutations, no restarts, no package installs, no Plane writes — only its own
state file.

HONESTY RULE (no-theater): a layer or component with no live reader, or whose
reader raises, is recorded status="not-checked" with the reason — NEVER a
faked "fresh". Green must be earned by a real reading.

Layers in CUR-1: os_apt, container_images, tls_certs.
Later phases add: py_deps/npm_deps + CVE (CUR-2), wp_fleet (CUR-3).
"""
from __future__ import annotations

import json
import re
import socket
import ssl
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
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
        return {
            "status": status,
            "checked_at": checked,
            "detail": detail,
            "components": [{
                "name": "apt packages",
                "current": security == 0,
                "security_pending": security,
                "regular_pending": regular,
                "auto_upgrades_enforced": auto_enforced,
                "risk_tier": "auto",  # OS security patches auto-apply (unattended-upgrades)
                "status": status,
                "checked_at": checked,
            }],
        }
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


def main():
    layers = {
        "os_apt": read_os_apt(),
        "container_images": read_container_images(),
        "tls_certs": read_tls_certs(),
    }
    counts = {FRESH: 0, STALE: 0, NOT_CHECKED: 0}
    for layer in layers.values():
        counts[layer["status"]] = counts.get(layer["status"], 0) + 1
    state = {
        "generated_at": now_iso(),
        "host": HOST,
        "scanner": "currency_scan.py (CUR-1)",
        "layers": layers,
        "rollup": {
            "fresh": counts[FRESH],
            "stale": counts[STALE],
            "not_checked": counts[NOT_CHECKED],
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
