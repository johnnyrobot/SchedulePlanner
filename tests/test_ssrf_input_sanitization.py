"""SSRF / injection regression for the chat router's input sanitizers (ship-review).

The chat router lets a LOCAL model emit a JSON intent that drives a small fixed
set of READ-ONLY live lookups. The campus/terms/courses it proposes are
attacker-influenceable (a malicious catalog PDF or eLumen payload could steer the
model), so chat_assist sanitizes them before they reach a URL. ``campus`` is the
SSRF-critical field — it is interpolated into the request PATH and the
program_mapper Origin header — so it must never carry the characters needed to
redirect a request to a different host, scheme, or path.

These tests are deliberately NON-VACUOUS: loosening ``_clean_campus`` to allow
'.', '/', ':' (the characters required to build an alternate URL) fails here.
"""
import chat_assist as ca


# --------------------------------------------------------------- campus (URL path)
SSRF_CAMPUS_PAYLOADS = [
    "evil.com",                 # alternate host (has '.')
    "la mission",               # whitespace
    "LAMC/../../admin",         # path traversal
    "LAMC/extra",               # extra path segment
    "http://evil.com",          # scheme + host
    "https://evil.com/x",       # scheme + host + path
    "127.0.0.1",                # raw ip (has '.')
    "localhost:11434",          # host:port (the local Ollama port, via ':')
    "LAMC?q=1",                 # query string
    "LAMC#frag",                # fragment
    "%2e%2e",                   # url-encoded traversal ('%')
    "a/b",                      # slash
    "../",                      # traversal
    "@evil.com",                # userinfo@host
    "AAAAAAAAA",                # 9 chars -> over the 8-char bound
    "",                         # empty
    None,                       # missing
]


def test_clean_campus_rejects_every_url_injection_payload():
    for p in SSRF_CAMPUS_PAYLOADS:
        assert ca._clean_campus(p) is None, (
            f"_clean_campus must reject {p!r} (host/scheme/path SSRF injection)")


def test_clean_campus_forbids_the_chars_needed_to_build_an_alternate_url():
    # The crux: every character that could redirect a request off-host or off-path
    # must be rejected. If _clean_campus ever stops requiring isalnum(), this breaks.
    for ch in "./:?#@\\%& \t":
        assert ca._clean_campus(f"LA{ch}MC") is None, (
            f"_clean_campus must reject the URL-significant char {ch!r}")


def test_clean_campus_accepts_only_short_alphanumeric_and_normalizes():
    assert ca._clean_campus("lamc") == "LAMC"            # uppercased
    assert ca._clean_campus("  LAVC  ") == "LAVC"        # stripped
    assert ca._clean_campus("LAMC") == "LAMC"
    assert ca._clean_campus("ABCDEFGH") == "ABCDEFGH"    # 8 chars = boundary, allowed
    assert ca._clean_campus("ABCDEFGHI") is None         # 9 chars rejected


# --------------------------------------------------------------- terms (URL path int)
def test_clean_terms_keeps_only_positive_ints():
    # term is interpolated into the path (/listing/{campus}/{term}); only positive
    # ints survive, so no string can ride into the URL.
    assert ca._clean_terms([2268, "2270", -1, 0, "x", None, 2268]) == [2268, 2270]
    assert ca._clean_terms("2268") == []                 # non-list -> empty
    assert ca._clean_terms(["../", "1;DROP TABLE", "9 OR 1=1"]) == []
    assert ca._clean_terms([2268] * 20) == [2268]        # dedup
    assert ca._clean_terms(list(range(1, 100))) == [1, 2, 3, 4, 5, 6]   # capped at 6


# --------------------------------------------------------------- courses (query param)
def test_clean_courses_bounds_and_dedups_untrusted_list():
    out = ca._clean_courses(["MATH 261", "MATH 261", " CHEM 101 ", "X"])
    assert out == ["MATH 261", "CHEM 101", "X"]          # stripped + deduped
    assert len(ca._clean_courses([f"C{i}" for i in range(50)])) == ca.MAX_COURSES
    assert ca._clean_courses(123) == []                  # non-list/str -> empty


# --------------------------------------------------------- source guard on the clamp
def test_clean_campus_source_still_requires_isalnum_and_a_length_bound():
    # Mirror the determinism source-pins: a refactor that drops the isalnum() OR the
    # length bound (the two halves of the SSRF clamp) is caught even if some future
    # behavioral case slips the payload list above.
    import inspect
    src = inspect.getsource(ca._clean_campus)
    assert ".isalnum()" in src, "_clean_campus must keep the isalnum() character clamp"
    assert "len(" in src and "8" in src, "_clean_campus must keep the length bound"
