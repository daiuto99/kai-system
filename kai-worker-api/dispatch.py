"""Dispatch handlers for Sprint A Slice 3.

Takes a dispatch_plan from routing_engine and actually does the thing. Pure
side-effects: calls the council API for advisor work, calls WordPress for blog
drafts, writes vault files for recipes. Does NOT post replies back to the
origin channel — the caller (Slack interactions handler, dashboard, etc.)
owns that step.

Result shape:
    {
        "ok":      bool,
        "handler": "<plan.handler>",
        "summary": "<one-line, suitable for a thread reply>",
        "details": {...},     # handler-specific, e.g. {"wp_draft_url": "..."}
        "error":   "<msg>" | None,
    }

Every call appends a row to vault/60_Council/sprint_a_dispatch_log.jsonl.
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

COUNCIL_API = os.environ.get("COUNCIL_API_URL", "http://kai-council-api:8002")
WORKER_API = os.environ.get("WORKER_API_URL", "http://kai-worker-api:8001")

VAULT_PATH = Path("/vault")
DISPATCH_LOG = VAULT_PATH / "60_Council" / "sprint_a_dispatch_log.jsonl"
RECIPE_INBOX = VAULT_PATH / "50_Inbox" / "recipes"

# Council request budget. The council router does its own rate-limiting and
# auto-capture short-circuiting, so 90s is comfortable for an advisor reply.
COUNCIL_TIMEOUT_S = 90


class DispatchError(Exception):
    pass


def dispatch(
    plan: dict,
    captured_content: dict,
    parsed_intent: dict,
    *,
    council_client: httpx.Client | None = None,
    worker_client: httpx.Client | None = None,
) -> dict:
    """Execute a dispatch_plan. See module docstring for result shape.

    Args:
        plan: a fully-resolved dispatch_plan (ok_to_dispatch=True, no pending
              clarifications).
        captured_content: {"original_message", "url", "og_title", "og_description"}.
        parsed_intent: the upstream parsed intent (action / instructions / etc).
        council_client / worker_client: injectable httpx clients for tests.
    """
    if not plan.get("ok_to_dispatch"):
        return _result(
            ok=False, handler=plan.get("handler"),
            summary="Plan is not ready to dispatch.",
            details={"blocked_reason": plan.get("blocked_reason"),
                     "clarifications_needed": plan.get("clarifications_needed", [])},
            error="plan_not_dispatchable",
        )

    handler = plan.get("handler", "capture")
    try:
        if handler == "capture":
            result = _dispatch_capture(plan, captured_content, parsed_intent)
        elif handler == "share":
            result = _dispatch_share(plan, captured_content, parsed_intent,
                                     client=council_client)
        elif handler == "summarize":
            result = _dispatch_summarize(plan, captured_content, parsed_intent,
                                         client=council_client)
        elif handler == "blog_post":
            result = _dispatch_blog_post(plan, captured_content, parsed_intent,
                                         council_client=council_client,
                                         worker_client=worker_client)
        elif handler == "recipe":
            result = _dispatch_recipe(plan, captured_content, parsed_intent)
        elif handler == "forward_summary":
            result = _dispatch_forward_summary(plan, captured_content, parsed_intent,
                                               client=council_client)
        else:
            result = _result(
                ok=False, handler=handler,
                summary=f"Unknown handler '{handler}'.",
                details={}, error="unknown_handler",
            )
    except Exception as e:
        logger.exception("dispatch handler %s failed: %s", handler, e)
        result = _result(
            ok=False, handler=handler,
            summary=f"Dispatch failed: {e}",
            details={}, error=str(e),
        )

    _log_dispatch(plan, parsed_intent, result)
    return result


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def _dispatch_capture(plan: dict, content: dict, intent: dict) -> dict:
    # Capture is the legacy path. Slice 3 does not re-implement it; we surface
    # the no-op result and rely on the caller (or Slice 5 cutover) to actually
    # invoke parking_lot.capture(). Keeping it side-effect-free here keeps
    # the dispatcher safe to call from tests.
    return _result(
        ok=True, handler="capture",
        summary="Captured to parking lot.",
        details={"note": "legacy parking_lot.capture() is invoked by caller"},
    )


def _dispatch_share(plan: dict, content: dict, intent: dict, *,
                    client: httpx.Client | None) -> dict:
    advisor = plan["target"].get("advisor")
    if not advisor:
        return _result(ok=False, handler="share",
                       summary="No advisor on plan.", details={},
                       error="missing_advisor")
    prompt = _share_prompt(content, intent, advisor)
    reply = _call_council(advisor, prompt, client=client)
    reply_text = reply.get("reply", "") or ""

    title = (content.get("og_title") or "").strip() or (content.get("original_message") or "(untitled)")[:80]
    note_path = _write_advisor_knowledge(advisor, content, reply_text, intent)
    chunks = _ingest_into_advisor(advisor, note_path,
                                  title=title,
                                  source_url=content.get("url") or "")
    vault_rel = str(note_path).replace("/vault/", "vault/")

    terse = f"Sent to {advisor} · added to {advisor} knowledge ({chunks} chunks indexed)"
    return _result(
        ok=True, handler="share",
        summary=terse,
        details={"advisor": advisor, "reply": reply_text,
                 "note_path": vault_rel, "ingest_chunks": chunks,
                 "usage": reply.get("usage", {})},
    )


def _short_summary_id() -> str:
    """4-char base32 id like S-7K3M, easy to read back to KAI from a Slack message."""
    import secrets, string
    alphabet = string.ascii_uppercase + "2345679"  # no 0/1/8/I/O — readability
    return "S-" + "".join(secrets.choice(alphabet) for _ in range(4))


def _slugify(text: str, n: int = 40) -> str:
    import re as _re
    s = _re.sub(r"[^a-zA-Z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:n] or "note"


def _write_summary_md(content: dict, summary_text: str, intent: dict) -> tuple[Path, str]:
    """Write a neutral summary md to vault/40_Summaries/. Returns (path, id)."""
    from datetime import datetime, timezone

    sid = _short_summary_id()
    title = (content.get("og_title") or "").strip()
    if not title:
        first = (content.get("original_message") or "").splitlines()[:1]
        title = (first[0] if first else "untitled").strip()[:80] or "untitled"
    slug = _slugify(title)
    folder = VAULT_PATH / "40_Summaries"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{sid}_{slug}.md"

    url = content.get("url") or ""
    instructions = (intent.get("instructions") or "").strip()
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    fm = (
        "---\n"
        f"id: {sid}\n"
        f"title: {title}\n"
        f"date: {ts[:8]}\n"
        f"source_url: {url}\n"
        f"generated_by: kai\n"
        f"instructions: {instructions}\n"
        "---\n\n"
        f"# {title}\n\n"
        f"{summary_text.strip()}\n"
    )
    path.write_text(fm, encoding="utf-8")
    return path, sid


def _update_last_summary(channel: str, chat_id: str | None, sid: str, path: Path) -> None:
    """Maintain vault/00_System/last_summaries.json so 'send this summary' resolves."""
    import json as _json
    store = VAULT_PATH / "00_System" / "last_summaries.json"
    store.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = _json.loads(store.read_text()) if store.exists() else {}
    except Exception:
        data = {}
    key = f"{channel or 'unknown'}:{chat_id or 'unknown'}"
    data[key] = {"id": sid, "path": str(path).replace("/vault/", "vault/")}
    # Also a global most-recent so backlog reprocess (no chat_id) still resolves
    data["_latest"] = data[key]
    store.write_text(_json.dumps(data, indent=2))


def _dispatch_summarize(plan: dict, content: dict, intent: dict, *,
                        client: httpx.Client | None) -> dict:
    """Summarize via KAI. Writes neutral md to vault/40_Summaries/, terse Slack notice."""
    advisor = plan["target"].get("advisor") or "kai"
    prompt = _summarize_prompt(content, intent)
    reply = _call_council(advisor, prompt, client=client)
    reply_text = reply.get("reply", "") or ""

    path, sid = _write_summary_md(content, reply_text, intent)
    vault_rel = str(path).replace("/vault/", "vault/")

    origin_channel = plan.get("origin_channel") or "unknown"
    origin_chat_id = plan.get("origin_chat_id")
    _update_last_summary(origin_channel, origin_chat_id, sid, path)

    title = (content.get("og_title") or "").strip() or "(untitled)"
    terse = f"Summary {sid} of '{title}' is available · vault/40_Summaries/"

    return _result(
        ok=True, handler="summarize",
        summary=terse,
        details={"advisor": advisor, "summary_id": sid, "note_path": vault_rel,
                 "reply": reply_text, "usage": reply.get("usage", {})},
    )


def _write_advisor_knowledge(advisor: str, content: dict, reply_text: str,
                              intent: dict) -> Path:
    """Write source + advisor take to vault/60_Council/<advisor>/knowledge/."""
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    title = (content.get("og_title") or "").strip()
    if not title:
        first = (content.get("original_message") or "").splitlines()[:1]
        title = (first[0] if first else "untitled").strip()[:80] or "untitled"
    slug = _slugify(title)
    folder = VAULT_PATH / "60_Council" / advisor / "knowledge"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{ts}_{slug}.md"

    url = content.get("url") or ""
    og_desc = content.get("og_description") or ""
    original = content.get("original_message") or ""
    instructions = (intent.get("instructions") or "").strip()
    fm = (
        "---\n"
        f"title: {title}\n"
        f"date: {ts[:8]}\n"
        f"advisor: {advisor}\n"
        f"source_url: {url}\n"
        f"generated_by: sprint_a_share\n"
        f"instructions: {instructions}\n"
        "---\n\n"
        f"# {title}\n\n"
        "## Source\n\n"
        f"{og_desc}\n\n"
        f"Original message: {original}\n\n"
        f"## {advisor.capitalize()}'s Take\n\n"
        f"{reply_text.strip()}\n"
    )
    path.write_text(fm, encoding="utf-8")
    return path


def _ingest_into_advisor(advisor: str, md_path: Path, title: str, source_url: str) -> int:
    """Embed + upsert md into the advisor's Qdrant collection. Returns chunks."""
    try:
        from vault_ingest import upsert_md
        return upsert_md(advisor, md_path, title=title, source_url=source_url)
    except Exception as e:
        logger.warning("ingest into %s failed: %s", advisor, e)
        return 0


def _dispatch_forward_summary(plan: dict, content: dict, intent: dict, *,
                              client: httpx.Client | None) -> dict:
    """Take an existing summary md and run the share+ingest path against an advisor."""
    advisor = plan["target"].get("advisor")
    if not advisor:
        return _result(ok=False, handler="forward_summary",
                       summary="No advisor on plan.", details={},
                       error="missing_advisor")
    ref = plan.get("forward_ref")  # short id like S-XXXX, or None for last-in-channel
    summary_path = _resolve_summary_ref(ref, plan)
    if not summary_path:
        return _result(ok=False, handler="forward_summary",
                       summary="Could not resolve summary reference.",
                       details={"ref": ref}, error="summary_not_found")

    summary_text = summary_path.read_text(encoding="utf-8")
    # Build content from the summary md
    title = _frontmatter_field(summary_text, "title") or summary_path.stem
    source_url = _frontmatter_field(summary_text, "source_url") or ""
    fwd_content = {
        "original_message": f"Forwarded summary {summary_path.name}",
        "url": source_url,
        "og_title": title,
        "og_description": _strip_frontmatter(summary_text)[:500],
    }
    fwd_intent = dict(intent)
    fwd_intent.setdefault("instructions", "Read this summary and respond with your take.")

    # Reuse share dispatch logic
    share_plan = dict(plan)
    share_plan["handler"] = "share"
    return _dispatch_share(share_plan, fwd_content, fwd_intent, client=client)


def _frontmatter_field(md_text: str, key: str) -> str:
    if not md_text.startswith("---"):
        return ""
    end = md_text.find("\n---\n", 4)
    if end < 0:
        return ""
    fm = md_text[4:end]
    for line in fm.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            if k.strip() == key:
                return v.strip()
    return ""


def _strip_frontmatter(md_text: str) -> str:
    if not md_text.startswith("---"):
        return md_text
    end = md_text.find("\n---\n", 4)
    if end < 0:
        return md_text
    return md_text[end + 5:]


def _resolve_summary_ref(ref: str | None, plan: dict) -> Path | None:
    """Resolve a forward-summary reference to a vault path.

    ref priority: explicit S-XXXX → last_summaries[channel:chat_id] → _latest.
    """
    import json as _json
    base = VAULT_PATH / "40_Summaries"
    if not base.exists():
        return None
    # Explicit id
    if ref and ref.upper().startswith("S-"):
        matches = list(base.glob(f"{ref.upper()}_*.md"))
        if matches:
            return matches[0]
    # Channel-aware lookup
    store = VAULT_PATH / "00_System" / "last_summaries.json"
    if not store.exists():
        return None
    try:
        data = _json.loads(store.read_text())
    except Exception:
        return None
    origin_channel = plan.get("origin_channel") or "unknown"
    origin_chat_id = plan.get("origin_chat_id")
    key = f"{origin_channel}:{origin_chat_id}"
    entry = data.get(key) or data.get("_latest")
    if not entry:
        return None
    sid = entry.get("id")
    matches = list(base.glob(f"{sid}_*.md")) if sid else []
    return matches[0] if matches else None



def _dispatch_blog_post(plan: dict, content: dict, intent: dict, *,
                        council_client: httpx.Client | None,
                        worker_client: httpx.Client | None) -> dict:
    blog = plan["target"].get("blog")
    if not blog:
        return _result(ok=False, handler="blog_post",
                       summary="No blog target on plan.", details={},
                       error="missing_blog_target")

    # Step 1: ask Creative to draft.
    prompt = _blog_post_prompt(content, intent, blog)
    creative = _call_council("creative", prompt, client=council_client)
    draft_text = creative.get("reply", "").strip()
    if not draft_text:
        return _result(ok=False, handler="blog_post",
                       summary="Creative returned empty draft.",
                       details={"advisor": "creative"}, error="empty_draft")

    title, body_html = _split_draft(draft_text)

    # Step 2: post to WordPress as a draft via the worker's WP route.
    wp_resp = _call_worker_wp_post(blog, title, body_html, client=worker_client)
    if not wp_resp.get("ok"):
        return _result(ok=False, handler="blog_post",
                       summary=f"WP draft create failed: {wp_resp.get('error')}",
                       details={"blog": blog, "wp_response": wp_resp},
                       error=wp_resp.get("error", "wp_error"))

    return _result(
        ok=True, handler="blog_post",
        summary=f"Draft saved to {blog}: {wp_resp.get('edit_url', '(no url)')}",
        details={"blog": blog, "title": title,
                 "wp_post_id": wp_resp.get("post_id"),
                 "wp_edit_url": wp_resp.get("edit_url"),
                 "usage": creative.get("usage", {})},
    )


def _dispatch_recipe(plan: dict, content: dict, intent: dict) -> dict:
    RECIPE_INBOX.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    title = _recipe_title(content, intent)
    slug = _slugify(title) or "recipe"
    path = RECIPE_INBOX / f"{ts}_{slug}.md"

    front = {
        "title":    title,
        "source":   content.get("url") or "",
        "added_at": ts,
        "tags":     ["recipe", "unprocessed"],
    }
    front_yaml = "\n".join(f"{k}: {json.dumps(v) if isinstance(v, list) else v}"
                           for k, v in front.items())
    body_md = (
        f"---\n{front_yaml}\n---\n\n"
        f"# {title}\n\n"
        f"**Source:** {content.get('url') or '(none)'}\n\n"
        f"## Original capture\n\n"
        f"{content.get('original_message','').strip() or '(empty)'}\n\n"
        f"## OG metadata\n\n"
        f"- Title: {content.get('og_title','') or '(none)'}\n"
        f"- Description: {content.get('og_description','') or '(none)'}\n\n"
        f"## Notes\n\n"
        f"_To be filled in._\n"
    )
    path.write_text(body_md, encoding="utf-8")
    return _result(
        ok=True, handler="recipe",
        summary=f"Recipe saved: {path.relative_to(VAULT_PATH)}",
        details={"vault_path": str(path.relative_to(VAULT_PATH)),
                 "title": title},
    )


# ---------------------------------------------------------------------------
# Council + Worker IO
# ---------------------------------------------------------------------------

def _call_council(advisor: str, message: str, *,
                  client: httpx.Client | None) -> dict:
    payload = {
        "channel":   advisor,
        "message":   message,
        "user_id":   "sprint-a-dispatch",
        "history":   [],
    }
    if client is not None:
        r = client.post(f"{COUNCIL_API}/council/message", json=payload,
                        timeout=COUNCIL_TIMEOUT_S)
    else:
        with httpx.Client(timeout=COUNCIL_TIMEOUT_S) as c:
            r = c.post(f"{COUNCIL_API}/council/message", json=payload)
    if r.status_code != 200:
        raise DispatchError(f"council error {r.status_code}: {r.text[:200]}")
    return r.json()


def _call_worker_wp_post(site_id: str, title: str, body_html: str, *,
                         client: httpx.Client | None) -> dict:
    payload = {"title": title, "content": body_html, "status": "draft"}
    url = f"{WORKER_API}/wordpress/{site_id}/posts"
    try:
        if client is not None:
            r = client.post(url, json=payload, timeout=60)
        else:
            with httpx.Client(timeout=60) as c:
                r = c.post(url, json=payload)
    except httpx.HTTPError as e:
        return {"ok": False, "error": str(e)}
    if r.status_code not in (200, 201):
        return {"ok": False, "error": f"wp http {r.status_code}: {r.text[:200]}"}
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if isinstance(body, dict) and body.get("error"):
        return {"ok": False, "error": body["error"], "raw": body}
    return {
        "ok":       True,
        "post_id":  body.get("id") or body.get("post_id"),
        "edit_url": body.get("link") or body.get("edit_url"),
        "raw":      body,
    }


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------

def _share_prompt(content: dict, intent: dict, advisor: str) -> str:
    parts = [
        f"Leo dropped this into the parking lot and wants you ({advisor}) to take a look.",
    ]
    if intent.get("instructions"):
        parts.append(f"His instructions: {intent['instructions']}")
    parts.append("---")
    parts.append(content.get("original_message", "").strip() or "(no message text)")
    if content.get("url"):
        parts.append(f"\nLink: {content['url']}")
        if content.get("og_title"):
            parts.append(f"Page title: {content['og_title']}")
        if content.get("og_description"):
            parts.append(f"Page description: {content['og_description']}")
    return "\n".join(parts)


def _summarize_prompt(content: dict, intent: dict) -> str:
    parts = ["Summarize the following capture from Leo. Pull in any context "
             "you can fetch if relevant."]
    if intent.get("instructions"):
        parts.append(f"Specific ask: {intent['instructions']}")
    parts.append("---")
    parts.append(content.get("original_message", "").strip() or "(no message text)")
    if content.get("url"):
        parts.append(f"\nURL: {content['url']}")
        if content.get("og_title"):
            parts.append(f"Page title: {content['og_title']}")
        if content.get("og_description"):
            parts.append(f"Page description: {content['og_description']}")
    return "\n".join(parts)


def _blog_post_prompt(content: dict, intent: dict, blog: str) -> str:
    parts = [
        f"Draft a blog post for the '{blog}' WordPress site. Output ONLY the "
        f"post — start with a single H1 line (`# Title`) followed by the body "
        f"as plain HTML or Markdown. Do not include commentary or framing.",
    ]
    if intent.get("instructions"):
        parts.append(f"Leo's instructions: {intent['instructions']}")
    parts.append("---")
    parts.append(content.get("original_message", "").strip() or "(no message text)")
    if content.get("url"):
        parts.append(f"\nReference link: {content['url']}")
        if content.get("og_title"):
            parts.append(f"Reference title: {content['og_title']}")
        if content.get("og_description"):
            parts.append(f"Reference description: {content['og_description']}")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _result(*, ok: bool, handler: str | None, summary: str,
            details: dict, error: str | None = None) -> dict:
    return {"ok": ok, "handler": handler, "summary": summary,
            "details": details, "error": error}


def _split_draft(draft_text: str) -> tuple[str, str]:
    """Pull the first H1 as the title; everything after is body."""
    lines = draft_text.splitlines()
    title = ""
    body_start = 0
    for i, line in enumerate(lines):
        m = re.match(r"^\s*#\s+(.+?)\s*$", line)
        if m:
            title = m.group(1).strip()
            body_start = i + 1
            break
    body = "\n".join(lines[body_start:]).strip()
    if not title:
        title = (draft_text.strip().splitlines() or ["Untitled draft"])[0][:80]
        body = draft_text
    return title, body


def _recipe_title(content: dict, intent: dict) -> str:
    if content.get("og_title"):
        return content["og_title"].strip()[:120]
    msg = (content.get("original_message") or "").strip()
    if msg:
        return msg.splitlines()[0][:120]
    if content.get("url"):
        return content["url"].split("/")[-1][:120] or "Recipe"
    return "Recipe"


def _slugify(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s[:50]


def _oneliner(text: str, limit: int = 140) -> str:
    if not text:
        return "(empty)"
    flat = re.sub(r"\s+", " ", text).strip()
    return flat[:limit] + ("…" if len(flat) > limit else "")


def _log_dispatch(plan: dict, intent: dict, result: dict) -> None:
    try:
        DISPATCH_LOG.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "ts":      datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "handler": result.get("handler"),
            "ok":      result.get("ok"),
            "error":   result.get("error"),
            "target":  plan.get("target", {}),
            "action":  intent.get("action"),
            "summary": result.get("summary"),
        }
        with DISPATCH_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
    except Exception as e:
        logger.warning("dispatch log write failed: %s", e)
