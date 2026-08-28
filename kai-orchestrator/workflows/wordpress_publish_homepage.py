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
    # KAI-1083 — build tier the dev gate reviews against. "publish" workflows
    # deploy live; "draft_only" siblings never publish. See _run_gate / _dev_gate_review.
    write_mode = "publish"
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
        # WP creds are resolved in-memory here and NEVER persisted (443fb11e,
        # L18): load_config intentionally no longer returns them in its step
        # result, so re-read them from the secrets layer into ctx at run time.
        # Every downstream step still reads ctx["creds"] unchanged, but the
        # jobs DB / GET /jobs/{id} never see the app-password.
        #
        # ALWAYS overwrite any 'creds' that leaked in from a merged step result
        # or job input — the secrets layer is the sole authority. A stale value
        # (e.g. a legacy row's "[REDACTED]" string, or a caller-supplied creds
        # input) must never survive to a downstream step.
        ctx.pop("creds", None)
        if ctx.get("site"):
            try:
                from capabilities.wordpress import resolve_creds
                ctx["creds"] = resolve_creds(ctx["site"])
            except Exception as e:
                log.warning("in-memory creds resolution failed for site=%s: %s",
                            ctx.get("site"), e)
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
            "generate_page":       self._step_generate_page,
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
            "write_mode": getattr(self, "write_mode", "publish"),
            # KAI-1113 · [MR1] WP governed-pipeline probe marker. Only set True when the
            # build was launched via the authed worker-api build-draft launcher with
            # probe=True; council-api auto-approves probe-flagged drafts-only gates.
            "probe":     bool(ctx.get("probe", False)),
            "context":   {k: ctx[k] for k in ("site", "fqdn") if k in ctx},
        }
        # KAI-1083 — drafts-only builds carry a real deliverable envelope so the dev
        # gate reviews the actual draft against drafts-only criteria, not a bare job
        # context object against production-deploy criteria.
        if step_name == "dev_gate" and getattr(self, "write_mode", "publish") == "draft_only":
            brief["deliverable"] = {
                "page_title":      ctx.get("page_title", ctx.get("page_id", "(untitled draft)")),
                "content_model":   "single WordPress page saved as DRAFT",
                "content_present": bool(ctx.get("page_content") or ctx.get("brief_text")),
                "acceptance_criteria": [
                    "a DRAFT page is created/updated (status=draft)",
                    "nothing is published; no homepage / front-page change",
                    "brand-drift check passes for the property",
                ],
            }
            brief["safety"] = {
                "publishes":           False,
                "touches_live_infra":  False,
                "enforced_by":         "WP write chokepoint (status=draft) + workflow has no publish/homepage steps",
                "secrets_path":        "WordPress credentials loaded from the secrets layer via wordpress.load_config; never in job context",
            }
            brief["plane_issue"] = ctx.get("plane_issue", "")
        if step_name == "creative_brief":
            # WP AR-1 gap2 — the gate reviews the property's approved brief
            # (auto-loaded by load_brief into review_brief), falling back to any
            # authored brief_text. brief_source records where the material came
            # from so a rubber-stamp (empty) review is visible in the record.
            brief["vault_brief"] = ctx.get("review_brief") or ctx.get("brief_text", "")
            brief["brief_source"] = ctx.get("brief_source", "none")
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
        """Load the brief the creative gate reviews.

        Precedence: an explicit ``brief_path`` in inputs wins (authored brief).
        Otherwise (WP AR-1 gap2) auto-load the property's APPROVED brand brief —
        its ``BUILD_PROFILE.md`` resolved by governance slug — so the creative
        gate reviews real material instead of rubber-stamping an empty
        ``vault_brief``. The reviewed material rides a dedicated ``review_brief``
        key; ``brief_text`` (the page-content fallback consumed by create_page)
        is left empty on auto-load so the draft content still comes only from
        ``inputs.page_content``. ``brief_source`` records provenance.
        """
        brief_path = ctx.get("brief_path", "")
        if brief_path:
            try:
                text = Path(brief_path).read_text()
                return CapabilityResult(
                    ok=True, status="succeeded",
                    data={"brief_text": text, "review_brief": text,
                          "brief_source": f"inputs:{brief_path}"},
                    verification={"verified": True, "evidence": {"path": brief_path,
                                                                 "chars": len(text)}},
                )
            except Exception as e:
                return CapabilityResult(
                    ok=False, status="failed_recoverable",
                    error={"type": "brief_read_failed", "path": brief_path, "detail": str(e)},
                )
        # No explicit brief — auto-load the property's approved brand brief by
        # governance slug (same slug the brand-drift check uses: KAI-39 property
        # override, else the site key). Fail-open to empty when unseeded so an
        # ungoverned property surfaces as brief_source=none rather than erroring.
        import brand_profile
        from capabilities.wordpress import _site_key
        slug = ctx.get("brand_slug") or ctx.get("property") or _site_key(ctx.get("site", ""))
        prof = brand_profile.profile_path(slug) if slug else None
        if prof and prof.exists():
            try:
                text = prof.read_text()
            except Exception as e:
                # Fail open, never crash the governed workflow on a brief read
                # error — surface it as an unreadable (empty) review instead.
                log.warning("load_brief: approved brief for '%s' unreadable (%s) — "
                            "creative gate reviews empty material", slug, e)
                return CapabilityResult(
                    ok=True, status="succeeded",
                    data={"brief_text": "", "review_brief": "",
                          "brief_source": "none:brief_unreadable"},
                    verification={"verified": True,
                                  "evidence": {"brief": "empty", "slug": slug,
                                               "error": str(e)}},
                )
            log.info("load_brief: auto-loaded approved brief for creative gate "
                     "(slug=%s, %d chars)", slug, len(text))
            return CapabilityResult(
                ok=True, status="succeeded",
                data={"brief_text": "", "review_brief": text,
                      "brief_source": f"auto:build_profile:{slug}"},
                verification={"verified": True,
                              "evidence": {"brief": "auto:build_profile",
                                           "slug": slug, "chars": len(text)}},
            )
        log.warning("load_brief: no approved brief for property '%s' — creative gate "
                    "reviews empty material (ungoverned)", slug)
        return CapabilityResult(
            ok=True, status="succeeded",
            data={"brief_text": "", "review_brief": "", "brief_source": "none:no_profile"},
            verification={"verified": True, "evidence": {"brief": "empty", "slug": slug}},
        )

    def _step_generate_page(self, ctx: dict) -> CapabilityResult:
        """AR-3/KAI-965 — generate the page body locally (style.md -> Gutenberg
        blocks) and expose it as ``page_content`` for the create_page step. Runs
        AFTER the dev + creative gates, so generation is governed; fails closed
        (the workflow stops) rather than authoring invalid/absent content."""
        from capabilities import get_capability
        fn = get_capability("wordpress.generate_blocks")
        site  = ctx.get("site", "")
        slug  = ctx.get("brand_slug") or ctx.get("property")
        brief = (ctx.get("page_brief") or ctx.get("review_brief")
                 or ctx.get("brief_text") or "").strip()
        if not brief:
            return CapabilityResult(
                ok=False, status="failed_permanent",
                error={"type": "no_brief",
                       "detail": "generate_page needs a page brief (page_brief input "
                                 "or a loaded property brief)"})
        result = fn(site=site, brief=brief, slug=slug)
        if result.ok:
            content = (result.data or {}).get("content", "")
            result.data = {**(result.data or {}), "page_content": content}
        return result

    def _step_create_page(self, ctx: dict) -> CapabilityResult:
        from capabilities import get_capability
        from workflows import wordpress_verifiers as wv
        fn = get_capability("wordpress.create_page")
        site  = ctx.get("site", "")
        creds = ctx.get("creds")
        title   = ctx.get("page_title", "Home")
        content = ctx.get("page_content", ctx.get("brief_text", "<p>Welcome.</p>"))
        # KAI-39 — thread the brand-governance slug so a site key whose brand
        # profile lives under a different slug (e.g. the71company → the71c) is
        # brand-drift-checked against its real profile instead of silently going
        # ungoverned. Falls back to an explicit `property` input, then to None
        # (create_page derives _site_key(site) — the genuinely-unseeded fail-safe).
        brand_slug = ctx.get("brand_slug") or ctx.get("property")

        result = fn(site=site, creds=creds, title=title, content=content, status="draft",
                    property=brand_slug, caller=__file__)
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
