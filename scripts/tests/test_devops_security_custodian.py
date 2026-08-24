"""KAI-52 Phase 2 — security custodian decision logic."""
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "devops_security_custodian",
    Path(__file__).resolve().parent.parent / "devops_security_custodian.py")
se = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(se)


def test_green_verdict_yields_no_finding():
    assert se.verdict_severity("public TLS certs valid (kai 84d)", False) is None


def test_warn_verdict_is_warn():
    assert se.verdict_severity("WARN public TLS: kai 10d [S1-B2]", False) == "warn"


def test_raised_check_is_crit():
    # a RED check raises → crit, regardless of any returned string
    assert se.verdict_severity(None, True) == "crit"
    assert se.verdict_severity("anything", True) == "crit"


def test_empty_return_is_not_a_finding():
    assert se.verdict_severity("", False) is None
    assert se.verdict_severity(None, False) is None


def test_jobs_secret_leak_routes_to_decision():
    specs = {name: (disp, action) for _attr, name, disp, action in se._security_check_specs()}
    from devops_ownership import DECISION, STRUCTURAL
    assert specs["jobs_secret_leak"][0] == DECISION  # a live cleartext leak is a genuine Leo decision


def test_cert_and_auth_route_to_structural_never_auto():
    specs = {name: disp for _attr, name, disp, _action in se._security_check_specs()}
    from devops_ownership import STRUCTURAL, AUTO
    # certs are Cloudflare-managed; rotation is decision-class — never a silent AUTO
    for name in ("cert_expiry", "tailscale_key", "codex_auth", "cloudways_auth"):
        assert specs[name] == STRUCTURAL
        assert specs[name] != AUTO


def test_codex_action_says_do_not_flip_billing():
    specs = {name: action for _attr, name, _disp, action in se._security_check_specs()}
    assert "DO NOT" in specs["codex_auth"]  # the standing do-not-touch decision is encoded


def test_every_baseline_check_is_owned():
    # all 8 reused security checks carry a disposition (none left unrouted)
    names = {name for _attr, name, _disp, _action in se._security_check_specs()}
    assert names == {"cert_expiry", "tailscale_key", "codex_auth", "cloudways_auth",
                     "credential_registry", "source_drift", "secret_permissions",
                     "jobs_secret_leak"}
