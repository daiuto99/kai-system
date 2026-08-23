"""443fb11e (L18) — WP credentials must never be persisted or served in cleartext.

Root cause: wordpress.load_config returned the site's WP app-password inside its
CapabilityResult.data; the engine persisted that dict verbatim into steps.result
and re-served it via GET /jobs/{id}, leaking a live credential into the caller
(and into a Claude session .jsonl). These tests lock every part of the fix:

  1. redact() / redact_json_str() / redact_row() scrub secrets (unit).
  2. load_config no longer emits creds/app_password in its step result (source).
  3. resolve_creds() gives the workflow creds in-memory (the replacement path).
  4. The engine PERSIST chokepoint redacts any step result that still carries a
     secret (defense-in-depth, DB round-trip).
  5. _ctx() re-injects creds in-memory so downstream steps keep working while
     the DB never sees the app-password (DB round-trip).
"""
import json
import tempfile
from pathlib import Path
from unittest import mock

import db
from db import new_id, now_iso
from models import CapabilityResult, StepDef
from workflow_base import Workflow
from redact import redact, redact_json_str, redact_row

_FAKE_CREDS = {
    "fqdn": "the71company.com",
    "app_password": "SUPERSECRET-app-pw-value",
    "cloudways_sys_user": "kai",
    "url": "https://the71company.com",
    "username": "kai",
}


# ── 1. redact primitives ──────────────────────────────────────────────────
def test_redact_scrubs_top_level_app_password():
    out = redact({"app_password": "hunter2", "title": "Home"})
    assert out["app_password"] == "[REDACTED]"
    assert out["title"] == "Home"


def test_redact_scrubs_whole_creds_dict():
    out = redact({"site": "x", "creds": _FAKE_CREDS})
    assert out["creds"] == "[REDACTED]"
    assert out["site"] == "x"


def test_redact_scrubs_nested_secret():
    out = redact({"outer": {"inner": {"app_password": "s"}}, "ok": [1, 2]})
    assert out["outer"]["inner"]["app_password"] == "[REDACTED]"
    assert out["ok"] == [1, 2]


def test_redact_suffix_rule():
    out = redact({"wp_app_password": "s", "cloudways_api_secret": "s",
                  "session_token": "s", "page_id": 42, "sort_key": "abc"})
    assert out["wp_app_password"] == "[REDACTED]"
    assert out["cloudways_api_secret"] == "[REDACTED]"
    assert out["session_token"] == "[REDACTED]"
    # benign identifiers must survive — the fix must not over-redact
    assert out["page_id"] == 42
    assert out["sort_key"] == "abc"


def test_redact_is_non_mutating():
    original = {"creds": {"app_password": "s"}}
    _ = redact(original)
    assert original["creds"]["app_password"] == "s", "redact() must not mutate input"


def test_redact_json_str_scrubs_legacy_row():
    raw = json.dumps({"site": "x", "creds": _FAKE_CREDS})
    scrubbed = json.loads(redact_json_str(raw))
    assert scrubbed["creds"] == "[REDACTED]"
    assert "SUPERSECRET" not in redact_json_str(raw)


def test_redact_json_str_passthrough_non_json():
    assert redact_json_str("not json") == "not json"
    assert redact_json_str(None) is None
    assert redact_json_str("") == ""


def test_redact_row_scrubs_json_columns_and_top_level():
    row = {"id": "j1", "type": "wordpress.build_page_draft",
           "result": json.dumps({"creds": _FAKE_CREDS}),
           "inputs": json.dumps({"site": "the71company", "password": "p"}),
           "app_password": "top-level-leak"}
    out = redact_row(row)
    assert out["app_password"] == "[REDACTED]"
    assert json.loads(out["result"])["creds"] == "[REDACTED]"
    assert json.loads(out["inputs"])["password"] == "[REDACTED]"
    assert json.loads(out["inputs"])["site"] == "the71company"
    assert "SUPERSECRET" not in json.dumps(out)


# ── 2. load_config no longer emits creds (the leak source) ────────────────
def test_load_config_result_carries_no_creds():
    from capabilities import wordpress as wp
    with mock.patch.object(wp, "_load_creds", return_value=dict(_FAKE_CREDS)):
        res = wp.load_config(site="the71company")
    assert res.ok
    assert res.data.get("site") == "the71company"
    assert res.data.get("fqdn") == "the71company.com"
    assert "creds" not in res.data, "load_config must not return creds"
    assert "app_password" not in json.dumps(res.data), "app_password leaked in result"


# ── 3. resolve_creds is the in-memory replacement path ────────────────────
def test_resolve_creds_returns_full_creds_in_memory():
    from capabilities import wordpress as wp
    with mock.patch.object(wp, "_load_creds", return_value=dict(_FAKE_CREDS)):
        creds = wp.resolve_creds("the71company")
    assert creds["app_password"] == "SUPERSECRET-app-pw-value"
    assert creds["fqdn"] == "the71company.com"


# ── 4. engine PERSIST chokepoint (DB round-trip) ──────────────────────────
class _LeakyWorkflow(Workflow):
    """A step that (wrongly) returns creds in its data — the persist chokepoint
    must scrub it before it lands in steps.result."""
    name = "test.leaky"
    steps = [StepDef("load_site_config", "wordpress.load_config", max_retries=0)]

    def execute_step(self, step_def, step):
        return CapabilityResult(
            ok=True, status="succeeded",
            data={"site": "the71company", "fqdn": "the71company.com",
                  "creds": dict(_FAKE_CREDS)},
            verification={"verified": True, "evidence": {"auto": True}},
        )


def test_persist_chokepoint_redacts_step_result():
    with tempfile.TemporaryDirectory() as tmpdir:
        with mock.patch.object(db, "DB_PATH", Path(tmpdir) / "orchestrator.db"):
            db.init_db()
            wf = _LeakyWorkflow.start({"site": "the71company"})
            wf.resume()
            conn = db.get_conn()
            try:
                row = conn.execute(
                    "SELECT result FROM steps WHERE job_id=?", (wf.job_id,)
                ).fetchone()
            finally:
                conn.close()
    stored = row["result"]
    assert "SUPERSECRET" not in stored, "app_password persisted to jobs DB"
    parsed = json.loads(stored)
    assert parsed["creds"] == "[REDACTED]"
    assert parsed["site"] == "the71company", "non-secret fields must survive redaction"


# ── 5. _ctx() re-injects creds in-memory (DB round-trip) ──────────────────
def test_ctx_injects_creds_in_memory_not_from_db():
    from workflows.wordpress_publish_homepage import PublishHomepageWorkflow
    from capabilities import wordpress as wp
    with tempfile.TemporaryDirectory() as tmpdir:
        with mock.patch.object(db, "DB_PATH", Path(tmpdir) / "orchestrator.db"):
            db.init_db()
            wf = PublishHomepageWorkflow.start({"site": "the71company"})
            with mock.patch.object(wp, "_load_creds", return_value=dict(_FAKE_CREDS)):
                ctx = wf._ctx()
    # creds arrive in ctx (downstream steps keep working)…
    assert ctx["creds"]["app_password"] == "SUPERSECRET-app-pw-value"
    # …and site came from job inputs, proving no persisted-creds dependency.
    assert ctx["site"] == "the71company"


# ── 6. Codex round-1 hardening (error/input persist, stale-creds overwrite, camelCase) ──
def test_engine_redacts_error_on_persist():
    """A capability error shaped as a JSON dict with a secret must be scrubbed
    before it lands in steps.error (443fb11e serve path can't fix plaintext)."""
    class _ErrCreds(Workflow):
        name = "test.errcreds"
        steps = [StepDef("s", "x", max_retries=0)]

        def execute_step(self, step_def, step):
            return CapabilityResult(
                ok=False, status="failed_permanent",
                error={"type": "boom", "creds": dict(_FAKE_CREDS)})

    with tempfile.TemporaryDirectory() as tmpdir:
        with mock.patch.object(db, "DB_PATH", Path(tmpdir) / "orchestrator.db"):
            db.init_db()
            wf = _ErrCreds.start({"site": "the71company"})
            wf.resume()
            conn = db.get_conn()
            try:
                row = conn.execute("SELECT error FROM steps WHERE job_id=?",
                                   (wf.job_id,)).fetchone()
            finally:
                conn.close()
    assert "SUPERSECRET" not in (row["error"] or ""), "secret persisted to steps.error"


def test_engine_redacts_input_on_persist():
    """A secret passed as a workflow input must never land in jobs.inputs."""
    with tempfile.TemporaryDirectory() as tmpdir:
        with mock.patch.object(db, "DB_PATH", Path(tmpdir) / "orchestrator.db"):
            db.init_db()
            wf = _LeakyWorkflow.start({"site": "the71company",
                                       "app_password": "INPUT-LEAK-pw"})
            conn = db.get_conn()
            try:
                row = conn.execute("SELECT inputs FROM jobs WHERE id=?",
                                   (wf.job_id,)).fetchone()
            finally:
                conn.close()
    stored = json.loads(row["inputs"])
    assert stored["app_password"] == "[REDACTED]"
    assert stored["site"] == "the71company"


def test_ctx_overwrites_stale_redacted_creds():
    """A legacy persisted step whose creds is the "[REDACTED]" string (or any
    stale/attacker value) must be OVERWRITTEN with fresh creds — never sent
    downstream as a bad value."""
    from workflows.wordpress_publish_homepage import PublishHomepageWorkflow
    from capabilities import wordpress as wp
    with tempfile.TemporaryDirectory() as tmpdir:
        with mock.patch.object(db, "DB_PATH", Path(tmpdir) / "orchestrator.db"):
            db.init_db()
            wf = PublishHomepageWorkflow.start({"site": "the71company"})
            # Simulate a legacy succeeded step whose persisted result carries a
            # redacted creds value (what a re-run under the new persist path
            # would store).
            conn = db.get_conn()
            conn.execute(
                "INSERT INTO steps (id,job_id,name,status,result,created_at) "
                "VALUES (?,?,?,?,?,?)",
                (new_id(), wf.job_id, "load_site_config", "succeeded",
                 json.dumps({"site": "the71company", "fqdn": "the71company.com",
                             "creds": "[REDACTED]"}), now_iso()))
            conn.commit()
            conn.close()
            with mock.patch.object(wp, "_load_creds", return_value=dict(_FAKE_CREDS)):
                ctx = wf._ctx()
    assert ctx["creds"]["app_password"] == "SUPERSECRET-app-pw-value", \
        "stale redacted creds must be overwritten by a fresh in-memory resolve"


def test_ctx_overwrites_caller_supplied_creds_input():
    """A creds field smuggled in via job inputs must not survive to downstream."""
    from workflows.wordpress_publish_homepage import PublishHomepageWorkflow
    from capabilities import wordpress as wp
    with tempfile.TemporaryDirectory() as tmpdir:
        with mock.patch.object(db, "DB_PATH", Path(tmpdir) / "orchestrator.db"):
            db.init_db()
            # inputs.creds is scrubbed at persist, but prove _ctx overwrites
            # regardless of what survives the merge.
            wf = PublishHomepageWorkflow.start(
                {"site": "the71company", "creds": {"app_password": "ATTACKER"}})
            with mock.patch.object(wp, "_load_creds", return_value=dict(_FAKE_CREDS)):
                ctx = wf._ctx()
    assert ctx["creds"] == _FAKE_CREDS or ctx["creds"]["app_password"] == "SUPERSECRET-app-pw-value"
    assert ctx["creds"] != {"app_password": "ATTACKER"}


def test_redact_camelcase_secret_keys():
    from redact import redact
    out = redact({"appPassword": "s", "accessToken": "s", "apiKey": "s",
                  "userId": 7})
    assert out["appPassword"] == "[REDACTED]"
    assert out["accessToken"] == "[REDACTED]"
    assert out["apiKey"] == "[REDACTED]"
    assert out["userId"] == 7
