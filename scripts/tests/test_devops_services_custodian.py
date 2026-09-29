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


def test_network_detach_while_running_is_auto_recreate():
    # kai-code-server signature: running, healthy restart count, but attached to zero
    # networks on a real named-network mode → detach, healed by recreate not restart.
    disp, reason = sv.classify_container(
        "running", "0", "unless-stopped", 0, uptime_s=99999,
        net_mode="kai-system_default", net_count=0)
    assert disp == "detach"
    assert "zero docker networks" in reason.lower()


def test_network_detach_takes_precedence_over_crash_loop():
    # langfuse-web signature: flapping AND detached → detach (recreate), not the
    # structural crash-loop path (a restart would loop forever on the same detachment).
    disp, _ = sv.classify_container(
        "running", "0", "unless-stopped", sv.CRASH_LOOP_RC, uptime_s=10,
        net_mode="kai-system_default", net_count=0)
    assert disp == "detach"


def test_host_and_service_netmodes_are_not_detach():
    # host / service: / container: legitimately show zero attached networks — not a detach
    for mode in ("host", "none", "service:tailscale", "container:kai-tailscale"):
        disp, _ = sv.classify_container(
            "running", "0", "unless-stopped", 0, uptime_s=99999,
            net_mode=mode, net_count=0)
        assert disp is None, f"{mode} wrongly flagged as detach"


def test_attached_container_is_not_detach():
    # a normally attached container (net_count>=1) is never a detach
    disp, _ = sv.classify_container(
        "running", "0", "unless-stopped", 0, uptime_s=99999,
        net_mode="kai-system_default", net_count=1)
    assert disp is None


def test_unknown_net_count_skips_detach_detection():
    # net_count=None (unknown) must stay conservative and not fire detach
    disp, _ = sv.classify_container(
        "running", "0", "unless-stopped", 0, uptime_s=99999,
        net_mode="kai-system_default", net_count=None)
    assert disp is None


def test_uptime_parser_handles_nanoseconds_and_zero_value():
    # docker emits nanosecond RFC3339; parser must not choke, and returns a positive age
    old = (datetime.now(timezone.utc) - timedelta(hours=5))
    stamp = old.strftime("%Y-%m-%dT%H:%M:%S.") + "123456789Z"
    age = sv._uptime_s(stamp)
    assert age is not None and age > 4 * 3600
    # docker's never-started zero value → None (unknown), not a huge bogus age
    assert sv._uptime_s("0001-01-01T00:00:00Z") is None
    assert sv._uptime_s("") is None
