import json
import logging
import os
import re
import ipaddress
import socket
from datetime import datetime as _datetime
from pathlib import Path
from urllib.parse import urlparse
from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from config import VAULT_PATH
from parking_lot import (
    capture as pl_capture,
    enrich_text,
    gather_capture_context,
    write_capture_card,
    load_secret,
)

logger = logging.getLogger(__name__)
router = APIRouter()

SPRINT_A_ENABLED = os.environ.get("SPRINT_A_INTENT_PIPELINE_ENABLED", "false").lower() == "true"

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
        "project":     meta.get("project", ""),
        "next_action": meta.get("next_action", ""),
        "source":    meta.get("source", ""),
    }


class ParkingLotRequest(BaseModel):
    text: str
    channel_id: str
    thread_ts: str
    user_id: str = ""
    # Origin channel drives the routing_engine private-advisor privacy gate.
    # FAIL-CLOSED default: an unspecified origin is treated as the privacy-restricted
    # remote channel ("telegram"), so private-advisor dispatch is blocked unless the
    # caller declares a trusted origin. (Was hardcoded "slack" — a retired channel that
    # silently bypassed the gate: KAI [BUG][PRIV] 0e6870b7.)
    origin_channel: str = "telegram"


class QuickCaptureRequest(BaseModel):
    text: str


class RouteBody(BaseModel):
    advisor: str


@router.post("/parking-lot/capture")
def parking_lot_capture(req: ParkingLotRequest):
    if SPRINT_A_ENABLED:
        from intent_parser import parse_intent
        from routing_engine import build_dispatch_plan
        from clarification_store import create_pending
        from clarification_surface import ask
        from dispatch import dispatch as _dispatch

        captured_content = gather_capture_context(req.text)
        intent = parse_intent(
            req.text,
            og_title=captured_content.get("og_title", ""),
            og_description=captured_content.get("og_description", ""),
        )
        plan = build_dispatch_plan(intent, origin_channel=req.origin_channel)

        if plan.get("clarifications_needed"):
            entry = create_pending(
                parsed_intent=intent,
                dispatch_plan=plan,
                channel=req.origin_channel,
                origin_chat_id=req.channel_id,
                captured_content=captured_content,
                # slack_thread_ts: retired-Slack-named param that actually stores the
                # message thread ts of any channel (rename deferred, see clarification_store).
                slack_thread_ts=req.thread_ts or None,
            )
            ask(entry["id"])
            return {"status": "pending_clarification", "pending_id": entry["id"]}

        result = _dispatch(plan, captured_content, intent)
        return {"status": "dispatched", **result}

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
    if ".." in slug or "/" in slug:
        raise HTTPException(400, "Invalid slug")
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


class SprintARouteRequest(BaseModel):
    context: str | None = None
    dry_run: bool = False


def _backlog_audit(slug: str, intent: dict, plan: dict):
    """Write a marker row to the dispatch log noting this is a backlog reprocess.

    A reprocess attempt always produces this row + a downstream row from
    dispatch.dispatch (on dispatch path) or no second row (on clarification path,
    until the eventual resolution dispatches and writes its own row).
    """
    log_path = VAULT_PATH / "60_Council" / "sprint_a_dispatch_log.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "ts": _datetime.utcnow().isoformat() + "+00:00",
        "handler": "backlog_reprocess",
        "ok": True,
        "error": None,
        "target": plan.get("target", {}),
        "action": intent.get("action"),
        "summary": f"reprocess attempt slug={slug} planned_handler={plan.get('handler')}",
    }
    with open(log_path, "a") as f:
        f.write(json.dumps(row) + "\n")


@router.post("/parking-lot/{slug}/sprint-a-route")
def parking_lot_sprint_a_route(slug: str, req: SprintARouteRequest):
    """Re-run an existing backlog card through the Sprint A intent pipeline.

    Combines optional user-supplied context with the card's title/summary/url,
    runs intent_parser -> dispatch_plan -> clarification|dispatch. On dispatch
    success the original card is archived; on clarification it stays put until
    resolved.
    """
    if not SPRINT_A_ENABLED:
        raise HTTPException(503, "Sprint A pipeline disabled")

    path = LOT_DIR / f"{slug}.md"
    if not path.exists():
        raise HTTPException(404, "Capture not found")

    from intent_parser import parse_intent
    from routing_engine import build_dispatch_plan
    from clarification_store import create_pending
    from clarification_surface import ask
    from dispatch import dispatch as _dispatch

    card = _parse_card(path)

    parts = []
    if req.context:
        parts.append(req.context.strip())
    title = card.get("title", "") or ""
    summary = card.get("summary", "") or ""
    url = card.get("url", "") or ""
    if title:
        parts.append(title)
    if summary and summary != title:
        parts.append(summary)
    if url:
        parts.append(url)
    text = "\n".join(p for p in parts if p).strip()

    if not text:
        raise HTTPException(400, "Card has no content to reprocess")

    captured_content = {
        "original_message": text,
        "url": url or None,
        "og_title": title,
        "og_description": summary,
    }

    intent = parse_intent(
        text,
        og_title=title,
        og_description=summary,
    )
    # Backlog reprocess is an internal dashboard-originated action (Leo reprocessing a
    # saved card), not a remote message channel — a trusted "web" origin. (Was hardcoded
    # "slack" — retired: KAI [BUG][PRIV] 0e6870b7.)
    plan = build_dispatch_plan(intent, origin_channel="web")

    _backlog_audit(slug, intent, plan)

    if req.dry_run:
        return {
            "status": "dry_run",
            "slug": slug,
            "text": text,
            "intent": intent,
            "plan": plan,
        }

    if plan.get("clarifications_needed"):
        entry = create_pending(
            parsed_intent=intent,
            dispatch_plan=plan,
            channel="web",
            origin_chat_id=f"backlog:{slug}",
            captured_content=captured_content,
        )
        ask(entry["id"])
        return {
            "status": "pending_clarification",
            "pending_id": entry["id"],
            "slug": slug,
            "archived": False,
        }

    result = _dispatch(plan, captured_content, intent)

    archived = False
    if result.get("ok"):
        ARCH_DIR.mkdir(parents=True, exist_ok=True)
        path.rename(ARCH_DIR / path.name)
        archived = True

    return {"status": "dispatched", "slug": slug, "archived": archived, **result}


class AnnotateRequest(BaseModel):
    note: str


@router.post("/parking-lot/{slug}/annotate")
def parking_lot_annotate(slug: str, req: AnnotateRequest):
    """Append a timestamped context note to a backlog card without dispatching.

    The note is appended as a markdown blockquote at the end of the card body.
    Card stays in /vault/50_ParkingLot/. No archive, no dispatch, no Slack noise.
    Use this when Leo wants to add context to an existing card while triaging.
    """
    path = LOT_DIR / f"{slug}.md"
    if not path.exists():
        raise HTTPException(404, "Capture not found")
    note = (req.note or "").strip()
    if not note:
        raise HTTPException(400, "Empty note")
    ts = _datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    block = f"\n\n> **Note ({ts}):** {note}\n"
    path.write_text(path.read_text() + block, encoding="utf-8")
    return {"ok": True, "slug": slug, "note_added": note, "ts": ts}


class TriageRequest(BaseModel):
    action: str
    advisor: str = "kai"
    notes: str = ""


@router.post("/parking-lot/{slug}/triage")
def parking_lot_triage(slug: str, req: TriageRequest):
    """Dispatch a lot item to its exit path."""
    import httpx as _hx
    path = LOT_DIR / f"{slug}.md"
    if not path.exists():
        raise HTTPException(404, "Not found")

    item_text = path.read_text()
    card = _parse_card(path)
    title   = card.get("title") or slug
    summary = card.get("summary") or ""
    url     = card.get("url") or ""
    tags    = card.get("tags") or []
    context_note = req.notes or card.get("why_saved") or card.get("next_action") or ""

    action = req.action.lower().strip()

    if action == "task":
        from services.todoist import create_task as _create_task
        desc_parts = []
        if summary: desc_parts.append(summary)
        if url: desc_parts.append(url)
        if context_note: desc_parts.append(context_note)
        _create_task(title, description="\n".join(desc_parts))
        # Mark item as triaged
        _update_field(path, "status", "triaged")
        _update_field(path, "next_action", "→ Todoist task created")
        path.rename(ARCH_DIR / path.name)
        return {"ok": True, "action": "task", "title": title}

    elif action == "project":
        # Write to inbox so KAI creates the project via the intake pipeline
        _write_to_inbox(slug, title, summary, url, context_note, route="project", action="create_project")
        path.rename(ARCH_DIR / path.name)
        return {"ok": True, "action": "project", "title": title}

    elif action == "knowledge":
        advisor = req.advisor if req.advisor in {"doc","sky","roads","beats","creative","dev","kai"} else "kai"
        _write_to_inbox(slug, title, summary, url, context_note, route=advisor, action="ingest")
        path.rename(ARCH_DIR / path.name)
        return {"ok": True, "action": "knowledge", "advisor": advisor, "title": title}

    elif action == "archive":
        ARCH_DIR.mkdir(parents=True, exist_ok=True)
        path.rename(ARCH_DIR / path.name)
        return {"ok": True, "action": "archive", "title": title}

    elif action == "defer":
        _update_field(path, "status", "waiting")
        return {"ok": True, "action": "defer", "title": title}

    else:
        raise HTTPException(400, f"Unknown action: {action}")


def _update_field(path, key, value):
    text = path.read_text()
    pattern = rf"^{re.escape(key)}:.*$"
    if re.search(pattern, text, re.MULTILINE):
        text = re.sub(pattern, f"{key}: {value}", text, flags=re.MULTILINE)
    else:
        lines = text.splitlines()
        for i, line in enumerate(lines):
            if i > 0 and line.strip() == "---":
                lines.insert(i, f"{key}: {value}")
                break
        text = "\n".join(lines)
    path.write_text(text)


def _write_to_inbox(slug, title, summary, url, context_note, route, action):
    inbox_dir = VAULT_PATH / "50_Inbox"
    inbox_dir.mkdir(parents=True, exist_ok=True)
    filename = f"lot-{slug}.md"
    body_parts = [
        f"# {title}",
        "\n> IMPORTANT: The summary below was pre-captured at save time. Work from this content only. Do NOT attempt to fetch or visit the URL — it may be paywalled, require authentication, or block bots.",
    ]
    if summary: body_parts.append(f"\n## Captured Summary\n\n{summary}")
    if url: body_parts.append(f"\nOriginal source (do not fetch): {url}")
    if context_note: body_parts.append(f"\nLeo's notes: {context_note}")
    content = (
        f"---\nroute: {route}\naction: {action}\n"
        f"context: From Parking Lot — {title}\n---\n\n"
        + "\n".join(body_parts)
    )
    (inbox_dir / filename).write_text(content)

@router.post("/parking-lot/resolve-urls")
def parking_lot_resolve_urls(background_tasks: BackgroundTasks):
    background_tasks.add_task(_resolve_existing_urls)
    return {"status": "resolving in background"}


def _resolve_existing_urls():
    from parking_lot import resolve_url
    import re as _re
    updated = 0
    for path in LOT_DIR.glob("*.md"):
        text = path.read_text()
        m = _re.search(r'^url:\s*(.+)$', text, _re.MULTILINE)
        if not m:
            continue
        stored_url = m.group(1).strip()
        if not stored_url or 'share.google' not in stored_url:
            continue
        resolved = resolve_url(stored_url)
        if resolved != stored_url and 'share.google' not in resolved:
            new_text = _re.sub(r'^url:\s*.+$', f'url: {resolved}', text, flags=_re.MULTILINE)
            path.write_text(new_text)
            logger.info("resolved %s -> %s", stored_url[:50], resolved[:60])
            updated += 1
    logger.info("resolve-urls: updated %d items", updated)


@router.post("/parking-lot/reenrich-bad")
def parking_lot_reenrich_bad(background_tasks: BackgroundTasks):
    """Re-enrich items whose summary is empty or is a raw URL (failed first-pass enrichment)."""
    LOT_DIR.mkdir(parents=True, exist_ok=True)
    queued = 0
    for f in LOT_DIR.glob("*.md"):
        try:
            card = _parse_card(f)
            summary = (card.get("summary") or "").strip()
            url = (card.get("url") or "").strip()
            bad_summary = (not summary) or bool(re.match(r'^https?://', summary))
            has_real_url = url and "share.google" not in url
            if bad_summary and has_real_url:
                background_tasks.add_task(_enrich_item, f, url)
                queued += 1
        except Exception:
            pass
    return {"ok": True, "queued": queued}
