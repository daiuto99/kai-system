import hashlib
import hmac
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter()

from config import VAULT_PATH, load_secret

GIT_ACTIVITY_FILE = VAULT_PATH / "00_System" / "git_activity.json"
ET = ZoneInfo("America/New_York")
SLACK_CHANNEL = "devops"


def _slack_token() -> str:
    p = Path("/run/secrets/slack_bot_token")
    return p.read_text().strip() if p.exists() else os.environ.get("SLACK_BOT_TOKEN", "")


def _post_slack(text: str):
    # AR-5.3: rerouted to Telegram (sole surface, AR-5). Name kept so call sites
    # stay unchanged; fail-soft via the shared chokepoint.
    from tg_alert import tg_alert
    tg_alert(text)


def _load() -> list:
    if not GIT_ACTIVITY_FILE.exists():
        return []
    try:
        return json.loads(GIT_ACTIVITY_FILE.read_text())
    except Exception:
        return []


def _save(entries: list):
    GIT_ACTIVITY_FILE.parent.mkdir(parents=True, exist_ok=True)
    GIT_ACTIVITY_FILE.write_text(json.dumps(entries, indent=2))


def _record(hash_: str, message: str, branch: str, author: str, repo: str, commit_type: str) -> dict:
    entries = _load()
    short = hash_[:7]

    for entry in entries:
        if entry.get("short_hash") == short or entry.get("hash", "")[:7] == short:
            prev = entry.get("commit_type", "local")
            if prev != "both" and prev != commit_type:
                entry["commit_type"] = "both"
                entry["pushed_at"] = datetime.now(ET).isoformat()
                _save(entries)
                # JARVIS §6: commits are not actionable. Activity is on the dashboard.
                logger.info(f"Commit {short} upgraded to both")
                return {"action": "upgraded"}
            return {"action": "duplicate"}

    now = datetime.now(ET).isoformat()
    entry = {
        "hash": hash_,
        "short_hash": short,
        "message": message,
        "branch": branch,
        "author": author,
        "repo": repo,
        "commit_type": commit_type,
        "committed_at": now,
    }
    if commit_type in ("remote", "both"):
        entry["pushed_at"] = now

    entries.insert(0, entry)
    _save(entries[:100])

    # JARVIS §6: commits are not "auto-fixed" and not "needs-action".
    # Recording to the dashboard is sufficient — no Slack post.
    logger.info(f"Git commit recorded: {short} ({commit_type}) — {message[:60]}")
    return {"action": "recorded"}


# ── REST endpoint (called by post-commit hook) ──────────────────────────────

class CommitPayload(BaseModel):
    hash: str
    short_hash: str | None = None
    message: str
    branch: str = "main"
    author: str = "leo"
    repo: str = "kai-system"
    commit_type: str = "local"


@router.post("/git-activity")
def record_commit(body: CommitPayload):
    result = _record(body.hash, body.message, body.branch, body.author, body.repo, body.commit_type)
    return {"ok": True, **result}


@router.get("/git-activity/latest")
def get_latest(limit: int = 10):
    return {"ok": True, "commits": _load()[:limit]}


# ── GitHub push webhook ─────────────────────────────────────────────────────

@router.post("/github/webhook")
async def github_webhook(request: Request):
    body = await request.body()

    secret = load_secret("github_webhook_secret")
    if not secret:
        raise HTTPException(status_code=503, detail="webhook secret not configured")

    sig = request.headers.get("X-Hub-Signature-256", "")
    if not sig:
        raise HTTPException(status_code=401, detail="Invalid signature")

    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        raise HTTPException(status_code=401, detail="Invalid signature")

    event = request.headers.get("X-GitHub-Event", "")
    if event != "push":
        return {"ok": True, "skipped": f"event={event}"}

    payload = json.loads(body)
    head = payload.get("head_commit")
    if not head:
        return {"ok": True, "skipped": "no head_commit"}

    ref = payload.get("ref", "refs/heads/main")
    branch = ref.replace("refs/heads/", "")
    repo = payload.get("repository", {}).get("name", "unknown")
    result = _record(
        hash_=head.get("id", ""),
        message=head.get("message", "").split("\n")[0],
        branch=branch,
        author=head.get("author", {}).get("name", "unknown"),
        repo=repo,
        commit_type="remote",
    )
    return {"ok": True, **result}
