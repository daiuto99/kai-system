"""HARDEN-2: brief consumers read the same vault wiki surface close verifies."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from routes import session  # noqa: E402


def _isolate_non_brief_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(session, "MANIFEST_PATH", tmp_path / "close.json")
    monkeypatch.setattr(session, "WARMBOOT_MANIFEST_PATH", tmp_path / "warmboot.json")
    monkeypatch.setattr(session, "NEXT_ACTION_PATH", tmp_path / "next-action.json")
    monkeypatch.setattr(session, "VAULT_PATH", tmp_path / "vault")


def test_brief_reads_close_verified_vault_wiki_artifacts_without_workspace_sync(monkeypatch, tmp_path):
    wiki = tmp_path / "70_Knowledge" / "System"
    wiki.mkdir(parents=True)
    sotu = wiki / "StateOfTheUnion.md"
    history = wiki / "Sprint_History.md"
    monkeypatch.setattr(session, "BRIEF_SOTU_PATH", sotu)
    monkeypatch.setattr(session, "BRIEF_SPRINT_HISTORY_PATH", history)
    _isolate_non_brief_paths(monkeypatch, tmp_path)

    sotu.write_text(
        "**Last updated: 2026-07-17 21:00 | v9.9.9**\n"
        "**Last session:** canonical SOTU one\n"
    )
    history.write_text("# Sprint History\n\n## canonical history one — Complete — 2026-07-17\n")
    first = session.session_brief()

    assert first["version"] == "9.9.9"
    assert first["last_session"] == "canonical SOTU one"
    # Sprint framing retired 2026-09-30 (KAI-1547): /PLAN.md is a flat priority stack,
    # so the brief no longer surfaces a sprint even when Sprint_History has an entry.
    # version + last_session above still prove the wiki surface is read (test's intent).
    assert first["sprint"] is None

    # This models the close's vault wiki write; no /workspace or rsync read is involved.
    sotu.write_text(
        "**Last updated: 2026-07-17 21:01 | v9.9.10**\n"
        "**Last session:** canonical SOTU two\n"
    )
    history.write_text("# Sprint History\n\n## canonical history two — In Progress — 2026-07-17\n")
    second = session.session_brief()

    assert second["version"] == "9.9.10"
    assert second["last_session"] == "canonical SOTU two"
    assert second["sprint"] is None
    assert second["sprint_status"] is None


def test_brief_artifact_paths_match_close_vault_wiki_contract():
    expected = session.VAULT_PATH / "70_Knowledge" / "System"
    assert session.BRIEF_SOTU_PATH == expected / "StateOfTheUnion.md"
    assert session.BRIEF_SPRINT_HISTORY_PATH == expected / "Sprint_History.md"
