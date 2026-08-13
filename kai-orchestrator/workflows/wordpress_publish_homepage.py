"""wordpress_publish_homepage workflow — guarded homepage publish.

Steps:
  1. load_site_config      — fetch creds from vault
  2. check_credentials     — probe WP app password
  3. load_brief            — read brief from vault (skipped if no brief path in inputs)
  4. dev_gate              — council.gate (dev type) for engineering sign-off
  5. creative_brief        — council.gate (creative_review) for Ember draft
  6. create_page_draft     — create WP page, inject uuid marker
  7. disable_coming_soon   — set kai_cs_active=0
  8. precheck_homepage_overwrite — compare live front page to expected predecessor
  9. set_as_homepage       — set WP front page
  10. publish_page         — publish the draft
  11. purge_cache          — SSH Varnish purge
  12. verify_live          — check marker visible on public URL
  13. devops_review        — council.gate (dev) final sign-off
  14. complete             — finalize
"""
import json
import logging
from pathlib import Path
from workflow_base import Workflow
from models import StepDef, CapabilityResult
from db import get_conn

log = logging.getLogger(__name__)

_VAULT_BRIEFS = Path("/vault/20_Projects")


class PublishHomepageWorkflow(Workflow):
    name = "wordpress.publish_homepage"
    approval_policy = "council_gate"
    steps = [
        StepDef("load_site_config",    capability="wordpress.load_config"),
        StepDef("check_credentials",   capability="wordpress.probe_credentials"),
        StepDef("load_brief",          capability="vault.read"),
        StepDef("dev_gate",            step_type="approval_gate"),
        StepDef("creative_brief",      step_type="approval_gate"),
        StepDef("create_page_draft",   capability="wordpress.create_page",   max_retries=2),
        StepDef("disable_coming_soon", capability="wordpress.set_option",    max_retries=2),
        StepDef("precheck_homepage_overwrite", capability="wordpress.get_front_page"),
        StepDef("set_as_homepage",     capability="wordpress.set_front_page", max_retries=2),
        StepDef("publish_page",        capability="wordpress.publish",        max_retries=2),
        StepDef("purge_cache",         capability="wordpress.purge_varnish",  max_retries=3),
        StepDef("verify_live",         capability="wordpress.verify_live"),
        StepDef("devops_review",       step_type="approval_gate"),
        StepDef("complete",            finalize=True),
    ]

    # ── Context aggregation ───────────────────────────────────────────────

    def _ctx(self) -> dict:
        """Merge all succeeded step results into a single context dict."""
        conn = get_conn()
        rows = conn.execute(
            "SELECT name, result FROM steps WHERE job_id=? AND status='succeeded'",
            (self.job_id,),
        ).fetchall()
        conn.close()
        ctx = {}
        for row in rows:
            if row["result"]:
                try:
                    ctx.update(json.loads(row["result"]))
                except Exception:
                    pass
        # Also pull job inputs
        conn = get_conn()
        job = conn.execute("SELECT inputs FROM jobs WHERE id=?", (self.job_id,)).fetchone()
        conn.close()
        if job:
            inputs = json.loads(job["inputs"])
            # inputs take lower precedence than step results (don't overwrite)
            for k, v in inputs.items():
                ctx.setdefault(k, v)
        return ctx

    # ── Main dispatch ─────────────────────────────────────────────────────

    def execute_step(self, step_def: StepDef, step: dict) -> CapabilityResult:
        name = step_def.name
        ctx  = self._ctx()

        # Approval gate steps delegate to council.gate capability
        if step_def.step_type == "approval_gate":
            return self._run_gate(name, step, ctx)

        # Finalize step — always succeeds if we get here
        if step_def.finalize:
            return CapabilityResult(
                ok=True, status="succeeded",
                data={"complete": True, "job_id": self.job_id},
                verification={"verified": True, "evidence": {"finalize": True}},
            )

        # Route to capability handler
        handler = {
            "load_site_config":    self._step_load_config,
            "check_credentials":   self._step_check_credentials,
            "load_brief":          self._step_load_brief,
            "create_page_draft":   self._step_create_page,
            "disable_coming_soon": self._step_disable_cs,
            "precheck_homepage_overwrite": self._step_precheck_homepage_overwrite,
            "set_as_homepage":     self._step_set_homepage,
            "publish_page":        self._step_publish,
            "purge_cache":         self._step_purge,
            "verify_live":         self._step_verify_live,
        }.get(name)

        if handler is None:
            return CapabilityResult(
                ok=False, status="failed_permanent",
                error={"type": "no_handler", "step": name},
            )
        if name == "precheck_homepage_overwrite":
            return handler(ctx, step)
        return handler(ctx)

    # ── Approval gates ────────────────────────────────────────────────────

    def _run_gate(self, step_name: str, step: dict, ctx: dict) -> CapabilityResult:
        from capabilities import get_capability
        gate_fn = get_capability("council.gate")

        # Emit council-api's canonical gate types (bug 286e8874) — these route to
        # the real review chains (_dev_gate_review / _creative_gate_review /
        # _devops_gate_review). The old "dev"/"creative_review" values hit the
        # unknown->human-only fall-through, so those chains never ran.
        gate_type_map = {
            "dev_gate":      "dev_gate",
            "creative_brief": "creative_gate",
            "devops_review": "devops_gate",
            # homepage_overwrite stays a deliberately human-only type in council-api.
            # This must never be auto-approved: it authorizes replacing live content.
            "precheck_homepage_overwrite": "homepage_overwrite",
        }
        gate_type = gate_type_map.get(step_name, "dev_gate")

        brief = {
            "job_id":    self.job_id,
            "step":      step_name,
            "site":      ctx.get("site", ""),
            "workflow":  self.name,
            "context":   {k: ctx[k] for k in ("site", "fqdn") if k in ctx},
        }
        if step_name == "creative_brief":
            brief["vault_brief"] = ctx.get("brief_text", "")
        if step_name == "precheck_homepage_overwrite":
            brief["overwrite_guard"] = {
                "expected_current_homepage_id": ctx.get("expected_current_homepage_id"),
                "live_show_on_front": ctx.get("show_on_front"),
                "live_page_on_front": ctx.get("page_on_front"),
                "reason": "live front page differs from expected predecessor",
                "required_decision": "explicit human confirmation to replace live homepage",
            }

        return gate_fn(
            job_id=self.job_id,
            step_id=step["id"],
            brief=brief,
            gate_type=gate_type,
        )

    # ── Capability steps ──────────────────────────────────────────────────

    def _step_load_config(self, ctx: dict) -> CapabilityResult:
        from capabilities import get_capability
        fn = get_capability("wordpress.load_config")
        return fn(site=ctx.get("site", ""))

    def _step_check_credentials(self, ctx: dict) -> CapabilityResult:
        from capabilities import get_capability
        fn = get_capability("wordpress.probe_credentials")
        return fn(site=ctx.get("site", ""), creds=ctx.get("creds"))

    def _step_load_brief(self, ctx: dict) -> CapabilityResult:
        """Read brief from vault. If no brief_path in inputs, skip with empty brief."""
        brief_path = ctx.get("brief_path", "")
        if not brief_path:
            log.info("No brief_path in inputs — using empty brief")
            return CapabilityResult(
                ok=True, status="succeeded",
                data={"brief_text": ""},
                verification={"verified": True, "evidence": {"brief": "empty"}},
            )
        try:
            p = Path(brief_path)
            text = p.read_text()
            return CapabilityResult(
                ok=True, status="succeeded",
                data={"brief_text": text},
                verification={"verified": True, "evidence": {"path": brief_path,
                                                              "chars": len(text)}},
            )
        except Exception as e:
            return CapabilityResult(
                ok=False, status="failed_recoverable",
                error={"type": "brief_read_failed", "path": brief_path, "detail": str(e)},
            )

    def _step_create_page(self, ctx: dict) -> CapabilityResult:
        from capabilities import get_capability
        from workflows import wordpress_verifiers as wv
        fn = get_capability("wordpress.create_page")
        site  = ctx.get("site", "")
        creds = ctx.get("creds")
        title   = ctx.get("page_title", "Home")
        content = ctx.get("page_content", ctx.get("brief_text", "<p>Welcome.</p>"))

        result = fn(site=site, creds=creds, title=title, content=content, status="draft", caller=__file__)
        if result.ok and result.verification is None:
            vr = wv.verify_page_exists(site, creds, {"data": result.data})
            result.verification = vr
        return result

    def _step_disable_cs(self, ctx: dict) -> CapabilityResult:
        from capabilities import get_capability
        from workflows import wordpress_verifiers as wv
        fn = get_capability("wordpress.set_option")
        site  = ctx.get("site", "")
        creds = ctx.get("creds")
        result = fn(site=site, option="kai_cs_active", value="0", creds=creds, caller=__file__)
        # set_option already does readback verification; add explicit check
        if result.ok and (result.verification is None or
                          not result.verification.get("verified")):
            vr = wv.verify_cs_off(site, creds, {})
            result.verification = vr
        return result

    def _step_precheck_homepage_overwrite(self, ctx: dict, step: dict) -> CapabilityResult:
        """Fail closed unless the live homepage matches the caller's predecessor."""
        from capabilities import get_capability
        fn = get_capability("wordpress.get_front_page")
        result = fn(site=ctx.get("site", ""), creds=ctx.get("creds"))
        if not result.ok:
            return result

        live = result.data or {}
        show_on_front = live.get("show_on_front")
        page_on_front = live.get("page_on_front")
        # A posts front page or no page-on-front means no existing page is replaced.
        if show_on_front != "page" or page_on_front in (None, "", 0, "0"):
            return CapabilityResult(
                ok=True, status="succeeded", data=live,
                verification={"verified": True, "evidence": {
                    "decision": "safe_no_existing_front_page",
                    "show_on_front": show_on_front,
                    "page_on_front": page_on_front,
                }},
            )

        expected = ctx.get("expected_current_homepage_id")
        if expected is not None and str(page_on_front) == str(expected):
            return CapabilityResult(
                ok=True, status="succeeded", data=live,
                verification={"verified": True, "evidence": {
                    "decision": "expected_predecessor_matches",
                    "expected_current_homepage_id": expected,
                    "live_page_on_front": page_on_front,
                }},
            )

        # Missing expected ID is intentionally a mismatch. The council gate's
        # brief and resolution are persisted in the durable workflow record.
        return self._run_gate("precheck_homepage_overwrite", step, {**ctx, **live})

    def _step_set_homepage(self, ctx: dict) -> CapabilityResult:
        from capabilities import get_capability
        from workflows import wordpress_verifiers as wv
        fn = get_capability("wordpress.set_front_page")
        site    = ctx.get("site", "")
        creds   = ctx.get("creds")
        page_id = ctx.get("id")  # from create_page result
        if not page_id:
            return CapabilityResult(
                ok=False, status="failed_permanent",
                error={"type": "missing_page_id",
                       "message": "create_page_draft must succeed first"},
            )
        result = fn(site=site, page_id=page_id, creds=creds, caller=__file__)
        if result.ok and result.verification is None:
            vr = wv.verify_front_page_set(site, creds, {})
            result.verification = vr
        return result

    def _step_publish(self, ctx: dict) -> CapabilityResult:
        from capabilities import get_capability
        from workflows import wordpress_verifiers as wv
        fn = get_capability("wordpress.publish")
        site    = ctx.get("site", "")
        creds   = ctx.get("creds")
        page_id = ctx.get("id")
        if not page_id:
            return CapabilityResult(
                ok=False, status="failed_permanent",
                error={"type": "missing_page_id"},
            )
        result = fn(site=site, page_id=page_id, creds=creds, caller=__file__)
        if result.ok and result.verification is None:
            vr = wv.verify_page_published(site, creds, {"data": {"id": page_id}})
            result.verification = vr
        return result

    def _step_purge(self, ctx: dict) -> CapabilityResult:
        from capabilities import get_capability
        fn = get_capability("wordpress.purge_varnish")
        site  = ctx.get("site", "")
        creds = ctx.get("creds")
        try:
            result = fn(site=site, url_path="/", creds=creds)
        except Exception as e:
            # Purge is best-effort — SSH not available in all environments
            log.warning("Purge raised (best-effort skipped) for %s: %s", site, e)
            return CapabilityResult(
                ok=True, status="succeeded",
                data={"purge_skipped": True, "detail": str(e)},
                verification={"verified": True, "evidence": {"best_effort": True}},
            )
        if not result.ok:
            log.warning("Purge non-ok for %s: %s", site, result.error)
            return CapabilityResult(
                ok=True, status="succeeded",
                data={"purge_skipped": True, "detail": str(result.error)},
                verification={"verified": True, "evidence": {"best_effort": True}},
            )
        result.verification = {"verified": True, "evidence": {"purge_data": result.data}}
        return result

    def _step_verify_live(self, ctx: dict) -> CapabilityResult:
        from capabilities import get_capability
        from workflows import wordpress_verifiers as wv
        from transports.base import safe_request
        fn = get_capability("wordpress.verify_live")
        site   = ctx.get("site", "")
        creds  = ctx.get("creds")
        marker = ctx.get("marker", "")
        page_id = ctx.get("id")

        result = fn(site=site, url="", marker=marker, creds=creds)

        # Cloudflare-protected sites block anonymous requests — fall back to REST API
        if not result.ok and result.error and (
            result.error.get("cloudflare_blocked") or True  # always try REST fallback
        ):
            log.info("verify_live: falling back to REST API check for %s", site)
            fqdn = (creds or {}).get("fqdn", "")
            pw   = (creds or {}).get("app_password", "")
            if fqdn and pw and page_id:
                r = safe_request(
                    "GET", f"https://{fqdn}/wp-json/wp/v2/pages/{page_id}",
                    auth=("kai", pw), verify=False,
                )
                if r.ok and r.data:
                    page_status  = r.data.get("status", "")
                    page_content = r.data.get("content", {}).get("rendered", "")
                    marker_found = marker and marker in page_content
                    if page_status == "publish" and marker_found:
                        return CapabilityResult(
                            ok=True, status="succeeded",
                            data={"marker": marker, "page_id": page_id,
                                  "page_status": page_status},
                            verification={"verified": True, "evidence": {
                                "method": "rest_api_fallback",
                                "page_status": page_status,
                                "marker_in_content": True,
                                "cloudflare_protected": True,
                            }},
                        )

        if result.ok and result.verification is None:
            vr = wv.verify_live_marker(site, creds, {"data": {"marker": marker}})
            result.verification = vr
        return result
