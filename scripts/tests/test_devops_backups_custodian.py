"""KAI-47 Phase 2 — backups custodian decision logic."""
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "devops_backups_custodian",
    Path(__file__).resolve().parent.parent / "devops_backups_custodian.py")
bk = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bk)


def test_store_missing_is_stale():
    assert bk.store_stale(None) is True


def test_store_fresh_is_not_stale():
    assert bk.store_stale(2.0) is False
    assert bk.store_stale(bk.STALE_H - 0.1) is False


def test_store_old_is_stale():
    assert bk.store_stale(bk.STALE_H + 1) is True


def test_offsite_disabled_never_lapsed():
    # a gated/disabled offsite is a known WARN, not a backups-custodian action
    assert bk.offsite_stale(False, "FAIL", 100.0) is False


def test_offsite_enabled_failed_is_lapsed():
    assert bk.offsite_stale(True, "FAIL", 1.0) is True


def test_offsite_enabled_fresh_ok_is_not_lapsed():
    assert bk.offsite_stale(True, "OK", 5.0) is False


def test_offsite_enabled_stale_is_lapsed():
    assert bk.offsite_stale(True, "OK", bk.OFFSITE_STALE_H + 1) is True


def test_offsite_enabled_never_run_is_not_forced():
    # never run yet = cron pending, not the custodian's job to force
    assert bk.offsite_stale(True, None, None) is False
