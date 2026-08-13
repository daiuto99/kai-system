"""wordpress_edit_page_draft workflow — drafts-only governed EDIT of an existing page.

Sibling of wordpress.build_page_draft. Runs the same governed chain (dev +
creative gates + WP write chokepoint + brand-drift) but targets an EXISTING
page via wordpress.update_page instead of creating one. Drafts-only is enforced
twice: the workflow never publishes / sets a homepage, AND update_page itself
refuses to mutate any page that is not already a draft (editing live content is
the guarded publish workflow's job, WP-20.4).

KAI-20 / WP-20.6c — the EDIT mode of the dashboard's build/edit/maintain trio.

Steps:
  1. load_site_config   — wordpress.load_config
  2. check_credentials  — wordpress.probe_credentials
  3. load_brief         — vault.read (optional)
  4. dev_gate           — council.gate (dev)
  5. creative_brief     — council.gate (creative_review)
  6. update_page_draft  — wordpress.update_page (status=draft, drafts-only guard)
  7. complete           — finalize

Inputs: site, page_id (required), page_content (required), page_title (optional),
brief_path (optional).
"""
import logging

from workflows.wordpress_publish_homepage import PublishHomepageWorkflow
from models import StepDef, CapabilityResult

log = logging.getLogger(__name__)


class EditPageDraftWorkflow(PublishHomepageWorkflow):
    name = "wordpress.edit_page_draft"
    approval_policy = "council_gate"
    write_mode = "draft_only"  # KAI-1083 — never publishes; dev gate uses drafts-only rubric
    steps = [
        StepDef("load_site_config",  capability="wordpress.load_config"),
        StepDef("check_credentials", capability="wordpress.probe_credentials"),
        StepDef("load_brief",        capability="vault.read"),
        StepDef("dev_gate",          step_type="approval_gate"),
        StepDef("creative_brief",    step_type="approval_gate"),
        StepDef("update_page_draft", capability="wordpress.update_page", max_retries=2),
        StepDef("complete",          finalize=True),
    ]

    def execute_step(self, step_def, step):
        # Only update_page_draft differs from the parent; everything else
        # (approval gates, config/creds/brief, finalize) reuses the parent.
        if step_def.name == "update_page_draft":
            return self._step_update_page(self._ctx())
        return super().execute_step(step_def, step)

    def _step_update_page(self, ctx) -> CapabilityResult:
        from capabilities import get_capability
        from workflows import wordpress_verifiers as wv
        fn = get_capability("wordpress.update_page")
        site    = ctx.get("site", "")
        creds   = ctx.get("creds")
        page_id = ctx.get("page_id")
        if not page_id:
            return CapabilityResult(
                ok=False, status="failed_permanent",
                error={"type": "missing_page_id",
                       "message": "edit_page_draft requires a page_id input"},
            )
        title   = ctx.get("page_title")
        content = ctx.get("page_content", ctx.get("brief_text", ""))
        result = fn(site=site, page_id=page_id, content=content, title=title,
                    status="draft", creds=creds, caller=__file__)
        if result.ok and result.verification is None:
            vr = wv.verify_page_exists(site, creds, {"data": result.data})
            result.verification = vr
        return result
