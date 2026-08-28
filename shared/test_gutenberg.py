#!/usr/bin/env python3
"""AR-3 / KAI-965 — gutenberg.validate() tests — self-contained (no pytest dependency).

Run with `python3 shared/test_gutenberg.py`. Pure module, no network / no I/O.
Proves the safety floor holds: a well-formed on-whitelist document passes, and every
way a local model can emit broken block markup (unbalanced, misnested, off-whitelist,
bad JSON attrs, stray close) is caught as high-severity so the write is refused.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import gutenberg  # noqa: E402

_FAILS = []


def _check(name, cond):
    print(("PASS" if cond else "FAIL"), name)
    if not cond:
        _FAILS.append(name)


def _highs(rep):
    return [f for f in rep["findings"] if f["severity"] == "high"]


# ── 1. a clean, nested, on-whitelist document is valid ──────────────────────
CLEAN = """
<!-- wp:group {"tagName":"section"} -->
<div class="wp-block-group">
<!-- wp:heading {"level":1} --><h1>Strategy-led consulting</h1><!-- /wp:heading -->
<!-- wp:paragraph --><p>We help teams ship.</p><!-- /wp:paragraph -->
<!-- wp:buttons -->
<div class="wp-block-buttons">
<!-- wp:button --><div class="wp-block-button"><a>Contact</a></div><!-- /wp:button -->
</div>
<!-- /wp:buttons -->
<!-- wp:spacer {"height":"48px"} /-->
</div>
<!-- /wp:group -->
"""
r = gutenberg.validate(CLEAN)
_check("clean doc is valid", r["valid"] is True)
_check("clean doc has no high findings", _highs(r) == [])
_check("clean doc counts blocks (group,heading,paragraph,buttons,button,spacer=6)",
       r["blocks"] == 6)

# ── 2. a void block needs no closing delimiter (spacer alone) ───────────────
r = gutenberg.validate('<!-- wp:separator /-->')
_check("lone void block is valid", r["valid"] is True and _highs(r) == [])

# ── 3. unbalanced: an opening block never closed ────────────────────────────
r = gutenberg.validate('<!-- wp:group --><p>x</p>')
_check("unclosed block is INVALID", r["valid"] is False)
_check("unclosed block flags unbalanced",
       any(f["type"] == "unbalanced" for f in _highs(r)))

# ── 4. stray closing delimiter with nothing open ────────────────────────────
r = gutenberg.validate('<!-- /wp:group -->')
_check("stray close is INVALID", r["valid"] is False)
_check("stray close flags unbalanced",
       any(f["type"] == "unbalanced" for f in _highs(r)))

# ── 5. misnested close (wrong innermost block) ──────────────────────────────
r = gutenberg.validate(
    '<!-- wp:group --><!-- wp:paragraph --><p>x</p><!-- /wp:group --><!-- /wp:paragraph -->')
_check("misnested close is INVALID", r["valid"] is False)
_check("misnested close flags misnested",
       any(f["type"] == "misnested" for f in _highs(r)))

# ── 6. off-whitelist block name ─────────────────────────────────────────────
r = gutenberg.validate('<!-- wp:evil-widget --><div>x</div><!-- /wp:evil-widget -->')
_check("off-whitelist block is INVALID", r["valid"] is False)
_check("off-whitelist flags block_not_allowed",
       any(f["type"] == "block_not_allowed" for f in _highs(r)))

# ── 7. malformed JSON attrs ─────────────────────────────────────────────────
r = gutenberg.validate('<!-- wp:heading {level:1,} --><h1>x</h1><!-- /wp:heading -->')
_check("bad JSON attrs is INVALID", r["valid"] is False)
_check("bad attrs flags bad_attrs",
       any(f["type"] == "bad_attrs" for f in _highs(r)))

# ── 8. attrs that are valid JSON but not an object (array) ──────────────────
r = gutenberg.validate('<!-- wp:list [1,2,3] --><ul></ul><!-- /wp:list -->')
_check("non-object attrs is INVALID", r["valid"] is False)
_check("non-object attrs flags bad_attrs",
       any(f["type"] == "bad_attrs" for f in _highs(r)))

# ── 9. namespaced block on the whitelist parses (ns/name form) ──────────────
r = gutenberg.validate('<!-- wp:core/embed {"url":"x"} /-->',
                       allowed=frozenset({"core/embed"}))
_check("namespaced allowed block is valid", r["valid"] is True and _highs(r) == [])

# ── 10. empty document is a warn, not a hard fail ───────────────────────────
r = gutenberg.validate('   \n  ')
_check("empty doc is valid (warn only)", r["valid"] is True)
_check("empty doc flags empty(warn)",
       any(f["type"] == "empty" and f["severity"] == "warn" for f in r["findings"]))

# ── 10b. non-empty text with NO block delimiters is INVALID (the `<wp:x>` tell) ─
r = gutenberg.validate("<wp:heading level='1'>Hi</wp:heading><wp:paragraph>x</wp:paragraph>")
_check("xml-ish tags (no delimiters) is INVALID", r["valid"] is False)
_check("no-delimiters flags no_blocks(high)",
       any(f["type"] == "no_blocks" for f in _highs(r)))

# ── 11. None / falsy input does not crash ───────────────────────────────────
r = gutenberg.validate(None)
_check("None input is handled (no crash, warn)", r["checked"] and r["valid"])

# ── 12. nested attrs with braces don't break the delimiter parse ────────────
r = gutenberg.validate(
    '<!-- wp:cover {"style":{"color":{"background":"#111"}}} -->'
    '<div>x</div><!-- /wp:cover -->')
_check("nested-brace JSON attrs parse and validate", r["valid"] is True and _highs(r) == [])

print()
if _FAILS:
    print(f"{len(_FAILS)} FAILED: {_FAILS}")
    sys.exit(1)
print("ALL PASS")
