"""KAI-47 Phase 2 — services custodian decision logic."""
import importlib.util
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


def test_running_but_flapping_is_structural_not_auto():
    # the live case: kai-tailscale running with RestartCount>=5 → do NOT auto-restart
    disp, reason = sv.classify_container("running", "0", "always", sv.CRASH_LOOP_RC)
    assert disp == "structural"
    assert "flapping" in reason.lower()


def test_crash_loop_threshold_boundary():
    assert sv.classify_container("exited", "1", "always", sv.CRASH_LOOP_RC - 1)[0] == "auto"
    assert sv.classify_container("exited", "1", "always", sv.CRASH_LOOP_RC)[0] == "structural"
