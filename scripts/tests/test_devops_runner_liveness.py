"""KAI-48 — external runner-liveness verdict (who watches the watcher).

The DevOps custodian runner's own per-custodian meta-monitor can only fire while the
runner runs; a dead runner is invisible to it. green_baseline.check_devops_custodian_runner
is the external probe. These test its pure verdict (devops_runner_liveness_eval).

Wired into ci.sh's findings-contract block (a plain `pytest` run, no `-m whole_repo`) so
it actually executes — unlike test_green_baseline.py, whose tests are deselected by that
filter (tracked separately).
"""
import importlib.util
import sys
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1] / "green_baseline.py"
_spec = importlib.util.spec_from_file_location("green_baseline", MODULE)
gb = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = gb
_spec.loader.exec_module(gb)


def test_missing_stamp_warns_never_reds():
    sev, detail = gb.devops_runner_liveness_eval(None, 1800)
    assert sev == "warn"
    assert detail.startswith("WARN")
    assert "no liveness stamp" in detail


def test_stale_stamp_warns():
    sev, detail = gb.devops_runner_liveness_eval(3600, 1800)  # 60m old, > 30m
    assert sev == "warn"
    assert "stalled" in detail and detail.startswith("WARN")


def test_fresh_stamp_is_green():
    sev, detail = gb.devops_runner_liveness_eval(120, 1800)  # 2m old
    assert sev == "green"
    assert not detail.startswith("WARN")
    assert "live" in detail


def test_boundary_at_threshold_is_green():
    # exactly at max_age is not yet stale (> is the stale condition)
    sev, _ = gb.devops_runner_liveness_eval(1800, 1800)
    assert sev == "green"


def test_verdict_is_warn_only_never_raises():
    # The runner-liveness probe must never RED the baseline (it is a meta-monitor,
    # not a runtime-down condition). Both problem states are WARN.
    for age in (None, 999999):
        sev, _ = gb.devops_runner_liveness_eval(age, 1800)
        assert sev in ("warn", "green")
        assert sev != "red"


def test_live_probe_returns_string_and_never_raises(tmp_path, monkeypatch):
    # A missing liveness file must degrade to a WARN string, not an exception.
    monkeypatch.setenv("DEVOPS_CUSTODIAN_LIVENESS", str(tmp_path / "nope.json"))
    detail = gb.check_devops_custodian_runner()
    assert isinstance(detail, str)
    assert detail.startswith("WARN")


def test_live_probe_reads_fresh_stamp_green(tmp_path, monkeypatch):
    import json
    from datetime import datetime, timezone
    live = tmp_path / "liveness.json"
    now = datetime.now(timezone.utc).isoformat()
    live.write_text(json.dumps({"storage": now, "backups": now}))
    monkeypatch.setenv("DEVOPS_CUSTODIAN_LIVENESS", str(live))
    detail = gb.check_devops_custodian_runner()
    assert not detail.startswith("WARN")
    assert "live" in detail
