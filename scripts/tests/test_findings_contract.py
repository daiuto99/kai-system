"""Proof the Findings Contract has teeth: it rejects / repairs a bare uncaused
alarm rather than letting it through. If these pass, the system cannot emit a
bad-status finding without a cause or an explicit not-yet-diagnosed."""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "shared"))
import findings  # noqa: E402


def test_bad_status_without_cause_is_stamped_undiagnosed():
    layers = {"x": {"status": "stale", "components": [{"name": "a", "status": "stale"}]}}
    n = findings.enforce_causes(layers)
    assert n == 2  # layer + component both had no cause
    assert layers["x"]["cause"] == findings.NOT_YET_DIAGNOSED
    assert layers["x"]["components"][0]["cause"] == findings.NOT_YET_DIAGNOSED


def test_verified_cause_is_preserved_and_inherited_by_layer():
    layers = {"x": {"status": "stale",
                    "components": [{"name": "a", "status": "stale", "cause": "real verified reason"}]}}
    n = findings.enforce_causes(layers)
    assert n == 0  # nothing undiagnosed: component carries a cause, layer inherits it
    assert layers["x"]["cause"] == "real verified reason"


def test_good_and_not_checked_need_no_cause():
    layers = {"g": {"status": "fresh", "components": []},
              "n": {"status": "not-checked", "components": []}}
    assert findings.enforce_causes(layers) == 0
    assert "cause" not in layers["g"]
    assert "cause" not in layers["n"]


def test_assert_contract_raises_on_bare_alarm():
    with pytest.raises(AssertionError):
        findings.assert_contract({"x": {"status": "fail", "components": []}})


def test_assert_contract_passes_after_enforce():
    layers = {"x": {"status": "alert", "components": [{"name": "a", "status": "alert"}]}}
    findings.enforce_causes(layers)
    findings.assert_contract(layers)  # must not raise
