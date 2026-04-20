import json
import logging
import re
import ipaddress
import socket
from datetime import datetime as _datetime
from pathlib import Path
from urllib.parse import urlparse
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from config import VAULT_PATH
from parking_lot import capture as pl_capture

logger = logging.getLogger(__name__)
router = APIRouter()

LOT_DIR  = VAULT_PATH / "50_ParkingLot"
ARCH_DIR = LOT_DIR / "archived"


def _is_safe_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        host = parsed.hostname
        ip = ipaddress.ip_address(socket.gethostbyname(host))
        return not (ip.is_private or ip.is_loopback or ip.is_link_local)
    except Exception:
        return False


def _parse_card(path: Path) -> dict:
    text = path.read_text()
    meta, content_lines, in_fm, fm_done = {}, [], False, False
    for i, line in enumerate(text.strip().splitlines()):
        if i == 0 and line == "---":
            in_fm = True; continue
        if in_fm and line == "---":
            in_fm = False; fm_done = True; continue
        if in_fm:
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip()
        else:
            content_lines.append(line)

    content = "\n".join(content_lines)
    title = meta.get("title", "")
    for line in content_lines:
        if line.startswith("# "):
            title = line[2:].strip(); break

    summary = ""
    past = False
    for line in content_lines:
        if line.startswith("# "): past = True; continue
        if past and line.strip() and not line.startswith("#"):
            summary = line.strip(); break

    urls = re.findall(r"<(https?://[^>]+)>", content)
    return {
        "slug":    path.stem,
        "title":   title or path.stem,
        "type":    meta.get("type", "item"),
        "date":    meta.get("date", ""),
        "status":  meta.get("status", "captured"),
        "summary": summary,
        "url":     urls[0] if urls else "",
    }


class ParkingLotRequest(BaseModel):
    text: str
    channel_id: str
    thread_ts: str
    user_id: str = ""


class QuickCaptureRequest(BaseModel):
    text: str


class RouteBody(BaseModel):
    advisor: str


@router.post("/parking-lot/capture")
def parking_lot_capture(req: ParkingLotRequest):
    result = pl_capture(req.text, req.channel_id, req.thread_ts, req.user_id)
    return result


@router.post("/parking-lot/quick")
def parking_lot_quick(req: QuickCaptureRequest):
    """Quick capture from web UI — no Slack context needed."""
    slug = _datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    LOT_DIR.mkdir(parents=True, exist_ok=True)
    urls = re.findall(r"https?://\S+", req.text)
    item_type = "link" if urls else "note"
    content = f"""---
title: {req.text[:60]}
date: {_datetime.utcnow().strftime("%Y-%m-%d")}
type: {item_type}
status: captured
source: web
---

# {req.text[:60]}

{req.text}
"""
    (LOT_DIR / f"{slug}.md").write_text(content, encoding="utf-8")
    return {"ok": True, "slug": slug}


@router.get("/parking-lot/og")
def parking_lot_og_image(url: str):
    """Fetch OG image URL for a given URL (for Lot thumbnails)."""
    import httpx
    if not _is_safe_url(url):
        raise HTTPException(400, "URL not allowed")
    try:
        headers = {"User-Agent": "Mozilla/5.0 Twitterbot/1.0"}
        with httpx.Client(timeout=5, follow_redirects=True) as client:
            r = client.get(url, headers=headers)
        html = r.text[:80000]
        m = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', html, re.I)
        if not m:
            m = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']', html, re.I)
        if m:
            img = m.group(1)
            if img.startswith("/"):
                p = urlparse(url)
                img = f"{p.scheme}://{p.netloc}{img}"
            return {"image": img}
        return {"image": ""}
    except Exception as e:
        logger.exception("og image fetch: %s", e)
        return {"image": ""}


@router.get("/parking-lot/list")
def parking_lot_list():
    LOT_DIR.mkdir(parents=True, exist_ok=True)
    items = []
    for f in sorted(LOT_DIR.glob("*.md"), reverse=True):
        try:
            items.append(_parse_card(f))
        except Exception as e:
            logger.exception("parse card error: %s", e)
    return {"items": items, "count": len(items)}


@router.patch("/parking-lot/{slug}")
def parking_lot_edit(slug: str, body: dict):
    """Edit a lot item's title."""
    path = LOT_DIR / f"{slug}.md"
    if not path.exists():
        raise HTTPException(404, "Not found")
    text = path.read_text()
    new_title = body.get("title", "").strip()
    if new_title:
        if re.search(r'^title:', text, re.MULTILINE):
            text = re.sub(r'^title:.*$', f'title: {new_title}', text, flags=re.MULTILINE)
        path.write_text(text)
    return {"ok": True}


@router.delete("/parking-lot/{slug}")
def parking_lot_delete(slug: str):
    """Permanently delete a lot item."""
    path = LOT_DIR / f"{slug}.md"
    if path.exists():
        path.unlink()
    return {"ok": True}


@router.post("/parking-lot/{slug}/route")
def parking_lot_route(slug: str, body: RouteBody):
    path = LOT_DIR / f"{slug}.md"
    if not path.exists():
        raise HTTPException(404, "Capture not found")
    ARCH_DIR.mkdir(parents=True, exist_ok=True)
    dest = ARCH_DIR / f"{slug}.md"
    dest.write_text(path.read_text() + f"\n\n<!-- Routed to #{body.advisor} -->")
    path.unlink()
    return {"ok": True, "routed_to": body.advisor}


@router.post("/parking-lot/{slug}/archive")
def parking_lot_archive(slug: str):
    path = LOT_DIR / f"{slug}.md"
    if not path.exists():
        raise HTTPException(404, "Capture not found")
    ARCH_DIR.mkdir(parents=True, exist_ok=True)
    path.rename(ARCH_DIR / path.name)
    return {"ok": True}
