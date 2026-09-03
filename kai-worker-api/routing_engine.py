"""Routing engine for Sprint A — maps parsed intent → dispatch plan.

Pure function. No I/O. No advisor calls. No Slack/Telegram posts. The dispatch
plan it returns describes what should happen; Slice 3 (advisor dispatch) reads
the plan and performs the actions.

Input: intent dict from intent_parser.parse_intent()
Output: dispatch plan with handler, target, instructions, clarifications_needed,
        privacy_scope, ok_to_dispatch flag.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from privacy_tiers import PRIVACY_ADVISORS  # SSOT (P5 af5245bd)

# All registered WordPress blogs — sourced from vault/00_System/wordpress_sites.json
# at module load. Kept as a sorted tuple so unit tests are deterministic.
def _load_wp_sites(vault_path: Path = Path("/vault")) -> tuple[str, ...]:
    p = vault_path / "00_System" / "wordpress_sites.json"
    if not p.exists():
        return ()
    try:
        data = json.loads(p.read_text())
        return tuple(sorted(data.get("sites", {}).keys()))
    except Exception:
        return ()


WP_SITES_DEFAULT = _load_wp_sites()

# Recommended default blog when intent is write_blog_post and Leo did not name one.
# Per Sprint A scope addendum #2 — sette-uno is Leo's active blog destination.
DEFAULT_BLOG = "sette-uno"


def build_dispatch_plan(
    intent: dict,
    origin_channel: str = "telegram",
    wp_sites: tuple[str, ...] | None = None,
) -> dict:
    """Map a parsed intent to a dispatch plan.

    Args:
        intent: output of intent_parser.parse_intent().
        origin_channel: "telegram" | "web" — affects privacy + surface routing.
            The private-advisor privacy gate below blocks dispatch to PRIVACY_ADVISORS
            when origin_channel == "telegram" (not end-to-end encrypted). The default
            is FAIL-CLOSED ("telegram"): a caller that omits the origin gets the
            privacy-restricted posture, never a bypass. (Was "slack" pre-AR-5; Slack
            is retired — a dead-channel default silently defeated this gate, KAI [BUG][PRIV]
            0e6870b7. Every caller should pass the true origin explicitly.)
        wp_sites: override list of valid WordPress site keys (defaults to vault config).

    Returns a dict describing the dispatch plan. The plan is NOT executed here.
    """
    sites = wp_sites if wp_sites is not None else WP_SITES_DEFAULT
    action = intent.get("action", "capture")
    destination = intent.get("destination")
    instructions = intent.get("instructions") or ""
    confidence = intent.get("confidence", "low")

    plan: dict = {
        "handler":              "capture",
        "target":               {"advisor": None, "blog": None, "vault_path": None},
        "instructions":         instructions,
        "clarifications_needed": [],
        "privacy_scope":        False,
        "origin_channel":       origin_channel,
        "ok_to_dispatch":       True,
        "blocked_reason":       None,
        "notes":                [],
    }

    if action == "capture":
        plan["handler"] = "capture"
        plan["notes"].append("No actionable intent — falling back to existing parking-lot capture.")
        return plan

    if action == "save_to_recipes":
        plan["handler"] = "recipe"
        plan["target"]["vault_path"] = "50_Inbox/recipes"
        plan["notes"].append("Recipe handler writes to 50_Inbox/recipes/ (see dispatch._dispatch_recipe).")
        return plan

    if action == "write_blog_post":
        plan["handler"] = "blog_post"
        plan["target"]["advisor"] = "creative"
        named = _extract_blog_target(instructions, sites)
        if named:
            plan["target"]["blog"] = named
            plan["notes"].append(f"Blog target detected in instructions: {named}.")
        else:
            plan["clarifications_needed"].append({
                "field":   "blog",
                "prompt":  "Which blog should this draft live in?",
                "options": list(sites) if sites else [],
                "default": DEFAULT_BLOG if DEFAULT_BLOG in sites else None,
            })
            plan["ok_to_dispatch"] = False
        plan["notes"].append("write_blog_post always creates a DRAFT — never published.")
        return plan

    if action == "summarize":
        plan["handler"] = "summarize"
        target = destination or "doc"
        plan["target"]["advisor"] = target
        plan["privacy_scope"] = target in PRIVACY_ADVISORS
        if plan["privacy_scope"] and origin_channel == "telegram":
            plan["ok_to_dispatch"] = False
            plan["blocked_reason"] = (
                f"Routing to '{target}' requires the dashboard — "
                "Telegram is not end-to-end encrypted."
            )
        return plan

    if action == "share_with_advisor":
        if not destination:
            plan["handler"] = "capture"
            plan["notes"].append("share_with_advisor without destination — demoted to capture.")
            return plan
        plan["handler"] = "share"
        plan["target"]["advisor"] = destination
        plan["privacy_scope"] = destination in PRIVACY_ADVISORS
        if plan["privacy_scope"] and origin_channel == "telegram":
            plan["ok_to_dispatch"] = False
            plan["blocked_reason"] = (
                f"Routing to '{destination}' requires the dashboard — "
                "Telegram is not end-to-end encrypted."
            )
        return plan

    plan["handler"] = "capture"
    plan["notes"].append(f"Unknown action '{action}' — falling back to capture.")
    if action == "forward_summary":
        plan["handler"] = "forward_summary"
        plan["target"]["advisor"] = destination or "kai"
        plan["privacy_scope"] = (destination or "") in PRIVACY_ADVISORS
        # Pass through the explicit S-XXXX ref if present in instructions
        instr = (intent.get("instructions") or "").strip()
        ref = None
        for token in instr.split():
            t = token.strip(",.")
            if t.upper().startswith("S-") and len(t) >= 4:
                ref = t.upper()
                break
        plan["forward_ref"] = ref
        return plan


    return plan


def _extract_blog_target(text: str, sites: tuple[str, ...]) -> str | None:
    """Best-effort match of a blog name in the user's instructions.

    Order of attempts:
    1. Site key as a token (e.g. 'sette-uno').
    2. Site URL host (e.g. 'sette-uno.com').
    3. Stripped variants ('the71' vs 'the 71').
    """
    if not text or not sites:
        return None
    lower = text.lower()
    for key in sites:
        if re.search(rf"\b{re.escape(key)}\b", lower):
            return key
        if re.search(rf"\b{re.escape(key)}\.com\b", lower):
            return key
        no_dash = key.replace("-", "")
        if no_dash != key and re.search(rf"\b{re.escape(no_dash)}\b", lower):
            return key
    return None


# ---------------------------------------------------------------------------
# Slice 2b — clarification resume + auto-park
# ---------------------------------------------------------------------------

def validate_choice(clar: dict, raw_value: str) -> str | None:
    """Map a free-text reply to a canonical option for this clarification.

    Accepts: exact match (case-insensitive), 1-based index ("1", "2"), or a
    unique prefix. Returns the canonical option string, or None if no match.
    """
    if raw_value is None:
        return None
    options = clar.get("options") or []
    if not options:
        return None
    cleaned = str(raw_value).strip()
    if not cleaned:
        return None
    lower = cleaned.lower()

    for opt in options:
        if str(opt).lower() == lower:
            return opt

    if cleaned.isdigit():
        idx = int(cleaned) - 1
        if 0 <= idx < len(options):
            return options[idx]

    prefix_hits = [opt for opt in options if str(opt).lower().startswith(lower)]
    if len(prefix_hits) == 1:
        return prefix_hits[0]

    return None


def resume(pending_entry: dict, choice: dict) -> dict:
    """Merge Leo's choice into the half-built dispatch plan.

    Args:
        pending_entry: full row from clarification_store.
        choice: {"field": str, "value": str} — what Leo picked.

    Returns a fully-formed dispatch plan with clarifications_needed=[] and
    ok_to_dispatch=True. Pure function — does not mutate the entry.
    """
    plan = json.loads(json.dumps(pending_entry["dispatch_plan"]))  # deep copy
    field = choice.get("field")
    value = choice.get("value")
    if not field or value is None:
        plan["notes"].append("resume() received malformed choice — leaving plan as-is.")
        return plan

    if field == "blog":
        plan["target"]["blog"] = value
        plan["notes"].append(f"Blog target resolved via clarification: {value}.")
    elif field == "advisor":
        plan["target"]["advisor"] = value
        plan["notes"].append(f"Advisor resolved via clarification: {value}.")
    elif field == "vault_path":
        plan["target"]["vault_path"] = value
        plan["notes"].append(f"Vault path resolved via clarification: {value}.")
    else:
        plan["target"][field] = value
        plan["notes"].append(f"Field '{field}' resolved via clarification: {value}.")

    plan["clarifications_needed"] = [
        c for c in plan.get("clarifications_needed", [])
        if c.get("field") != field
    ]
    if not plan["clarifications_needed"]:
        plan["ok_to_dispatch"] = True
        plan["blocked_reason"] = None
    return plan


def auto_park_plan(pending_entry: dict) -> dict:
    """Build a capture-fallback dispatch plan for an expired pending row.

    Per Slice 2b spec — when Leo never replies, we still park the item, but
    we populate `intent` + `target.advisor` so future re-engagement can pick
    up where we left off rather than re-parsing cold.
    """
    plan = json.loads(json.dumps(pending_entry["dispatch_plan"]))
    plan["handler"] = "capture"
    plan["ok_to_dispatch"] = True
    plan["blocked_reason"] = None
    plan["clarifications_needed"] = []
    plan["notes"].append(
        f"Auto-parked after expiry. Original intent + target retained for "
        f"re-engagement (action={pending_entry['parsed_intent'].get('action')}, "
        f"advisor={plan['target'].get('advisor')})."
    )
    return plan
