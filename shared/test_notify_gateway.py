#!/usr/bin/env python3
"""COMMS Phase 1 — notify() gateway tests (KAI-1004).

Self-contained (no pytest dependency): run with `python3 test_notify_gateway.py`.
Env is set BEFORE importing the gateway so the log/dedup paths point at temp files
and test-mode is explicitly disabled (KAI_NOTIFY_TEST_SINK=0) — letting us exercise
routing with a stubbed transport instead of the real Telegram send.

Covers the P1 acceptance behaviors:
  1. reality gate      — synthetic provenance → sink, never sent
  2. dashboard routing — a technical alert (Rule B) → dashboard_only, transport untouched
  3. approval routing  — audience=approval → delivered to Telegram
  4. dedup             — a repeated standing condition notifies once
  5. classify refine   — a low-risk Leo-owned action → dashboard (autonomous, not Leo)
  6. pytest auto-sink  — with env unset under pytest, sends auto-suppress (P0 behavior)
"""
import json
import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="notify_test_")
os.environ["KAI_NOTIFY_LOG"] = os.path.join(_TMP, "notify_log.jsonl")
os.environ["KAI_NOTIFY_DEDUP"] = os.path.join(_TMP, "notify_dedup.json")
os.environ["KAI_NOTIFY_TEST_SINK"] = "0"  # exercise routing; transport is stubbed
os.environ["KAI_NOTIFY_DEDUP_WINDOW"] = "3600"

import notify_gateway as ng  # noqa: E402

# ── transport stub ──────────────────────────────────────────────────────────────
_SENT: list = []


class _FakeResp:
    status_code = 200

    def json(self):
        return {"ok": True, "result": {"message_id": 4242}}


def _install_stub():
    _SENT.clear()

    def _fake_raw_post(chat_id, text, reply_markup, parse_mode, disable_notification=None):
        _SENT.append({"chat_id": chat_id, "text": text})
        return _FakeResp()

    ng._raw_post = _fake_raw_post
    # a token must appear present so send_message() proceeds to _raw_post
    ng._secret = lambda name: "test-token" if name == "telegram_bot_token" else "111"


_RESULTS: list = []


def check(name, cond):
    _RESULTS.append((name, bool(cond)))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")


def test_reality_gate_synthetic():
    _install_stub()
    res = ng.notify(ng.Event(source="t", kind="alert", provenance="synthetic",
                             audience="approval", title="synthetic fixture"))
    check("synthetic → suppressed_synthetic", res.decision == "suppressed_synthetic")
    check("synthetic → not delivered", res.delivered is False)
    check("synthetic → transport untouched", len(_SENT) == 0)


def test_dashboard_routing():
    _install_stub()
    delivered = ng.tg_alert("CRITICAL — invariant failure, container down")
    check("technical alert → not delivered to Leo", delivered is False)
    check("technical alert → transport untouched", len(_SENT) == 0)


def test_approval_routing():
    _install_stub()
    res = ng.notify(ng.Event(source="gate", kind="gate", audience="approval",
                             actionable=True, title="Plan approval"))
    check("approval → delivered", res.delivered is True)
    check("approval → destination telegram", res.destination == "telegram")
    check("approval → transport called", len(_SENT) == 1)


def test_dedup():
    _install_stub()
    ev1 = ng.Event(source="s", kind="alert", audience="approval",
                   title="standing condition", dedup_key="cond-x")
    ev2 = ng.Event(source="s", kind="alert", audience="approval",
                   title="standing condition", dedup_key="cond-x")
    r1 = ng.notify(ev1)
    r2 = ng.notify(ev2)
    check("dedup first → delivered", r1.delivered is True)
    check("dedup second → suppressed_dedup", r2.decision == "suppressed_dedup")
    check("dedup → only one transport call", len(_SENT) == 1)


def test_classify_autonomous_dashboard():
    _install_stub()
    ng._classify = lambda action: ng.__dict__  # placeholder, overwritten below

    class _D:
        def __init__(self, mode, reason):
            self.mode = mode
            self.reason = reason

    ng._classify = lambda action: _D("autonomous", "low-risk Leo-owned action")
    res = ng.notify(ng.Event(source="ops", kind="action", audience="approval",
                             action={"owner": "leo", "op": "restart"}))
    check("classify autonomous → dashboard_only", res.decision == "dashboard_only")
    check("classify autonomous → transport untouched", len(_SENT) == 0)

    ng._classify = lambda action: _D("approve", "high-risk threshold")
    res2 = ng.notify(ng.Event(source="ops", kind="action", audience="dashboard",
                              action={"owner": "leo", "op": "deploy prod"}))
    check("classify approve → delivered to Leo", res2.delivered is True)


def test_pytest_auto_sink():
    # Simulate an in-process contract test: no explicit env, pytest imported.
    import sys
    saved = os.environ.pop("KAI_NOTIFY_TEST_SINK", None)
    sys.modules.setdefault("pytest", sys)  # make "pytest" appear imported
    try:
        _install_stub()
        ok = ng.send_telegram(123, "should not send", reason="reply")
        check("pytest auto-sink → send suppressed", ok is False)
        check("pytest auto-sink → transport untouched", len(_SENT) == 0)
    finally:
        if saved is not None:
            os.environ["KAI_NOTIFY_TEST_SINK"] = saved
        if sys.modules.get("pytest") is sys:
            del sys.modules["pytest"]


def test_uncaused_problem_stamped():
    """A problem-asserting event with NO cause cannot reach Leo as a bare alarm —
    the gateway stamps not-yet-diagnosed and the sent text says so visibly."""
    _install_stub()
    ev = ng.Event(source="invariants", kind="invariant", audience="approval",
                  status="fail", title="scheduler heartbeat missed")
    res = ng.notify(ev)
    check("uncaused problem → stamped not-yet-diagnosed", ev.cause == ng.NOT_YET_DIAGNOSED)
    check("uncaused problem → still delivered (honest, not dropped)", res.delivered is True)
    check("uncaused problem → text carries cause: not-yet-diagnosed",
          _SENT and "cause: not-yet-diagnosed" in _SENT[-1]["text"])


def test_caused_problem_passthrough():
    """A problem WITH a verified cause passes through unchanged and reports it."""
    _install_stub()
    ev = ng.Event(source="fleet", kind="alert", audience="approval",
                  status="degraded", cause="worker disk 96% — /var/lib/docker",
                  title="worker degraded")
    ng.notify(ev)
    check("caused problem → cause preserved", ev.cause == "worker disk 96% — /var/lib/docker")
    check("caused problem → text carries verified cause",
          _SENT and "cause: verified — worker disk 96% — /var/lib/docker" in _SENT[-1]["text"])
    check("caused problem → not stamped over", ev.cause != ng.NOT_YET_DIAGNOSED)


def test_good_status_no_cause_line():
    """A good/absent status asserts nothing wrong — no cause is required or appended."""
    _install_stub()
    ev = ng.Event(source="ops", kind="gate", audience="approval",
                  status="ok", title="all green")
    ng.notify(ev)
    check("good status → no cause stamped", ev.cause is None)
    check("good status → no cause line in text", _SENT and "cause:" not in _SENT[-1]["text"])


def _last_log_record():
    with open(os.environ["KAI_NOTIFY_LOG"]) as f:
        lines = [l for l in f if l.strip()]
    return json.loads(lines[-1]) if lines else {}


def test_tg_alert_status_stamped():
    """A DevOps pager call (tg_alert with a problem status, no cause) is routed
    through the contract: the logged finding carries status + not-yet-diagnosed,
    proving watchdog pages can no longer emit a bare, uncaused alarm."""
    _install_stub()
    ng.tg_alert("[DevOps] plane-api crash loop (+6 restarts)", status="alert")
    rec = _last_log_record()
    check("tg_alert status → logged status=alert", rec.get("status") == "alert")
    check("tg_alert uncaused → logged cause=not-yet-diagnosed",
          rec.get("cause") == ng.NOT_YET_DIAGNOSED)


def test_tg_alert_status_with_cause():
    """A pager call WITH a verified cause preserves it — not overwritten."""
    _install_stub()
    ng.tg_alert("[DevOps] worker degraded", status="degraded",
                cause="disk 96% on /var/lib/docker")
    rec = _last_log_record()
    check("tg_alert caused → cause preserved",
          rec.get("cause") == "disk 96% on /var/lib/docker")


def main():
    print("notify() gateway tests:")
    for fn in (test_reality_gate_synthetic, test_dashboard_routing,
               test_approval_routing, test_dedup,
               test_classify_autonomous_dashboard, test_pytest_auto_sink,
               test_uncaused_problem_stamped, test_caused_problem_passthrough,
               test_good_status_no_cause_line,
               test_tg_alert_status_stamped, test_tg_alert_status_with_cause):
        fn()
    failed = [n for n, ok in _RESULTS if not ok]
    print(f"\n{len(_RESULTS) - len(failed)}/{len(_RESULTS)} checks passed.")
    return 1 if failed else 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
