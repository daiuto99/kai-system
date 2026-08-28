"""AR-3 / KAI-965 — wp_generate.generate_blocks() orchestration tests.

Loads capabilities/wp_generate.py in ISOLATION (importlib, not the capabilities
package) so no orchestrator deps / no network / no council are needed. The single
model chokepoint (`_call_tool`) is stubbed per test. Proves:
  - plan -> render -> validate yields a gutenberg-valid document
  - an invalid section is repaired within the retry budget (validator error fed back)
  - a section that never validates fails CLOSED (raise), never returns bad markup
  - empty plan / empty render fail closed
  - the returned `content` passes gutenberg.validate
"""
import importlib.util
import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SHARED = os.path.join(_HERE, "..", "..", "shared")
if _SHARED not in sys.path:
    sys.path.insert(0, _SHARED)

import gutenberg  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "wp_generate_under_test",
    os.path.join(_HERE, "..", "capabilities", "wp_generate.py"))
wg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wg)

_GOOD = '<!-- wp:paragraph --><p>ok</p><!-- /wp:paragraph -->'
_BAD = '<!-- wp:paragraph --><p>broken'  # never closed -> invalid


def _install(plan_sections, render_fn):
    state = {"render_calls": 0}

    def fake_call_tool(messages, tool, *, timeout=180, fallback_key=None):
        name = tool["function"]["name"]
        if name == "plan_page":
            return {"sections": plan_sections}
        if name == "render_section":
            i = state["render_calls"]
            state["render_calls"] += 1
            return {"block_markup": render_fn(i)}
        raise AssertionError(name)

    wg._call_tool = fake_call_tool
    wg.load_style = lambda slug: None
    return state


def test_happy_path_single_section():
    _install([{"type": "hero", "intent": "x", "copy_brief": "y"}], lambda i: _GOOD)
    r = wg.generate_blocks("the71c", "make a hero")
    assert r["content"]
    assert gutenberg.validate(r["content"])["valid"]
    assert len(r["plan"]) == 1
    assert r["style_used"] is False


def test_multi_section_assembles_valid():
    _install([{"type": "hero", "intent": "a", "copy_brief": "b"},
              {"type": "cta", "intent": "c", "copy_brief": "d"}], lambda i: _GOOD)
    r = wg.generate_blocks("the71c", "two sections")
    assert gutenberg.validate(r["content"])["valid"]
    assert len(r["sections"]) == 2


def test_invalid_section_is_repaired():
    st = _install([{"type": "hero", "intent": "a", "copy_brief": "b"}],
                  lambda i: _BAD if i == 0 else _GOOD)
    r = wg.generate_blocks("the71c", "repair me")
    assert gutenberg.validate(r["content"])["valid"]
    assert st["render_calls"] == 2  # original + one repair


def test_never_valid_fails_closed():
    _install([{"type": "hero", "intent": "a", "copy_brief": "b"}], lambda i: _BAD)
    with pytest.raises(wg.GenerateError):
        wg.generate_blocks("the71c", "never valid")


def test_empty_plan_fails_closed():
    _install([], lambda i: _GOOD)
    with pytest.raises(wg.GenerateError):
        wg.generate_blocks("the71c", "no sections")


def test_empty_render_fails_closed():
    _install([{"type": "hero", "intent": "a", "copy_brief": "b"}], lambda i: "   ")
    with pytest.raises(wg.GenerateError):
        wg.generate_blocks("the71c", "empty render")


def test_read_key_empty_without_source():
    wg._KEY_FILE = "/nonexistent/key"
    os.environ.pop("LITELLM_MASTER_KEY", None)
    assert wg._read_key() == ""
