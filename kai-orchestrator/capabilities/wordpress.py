import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from models import CapabilityResult
from transports.base import safe_request
from transports import wp_rest_kai_route, ssh_php_eval, cloudways_ssh_purge
from . import capability, get_transports
from wp_write_preflight import preflight as wp_write_preflight

TRANSPORTS = {
    "wp_rest_kai_route": wp_rest_kai_route,
    "ssh_php_eval": ssh_php_eval,
    "cloudways_ssh_purge": cloudways_ssh_purge,
}

_OPTION_ALLOWLIST = {"kai_cs_active"}
_SITES_JSON = Path("/vault/00_System/wordpress_sites.json")
_SECRETS_DIR = Path("/run/wp_secrets")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _site_key(site: str) -> str:
    """Normalise site identifier: strip TLD + scheme, match by substring."""
    clean = site.replace("https://", "").replace("http://", "").rstrip("/")
    # strip TLD
    clean = clean.rsplit(".", 1)[0] if "." in clean else clean
    return clean


def _load_creds(site: str) -> dict:
    """Read site config from sites.json and app_password from secrets volume."""
    data = json.loads(_SITES_JSON.read_text())
    sites = data["sites"]
    # exact key match first, then substring
    key = _site_key(site)
    entry = sites.get(key) or next(
        (v for k, v in sites.items() if key in k or k in key), None
    )
    if entry is None:
        raise KeyError(f"Site '{site}' not found in wordpress_sites.json")

    pw_path = _SECRETS_DIR / f"wp_{key}_kai_app_password.txt"
    if not pw_path.exists():
        # try exact site key from json
        for k in sites:
            candidate = _SECRETS_DIR / f"wp_{k}_kai_app_password.txt"
            if candidate.exists() and (key in k or k in key):
                pw_path = candidate
                break

    app_password = pw_path.read_text().strip()
    return {
        "fqdn": entry["cloudways_fqdn"],
        "app_password": app_password,
        "cloudways_sys_user": entry["cloudways_sys_user"],
        "url": entry.get("url", ""),
        "username": entry.get("username", "kai"),
    }


@capability("wordpress.load_config")
def load_config(site: str, **_) -> CapabilityResult:
    try:
        creds = _load_creds(site)
        return CapabilityResult(
            ok=True, status="succeeded",
            data={"site": site, "fqdn": creds["fqdn"], "creds": creds},
        )
    except Exception as e:
        return CapabilityResult(ok=False, status="failed_final",
            error={"type": "config_error", "message": str(e)})


@capability("wordpress.probe_credentials")
def probe_credentials(site: str, creds: dict, **_) -> CapabilityResult:
    r = safe_request(
        "GET", f"https://{creds['fqdn']}/wp-json/wp/v2/users/me",
        auth=("kai", creds["app_password"]), verify=False,
    )
    if r.ok:
        return CapabilityResult(ok=True, status="succeeded",
            data={"authenticated": True, "user": r.data.get("name") if r.data else None})
    return CapabilityResult(ok=False, status="failed_final",
        error={"type": "auth_failure", "status_code": r.status_code})


@capability("wordpress.create_page")
def create_page(site: str, title: str, content: str, status: str = "draft",
                creds: dict = None, caller: str = "", **_) -> CapabilityResult:
    wp_write_preflight(caller, "create_page")
    marker = uuid.uuid4().hex[:12]
    tagged_content = f"{content}\n<!-- kai-marker:{marker} -->"
    r = safe_request(
        "POST", f"https://{creds['fqdn']}/wp-json/wp/v2/pages",
        auth=("kai", creds["app_password"]),
        json={"title": title, "content": tagged_content, "status": status,
              "template": "kai-blank"},
        verify=False,
    )
    if r.ok and r.data:
        return CapabilityResult(ok=True, status="succeeded",
            data={"id": r.data["id"], "link": r.data.get("link"), "marker": marker},
            transport_used="wp_rest")
    return CapabilityResult(ok=False, status="failed_recoverable",
        error={"type": "create_failed", "status_code": r.status_code,
               "detail": r.body_preview})


@capability("wordpress.set_option")
def set_option(site: str, option: str, value: str, creds: dict = None, caller: str = "", **_) -> CapabilityResult:
    if option not in _OPTION_ALLOWLIST:
        return CapabilityResult(ok=False, status="failed_final",
            error={"type": "option_not_allowed",
                   "message": f"'{option}' not in capability allowlist"})

    wp_write_preflight(caller, "set_option")
    for transport_name in get_transports(site, "set_option"):
        transport = TRANSPORTS.get(transport_name)
        if transport is None:
            continue
        write_r = transport.set_option(site, option, value, creds)
        if not write_r.ok:
            continue
        read_r = transport.get_option(site, option, creds)
        if read_r.ok and read_r.data and read_r.data.get("value") == value:
            return CapabilityResult(
                ok=True, status="succeeded",
                data={"option": option, "value": value},
                verification={"verified": True, "evidence": {
                    "written_value": value,
                    "read_back_value": read_r.data["value"],
                    "transport": transport_name,
                    "verified_at": _now(),
                }},
                transport_used=transport_name,
            )

    return CapabilityResult(ok=False, status="failed_recoverable",
        error={"type": "all_transports_failed",
               "tried": get_transports(site, "set_option")})


@capability("wordpress.set_front_page")
def set_front_page(site: str, page_id: int, creds: dict = None, caller: str = "", **_) -> CapabilityResult:
    wp_write_preflight(caller, "set_front_page")
    r = safe_request(
        "POST", f"https://{creds['fqdn']}/wp-json/wp/v2/settings",
        auth=("kai", creds["app_password"]),
        json={"show_on_front": "page", "page_on_front": page_id},
        verify=False,
    )
    if r.ok:
        return CapabilityResult(ok=True, status="succeeded",
            data={"page_id": page_id}, transport_used="wp_rest")
    return CapabilityResult(ok=False, status="failed_recoverable",
        error={"type": "set_front_page_failed", "status_code": r.status_code})


@capability("wordpress.publish")
def publish(site: str, page_id: int, creds: dict = None, caller: str = "", **_) -> CapabilityResult:
    wp_write_preflight(caller, "publish")
    r = safe_request(
        "POST", f"https://{creds['fqdn']}/wp-json/wp/v2/pages/{page_id}",
        auth=("kai", creds["app_password"]),
        json={"status": "publish"},
        verify=False,
    )
    if r.ok and r.data and r.data.get("status") == "publish":
        return CapabilityResult(ok=True, status="succeeded",
            data={"id": page_id, "status": "publish"}, transport_used="wp_rest")
    return CapabilityResult(ok=False, status="failed_recoverable",
        error={"type": "publish_failed", "status_code": r.status_code})


@capability("wordpress.purge_varnish")
def purge_varnish(site: str, url_path: str = "/", creds: dict = None, **_) -> CapabilityResult:
    r = cloudways_ssh_purge.purge(site, url_path, creds)
    if r.ok:
        return CapabilityResult(ok=True, status="succeeded",
            data=r.data, transport_used="cloudways_ssh_purge")
    return CapabilityResult(ok=False, status="failed_recoverable",
        error={"type": "purge_failed", "detail": r.error})


@capability("wordpress.verify_live")
def verify_live(site: str, url: str = None, marker: str = None,
                creds: dict = None, **_) -> CapabilityResult:
    target = url or f"https://{creds['fqdn']}/"
    r = safe_request("GET", target, verify=False)
    found = bool(marker and marker in (r.body_preview or ""))
    if not found and r.is_cloudflare_challenge:
        custom = creds.get("url", "").replace("https://", "").rstrip("/")
        r2 = safe_request("GET", target, headers={"Host": custom}, verify=False)
        found = bool(marker and marker in (r2.body_preview or ""))
    if found:
        return CapabilityResult(ok=True, status="succeeded",
            data={"marker": marker, "url": target, "found": True})
    return CapabilityResult(ok=False, status="failed_recoverable",
        error={"type": "marker_not_found", "cloudflare_blocked": r.is_cloudflare_challenge})
