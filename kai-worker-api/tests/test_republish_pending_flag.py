"""Async-close: the artifact-republish-pending flag round-trip.

A detached close (trigger_close background=True) has no model present to republish
the Leo-facing State & Plan artifact, so it leaves this flag; the brief surfaces it
(session_boot warns), and POST /session/republish-done clears it once a model
session republishes.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from routes import session  # noqa: E402


def test_read_pending_false_when_absent(monkeypatch, tmp_path):
    monkeypatch.setattr(session, "REPUBLISH_PENDING_PATH", tmp_path / "flag.json")
    assert session._read_republish_pending() is False


def test_read_pending_reflects_flag(monkeypatch, tmp_path):
    p = tmp_path / "flag.json"
    monkeypatch.setattr(session, "REPUBLISH_PENDING_PATH", p)
    p.write_text(json.dumps({"pending": True, "issue_id": "x"}))
    assert session._read_republish_pending() is True
    p.write_text(json.dumps({"pending": False}))
    assert session._read_republish_pending() is False


def test_read_pending_failsafe_on_corrupt(monkeypatch, tmp_path):
    p = tmp_path / "flag.json"
    monkeypatch.setattr(session, "REPUBLISH_PENDING_PATH", p)
    p.write_text("{not json")
    # a corrupt/truncated flag must never crash the brief — reads as not pending
    assert session._read_republish_pending() is False


def test_republish_done_clears_flag(monkeypatch, tmp_path):
    p = tmp_path / "flag.json"
    monkeypatch.setattr(session, "REPUBLISH_PENDING_PATH", p)
    p.write_text(json.dumps({"pending": True, "issue_id": "x"}))
    assert session._read_republish_pending() is True

    resp = session.republish_done()
    assert resp["ok"] is True and resp["pending"] is False
    assert session._read_republish_pending() is False

    # idempotent — safe to call with nothing pending
    resp2 = session.republish_done()
    assert resp2["ok"] is True and resp2["pending"] is False


def test_brief_surfaces_pending_key(monkeypatch, tmp_path):
    p = tmp_path / "flag.json"
    monkeypatch.setattr(session, "REPUBLISH_PENDING_PATH", p)
    # isolate the other brief inputs so the call is cheap + deterministic
    monkeypatch.setattr(session, "MANIFEST_PATH", tmp_path / "close.json")
    monkeypatch.setattr(session, "WARMBOOT_MANIFEST_PATH", tmp_path / "wb.json")
    monkeypatch.setattr(session, "NEXT_ACTION_PATH", tmp_path / "na.json")
    monkeypatch.setattr(session, "BRIEF_SOTU_PATH", tmp_path / "sotu.md")
    monkeypatch.setattr(session, "BRIEF_SPRINT_HISTORY_PATH", tmp_path / "hist.md")

    assert session.session_brief()["artifact_republish_pending"] is False
    p.write_text(json.dumps({"pending": True}))
    assert session.session_brief()["artifact_republish_pending"] is True
