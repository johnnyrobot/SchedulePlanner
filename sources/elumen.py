"""eLumen prerequisite logic -> engine CNF catalog strings (FIXTURE-ONLY).

================================ NOT VALIDATED ON REAL DATA ===================
This module is **FIXTURE-ONLY**. There is NO known real eLumen REST endpoint,
auth scheme, or captured response. Everything here parses a SELF-DEFINED,
COMMITTED fixture (``tests/fixtures/elumen_prereqs_LAMC.json``) whose shape we
DEFINE and DOCUMENT below — it is **ASSUMED**, not confirmed against a real
eLumen payload. First contact with real eLumen data may require adjusting
``parse_elumen_dnf`` (the change stays isolated to this one module). NOTHING in
this module opens a socket: it is a pure file read + pure DNF->CNF conversion.
The live eLumen HTTP client is intentionally NOT built here (no endpoint/auth to
build against without fabrication) — it is plan-only (see the m7 design §10).
==============================================================================

ASSUMED real eLumen prerequisite response shape (documented so a real capture is
recognizable, and so a future ``sources/elumen_client.py`` knows the target):

    {
      "source": "elumen ...",          # provenance label
      "campus": "LAMC",                # campus code
      "courses": [
        {
          "course_id": "PHYS 102",     # the gated course (catalog Course ID form)
          "raw": "(MATH 245 and MATH 246) or PHYS 185",   # human prereq text (provenance)
          "dnf": [["MATH 245", "MATH 246"], ["PHYS 185"]] # OUTER OR'd / INNER AND'd
        },
        ...
      ]
    }

eLumen prerequisite logic is naturally **DNF** (alternative pathways OR'd, each
pathway a set of co-required courses AND'd). The engine consumes **CNF** (AND of
OR-groups). We convert per course via ``sources.prereq_cnf.dnf_to_cnf`` (exact
distribution with a configurable clause-count guard + a FLAGGED conservative
under-approximation fallback — never silent). The ``course_id`` is passed as
``gated_course`` so the converter normalizes it and drops any self-reference
(a course cannot be its own prerequisite — a self-prereq makes the solver
false-INFEASIBLE).

PURITY: imports neither pandas nor httpx in the hot path. ``parse_elumen_dnf``
normalizes literals with ``mapping._norm`` (the catalog ``Course ID`` normalizer)
so join keys match; the heavy lifting is in the already-pure ``prereq_cnf``.
"""
from __future__ import annotations

import json
import pathlib

from .http import SourceDataError
from .mapping import _norm
from .prereq_cnf import DEFAULT_MAX_CLAUSES, ConversionResult, dnf_to_cnf

# Human label so a malformed-record guard names where the bad data came from,
# mirroring sources/http.py and sources/mapping.py style.
SOURCE = "eLumen (FIXTURE-ONLY)"


def load_elumen_fixture(path) -> list:
    """Load the committed eLumen fixture and return its ``courses`` list.

    FIXTURE-ONLY: a pure file read (no network, no httpx client). Raises
    ``SourceDataError`` naming the file if the JSON is missing the expected
    ``courses`` list (so a future drift in the fixture/real shape is caught
    loudly rather than silently producing an empty map).
    """
    path = pathlib.Path(path)
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise SourceDataError(
            f"{SOURCE}: could not read eLumen fixture {path} ({exc})."
        ) from exc
    if not isinstance(data, dict) or not isinstance(data.get("courses"), list):
        raise SourceDataError(
            f"{SOURCE}: eLumen fixture {path} missing a 'courses' list "
            f"(got {type(data).__name__}). The assumed eLumen shape may have drifted."
        )
    return data["courses"]


def parse_elumen_dnf(record) -> list:
    """Normalize one eLumen course record's ``dnf`` field to a clean DNF.

    Returns ``list[list[str]]`` (OUTER OR'd / INNER AND'd) with every literal
    ``_norm``-canonicalized (upper + whitespace-collapsed) so it matches catalog
    ``Course ID``. A missing/None/empty ``dnf`` field is treated as "no
    prerequisite" -> ``[]`` (a real export may omit the field for unrestricted
    courses; that is not an error). A NON-EMPTY but wrongly-shaped ``dnf`` (not a
    list whose members are all lists) raises ``SourceDataError`` naming the
    course, so a schema drift is loud rather than silently dropped.
    """
    dnf = record.get("dnf")
    if dnf is None:
        return []
    cid = record.get("course_id", "<unknown>")
    if not isinstance(dnf, (list, tuple)):
        raise SourceDataError(
            f"{SOURCE}: course {cid!r} has a malformed 'dnf' "
            f"({type(dnf).__name__}); expected a list of AND-branches."
        )
    normalized = []
    for and_term in dnf:
        if not isinstance(and_term, (list, tuple)):
            raise SourceDataError(
                f"{SOURCE}: course {cid!r} has a malformed AND-branch "
                f"{and_term!r}; expected a list of course literals."
            )
        normalized.append([_norm(lit) for lit in and_term])
    return normalized


def build_prereq_map(records, *, max_clauses=DEFAULT_MAX_CLAUSES):
    """Build the catalog prereq-string map + per-course conversion records.

    For each eLumen course record: normalize its DNF (``parse_elumen_dnf``) and
    convert DNF->CNF via ``prereq_cnf.dnf_to_cnf``, passing the ``course_id`` as
    ``gated_course`` (so the converter ``_norm``'s it and drops any
    self-reference). Returns a 2-tuple:

      - ``prereqs``: ``dict[course_id -> catalog CNF string]`` (``""`` = no
        prereq), ready to thread into ``mapping.build_catalog_df(prereqs=...)``;
      - ``results``: ``dict[course_id -> ConversionResult]`` carrying the
        out-of-band exact/fallback flag for the report (budget-exceeded courses
        are flagged ``exact=False`` with a structured ``fallback_reason``).

    Pure: no network, no httpx client. ``max_clauses`` is the configurable guard
    forwarded to ``dnf_to_cnf`` (a real-data run is tunable without a code
    change). FIXTURE-ONLY caveat applies to the whole map: it is built from the
    self-defined eLumen fixture and is NOT validated on real eLumen data.
    """
    prereqs: dict = {}
    results: dict[str, ConversionResult] = {}
    for record in records:
        cid = record.get("course_id")
        if cid is None:
            raise SourceDataError(
                f"{SOURCE}: an eLumen record is missing 'course_id' ({record!r})."
            )
        dnf = parse_elumen_dnf(record)
        result = dnf_to_cnf(dnf, gated_course=cid, max_clauses=max_clauses)
        # Key the catalog map on the same _norm form the engine/catalog uses, so
        # build_catalog_df(prereqs).get(cid) hits. (For the committed fixture the
        # ids are already canonical; _norm is idempotent there.)
        key = _norm(cid)
        prereqs[key] = result.cnf_string
        results[key] = result
    return prereqs, results
