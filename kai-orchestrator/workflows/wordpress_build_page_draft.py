"""wordpress_build_page_draft workflow — drafts-only governed page build.

The publish counterpart (wordpress.publish_homepage) runs all the way through
set-as-homepage + publish + verify-live: it DEPLOYS. This workflow is the
develop-the-process / drafts-only sibling — it exercises the same governed
chain (dev gate + creative gate + the WP write chokepoint + brand-drift check)
but STOPS at the draft. It never sets a front page, never publishes, never
touches the live homepage, so there is no homepage-overwrite gate to run.

KAI-20 / WP-20.6d — the frame (Leo, 2026-07-28): the WordPress work is to
solidify the PROCEDURE and exercise the creative + technical teams to produce
DRAFTS, not to deploy final sites. The only pre-existing WP workflow published;
this is the missing drafts-only path the dashboard EDIT/BUILD modes launch over.

Steps:
  1. load_site_config   — fetch creds from vault           (wordpress.load_config)
  2. check_credentials  — probe WP app password            (wordpress.probe_credentials)
  3. load_brief         — read brief from vault (optional)  (vault.read)
  4. dev_gate           — council.gate (dev) engineering sign-off
  5. creative_brief     — council.gate (creative_review) — CD/Ember draft copy
  6. create_page_draft  — create WP page as status=draft, brand-drift checked,
                          uuid marker injected, verify_page_exists (wordpress.create_page)
  7. complete           — finalize

Every capability/gate step and helper is inherited unchanged from
PublishHomepageWorkflow; only the step list is trimmed. Drafts-only is enforced
structurally — the publish/homepage steps simply do not exist in this workflow.
"""
import logging

from workflows.wordpress_publish_homepage import PublishHomepageWorkflow
from models import StepDef

log = logging.getLogger(__name__)


class BuildPageDraftWorkflow(PublishHomepageWorkflow):
    name = "wordpress.build_page_draft"
    approval_policy = "council_gate"
    write_mode = "draft_only"  # KAI-1083 — never publishes; dev gate uses drafts-only rubric
    steps = [
        StepDef("load_site_config",  capability="wordpress.load_config"),
        StepDef("check_credentials", capability="wordpress.probe_credentials"),
        StepDef("load_brief",        capability="vault.read"),
        StepDef("dev_gate",          step_type="approval_gate"),
        StepDef("creative_brief",    step_type="approval_gate"),
        StepDef("generate_page",     capability="wordpress.generate_blocks"),  # AR-3/KAI-965
        StepDef("create_page_draft", capability="wordpress.create_page", max_retries=2),
        StepDef("complete",          finalize=True),
    ]
