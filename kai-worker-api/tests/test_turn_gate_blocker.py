"""Server-side turn-gate blocker authority (F2 / bbc788da).

Proves the invariants that used to live on the agent's own uid now hold on the
worker: INV1 trusted issuance, INV2 atomic one-shot, INV4 per-session yield bound
— all on non-agent-writable state, reached only through these endpoints.
"""
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from routes import turn_gate as tg  # noqa: E402


def _fresh_state():
    """Point the module at a throwaway state dir (isolates each test)."""
    d = Path(tempfile.mkdtemp(prefix="tg_srv_"))
    tg._STATE = d
    tg._LEDGER = d / "ledger.jsonl"
    tg._CLAIMS = d / "claims"
    tg._YIELDS = d / "yields.json"
    return d


def _reg(action="x", klass="lock_asset", target="scripts/check_context.py",
         session="s", ts="2026-09-03T00:00:00+00:00", evidence="y" * 25):
    return tg.register_blocker(tg.RegisterBody(
        action=action, klass=klass, target=target, evidence=evidence,
        ticket="", session=session, ts=ts, target_verified=True))


def _claim(action="x", klass="lock_asset", target="scripts/check_context.py",
           session="s", ts="2026-09-03T00:00:00+00:00"):
    return tg.claim_blocker(tg.ClaimBody(
        action=action, klass=klass, target=target, session=session, ts=ts))


def test_register_then_claim_honors_once():
    _fresh_state()
    assert _reg()["registered"] is True
    assert _claim()["honored"] is True


def test_inv2_second_claim_is_replay_refused():
    _fresh_state()
    _reg()
    assert _claim()["honored"] is True
    d = _claim()
    assert d["honored"] is False and "already claimed" in d["reason"]


def test_inv1_unledgered_claim_refused():
    # a claim with no matching register row is untrusted -> refused
    _fresh_state()
    d = _claim()
    assert d["honored"] is False and "untrusted" in d["reason"]


def test_non_stop_suppressing_class_refused():
    _fresh_state()
    _reg(klass="scope_change")
    d = _claim(klass="scope_change")
    assert d["honored"] is False and "stop-suppressing" in d["reason"]


def test_register_validates_class_and_evidence():
    _fresh_state()
    assert tg.register_blocker(tg.RegisterBody(
        action="x", klass="not_a_class", target="t", evidence="y" * 25,
        session="s", ts="t"))["registered"] is False
    assert tg.register_blocker(tg.RegisterBody(
        action="x", klass="lock_asset", target="t", evidence="short",
        session="s", ts="t"))["registered"] is False


def test_inv4_yield_bound_enforced_per_session():
    # distinct cards (distinct ts) within one session: honored up to the bound,
    # then refused as over_bound — a fresh card per turn no longer buys a yield.
    _fresh_state()
    honored = 0
    for i in range(tg._YIELD_BOUND + 2):
        ts = f"2026-09-03T00:00:0{i}+00:00"
        _reg(ts=ts)
        d = _claim(ts=ts)
        if d.get("honored"):
            honored += 1
        else:
            assert d.get("over_bound") is True
    assert honored == tg._YIELD_BOUND


def test_inv4_bound_is_per_session_not_global():
    _fresh_state()
    for i in range(tg._YIELD_BOUND):
        ts = f"2026-09-03T00:00:0{i}+00:00"
        _reg(session="a", ts=ts)
        assert _claim(session="a", ts=ts)["honored"]
    # a different session starts fresh
    _reg(session="b", ts="2026-09-03T01:00:00+00:00")
    assert _claim(session="b", ts="2026-09-03T01:00:00+00:00")["honored"] is True


def test_inv2_atomic_claim_exactly_once_under_contention():
    _fresh_state()
    _reg()
    out = []
    def worker():
        out.append(_claim().get("honored"))
    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert out.count(True) == 1


def test_status_reports_yield_count():
    _fresh_state()
    _reg()
    _claim()
    st = tg.blocker_status(session="s")
    assert st["yield_count"] == 1 and st["bound"] == tg._YIELD_BOUND


def test_inv4_write_failure_fails_closed():
    # Codex P0: if the yield counter cannot be durably persisted, the claim must be
    # REFUSED (fail-closed) — never re-read a lower count and grant a fresh yield.
    _fresh_state()
    _reg()
    orig = tg.os.replace
    tg.os.replace = lambda *a, **k: (_ for _ in ()).throw(OSError("disk full"))
    try:
        res = _claim()
    finally:
        tg.os.replace = orig
    assert res["honored"] is False and res.get("fail_closed") is True


def test_inv4_corrupt_counter_fails_closed():
    # a corrupt/torn counter store must fail-closed, not silently reset to {} and
    # re-grant yields.
    d = _fresh_state()
    _reg()
    d.mkdir(parents=True, exist_ok=True)
    tg._YIELDS.write_text("{ this is not valid json")
    res = _claim()
    assert res["honored"] is False and res.get("fail_closed") is True


def test_bound_not_exceeded_when_writes_fail_from_persisted_count():
    # the exact Codex scenario: persisted count at the bound, then repeated fresh
    # identities under a write failure must NOT each return honored:True.
    _fresh_state()
    for i in range(tg._YIELD_BOUND):
        ts = f"2026-09-03T00:00:0{i}+00:00"
        _reg(ts=ts)
        assert _claim(ts=ts)["honored"] is True
    orig = tg.os.replace
    tg.os.replace = lambda *a, **k: (_ for _ in ()).throw(OSError("disk full"))
    try:
        honored_after = 0
        for i in range(3):
            ts = f"2026-09-03T00:01:0{i}+00:00"
            _reg(ts=ts)
            if _claim(ts=ts).get("honored"):
                honored_after += 1
    finally:
        tg.os.replace = orig
    assert honored_after == 0
