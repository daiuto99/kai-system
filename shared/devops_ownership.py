"""DevOps ownership layer — the escalation spine (KAI-46, Phase 1).

Design: docs/DEVOPS_AUTONOMOUS_OWNERSHIP.md §2.2–2.5. Every failing check in the
system becomes OWNED through ONE path — auto-remediated, queued as a triaged Plane
item, or gated to Leo for a genuine decision — never merely logged to an unwatched
dashboard (the root of the 2026-08-24 disk crisis, KAI-43).

This module invents NO transport. It composes the three live primitives:
  - shared/notify_gateway.notify   → dashboard audience (DevOps activity, Leo untouched)
  - sync_plane_state.req/get_issues → the triaged, deduped Plane queue
  - shared/sprint_gate.request_sprint_gate → the blocking, fail-closed decision gate

The vocabulary of "a problem needs a cause" is inherited from shared/findings.py —
a Finding that asserts a problem (bad severity) carries a diagnosis, or is stamped
NOT_YET_DIAGNOSED. No bare alarms.

Three objects:
  Finding    — the one structured signal every check produces (§2.2)
  Custodian  — the protocol every domain implements (§2.4)
  dispatch() — the one router: auto | structural | decision (§2.3)

L18: nothing here logs a secret; the primitives it calls each enforce their own.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional, Protocol, runtime_checkable

log = logging.getLogger("devops_ownership")

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

# Findings Contract vocabulary — reuse, don't reinvent (shared/findings.py).
try:
    from findings import NOT_YET_DIAGNOSED
except Exception:  # pragma: no cover - contract module optional at import time
    NOT_YET_DIAGNOSED = "not-yet-diagnosed"

# ── Dispositions — how DevOps will handle a Finding (§2.3) ─────────────────────
AUTO = "auto"              # safe, reversible/regenerable → remediate autonomously
STRUCTURAL = "structural"  # must not decide alone → triaged, deduped Plane item
DECISION = "decision"      # a genuine Leo decision → blocking approval gate
_DISPOSITIONS = frozenset({AUTO, STRUCTURAL, DECISION})

# Where the layer keeps its state on the worker.
_KAI_ROOT = Path(os.environ.get("KAI_SYSTEM_ROOT", "/home/leo/kai-system"))
RUN_LOG = Path(os.environ.get("DEVOPS_CUSTODIAN_LOG", str(_KAI_ROOT / "logs" / "devops_custodian.jsonl")))
LIVENESS = Path(os.environ.get("DEVOPS_CUSTODIAN_LIVENESS", str(_KAI_ROOT / "logs" / "devops_custodian_liveness.json")))

# Pre-exhaustion guard (§Phase 3, KAI-48). The 2026-08-24 disk crisis taught that at
# 100% the monitor itself cannot write its own state — detection and remediation both
# die exactly when they are needed most. So the runner reserves headroom: at/above
# PREEMPT_PCT (a hard pre-empt band BELOW true exhaustion) it runs an emergency reclaim
# FIRST, ahead of the normal sweep, so the custodian can always still stamp liveness and
# record its run. 95% default = a 5% reserve the runner defends before it does anything else.
PREEMPT_PCT = int(os.environ.get("DEVOPS_PREEMPT_PCT", "95"))

# The Plane project DevOps files its structural queue into: "KAI System".
# (The KAI-44 disk custodian filed into the WordPress project by id — a copy-paste
# bug; the shared layer files DevOps work where DevOps work lives. Overridable.)
KAI_PROJECT_ID = os.environ.get("DEVOPS_PLANE_PROJECT_ID", "78c49227-82d4-477d-a920-66b08cb91c56")
_STATE_BACKLOG = "390860ae-b4fd-4ac6-8ee7-88a58e735e2f"  # KAI System · Backlog
_STRUCTURAL_MARKER_TMPL = "[devops-structural:{dedup_key}]"  # stable dedup token in the ticket name


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── The Finding — the one structured object every check produces (§2.2) ────────

@dataclass
class Finding:
    domain: str            # "storage" | "updates" | "backups" | "services" | "security" | "fleet"
    check: str             # "disk" | "host_updates" | "backup_freshness" | "cert_expiry" | ...
    severity: str          # "warn" | "crit"
    diagnosis: str         # root-cause / composition (human-readable). NEVER a bare boolean.
    disposition: str       # "auto" | "structural" | "decision"
    proposed_action: str   # what auto did, or what structural/decision proposes
    dedup_key: str         # stable key for queue/gate dedup + cooldown
    detail: dict = field(default_factory=dict)  # machine-readable evidence

    def __post_init__(self) -> None:
        if self.disposition not in _DISPOSITIONS:
            raise ValueError(
                f"Finding.disposition must be one of {sorted(_DISPOSITIONS)}, got {self.disposition!r}"
            )
        if not self.dedup_key or not str(self.dedup_key).strip():
            raise ValueError("Finding.dedup_key is required (queue/gate dedup depends on it)")
        # Findings Contract: every Finding asserts a problem (a healthy domain returns
        # []), so every Finding MUST carry a cause. A blank diagnosis is stamped
        # not-yet-diagnosed — never left as a guessable void the operator fills from memory.
        if not (self.diagnosis and str(self.diagnosis).strip()):
            self.diagnosis = NOT_YET_DIAGNOSED

    @property
    def undiagnosed(self) -> bool:
        return self.diagnosis == NOT_YET_DIAGNOSED

    def to_dict(self) -> dict:
        return {
            "domain": self.domain, "check": self.check, "severity": self.severity,
            "diagnosis": self.diagnosis, "disposition": self.disposition,
            "proposed_action": self.proposed_action, "dedup_key": self.dedup_key,
            "detail": self.detail,
        }


# ── The Custodian protocol — every domain implements this (§2.4) ───────────────

@runtime_checkable
class Custodian(Protocol):
    domain: str
    def assess(self) -> list["Finding"]:
        """WATCH + DIAGNOSE (read-only). Returns [] when the domain is healthy.
        classify()/propose lives inside assess(): each Finding is returned with its
        disposition + proposed_action already set."""
        ...

    def remediate_safe(self, f: "Finding") -> str:
        """REMEDIATE-SAFE: autonomously fix the safe, reversible/regenerable class.
        Returns a human-readable description of what was done. Called only for
        disposition == auto."""
        ...

    # execute_decision(f) is OPTIONAL — implement it when a domain has a gated action
    # to run after Leo approves a DECISION finding. Absent → approval logs, no action.


def _execute_decision(custodian: "Custodian", f: "Finding") -> str:
    fn = getattr(custodian, "execute_decision", None)
    if callable(fn):
        return str(fn(f))
    return "approved — no execute_decision() implemented for this custodian (logged only)"


# ── The dispatcher's dependencies — injectable so tests never page/queue for real ──

@dataclass
class Deps:
    """The three transports the dispatcher routes to. Defaults wire the live
    primitives; tests inject recorders/stubs. Kept as an explicit object so the
    router logic (auto/structural/decision) is unit-testable in isolation."""
    notify_dashboard: Callable[["Finding", str], None]
    file_structural: Callable[["Finding"], str]
    request_decision: Callable[["Finding"], "DecisionOutcome"]


@dataclass
class DecisionOutcome:
    approved: bool
    resolved: bool   # True iff Leo actually decided; False on timeout
    notes: str = ""

    @property
    def timed_out(self) -> bool:
        return not self.resolved


# ── Live primitive adapters (the defaults) ─────────────────────────────────────

def _live_notify_dashboard(f: "Finding", result: str) -> None:
    """Log DevOps activity to the dashboard audience — Leo is NOT pushed (Rule B)."""
    try:
        from notify_gateway import Event, notify
        notify(Event(
            source="devops-custodian", kind="devops",
            title=f"[{f.domain}/{f.check}] {f.proposed_action}",
            body=f"{f.diagnosis}\n\nresult: {result}",
            audience="dashboard", provenance="real",
            dedup_key=f.dedup_key, status=f.severity, cause=f.diagnosis,
        ))
    except Exception as e:  # dashboard logging must never crash the custodian
        log.error("dashboard notify failed for %s/%s: %s", f.domain, f.check, type(e).__name__)


def _live_file_structural(f: "Finding") -> str:
    """File OR refresh a triaged, deduped Plane item for a structural finding.
    Dedup on the stable marker embedded in the ticket name — refresh the open
    marker instead of spamming duplicates (the disk custodian's pattern, generalized)."""
    marker = _STRUCTURAL_MARKER_TMPL.format(dedup_key=f.dedup_key)
    title = f"[DevOps] {f.domain}/{f.check} {f.severity} — {f.proposed_action} {marker}"
    body = (
        f"<p><strong>Diagnosis:</strong> {f.diagnosis}</p>"
        f"<p><strong>Proposed action (DevOps queue):</strong> {f.proposed_action}</p>"
        f"<p><strong>Evidence:</strong> {json.dumps(f.detail)[:1500]}</p>"
        f"<p>Structural — must not be auto-decided. This is a triaged decision item, not a silent log.</p>"
    )
    try:
        sys.path.insert(0, str(_KAI_ROOT))
        import sync_plane_state as sp
        for i in sp.get_issues(KAI_PROJECT_ID):
            if marker in (i.get("name") or ""):
                return f"structural ticket already open ({i.get('sequence_id') or i.get('id')}) — refreshed, not duplicated"
        r = sp.req("POST", f"projects/{KAI_PROJECT_ID}/issues/",
                   {"name": title, "description_html": body,
                    "priority": "high" if f.severity == "crit" else "medium",
                    "state": _STATE_BACKLOG})
        return f"queued structural → Plane {r.get('sequence_id') or r.get('id')}"
    except Exception as e:  # queue failure must never crash the custodian; it is logged
        log.error("structural filing failed for %s/%s: %s", f.domain, f.check, type(e).__name__)
        return f"structural filing failed (logged): {type(e).__name__}"


def _live_request_decision(f: "Finding") -> "DecisionOutcome":
    """Raise a blocking approval gate for a genuine Leo decision. Fail-closed:
    a raise failure or a timeout is NOT approved."""
    try:
        from sprint_gate import request_sprint_gate
        summary = f"[DevOps decision] {f.domain}/{f.check}: {f.proposed_action}"
        detail = f"Diagnosis: {f.diagnosis}\nEvidence: {json.dumps(f.detail)[:1000]}"
        timeout_s = float(os.environ.get("DEVOPS_GATE_TIMEOUT_S", "1800"))
        out = request_sprint_gate(summary, detail, timeout_s=timeout_s)
        return DecisionOutcome(approved=bool(out.approved), resolved=bool(out.resolved),
                               notes=str(getattr(out, "notes", "") or ""))
    except Exception as e:
        # Could not RAISE the gate — fail closed (NOT approved), never guess Leo's answer.
        log.error("decision gate raise failed for %s/%s: %s", f.domain, f.check, type(e).__name__)
        return DecisionOutcome(approved=False, resolved=False, notes=f"gate raise failed: {type(e).__name__}")


def default_deps() -> "Deps":
    return Deps(
        notify_dashboard=_live_notify_dashboard,
        file_structural=_live_file_structural,
        request_decision=_live_request_decision,
    )


# ── The dispatcher — one router, reused primitives (§2.3) ───────────────────────

def dispatch(f: "Finding", custodian: "Custodian", deps: Optional["Deps"] = None) -> dict:
    """Route ONE Finding by its disposition. Returns a structured outcome record.
    Every branch is fail-soft: a transport error is captured in the record, never
    raised into the runner (one bad finding must not abort the sweep)."""
    deps = deps or default_deps()
    rec = {"ts": _now(), **f.to_dict(), "outcome": None, "handled": False}

    try:
        if f.disposition == AUTO:
            done = custodian.remediate_safe(f)
            deps.notify_dashboard(f, done)
            rec["outcome"] = f"auto-remediated: {done}"
            rec["handled"] = True

        elif f.disposition == STRUCTURAL:
            rec["outcome"] = deps.file_structural(f)
            rec["handled"] = True

        elif f.disposition == DECISION:
            gate = deps.request_decision(f)
            if gate.approved:
                done = _execute_decision(custodian, f)
                deps.notify_dashboard(f, f"decision APPROVED → {done}")
                rec["outcome"] = f"approved → {done}"
                rec["handled"] = True
            else:
                # Reject OR timeout → queue it as structural and stand down.
                # Fail-closed: an unmade decision is never executed; it is triaged.
                reason = "timed out" if gate.timed_out else "rejected"
                queued = deps.file_structural(f)
                rec["outcome"] = f"decision {reason} → stood down, queued: {queued}"
                rec["handled"] = True
    except Exception as e:  # a single finding's failure never aborts the sweep
        log.error("dispatch failed for %s/%s: %s", f.domain, f.check, type(e).__name__)
        rec["outcome"] = f"dispatch error (logged): {type(e).__name__}: {e}"

    return rec


# ── Pre-exhaustion guard (§Phase 3, KAI-48) ────────────────────────────────────

def root_pct() -> float:
    """Root-fs usage percent. A float so callers keep precision; the guard compares
    against an int threshold. Isolated + tiny so tests can monkeypatch it."""
    du = shutil.disk_usage("/")
    return du.used / du.total * 100 if du.total else 0.0


def pre_exhaustion_engage(pct: float, *, preempt_pct: int = PREEMPT_PCT) -> bool:
    """Pure verdict: is the root fs inside the reserved headroom band (>= preempt_pct)?
    At that point a resource is about to exhaust and the custodian's own writes are at
    risk — so we pre-empt. Unit-testable in isolation."""
    return pct >= preempt_pct


def pre_exhaustion_guard(*, preempt_pct: int = PREEMPT_PCT,
                         reclaim: Optional[Callable[[], str]] = None,
                         pct_fn: Optional[Callable[[], float]] = None) -> Optional[dict]:
    """Run BEFORE the normal sweep. If the root fs has crossed into the reserved
    headroom band, run the emergency reclaim NOW — ahead of everything else — so the
    runner can always still record its own state (the KAI-43 lesson: at 100% the
    monitor cannot write). Returns a preempt record when it engages, else None.
    Fail-soft: a reclaim error is captured, never raised into the runner.

    pct_fn defaults to the module-level root_pct resolved at CALL time (not def time)
    so tests can monkeypatch do.root_pct."""
    pct_fn = pct_fn or root_pct
    pct = pct_fn()
    if not pre_exhaustion_engage(pct, preempt_pct=preempt_pct):
        return None
    rec = {"ts": _now(), "event": "pre_exhaustion_preempt", "pct": round(pct, 1),
           "preempt_pct": preempt_pct}
    if reclaim is None:
        rec["reclaimed"] = "no emergency reclaimer supplied — pre-empt flagged only"
        rec["pct_after"] = round(pct, 1)
        return rec
    try:
        rec["reclaimed"] = reclaim()
    except Exception as e:  # emergency reclaim must never crash the runner
        log.error("pre-exhaustion reclaim failed: %s", type(e).__name__)
        rec["reclaimed"] = f"emergency reclaim error: {type(e).__name__}: {e}"
    try:
        rec["pct_after"] = round(pct_fn(), 1)
    except Exception:
        rec["pct_after"] = None
    return rec


# ── Ownership state + meta-monitoring (§2.5) ────────────────────────────────────

def _record_run(rec: dict) -> None:
    try:
        RUN_LOG.parent.mkdir(parents=True, exist_ok=True)
        with RUN_LOG.open("a") as fh:
            fh.write(json.dumps(rec, default=str) + "\n")
    except Exception as e:
        log.error("run-log write failed: %s", type(e).__name__)


def mark_liveness(domain: str, ts: Optional[str] = None) -> None:
    """Stamp the last-successful-run time for a custodian. A custodian that stops
    stamping becomes itself a Finding (meta_monitor)."""
    ts = ts or _now()
    try:
        data = {}
        if LIVENESS.exists():
            data = json.loads(LIVENESS.read_text())
        data[domain] = ts
        LIVENESS.parent.mkdir(parents=True, exist_ok=True)
        LIVENESS.write_text(json.dumps(data, indent=2))
    except Exception as e:
        log.error("liveness write failed for %s: %s", domain, type(e).__name__)


def _parse_iso(ts: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(ts)
    except Exception:
        return None


def meta_monitor(expected_domains: list[str], *, max_age_s: float, now: Optional[datetime] = None) -> list["Finding"]:
    """A custodian that stopped running is an incident, not silence (§2.5). Returns a
    structural Finding for every expected domain whose liveness stamp is missing or
    older than max_age_s. Pure logic (now injectable) so it is unit-testable."""
    now = now or datetime.now(timezone.utc)
    try:
        data = json.loads(LIVENESS.read_text()) if LIVENESS.exists() else {}
    except Exception:
        data = {}
    out: list[Finding] = []
    for domain in expected_domains:
        stamp = data.get(domain)
        age = None
        if stamp:
            dt = _parse_iso(stamp)
            if dt is not None:
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                age = (now - dt).total_seconds()
        if age is None or age > max_age_s:
            reason = "never ran" if stamp is None else f"last ran {int(age)}s ago (> {int(max_age_s)}s)"
            out.append(Finding(
                domain="meta", check=f"custodian_liveness:{domain}", severity="warn",
                diagnosis=f"custodian '{domain}' is not running — {reason}. A stopped custodian is an incident.",
                disposition=STRUCTURAL,
                proposed_action=f"investigate why the {domain} custodian stopped and restore its cron/timer",
                dedup_key=f"meta-liveness-{domain}",
                detail={"domain": domain, "last_stamp": stamp, "age_s": age, "max_age_s": max_age_s},
            ))
    return out


def run_custodians(custodians: list["Custodian"], *, deps: Optional["Deps"] = None,
                   liveness_max_age_s: float = 3600.0, record: bool = True,
                   preempt_reclaim: Optional[Callable[[], str]] = None,
                   preempt_pct: int = PREEMPT_PCT) -> dict:
    """Sweep every custodian: assess → dispatch each Finding → stamp liveness. Then
    meta-monitor the roster. Returns a summary. This is the runner's core, factored
    out so it is testable with injected deps + custodians.

    Pre-exhaustion guard runs FIRST (§Phase 3): if root disk is inside the reserved
    headroom band, an emergency reclaim runs ahead of the sweep so the runner can
    always still record its own state even under near-exhaustion."""
    deps = deps or default_deps()
    summary = {"ts": _now(), "custodians": [], "findings": 0, "outcomes": [],
               "meta": [], "preempt": None}

    # Reserve headroom before doing anything else — a near-full disk must not block
    # the custodian from writing its own liveness/run-log (the KAI-43 lesson).
    try:
        preempt = pre_exhaustion_guard(preempt_pct=preempt_pct, reclaim=preempt_reclaim)
        if preempt is not None:
            summary["preempt"] = preempt
            if record:
                _record_run(preempt)
    except Exception as e:  # the guard must never itself abort the sweep
        log.error("pre-exhaustion guard raised: %s", type(e).__name__)

    for c in custodians:
        cname = getattr(c, "domain", c.__class__.__name__)
        entry = {"domain": cname, "findings": 0, "error": None}
        try:
            findings = c.assess() or []
        except Exception as e:
            entry["error"] = f"assess failed: {type(e).__name__}: {e}"
            log.error("assess failed for %s: %s", cname, type(e).__name__)
            summary["custodians"].append(entry)
            continue
        entry["findings"] = len(findings)
        summary["findings"] += len(findings)
        for f in findings:
            rec = dispatch(f, c, deps)
            summary["outcomes"].append(rec)
            if record:
                _record_run(rec)
        # A custodian that assessed without raising is alive — stamp it.
        mark_liveness(cname)
        summary["custodians"].append(entry)

    # Meta-monitor: a custodian that stopped stamping is itself a structural Finding.
    expected = [getattr(c, "domain", c.__class__.__name__) for c in custodians]
    meta_findings = meta_monitor(expected, max_age_s=liveness_max_age_s)
    for mf in meta_findings:
        rec = dispatch(mf, _NullCustodian(), deps)
        summary["meta"].append(rec)
        if record:
            _record_run(rec)

    return summary


class _NullCustodian:
    """Stand-in owner for meta findings (they are structural — no remediate needed)."""
    domain = "meta"
    def assess(self) -> list["Finding"]:
        return []
    def remediate_safe(self, f: "Finding") -> str:  # pragma: no cover - never auto
        return "n/a"
