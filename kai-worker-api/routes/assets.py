"""Asset delivery from KAI to Leo's Slack DM, with versioned vault persistence.

Convention (Slack Sprint Task 4):
- Files persist at vault/60_Council/<advisor>/deliverables/<slug>/v<n>.<ext>
- Latest version is DM'd to Leo via KAI bot (using "Beats says:" / "Dev says:" prefix)
- Filename in DM: <slug>_v<n>.<ext>
- All KAI's posts use the dashboard avatar
"""
import json
import logging
import os
import re
import shutil
from pathlib import Path
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import httpx

from config import VAULT_PATH

logger = logging.getLogger(__name__)
router = APIRouter()

LEO_USER_ID = os.environ.get("LEO_USER_ID", "U0AG93XJ927")
KAI_AVATAR = "https://kai.sonicink.space/avatar-kai.png"

# Advisors whose Slack relay needs a "Beats says:" prefix (anyone not self-posting)
_RELAY_LABELS = {
    "beats": "Beats", "ember": "Ember", "doc": "Doc", "coach": "Coach",
    "creative": "Creative", "tech": "Tech", "dev": "Dev", "ops": "Ops",
    "learning": "Learning", "support": "Support",
}
_SELF_POST = {"kai", "sky", "roads", "devops"}


def _slack_token() -> str:
    p = Path("/run/secrets/slack_bot_token")
    return p.read_text().strip() if p.exists() else os.environ.get("SLACK_BOT_TOKEN", "")


def _slugify(value: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return s or "asset"


def _next_version(asset_dir: Path, ext: str) -> int:
    if not asset_dir.exists():
        return 1
    used = []
    for f in asset_dir.iterdir():
        m = re.match(r"^v(\d+)\." + re.escape(ext) + r"$", f.name)
        if m:
            used.append(int(m.group(1)))
    return (max(used) + 1) if used else 1


def _resolve_leo_dm_channel(token: str) -> str | None:
    try:
        r = httpx.post(
            "https://slack.com/api/conversations.open",
            headers={"Authorization": f"Bearer {token}"},
            json={"users": LEO_USER_ID},
            timeout=10,
        )
        d = r.json()
        if d.get("ok"):
            return d["channel"]["id"]
        logger.warning("conversations.open failed: %s", d.get("error"))
    except Exception as e:
        logger.exception("conversations.open: %s", e)
    return None


def _attribution_text(advisor: str, context: str) -> str:
    if advisor in _SELF_POST:
        return context
    label = _RELAY_LABELS.get(advisor, advisor.capitalize())
    return f"{label} says:\n{context}"


class DeliverAssetRequest(BaseModel):
    advisor: str
    context: str
    source_path: str   # absolute path on worker, or vault-relative
    slug: str = ""     # auto-generated from context if omitted
    ext: str = ""      # inferred from source if omitted


@router.post("/assets/deliver")
def deliver_asset(req: DeliverAssetRequest):
    """Persist + DM an asset to Leo. See module docstring for convention."""
    src = Path(req.source_path)
    if not src.is_absolute():
        src = VAULT_PATH / src
    if not src.exists() or not src.is_file():
        raise HTTPException(404, f"source file not found: {src}")

    advisor = (req.advisor or "kai").lower()
    ext = (req.ext or src.suffix.lstrip(".")).lower() or "bin"
    slug = _slugify(req.slug or src.stem)

    asset_dir = VAULT_PATH / "60_Council" / advisor / "deliverables" / slug
    asset_dir.mkdir(parents=True, exist_ok=True)
    version = _next_version(asset_dir, ext)
    versioned_path = asset_dir / f"v{version}.{ext}"
    shutil.copy2(src, versioned_path)

    token = _slack_token()
    if not token:
        return {"ok": False, "error": "Slack token not configured",
                "vault_path": str(versioned_path), "version": version}

    dm_channel = _resolve_leo_dm_channel(token)
    if not dm_channel:
        return {"ok": False, "error": "could not resolve Leo DM channel",
                "vault_path": str(versioned_path), "version": version}

    text = _attribution_text(advisor, req.context or f"new {slug} delivered")
    msg_resp = httpx.post(
        "https://slack.com/api/chat.postMessage",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "channel": dm_channel, "text": text,
            "username": "KAI", "icon_url": KAI_AVATAR,
        },
        timeout=15,
    ).json()
    if not msg_resp.get("ok"):
        return {"ok": False, "error": f"chat.postMessage: {msg_resp.get('error')}",
                "vault_path": str(versioned_path), "version": version}
    thread_ts = msg_resp.get("ts")

    # Slack files.upload_v2 is a 3-step flow (getUploadURLExternal → PUT → completeUploadExternal).
    # files.upload (legacy) is simpler and still works for small files; use that.
    with open(versioned_path, "rb") as fh:
        upload = httpx.post(
            "https://slack.com/api/files.upload",
            headers={"Authorization": f"Bearer {token}"},
            data={
                "channels": dm_channel,
                "thread_ts": thread_ts,
                "filename": f"{slug}_v{version}.{ext}",
                "title": f"{slug}_v{version}",
            },
            files={"file": fh},
            timeout=60,
        ).json()
    if not upload.get("ok"):
        return {"ok": False, "error": f"files.upload: {upload.get('error')}",
                "vault_path": str(versioned_path), "version": version, "slack_ts": thread_ts}

    return {
        "ok": True,
        "advisor": advisor,
        "slug": slug,
        "version": version,
        "vault_path": str(versioned_path),
        "slack_ts": thread_ts,
        "filename": f"{slug}_v{version}.{ext}",
    }


@router.get("/council/advisor/{advisor}/recent_dms")
def get_advisor_recent_dms(advisor: str, n: int = 20):
    """Return recent DM exchanges for Sky/Roads (KAI awareness mechanism)."""
    advisor = advisor.lower()
    log_file = VAULT_PATH / "60_Council" / advisor / "dm_log.jsonl"
    if not log_file.exists():
        return {"advisor": advisor, "count": 0, "exchanges": []}
    lines = log_file.read_text().splitlines()
    tail = lines[-n:] if len(lines) > n else lines
    exchanges = []
    for ln in tail:
        try:
            exchanges.append(json.loads(ln))
        except Exception:
            continue
    return {"advisor": advisor, "count": len(exchanges), "exchanges": exchanges}
