#!/usr/bin/env python3
"""Rotate a WordPress application-password for a Cloudways-hosted site — value-safe.

WHY (443fb11e / L18): a WP app-password leaked from KAI's jobs DB into a session
transcript, so the exposed credential must be rotated. The mode gate correctly
blocks Claude-initiated writes to secrets/, so rotation cannot be done from a
Claude Bash turn. This tool is the authorized-execution path
(feedback_authorized_execution_path): Leo runs it on the worker; it performs the
whole rotation without the new value ever crossing a session boundary.

    python3 scripts/rotate_wp_app_password.py <site>            # interactive confirm
    python3 scripts/rotate_wp_app_password.py <site> --dry-run  # inspect, no changes
    python3 scripts/rotate_wp_app_password.py <site> --yes      # skip the prompt

Rollback-safe ordering:
  1. read current secret value (kept in memory only, for rollback)
  2. snapshot the site's existing kai app-password UUIDs
  3. create a NEW app-password via wp-cli over the Cloudways master SSH
  4. write it to secrets/wp_<key>_kai_app_password.txt (0600)
  5. recreate the services that mount it, so /run/wp_secrets updates
  6. verify the new password authenticates against the live WP REST API
  7. success → revoke every previously-existing kai app-password
     failure → restore the prior secret file + recreate + abort (no revoke)
  8. append a METADATA-ONLY audit record (never secret bytes)

The new/old password VALUES are never printed, logged, or returned — only
lengths, UUIDs, and auth status.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent          # ~/kai-system
SECRETS = ROOT / "secrets"
AUDIT_LOG = ROOT / "logs" / "wp_credential_rotation.jsonl"
ORCH = "kai-orchestrator"
SITES_JSON_IN_ORCH = "/vault/00_System/wordpress_sites.json"

# Mirrors kai-orchestrator/transports/ssh_php_eval.py — the approved Cloudways
# transport (server master credential; no per-app keys).
_SSH_MASTER = "master_vvbwxpwpcc@134.209.166.23"
_SSH_KEY = "/run/secrets/cloudways_ssh_key"
_SSH_OPTS = ("-i", _SSH_KEY, "-o", "StrictHostKeyChecking=no",
             "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR",
             "-o", "BatchMode=yes", "-o", "ConnectTimeout=15")
# Services that mount the WP app-password docker secret (from docker-compose.yml).
_MOUNTING_SERVICES = ("kai-worker-api", "kai-orchestrator")


class RotateError(RuntimeError):
    """Safe-to-report failure — never carries secret bytes."""


def _scrub(text: str, secrets) -> str:
    """Remove any known secret value from a string before it is printed/logged.

    Defense against wp-cli/docker stderr (or an exception) echoing a credential
    into the session/audit — the exact L18 failure this tool exists to prevent."""
    out = text or ""
    for s in secrets:
        if s and len(s) >= 8:
            out = out.replace(s, "[REDACTED]")
    return out


def _site_key(site: str) -> str:
    return site.replace("https://", "").replace("http://", "").split("/")[0].split(".")[0]


def _load_site() -> dict:
    """Read the (non-secret) site config from wordpress_sites.json via the orchestrator."""
    out = subprocess.run(["docker", "exec", ORCH, "cat", SITES_JSON_IN_ORCH],
                         capture_output=True, text=True, timeout=15)
    if out.returncode != 0:
        raise RotateError(f"could not read sites config ({out.stderr.strip()[:120]})")
    return json.loads(out.stdout)["sites"]


def _wp(sys_user: str, *wp_args: str, timeout: int = 40) -> subprocess.CompletedProcess:
    """Run a wp-cli command for the site via the orchestrator's Cloudways SSH."""
    public_html = f"/home/1623875.cloudwaysapps.com/{sys_user}/public_html"
    remote = "cd " + public_html + " && wp " + " ".join(_q(a) for a in wp_args)
    cmd = ["docker", "exec", ORCH, "ssh", *_SSH_OPTS, _SSH_MASTER, remote]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _q(s: str) -> str:
    import shlex
    return shlex.quote(s)


def _list_app_passwords(sys_user: str) -> list[dict]:
    r = _wp(sys_user, "user", "application-password", "list", "kai",
            "--fields=name,uuid,created", "--format=json")
    if r.returncode != 0:
        raise RotateError(f"app-password list failed ({r.stderr.strip()[:160]})")
    try:
        return json.loads(r.stdout or "[]")
    except json.JSONDecodeError:
        raise RotateError("app-password list returned non-JSON")


def _verify(fqdn: str, password: str) -> int:
    """Authenticate against the live WP REST API. Returns the HTTP status."""
    import base64
    import ssl
    url = f"https://{fqdn}/wp-json/wp/v2/users/me"
    token = base64.b64encode(f"kai:{password}".encode()).decode()
    req = urllib.request.Request(url, headers={
        "Authorization": f"Basic {token}",
        # Cloudways' WAF 403s the default python-urllib User-Agent.
        "User-Agent": "Mozilla/5.0 (KAI-rotate)",
    })
    # The Cloudways app fqdn cert does not validate for direct API calls — the
    # orchestrator's own WP client uses verify=False; mirror that here (the app
    # host is reached over the trusted server path, not the public domain).
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except Exception as exc:  # noqa: BLE001
        raise RotateError(f"verify request failed ({type(exc).__name__})")


def _recreate_services() -> None:
    # --force-recreate: docker secrets are read at container CREATE, and a plain
    # `up -d` may leave an unchanged container running with the OLD secret still
    # mounted. Forcing recreate guarantees /run/wp_secrets reloads the new value
    # before we ever revoke the old credential.
    r = subprocess.run(
        ["docker", "compose", "up", "-d", "--force-recreate", *_MOUNTING_SERVICES],
        cwd=str(ROOT), capture_output=True, text=True, timeout=240)
    if r.returncode != 0:
        raise RotateError(f"service recreate failed ({r.stderr.strip()[:200]})")


def _write_secret(path: Path, value: str) -> None:
    """Atomic mode-0600 write: temp file → fsync → os.replace, so a crash or
    partial write can never leave the secret file empty/corrupt."""
    import os
    import tempfile
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp_", suffix=".secret")
    try:
        os.fchmod(fd, 0o600)
        os.write(fd, value.encode())
        os.fsync(fd)
    finally:
        os.close(fd)
    try:
        os.replace(tmp, path)            # atomic swap
        os.chmod(path, 0o600)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _audit(record: dict) -> None:
    AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    record["ts"] = datetime.now(timezone.utc).isoformat()
    with open(AUDIT_LOG, "a") as fh:
        fh.write(json.dumps(record) + "\n")


def rotate(site: str, *, dry_run: bool, assume_yes: bool) -> int:
    sites = _load_site()
    key = _site_key(site)
    entry = sites.get(key) or next((v for k, v in sites.items() if key in k or k in key), None)
    if entry is None:
        raise RotateError(f"site '{site}' not found in wordpress_sites.json")
    # resolve the canonical secret-file key from the sites map
    site_key = key if key in sites else next(k for k, v in sites.items() if v is entry)
    sys_user = entry["cloudways_sys_user"]
    fqdn = entry["cloudways_fqdn"]
    secret_path = SECRETS / f"wp_{site_key}_kai_app_password.txt"

    print(f"site           : {site_key}  ({entry.get('url', fqdn)})")
    print(f"cloudways user : {sys_user}")
    print(f"secret file    : {secret_path}")
    existing = _list_app_passwords(sys_user)
    print(f"existing kai app-passwords: {[(p['name'], p['uuid']) for p in existing]}")

    if dry_run:
        print("\n[dry-run] access OK; no changes made.")
        return 0
    if not secret_path.exists():
        raise RotateError(f"secret file missing: {secret_path}")

    if not assume_yes:
        ans = input(f"\nType '{site_key}' to rotate its app-password (this revokes the old one): ")
        if ans.strip() != site_key:
            print("aborted — confirmation did not match.")
            return 1

    old_value = secret_path.read_text()           # in-memory rollback copy
    pre_uuids = [p["uuid"] for p in existing]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")

    # ── create new (uniquely named so rollback can target THIS pw exactly) ──
    import re
    import secrets as _secrets
    # timestamp + random suffix → collision-free even across concurrent rotations
    # in the same second, so rollback deletes ONLY this run's credential.
    created_name = f"kai-rotated-{stamp}-{_secrets.token_hex(4)}"
    cr = _wp(sys_user, "user", "application-password", "create", "kai",
             created_name, "--porcelain")
    # --porcelain must be EXACTLY the password (alnum, 20–64) + an optional single
    # trailing newline. Validate the RAW stdout (not a stripped copy) so any
    # contamination — warning lines, spaces — is rejected BEFORE we touch disk.
    candidate = cr.stdout or ""
    if cr.returncode != 0 or not re.fullmatch(r"[A-Za-z0-9]{20,64}\n?", candidate):
        # Scrub every secret-shaped token from stdout individually (not just the
        # whole string): if create succeeded-but-contaminated, the password token
        # could echo separately in stderr and must be redacted wherever it appears.
        tokens = [t for t in re.split(r"\s+", candidate) if re.fullmatch(r"[A-Za-z0-9]{16,80}", t)]
        raise RotateError(_scrub(
            f"create failed or produced contaminated output "
            f"(rc={cr.returncode}, {cr.stderr.strip()[:120]}) — nothing changed on disk",
            [old_value, *tokens]))
    new_value = candidate.strip()
    sensitive = [old_value, new_value]
    # Deterministically capture THIS credential's UUID (new since the pre-snapshot
    # AND carrying our unique name) so rollback deletes exactly it — never another
    # run's pw. If it can't be unambiguously identified, rollback revokes nothing.
    try:
        mine = [p["uuid"] for p in _list_app_passwords(sys_user)
                if p.get("name") == created_name and p["uuid"] not in pre_uuids]
    except RotateError:
        mine = []
    my_uuid = mine[0] if len(mine) == 1 else None
    print(f"created new app-password (len={len(new_value)})")

    # Outer boundary: any error escaping the mutating region is scrubbed of both
    # credential values before it can reach the console/logs (L18).
    try:
        # ── write + recreate + verify, with rollback ──
        try:
            _write_secret(secret_path, new_value)
            _recreate_services()
            status = _verify(fqdn, new_value)
            if status != 200:
                raise RotateError(f"new password did not authenticate (HTTP {status})")
            print(f"verified: new password authenticates (HTTP {status})")
        except Exception as exc:  # noqa: BLE001
            # Restore the previous secret and recreate. Delete the new pw ONLY
            # after confirming the old credential authenticates again — never
            # revoke a credential the running services might still be using.
            rolled_back = False
            try:
                _write_secret(secret_path, old_value)
                _recreate_services()
                rolled_back = (_verify(fqdn, old_value) == 200)
            except Exception:  # noqa: BLE001
                rolled_back = False
            if rolled_back:
                # Delete ONLY the exact UUID we captured at creation time — never a
                # name/set-diff match, so a concurrently-created credential is safe.
                cleaned = 0
                if my_uuid and _wp(sys_user, "user", "application-password",
                                   "delete", "kai", my_uuid).returncode == 0:
                    cleaned = 1
                _audit({"site": site_key, "action": "rotate", "result": "rolled_back",
                        "reason": _scrub(str(exc), sensitive)[:200],
                        "new_len": len(new_value), "new_revoked": cleaned})
                raise RotateError(f"rotation rolled back — old credential restored "
                                  f"({'new pw revoked' if cleaned else 'new pw may linger — check site'})")
            _audit({"site": site_key, "action": "rotate", "result": "rollback_unconfirmed",
                    "reason": _scrub(str(exc), sensitive)[:200], "new_len": len(new_value),
                    "manual_action": "verify creds + revoke stale by hand"})
            raise RotateError("rotation FAILED and rollback could NOT be confirmed — BOTH "
                              "old and new app-passwords left VALID, nothing revoked. Check "
                              "the site + secret file by hand.")

        # ── success → revoke every previously-existing kai app-password ──
        revoked, failed_revoke = [], []
        for u in pre_uuids:
            if _wp(sys_user, "user", "application-password", "delete", "kai", u).returncode == 0:
                revoked.append(u)
            else:
                failed_revoke.append(u)
        _audit({"site": site_key, "action": "rotate", "result": "ok",
                "new_len": len(new_value), "revoked_uuids": revoked,
                "revoke_failed_uuids": failed_revoke, "recreated": list(_MOUNTING_SERVICES)})
        msg = f"revoked {len(revoked)}/{len(pre_uuids)} prior app-password(s)"
        if failed_revoke:
            msg += f" — WARNING {len(failed_revoke)} could not be revoked: {failed_revoke}"
        print(msg)
        print("\nROTATION COMPLETE — new credential live, old revoked, value never printed.")
        return 0
    except RotateError as e:
        raise RotateError(_scrub(str(e), sensitive))


def main() -> int:
    ap = argparse.ArgumentParser(description="Rotate a WP application-password (value-safe).")
    ap.add_argument("site", help="site key or url, e.g. the71c")
    ap.add_argument("--dry-run", action="store_true", help="inspect access; make no changes")
    ap.add_argument("--yes", action="store_true", help="skip the interactive confirmation")
    args = ap.parse_args()
    try:
        return rotate(args.site, dry_run=args.dry_run, assume_yes=args.yes)
    except RotateError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
