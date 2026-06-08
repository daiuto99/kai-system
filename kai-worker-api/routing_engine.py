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

PRIVACY_ADVISORS = {"ember", "doc"}

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
    origin_channel: str = "slack",
    wp_sites: tuple[str, ...] | None = None,
) -> dict:
    """Map a parsed intent to a dispatch plan.

    Args:
        intent: output of intent_parser.parse_intent().
        origin_channel: "slack" | "telegram" | "web" — affects privacy + surface routing.
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
        plan["notes"].append("Recipe vault structure is owned by Sprint A Slice 4 — vault_path is placeholder.")
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
