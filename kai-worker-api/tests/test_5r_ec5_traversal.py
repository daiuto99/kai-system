"""
Exit Criterion #5 — traversal-payload 400s (revised test).

Finding from v1: Raw `../` in URL path is normalized by the HTTP stack BEFORE 
reaching the route handler — returns 404 (route not found). This means `safe_path` 
is never called via URL-path traversal.

v2 approach: Use URL-encoded dots (%2e%2e) which are decoded by the ASGI framework 
into `..` AFTER URL normalization, so the handler receives the traversal payload 
and `safe_path`/equivalent is exercised.

Route categorization:
 A1 - filesystem routes using config.safe_path (→ returns 400 on traversal)
 A2 - filesystem routes using advisors._safe_path (→ returns 403 on traversal)
 A3 - filesystem routes where param used in Path() directly (template routes)
 B  - non-filesystem routes (external API ID — traversal irrelevant)
"""
import httpx
import json
import sys

BASE = "http://localhost:8001"

def get_auth():
    auth_str = open("/tmp/kai_auth.txt").read().strip()
    user, pw = auth_str.split(":", 1)
    return (user, pw)

# URL-encoded ".." — reaches handler (not normalized away)
DOT_DOT = "%2e%2e"
TRAVERSAL_ENCODED = f"{DOT_DOT}%2f{DOT_DOT}%2f{DOT_DOT}%2fetc%2fpasswd"

ROUTES = [
    # A1 — config.safe_path() → None → HTTPException(400)
    ("GET",    f"/checkin/questions/{TRAVERSAL_ENCODED}",           "A1-safe_path-400"),
    ("GET",    f"/knowledge/decisions/{TRAVERSAL_ENCODED}",         "A1-safe_path-400"),
    # A2 — advisors._safe_path() → HTTPException(403) on escape
    ("GET",    f"/advisors/{TRAVERSAL_ENCODED}",                    "A2-advisors_safe_path-403"),
    ("PUT",    f"/advisors/{TRAVERSAL_ENCODED}",                    "A2-advisors_safe_path-403"),
    ("GET",    f"/advisors/{TRAVERSAL_ENCODED}/assets",             "A2-advisors_safe_path-403"),
    ("PUT",    f"/advisors/{TRAVERSAL_ENCODED}/assets",             "A2-advisors_safe_path-403"),
    ("GET",    f"/advisors/{TRAVERSAL_ENCODED}/team",               "A2-advisors_safe_path-403"),
    # A3 — template filename (Path constructed, no safe_path wrapper)
    ("GET",    f"/templates/v1/{TRAVERSAL_ENCODED}",                "A3-template-check"),
    # B — external API ID routes (traversal via URL is irrelevant; show they're non-filesystem)
    ("PATCH",  f"/parking-lot/{DOT_DOT}",                           "B-slug-lookup"),
    ("DELETE", f"/parking-lot/{DOT_DOT}",                           "B-slug-lookup"),
    ("POST",   f"/parking-lot/{DOT_DOT}/route",                     "B-slug-lookup"),
    ("PATCH",  f"/projects/{DOT_DOT}",                              "B-project-id"),
    ("DELETE", f"/projects/{DOT_DOT}",                              "B-project-id"),
    ("PATCH",  f"/contacts/{DOT_DOT}",                              "B-ext-api"),
    ("POST",   f"/habits/{DOT_DOT}/complete",                       "B-ext-api"),
    ("GET",    f"/intake/resources/{DOT_DOT}",                      "B-ext-api"),
    ("PATCH",  f"/plane/issues/{DOT_DOT}",                          "B-ext-api"),
    ("PATCH",  f"/tasks/{DOT_DOT}",                                 "B-ext-api"),
    ("DELETE", f"/tasks/{DOT_DOT}",                                 "B-ext-api"),
    ("PATCH",  f"/wordpress/sites/{DOT_DOT}",                       "B-ext-api"),
    ("GET",    f"/wordpress/{DOT_DOT}/posts",                       "B-ext-api"),
    ("DELETE", f"/workflows/{DOT_DOT}",                             "B-ext-api"),
    ("GET",    f"/council/advisor/{DOT_DOT}/recent_dms",            "B-ext-api"),
    ("POST",   f"/admin/redeploy/{DOT_DOT}",                        "B-ext-api"),
    ("POST",   f"/slack/channels/{DOT_DOT}/invite",                 "B-ext-api"),
    ("GET",    f"/org/{DOT_DOT}",                                   "B-ext-api"),
    ("GET",    f"/mode_lock/approval_status/{DOT_DOT}",             "B-ext-api"),
]

def run():
    auth = get_auth()
    results = []
    failures = []

    with httpx.Client(base_url=BASE, auth=auth, timeout=10) as client:
        for method, path, group in ROUTES:
            try:
                resp = client.request(method, path, json={})
                status = resp.status_code
            except Exception as e:
                status = f"ERR:{e}"

            # Pass criteria:
            # A1 → 400 required (safe_path returns None → HTTPException(400))
            # A2 → 403 required (advisors._safe_path raises HTTPException(403))
            # A3 → any 4xx (template route)
            # B  → any 4xx (non-filesystem; exact code unimportant for traversal safety)
            expected = None
            if group.startswith("A1"):
                expected = 400
            elif group.startswith("A2"):
                expected = 403
            elif group.startswith("A3"):
                expected = None  # any 4xx
            # B: any 4xx

            is_pass = False
            if expected is not None:
                is_pass = (status == expected)
            else:
                is_pass = isinstance(status, int) and 400 <= status < 500

            if group.startswith("A") and not is_pass:
                failures.append((method, path, group, status, expected))

            results.append({
                "group": group, "method": method, "path": path[:55],
                "status": status, "expected": expected, "pass": is_pass,
            })

    print("=" * 75)
    print("EC#5 v2 — Traversal-payload (%2e%2e) on EVERY worker route with path/ID")
    print("=" * 75)
    print(f"{'Group':<25} {'M':<8} {'Status':<8} {'Exp':<6} {'Pass':<6} Path")
    print("-" * 75)
    for r in results:
        p = "[P]" if r["pass"] else "[F]"
        exp = str(r["expected"]) if r["expected"] else "4xx"
        print(f"{r['group']:<25} {r['method']:<8} {str(r['status']):<8} {exp:<6} {p:<6} {r['path']}")

    print()
    a_total = sum(1 for r in results if r["group"].startswith("A"))
    a_pass = sum(1 for r in results if r["group"].startswith("A") and r["pass"])
    b_total = sum(1 for r in results if r["group"].startswith("B"))
    b_pass = sum(1 for r in results if r["group"].startswith("B") and r["pass"])
    print(f"Group A (filesystem routes): {a_pass}/{a_total} pass")
    print(f"Group B (external API ID):   {b_pass}/{b_total} return 4xx")

    if failures:
        print(f"\n[FAIL] Group A routes not returning expected code:")
        for method, path, group, status, expected in failures:
            print(f"  {method} {path} → got {status}, expected {expected} ({group})")
        sys.exit(1)
    else:
        print("\n[PASS] All filesystem routes reject traversal payload with correct 4xx")
        print("       (A1 → 400 via safe_path; A2 → 403 via advisors._safe_path)")
        print("\nNote: Raw ../ in URL → 404 (HTTP stack normalizes before handler; safe by design)")

if __name__ == "__main__":
    run()
