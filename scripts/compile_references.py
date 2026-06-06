#!/usr/bin/env python3
"""
KAI-391: One-time reference compilation job.
Reads each uncompiled reference, uses KAI to extract style notes,
appends to BUILD_PROFILE under the matching subsection, marks as compiled.

Usage:
  python3 compile_references.py              # compile all uncompiled refs
  python3 compile_references.py --dry-run    # show what would be compiled
"""
import sys
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)

REFS_DIR = Path("/home/leo/vault/60_Council/creative/references")
BUILD_PROFILE = Path("/home/leo/vault/60_Council/creative/BUILD_PROFILE.md")
COMPILATION_LOG = Path("/home/leo/vault/60_Council/creative/compilation_log.md")
DRY_RUN = "--dry-run" in sys.argv


def parse_frontmatter(text: str) -> dict:
    meta = {}
    in_fm = False
    for line in text.splitlines():
        if line.strip() == "---":
            in_fm = not in_fm
            continue
        if in_fm and ":" in line:
            k, _, v = line.partition(":")
            meta[k.strip()] = v.strip()
    return meta


def call_kai(prompt: str) -> str:
    """Call KAI via the council graph to extract style notes."""
    try:
        sys.path.insert(0, "/home/leo/kai-system/kai-council-api")
        from graphs.graph import get_graph
        graph = get_graph()
        state = {
            "channel": "kai", "message": prompt, "user_id": "compile-job",
            "thread_ts": "compile-references", "attachments": [], "privacy_mode": False,
            "history": [], "target_advisor": "kai", "routing_reason": "reference compilation",
            "advisor_reply": "", "final_reply": "", "model_used": "",
            "input_tokens": 0, "output_tokens": 0, "audit_log": [],
        }
        result = graph.invoke(state, config={"configurable": {"thread_id": "compile-references"}})
        return result.get("final_reply", "").strip()
    except Exception as e:
        log.error("KAI call failed: %s", e)
        return f"[Compilation failed: {e}]"


def compile_reference(ref_file: Path, meta: dict, text: str) -> str:
    """Ask KAI to extract style notes from a reference."""
    url = meta.get("url", "")
    relevance = meta.get("relevance", "")
    category = meta.get("category", "")
    property_name = meta.get("property", "all")

    prompt = (
        f"[Reference Compilation — {ref_file.stem}]\n\n"
        f"Category: {category} | Property: {property_name}\n"
        f"URL: {url}\n"
        f"Why it was added: {relevance}\n\n"
        f"Additional notes:\n{text}\n\n"
        "Extract 3-5 specific style notes from this reference. Each note must be:\n"
        "- A concrete, actionable observation (not a vague principle)\n"
        "- Written as a directive sentence (e.g. 'Use full-bleed dark backgrounds with type as the sole compositional element')\n"
        "- Tied to what is specifically useful about this reference for Leo's properties\n\n"
        "Format: one STYLE NOTE: line per observation.\n"
        "Nothing else."
    )
    return call_kai(prompt)


def main():
    if not REFS_DIR.exists():
        log.error("References directory not found: %s", REFS_DIR)
        sys.exit(1)

    ref_files = [f for f in sorted(REFS_DIR.glob("*.md")) if f.name != "README.md"]
    uncompiled = []
    for f in ref_files:
        text = f.read_text()
        meta = parse_frontmatter(text)
        if meta.get("compiled", "false").lower() != "true":
            uncompiled.append((f, meta, text))

    log.info("Found %d uncompiled references (of %d total)", len(uncompiled), len(ref_files))

    if not uncompiled:
        log.info("Nothing to compile.")
        return

    if DRY_RUN:
        for f, meta, _ in uncompiled:
            log.info("  Would compile: %s [%s]", f.name, meta.get("category", "?"))
        return

    compiled_count = 0
    log_entries = []

    for ref_file, meta, text in uncompiled:
        log.info("Compiling: %s ...", ref_file.name)
        style_notes = compile_reference(ref_file, meta, text)

        if DRY_RUN or not style_notes or "failed" in style_notes.lower():
            log.warning("  Skipping %s — no notes produced", ref_file.name)
            continue

        # Append compiled notes to the reference file itself
        notes_section = f"\n## Compiled Style Notes\n*(compiled {datetime.now(timezone.utc).strftime('%Y-%m-%d')})*\n"
        for line in style_notes.splitlines():
            if line.strip().upper().startswith("STYLE NOTE:"):
                note = line.strip()[11:].strip()
                notes_section += f"- {note}\n"

        # Update the reference file — replace empty Compiled Style Notes section
        updated = text
        if "## Compiled Style Notes\n" in updated:
            parts = updated.split("## Compiled Style Notes\n", 1)
            updated = parts[0] + notes_section.lstrip()
        else:
            updated = updated.rstrip() + "\n" + notes_section

        # Mark as compiled in frontmatter
        updated = updated.replace("compiled: false", "compiled: true", 1)
        ref_file.write_text(updated)

        # Append style notes to BUILD_PROFILE under Compiled Taste Notes
        if BUILD_PROFILE.exists():
            profile = BUILD_PROFILE.read_text()
            entry = f"\n### Reference: {ref_file.stem} [{meta.get('category', '')}] — {meta.get('property', 'all')}\n"
            for line in style_notes.splitlines():
                if line.strip().upper().startswith("STYLE NOTE:"):
                    note = line.strip()[11:].strip()
                    entry += f"- {note}\n"

            if "*(No entries yet" in profile:
                profile = profile.replace(
                    "*(No entries yet — populated after first creative gate approve/reject cycle)*",
                    entry.strip()
                )
            else:
                # Append after Compiled Taste Notes header
                profile = profile + "\n" + entry
            BUILD_PROFILE.write_text(profile)

        log_entries.append(f"- {ref_file.name} [{meta.get('category')}] compiled {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
        compiled_count += 1
        log.info("  Done: %s", ref_file.name)

    # Write compilation log
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    log_text = f"\n## Compilation run — {ts}\n" + "\n".join(log_entries) + "\n"
    existing = COMPILATION_LOG.read_text() if COMPILATION_LOG.exists() else "# Reference Compilation Log\n"
    COMPILATION_LOG.write_text(existing + log_text)

    log.info("\nCompiled %d/%d references. BUILD_PROFILE updated.", compiled_count, len(uncompiled))


if __name__ == "__main__":
    main()
