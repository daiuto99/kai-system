"""B2 (KAI-1466) — capture half of the brainstorm loop.

The deterministic, channel-agnostic step that folds a brainstorm riff back into an
idea's living brief. Design: docs/IDEA_BRAINSTORM_LOOP_DESIGN.md §4.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import HTTPException  # noqa: E402
from routes import console  # noqa: E402


def _setup(monkeypatch, tmp_path, rel="20_Projects/soul-collective"):
    vault = tmp_path / "vault"
    idea_dir = vault / rel
    idea_dir.mkdir(parents=True)
    monkeypatch.setattr(console, "VAULT_PATH", vault)
    store = {"ideas": [{
        "slug": "soul-collective", "name": "The Soul Collective",
        "note": "n", "dir": "vault/" + rel,
    }]}
    monkeypatch.setattr(console, "_load_store", lambda: store)
    return vault, idea_dir


def test_capture_seeds_brief_then_folds_riff(monkeypatch, tmp_path):
    _, idea_dir = _setup(monkeypatch, tmp_path)
    res = console.capture_idea_riff("soul-collective", console.RiffCapture(riff="try a members zine"))
    assert res["ok"] is True and res["captured"] is True
    md = (idea_dir / "_brief.md").read_text()
    # spine present (seeded), placeholder replaced, riff folded under the section
    assert "## Last session's riff" in md
    assert "try a members zine" in md
    assert md.count("_(none captured yet)_") < 5  # at least the riff placeholder gone


def test_capture_prepends_most_recent_and_keeps_prior(monkeypatch, tmp_path):
    _, idea_dir = _setup(monkeypatch, tmp_path)
    console.capture_idea_riff("soul-collective", console.RiffCapture(riff="first riff"))
    console.capture_idea_riff("soul-collective", console.RiffCapture(riff="second riff"))
    md = (idea_dir / "_brief.md").read_text()
    assert "first riff" in md and "second riff" in md
    # most-recent-first ordering under the riff heading
    hi = md.index("## Last session's riff")
    assert md.index("second riff", hi) < md.index("first riff", hi)


def test_capture_optional_thinking_folds_into_current_thinking(monkeypatch, tmp_path):
    _, idea_dir = _setup(monkeypatch, tmp_path)
    console.capture_idea_riff(
        "soul-collective",
        console.RiffCapture(riff="r", thinking="positioning: analog x digital"),
    )
    md = (idea_dir / "_brief.md").read_text()
    ct = md.index("## Current thinking")
    nxt = md.index("## Open threads")
    assert "positioning: analog x digital" in md[ct:nxt]


def test_capture_empty_riff_400(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    with pytest.raises(HTTPException) as ei:
        console.capture_idea_riff("soul-collective", console.RiffCapture(riff="   "))
    assert ei.value.status_code == 400


def test_capture_unknown_idea_404(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    with pytest.raises(HTTPException) as ei:
        console.capture_idea_riff("nope", console.RiffCapture(riff="x"))
    assert ei.value.status_code == 404


def test_upsert_section_appends_when_heading_absent():
    out = console._upsert_section("# Freeform brief\n\nsome notes\n",
                                  "## Last session's riff", "_2026-01-01_ — hi")
    assert "## Last session's riff" in out
    assert "_2026-01-01_ — hi" in out
    assert out.startswith("# Freeform brief")
