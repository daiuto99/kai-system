"""KAI-44 — autonomous DevOps disk custodian decision logic."""
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "devops_disk_remediation",
    Path(__file__).resolve().parent.parent / "devops_disk_remediation.py")
dd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dd)


def test_engages_at_or_above_warn():
    assert dd.should_engage(80) is True
    assert dd.should_engage(95) is True
    assert dd.should_engage(79) is False


def test_custom_warn_threshold():
    assert dd.should_engage(50, warn=40) is True
    assert dd.should_engage(39, warn=40) is False


def test_structural_paths_never_auto_reclaimed():
    # data / backups / mirror / containerd = DevOps must NOT auto-destroy → structural
    assert dd.is_structural("/mnt/mirror") is True
    assert dd.is_structural("/mnt/storage") is True
    assert dd.is_structural("/home/leo/backups") is True
    assert dd.is_structural("/var/lib/containerd") is True
    assert dd.is_structural("/var/lib/docker") is True


def test_logs_are_not_structural():
    # /var/log is the safe, auto-reclaimable class
    assert dd.is_structural("/var") is False
    assert dd.is_structural("/usr") is False


def test_top_structural_ranks_and_filters():
    comp = {
        "/var/lib/containerd": 23_000_000,
        "/home/leo/backups": 7_000_000,
        "/var": 25_000_000,          # not structural (parent /var, logs live here)
        "/usr": 3_000_000,           # not structural
        "/mnt": 1_000_000,           # structural
    }
    top = dd.top_structural(comp, n=3)
    paths = [p for p, _ in top]
    assert paths[0] == "/var/lib/containerd"   # biggest structural first
    assert "/var" not in paths and "/usr" not in paths  # non-structural excluded
    assert "/home/leo/backups" in paths


def test_dry_run_takes_no_destructive_action(monkeypatch):
    calls = {"root_docker": 0}
    monkeypatch.setattr(dd, "_root_docker",
                        lambda *a, **k: calls.__setitem__("root_docker", calls["root_docker"] + 1) or type("R", (), {"stdout": ""})())
    monkeypatch.setattr(dd, "root_pct", lambda: 85)  # engaged
    monkeypatch.setattr(dd, "_record", lambda rec: None)
    rec = dd.run(dry=True, warn=80, crit=90)
    assert rec["engaged"] is True
    # dry-run listing may call _root_docker for previews, but NEVER a -delete/-truncate:
    # assert no action string claims a mutation happened
    assert all("would" in a or "none" in a.lower() for a in rec["actions"])
