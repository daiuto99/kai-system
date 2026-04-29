import json
import logging
import re
import ipaddress
import socket
from datetime import datetime as _datetime
from pathlib import Path
from urllib.parse import urlparse
from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from config import VAULT_PATH
from parking_lot import capture as pl_capture, enrich_text, write_capture_card, load_secret

logger = logging.getLogger(__name__)
router = APIRouter()

LOT_DIR  = VAULT_PATH / "50_ParkingLot"
ARCH_DIR = LOT_DIR / "archived"

ADVISORS = ["kai", "beats", "creative", "dev", "sky", "roads"]

EDITABLE_FIELDS = {"title", "status", "intent", "why_saved", "project", "next_action"}


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

    fm_title = meta.get("title", "")
    body_title = ""
    for line in content_lines:
        if line.startswith("# "):
            body_title = line[2:].strip(); break
    is_url_title = fm_title.startswith("http") or fm_title.startswith("share.")
    title = (fm_title if fm_title and not is_url_title else None) or \
            (body_title if body_title and not body_title.startswith("http") else None) or \
            fm_title or body_title or path.stem

    summary = ""
    past = False
    for line in content_lines:
        if line.startswith("# "): past = True; continue
        if past and line.strip() and not line.startswith("#"):
            summary = line.strip(); break

    url = meta.get("url", "")
    if not url:
        urls = re.findall(r"<(https?://[^>]+)>", content) or re.findall(r"https?://\S+", content)
        url = urls[0] if urls else ""

    raw_tags = meta.get("tags", "")
    tags = [t.strip() for t in raw_tags.split(",") if t.strip()] if raw_tags else []

    raw_status = meta.get("status", "new")
    status = "new" if raw_status == "captured" else raw_status

    return {
        "slug":      path.stem,
        "title":     title or path.stem,
        "type":      meta.get("type", "item"),
        "date":      meta.get("date", ""),
        "status":    status,
        "summary":   summary,
        "url":       url,
        "image":     meta.get("image", ""),
        "tags":      tags,
        "enriched":  bool(meta.get("image") or meta.get("tags")),
        "intent":    meta.get("intent", ""),
        "why_saved": meta.get("why_saved", ""),
        "project":   meta.get("project", ""),
        "source":    meta.get("source", ""),
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
def parking_lot_quick(req: QuickCaptureRequest, background_tasks: BackgroundTasks):
    slug = _datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    LOT_DIR.mkdir(parents=True, exist_ok=True)

    urls = re.findall(r"https?://\S+", req.text)
    raw_url = urls[0].rstrip(">).,") if urls else ""
    item_type = "link" if raw_url else "note"
    stub = f"""---
title: {req.text[:80]}
date: {_datetime.utcnow().strftime("%Y-%m-%d")}
type: {item_type}
status: new
source: web
url: {raw_url}
image:
tags:
intent:
why_saved:
project:
---

# {req.text[:80]}

{req.text}
"""
    path = LOT_DIR / f"{slug}.md"
    path.write_text(stub, encoding="utf-8")

    background_tasks.add_task(_enrich_item, path, req.text)
    return {"ok": True, "slug": slug}


def _enrich_item(path: Path, original_text: str):
    try:
        api_key = load_secret("anthropic_api_key")
        classification, og, real_url = enrich_text(original_text, api_key)

        tags_str = ", ".join(classification.get("tags", []))
        text = path.read_text()

        def fm_set(key, value):
            nonlocal text
            pattern = rf"^{key}:.*$"
            replacement = f"{key}: {value}"
            if re.search(pattern, text, re.MULTILINE):
                text = re.sub(pattern, replacement, text, flags=re.MULTILINE)
            else:
                text = text.replace("---\n\n#", f"{key}: {value}\n---\n\n#", 1)

        fm_set("title", classification["title"][:80])
        fm_set("type", classification["type"])
        fm_set("url", real_url or "")
        fm_set("image", og.get("og_image", ""))
        fm_set("tags", tags_str)

        lines = text.splitlines()
        new_lines = []
        past_title = False
        summary_replaced = False
        for line in lines:
            if not past_title and line.startswith("# "):
                past_title = True
                new_lines.append(line)
                continue
            if past_title and not summary_replaced and line.strip() and not line.startswith("#"):
                new_lines.append(classification["summary"])
                summary_replaced = True
                continue
            new_lines.append(line)
        text = "\n".join(new_lines)

        path.write_text(text, encoding="utf-8")
        logger.info("enriched: %s", path.name)
    except Exception as e:
        logger.exception("enrich_item failed for %s: %s", path.name, e)


@router.post("/parking-lot/enrich-all")
def parking_lot_enrich_all(background_tasks: BackgroundTasks):
    LOT_DIR.mkdir(parents=True, exist_ok=True)
    queued = 0
    for f in LOT_DIR.glob("*.md"):
        try:
            card = _parse_card(f)
            if not card.get("enriched"):
                original_text = card.get("url") or card.get("title") or f.stem
                background_tasks.add_task(_enrich_item, f, original_text)
                queued += 1
        except Exception:
            pass
    return {"ok": True, "queued": queued}


@router.get("/parking-lot/og")
def parking_lot_og_image(url: str):
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
    path = LOT_DIR / f"{slug}.md"
    if not path.exists():
        raise HTTPException(404, "Not found")
    text = path.read_text()
    for key, raw in body.items():
        if key not in EDITABLE_FIELDS:
            continue
        value = str(raw).strip()
        pattern = rf"^{re.escape(key)}:.*$"
        if re.search(pattern, text, re.MULTILINE):
            text = re.sub(pattern, f"{key}: {value}", text, flags=re.MULTILINE)
        else:
            lines = text.splitlines()
            fm_end = None
            for i, line in enumerate(lines):
                if i > 0 and line.strip() == "---":
                    fm_end = i
                    break
            if fm_end is not None:
                lines.insert(fm_end, f"{key}: {value}")
                text = "\n".join(lines)
    path.write_text(text)
    return {"ok": True}


@router.delete("/parking-lot/{slug}")
def parking_lot_delete(slug: str):
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
