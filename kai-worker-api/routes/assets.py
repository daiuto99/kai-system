"""Asset delivery from KAI to Leo (Telegram notice), with versioned vault persistence.

Convention:
- Files persist at vault/60_Council/<advisor>/deliverables/<slug>/v<n>.<ext>
- Leo is notified on Telegram with the vault path (using "Beats says:" / "Dev says:" prefix)
"""
import json
import logging
import re
import shutil
from pathlib import Path
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config import VAULT_PATH

logger = logging.getLogger(__name__)
router = APIRouter()

KAI_AVATAR = "https://kai.sonicink.space/avatar-kai.png"

# Advisors whose relay needs a "Beats says:" prefix (anyone not self-posting)
_RELAY_LABELS = {
    "beats": "Beats", "ember": "Ember", "doc": "Doc", "coach": "Coach",
    "creative": "Creative", "tech": "Tech", "dev": "Dev", "ops": "Ops",
    "learning": "Learning", "support": "Support",
}
_SELF_POST = {"kai", "sky", "roads", "devops"}


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

    # The asset is versioned in the vault above; notify Leo on Telegram with the
    # vault path (native Telegram file upload is a separate future build).
    try:
        from tg_alert import tg_alert
        tg_alert(_attribution_text(advisor, req.context or f"new {slug} delivered")
                 + f"\n(vault: {versioned_path})")
    except Exception as e:
        logger.warning("asset telegram notify failed: %s", e)

    return {
        "ok": True,
        "advisor": advisor,
        "slug": slug,
        "version": version,
        "vault_path": str(versioned_path),
        "delivered_via": "telegram_notice",
        "filename": f"{slug}_v{version}.{ext}",
    }


@router.get("/council/advisor/{advisor}/recent_dms")
def get_advisor_recent_dms(advisor: str, n: int = 20):
    """Return recent DM exchanges for Sky/Roads (KAI awareness mechanism)."""
    if ".." in advisor:
        raise HTTPException(404, "Advisor not found")
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
