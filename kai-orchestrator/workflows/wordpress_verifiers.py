from transports.base import safe_request


def verify_page_exists(site, creds, result) -> dict:
    page_id = result.get("data", {}).get("id")
    r = safe_request(
        "GET", f"https://{creds['fqdn']}/wp-json/wp/v2/pages/{page_id}",
        auth=("kai", creds["app_password"]), verify=False,
    )
    # KAI-41 — surface the WP page status (draft/publish) so a caller can confirm
    # drafts-only from the verification evidence. `status` stays the HTTP code (an
    # existing consumer contract); `wp_status` is the new page-state field.
    wp_status = r.data.get("status") if r.data else None
    return {
        "verified": bool(r.ok and r.data and r.data.get("id") == page_id),
        "evidence": {"page_id": page_id, "status": r.status_code, "wp_status": wp_status},
    }


def verify_cs_off(site, creds, result) -> dict:
    r = safe_request(
        "GET", f"https://{creds['fqdn']}/wp-json/kai/v1/option/kai_cs_active",
        auth=("kai", creds["app_password"]), verify=False,
    )
    v = r.data.get("value") if r.data else None
    return {"verified": v == "0", "evidence": {"actual": v, "expected": "0"}}


def verify_front_page_set(site, creds, result) -> dict:
    r = safe_request(
        "GET", f"https://{creds['fqdn']}/wp-json/wp/v2/settings",
        auth=("kai", creds["app_password"]), verify=False,
    )
    return {
        "verified": bool(r.ok and r.data and r.data.get("show_on_front") == "page"),
        "evidence": {"show_on_front": r.data.get("show_on_front") if r.data else None},
    }


def verify_page_published(site, creds, result) -> dict:
    page_id = result.get("data", {}).get("id")
    r = safe_request(
        "GET", f"https://{creds['fqdn']}/wp-json/wp/v2/pages/{page_id}",
        auth=("kai", creds["app_password"]), verify=False,
    )
    return {
        "verified": bool(r.ok and r.data and r.data.get("status") == "publish"),
        "evidence": {"status": r.data.get("status") if r.data else None},
    }


def verify_live_marker(site, creds, result) -> dict:
    marker = result.get("data", {}).get("marker")
    r = safe_request("GET", f"https://{creds['fqdn']}/", verify=False)
    found = bool(marker and marker in (r.body_preview or ""))
    if not found and r.is_cloudflare_challenge:
        r2 = safe_request(
            "GET", f"https://{creds['fqdn']}/",
            headers={"Host": site}, verify=False,
        )
        found = bool(marker and marker in (r2.body_preview or ""))
    return {
        "verified": found,
        "evidence": {"marker": marker, "cloudflare_blocked": r.is_cloudflare_challenge},
    }
