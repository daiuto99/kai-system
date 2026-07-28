import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from models import CapabilityResult
from transports.base import safe_request
from transports import wp_rest_kai_route, ssh_php_eval, cloudways_ssh_purge
from . import capability, get_transports
from wp_write_preflight import preflight as wp_write_preflight
import brand_drift
import logging

_log = logging.getLogger(__name__)


def _run_brand_drift(site: str, property: str, content: str) -> dict:
    """WP-20.2 — check authored page content against the property's brand spec and,
    on blocking drift, file a content_bug (routed to Creative) so drift lands on the
    board instead of being caught by eyeball. Never raises: a detector/filing failure
    must not break a draft write (drafts are low-risk; live overwrite is guarded by
    WP-20.4). The `property` slug wins when given (a brand may be built on a different
    host site — e.g. a the71c draft staged on sette-uno); else it derives from `site`."""
    slug = property or _site_key(site)
    try:
        report = brand_drift.detect(slug, content)
    except Exception as e:  # detector must never take down the write path
        _log.warning("brand-drift detector errored for %s: %s", slug, e)
        return {"slug": slug, "checked": False, "drift": False, "error": str(e), "findings": []}
    if report.get("drift"):
        highs = [f for f in report.get("findings", []) if f.get("severity") == "high"]
        detail = "; ".join(f["detail"] for f in highs) or report.get("summary", "")
        _log.warning("BRAND DRIFT on %s (site=%s): %s", slug, site, detail)
        try:
            from main import _create_plane_bug  # lazy: avoids capability<->main import cycle
            bug_id = _create_plane_bug(
                f"[BRAND DRIFT] {slug} — {len(highs)} blocking issue(s) on an authored page",
                f"Property {slug} (site {site}): {detail}. "
                f"Content routed through wordpress.create_page failed the brand-drift "
                f"check (WP-20.2). Review against the property BUILD_PROFILE before publish.",
            )
            if bug_id:
                report["content_bug_id"] = bug_id
        except Exception as e:
            _log.warning("brand-drift content_bug filing failed for %s: %s", slug, e)
    return report

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


@capability("wordpress.get_front_page")
def get_front_page(site: str, creds: dict, **_) -> CapabilityResult:
    """Read the live front-page setting before a homepage replacement."""
    r = safe_request(
        "GET", f"https://{creds['fqdn']}/wp-json/wp/v2/settings",
        auth=("kai", creds["app_password"]), verify=False,
    )
    if r.ok and r.data:
        show_on_front = r.data.get("show_on_front")
        page_on_front = r.data.get("page_on_front")
        return CapabilityResult(
            ok=True, status="succeeded",
            data={"show_on_front": show_on_front, "page_on_front": page_on_front},
            verification={"verified": True, "evidence": {
                "source": "wp_settings_readback",
                "show_on_front": show_on_front,
                "page_on_front": page_on_front,
            }},
            transport_used="wp_rest",
        )
    return CapabilityResult(ok=False, status="failed_recoverable",
        error={"type": "get_front_page_failed", "status_code": r.status_code,
               "detail": r.body_preview})


@capability("wordpress.create_page")
def create_page(site: str, title: str, content: str, status: str = "draft",
                creds: dict = None, caller: str = "", property: str = None,
                **_) -> CapabilityResult:
    wp_write_preflight(caller, "create_page")
    # WP-20.2 — brand-drift check on the authored content BEFORE the write. Draft
    # creation still proceeds (drafts are iterative; live overwrite is guarded by
    # WP-20.4) but drift is recorded in the result (audit trail, §5.4) and, when
    # blocking, filed as a content_bug to Creative.
    brand_drift_report = _run_brand_drift(site, property, content)
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
            data={"id": r.data["id"], "link": r.data.get("link"), "marker": marker,
                  "brand_drift": brand_drift_report},
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


@capability("wordpress.update_page")
def update_page(site: str, page_id: int, content: str, title: str = None,
                status: str = "draft", creds: dict = None, caller: str = "",
                property: str = None, **_) -> CapabilityResult:
    """WP-20.6c EDIT — update an EXISTING page, drafts-only.

    Mirrors create_page (write-preflight + brand-drift before the write) but
    targets an existing page id. Drafts-only by construction: it refuses to
    mutate a published/live page — editing live content is the guarded publish
    workflow's job (WP-20.4), never this path. A draft stays a draft.
    """
    wp_write_preflight(caller, "update_page")

    # Drafts-only guard: read the current page; refuse anything that isn't a draft.
    cur = safe_request(
        "GET", f"https://{creds['fqdn']}/wp-json/wp/v2/pages/{page_id}?context=edit",
        auth=("kai", creds["app_password"]), verify=False,
    )
    if not (cur.ok and cur.data):
        return CapabilityResult(ok=False, status="failed_recoverable",
            error={"type": "page_not_found", "page_id": page_id,
                   "status_code": cur.status_code, "detail": cur.body_preview})
    cur_status = cur.data.get("status")
    if cur_status not in ("draft", "pending", "auto-draft"):
        return CapabilityResult(ok=False, status="failed_permanent",
            error={"type": "not_a_draft", "page_id": page_id, "page_status": cur_status,
                   "detail": "EDIT is drafts-only; editing a published/live page requires "
                             "the guarded publish workflow (WP-20.4), not this path."})

    # Brand-drift on the edited content BEFORE the write (same as create_page).
    brand_drift_report = _run_brand_drift(site, property, content)
    marker = uuid.uuid4().hex[:12]
    tagged_content = f"{content}\n<!-- kai-marker:{marker} -->"
    payload = {"content": tagged_content, "status": "draft", "template": "kai-blank"}
    if title:
        payload["title"] = title
    r = safe_request(
        "POST", f"https://{creds['fqdn']}/wp-json/wp/v2/pages/{page_id}",
        auth=("kai", creds["app_password"]),
        json=payload,
        verify=False,
    )
    if r.ok and r.data:
        return CapabilityResult(ok=True, status="succeeded",
            data={"id": r.data["id"], "link": r.data.get("link"), "marker": marker,
                  "brand_drift": brand_drift_report},
            transport_used="wp_rest")
    return CapabilityResult(ok=False, status="failed_recoverable",
        error={"type": "update_failed", "status_code": r.status_code,
               "detail": r.body_preview})
