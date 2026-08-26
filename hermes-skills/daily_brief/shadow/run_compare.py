#!/usr/bin/env python3
"""
Shadow comparison harness (AR-2 cutover evidence).

Runs, for N cycles, the INCUMBENT brief (kai-worker-api/focus.py) against the
NEW skill (scripts/build_brief.py) on the SAME deterministic input pull, and
evaluates content/structure parity per references/brief-contract.md.

Runs inside the kai-worker-api container (has focus.py, anthropic, httpx, and
/run/secrets). NEVER delivers to any comms surface — both sides are generated in-memory.

Emits a JSON summary on stdout: {cycles:[...], green:int, total:int, verdict:str}.
The caller renders shadow/comparison_log.md from it.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, "/app")          # focus.py (kai-worker-api)
sys.path.insert(0, "/skill")        # build_brief.py (mounted skill)

import focus            # incumbent
import build_brief      # new skill core

SECRETS = "/run/secrets"
VAULT = Path("/vault")
REQUIRED = ["top3", "next5", "carried_over"]

# parity thresholds (see references/brief-contract.md)
PARITY_OVERLAP_MIN = 0.35    # keyword Jaccard, skill <-> incumbent
NOVEL_RATIO_MAX = 0.35       # fraction of skill keywords absent from both incumbent + source

_STOP = {
    "the", "and", "for", "with", "that", "this", "your", "from", "have", "here",
    "here's", "good", "morning", "today", "focus", "most", "important", "things",
    "move", "deck", "after", "over", "carried", "items", "needing", "attention",
    "none", "next", "top", "task", "tasks", "due", "tomorrow", "keep", "tight",
    "make", "create", "contact", "reach", "out", "set", "have",
}


def keywords(text: str) -> set:
    """Significant lowercased tokens: alnum, len>=4, minus stopwords/section boilerplate."""
    toks = re.findall(r"[a-z0-9]+", text.lower())
    return {t for t in toks if len(t) >= 4 and t not in _STOP}


def evaluate(skill_env: dict, inc_brief: str, tasks: dict) -> dict:
    """Green/red verdict + the reasons, for one cycle. Parity is skill<->incumbent."""
    skill_brief = skill_env["brief_markdown"]
    skill_sections = skill_env["sections_present"]
    inc_sections = build_brief.detect_sections(inc_brief)

    kw_skill = keywords(skill_brief)
    kw_inc = keywords(inc_brief)
    kw_tasks = keywords(" ".join(tasks["today"] + tasks["overdue"]))

    union = kw_skill | kw_inc
    overlap = (len(kw_skill & kw_inc) / len(union)) if union else 1.0
    novel = kw_skill - kw_inc - kw_tasks               # in skill, absent from incumbent AND source
    novel_ratio = (len(novel) / len(kw_skill)) if kw_skill else 0.0

    checks = {
        "schema_valid": skill_env.get("schema") == "kai.daily_brief.v1",
        "skill_all_sections": skill_sections == REQUIRED,
        "incumbent_all_sections": all(s in inc_sections for s in REQUIRED),
        "input_counts_match": (
            skill_env["tasks_today"] == len(tasks["today"])
            and skill_env["tasks_overdue"] == len(tasks["overdue"])
        ),
        "freshness": skill_env["date_label"] == date.today().strftime("%A, %B %d"),
        "content_parity": overlap >= PARITY_OVERLAP_MIN,
        "no_hallucination": novel_ratio <= NOVEL_RATIO_MAX,
    }
    green = all(checks.values())
    return {
        "green": green,
        "checks": checks,
        "overlap": round(overlap, 3),
        "novel_ratio": round(novel_ratio, 3),
        "novel_terms": sorted(novel),
        "tasks_today": len(tasks["today"]),
        "tasks_overdue": len(tasks["overdue"]),
        "skill_sections": skill_sections,
        "incumbent_sections": inc_sections,
    }


def run_cycle(i: int) -> dict:
    # ONE shared input pull → apples-to-apples (removes Todoist-timing noise)
    tasks = focus.get_todoist_tasks()
    close_notes = focus.load_kai_close_notes(VAULT)

    inc_brief = focus.build_focus_brief(tasks, close_notes)                       # incumbent
    skill_brief = build_brief.build_brief_text(tasks, close_notes, SECRETS)       # new skill

    skill_env = {
        "schema": build_brief.SCHEMA,
        "date_label": date.today().strftime("%A, %B %d"),
        "tasks_today": len(tasks["today"]),
        "tasks_overdue": len(tasks["overdue"]),
        "sections_present": build_brief.detect_sections(skill_brief),
        "brief_markdown": skill_brief,
    }
    result = evaluate(skill_env, inc_brief, tasks)
    result["cycle"] = i
    result["incumbent_brief"] = inc_brief
    result["skill_brief"] = skill_brief
    return result


def main() -> int:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 5

    # input-path parity: skill's own Todoist pull matches incumbent's, once
    inc_pull = focus.get_todoist_tasks()
    skill_pull = build_brief.get_todoist_tasks(SECRETS)
    input_path_parity = (
        len(inc_pull["today"]) == len(skill_pull["today"])
        and len(inc_pull["overdue"]) == len(skill_pull["overdue"])
    )

    cycles = [run_cycle(i + 1) for i in range(n)]
    green = sum(1 for c in cycles if c["green"])
    verdict = "GREEN" if green >= 5 and green == n and input_path_parity else "NOT_GREEN"

    print(json.dumps({
        "generated_for": date.today().isoformat(),
        "input_path_parity": input_path_parity,
        "cycles": cycles,
        "green": green,
        "total": n,
        "verdict": verdict,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
