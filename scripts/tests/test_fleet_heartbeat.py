"""KAI-1047 · unit tests for the fleet-heartbeat pure logic (no IO)."""
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "fleet_heartbeat", Path(__file__).resolve().parents[1] / "fleet_heartbeat.py")
fh = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fh)


# ── parse_remote_probe ────────────────────────────────────────────────────────

def test_parse_linux_probe():
    got = fh.parse_remote_probe("boot_epoch=1785948437\ndocker=1\n")
    assert got["boot_epoch"] == 1785948437
    assert got["services"] == {"docker": True}


def test_parse_macos_probe():
    got = fh.parse_remote_probe("boot_epoch=1785900000\ncolima=1\nollama=0\n")
    assert got["boot_epoch"] == 1785900000
    assert got["services"] == {"colima": True, "ollama": False}


def test_parse_macos_boottime_raw():
    # The exact live string from the retired macOS mini (pre-reimage) `sysctl -n kern.boottime` (KAI-1180).
    # The old remote greedy sed matched `usec` and returned 938717 -> last_boot 1970.
    raw = "boottime_raw={ sec = 1787275622, usec = 938717 } Thu Aug 20 21:27:02 2026\ncolima=1\nollama=0\n"
    got = fh.parse_remote_probe(raw)
    assert got["boot_epoch"] == 1787275622
    assert got["services"] == {"colima": True, "ollama": False}


def test_parse_macos_boottime_raw_ignores_usec():
    # Regression guard: even if usec sorts/appears such that a greedy match would
    # grab it, the \bsec anchor must never return the microseconds field.
    got = fh.parse_remote_probe("boottime_raw={ sec = 1000000000, usec = 42 }\n")
    assert got["boot_epoch"] == 1000000000


def test_parse_empty_boot_epoch():
    got = fh.parse_remote_probe("boot_epoch=\ncolima=0\n")
    assert got["boot_epoch"] is None
    assert got["services"] == {"colima": False}


def test_parse_garbage_is_ignored():
    got = fh.parse_remote_probe("hello world\nboot_epoch=notanumber\n")
    assert got["boot_epoch"] is None


# ── build_host_entry ──────────────────────────────────────────────────────────

def test_entry_online_ssh_ok():
    e = fh.build_host_entry(
        "kai-worker", "nzk", {"online": True, "ips": ["100.78.94.80"], "last_seen": None},
        {"boot_epoch": 1785948437, "services": {"docker": True}}, now_epoch=1785950000,
        ssh_expected=True)
    assert e["reachable"] is True and e["ssh_ok"] is True and e["ssh_expected"] is True
    assert e["boot_epoch"] == 1785948437
    assert e["last_boot"].endswith("Z")
    assert "degraded" not in e


def test_entry_offline_is_unreachable():
    e = fh.build_host_entry(
        "kai-mini", "ntz", {"online": False, "ips": ["100.85.243.2"], "last_seen": "x"},
        None, now_epoch=1785950000, ssh_expected=True)
    assert e["reachable"] is False and e["ssh_ok"] is False
    assert "offline" in e["degraded"]


def test_entry_tailnet_flap_but_ssh_reachable():
    # KAI-1176: Tailscale Online flag flapped to false but the ssh probe still
    # answered (napping mini). ssh_ok overrides the stale flag -> reachable, no page.
    e = fh.build_host_entry(
        "kai-mini", "ntz", {"online": False, "ips": ["100.85.243.2"], "last_seen": "x"},
        {"boot_epoch": 1785948437, "services": {"ollama": True}}, now_epoch=1785950000,
        ssh_expected=True)
    assert e["reachable"] is True and e["ssh_ok"] is True
    assert e["tailnet_online"] is False
    assert "degraded" not in e


def test_entry_online_ssh_expected_but_down_is_flagged_blind():
    # The exact ticket gap: a WIRED node 'on but SSH-unreachable after reboot'.
    e = fh.build_host_entry(
        "kai-mini", "ntz", {"online": True, "ips": ["100.85.243.2"], "last_seen": None},
        None, now_epoch=1785950000, ssh_expected=True)
    assert e["reachable"] is True and e["ssh_ok"] is False and e["ssh_expected"] is True
    assert "boot/services blind" in e["degraded"]


def test_entry_online_ssh_not_expected_is_intentional():
    # mac-mini: online, no transport wiring -> ssh-off is intentional, not a fault.
    e = fh.build_host_entry(
        "mac-mini", "nwU", {"online": True, "ips": ["100.79.114.44"], "last_seen": None},
        None, now_epoch=1785950000, ssh_expected=False)
    assert e["reachable"] is True and e["ssh_expected"] is False
    assert "intentionally off" in e["degraded"]


# ── _transport_valid (R3-1: corrupt/zeroed inventory is NOT 'loaded') ─────────

def test_transport_valid_good():
    assert fh._transport_valid({
        "_note": "x",
        "kai-worker": {"ssh_user": "leo", "ssh_key": "/k", "remote_secrets_dir": "/d"},
    }) is True


def test_transport_valid_empty_is_false():
    assert fh._transport_valid({}) is False
    assert fh._transport_valid({"_note": "only a note"}) is False


def test_transport_valid_missing_keys_is_false():
    assert fh._transport_valid({"kai-worker": {"ssh_user": "leo"}}) is False
    assert fh._transport_valid({"kai-worker": "not-a-dict"}) is False


# ── summarize ─────────────────────────────────────────────────────────────────

def test_summary_reports_down_and_degraded():
    state = {"hosts": {
        "kai-worker": {"reachable": True, "ssh_ok": True},
        "kai-mini": {"reachable": False, "ssh_ok": False},
        "mac-mini": {"reachable": True, "ssh_ok": False},
    }}
    s = fh.summarize(state)
    assert "2/3 reachable" in s
    assert "DOWN: kai-mini" in s
    assert "degraded: mac-mini" in s
