"""ui.html output-escaping regression guard (ship-review, Tests pillar).

ui.html is the pywebview front-end and has NO JS test harness, yet it renders
untrusted workbook / live-API / model-derived strings into the DOM (~30 innerHTML
sinks). A full DOM-level XSS test needs jsdom/Playwright (a deeper follow-up the
4-pillar review flags); these are dependency-free STATIC invariants that catch the
realistic regressions: breaking the escaper, mass-stripping escapeHtml, switching
the untrusted model output to innerHTML, or rendering the live-API error unescaped.

Non-vacuous: each test fails if the corresponding escaping control is removed.
"""
import pathlib
import re

UI = (pathlib.Path(__file__).parent.parent / "ui.html").read_text(encoding="utf-8")


def test_escapeHtml_is_defined_and_escapes_all_five_metacharacters():
    # The escaper must neutralize every char that can break out of HTML text/attr.
    m = re.search(r"function escapeHtml\([^)]*\)\s*\{.*?\}", UI, re.DOTALL)
    assert m, "escapeHtml() must be defined in ui.html"
    body = m.group(0)
    assert re.search(r"/\[&<>\"'\]/", body) or re.search(r"/\[&<>['\"]['\"]\]/", body), \
        "escapeHtml must match the [&<>\"'] character class"
    for needle in ("&amp;", "&lt;", "&gt;", "&quot;", "&#39;"):
        assert needle in body, f"escapeHtml must map a metacharacter to {needle}"


def test_untrusted_model_answer_renders_via_textContent_not_innerHTML():
    # The model's free-text answer is the highest-risk untrusted string; it must sink
    # to .textContent (browser-escaped), never .innerHTML.
    assert re.search(r"\.textContent\s*=\s*text\b", UI), \
        "the model answer must be assigned via textContent (browser-escaped)"
    assert not re.search(r"\.innerHTML\s*=\s*text\b", UI), \
        "the model answer must NOT be assigned via innerHTML (XSS sink)"


def test_live_api_error_is_escaped_before_it_reaches_innerHTML():
    # res.error comes straight from the live pipeline; every place it is rendered
    # into innerHTML must wrap it in escapeHtml.
    for m in re.finditer(r"\.innerHTML\s*=[^;]*res\.error[^;]*", UI):
        assert "escapeHtml(res.error)" in m.group(0), \
            "live-API res.error must be escapeHtml()-wrapped at every innerHTML sink"


def test_escaping_is_used_pervasively_in_the_render_paths():
    # A mass de-escaping regression (someone deletes escapeHtml across the render
    # builders) drops this far below the current ~169 calls.
    assert UI.count("escapeHtml(") >= 100, \
        "escapeHtml usage collapsed — the render paths may have lost data escaping"


def test_supply_diagnostics_have_chair_friendly_guides():
    """The terse Supply diagnostic buckets need in-context plain-English help."""
    for needle in (
        "Required courses offered in too few of the terms you checked",
        "Only one section is available in the analyzed window",
        "Capacity / fill-rate needs real Cap/Tot enrollment counts",
        "Treat it as a planning signal, not proof of completion impact",
        "Required courses meet at overlapping times",
        "This Supply list is scoped to the selected program",
    ):
        assert needle in UI
    assert "mk('Capacity / fill-rate'" in UI


def test_no_innerHTML_assigns_a_bare_untrusted_property_unescaped():
    # Targeted antipattern scan: an innerHTML assignment whose RHS is a SINGLE bare
    # untrusted property read (e.g. `el.innerHTML = res.error`) with no escapeHtml
    # and no textContent. The render sinks instead assign a pre-escaped `html`/`chunk`
    # builder var or call escapeHtml inline, so this must find nothing.
    bad = []
    for m in re.finditer(r"\.innerHTML\s*=\s*([A-Za-z_$][\w$]*\.[\w$.]+)\s*;", UI):
        rhs = m.group(1)
        if "escapeHtml" not in rhs:
            bad.append(rhs)
    assert not bad, f"innerHTML assigned a raw unescaped property: {bad}"
