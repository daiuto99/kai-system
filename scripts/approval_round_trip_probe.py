#!/usr/bin/env python3
"""KAI-1112 · [M-R1] Approvals round-trip synthetic probe — prove the approval RETURN path by USING it.

The failure this guards against: KAI can *send* an approval prompt (buzz_approve posts a
gate to Leo's kai-approvals channel) but the RETURN leg — Leo's tap resolving the gate
through POST /council/gate/{id}/resolve — can silently rot. That return path had ZERO
resolved lines from 2026-08-12 onward and was otherwise unexercised: nothing re-derived
its health from an observed round-trip. This probe does exactly that, weekly:

  once a week:  create a synthetic low-stakes gate (gate_type='synthetic_probe')
                through the SAME nginx ingress + basic-auth that Leo's approvals use
                (localhost:3001/council/council/gate  ->  kai-web  ->  kai-council-api)
                resolve it through the IDENTICAL endpoint Leo's tap fires
                (buzz_approve._council_resolve -> POST /council/gate/{id}/resolve)
                assert the gate lands status='resolved' AND an audit.json was persisted
                write a heartbeat file (the EXIT DRILL asserts this file is fresh)
                on any break -> notify() pages Leo (provenance='real', audience='personal')

Design pins (why this is faithful and not just another green check):
  • The synthetic gate uses a first-class gate_type='synthetic_probe' that kai-council-api
    treats as a NO-OP in _process_gate: it never reaches pending_leo, so the buzz_approve
    poller (which prompts only pending_leo gates) NEVER surfaces it to Leo's channel. The
    probe drives the resolve endpoint directly. Leo is never prompted by a probe run.
  • resolve_gate accepts a gate in 'processing' with the exact same handler body it runs
    for a 'pending_leo' gate Leo taps — so the RETURN path exercised is byte-identical to
    a real resolution (persist audit + dashboard FYI). Callback + taste-learning are
    skipped for synthetic gates on the council side (no orchestrator, no human decision).
  • The probe TRAFFIC is synthetic but the outage PAGE is provenance='real'. The notify
    gateway suppresses provenance='synthetic' — a synthetic-stamped alarm would never
    reach Leo's phone and would silently re-create the very blind spot this closes.
  • Read-only w.r.t. real work: it creates + resolves its OWN gate and writes its own
    heartbeat. It never touches a real gate, never restarts/mutates a service, and prunes
    its own stale gate dirs so the audit store does not accumulate 52 dirs/year.

Runs on the worker HOST via cron (same pattern as advisor_dm_probe.py / fleet_heartbeat.py).
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

# Same nginx ingress + basic-auth realm Leo's approvals traverse (buzz_approve reaches
# kai-web as http://kai-web/council/council; from the host that is localhost:3001). nginx
# strips ONE /council/ prefix, so gate routes need the doubled prefix.
COUNCIL_BASE = "http://localhost:3001/council/council"
WEB_USER = "kai"                                   # BUZZ_WEB_USER in docker-compose.yml
WEB_PW_FILE = ROOT / "secrets" / "kai_web_password.txt"
REQUEST_TIMEOUT_SEC = 30
SCHEMA = "kai.approval_probe.v1"
GATE_ID_PREFIX = "synthetic-approval-probe-"
STATE_FILENAME = "_approval_probe_state.json"
# Prune the probe's own resolved gate dirs older than this (keep recent ones as evidence).
_PRUNE_AGE_DAYS = 30

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


def _gates_dir() -> Path:
    return _vault_dir() / "00_System" / "gates"


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
        return d if isinstance(d, dict) else {}  # a list/scalar must not reach prior.get(...)
    except Exception:
        return {}


def _write_state(state: dict) -> None:
    """Atomic write so a reader never sees a half-written heartbeat."""
    p = _state_path()
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(p)


def _auth_header() -> str:
    pw = WEB_PW_FILE.read_text().strip()
    return "Basic " + base64.b64encode(f"{WEB_USER}:{pw}".encode()).decode()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow redirects on a credentialed request: urllib would re-send the basic
    Authorization header to the redirect target, which under a misconfigured/hostile nginx
    could disclose the web password to another origin (L18). A 3xx surfaces as HTTPError
    and is handled as a probe failure — the gate API never legitimately redirects."""
    def redirect_request(self, *args, **kwargs):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _post(path: str, body: dict) -> tuple[int, dict]:
    data = json.dumps(body).encode()
    req = urllib.request.Request(f"{COUNCIL_BASE}{path}", data=data, method="POST", headers={
        "Authorization": _auth_header(),
        "Content-Type": "application/json",
    })
    with _OPENER.open(req, timeout=REQUEST_TIMEOUT_SEC) as r:
        status = getattr(r, "status", None) or getattr(r, "code", 200)
        return status, json.loads(r.read())


def _get(path: str) -> tuple[int, dict]:
    req = urllib.request.Request(f"{COUNCIL_BASE}{path}", headers={"Authorization": _auth_header()})
    with _OPENER.open(req, timeout=REQUEST_TIMEOUT_SEC) as r:
        status = getattr(r, "status", None) or getattr(r, "code", 200)
        return status, json.loads(r.read())


def probe_once() -> dict:
    """Create + resolve one synthetic gate through the real approval path. Never raises."""
    gate_id = f"{GATE_ID_PREFIX}{_now().strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    started = time.time()
    try:
        # 1. Create — same ingress a real gate enters through.
        st, resp = _post("/gate", {
            "gate_id": gate_id,
            "gate_type": "synthetic_probe",
            "brief": {"kind": "approval_round_trip_probe", "note": "automated weekly health check — not a real approval"},
            "callback_url": "http://localhost/synthetic-probe-no-callback",
        })
        if st != 200 or resp.get("status") != "accepted":
            return _fail(gate_id, started, f"create rejected: status={st} resp={json.dumps(resp)[:200]}")

        # 2. Resolve — the IDENTICAL endpoint buzz_approve._council_resolve calls on Leo's tap.
        st, resp = _post(f"/gate/{gate_id}/resolve", {
            "approved": True,
            "notes": "synthetic round-trip probe (KAI-1112)",
            "resolver": "approval_probe",
        })
        if st != 200 or resp.get("status") != "resolved" or resp.get("approved") is not True:
            return _fail(gate_id, started, f"resolve did not land: status={st} resp={json.dumps(resp)[:200]}")

        # 3. Read the gate back — the return leg must be observable, not just claimed.
        st, state = _get(f"/gate/{gate_id}/state")
        if st != 200 or state.get("status") != "resolved":
            return _fail(gate_id, started, f"state readback not resolved: status={st} state={json.dumps(state)[:200]}")
        res = state.get("resolution") or {}
        if res.get("approved") is not True or res.get("advisor") != "approval_probe":
            return _fail(gate_id, started, f"resolution content wrong: {json.dumps(res)[:200]}")

        # 4. Assert the audit record was persisted to the vault (proof the resolve handler
        #    ran its full return-path body, not just flipped an in-memory flag).
        audit = _gates_dir() / gate_id / "audit.json"
        if not audit.exists():
            return _fail(gate_id, started, f"audit.json not persisted at {audit}")
        try:
            rec = json.loads(audit.read_text())
            if (rec.get("resolution") or {}).get("approved") is not True:
                return _fail(gate_id, started, f"audit.json resolution not approved: {json.dumps(rec)[:200]}")
        except Exception as e:
            return _fail(gate_id, started, f"audit.json unreadable: {type(e).__name__}: {e}")

        latency_ms = int((time.time() - started) * 1000)
        return {"gate_id": gate_id, "ok": True, "latency_ms": latency_ms, "reason": "healthy round-trip"}
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode()[:200]
        except Exception:
            pass
        return _fail(gate_id, started, f"HTTP {e.code}: {detail}")
    except Exception as e:
        return _fail(gate_id, started, f"round-trip error: {type(e).__name__}: {e}")


def _fail(gate_id: str, started: float, reason: str) -> dict:
    return {"gate_id": gate_id, "ok": False, "latency_ms": int((time.time() - started) * 1000), "reason": reason}


def _prune_old_gates() -> int:
    """Remove this probe's own resolved gate dirs older than _PRUNE_AGE_DAYS. Best-effort;
    never raises into the probe result. Keeps the audit store from growing 52 dirs/year."""
    removed = 0
    cutoff = _now().timestamp() - _PRUNE_AGE_DAYS * 86400
    try:
        import shutil
        for d in _gates_dir().iterdir():
            if not d.is_dir() or not d.name.startswith(GATE_ID_PREFIX):
                continue
            try:
                if d.stat().st_mtime < cutoff:
                    shutil.rmtree(d)
                    removed += 1
            except Exception:
                continue
    except Exception:
        pass
    return removed


def _page_leo(result: dict, consecutive: int, dry_run: bool) -> tuple[str, bool]:
    """Page Leo about a REAL approval-path outage. Returns (log_line, delivered). provenance
    is 'real' — do NOT stamp it synthetic or the gateway suppresses it (the blind-spot trap).
    `delivered` reflects notify()'s actual result, so a send that did not land is never
    recorded as a successful page."""
    title = f"Approval path DOWN — {consecutive} consecutive fail"
    body = ("Synthetic approvals round-trip probe failed (KAI-1112).\n"
            f"gate: {result['gate_id']}\n"
            f"reason: {result['reason']}\n"
            f"latency: {result['latency_ms']} ms\n"
            "This is the leg where Leo's approval taps resolve gates — if it is dead, a tap "
            "may not land and a gate can hang. Check kai-council-api + kai-web.")
    if dry_run:
        return f"[dry-run] would page: {title}", False
    try:
        import os
        # notify_gateway's audit log defaults to the CONTAINER vault path (/vault), unwritable
        # from this host-cron context — point it at the host vault (same physical file as the
        # container bind mount) before the module reads _LOG_PATH at import.
        os.environ.setdefault("KAI_NOTIFY_LOG", str(_vault_dir() / "00_System" / "notify_log.jsonl"))
        from notify_gateway import notify, Event
        bucket = _now().strftime("%Y-%m-%dT%H")  # hour-bucketed dedup: at most one page/hour
        res = notify(Event(
            source="approval_round_trip_probe",
            kind="alert",
            title=title,
            body=body,
            audience="personal",   # personal-consequence -> reaches Leo's Telegram
            actionable=True,
            provenance="real",     # explicit: the outage is real even though the probe gate is synthetic
            dedup_key=f"approval_probe_down:{bucket}",
        ))
        delivered = bool(getattr(res, "delivered", False))
        prefix = "paged" if delivered else "PAGE NOT DELIVERED"
        return f"{prefix}: decision={res.decision} dest={res.destination} delivered={delivered}", delivered
    except Exception as e:
        return f"PAGE FAILED: {type(e).__name__}: {e}", False


def main(argv: list[str]) -> int:
    dry_run = "--dry-run" in argv
    now = _now()

    result = probe_once()
    pruned = _prune_old_gates()
    prior = _load_prior()
    consecutive = 0 if result["ok"] else _int(prior.get("consecutive_failures", 0)) + 1

    page_line = None
    paged = False
    if not result["ok"]:
        page_line, paged = _page_leo(result, consecutive, dry_run)

    state = {
        "schema": SCHEMA,
        "last_probe": _iso(now),
        "gate_id": result["gate_id"],
        "ok": result["ok"],
        "reason": result["reason"],
        "latency_ms": result["latency_ms"],
        "consecutive_failures": consecutive,
        "last_ok": _iso(now) if result["ok"] else prior.get("last_ok"),
        "round_trips_ok_total": _int(prior.get("round_trips_ok_total", 0)) + (1 if result["ok"] else 0),
        "pruned_stale_gates": pruned,
        "paged": paged,  # reflects actual notify() delivery, not merely "notify returned"
        "dry_run": dry_run,
    }
    # A monitor that cannot record its own heartbeat is going DARK — the freshness check
    # downstream will read stale and (worse) an otherwise-green run would exit 0 and hide it.
    # Treat a heartbeat-write failure as a probe failure: page once (best-effort) and exit
    # non-zero so it is never a silent false-green.
    heartbeat_ok = True
    try:
        _write_state(state)
    except Exception as e:
        heartbeat_ok = False
        print(f"[{_iso(now)}] WARN heartbeat write failed: {e}", flush=True)
        if not dry_run and (page_line is None):  # don't double-page an already-failing run
            hb_line, _ = _page_leo(
                {"gate_id": result["gate_id"], "reason": f"heartbeat write failed: {e}",
                 "latency_ms": result["latency_ms"]}, consecutive, dry_run)
            page_line = hb_line

    status = "OK" if (result["ok"] and heartbeat_ok) else "FAIL"
    line = f"[{_iso(now)}] {status} gate={result['gate_id']} {result['latency_ms']}ms — {result['reason']}"
    if pruned:
        line += f" | pruned {pruned} stale gate dir(s)"
    if page_line:
        line += f" | {page_line}"
    print(line, flush=True)
    return 0 if (result["ok"] and heartbeat_ok) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
