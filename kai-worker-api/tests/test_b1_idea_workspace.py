"""B1 (KAI-1465) — idea brief + idea-workspace endpoints.

The idea-side twin of the project workspace: a per-idea living brief (_brief.md)
KAI maintains, read/written through the console API, plus a live sources index.
Design: docs/IDEA_BRAINSTORM_LOOP_DESIGN.md §3/§4/§7.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import HTTPException  # noqa: E402
from routes import console  # noqa: E402


def _setup(monkeypatch, tmp_path, rel="20_Projects/soul-collective"):
    """Point the console module at a tmp vault holding one idea dir + one source."""
    vault = tmp_path / "vault"
    idea_dir = vault / rel
    idea_dir.mkdir(parents=True)
    (idea_dir / "STATUS.md").write_text("status: yellow\n")  # a raw source file
    monkeypatch.setattr(console, "VAULT_PATH", vault)
    store = {"ideas": [{
        "slug": "soul-collective",
        "name": "The Soul Collective",
        "note": "Culture/lifestyle community.",
        "dir": "vault/" + rel,
    }]}
    monkeypatch.setattr(console, "_load_store", lambda: store)
    return vault, idea_dir


def test_get_brief_absent_returns_canonical_spine(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    out = console.get_idea_brief("soul-collective")
    assert out["exists"] is False
    assert out["filename"] == "_brief.md"
    # canonical spine present so the UI can render + edit immediately
    for heading in ("# The Soul Collective — Idea Brief", "## What it is",
                    "## Current thinking", "## Open threads",
                    "## Last session's riff", "## Sources index"):
        assert heading in out["content"]
    # seeded "what it is" from the idea note
    assert "Culture/lifestyle community." in out["content"]


def test_put_then_get_brief_roundtrip(monkeypatch, tmp_path):
    _, idea_dir = _setup(monkeypatch, tmp_path)
    body = console.DocWrite(content="# riff\n\nnew thinking captured\n")
    res = console.put_idea_brief("soul-collective", body)
    assert res["ok"] is True
    assert res["bytes"] == len("# riff\n\nnew thinking captured\n".encode())
    # landed on disk at the idea folder root
    assert (idea_dir / "_brief.md").read_text() == "# riff\n\nnew thinking captured\n"
    # read back reflects exists True + exact content
    got = console.get_idea_brief("soul-collective")
    assert got["exists"] is True
    assert got["content"] == "# riff\n\nnew thinking captured\n"


def test_workspace_bundles_idea_brief_and_live_sources(monkeypatch, tmp_path):
    _, idea_dir = _setup(monkeypatch, tmp_path)
    # add a heterogeneous source to prove kind inference + live enumeration
    (idea_dir / "moodboard.png").write_bytes(b"\x89PNG\r\n")
    ws = console.get_idea_workspace("soul-collective")
    assert ws["idea"]["slug"] == "soul-collective"
    assert ws["brief"]["exists"] is False  # seeded spine, not yet written
    assert "## What it is" in ws["brief"]["content"]
    names = {s["name"]: s["kind"] for s in ws["sources"]}
    assert names.get("STATUS.md") == "markdown"
    assert names.get("moodboard.png") == "image"
    assert "loop" in ws  # loop stub present for B2/B4/B5 wiring


def test_sources_index_excludes_the_brief_itself(monkeypatch, tmp_path):
    _, idea_dir = _setup(monkeypatch, tmp_path)
    console.put_idea_brief("soul-collective", console.DocWrite(content="x"))
    ws = console.get_idea_workspace("soul-collective")
    assert "_brief.md" not in {s["name"] for s in ws["sources"]}
    assert ws["brief"]["exists"] is True  # now written, workspace reflects it


def test_unknown_idea_404(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    for fn in (console.get_idea_brief, console.get_idea_workspace):
        with pytest.raises(HTTPException) as ei:
            fn("no-such-idea")
        assert ei.value.status_code == 404
    with pytest.raises(HTTPException) as ei:
        console.put_idea_brief("no-such-idea", console.DocWrite(content="x"))
    assert ei.value.status_code == 404


def test_put_creates_brief_when_folder_missing_child(monkeypatch, tmp_path):
    """The write path mkdirs parents — capture works even if the idea dir needs it."""
    vault, idea_dir = _setup(monkeypatch, tmp_path)
    # remove the dir to prove mkdir(parents=True) recreates it
    (idea_dir / "STATUS.md").unlink()
    idea_dir.rmdir()
    res = console.put_idea_brief("soul-collective", console.DocWrite(content="rebuilt"))
    assert res["ok"] is True
    assert (idea_dir / "_brief.md").read_text() == "rebuilt"
