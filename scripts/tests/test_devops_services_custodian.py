"""KAI-47 Phase 2 — services custodian decision logic."""
import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "devops_services_custodian",
    Path(__file__).resolve().parent.parent / "devops_services_custodian.py")
sv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sv)


def test_healthy_running_container_yields_no_finding():
    disp, _ = sv.classify_container("running", "0", "always", 0)
    assert disp is None


def test_oneshot_exited_zero_is_healthy():
    # a one-shot (no restart policy) that exited 0 is NOT down
    disp, _ = sv.classify_container("exited", "0", "no", 0)
    assert disp is None


def test_down_expected_up_low_restarts_is_auto_restart():
    disp, reason = sv.classify_container("exited", "1", "always", 1)
    assert disp == "auto"
    assert "restart" in reason.lower()


def test_down_after_many_restarts_is_crash_loop_structural():
    disp, reason = sv.classify_container("exited", "1", "always", sv.CRASH_LOOP_RC)
    assert disp == "structural"
    assert "crash-loop" in reason.lower()


def test_running_flapping_unknown_uptime_stays_conservative():
    # uptime unknown (None) → cannot prove stability → still structural, do NOT auto-restart
    disp, reason = sv.classify_container("running", "0", "always", sv.CRASH_LOOP_RC)
    assert disp == "structural"
    assert "flapping" in reason.lower()


def test_running_high_rc_but_stable_uptime_is_cleared():
    # the fix: high LIFETIME count but continuously up past the window → not looping.
    # This is the kai-tailscale false positive (rc=5, up for days).
    disp, reason = sv.classify_container(
        "running", "0", "always", sv.CRASH_LOOP_RC, uptime_s=sv.STABLE_UPTIME_S + 1)
    assert disp is None
    assert "not looping" in reason.lower()


def test_running_high_rc_short_uptime_is_structural():
    # a genuinely flapping container: high count AND recently (re)started → structural
    disp, reason = sv.classify_container(
        "running", "0", "always", sv.CRASH_LOOP_RC, uptime_s=60)
    assert disp == "structural"
    assert "flapping" in reason.lower()


def test_stability_window_boundary():
    # just under the window is still a live flap; at/over it is cleared
    assert sv.classify_container("running", "0", "always", sv.CRASH_LOOP_RC,
                                 uptime_s=sv.STABLE_UPTIME_S - 1)[0] == "structural"
    assert sv.classify_container("running", "0", "always", sv.CRASH_LOOP_RC,
                                 uptime_s=sv.STABLE_UPTIME_S)[0] is None


def test_crash_loop_threshold_boundary():
    assert sv.classify_container("exited", "1", "always", sv.CRASH_LOOP_RC - 1)[0] == "auto"
    assert sv.classify_container("exited", "1", "always", sv.CRASH_LOOP_RC)[0] == "structural"


def test_uptime_parser_handles_nanoseconds_and_zero_value():
    # docker emits nanosecond RFC3339; parser must not choke, and returns a positive age
    old = (datetime.now(timezone.utc) - timedelta(hours=5))
    stamp = old.strftime("%Y-%m-%dT%H:%M:%S.") + "123456789Z"
    age = sv._uptime_s(stamp)
    assert age is not None and age > 4 * 3600
    # docker's never-started zero value → None (unknown), not a huge bogus age
    assert sv._uptime_s("0001-01-01T00:00:00Z") is None
    assert sv._uptime_s("") is None
