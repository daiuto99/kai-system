"""
Deny/verify tests for the KAI-984 live transport + entrypoint enrollment gate.

Every path uses an INJECTED fake runner — no live SSH, node, or tailnet. Security properties:
fail-closed on every error, no runner call unless both pins hold, the secret VALUE only on stdin,
verify-before-install (mv is remotely gated on the digest), and an EXACT-TYPE boundary that defeats
hostile str/bytes subclasses (Codex inc4 findings #1–#6). Regression tests for each finding included.
"""
from __future__ import annotations

import json
import subprocess
from hashlib import sha256

import provision_transport
from provision_transport import OpenSshSecretTransport
from provision_run import enrollment_confirmed

_IP = "100.100.1.9"        # a valid Tailscale CGNAT (100.64.0.0/10) address
_MATERIAL = b"sk-super-secret-value-\x00\xff-bytes"
_DIGEST = sha256(_MATERIAL).hexdigest()
_OK = ("OK:" + _DIGEST + "\n").encode()   # what the remote echoes on a verified install


class FakeCompleted:
    def __init__(self, returncode, stdout) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = b""


class FakeRunner:
    def __init__(self, *, returncode: int = 0, stdout: bytes | None = None, raises: BaseException | None = None):
        self._rc = returncode
        self._stdout = stdout if stdout is not None else _OK
        self._raises = raises
        self.calls: list[dict] = []

    def __call__(self, argv, **kwargs):
        self.calls.append({"argv": argv, "kwargs": kwargs})
        if self._raises is not None:
            raise self._raises
        return FakeCompleted(self._rc, self._stdout)


def _provision(runner, *, ip=_IP, name="anthropic_api_key", material=_MATERIAL):
    return OpenSshSecretTransport(runner).provision(tailnet_ip=ip, secret_name=name, material=material)


# --- happy path -------------------------------------------------------------------------------

def test_written_and_verified_on_ok_literal():
    assert _provision(FakeRunner(stdout=_OK)) == {"written": True, "verified": True}


def test_surrounding_whitespace_still_verifies():
    assert _provision(FakeRunner(stdout=(b"  OK:" + _DIGEST.encode() + b"  \n")))["verified"] is True


# --- verify-before-install: a mismatch/failure moves nothing ----------------------------------

def test_remote_mismatch_moves_nothing():
    # The remote gates mv on the digest; a mismatch exits 3 with MISMATCH and installs nothing.
    assert _provision(FakeRunner(returncode=3, stdout=b"MISMATCH\n")) == {"written": False, "verified": False}


def test_ok_of_wrong_digest_not_verified():
    # rc 0 but the echoed OK digest is not ours => not the exact literal => deny.
    assert _provision(FakeRunner(stdout=(b"OK:" + sha256(b"other").hexdigest().encode() + b"\n"))) \
        == {"written": False, "verified": False}


def test_empty_stdout_not_verified():
    assert _provision(FakeRunner(returncode=0, stdout=b"")) == {"written": False, "verified": False}


def test_nonzero_rc_not_written():
    assert _provision(FakeRunner(returncode=255, stdout=b"")) == {"written": False, "verified": False}


def test_bare_hash_without_ok_prefix_not_verified():
    # old protocol echoed a bare hash; new protocol requires the OK: literal, so a bare hash fails.
    assert _provision(FakeRunner(stdout=(_DIGEST + "\n").encode()))["verified"] is False


# --- fail-closed on runner errors -------------------------------------------------------------

def test_timeout_fails_closed():
    assert _provision(FakeRunner(raises=subprocess.TimeoutExpired(cmd=["ssh"], timeout=30))) \
        == {"written": False, "verified": False}


def test_oserror_fails_closed():
    assert _provision(FakeRunner(raises=OSError("boom"))) == {"written": False, "verified": False}


# --- pins: no runner call unless BOTH hold ----------------------------------------------------

def test_path_separator_name_denied_no_call():
    r = FakeRunner()
    assert _provision(r, name="../etc/shadow") == {"written": False, "verified": False}
    assert r.calls == []


def test_dot_in_name_denied_no_call():
    r = FakeRunner()
    assert _provision(r, name="a.b") == {"written": False, "verified": False}
    assert r.calls == []


def test_empty_name_denied_no_call():
    r = FakeRunner()
    assert _provision(r, name="") == {"written": False, "verified": False}
    assert r.calls == []


def test_non_tailnet_ip_denied_no_call():
    r = FakeRunner()
    assert _provision(r, ip="10.0.0.5") == {"written": False, "verified": False}
    assert r.calls == []


def test_public_ip_denied_no_call():
    r = FakeRunner()
    assert _provision(r, ip="134.209.166.23") == {"written": False, "verified": False}
    assert r.calls == []


def test_malformed_ip_denied_no_call():
    r = FakeRunner()
    assert _provision(r, ip="not-an-ip") == {"written": False, "verified": False}
    assert r.calls == []


def test_empty_material_denied_no_call():
    r = FakeRunner()
    assert _provision(r, material=b"") == {"written": False, "verified": False}
    assert r.calls == []


# --- EXACT-TYPE boundary: hostile str/bytes SUBCLASSES cannot bypass (Codex #1/#3/#4/#5) -------

class _EvilName(str):
    def __iter__(self):                       # lie: iterate as safe chars
        return iter("safe")
    def __format__(self, spec):               # but format as an escaping path
        return "../../escape"


class _EvilIP(str):
    def __str__(self):
        return "100.64.0.1"                   # look like a CGNAT address to ipaddress
    def __format__(self, spec):
        return "evil.example"                 # but format as a hostname in argv


class _FlipBytes(bytes):
    calls = 0
    def __bytes__(self):                      # return different content across calls
        type(self).calls += 1
        return b"first" if type(self).calls == 1 else b"second"


class _BadLenBytes(bytes):
    def __len__(self):
        raise RuntimeError("SECRET-IN-EXCEPTION")


def test_hostile_name_subclass_denied_no_call():
    r = FakeRunner()
    assert _provision(r, name=_EvilName("../../escape")) == {"written": False, "verified": False}
    assert r.calls == []                      # never reaches the runner => no escaping remote path


def test_hostile_ip_subclass_denied_no_call():
    r = FakeRunner()
    assert _provision(r, ip=_EvilIP("x")) == {"written": False, "verified": False}
    assert r.calls == []                      # never targets leo@evil.example


def test_bytes_subclass_flip_denied_no_call():
    r = FakeRunner()
    assert _provision(r, material=_FlipBytes(b"x")) == {"written": False, "verified": False}
    assert r.calls == []                      # a value that can change between hash and send is refused


def test_hostile_bytes_len_cannot_leak():
    # __len__ raising with the secret in the message must NOT execute or escape — exact-type check
    # rejects the subclass BEFORE len() is ever called.
    r = FakeRunner()
    assert _provision(r, material=_BadLenBytes(b"x")) == {"written": False, "verified": False}
    assert r.calls == []


# --- fail-closed on hostile runner RESULT objects (Codex #2/#6) -------------------------------

class _EqBomb:
    def __eq__(self, other):
        raise RuntimeError("rc bomb")


def test_returncode_eq_bomb_fails_closed():
    r = FakeRunner(returncode=_EqBomb(), stdout=b"OK:x")
    assert _provision(r) == {"written": False, "verified": False}


def test_non_bytes_stdout_denied():
    class Hostile:
        def __str__(self): return "OK:" + _DIGEST
    assert _provision(FakeRunner(stdout=Hostile())) == {"written": False, "verified": False}


def test_forged_returncode_object_cannot_verify():
    # A returncode whose __eq__/__bool__ resolves truthy must NOT forge success even with valid stdout.
    # Exact-type type(rc) is int rejects it before the comparison.
    class Truth:
        def __bool__(self): return True
    class ForgedRC:
        def __eq__(self, other): return Truth()
    assert _provision(FakeRunner(returncode=ForgedRC(), stdout=_OK)) == {"written": False, "verified": False}


def test_hostile_constructor_dir_does_not_escape():
    # A malformed remote_secrets_dir must make provision() return deny, never raise — command
    # construction is inside the fail-closed guard.
    class BadDir:
        def __add__(self, other): raise RuntimeError("construction escaped")
    t = OpenSshSecretTransport(FakeRunner(), remote_secrets_dir=BadDir())
    assert t.provision(tailnet_ip=_IP, secret_name="x", material=b"x") == {"written": False, "verified": False}


def test_non_ascii_stdout_denied():
    # strict ascii decode: a non-ascii byte in stdout fails closed (no silent "ignore").
    assert _provision(FakeRunner(stdout=(b"OK:" + _DIGEST.encode() + b"\xff\n"))) \
        == {"written": False, "verified": False}


def test_stdout_property_toctou_cannot_forge():
    # A hostile .stdout PROPERTY yields harmless non-matching bytes on read 1 (passes the type-check,
    # fails the literal) and a forging object on read 2. The old double-read code would type-check the
    # bytes then decode the forge to "OK:<expected>". We snapshot stdout EXACTLY ONCE, so read 2 (the
    # forge) is never reached — proven by asserting reads == 1.
    class Forge:
        def decode(self, *a, **k): return "OK:" + _DIGEST
    class ChameleonStdout:
        def __init__(self): self.reads = 0
        @property
        def stdout(self):
            self.reads += 1
            return b"definitely-not-OK" if self.reads == 1 else Forge()
        returncode = 0
        stderr = b""
    comp = ChameleonStdout()
    class R:
        def __call__(self, argv, **kw): return comp
    assert _provision(R()) == {"written": False, "verified": False}
    assert comp.reads == 1   # stdout snapshotted exactly once => no TOCTOU window


def test_stateful_returncode_bool_cannot_escape():
    # returncode == 0 returns a stateful bool-like object; bool() is coerced INSIDE the guard, so a
    # __bool__ that raises on a later call denies rather than escaping the function.
    class Bomb:
        def __eq__(self, other): return self   # rc == 0 yields a hostile bool-like...
        def __bool__(self): raise RuntimeError("verdict bomb")   # ...that raises when coerced
    class Comp:
        returncode = Bomb(); stdout = b"OK:" + _DIGEST.encode(); stderr = b""
    class R:
        def __call__(self, argv, **kw): return Comp()
    # Must return a dict (fail-closed), never raise.
    assert _provision(R()) == {"written": False, "verified": False}


# --- L18: value only on stdin, NEVER in argv --------------------------------------------------

def test_value_only_on_stdin_never_in_argv():
    r = FakeRunner()
    _provision(r)
    call = r.calls[0]
    assert call["kwargs"]["input"] == _MATERIAL
    argv_blob = " ".join(str(a) for a in call["argv"])
    assert "super-secret-value" not in argv_blob
    assert _MATERIAL.decode("latin-1") not in argv_blob
    assert _DIGEST in argv_blob               # the (non-secret) expected hash IS passed for the gate
    assert call["kwargs"]["capture_output"] is True


def test_argv_targets_canonical_tailnet_ip():
    r = FakeRunner()
    _provision(r)
    assert any(f"@{_IP}" in str(a) for a in r.calls[0]["argv"])


def test_return_is_only_two_booleans():
    out = _provision(FakeRunner())
    assert set(out.keys()) == {"written", "verified"}
    assert all(isinstance(v, bool) for v in out.values())


# --- R1 enrollment gate -----------------------------------------------------------------------

def test_enrollment_gate_rejects_seeded_pending(tmp_path):
    p = tmp_path / "allow.json"
    p.write_text(json.dumps({"enrollment_status": "seeded_pending_leo_confirmation", "nodes": {}}))
    assert enrollment_confirmed(str(p)) is False


def test_enrollment_gate_accepts_confirmed(tmp_path):
    import tailnet_guard
    p = tmp_path / "allow.json"
    p.write_text(json.dumps({"enrollment_status": tailnet_guard._CONFIRMED_ENROLLMENT, "nodes": {}}))
    assert enrollment_confirmed(str(p)) is True


def test_enrollment_gate_matches_tailnet_guard_literal():
    import provision_run
    import tailnet_guard
    assert provision_run._CONFIRMED == tailnet_guard._CONFIRMED_ENROLLMENT


def test_enrollment_gate_missing_file_is_false(tmp_path):
    assert enrollment_confirmed(str(tmp_path / "nope.json")) is False


def test_enrollment_gate_bad_json_is_false(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not json")
    assert enrollment_confirmed(str(p)) is False


def test_live_allowlist_is_confirmed():
    # Retired the pre-enrollment tripwire: Leo ran the enrollment ceremony
    # 2026-07-31 (enrollment_status seeded_pending_leo_confirmation -> confirmed).
    # The live allowlist is now the enrolled, deliberately-confirmed state.
    from pathlib import Path
    live = Path(__file__).resolve().parent / "kai_node_allowlist.json"
    assert enrollment_confirmed(str(live)) is True
