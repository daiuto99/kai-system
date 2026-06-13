"""self_modify.* capabilities — M2-1.B chain.

M2-1.A scaffolded the proposal log. M2-1.B adds the verify/apply/commit/update_plane
chain so a ritual + diff can actually land code under approval, replacing direct
Edit/Write/Bash on KAI's own source.

Capabilities:
  self_modify.propose       — append a structured proposal record (M2-1.A, kept)
  self_modify.verify        — LiteLLM semantic check: ritual ↔ diff coherence
  self_modify.apply         — apply unified diff to target_root via `patch -p1`
  self_modify.commit        — git commit inside target_root with ritual in message
  self_modify.update_plane  — append commit SHA + workflow_id to Plane ticket

Allowlist (apply/commit target_root, enforced):
  /kai-system   — the orchestrator's own source tree (rw mount, M2-1.B)
  /workspace    — sonicink rw mount

Ritual contract (5 fields, validated by propose):
  plane_ticket_id, gate, principle, retirement, diff
"""
import json
import os
import re
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx

from models import CapabilityResult
from . import capability

_LOG_PATH = Path("/vault/00_System/self_modify_proposals.jsonl")
_VERIFIER_LOG_PATH = Path("/vault/00_System/self_modify_verifications.jsonl")

_REQUIRED = ("plane_ticket_id", "gate", "principle", "retirement", "diff")

_TARGET_ROOT_ALLOWLIST = ("/kai-system", "/workspace")

_LITELLM_URL = os.environ.get("LITELLM_URL", "http://kai-litellm:4000")
_VERIFIER_MODEL = os.environ.get("SELF_MODIFY_VERIFIER_MODEL", "qwen-mid")
_VERIFIER_TIMEOUT_S = 60

_WORKER_API_URL = os.environ.get("WORKER_API_URL", "http://kai-worker-api:8001")

_VERIFIER_SYSTEM = (
    "You are a semantic verifier for KAI's self-modify workflow. You receive a "
    "ritual (the §3 JARVIS gate the change moves toward, the §5 operating principle "
    "it invokes, and what gets retired alongside) plus a unified diff. Decide:\n"
    "  (a) Does the diff plausibly match the stated ritual? "
    "Look for coherence between what the ritual promises and what the diff actually changes.\n"
    "  (b) Does the diff appear to be a bandaid framed as systemic? "
    "A bandaid is a narrow patch presented with grand framing — e.g. a one-off "
    "string change claiming it 'fixes the whole gate flow', or copy-pasted ritual "
    "boilerplate around a trivial edit.\n"
    "Respond with raw JSON only (no markdown), shape:\n"
    '  {"pass": true|false, "reason": "<one to three sentences>"}\n'
    "Pass means the change is allowed to apply. Fail means it should NOT apply."
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _validate(inputs: dict) -> tuple[bool, list[str]]:
    missing = [k for k in _REQUIRED if not inputs.get(k)]
    return (not missing), missing


def _litellm_key() -> str:
    p = Path("/run/wp_secrets/litellm_master_key.txt")
    return p.read_text().strip() if p.exists() else os.environ.get("LITELLM_MASTER_KEY", "")


def _plane_token() -> str:
    p = Path("/run/wp_secrets/plane_api_token.txt")
    return p.read_text().strip() if p.exists() else os.environ.get("PLANE_API_TOKEN", "")


def _safe_target_root(target_root: str | None) -> str | None:
    """Return the normalized target_root if allowlisted, else None."""
    if not target_root:
        return None
    try:
        norm = str(Path(target_root).resolve())
    except Exception:
        return None
    if norm not in _TARGET_ROOT_ALLOWLIST:
        return None
    if not Path(norm).is_dir():
        return None
    return norm


# ─── propose (M2-1.A, kept) ──────────────────────────────────────────────────

@capability("self_modify.propose")
def propose(**inputs) -> CapabilityResult:
    """Record a self-modify proposal. Does not apply, verify, or commit."""
    ok, missing = _validate(inputs)
    if not ok:
        return CapabilityResult(
            ok=False, status="failed_permanent",
            error={"type": "missing_required_fields", "missing": missing,
                   "required": list(_REQUIRED)},
        )

    proposal_id = f"prop_{uuid.uuid4().hex[:12]}"
    record = {
        "proposal_id": proposal_id,
        "logged_at": _now_iso(),
        "plane_ticket_id": inputs["plane_ticket_id"],
        "ritual": {
            "gate": inputs["gate"],
            "principle": inputs["principle"],
            "retirement": inputs["retirement"],
        },
        "diff": {
            "bytes": len(inputs["diff"]),
            "lines": inputs["diff"].count("\n") + 1,
            "preview": inputs["diff"][:400],
            "body": inputs["diff"],
        },
        "stage": "proposed",
        "scaffold_version": "M2-1.B",
    }

    try:
        _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _LOG_PATH.open("a") as f:
            f.write(json.dumps(record) + "\n")
    except Exception as e:
        return CapabilityResult(
            ok=False, status="failed_recoverable",
            error={"type": "log_write_failed", "detail": str(e),
                   "path": str(_LOG_PATH)},
        )

    return CapabilityResult(
        ok=True, status="succeeded",
        data={
            "proposal_id": proposal_id,
            "logged_at": record["logged_at"],
            "log_path": str(_LOG_PATH),
            "diff_bytes": record["diff"]["bytes"],
            "stage": "proposed",
        },
        verification={
            "verified": True,
            "method": "jsonl_append",
            "evidence": {"proposal_id": proposal_id,
                         "path": str(_LOG_PATH)},
        },
    )


# ─── verify ──────────────────────────────────────────────────────────────────

def _call_verifier(ritual: dict, diff: str) -> tuple[bool, dict, str]:
    """Returns (transport_ok, parsed_or_error_dict, raw_text)."""
    key = _litellm_key()
    if not key:
        return False, {"type": "no_litellm_key"}, ""

    user_payload = (
        "RITUAL:\n"
        f"- gate (§3): {ritual['gate']}\n"
        f"- principle (§5): {ritual['principle']}\n"
        f"- retirement: {ritual['retirement']}\n\n"
        "DIFF:\n"
        f"{diff}\n"
    )
    try:
        with httpx.Client(timeout=_VERIFIER_TIMEOUT_S) as client:
            r = client.post(
                f"{_LITELLM_URL}/v1/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json={
                    "model": _VERIFIER_MODEL,
                    "messages": [
                        {"role": "system", "content": _VERIFIER_SYSTEM},
                        {"role": "user", "content": user_payload},
                    ],
                    "temperature": 0.1,
                },
            )
    except httpx.RequestError as e:
        return False, {"type": "litellm_unreachable", "detail": str(e)}, ""

    if r.status_code != 200:
        return False, {"type": "litellm_http_error",
                       "status": r.status_code,
                       "body": r.text[:500]}, r.text

    body = r.json()
    raw = (body.get("choices") or [{}])[0].get("message", {}).get("content", "")
    if not raw:
        return False, {"type": "empty_verifier_response"}, ""

    # Strip optional markdown fence
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\n", "", text)
        text = re.sub(r"\n```$", "", text)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        # Try to find a JSON object inside the text
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            return False, {"type": "verifier_response_not_json",
                           "raw": raw[:500]}, raw
        try:
            parsed = json.loads(m.group(0))
        except json.JSONDecodeError as e:
            return False, {"type": "verifier_response_not_json",
                           "detail": str(e),
                           "raw": raw[:500]}, raw

    if "pass" not in parsed or "reason" not in parsed:
        return False, {"type": "verifier_response_bad_shape",
                       "got": parsed,
                       "expected_keys": ["pass", "reason"]}, raw

    return True, parsed, raw


@capability("self_modify.verify")
def verify(**inputs) -> CapabilityResult:
    """Semantic verifier — calls LiteLLM with ritual + diff, expects {pass, reason}."""
    ok, missing = _validate(inputs)
    if not ok:
        return CapabilityResult(
            ok=False, status="failed_permanent",
            error={"type": "missing_required_fields", "missing": missing},
        )

    ritual = {
        "gate": inputs["gate"],
        "principle": inputs["principle"],
        "retirement": inputs["retirement"],
    }
    transport_ok, parsed, raw = _call_verifier(ritual, inputs["diff"])

    record = {
        "logged_at": _now_iso(),
        "plane_ticket_id": inputs["plane_ticket_id"],
        "proposal_id": inputs.get("proposal_id"),
        "model": _VERIFIER_MODEL,
        "transport_ok": transport_ok,
        "result": parsed,
        "raw_excerpt": (raw or "")[:600],
    }
    try:
        _VERIFIER_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _VERIFIER_LOG_PATH.open("a") as f:
            f.write(json.dumps(record) + "\n")
    except Exception:
        pass

    if not transport_ok:
        return CapabilityResult(
            ok=False, status="failed_recoverable",
            error={"type": "verifier_transport_failed", "detail": parsed,
                   "model": _VERIFIER_MODEL},
        )

    passed = bool(parsed.get("pass"))
    reason = str(parsed.get("reason", ""))[:1000]

    if not passed:
        return CapabilityResult(
            ok=False, status="failed_permanent",
            error={"type": "semantic_verifier_rejected",
                   "reason": reason,
                   "model": _VERIFIER_MODEL},
        )

    return CapabilityResult(
        ok=True, status="succeeded",
        data={"pass": True, "reason": reason, "model": _VERIFIER_MODEL},
        verification={
            "verified": True,
            "method": "llm_semantic_check",
            "evidence": {"model": _VERIFIER_MODEL, "reason": reason},
        },
        transport_used=f"litellm:{_VERIFIER_MODEL}",
    )


# ─── apply ───────────────────────────────────────────────────────────────────

@capability("self_modify.apply")
def apply(target_root: str, diff: str, **_) -> CapabilityResult:
    """Apply a unified diff inside target_root using `patch -p1`.

    target_root must be in the allowlist (/kai-system or /workspace).
    Returns the list of files the patch touched.
    """
    root = _safe_target_root(target_root)
    if root is None:
        return CapabilityResult(
            ok=False, status="failed_permanent",
            error={"type": "target_root_not_allowed",
                   "given": target_root,
                   "allowlist": list(_TARGET_ROOT_ALLOWLIST)},
        )
    if not diff:
        return CapabilityResult(
            ok=False, status="failed_permanent",
            error={"type": "empty_diff"},
        )

    # Parse target files for evidence (best-effort; doesn't gate apply)
    touched = sorted(set(re.findall(r"^\+\+\+ [ab]/(\S+)", diff, re.MULTILINE)))

    try:
        result = subprocess.run(
            ["patch", "-p1", "--batch", "--forward"],
            cwd=root,
            input=diff,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return CapabilityResult(
            ok=False, status="failed_recoverable",
            error={"type": "patch_timeout"},
        )
    except FileNotFoundError:
        return CapabilityResult(
            ok=False, status="failed_permanent",
            error={"type": "patch_binary_missing"},
        )

    if result.returncode != 0:
        return CapabilityResult(
            ok=False, status="failed_permanent",
            error={"type": "patch_failed",
                   "returncode": result.returncode,
                   "stdout": result.stdout[:1000],
                   "stderr": result.stderr[:1000]},
        )

    return CapabilityResult(
        ok=True, status="succeeded",
        data={"target_root": root,
              "touched_files": touched,
              "patch_stdout": result.stdout[:1000]},
        verification={
            "verified": True,
            "method": "patch_returncode_zero",
            "evidence": {"touched_files": touched, "target_root": root},
        },
        transport_used="patch_p1",
    )


# ─── commit ──────────────────────────────────────────────────────────────────

def _git(args: list[str], cwd: str) -> tuple[int, str, str]:
    r = subprocess.run(["git"] + args, cwd=cwd, capture_output=True,
                       text=True, timeout=15)
    return r.returncode, r.stdout, r.stderr


@capability("self_modify.commit")
def commit(target_root: str, plane_ticket_id: str, gate: str, principle: str,
           retirement: str, touched_files: list[str] | None = None,
           workflow_id: str | None = None, **_) -> CapabilityResult:
    """Stage touched_files (or `-A` if not given) and create a commit at target_root.

    Commit message embeds the full ritual + workflow_id so audit is one-look.
    Does NOT push. Uses git identity from env (commit.author.*) with safe
    fallback so the orchestrator can commit even on a fresh container.
    """
    root = _safe_target_root(target_root)
    if root is None:
        return CapabilityResult(
            ok=False, status="failed_permanent",
            error={"type": "target_root_not_allowed",
                   "given": target_root,
                   "allowlist": list(_TARGET_ROOT_ALLOWLIST)},
        )

    # Ensure we have a git identity inside the container.
    author_name = os.environ.get("SELF_MODIFY_GIT_AUTHOR_NAME", "kai-orchestrator")
    author_email = os.environ.get("SELF_MODIFY_GIT_AUTHOR_EMAIL",
                                  "orchestrator@kai-system.local")

    # Stage
    if touched_files:
        rc, _, err = _git(["add", "--"] + touched_files, root)
    else:
        rc, _, err = _git(["add", "-A"], root)
    if rc != 0:
        return CapabilityResult(
            ok=False, status="failed_recoverable",
            error={"type": "git_add_failed", "stderr": err[:500]},
        )

    # Bail if nothing actually staged (patch may have been a no-op)
    rc, out, _ = _git(["diff", "--cached", "--name-only"], root)
    staged = [l for l in out.splitlines() if l]
    if not staged:
        return CapabilityResult(
            ok=False, status="failed_permanent",
            error={"type": "no_staged_changes",
                   "hint": "patch likely already applied or no-op"},
        )

    body = (
        f"self-modify({plane_ticket_id}): orchestrator workflow {workflow_id or '<n/a>'}\n\n"
        f"gate (§3): {gate}\n"
        f"principle (§5): {principle}\n"
        f"retirement: {retirement}\n\n"
        f"Workflow: devops.self_modify (M2-1.B)\n"
        f"Touched: {', '.join(staged)}\n"
    )

    env = {**os.environ,
           "GIT_AUTHOR_NAME": author_name,
           "GIT_AUTHOR_EMAIL": author_email,
           "GIT_COMMITTER_NAME": author_name,
           "GIT_COMMITTER_EMAIL": author_email}

    r = subprocess.run(
        ["git", "commit", "-m", body],
        cwd=root, capture_output=True, text=True, env=env, timeout=15,
    )
    if r.returncode != 0:
        return CapabilityResult(
            ok=False, status="failed_recoverable",
            error={"type": "git_commit_failed",
                   "stdout": r.stdout[:500],
                   "stderr": r.stderr[:500]},
        )

    rc, sha, _ = _git(["rev-parse", "HEAD"], root)
    sha = sha.strip()

    return CapabilityResult(
        ok=True, status="succeeded",
        data={"commit_sha": sha,
              "target_root": root,
              "staged_files": staged,
              "branch": _git(["rev-parse", "--abbrev-ref", "HEAD"], root)[1].strip()},
        verification={
            "verified": True,
            "method": "git_commit",
            "evidence": {"sha": sha, "files": staged},
        },
        transport_used="git_commit",
    )


# ─── update_plane ────────────────────────────────────────────────────────────

@capability("self_modify.update_plane")
def update_plane(plane_ticket_id: str, commit_sha: str | None = None,
                 workflow_id: str | None = None,
                 target_root: str | None = None,
                 staged_files: list[str] | None = None,
                 verifier_reason: str | None = None,
                 **_) -> CapabilityResult:
    """Append a self-modify trailer to the Plane ticket description.

    Uses the worker-api PATCH /plane/issues/{id} which already handles state
    mapping; we only update description, not state (state change is M2-1.C
    territory — KAI-mediated approval).
    """
    if not plane_ticket_id:
        return CapabilityResult(
            ok=False, status="failed_permanent",
            error={"type": "plane_ticket_id_required"},
        )

    trailer = (
        f"[self-modify trailer] "
        f"workflow_id={workflow_id or '?'} "
        f"commit={commit_sha or '?'} "
        f"target_root={target_root or '?'} "
        f"files={','.join(staged_files or []) or '?'} "
        f"verifier={verifier_reason or 'pass'} "
        f"at={_now_iso()}"
    )

    # Fetch current description, then append, then PATCH.
    try:
        with httpx.Client(timeout=15) as client:
            g = client.get(f"{_WORKER_API_URL}/plane/issues",
                           params={"limit": 500})
    except httpx.RequestError as e:
        return CapabilityResult(
            ok=False, status="failed_recoverable",
            error={"type": "worker_api_unreachable", "detail": str(e)},
        )
    if g.status_code != 200:
        return CapabilityResult(
            ok=False, status="failed_recoverable",
            error={"type": "worker_api_http_error",
                   "status": g.status_code, "body": g.text[:300]},
        )

    issues_doc = g.json()
    matched = None
    project_id = None
    for proj in issues_doc.get("projects", []):
        for i in proj.get("issues", []):
            if i.get("id") == plane_ticket_id:
                matched = i
                project_id = proj.get("id")
                break
        if matched:
            break
    if not matched:
        return CapabilityResult(
            ok=False, status="failed_permanent",
            error={"type": "plane_ticket_not_found",
                   "plane_ticket_id": plane_ticket_id},
        )

    existing_html = matched.get("description_html") or ""
    new_description = f"{existing_html}<p><em>{trailer}</em></p>"

    payload = {"description": new_description}
    if project_id:
        payload["project_id"] = project_id

    try:
        with httpx.Client(timeout=15) as client:
            p = client.patch(
                f"{_WORKER_API_URL}/plane/issues/{plane_ticket_id}",
                json=payload,
            )
    except httpx.RequestError as e:
        return CapabilityResult(
            ok=False, status="failed_recoverable",
            error={"type": "worker_api_unreachable", "detail": str(e)},
        )
    if p.status_code != 200:
        return CapabilityResult(
            ok=False, status="failed_recoverable",
            error={"type": "plane_patch_http_error",
                   "status": p.status_code, "body": p.text[:300]},
        )

    return CapabilityResult(
        ok=True, status="succeeded",
        data={"plane_ticket_id": plane_ticket_id,
              "trailer": trailer,
              "patched": p.json()},
        verification={
            "verified": True,
            "method": "plane_patch_description_append",
            "evidence": {"plane_ticket_id": plane_ticket_id,
                         "commit": commit_sha},
        },
        transport_used="worker_api_patch",
    )
