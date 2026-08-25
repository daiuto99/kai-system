#!/usr/bin/env python3
"""wp_build_draft.py — CLI launcher for the governed WP drafts-only build (AR-1 gap4).

Kills the docker-exec + hand-JSON + manual-gate-lookup dance: launches
wordpress.build_page_draft over the authed worker-api, polls for the pending
gate, and (optionally) resolves it — all from one command.

Usage:
  python3 scripts/wp_build_draft.py launch --site the71c --title "Home" [--content-file page.html] [--probe]
  python3 scripts/wp_build_draft.py status <job_id>
  python3 scripts/wp_build_draft.py resolve <gate_id> --approve [--notes "..."] [--advisor leo]

Auth + host resolve from the same secrets the rest of the fleet uses.
"""
import argparse
import base64
import json
import sys
import urllib.request
from pathlib import Path

_AUTH_CANDIDATES = [
    Path.home() / ".kai/secrets/kai_worker_auth.txt",
    Path("/home/leo/kai-system/secrets/kai_worker_auth.txt"),
    Path(__file__).resolve().parent.parent / "secrets/kai_worker_auth.txt",
]
# On the worker use localhost; from the Mac use the Tailscale IP.
_BASE = "http://localhost:8001" if Path("/home/leo/kai-system").exists() else "http://100.78.94.80:8001"


def _auth_header() -> str:
    for c in _AUTH_CANDIDATES:
        if c.exists():
            return "Basic " + base64.b64encode(c.read_text().strip().encode()).decode()
    sys.exit("no worker auth secret found")


def _call(method: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(_BASE + path, data=data, method=method,
                                 headers={"Authorization": _auth_header(),
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        sys.exit(f"HTTP {e.code}: {e.read().decode()[:300]}")


def cmd_launch(a):
    content = Path(a.content_file).read_text() if a.content_file else None
    body = {"site": a.site, "page_title": a.title, "probe": a.probe}
    if content is not None:
        body["page_content"] = content
    if a.property:
        body["property"] = a.property
    if a.brief_path:
        body["brief_path"] = a.brief_path
    res = _call("POST", "/orchestrator/wordpress/build_page_draft", body)
    job_id = res.get("workflow_id")
    print(f"launched job {job_id} ({res.get('status')})")
    # surface the first pending gate for convenience
    status = _call("GET", f"/orchestrator/jobs/{job_id}")
    gate = status.get("pending_gate")
    if gate:
        print(f"pending gate: {gate['gate_id']} (step={gate['step']})")
        print(f"resolve with: wp_build_draft.py resolve {gate['gate_id']} --approve")
    else:
        print("no gate open yet — re-run `status <job_id>` shortly")


def cmd_status(a):
    print(json.dumps(_call("GET", f"/orchestrator/jobs/{a.job_id}"), indent=2))


def cmd_resolve(a):
    body = {"approved": a.approve, "advisor": a.advisor, "notes": a.notes}
    print(json.dumps(_call("POST", f"/orchestrator/gates/{a.gate_id}/resolve", body), indent=2))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    pl = sub.add_parser("launch")
    pl.add_argument("--site", required=True)
    pl.add_argument("--title", required=True)
    pl.add_argument("--content-file", dest="content_file")
    pl.add_argument("--property")
    pl.add_argument("--brief-path", dest="brief_path")
    pl.add_argument("--probe", action="store_true")
    pl.set_defaults(func=cmd_launch)

    ps = sub.add_parser("status")
    ps.add_argument("job_id")
    ps.set_defaults(func=cmd_status)

    pr = sub.add_parser("resolve")
    pr.add_argument("gate_id")
    pr.add_argument("--approve", action="store_true")
    pr.add_argument("--advisor", default="leo")
    pr.add_argument("--notes", default="")
    pr.set_defaults(func=cmd_resolve)

    a = p.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
