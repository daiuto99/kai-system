"""CUR-5 — gated WordPress update-apply (hostops.update_wp + hostops.update).

The apply half of the System Currency Program. A WP core/plugin update on a live
client site is risky, so it rides the SAME council-gated host-ops rail as
publish_post, with the same two-layer "never autonomous" floor:
  1. the org-model `classify` chokepoint forces approval for a client-owned site;
  2. a code-level fail-closed guard refuses any authorization resolved autonomous.
These tests pin: component validation (argv-injection safety), gate-required,
autonomous-refused, exact-component gate binding, honest read-back (never a faked
"current"), the wp-cli command shape (single named component, never --all), and
the gated workflow wiring (gate binds op+site+component, exec is single-use).
"""
import json

import pytest

from autonomy_decisions import classify
from capabilities import get_capability
from capabilities.hostops import update_wp, OpenSshTransport, HostOpsTarget


CONSUME_CALLS = []  # records (gate_id, operation, site, resource) per fake consume


@pytest.fixture(autouse=True)
def configured(tmp_path, monkeypatch):
    sites = tmp_path / "sites.json"
    sites.write_text(json.dumps({"sites": {"site": {
        "cloudways_app_id": "123",
        "cloudways_sys_user": "appuser",
        "cloudways_server_id": "456",
        "cloudways_fqdn": "host.example",
        "url": "https://example.com",
        "owner": "client",   # non-Leo owner => classify() forces approval
    }}}))
    monkeypatch.setattr("capabilities.hostops._SITES_JSON", sites)
    monkeypatch.setattr("policy.autonomy._SITES_JSON", sites)

    org = tmp_path / "org_model.json"
    org.write_text(json.dumps({"routing_rules": {"infrastructure_task": {
        "high_risk_threshold": ["structural change", "new service", "publish"]}}}))
    monkeypatch.setattr("autonomy_decisions._ORG_MODEL_PATH", org)

    CONSUME_CALLS.clear()

    def fake_consume(gate_id, operation, site, resource=None):
        CONSUME_CALLS.append({"gate_id": gate_id, "operation": operation,
                              "site": site, "resource": resource})
        return (gate_id == "approved-gate" and operation == "update_wp"
                and site == "site")

    monkeypatch.setattr("engine.engine.consume_hostops_gate", fake_consume)


class FakeTransport:
    def __init__(self, updated=True, current=True, reason="", raises=None):
        self.updated, self.current, self.calls = updated, current, []
        self.reason, self.raises = reason, raises

    def update_wp(self, target, component):
        self.calls.append(("update_wp", target.audit_identity, component))
        if self.raises is not None:
            raise self.raises
        return {"updated": self.updated, "current": self.current, "component": component,
                "before": "1.0" if self.updated else "2.0", "after": "2.0",
                "reason": self.reason or ("" if (self.updated and self.current) else "no_version_change")}


# ── Capability registration + the "never autonomous" spine ────────────────────

def test_capability_is_registered():
    assert get_capability("hostops.update_wp")


def test_spine_client_site_update_is_never_autonomous():
    decision = classify({"op": "update_wp", "site": "site", "owner": "client",
                         "risk": "", "external_party": True})
    assert decision.mode == "approve"


def test_update_fails_closed_without_a_verified_gate():
    assert update_wp("site", "core").error["type"] == "gate_required"
    assert update_wp("site", "akismet",
                     gate_id="not-a-real-handle").error["type"] == "gate_required"


def test_autonomous_authorization_is_refused(monkeypatch):
    # Defence against org-model drift: even if _gate yields "autonomous", refuse.
    monkeypatch.setattr("capabilities.hostops._gate", lambda *a, **k: "autonomous")
    t = FakeTransport()
    result = update_wp("site", "core", gate_id="approved-gate", transport=t)
    assert result.error["type"] == "autonomous_wp_update_forbidden"
    assert t.calls == []  # never touched the site


# ── Component validation (argv-injection safety) ──────────────────────────────

@pytest.mark.parametrize("bad", [
    "", "--all", "Core", "ak smet", "a;rm -rf", "../evil", "plugin&x", 5, None,
])
def test_invalid_component_is_rejected(bad):
    assert update_wp("site", bad,
                     gate_id="approved-gate").error["type"] == "input_not_allowed"


@pytest.mark.parametrize("good", ["core", "akismet", "wp-super-cache", "w3-total-cache"])
def test_valid_components_pass_validation(good):
    # Reaches the gate stage (transport stubbed happy) — i.e. not input_not_allowed.
    t = FakeTransport()
    result = update_wp("site", good, gate_id="approved-gate", transport=t)
    assert result.ok and result.data["component"] == good


# ── Exact-component gate binding ──────────────────────────────────────────────

def test_gate_binds_to_the_exact_component():
    t = FakeTransport()
    update_wp("site", "akismet", gate_id="approved-gate", transport=t)
    assert CONSUME_CALLS[-1] == {"gate_id": "approved-gate", "operation": "update_wp",
                                 "site": "site", "resource": "akismet"}


# ── Honest read-back — a claimed success is proven, never faked ───────────────

def test_update_that_leaves_an_available_update_fails_not_faked_green():
    t = FakeTransport(updated=True, current=False)  # wp-cli ran but update still available
    result = update_wp("site", "core", gate_id="approved-gate", transport=t)
    assert not result.ok and result.error["type"] == "wp_update_failed"


def test_already_current_no_op_is_not_reported_as_applied():
    # The Codex-caught foot-gun: component already current => no version transition.
    # Must NOT read as a successful apply.
    t = FakeTransport(updated=False, current=True, reason="no_version_change")
    result = update_wp("site", "akismet", gate_id="approved-gate", transport=t)
    assert not result.ok and result.error["type"] == "wp_update_failed"
    assert result.error["reason"] == "no_version_change"


def test_timeout_never_leaks_the_ssh_argv():
    import subprocess
    t = FakeTransport(raises=subprocess.TimeoutExpired(cmd=["ssh", "-i", "/run/secrets/key",
                                                            "master@1.2.3.4"], timeout=180))
    result = update_wp("site", "core", gate_id="approved-gate", transport=t)
    assert not result.ok and result.error["type"] == "wp_update_timeout"
    blob = json.dumps(result.error)
    assert "ssh" not in blob and "master@" not in blob and "/run/secrets" not in blob


def test_happy_path_proves_a_version_transition():
    t = FakeTransport(updated=True, current=True)
    result = update_wp("site", "core", gate_id="approved-gate", transport=t)
    ev = result.verification["evidence"]
    assert result.ok and ev["current"] is True
    assert ev["before"] != ev["after"]              # a real transition is recorded
    assert ev["authorization"] == "gate"
    assert t.calls[0][2] == "core"


# ── Transport: wp-cli command shape (single named component, never --all) ─────

def _target():
    return HostOpsTarget(host="host.example", app_user="appuser",
                         app_id="123", server_id="456")


def _runner_for(stdout, returncode=0, capture=None):
    def runner(argv, **kw):
        if capture is not None:
            capture["argv"] = argv
        class R:  # noqa: E306
            pass
        R.returncode, R.stdout, R.stderr = returncode, stdout, ""
        return R()
    return runner


def test_transport_core_command_shape_and_transition():
    cap = {}
    # before=6.5.1, after=6.5.2 (real transition), 0 remaining => current.
    proof = OpenSshTransport(
        runner=_runner_for("6.5.1\n6.5.2\n0\n", capture=cap)).update_wp(_target(), "core")
    remote = cap["argv"][-1]
    assert "wp core version" in remote and "wp core update" in remote
    assert "wp core check-update" in remote and "--all" not in remote
    assert proof["updated"] is True and proof["current"] is True
    assert proof["before"] == "6.5.1" and proof["after"] == "6.5.2"


def test_transport_plugin_command_shape_touches_only_the_named_slug():
    cap = {}
    proof = OpenSshTransport(
        runner=_runner_for("4.0\n4.1\nnone\n", capture=cap)).update_wp(_target(), "akismet")
    remote = cap["argv"][-1]
    # Safe slug => shlex.quote is a no-op; only this one plugin is touched, never --all.
    assert "wp plugin update akismet --quiet" in remote
    assert "wp plugin get akismet --field=version" in remote
    # `update` is a `wp plugin list` field, NOT a `wp plugin get` field.
    assert "wp plugin list --name=akismet --field=update" in remote
    assert "wp plugin get akismet --field=update" not in remote
    assert "--all" not in remote and "wp plugin update --all" not in remote
    assert proof["updated"] is True and proof["current"] is True


def test_transport_already_current_no_transition_is_not_updated():
    # before == after (no version change), 0 remaining: honest no-op, not "applied".
    proof = OpenSshTransport(runner=_runner_for("6.5.2\n6.5.2\n0\n")).update_wp(_target(), "core")
    assert proof["updated"] is False and proof["current"] is True


def test_transport_update_still_available_is_not_current():
    proof = OpenSshTransport(runner=_runner_for("6.5.1\n6.5.2\n1\n")).update_wp(_target(), "core")
    assert proof["updated"] is True and proof["current"] is False
    # A real transition that still leaves an update pending is NOT "no_version_change".
    assert proof["reason"] == "update_incomplete"


def test_transport_already_current_reason_is_no_version_change():
    proof = OpenSshTransport(runner=_runner_for("6.5.2\n6.5.2\n0\n")).update_wp(_target(), "core")
    assert proof["updated"] is False and proof["reason"] == "no_version_change"


@pytest.mark.parametrize("good", ["6.5.2", "1.2.3-beta1", "6", "4.0", "2.0.1+build3"])
def test_clean_version_accepts_real_versions(good):
    from capabilities.hostops import _clean_version
    assert _clean_version(good) == good


@pytest.mark.parametrize("bad", [
    "1hunter2",           # no dots — not a version, a secret-shaped token
    "DB_PASSWORD=x",      # contains '='
    "1" + "A" * 1000,     # over the length bound
    "", "none", "unknown",
])
def test_clean_version_rejects_nonversions(bad):
    from capabilities.hostops import _clean_version
    assert _clean_version(bad) == "unknown"


def test_transport_nonzero_exit_is_a_clean_failure():
    proof = OpenSshTransport(runner=_runner_for("", returncode=1)).update_wp(_target(), "akismet")
    assert proof["updated"] is False and proof["current"] is False
    assert proof["reason"] == "wp_cli_error"


def test_transport_timeout_returns_fixed_token_never_the_argv():
    import subprocess

    def runner(argv, **kw):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=180)

    proof = OpenSshTransport(runner=runner).update_wp(_target(), "core")
    assert proof["updated"] is False and proof["current"] is False
    blob = json.dumps(proof)
    assert "ssh" not in blob and "master_vvbwxpwpcc" not in blob and "/run/secrets" not in blob


def test_transport_sanitizes_nonversion_stdout_out_of_the_result():
    # A hostile/garbled read-back (e.g. a leaked secret on the version line) must
    # never survive into the persisted proof — it is reduced to 'unknown' (L18).
    proof = OpenSshTransport(
        runner=_runner_for("DB_PASSWORD=hunter2\n6.5.2\n0\n")).update_wp(_target(), "core")
    blob = json.dumps(proof)
    assert "hunter2" not in blob and "DB_PASSWORD" not in blob
    assert proof["before"] == "unknown"
    # before is unknown => cannot prove a transition => not reported as updated,
    # and the reason is the honest "version_unverifiable" (NOT "no_version_change":
    # the update may well have landed; we simply cannot verify the prior version).
    assert proof["updated"] is False
    assert proof["reason"] == "version_unverifiable"


def test_transport_truncated_output_is_unparsable_not_faked():
    proof = OpenSshTransport(runner=_runner_for("6.5.2\n")).update_wp(_target(), "core")
    assert proof["updated"] is False and proof["current"] is False
    assert proof["reason"] == "unparsable"


def test_transport_failed_verification_fails_closed_not_current():
    # Codex re-review catch: if `wp core check-update` fails, the && chain breaks
    # and printf never emits the third line — so the read-back is unparsable and we
    # fail CLOSED. A broken post-update verification must never read as "current".
    proof = OpenSshTransport(runner=_runner_for("6.5.1\n6.5.2\n")).update_wp(_target(), "core")
    assert proof["current"] is False and proof["updated"] is False
    assert proof["reason"] == "unparsable"


# ── Gated workflow wiring (mirrors test_hostops_workflow) ─────────────────────

def test_workflow_exec_step_is_single_use():
    from workflows.hostops_update import HostopsUpdateWorkflow
    steps = {s.name: s for s in HostopsUpdateWorkflow.steps}
    assert steps["update_exec"].max_retries == 0
    assert steps["update_exec"].capability == "hostops.update_wp"


def test_workflow_gate_binds_op_site_component_and_omits_payload(monkeypatch):
    from unittest import mock
    from models import CapabilityResult
    from workflows.hostops_update import HostopsUpdateWorkflow

    gate = mock.Mock(return_value=CapabilityResult(
        ok=True, status="awaiting_gate", data={"gate_id": "g1"},
        verification={"verified": False},
    ))
    ctx = {"site": "site", "component": "akismet"}
    with (
        mock.patch("capabilities.get_capability", return_value=gate),
        mock.patch("capabilities.hostops.audit_identity", return_value="cloudways-app:123:appuser"),
    ):
        result = HostopsUpdateWorkflow("job-cur5")._run_gate({"id": "s1"}, ctx)

    assert result.status == "awaiting_gate"
    assert gate.call_args.kwargs["gate_type"] == "hostops_update_wp"
    brief = gate.call_args.kwargs["brief"]
    assert brief["hostops_operation"] == "update_wp"
    assert brief["site"] == "site"
    assert brief["hostops_resource"] == "akismet"   # the exact-component binding
    # No payload/secret material of any kind in the persisted brief.
    blob = json.dumps(brief)
    assert "secret" not in blob and "material" not in blob


@pytest.mark.parametrize("bad_ctx", [
    {"site": "site"},                       # component missing entirely
    {"site": "site", "component": ""},      # empty
    {"site": "site", "component": []},      # falsy non-str
    {"site": "site", "component": "--all"}, # invalid shape
])
def test_workflow_gate_fails_hard_on_missing_or_invalid_component(bad_ctx):
    # Codex-caught: the single-purpose update workflow must NOT green-no-op when
    # the component is absent/invalid — no gate opens and it fails permanently.
    from unittest import mock
    from workflows.hostops_update import HostopsUpdateWorkflow

    gate = mock.Mock()
    with mock.patch("capabilities.get_capability", return_value=gate):
        result = HostopsUpdateWorkflow("job-bad")._run_gate({"id": "s1"}, bad_ctx)
    assert result.status == "failed_permanent"
    assert result.error["type"] == "input_not_allowed"
    gate.assert_not_called()   # no council gate is ever opened for an invalid request


def test_workflow_exec_fails_hard_on_missing_component():
    from workflows.hostops_update import HostopsUpdateWorkflow
    result = HostopsUpdateWorkflow("job-bad")._step_update({"site": "site"})
    assert result.status == "failed_permanent"
    assert result.error["type"] == "input_not_allowed"
