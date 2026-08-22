#!/usr/bin/env python3
"""KAI-1113 · [M-R1] WP governed-pipeline synthetic draft probe — prove the drafts-only
governed WordPress build path by USING it, weekly.

The failure this guards against: the KAI-20 governed WP pipeline (dev gate + creative gate
+ WP write chokepoint + drafts-only enforcement) was proven ONCE end-to-end (the71c draft
#23, 2026-08-14) and then left unexercised. It has silently rotted before — the
edit_page_draft path could NEVER write for an unknown span because the module was absent
from wp_write_guard.CANONICAL_CALLERS (bug 66800833, fixed today e4a75ea). Nothing
re-derived the pipeline's health from an observed draft. This probe does exactly that:

  once a week:  launch the REAL wordpress.build_page_draft workflow through the SAME
                authed worker-api launcher the dashboard BUILD button uses
                (localhost:8001/wordpress/{site}/build-draft), with probe=True
                the workflow runs its real steps: load_site_config, probe_credentials,
                dev_gate, creative_gate, the WP write chokepoint, create_page (status=draft)
                assert the draft was written (find it by a unique token, read it back)
                trash the draft (force) and confirm it is gone — leave zero residue
                write a heartbeat file (the EXIT DRILL asserts this file is fresh)
                on any break -> notify() pages Leo (provenance='real', audience='personal')

Design pins (why this is faithful and not just another green check):
  • Scope — what THIS probe uniquely proves: the WP-specific rot surface — creds load,
    the write chokepoint accepts the canonical workflow caller, the drafts-only workflow
    orchestration reaches the write step through both gates, the page is written and reads
    back, and cleanup works. Advisor-review liveness is already covered by advisor_dm_probe
    (*/15). Gate human-resolve is covered by the approvals round-trip probe (KAI-1112).
  • The two gates auto-resolve WITHOUT the LLM review chain and WITHOUT ever reaching
    pending_leo: build-draft launched with probe=True stamps brief.probe=True, and
    kai-council-api auto-approves probe-flagged dev_gate/creative_gate (drafts-only only —
    never a publish/homepage/hostops gate). So Leo is never prompted and the probe is not
    hostage to advisor/LLM flakiness (kai-mini offline would otherwise false-alarm here).
  • Drafts-only by construction: build_page_draft has no publish/homepage steps and the
    probe never calls a publish endpoint. The synthetic page is status=draft, then trashed.
  • The probe TRAFFIC is synthetic but the outage PAGE is provenance='real'. The notify
    gateway suppresses provenance='synthetic' — a synthetic-stamped alarm would never reach
    Leo's phone and would silently re-create the very blind spot this closes. Alert text is
    plain (no Markdown) — KAI-1134: Telegram parse_mode=Markdown with unescaped content 400s
    and is dropped.
  • Leaves no residue: it creates + trashes its OWN draft (force delete). It never touches a
    real page, never publishes, never mutates a service.

Runs on the worker HOST via cron (same pattern as approval_round_trip_probe.py).
"""
from __future__ import annotations

import base64
import json
import sys
import time
import urllib.request
import urllib.error
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "shared"))  # /shared convention (notify_gateway lives here)

# The same authed worker-api the dashboard BUILD button reaches. From the host that is
# localhost:8001 behind BasicAuthMiddleware (the same realm /session/brief uses).
# The worker-api control plane is published on the worker Tailscale IP only (not loopback),
# the same canonical IP as web :3001. localhost:8001 is connection-refused from host cron.
API_BASE = "http://100.78.94.80:8001"
AUTH_FILE = ROOT / "secrets" / "kai_worker_auth.txt"          # "user:pass"
WP_WRITE_TOKEN_FILE = ROOT / "secrets" / "wp_write_token.txt"  # workflow-only WP write token
REQUEST_TIMEOUT_SEC = 30
JOB_POLL_TIMEOUT_SEC = 180
JOB_POLL_INTERVAL_SEC = 3
SCHEMA = "kai.wp_pipeline_probe.v1"
STATE_FILENAME = "_wp_pipeline_probe_state.json"

# The stable test property — the71c is where the governed build_page_draft path was proven
# end-to-end (draft #23, 2026-08-14) and it carries a BUILD_PROFILE (governed). One site only.
SITE = "the71c"
PLANE_ISSUE = "KAI-1113"

_TERMINAL_JOB_STATES = {"succeeded", "failed", "failed_permanent", "cancelled", "orphaned"}
_VAULT_CANDIDATES = (Path("/home/leo/vault"), Path("/vault"))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _vault_dir() -> Path:
    for c in _VAULT_CANDIDATES:
        if c.exists():
            return c
    return _VAULT_CANDIDATES[0]


def _state_path() -> Path:
    return _vault_dir() / STATE_FILENAME


def _int(v, default: int = 0) -> int:
    """Coerce a prior-state counter to int; never raise on a corrupt/non-numeric value
    (a crash here would happen BEFORE the failure page and swallow a real outage)."""
    try:
        return int(v)
    except Exception:
        return default


def _load_prior() -> dict:
    try:
        d = json.loads(_state_path().read_text())
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _write_state(state: dict) -> None:
    """Atomic write so a reader never sees a half-written heartbeat."""
    p = _state_path()
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(p)


def _auth_header() -> str:
    return "Basic " + base64.b64encode(AUTH_FILE.read_text().strip().encode()).decode()


def _wp_write_token() -> str:
    return WP_WRITE_TOKEN_FILE.read_text().strip()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow redirects on a credentialed request: urllib would re-send the basic
    Authorization header (and the WP write token) to the redirect target, which under a
    misconfigured/hostile ingress could disclose them to another origin (L18). A 3xx
    surfaces as HTTPError and is handled as a probe failure — the API never legitimately
    redirects."""
    def redirect_request(self, *args, **kwargs):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _req(method: str, path: str, body: dict | None = None, extra_headers: dict | None = None) -> tuple[int, dict]:
    headers = {"Authorization": _auth_header()}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(f"{API_BASE}{path}", data=data, method=method, headers=headers)
    with _OPENER.open(req, timeout=REQUEST_TIMEOUT_SEC) as r:
        status = getattr(r, "status", None) or getattr(r, "code", 200)
        raw = r.read()
        try:
            return status, json.loads(raw)
        except Exception:
            return status, {"_non_json": raw[:200].decode(errors="replace")}


def probe_once() -> dict:
    """Drive one synthetic draft through the governed pipeline, read it back, trash it.
    Never raises."""
    token = uuid.uuid4().hex[:12]
    ts = _now().strftime("%Y%m%dT%H%M%SZ")
    title = f"[KAI-PROBE] governed-pipeline health {ts} {token}"
    content = (f"<p>Synthetic KAI-1113 governed-pipeline probe draft — automatically created "
               f"and trashed by scripts/wp_governed_pipeline_probe.py. token={token}</p>")
    started = time.time()
    draft_id = None
    try:
        # 1. Launch the REAL governed build through the authed worker-api launcher.
        st, resp = _req("POST", f"/wordpress/{SITE}/build-draft", {
            "plane_issue": PLANE_ISSUE,
            "page_title": title,
            "page_content": content,
            "probe": True,
        })
        if st != 200 or not resp.get("job_id"):
            return _fail(token, draft_id, started, f"launch rejected: status={st} resp={json.dumps(resp)[:200]}")
        job_id = resp["job_id"]

        # 2. Poll the job to a terminal state through the same dashboard status surface.
        deadline = time.time() + JOB_POLL_TIMEOUT_SEC
        job_status, draft_written, last = None, False, {}
        while time.time() < deadline:
            st, js = _req("GET", f"/wordpress/build-draft/{job_id}")
            if st == 200:
                last = js
                job_status = js.get("status")
                draft_written = bool(js.get("draft_written"))
                if job_status in _TERMINAL_JOB_STATES:
                    break
            time.sleep(JOB_POLL_INTERVAL_SEC)
        if job_status != "succeeded":
            return _fail(token, draft_id, started,
                         f"job {job_id} did not succeed: status={job_status} awaiting={last.get('awaiting_gate')} steps={json.dumps(last.get('steps'))[:200]}")
        if not draft_written:
            return _fail(token, draft_id, started, f"job {job_id} succeeded but create_page_draft did not write")

        # 3. Find the created draft by its unique token (status endpoint redacts the id).
        st, listing = _req("GET", f"/wordpress/{SITE}/posts?page_type=pages&status=draft&count=50")
        if st != 200 or not isinstance(listing.get("items"), list):
            return _fail(token, draft_id, started, f"draft listing failed: status={st} resp={json.dumps(listing)[:200]}")
        match = next((it for it in listing["items"] if token in (it.get("title") or "")), None)
        if not match:
            return _fail(token, draft_id, started, "draft not found after job success (write did not land)")
        draft_id = match.get("id")

        # 4. Read the draft back — the write must be observable, not just claimed.
        st, page = _req("GET", f"/wordpress/{SITE}/posts/{draft_id}?post_type=pages")
        if st != 200 or page.get("error"):
            return _fail(token, draft_id, started, f"readback failed: status={st} resp={json.dumps(page)[:200]}")
        if token not in (page.get("title") or ""):
            return _fail(token, draft_id, started, f"readback title mismatch: {json.dumps(page)[:200]}")
        if page.get("status") != "draft":
            return _fail(token, draft_id, started, f"readback status not draft: {page.get('status')}")

        # 5. Clean up — trash + permanently remove so no residue accumulates (52/year).
        st, dele = _req("DELETE", f"/wordpress/{SITE}/posts/{draft_id}?post_type=pages&force=true",
                        body={"operator": "wp_probe", "reason": "KAI-1113 synthetic probe cleanup"},
                        extra_headers={"X-Wp-Write-Token": _wp_write_token()})
        if st != 200 or not dele.get("ok"):
            return _fail(token, draft_id, started, f"cleanup delete failed: status={st} resp={json.dumps(dele)[:200]}")

        # 6. Confirm it is gone — re-list drafts, the token must be absent.
        st, listing2 = _req("GET", f"/wordpress/{SITE}/posts?page_type=pages&status=draft&count=50")
        if st == 200 and isinstance(listing2.get("items"), list):
            if any(token in (it.get("title") or "") for it in listing2["items"]):
                return _fail(token, draft_id, started, f"cleanup did not remove draft {draft_id} (still listed)")

        latency_ms = int((time.time() - started) * 1000)
        return {"token": token, "draft_id": draft_id, "ok": True, "latency_ms": latency_ms,
                "reason": f"healthy governed draft round-trip (draft {draft_id} written, read back, trashed)"}
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode()[:200]
        except Exception:
            pass
        return _fail(token, draft_id, started, f"HTTP {e.code}: {detail}")
    except Exception as e:
        return _fail(token, draft_id, started, f"pipeline error: {type(e).__name__}: {e}")


def _fail(token: str, draft_id, started: float, reason: str) -> dict:
    return {"token": token, "draft_id": draft_id, "ok": False,
            "latency_ms": int((time.time() - started) * 1000), "reason": reason}


def _page_leo(result: dict, consecutive: int, dry_run: bool) -> tuple[str, bool]:
    """Page Leo about a REAL governed-WP-pipeline outage. provenance='real' — do NOT stamp it
    synthetic or the gateway suppresses it. `delivered` reflects notify()'s actual result, so
    a send that did not land is never recorded as a successful page. Plain text (KAI-1134)."""
    title = f"WP governed pipeline DOWN — {consecutive} consecutive fail"
    body = ("Synthetic WP governed-pipeline probe failed (KAI-1113).\n"
            f"site: {SITE}\n"
            f"draft_id: {result.get('draft_id')}\n"
            f"reason: {result['reason']}\n"
            f"latency: {result['latency_ms']} ms\n"
            "This is the drafts-only governed build path (dev+creative gates + WP write "
            "chokepoint). If it is dead, KAI cannot produce WP drafts. If draft_id is set, "
            "a synthetic draft may have leaked cleanup — check the property's draft list. "
            "Check kai-worker-api + kai-orchestrator + kai-council-api.")
    if dry_run:
        return f"[dry-run] would page: {title}", False
    try:
        import os
        # notify_gateway defaults its audit log to the CONTAINER vault path (/vault),
        # unwritable from host cron — point it at the host vault before import.
        os.environ.setdefault("KAI_NOTIFY_LOG", str(_vault_dir() / "00_System" / "notify_log.jsonl"))
        os.environ.setdefault("KAI_NOTIFY_DEDUP", str(_vault_dir() / "00_System" / "notify_dedup.json"))
        from notify_gateway import notify, Event
        bucket = _now().strftime("%Y-%m-%dT%H")  # hour-bucketed dedup: at most one page/hour
        res = notify(Event(
            source="wp_governed_pipeline_probe",
            kind="alert",
            title=title,
            body=body,
            audience="personal",
            actionable=True,
            provenance="real",
            dedup_key=f"wp_pipeline_probe_down:{bucket}",
        ))
        delivered = bool(getattr(res, "delivered", False))
        prefix = "paged" if delivered else "PAGE NOT DELIVERED"
        return f"{prefix}: decision={res.decision} dest={res.destination} delivered={delivered}", delivered
    except Exception as e:
        return f"PAGE FAILED: {type(e).__name__}: {e}", False


def main(argv: list[str]) -> int:
    dry_run = "--dry-run" in argv
    now = _now()
    today = now.strftime("%Y-%m-%d")

    result = probe_once()
    prior = _load_prior()

    # Weekly green streak — increment at most once per UTC date so repeated same-day (e.g.
    # in-session test) runs cannot fake the "2 consecutive weekly" exit proof; the real
    # accrual comes from the weekly cron tick. Reset to 0 on any failure.
    prior_streak = _int(prior.get("green_streak", 0))
    if not result["ok"]:
        green_streak = 0
    elif prior.get("last_green_date") == today:
        green_streak = max(prior_streak, 1)  # already counted today; don't double-count
    else:
        green_streak = prior_streak + 1
    consecutive = 0 if result["ok"] else _int(prior.get("consecutive_failures", 0)) + 1
    exit_proof_met = green_streak >= 2

    page_line, paged = (None, False)
    if not result["ok"]:
        page_line, paged = _page_leo(result, consecutive, dry_run)

    state = {
        "schema": SCHEMA,
        "last_probe": _iso(now),
        "site": SITE,
        "ok": result["ok"],
        "reason": result["reason"],
        "token": result.get("token"),
        "draft_id": result.get("draft_id"),
        "latency_ms": result["latency_ms"],
        "green_streak": green_streak,
        "last_green_date": today if result["ok"] else prior.get("last_green_date"),
        "exit_proof_met": exit_proof_met,
        "consecutive_failures": consecutive,
        "last_ok": _iso(now) if result["ok"] else prior.get("last_ok"),
        "runs_ok_total": _int(prior.get("runs_ok_total", 0)) + (1 if result["ok"] else 0),
        "paged": paged,
        "dry_run": dry_run,
    }
    # A monitor that cannot record its own heartbeat is going DARK. Treat a heartbeat-write
    # failure as a probe failure: page once (best-effort) and exit non-zero.
    heartbeat_ok = True
    try:
        _write_state(state)
    except Exception as e:
        heartbeat_ok = False
        print(f"[{_iso(now)}] WARN heartbeat write failed: {e}", flush=True)
        if not dry_run and (page_line is None):
            hb_line, _ = _page_leo(
                {"draft_id": result.get("draft_id"), "reason": f"heartbeat write failed: {e}",
                 "latency_ms": result["latency_ms"]}, consecutive, dry_run)
            page_line = hb_line

    status = "OK" if (result["ok"] and heartbeat_ok) else "FAIL"
    line = (f"[{_iso(now)}] {status} site={SITE} draft={result.get('draft_id')} "
            f"streak={green_streak} exit_proof={'MET' if exit_proof_met else 'pending'} "
            f"{result['latency_ms']}ms — {result['reason']}")
    if page_line:
        line += f" | {page_line}"
    print(line, flush=True)
    return 0 if (result["ok"] and heartbeat_ok) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
