"""kai-orchestrator — FastAPI entrypoint."""
import json, logging, os, threading, time
from pathlib import Path
from fastapi import FastAPI, HTTPException
from db import init_db, get_conn
from engine import engine
from learning.aggregator import start_learning_loop, run_aggregation
from learning.proposer import generate_proposals

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s — %(message)s")
log = logging.getLogger(__name__)

WORKFLOW_REGISTRY = {}  # type -> Workflow class, populated at import time

_COUNCIL_API_URL       = os.environ.get("COUNCIL_API_URL",           "http://kai-council-api:8002")
_ORCHESTRATOR_HANDLES_WP = True  # permanently true — wp_state_machine deleted 2026-05-17

# Plane config (host-gateway for in-container access to host Plane service)
_PLANE_BASE    = os.environ.get("PLANE_API_URL", "http://host.docker.internal:8090/api/v1")
_PLANE_WS      = "sonicink"
_PLANE_BUG_PID = "9d36a2f8-f00e-4a68-9055-69c647ee1361"   # Bugs project
_PLANE_BACKLOG = "ba26fb93-8826-4763-8f84-908ed11af231"   # Backlog state

# Slack system channel for OVERRIDE acks (DevOps persona)
_SLACK_SYSTEM_CHANNEL = "devops"

app = FastAPI(title="kai-orchestrator")

# ── Helpers ───────────────────────────────────────────────────────────────────

def _slack_token() -> str:
    p = Path("/run/wp_secrets/slack_bot_token.txt")
    return p.read_text().strip() if p.exists() else os.environ.get("SLACK_BOT_TOKEN", "")

def _plane_token() -> str:
    p = Path("/run/wp_secrets/plane_api_token.txt")
    return p.read_text().strip() if p.exists() else os.environ.get("PLANE_API_TOKEN", "")

def _post_slack(text: str, channel: str = _SLACK_SYSTEM_CHANNEL) -> bool:
    token = _slack_token()
    if not token:
        log.warning("No Slack token — cannot post override ack")
        return False
    try:
        import httpx
        r = httpx.post(
            "https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {token}"},
            json={"channel": channel, "text": text,
                  "username": "kai-orchestrator",
                  "icon_emoji": ":warning:"},
            timeout=10,
        )
        data = r.json()
        if not data.get("ok"):
            log.warning("Slack postMessage error: %s", data.get("error"))
            return False
        return True
    except Exception as e:
        log.exception("_post_slack failed: %s", e)
        return False


def _notify_job_complete(job_id: str):
    """Post Slack notification for terminal job states only."""
    conn = get_conn()
    job = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    conn.close()
    if not job:
        return
    status = job["status"]
    if status not in ("succeeded", "failed_permanent", "cancelled"):
        return  # gate-paused or still running — do not notify yet
    inputs = json.loads(job["inputs"] or "{}")
    title = inputs.get("title", job["type"])
    emoji = ":white_check_mark:" if status == "succeeded" else ":x:"
    text = f"{emoji} *Job complete* — `{title}`\nStatus: `{status}` · ID: `{job_id[:8]}`"
    if job["error_summary"]:
        text += f"\nError: {job['error_summary'][:300]}"
    _post_slack(text)


def _run_and_notify(job_id: str, wf):
    """Run workflow.resume() and post completion notification when terminal."""
    try:
        wf.resume()
    except Exception:
        log.exception("Job %s crashed in resume()", job_id)
    _notify_job_complete(job_id)

def _create_plane_bug(title: str, description: str) -> str:
    """Create a BUG issue in Plane and return the issue id."""
    token = _plane_token()
    if not token:
        log.warning("No Plane token — cannot create bug")
        return ""
    try:
        import httpx
        r = httpx.post(
            f"{_PLANE_BASE}/workspaces/{_PLANE_WS}/projects/{_PLANE_BUG_PID}/issues/",
            headers={"X-API-Key": token, "Content-Type": "application/json"},
            json={"name": title, "description_html": f"<p>{description}</p>",
                  "state": _PLANE_BACKLOG, "priority": "medium"},
            timeout=10,
        )
        data = r.json()
        issue_id = data.get("id", "")
        if issue_id:
            log.info("Plane BUG created: %s — %s", issue_id, title)
        else:
            log.warning("Plane bug creation returned no id: %s", data)
        return issue_id
    except Exception as e:
        log.exception("_create_plane_bug failed: %s", e)
        return ""

# ── Workflow registry & startup ───────────────────────────────────────────────

def _register_workflows():
    from workflows.hello_world import HelloWorldWorkflow
    from workflows.wordpress_publish_homepage import PublishHomepageWorkflow
    from workflows.capability_chain import CapabilityChainWorkflow
    from workflows.devops_self_modify import DevopsSelfModifyWorkflow
    WORKFLOW_REGISTRY["hello_world"] = HelloWorldWorkflow
    WORKFLOW_REGISTRY["capability_chain"] = CapabilityChainWorkflow
    WORKFLOW_REGISTRY["wordpress.publish_homepage"] = PublishHomepageWorkflow
    WORKFLOW_REGISTRY["devops.self_modify"] = DevopsSelfModifyWorkflow

def _resume_interrupted_jobs():
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, type FROM jobs WHERE status IN ('running','queued')"
    ).fetchall()
    conn.close()
    if not rows:
        return
    log.info("Resuming %d interrupted job(s) from previous run", len(rows))
    for row in rows:
        wf_class = WORKFLOW_REGISTRY.get(row["type"])
        if not wf_class:
            log.warning("No workflow class for type %s — marking failed", row["type"])
            engine.transition("job", row["id"], "failed_permanent",
                              error="Workflow type not registered on restart")
            continue
        def _run(job_id=row["id"], cls=wf_class):
            try:
                wf = cls(job_id)
                _run_and_notify(job_id, wf)
            except Exception:
                log.exception("Resume failed for job %s", job_id)
        threading.Thread(target=_run, daemon=True).start()

def _start_gate_poller():
    import httpx

    def poll():
        while True:
            time.sleep(30)
            try:
                pending = engine.list_pending_gates()
                for gate in pending:
                    try:
                        r = httpx.get(
                            f"{_COUNCIL_API_URL}/council/gate/{gate['id']}/state",
                            timeout=5,
                        )
                        if r.status_code == 200:
                            data = r.json()
                            if data.get("status") == "resolved":
                                resolution = data.get("resolution", {})
                                info = engine.resolve_gate(gate["id"], resolution)
                                if info:
                                    _trigger_resume(info["job_id"], info["job_type"])
                    except Exception:
                        log.debug("Gate poll failed for %s", gate["id"])
            except Exception:
                log.exception("Gate poller error")

    threading.Thread(target=poll, daemon=True, name="gate-poller").start()
    log.info("Gate poller started (30s interval)")

def _trigger_resume(job_id: str, job_type: str):
    wf_class = WORKFLOW_REGISTRY.get(job_type)
    if not wf_class:
        log.warning("No workflow class for type %s — cannot resume job %s", job_type, job_id)
        return
    def _run():
        try:
            wf = wf_class(job_id)
            _run_and_notify(job_id, wf)
        except Exception:
            log.exception("Resume after gate resolution failed for job %s", job_id)
    threading.Thread(target=_run, daemon=True).start()

@app.on_event("startup")
def startup():
    init_db()
    _register_workflows()
    _resume_interrupted_jobs()
    _start_gate_poller()
    start_learning_loop()
    log.info("kai-orchestrator started — ORCHESTRATOR_HANDLES_WP=%s", _ORCHESTRATOR_HANDLES_WP)

# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "orchestrator_handles_wp": _ORCHESTRATOR_HANDLES_WP}

@app.get("/status")
def status():
    conn = get_conn()
    active_jobs = conn.execute(
        "SELECT COUNT(*) as n FROM jobs WHERE status IN ('running','queued')"
    ).fetchone()["n"]
    pending_gates = conn.execute(
        "SELECT COUNT(*) as n FROM gates WHERE status='pending'"
    ).fetchone()["n"]
    conn.close()
    return {
        "orchestrator_handles_wp": _ORCHESTRATOR_HANDLES_WP,
        "active_jobs": active_jobs,
        "pending_gates": pending_gates,
        "registered_workflows": list(WORKFLOW_REGISTRY.keys()),
    }

@app.post("/workflows/run")
def run_workflow(body: dict):
    workflow_type = body.get("type")
    inputs = body.get("inputs", {})

    # Feature flag: block WP workflows if flag is off
    if workflow_type and "wordpress" in workflow_type and not _ORCHESTRATOR_HANDLES_WP:
        return {"error": "ORCHESTRATOR_HANDLES_WP is disabled", "workflow_type": workflow_type}

    wf_class = WORKFLOW_REGISTRY.get(workflow_type)
    if not wf_class:
        return {"error": f"Unknown workflow type: {workflow_type}"}
    wf = wf_class.start(inputs)
    threading.Thread(target=_run_and_notify, args=(wf.job_id, wf), daemon=True).start()
    return {"job_id": wf.job_id, "status": "started"}

@app.get("/jobs/{job_id}")
def get_job(job_id: str):
    conn = get_conn()
    job = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    steps = conn.execute("SELECT * FROM steps WHERE job_id=? ORDER BY rowid",
                         (job_id,)).fetchall()
    conn.close()
    if not job:
        return {"error": "not found"}
    return {
        "job": dict(job),
        "steps": [dict(s) for s in steps],
    }

@app.post("/gates/{gate_id}/resolve")
def resolve_gate(gate_id: str, body: dict):
    info = engine.resolve_gate(gate_id, body)
    if info is None:
        return {"error": "gate not found or already resolved"}
    _trigger_resume(info["job_id"], info["job_type"])
    return {"ok": True, "job_id": info["job_id"]}

@app.get("/gates/{gate_id}/state")
def get_gate_state(gate_id: str):
    gate = engine.get_gate(gate_id)
    if gate is None:
        return {"error": "not found"}
    return {
        "gate_id":     gate_id,
        "status":      gate["status"],
        "gate_type":   gate["gate_type"],
        "resolution":  gate["resolution"],
        "opened_at":   gate["opened_at"],
        "resolved_at": gate["resolved_at"],
    }

@app.post("/jobs/{job_id}/steps/{step_id}/override")
def override_step(job_id: str, step_id: str, body: dict):
    """High-friction step override. Requires reason ≥ 50 chars. Posts Slack ack.
    If the same step_name has been overridden ≥5 times in 7 days, auto-files a Plane BUG.
    """
    reason   = body.get("reason", "")
    operator = body.get("operator", "leo")

    if len(reason) < 50:
        raise HTTPException(
            status_code=400,
            detail=f"Override reason must be ≥ 50 characters (got {len(reason)}). "
                   "Be deliberate — what broke, why manual override is safe.",
        )

    # Load step
    conn = get_conn()
    step = conn.execute("SELECT * FROM steps WHERE id=? AND job_id=?",
                        (step_id, job_id)).fetchone()
    conn.close()
    if not step:
        raise HTTPException(status_code=404, detail="Step not found")
    step = dict(step)
    step_name = step["name"]

    if step["status"] in ("succeeded", "cancelled"):
        raise HTTPException(status_code=409,
                            detail=f"Step is already {step['status']} — cannot override")

    # Transition step to succeeded with override verification
    engine.transition(
        "step", step_id, "succeeded",
        verification={
            "verified": True,
            "evidence": {
                "override": True,
                "operator": operator,
                "reason": reason,
            },
        },
        result={"override": True},
    )

    # Post Slack ack
    slack_text = (
        f":warning: *OVERRIDE* by {operator}\n"
        f"*Job:* `{job_id[:8]}` | *Step:* `{step_name}`\n"
        f"*Reason:* {reason}"
    )
    slack_ok = _post_slack(slack_text)

    # Pattern check — ≥5 overrides of same step in 7 days → Plane BUG
    override_count_before = engine.count_overrides_7d(step_name)
    bug_id = ""
    if override_count_before >= 4:  # 4 previous + this one = 5
        title = f"[AUTO-BUG] OVERRIDE pattern: {step_name} overridden {override_count_before + 1}x in 7 days"
        desc = (f"Step '{step_name}' has been overridden {override_count_before + 1} times in the past 7 days. "
                f"Latest override by {operator}: {reason}")
        bug_id = _create_plane_bug(title, desc)

    # Record override in DB
    engine.record_override(job_id, step_id, step_name, reason, operator,
                           slack_ack=slack_ok, bug_filed=bug_id)

    # Resume workflow
    conn = get_conn()
    job = conn.execute("SELECT type FROM jobs WHERE id=?", (job_id,)).fetchone()
    conn.close()
    if job:
        _trigger_resume(job_id, job["type"])

    return {
        "ok": True,
        "job_id": job_id,
        "step_name": step_name,
        "slack_ack": slack_ok,
        "bug_filed": bug_id or None,
    }

@app.post("/dispatch")
def dispatch(body: dict):
    intent  = body.get("intent", "")
    inputs  = body.get("inputs", {})

    wf_type = body.get("type") or _infer_workflow_type(intent)
    if not wf_type:
        return {"error": "Cannot infer workflow type from intent", "intent": intent}

    if wf_type.startswith("wordpress") and not _ORCHESTRATOR_HANDLES_WP:
        return {"error": "ORCHESTRATOR_HANDLES_WP is disabled", "workflow_type": wf_type}

    wf_class = WORKFLOW_REGISTRY.get(wf_type)
    if not wf_class:
        return {"error": f"Unknown workflow type: {wf_type}"}

    wf = wf_class.start(inputs)
    threading.Thread(target=_run_and_notify, args=(wf.job_id, wf), daemon=True).start()
    return {"job_id": wf.job_id, "workflow_type": wf_type, "status": "started"}

def _infer_workflow_type(intent: str) -> str:
    intent_lower = intent.lower()
    if "publish" in intent_lower and ("home" in intent_lower or "page" in intent_lower):
        return "wordpress.publish_homepage"
    return ""



@app.get("/jobs")
def list_jobs(limit: int = 20, status: str = ""):
    """List recent jobs, optionally filtered by status."""
    conn = get_conn()
    if status:
        rows = conn.execute(
            "SELECT id, type, status, inputs, error_summary, created_at, updated_at "
            "FROM jobs WHERE status=? ORDER BY created_at DESC LIMIT ?",
            (status, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, type, status, inputs, error_summary, created_at, updated_at "
            "FROM jobs ORDER BY created_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
    conn.close()
    jobs = []
    for r in rows:
        d = dict(r)
        try:
            inp = json.loads(d.get("inputs") or "{}")
            d["title"] = inp.get("title", d["type"])
        except Exception:
            d["title"] = d["type"]
        jobs.append(d)
    return {"jobs": jobs, "count": len(jobs)}


@app.post("/context/assemble")
def context_assemble(body: dict):
    """Memory Service §4.1 — the mandatory first step of every judgment/creative
    workflow and every council-api chat turn. Internal only (compose-network)."""
    import context_service
    key = body.get("key") or {}
    for f in ("advisor", "device"):
        if not key.get(f):
            raise HTTPException(status_code=400, detail=f"ConversationKey.{f} is required")
    package = context_service.assemble(
        key, body.get("message", ""),
        task_type=body.get("task_type"), project=body.get("project"),
        channel=body.get("channel"),
    )
    if package.get("error"):
        raise HTTPException(status_code=404, detail=package["error"])
    return {"ok": True, "package": package}


@app.get("/context/persona")
def context_persona(advisor: str, channel: str = None):
    """§3/§13 Tier 5 — lightweight persona-only load (no ConversationKey,
    no Tier 1-4 machinery). This is what persona.py::load_persona() calls now
    that persona.py has ceased to be an assembly point: workflow callers
    (graphs/nodes.py, graphs/bug_nodes.py) and the KAI-458 persona-assembly
    invariant (kai-scheduler, via kai-council-api's persona_check diagnostic)
    both just need standing-context text, not a conversation package."""
    import context_service
    result = context_service.tier5_standing_context(advisor, channel=channel)
    if result.get("error"):
        raise HTTPException(status_code=404, detail=result["error"])
    return {"ok": True, **result}


@app.post("/context/turn")
def context_turn(body: dict):
    """Memory Service §4.1 record_turn() — idempotent on turn_id."""
    import context_service
    key = body.get("key") or {}
    role = body.get("role")
    if role not in ("user", "assistant"):
        raise HTTPException(status_code=400, detail="role must be 'user' or 'assistant'")
    receipt = context_service.record_turn(
        key, role, body.get("content", ""),
        package_id=body.get("package_id"), turn_id=body.get("turn_id"),
    )
    return {"ok": True, "receipt": receipt}


@app.get("/context/conversation")
def context_get_conversation(advisor: str, device: str, place: str = None, thread: str = None, limit: int = 50):
    """Read API (§13 Phase 1) — clients render history from here, never from
    a client-maintained copy."""
    import context_service
    key = {"advisor": advisor, "device": device, "place": place, "thread": thread}
    return {"ok": True, **context_service.get_conversation(key, limit=limit)}


@app.get("/context/invariants/t1")
def context_invariant_t1(sample: int = 50):
    """inv_context_t1 (§8) — populated store + empty T1 on a recent package = CRITICAL.
    assemble() also checks and alerts live on every call; this is the pollable form."""
    import context_service
    return context_service.check_inv_context_t1(sample=sample)


@app.post("/context/cache-shape")
def context_cache_shape(body: dict):
    """§7/§8 Phase 2 — council-api reports cache shape for a package once the
    model response is known (stable_prefix_hash, breakpoint, cache token counts)."""
    import context_service
    package_id = body.get("package_id")
    if not package_id:
        raise HTTPException(status_code=400, detail="package_id required")
    return context_service.record_cache_shape(
        package_id,
        body.get("stable_prefix_hash", ""),
        body.get("cache_breakpoint_after", 0),
        cache_read_tokens=body.get("cache_read_tokens", 0),
        cache_creation_tokens=body.get("cache_creation_tokens", 0),
    )


@app.get("/context/invariants/cache")
def context_invariant_cache(hours: int = 24):
    """inv_context_cache (§8) — stable_prefix_hash churn >2x/24h per advisor = warning."""
    import context_service
    return context_service.check_inv_context_cache(hours=hours)


@app.post("/context/import-legacy")
def context_import_legacy(body: dict):
    """§13 Phase 1 one-time migration: seed a conversation from an existing
    `_history/{channel}.jsonl`, then that file stays frozen read-only."""
    import context_service
    channel = body.get("channel")
    advisor = body.get("advisor", channel)
    device = body.get("device", f"legacy:{channel}")
    if not channel:
        raise HTTPException(status_code=400, detail="channel required")
    jsonl_path = Path(f"/vault/60_Council/_history/{channel}.jsonl")
    return context_service.import_legacy_history(channel, advisor, device, jsonl_path)


@app.get("/invariants/state")
def invariants_state():
    """Return current invariant results from vault JSON."""
    p = Path("/vault/00_System/invariants.json")
    if not p.exists():
        return {"ok": False, "error": "invariants.json not found"}
    try:
        return {"ok": True, "data": json.loads(p.read_text())}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _table_exists(conn, name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (name,)
    ).fetchone()
    return row is not None


@app.get("/metrics/summary")
def metrics_summary():
    """Return basic operational metrics from orchestrator DB."""
    conn = get_conn()
    try:
        active_jobs   = conn.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('running','queued')").fetchone()[0]
        pending_gates = conn.execute("SELECT COUNT(*) FROM gates WHERE status='pending'").fetchone()[0]
        total_jobs    = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        failed_jobs   = conn.execute("SELECT COUNT(*) FROM jobs WHERE status='failed_permanent'").fetchone()[0]
        overrides_7d  = 0
        if _table_exists(conn, "overrides"):
            overrides_7d = conn.execute(
                "SELECT COUNT(*) FROM overrides WHERE created_at > datetime('now', '-7 days')"
            ).fetchone()[0]
    finally:
        conn.close()
    return {
        "ok": True,
        "data": {
            "active_jobs":   active_jobs,
            "pending_gates": pending_gates,
            "total_jobs":    total_jobs,
            "failed_jobs":   failed_jobs,
            "overrides_7d":  overrides_7d,
        }
    }


@app.get("/reviews/summary")
def reviews_summary():
    """Return peer review history from vault/60_Council/reviews/."""
    reviews_dir = Path("/vault/60_Council/reviews")
    if not reviews_dir.exists():
        return {"ok": True, "count": 0, "reviews": [], "incorporation_rate": None}
    records = []
    for f in sorted(reviews_dir.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            d = json.loads(f.read_text())
            records.append({
                "topic":              d.get("topic", f.stem),
                "reviewer":           d.get("reviewer", "unknown"),
                "model":              d.get("model", "unknown"),
                "reviewed_at":        d.get("reviewed_at", ""),
                "findings_count":     len(d.get("findings", [])),
                "incorporated_count": len(d.get("incorporated", [])),
                "skipped_count":      len(d.get("skipped", [])),
            })
        except Exception:
            continue
    total_inc      = sum(r["incorporated_count"] for r in records)
    total_findings = sum(r["findings_count"] for r in records)
    rate = round(total_inc / total_findings, 2) if total_findings else None
    return {"ok": True, "count": len(records), "incorporation_rate": rate, "reviews": records}



@app.get("/cost-summary")
def cost_summary():
    """Cost aggregation from token_usage.json (by advisor/model) + fixed costs config."""
    import json as _json
    from datetime import date as _date

    usage_path = Path("/vault/00_System/token_usage.json")
    config_path = Path("/vault/00_System/pricing_config.json")

    today = _date.today().isoformat()
    this_month = today[:7]

    usage_data: dict = {}
    if usage_path.exists():
        try:
            usage_data = _json.loads(usage_path.read_text())
        except Exception as e:
            log.warning("cost-summary: token_usage.json read error: %s", e)

    pricing: dict = {}
    if config_path.exists():
        try:
            pricing = _json.loads(config_path.read_text())
        except Exception as e:
            log.warning("cost-summary: pricing_config.json read error: %s", e)

    today_day = next((d for d in usage_data.get("days", []) if d.get("date") == today), {})

    def _hit_rate(cache_read: float, cache_creation: float) -> float | None:
        # CONTEXT_SPEC §7/§8.25 — fraction of cacheable-prefix requests that hit
        # vs required a fresh cache write. None (not 0) when there's no cache
        # traffic yet, so the Health Board can distinguish "no data" from "0%".
        total = (cache_read or 0) + (cache_creation or 0)
        return round(cache_read / total, 4) if total else None

    # Month aggregation across days
    month_cost = 0.0
    month_calls = 0
    month_cache_read = 0
    month_cache_creation = 0
    by_advisor_month: dict = {}
    by_model_month: dict = {}
    for d in usage_data.get("days", []):
        if not d.get("date", "").startswith(this_month):
            continue
        month_cost += d.get("cost_usd", 0)
        month_calls += d.get("calls", 0)
        month_cache_read += d.get("cache_read", 0)
        month_cache_creation += d.get("cache_creation", 0)
        for adv, v in d.get("by_advisor", {}).items():
            e = by_advisor_month.setdefault(adv, {"calls": 0, "cost_usd": 0.0, "input": 0, "output": 0})
            e["calls"] += v.get("calls", 0)
            e["cost_usd"] = round(e["cost_usd"] + v.get("cost_usd", 0), 6)
            e["input"] += v.get("input", 0)
            e["output"] += v.get("output", 0)
        for mdl, v in d.get("by_model", {}).items():
            e = by_model_month.setdefault(mdl, {"calls": 0, "cost_usd": 0.0, "input": 0, "output": 0})
            e["calls"] += v.get("calls", 0)
            e["cost_usd"] = round(e["cost_usd"] + v.get("cost_usd", 0), 6)
            e["input"] += v.get("input", 0)
            e["output"] += v.get("output", 0)

    fixed_monthly = pricing.get("fixed_monthly", {})
    fixed_total = sum(v.get("usd", 0) for v in fixed_monthly.values() if isinstance(v, dict))
    month_total = round(month_cost + fixed_total, 2)

    return {
        "ok": True,
        "today": {
            "date": today,
            "cost_usd": round(today_day.get("cost_usd", 0), 4),
            "calls": today_day.get("calls", 0),
            "by_advisor": today_day.get("by_advisor", {}),
            "by_model": today_day.get("by_model", {}),
            "cache_hit_rate": _hit_rate(today_day.get("cache_read", 0), today_day.get("cache_creation", 0)),
        },
        "month": {
            "month": this_month,
            "token_cost_usd": round(month_cost, 4),
            "fixed_cost_usd": round(fixed_total, 2),
            "total_usd": month_total,
            "calls": month_calls,
            "by_advisor": by_advisor_month,
            "by_model": by_model_month,
            "fixed_monthly": fixed_monthly,
            "cache_hit_rate": _hit_rate(month_cache_read, month_cache_creation),
        },
        "all_time": {
            "cost_usd": round(usage_data.get("total", {}).get("cost_usd", 0), 2),
            "calls": usage_data.get("total", {}).get("calls", 0),
            "by_advisor": usage_data.get("total", {}).get("by_advisor", {}),
            "cache_hit_rate": _hit_rate(
                usage_data.get("total", {}).get("cache_read", 0),
                usage_data.get("total", {}).get("cache_creation", 0),
            ),
        },
    }


@app.get("/workflow-metrics")
def workflow_metrics_summary():
    """Return per-step workflow metrics from workflow_metrics table."""
    conn = get_conn()
    try:
        if not _table_exists(conn, "workflow_metrics"):
            return {"ok": True, "total_steps": 0, "rows": [], "by_capability": []}

        total = conn.execute("SELECT COUNT(*) FROM workflow_metrics").fetchone()[0]
        avg_latency = conn.execute(
            "SELECT AVG(latency_ms) FROM workflow_metrics WHERE latency_ms IS NOT NULL"
        ).fetchone()[0]
        verify_rate_row = conn.execute(
            "SELECT COUNT(*) FROM workflow_metrics WHERE verified_first_try=1"
        ).fetchone()[0]
        verify_rate = round(verify_rate_row / total, 2) if total else None

        by_cap = conn.execute("""
            SELECT capability,
                   COUNT(*) as count,
                   AVG(latency_ms) as avg_ms,
                   SUM(retry_count) as retries,
                   SUM(verified_first_try) as verified
            FROM workflow_metrics
            WHERE capability IS NOT NULL
            GROUP BY capability
            ORDER BY count DESC
            LIMIT 20
        """).fetchall()

        recent = conn.execute("""
            SELECT wm.step_name, wm.capability, wm.latency_ms,
                   wm.verified_first_try, wm.retry_count, wm.created_at,
                   j.type as job_type
            FROM workflow_metrics wm
            LEFT JOIN jobs j ON j.id = wm.job_id
            ORDER BY wm.created_at DESC
            LIMIT 50
        """).fetchall()

        return {
            "ok": True,
            "total_steps": total,
            "avg_latency_ms": round(avg_latency) if avg_latency else None,
            "verify_first_try_rate": verify_rate,
            "by_capability": [
                {"capability": r[0], "count": r[1],
                 "avg_ms": round(r[2]) if r[2] else None,
                 "retries": r[3], "verified": r[4]}
                for r in by_cap
            ],
            "recent": [
                {"step": r[0], "capability": r[1], "latency_ms": r[2],
                 "verified": bool(r[3]), "retries": r[4],
                 "at": r[5][:16] if r[5] else None, "job_type": r[6]}
                for r in recent
            ],
        }
    finally:
        conn.close()


@app.post("/learning/run-aggregation")
def trigger_aggregation():
    """Manually trigger the S6-2 pattern aggregation (for testing)."""
    try:
        path = run_aggregation()
        return {"ok": True, "output": str(path)}
    except Exception as exc:
        raise HTTPException(500, str(exc))

@app.post("/learning/run-proposals")
def trigger_proposals():
    """S6-3: generate proposals from latest pattern file and post to Slack."""
    try:
        result = generate_proposals()
        return result
    except Exception as exc:
        raise HTTPException(500, str(exc))

@app.get("/learning/proposals")
def list_proposals():
    """List all generated proposal files."""
    import pathlib
    vault_learning = pathlib.Path("/vault/60_Council/learning")
    if not vault_learning.exists():
        return {"proposals": [], "patterns": []}
    proposals = sorted(vault_learning.glob("*-proposal-*.md"), reverse=True)
    patterns  = sorted(vault_learning.glob("*-patterns.json"), reverse=True)
    return {
        "proposals": [p.name for p in proposals[:20]],
        "patterns":  [p.name for p in patterns[:10]],
    }

# ── Capability endpoint + quality gates ──────────────────────────────────────

import time as _time

_RATE_LIMITS: dict = {
    "slack.post": {"window": 60, "max": 5},   # 5 per minute
}

# Destructive capabilities require confirmed=true in inputs
_DESTRUCTIVE: set = {"vault.write", "session.close", "workspace.sync"}

# Read-only capabilities — no gate needed
_READ_ONLY: set = {
    "vault.read", "vault.list",
    "workspace.read", "workspace.list",
    "session.close_status",
    "calendar.get_events",
}


def _check_rate_limit(name: str) -> str | None:
    """Return error string if rate-limited, else None. Backed by SQLite."""
    cfg = _RATE_LIMITS.get(name)
    if not cfg:
        return None
    now = _time.time()
    window = cfg["window"]
    cutoff = _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime(now - window))
    ts_now = _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime(now))
    pruned = _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime(now - 3600))
    conn = get_conn()
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM rate_counters WHERE capability=? AND called_at > ?",
            (name, cutoff),
        ).fetchone()[0]
        if count >= cfg["max"]:
            return f"Rate limit: {name} max {cfg['max']} calls per {window}s — wait before retrying"
        conn.execute(
            "INSERT INTO rate_counters (capability, called_at) VALUES (?, ?)",
            (name, ts_now),
        )
        conn.execute("DELETE FROM rate_counters WHERE called_at < ?", (pruned,))
        conn.commit()
    finally:
        conn.close()
    return None


@app.get("/capabilities")
def list_capabilities():
    """List all registered capabilities with gate metadata including autonomy policy."""
    from capabilities import _registry
    from policy.autonomy import list_policies, AUTONOMY_POLICIES
    policy_map = {p["capability"]: p for p in list_policies()}
    caps = []
    for name in sorted(_registry.keys()):
        destructive = name in _DESTRUCTIVE
        rate_cfg = _RATE_LIMITS.get(name)
        pol = policy_map.get(name, {"rule": "allow", "reason": ""})
        caps.append({
            "name": name,
            "destructive": destructive,
            "read_only": name in _READ_ONLY,
            "rate_limit": rate_cfg,
            "confirmation_required": destructive,
            "autonomy_rule": pol["rule"],
            "autonomy_reason": pol.get("reason", ""),
        })
    return {"capabilities": caps, "count": len(caps)}


@app.post("/capability/{name}")
def run_capability_endpoint(name: str, body: dict):
    """Execute a named capability with given inputs.

    For destructive capabilities (vault.write, session.close, workspace.sync),
    pass confirmed=true in the body to bypass the confirmation gate.
    """
    from capabilities import _registry, get_capability

    # Existence check
    if name not in _registry:
        return {"ok": False, "error": f"Unknown capability: {name}",
                "available": sorted(_registry.keys())}

    inputs = body.get("inputs", {})
    caller = body.get("caller", "admin")

    # Autonomy policy gate — consulted before destructive gate
    from policy.autonomy import check_policy
    _policy_action, _policy_reason = check_policy(name, caller)
    if _policy_action == "block_never":
        return {"ok": False, "gate": "autonomy_never",
                "capability": name, "caller": caller, "message": _policy_reason}
    if _policy_action == "block_autonomous":
        return {"ok": False, "gate": "autonomy_requires_approval",
                "capability": name, "caller": caller, "message": _policy_reason}

    # Destructive gate
    if name in _DESTRUCTIVE and not body.get("confirmed", False):
        return {
            "ok": False,
            "gate": "destructive_confirmation",
            "capability": name,
            "message": f"'{name}' is destructive. Resend with confirmed=true to proceed.",
            "inputs_preview": {k: str(v)[:100] for k, v in inputs.items()},
        }

    # Destructive op audit — append-only JSONL + Slack mirror
    if name in _DESTRUCTIVE:
        import json as _json
        _audit = {
            "ts": _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime()),
            "capability": name,
            "caller": caller,
            "operator": body.get("operator", "unknown"),
            "inputs_preview": {k: str(v)[:100] for k, v in inputs.items()},
        }
        _audit_path = Path("/vault/00_System/destructive_ops.jsonl")
        try:
            _audit_path.parent.mkdir(parents=True, exist_ok=True)
            with _audit_path.open("a") as _af:
                _af.write(_json.dumps(_audit) + "\n")
        except Exception as _ae:
            log.warning("Audit log write failed: %s", _ae)
        _post_slack(
            f":axe: *DESTRUCTIVE OP CONFIRMED*\n"
            f"*Capability:* `{name}`\n"
            f"*Operator:* {_audit['operator']}\n"
            f"*Inputs:* `{_json.dumps(_audit['inputs_preview'])}`"
        )

    # Rate limit gate
    err = _check_rate_limit(name)
    if err:
        return {"ok": False, "gate": "rate_limit", "capability": name, "message": err}

    # Execute
    try:
        fn = get_capability(name)
        result = fn(**inputs)
        return {
            "ok": result.ok,
            "capability": name,
            "status": result.status,
            "data": result.data,
            "error": result.error,
            "verification": result.verification,
        }
    except TypeError as e:
        return {"ok": False, "error": f"Invalid inputs for {name}: {e}"}
    except Exception as e:
        log.exception("capability endpoint error: %s %s", name, e)
        return {"ok": False, "error": str(e)}
