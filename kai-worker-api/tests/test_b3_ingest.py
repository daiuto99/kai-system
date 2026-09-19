"""B3 (KAI-1467) — ingest pass: describe dropped files into the brief's sources.

Deterministic gist for text formats, typed caption for binaries; folded into the
brief's "## Sources index". Design: docs/IDEA_BRAINSTORM_LOOP_DESIGN.md §3/§4.
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


def test_describe_markdown_gets_heading_and_para(monkeypatch, tmp_path):
    _, idea_dir = _setup(monkeypatch, tmp_path)
    (idea_dir / "concept.md").write_text("---\ntag: x\n---\n# The Zine\n\nAnalog artifact for members.\n")
    desc = console._describe_source(idea_dir / "concept.md", "markdown")
    assert "The Zine" in desc and "Analog artifact for members." in desc


def test_describe_html_prefers_title(monkeypatch, tmp_path):
    _, idea_dir = _setup(monkeypatch, tmp_path)
    (idea_dir / "ref.html").write_text("<html><head><title>Inspiration Board</title></head><body>x</body></html>")
    desc = console._describe_source(idea_dir / "ref.html", "html")
    assert "Inspiration Board" in desc


def test_describe_binary_gets_typed_caption(monkeypatch, tmp_path):
    _, idea_dir = _setup(monkeypatch, tmp_path)
    (idea_dir / "deck.pdf").write_bytes(b"%PDF-1.4 xxx")
    (idea_dir / "mood.png").write_bytes(b"\x89PNG")
    assert "PDF document" in console._describe_source(idea_dir / "deck.pdf", "pdf")
    assert "image" in console._describe_source(idea_dir / "mood.png", "image")


def test_sources_index_carries_descriptions(monkeypatch, tmp_path):
    _, idea_dir = _setup(monkeypatch, tmp_path)
    (idea_dir / "notes.md").write_text("# Notes\n\nfirst thought\n")
    srcs = console._sources_index({"dir": "vault/20_Projects/soul-collective"})
    by = {s["name"]: s for s in srcs}
    assert "description" in by["notes.md"]
    assert "Notes" in by["notes.md"]["description"]


def test_ingest_folds_sources_into_brief(monkeypatch, tmp_path):
    _, idea_dir = _setup(monkeypatch, tmp_path)
    (idea_dir / "concept.md").write_text("# Big Idea\n\nthe pitch line\n")
    (idea_dir / "deck.pdf").write_bytes(b"%PDF-1.4")
    res = console.ingest_idea_sources("soul-collective")
    assert res["ok"] is True and res["count"] == 2
    md = (idea_dir / "_brief.md").read_text()
    sec = md[md.index("## Sources index"):]
    assert "`concept.md`" in sec and "Big Idea" in sec
    assert "`deck.pdf`" in sec and "PDF document" in sec
    # placeholder gone from the sources section
    assert "one line per raw file" not in sec


def test_ingest_re_derives_not_appends(monkeypatch, tmp_path):
    _, idea_dir = _setup(monkeypatch, tmp_path)
    (idea_dir / "a.md").write_text("# A\n\nx\n")
    console.ingest_idea_sources("soul-collective")
    # a file is removed; a second ingest must reflect the new truth, not stack
    (idea_dir / "a.md").unlink()
    (idea_dir / "b.md").write_text("# B\n\ny\n")
    console.ingest_idea_sources("soul-collective")
    md = (idea_dir / "_brief.md").read_text()
    sec = md[md.index("## Sources index"):]
    assert "`b.md`" in sec and "`a.md`" not in sec


def test_ingest_unknown_idea_404(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    with pytest.raises(HTTPException) as ei:
        console.ingest_idea_sources("nope")
    assert ei.value.status_code == 404


def test_set_section_replaces_body(monkeypatch, tmp_path):
    md = "# t\n\n## Sources index\n\nold\n\n## After\n\nkeep\n"
    out = console._set_section(md, "## Sources index", "new body")
    assert "new body" in out and "old" not in out
    assert "## After" in out and "keep" in out  # later section preserved
