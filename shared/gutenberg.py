"""AR-3 / KAI-965 — Gutenberg block-grammar validator (the safety floor).

Runs on the block document a local model generates, BEFORE it reaches the WordPress
write chokepoint (`wordpress.create_page`). The generator is a 7B local model
(qwen2.5 on kai-mini); this module is what guarantees it can never write malformed
or off-whitelist block markup to a site — invalid markup fails the step, it does not
get authored. It is the mechanical twin of `brand_drift.detect()` (WP-20.2): pure,
no I/O, no network, fully unit-testable, safe to call inline on the write path.

Gutenberg block grammar (the subset we author):
  container block : <!-- wp:name {attrs} --> …inner… <!-- /wp:name -->
  void block      : <!-- wp:name {attrs} /-->            (no closing delimiter)
  `name` is a core block (bare, e.g. `paragraph`) or namespaced (`ns/block`).
  `{attrs}` is optional and, when present, must be a JSON object.

Scope of THIS validator (v1) — checks that live entirely on the markup string:
  balance   : every non-void opening delimiter has a correctly-nested close   (high)
  whitelist : every block name is in the allowed set                          (high)
  attrs     : a present attrs blob parses as a JSON object                    (high)
  void_form : a void `/-->` block carries no closing delimiter                (high)
  empty     : the document contains at least one block                        (warn)

Deferred (documented, not silently dropped): per-block attribute schema validation
(e.g. a columns block's column count), inner-HTML well-formedness, and block-vs-theme
support — those need the property's theme.json / block registry, same ethos as
brand_drift's WP-20.2b deferrals.
"""
from __future__ import annotations

import json
import re

# Core + namespaced blocks the generator is allowed to author. A page layout that
# reaches for a block outside this set is a generation error, not something we ship
# to a live-ish draft unseen. Extend deliberately, with a test, never silently.
ALLOWED_BLOCKS: frozenset[str] = frozenset({
    # sectioning / layout
    "group", "columns", "column", "cover", "media-text", "spacer", "separator",
    # content
    "paragraph", "heading", "list", "list-item", "quote", "image", "gallery",
    "buttons", "button", "html", "table", "code", "preformatted", "pullquote",
    # site-ish primitives that show up in page bodies
    "shortcode", "embed", "video", "audio",
})

# One delimiter: optional leading `/` (close), `wp:name`, optional attrs region,
# optional trailing `/` (void). The attrs region is matched non-greedily up to the
# closing `-->`; JSON never contains `-->`, so this is robust to nested braces
# (a naive `\{.*?\}` is not).
_DELIM = re.compile(
    r"<!--\s*(?P<close>/)?wp:(?P<name>[a-z][a-z0-9-]*(?:/[a-z][a-z0-9-]*)?)"
    r"(?P<body>.*?)(?P<void>/)?\s*-->",
    re.DOTALL,
)


def _finding(ftype: str, severity: str, detail: str) -> dict:
    return {"type": ftype, "severity": severity, "detail": detail}


def validate(markup: str, *, allowed: frozenset[str] | None = None) -> dict:
    """Validate a Gutenberg block document.

    Returns a structured report (same shape as brand_drift.detect):
      {checked: bool, valid: bool, blocks: int, findings: [{type, severity, detail}], summary}
    `valid` is True iff there are NO high-severity findings. A caller on the write
    path MUST refuse to author when `valid` is False.
    """
    allowed = ALLOWED_BLOCKS if allowed is None else allowed
    markup = markup or ""
    findings: list[dict] = []
    stack: list[str] = []
    opened = 0

    for m in _DELIM.finditer(markup):
        name = m.group("name")
        is_close = m.group("close") == "/"
        is_void = m.group("void") == "/"
        body = (m.group("body") or "").strip()

        if is_close:
            # a close carries no attrs and no void marker
            if not stack:
                findings.append(_finding(
                    "unbalanced", "high",
                    f"closing delimiter </wp:{name}> with no open block"))
            elif stack[-1] != name:
                findings.append(_finding(
                    "misnested", "high",
                    f"closing </wp:{name}> but innermost open block is <wp:{stack[-1]}>"))
            else:
                stack.pop()
            continue

        # opening or void delimiter
        opened += 1
        if name not in allowed:
            findings.append(_finding(
                "block_not_allowed", "high",
                f"block 'wp:{name}' is not in the allowed set"))

        if body:
            try:
                attrs = json.loads(body)
            except json.JSONDecodeError as e:
                findings.append(_finding(
                    "bad_attrs", "high",
                    f"block 'wp:{name}' attrs are not valid JSON: {e.msg}"))
            else:
                if not isinstance(attrs, dict):
                    findings.append(_finding(
                        "bad_attrs", "high",
                        f"block 'wp:{name}' attrs must be a JSON object, got "
                        f"{type(attrs).__name__}"))

        if not is_void:
            stack.append(name)

    # any block left on the stack never got a close
    for name in stack:
        findings.append(_finding(
            "unbalanced", "high",
            f"block <wp:{name}> is never closed"))

    if opened == 0:
        # a document with real text but ZERO recognized block delimiters is
        # malformed, not empty — this is exactly the shape a model emits when it
        # writes `<wp:heading>` XML-ish tags instead of `<!-- wp:heading -->`
        # comment delimiters. Only a whitespace-only document is a benign warn.
        if markup.strip():
            findings.append(_finding(
                "no_blocks", "high",
                "content is present but contains no valid Gutenberg block "
                "delimiters (`<!-- wp:name -->`) — malformed block markup"))
        else:
            findings.append(_finding(
                "empty", "warn",
                "document contains no Gutenberg blocks"))

    valid = not any(f["severity"] == "high" for f in findings)
    highs = sum(1 for f in findings if f["severity"] == "high")
    warns = sum(1 for f in findings if f["severity"] == "warn")
    summary = ("gutenberg: valid" if valid and not warns
               else "gutenberg: valid (advisory only)" if valid
               else f"gutenberg: INVALID — {highs} blocking, {warns} advisory")
    return {
        "checked": True,
        "valid": valid,
        "blocks": opened,
        "findings": findings,
        "summary": summary,
    }
