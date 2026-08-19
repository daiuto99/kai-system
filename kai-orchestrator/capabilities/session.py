"""session.close, session.close_status — orchestrator-native KAI session close.

close() runs the close workflow entirely within the orchestrator using mounted
/workspace (ro) and /vault (rw) volumes plus the capability chain (notify.post).
No shell-outs, no worker-api HTTP calls.
"""
import json
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from models import CapabilityResult
from . import capability

_WORKSPACE = Path("/workspace")
_VAULT = Path("/vault")
_MANIFEST = _VAULT / "00_System" / "session_close_log.json"
_CHANNEL = "devops"


def _now() -> datetime:
    return datetime.now(ZoneInfo("America/New_York"))


def _date_str() -> str:
    return _now().strftime("%Y-%m-%d")


def _month_str() -> str:
    return _now().strftime("%Y-%m")


def _ts() -> str:
    return _now().strftime("%Y-%m-%dT%H:%M:%S")


def _parse_sprint_history() -> dict:
    path = _WORKSPACE / "Sprint_History.md"
    if not path.exists():
        return {"title": f"Session {_date_str()}", "items": []}
    text = path.read_text()
    m = re.search(r"(## .+?)(?=\n## |\Z)", text, re.DOTALL)
    if not m:
        return {"title": f"Session {_date_str()}", "items": []}
    block = m.group(1).strip().split("\n")
    title = block[0].replace("## ", "").strip()
    items = [l.lstrip("- ").strip() for l in block[1:] if l.strip().startswith("-")]
    return {"title": title, "items": items}


def _parse_sotu() -> dict:
    path = _WORKSPACE / "StateOfTheUnion.md"
    if not path.exists():
        return {"whats_next": "See Plane board", "open_items": []}
    text = path.read_text()
    m = re.search(r"\*\*What's next:\*\*([^\n]*)", text)
    whats_next = m.group(1).strip() if m else "See Plane board"
    m2 = re.search(r"\*\*Open:\*\*\n(.*?)(?=\n\*\*|\Z)", text, re.DOTALL)
    open_items = []
    if m2:
        open_items = [l.lstrip("- ").strip() for l in m2.group(1).split("\n") if l.strip().startswith("-")]
    return {"whats_next": whats_next, "open_items": open_items}


def _add_step(steps: list, name: str, label: str, status: str, detail: str) -> None:
    steps.append({"name": name, "label": label, "status": status,
                  "detail": detail, "timestamp": _ts(), "retried": False})


def _flush_manifest(steps: list, title: str, mode: str, context_pct: str) -> None:
    manifest = {
        "date": _date_str(), "timestamp": _ts(), "mode": mode,
        "context_pct": context_pct, "session_title": title, "steps": steps,
        "overall": (
            "ok" if all(s["status"] in ("ok", "skip") for s in steps)
            else "partial" if any(s["status"] == "ok" for s in steps)
            else "fail"
        ),
    }
    _MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    _MANIFEST.write_text(json.dumps(manifest, indent=2))


def _build_notify_report(steps: list, title: str, items: list, whats_next: str,
                         mode: str, context_pct: str) -> str:
    icon = {"ok": ":white_check_mark:", "fail": ":x:", "skip": ":white_circle:", "pending": ":hourglass:"}
    mode_tag = f"Auto-Close @ {context_pct}%" if mode == "auto" else "Manual Close"
    lines = [f"*:robot_face: KAI SESSION CLOSED — {_date_str()} | {mode_tag}*", f"*{title}*", ""]
    if items:
        lines.append("*Completed:*")
        lines.extend(f"• {i}" for i in items)
        lines.append("")
    lines.append("*Close verification:*")
    for s in steps:
        if s["name"] == "notify_report":
            continue
        mark = icon.get(s["status"], ":question:")
        lines.append(f"{mark} *{s['label']}* — {s['detail'][:80]}")
    lines += ["", f"*Next:* {whats_next}"]
    failed = [s for s in steps if s["status"] == "fail"]
    if failed:
        lines.append("\n:warning: *FAILED STEPS:*")
        lines.extend(f"  • {s['label']}: {s['detail'][:100]}" for s in failed)
    return "\n".join(lines)


@capability("session.close")
def close(mode: str = "manual", context_pct: str = "", **_) -> CapabilityResult:
    """Run the KAI session close workflow natively (no shell-outs, no worker-api).
    Reads /workspace (ro), writes /vault (rw), calls notify.post directly.
    mode: 'manual' (LSE already wrote all docs) or 'auto' (95% context trigger).
    """
    from .notify import post as _notify_post  # import here to avoid circular at module load

    date_str = _date_str()
    steps: list[dict] = []

    # 1 — Sprint_History.md
    sprint = _parse_sprint_history()
    title, items = sprint["title"], sprint["items"]
    sh_path = _WORKSPACE / "Sprint_History.md"
    sprint_ok = sh_path.exists() and date_str in sh_path.read_text()[:50000]
    _add_step(steps, "sprint_history", "Sprint_History.md",
              "ok" if sprint_ok else "fail",
              f"Entry dated {date_str} confirmed" if sprint_ok
              else f"No entry for {date_str} — LSE must write Sprint_History before closing")

    # 2 — StateOfTheUnion.md
    sotu = _parse_sotu()
    whats_next = sotu["whats_next"]
    sotu_path = _WORKSPACE / "StateOfTheUnion.md"
    sotu_ok = sotu_path.exists() and date_str in sotu_path.read_text()[:500]
    _add_step(steps, "sotu", "StateOfTheUnion.md",
              "ok" if sotu_ok else "fail",
              f"Date {date_str} confirmed in header" if sotu_ok
              else f"Date {date_str} NOT in SOTU header — LSE must update SOTU first")

    # 3 — Vault session file
    session_path = _VAULT / "60_Council" / "sessions" / "kai" / f"{date_str}.md"
    if session_path.exists():
        _add_step(steps, "vault_session", "Vault session file", "ok", f"Exists: {session_path.name}")
    else:
        session_path.parent.mkdir(parents=True, exist_ok=True)
        open_items = sotu.get("open_items", [])
        mode_tag = f"auto-close at {context_pct}%" if mode == "auto" else "manual close (engine fallback)"
        content = (
            f"# KAI Session — {title}\n**Date:** {date_str}\n**Close:** {mode_tag}\n\n"
            f"## Completed\n" + ("\n".join(f"- {i}" for i in items) or "- (none captured)") + "\n\n"
            f"## Open\n" + ("\n".join(f"- {o}" for o in open_items) or "- See Plane board") + "\n\n"
            f"## What's Next\n{whats_next}\n"
        )
        try:
            session_path.write_text(content)
            _add_step(steps, "vault_session", "Vault session file", "ok",
                      f"Written by engine ({mode})")
        except Exception as e:
            _add_step(steps, "vault_session", "Vault session file", "fail", str(e)[:120])

    # 4 — Vault wiki sync
    wiki_dir = _VAULT / "70_Knowledge" / "System"
    try:
        wiki_dir.mkdir(parents=True, exist_ok=True)
        synced = []
        for fname in ("StateOfTheUnion.md", "Sprint_History.md"):
            src = _WORKSPACE / fname
            if src.exists():
                (wiki_dir / fname).write_text(src.read_text())
                synced.append(fname)
        _add_step(steps, "vault_wiki_sync", "Vault wiki sync", "ok",
                  f"Synced: {', '.join(synced)}" if synced else "Nothing to sync")
    except Exception as e:
        _add_step(steps, "vault_wiki_sync", "Vault wiki sync", "fail", str(e)[:120])

    # 5 — Plane update (manual = LSE owns; auto = skip)
    if mode == "manual":
        _add_step(steps, "plane_update", "Plane issues", "ok",
                  "Manual close — Plane issues updated by LSE during session")
    else:
        _add_step(steps, "plane_update", "Plane issues", "skip",
                  "Auto-close — Plane update requires LSE review")

    # interim manifest write
    _flush_manifest(steps, title, mode, context_pct)

    # 6 — notify report
    report = _build_notify_report(steps, title, items, whats_next, mode, context_pct)
    try:
        notify_r = _notify_post(channel=_CHANNEL, text=report, username="KAI", icon_emoji=":robot_face:")
        _add_step(steps, "notify_report", "Notify close report",
                  "ok" if notify_r.ok else "fail",
                  "Posted to Telegram" if notify_r.ok else f"Notify error: {notify_r.error}")
    except Exception as e:
        _add_step(steps, "notify_report", "Notify close report", "fail", str(e)[:120])

    # final manifest
    _flush_manifest(steps, title, mode, context_pct)

    failed = [s for s in steps if s["status"] == "fail"]
    ok = not failed
    return CapabilityResult(
        ok=ok,
        status="succeeded" if ok else "failed_recoverable",
        data={"title": title, "date": date_str, "mode": mode, "steps": steps,
              "overall": "ok" if ok else "partial"},
        verification={"verified": ok, "method": "step_check"},
        error=None if ok else {"type": "steps_failed", "failed": [s["name"] for s in failed]},
    )


@capability("session.close_status")
def close_status(**_) -> CapabilityResult:
    """Read the last close manifest from vault."""
    if not _MANIFEST.exists():
        return CapabilityResult(ok=True, status="succeeded",
                                data={"status": "no_manifest",
                                      "message": "No close manifest — session not yet closed via engine"})
    try:
        manifest = json.loads(_MANIFEST.read_text())
        steps = manifest.get("steps", [])
        failed = [s for s in steps if s.get("status") == "fail"]
        ok_count = sum(1 for s in steps if s.get("status") == "ok")
        return CapabilityResult(
            ok=not failed, status="succeeded",
            data={
                "status": "ok" if not failed else "partial",
                "date": manifest.get("date"),
                "session_title": manifest.get("session_title"),
                "mode": manifest.get("mode"),
                "overall": manifest.get("overall"),
                "steps_ok": ok_count,
                "steps_total": len(steps),
                "failed": [{"name": s["name"], "label": s["label"], "detail": s["detail"]}
                            for s in failed],
                "steps": [{"name": s["name"], "label": s["label"],
                            "status": s["status"], "detail": s["detail"]}
                           for s in steps],
            },
            verification={"verified": True, "method": "manifest_read"},
        )
    except Exception as e:
        return CapabilityResult(ok=False, status="failed_recoverable",
                                error={"type": "manifest_error", "detail": str(e)})
