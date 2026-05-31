"""Live eLumen Public Portal prerequisite client (REAL, public, unauthenticated).

================================ REAL SOURCE ==================================
Unlike the FIXTURE-ONLY ``sources/elumen.py`` (which parses a self-defined DNF
fixture), THIS module talks to the **real, public, unauthenticated** eLumen
Public Portal REST endpoint, verified live 2026-05-30:

    GET https://portalapi-laccd.elumenapp.com/public/courses
        ?status=approved&tenant=<campus tenant>&query=<subject or code>
        &pageSize=<=25&page=<1-based>

Response envelope: ``_embedded.courses[]`` + ``pagination`` + ``_links``. Each
course wrapper carries ``subject``, ``number``, ``suffix``, ``code`` and a
``fullCourseInfo`` field that is a JSON **string** (you must ``json.loads`` it);
the parsed object exposes a recursive boolean ``requisites`` tree.

NO OVERCLAIMING / ToU CAVEAT: this client implements ONLY the verified request
+ response shape above. Terms-of-Use review, rate-limit policy, and human
approval for hitting the live endpoint at scale are STILL PENDING and OUT OF
SCOPE here. Do NOT treat this module as production-cleared to crawl eLumen; the
single networked path is exercised only by the ``@pytest.mark.live`` test, which
is deselected by default. Be a polite client (small pageSize, bounded pages).
==============================================================================

ARCHITECTURE (mirrors sources/schedule.py + sources/program_mapper.py):
  - ALL network IO goes through ``sources.http.get_json`` (injectable httpx
    client, browser UA, SourceError/SourceHTTPError/SourceDataError). The engine
    (engine.py) is never touched — it consumes a finished workbook only.
  - The parser (``requisites_to_dnf``) and normalizer (``normalize_course_code``)
    are PURE: no httpx import on the hot path, mirroring sources/elumen.py. The
    networked ``fetch_*`` functions are thin shells around the pure core.
  - The emitted records feed the EXISTING ``sources.elumen.build_prereq_map``
    UNCHANGED (which converts DNF->CNF with the budget-guarded conservative
    fallback); this module does NOT reimplement DNF->CNF.

REQUISITE SEMANTICS — the discriminator is the leaf ``itemType``:
  Each leaf ``item`` carries ``isCourse`` (bool), ``itemType`` (one of
  "Prerequisite" | "Advisory" | "Co-Requisite") and a CONCATENATED ``code``
  ("BIOTECH002"). There is NO ``requisiteType`` field. ONLY a leaf whose
  ``itemType`` collapses (case-insensitive, hyphens/spaces removed) to
  "prerequisite" becomes a hard ordering constraint. Co-Requisite and Advisory
  leaves are EXCLUDED from prereq ordering.

UNDER-APPROXIMATE (never false-infeasible), matching sources/prereq_cnf.py
doctrine: the top-level OR is a CONTAINER of independent requisites tagged by
itemType, so dropping non-prereq branches while keeping the prereq structure is
correct and never falsely tightens the schedule. RESIDUAL RISK (flagged, never
silently assumed): if a course's ONLY satisfiable path were a non-prereq
alternative OR'd against a prereq, dropping it could over-tighten. The coverage
summary surfaces unmatched prereq targets so this stays visible.

COURSE IDENTITY = ``normalize_course_code(wrapper["code"])`` (e.g. "CHEM101" ->
"CHEM 101"). Do NOT use subject+number for identity: they can disagree (the
MICRO020 wrapper's number is "020" but its Co-Req leaves are CHEM051/CHEM065;
code-based identity is the right one). Fall back to subject+number ONLY when the
wrapper ``code`` is missing/empty.
"""
from __future__ import annotations

import json
import re

from .http import SourceDataError, get_json

# ---- verified endpoint + tenants ------------------------------------------
API_BASE = "https://portalapi-laccd.elumenapp.com/public/courses"
SOURCE = "eLumen Public Portal"

# Campus -> tenant (all suffixed .elumenapp.com). Verified live 2026-05-30.
CAMPUS_TENANTS = {
    "LAMC": "lamission.elumenapp.com",
    "LAVC": "lavc.elumenapp.com",
    "LAPC": "pierce.elumenapp.com",
    "LAHC": "lahc.elumenapp.com",
    "LATTC": "lattc.elumenapp.com",
    "LACC": "lacc.elumenapp.com",
    "ELAC": "elac.elumenapp.com",
    "LASC": "lasc.elumenapp.com",
    "WLAC": "wlac.elumenapp.com",
}
DEFAULT_TENANT = CAMPUS_TENANTS["LAMC"]

# eLumen caps a query at 25 results per page.
MAX_PAGE_SIZE = 25
DEFAULT_MAX_PAGES = 20

# The leaf itemType values that count as a hard prerequisite ordering
# constraint (after collapsing case + hyphens/spaces). Co-Requisite ("corequisite")
# and Advisory ("advisory") are deliberately ABSENT, so they are excluded.
_PREREQ_ITEM_TYPES = frozenset({"prerequisite"})


def tenant_for(campus):
    """Map a campus code (e.g. 'LAMC') to its eLumen tenant host.

    Raises ``SourceDataError`` (naming the source) for an unknown campus rather
    than silently defaulting, so a typo surfaces loudly instead of querying the
    wrong campus.
    """
    key = str(campus).strip().upper()
    try:
        return CAMPUS_TENANTS[key]
    except KeyError:
        raise SourceDataError(
            f"{SOURCE}: unknown campus {campus!r}; known campuses are "
            f"{sorted(CAMPUS_TENANTS)}."
        ) from None


# ---- normalizer (PURE) -----------------------------------------------------
# Subjects that legitimately END in 'C' — do NOT split a trailing C-ID off these
# (PSYC065 must stay "PSYC 65", not "PSY C65").
_C_GUARD = ("MUSI", "PSYC", "ACAC", "DANC")
# C-ID: SUBJECT (>= 2 chars, last a letter) + literal 'C' + digits(+letters).
# Requires a real subject so a BARE C-ID ("C1000", no subject) is NOT split.
_CID_RE = re.compile(r"^([A-Z][A-Z./&\- ]*[A-Z])C(\d+[A-Z]*)$")
# Standard: SUBJECT (anything ending in a letter, possibly multi-word / "/" /
# "-" / dots) + number token.
_CODE_RE = re.compile(r"^(.*?[A-Z])\s*(\d+[A-Z]*(?:CE)?)$")
# A bare number token: optional leading C-prefix + digits + optional trailing
# letters / CE suffix.
_NUMTOK_RE = re.compile(r"^(C?)(\d+)([A-Z]*(?:CE)?)$")


def _norm(x):
    # Stable under sources.mapping._norm: UPPERCASE + single-spaced. Pinned
    # byte-identical so normalized literals match catalog Course IDs.
    return re.sub(r"\s+", " ", str(x).strip().upper())


def _strip_leading_zeros(digits):
    s = digits.lstrip("0")
    return s if s else "0"


def normalize_course_code(code):
    """Normalize a concatenated eLumen code to a stable catalog Course ID.

    PURE. Output is UPPERCASE + single-spaced and IDEMPOTENT under
    ``sources.mapping._norm``. Rules:
      - "BIOTECH002" -> "BIOTECH 2"  (split number token, strip leading zeros)
      - "MATH261"    -> "MATH 261"
      - "NRS-HCA060" -> "NRS-HCA 60", "KIN MAJ102" -> "KIN MAJ 102" (multi-word /
        punctuated subjects)
      - "STATC1000"  -> "STAT C1000" (C-ID), but "PSYC065" -> "PSYC 65" (guarded)
      - "C1000"      stays "C1000"   (bare C-ID, no subject)
      - "010CE"      -> "10CE", "201A" stays "201A" (preserve letter/CE suffix)
      - no clean match -> the uppercased + collapsed string unchanged (never crash).
    """
    raw = _norm(code)
    if not raw:
        return raw
    compact = raw.replace(" ", "")

    # 1) C-ID first (guard subjects that legitimately end in C). The C-guard
    #    check runs BEFORE accepting the split so PSYC065 stays "PSYC 65".
    #    The guard compares the FULL real subject (candidate subject + the 'C'
    #    we'd peel off): for PSYC065 the regex yields subject='PSY', and
    #    'PSY'+'C' == 'PSYC' IS guarded, so we do NOT split it as a C-ID.
    m = _CID_RE.match(compact)
    if m:
        subject, cdigits = m.group(1), m.group(2)
        if (subject + "C") not in _C_GUARD:
            return f"{subject} C{cdigits}"

    # 2) Standard subject + number token.
    m = _CODE_RE.match(raw)
    if m:
        subject = re.sub(r"\s+", " ", m.group(1)).strip()
        # A bare leading "C" subject means the standard split mis-parsed a bare
        # C-ID (C1000); leave it intact (compact, no inserted space).
        if subject == "C":
            return compact
        numtok = m.group(2)
        nm = _NUMTOK_RE.match(numtok)
        if nm:
            cpref, digits, suffix = nm.group(1), nm.group(2), nm.group(3)
            return f"{subject} {cpref}{_strip_leading_zeros(digits)}{suffix}"
        return f"{subject} {numtok}"

    # 3) Bare number token (no subject), e.g. "010CE" -> "10CE", "C1000" stays.
    nm = _NUMTOK_RE.match(raw)
    if nm:
        cpref, digits, suffix = nm.group(1), nm.group(2), nm.group(3)
        return f"{cpref}{_strip_leading_zeros(digits)}{suffix}"

    # 4) No clean match: degrade gracefully, return upper+collapsed unchanged.
    return raw


# ---- parser (PURE): requisites tree -> prereq-only DNF --------------------
def _itemtype_key(item_type):
    """Collapse an itemType for comparison: lower-case, drop hyphens/spaces.

    So "Co-Requisite" -> "corequisite", "Pre-Requisite"/"Prerequisite" ->
    "prerequisite". Only "prerequisite" is a hard ordering constraint.
    """
    return re.sub(r"[\s\-]+", "", str(item_type or "").strip().lower())


def _is_leaf(node):
    # ANY node carrying an "item" dict is a leaf (SINGLE nodes wrap exactly one
    # leaf), regardless of its "type".
    return isinstance(node, dict) and isinstance(node.get("item"), dict)


def _leaf_dnf(node):
    """A leaf -> ``[[norm(code)]]`` iff isCourse and itemType is Prerequisite and
    a code is present; otherwise ``None`` (no prereq contribution)."""
    item = node["item"]
    if not item.get("isCourse"):
        return None
    if _itemtype_key(item.get("itemType")) not in _PREREQ_ITEM_TYPES:
        return None
    code = item.get("code")
    if not code or not str(code).strip():
        return None
    return [[normalize_course_code(code)]]


def _merge_and(child_dnfs):
    """Cartesian-merge AND children: each result branch = union of one branch
    chosen from each child, deduping literals within a branch (first-seen order).
    Drops ``None``/empty children first. Returns ``None`` when nothing survives.
    """
    surviving = [d for d in child_dnfs if d]
    if not surviving:
        return None
    result = [[]]
    for dnf in surviving:
        merged = []
        for prefix in result:
            for branch in dnf:
                combined = list(prefix)
                for lit in branch:
                    if lit not in combined:
                        combined.append(lit)
                merged.append(combined)
        result = merged
    return result


def _merge_or(child_dnfs):
    """Concatenate OR children's surviving branches. Returns ``None`` when none
    survive."""
    out = []
    for dnf in child_dnfs:
        if dnf:
            out.extend(dnf)
    return out or None


def requisites_to_dnf(node):
    """Convert a recursive eLumen ``requisites`` tree to a prereq-only DNF.

    PURE. Returns a list of AND-branches (OUTER OR / INNER AND), i.e.
    ``list[list[str]]``; an empty list ``[]`` means "no prerequisite".

    Rules:
      - A node with an "item" is ALWAYS a leaf (regardless of its "type").
        leaf -> ``[[norm(code)]]`` when isCourse and itemType is Prerequisite and
        a code is present; otherwise it contributes nothing (None).
      - Internal node: compute children DNFs, drop None/empty children, then
          OR  -> concatenate surviving branches;
          AND (and SINGLE-with-multiple-children and any UNKNOWN type) ->
              cartesian-merge surviving children with per-branch dedup.
      - Top-level None -> ``[]``.
    """
    dnf = _node_dnf(node)
    return dnf if dnf else []


def _node_dnf(node):
    if not isinstance(node, dict):
        return None
    if _is_leaf(node):
        return _leaf_dnf(node)
    children = node.get("blockList") or []
    child_dnfs = [_node_dnf(child) for child in children]
    node_type = str(node.get("type") or "").strip().upper()
    if node_type == "OR":
        return _merge_or(child_dnfs)
    # AND, SINGLE-with-multiple-children, and any unknown type are treated as AND.
    return _merge_and(child_dnfs)


# ---- raw-text provenance (PURE) -------------------------------------------
def _requisites_to_text(node, depth=0):
    """Short human-readable prereq text for provenance (the record ``raw``).

    Lists every leaf with its itemType tag (Prerequisite / Co-Requisite /
    Advisory) so the dropped non-prereq leaves stay visible in provenance, joined
    by the node's OR/AND. Not parsed by anything; for humans only.
    """
    if not isinstance(node, dict):
        return ""
    if _is_leaf(node):
        item = node["item"]
        code = normalize_course_code(item.get("code", "")) or "?"
        itype = str(item.get("itemType") or "").strip() or "Requisite"
        return f"{code} [{itype}]"
    parts = [
        t for t in (_requisites_to_text(c, depth + 1) for c in node.get("blockList") or [])
        if t
    ]
    if not parts:
        return ""
    joiner = " OR " if str(node.get("type") or "").strip().upper() == "OR" else " AND "
    text = joiner.join(parts)
    return f"({text})" if depth > 0 and len(parts) > 1 else text


# ---- wrapper -> record -----------------------------------------------------
def _course_identity(wrapper):
    """Course identity from the wrapper ``code`` (normalized). Falls back to
    subject+number ONLY when ``code`` is missing/empty."""
    code = wrapper.get("code")
    if code and str(code).strip():
        return normalize_course_code(code)
    subject = str(wrapper.get("subject") or "").strip()
    number = str(wrapper.get("number") or "").strip()
    if subject or number:
        return normalize_course_code(f"{subject}{number}")
    return None


def course_record(wrapper):
    """One course wrapper -> a record for ``sources.elumen.build_prereq_map``.

    Returns ``{"course_id", "raw", "dnf"}`` or ``None`` when the wrapper has no
    usable identity. ``fullCourseInfo`` is a JSON STRING (``json.loads``); a
    malformed string raises ``SourceDataError`` naming the source + course (drift
    is loud, never silently dropped). A wrapper missing ``fullCourseInfo`` /
    ``requisites`` is a valid "no prerequisite" record (dnf == []).
    """
    if not isinstance(wrapper, dict):
        raise SourceDataError(
            f"{SOURCE}: expected a course wrapper dict, got "
            f"{type(wrapper).__name__}. The eLumen response shape may have changed."
        )
    course_id = _course_identity(wrapper)
    if course_id is None:
        return None

    info_raw = wrapper.get("fullCourseInfo")
    if info_raw is None or (isinstance(info_raw, str) and not info_raw.strip()):
        # No course info at all -> treat as no prerequisite (valid, not an error).
        return {"course_id": course_id, "raw": "", "dnf": []}

    if isinstance(info_raw, str):
        try:
            info = json.loads(info_raw)
        except (ValueError, TypeError) as exc:
            raise SourceDataError(
                f"{SOURCE}: course {course_id!r} has a malformed fullCourseInfo "
                f"JSON string ({exc}). The eLumen response shape may have changed."
            ) from exc
    elif isinstance(info_raw, dict):
        # Defensive: some captures may already hold the parsed object.
        info = info_raw
    else:
        raise SourceDataError(
            f"{SOURCE}: course {course_id!r} fullCourseInfo is "
            f"{type(info_raw).__name__}, expected a JSON string."
        )

    requisites = info.get("requisites") if isinstance(info, dict) else None
    dnf = requisites_to_dnf(requisites) if requisites else []
    raw = _requisites_to_text(requisites) if requisites else ""
    return {"course_id": course_id, "raw": raw, "dnf": dnf}


# ---- networked fetch (thin shell around get_json) -------------------------
def fetch_courses(campus, query, *, client=None, page_size=MAX_PAGE_SIZE,
                  max_pages=DEFAULT_MAX_PAGES):
    """Fetch raw course wrappers for one query, following pagination.

    Hits the verified eLumen endpoint via ``sources.http.get_json`` (browser UA,
    SourceError on transport / status / non-JSON). Stops paging when a page
    returns fewer than ``page_size`` courses (or ``_embedded``/``courses`` is
    absent/empty), and never exceeds ``max_pages`` (politeness bound).

    Raises ``SourceDataError`` (naming the source) if a page is not a dict or its
    ``_embedded`` is present but the wrong type — so schema drift surfaces by name.
    """
    page_size = min(int(page_size), MAX_PAGE_SIZE)
    tenant = tenant_for(campus)
    wrappers = []
    for page in range(1, int(max_pages) + 1):
        params = {
            "status": "approved",
            "tenant": tenant,
            "query": query,
            "pageSize": page_size,
            "page": page,
        }
        data = get_json(
            API_BASE, params=params, client=client,
            source=f"{SOURCE} courses ({campus} q={query!r} p{page})",
        )
        if not isinstance(data, dict):
            raise SourceDataError(
                f"{SOURCE} courses ({campus} q={query!r}): response is "
                f"{type(data).__name__}, expected a JSON object. "
                "The eLumen response shape may have changed."
            )
        embedded = data.get("_embedded")
        if embedded is None:
            # No embedded block at all -> no (more) courses; stop.
            break
        if not isinstance(embedded, dict):
            raise SourceDataError(
                f"{SOURCE} courses ({campus} q={query!r}): '_embedded' is "
                f"{type(embedded).__name__}, expected an object. "
                "The eLumen response shape may have changed."
            )
        page_courses = embedded.get("courses")
        if not page_courses:
            break
        if not isinstance(page_courses, list):
            raise SourceDataError(
                f"{SOURCE} courses ({campus} q={query!r}): "
                f"'_embedded.courses' is {type(page_courses).__name__}, "
                "expected a list. The eLumen response shape may have changed."
            )
        wrappers.extend(page_courses)
        if len(page_courses) < page_size:
            break
    return wrappers


def fetch_prereq_records(campus, queries, *, client=None,
                         page_size=MAX_PAGE_SIZE, max_pages=DEFAULT_MAX_PAGES):
    """Fetch + parse records across many queries (e.g. subject codes).

    Returns ``(records, fetched_course_ids)``: ``records`` is the de-duplicated
    list of ``{course_id, raw, dnf}`` (first wrapper wins per course_id),
    ``fetched_course_ids`` is the set of every course_id eLumen returned. A
    string ``queries`` is treated as one query.
    """
    if isinstance(queries, str):
        queries = [queries]
    records = []
    seen = set()
    for query in queries:
        for wrapper in fetch_courses(campus, query, client=client,
                                     page_size=page_size, max_pages=max_pages):
            record = course_record(wrapper)
            if record is None:
                continue
            cid = record["course_id"]
            if cid in seen:
                continue
            seen.add(cid)
            records.append(record)
    return records, seen


# ---- coverage reporting (nothing silent) ----------------------------------
def compute_coverage(records, known_course_ids=None, *, requested_course_ids=None):
    """Summarize coverage so nothing is silently dropped.

    Returns a dict with at least:
      - ``courses_fetched``: number of records produced;
      - ``courses_with_prereqs``: records whose dnf is non-empty;
      - ``unmatched_prereq_targets``: normalized prereq literals NOT present
        among the fetched course-ids (and not in ``known_course_ids`` — the
        program + section course-id set, if provided). These are prereqs whose
        target course we have no catalog row for: an advising gap to surface.
      - ``requested_courses_without_record``: requested course-ids eLumen
        returned no record for (only when ``requested_course_ids`` is provided).
    """
    fetched_ids = {r["course_id"] for r in records}
    known = {_norm(c) for c in (known_course_ids or set())}
    known |= fetched_ids

    prereq_targets = set()
    for r in records:
        for branch in r.get("dnf") or []:
            for lit in branch:
                prereq_targets.add(_norm(lit))
    unmatched = sorted(prereq_targets - known)

    coverage = {
        "courses_fetched": len(records),
        "courses_with_prereqs": sum(1 for r in records if r.get("dnf")),
        "unmatched_prereq_targets": unmatched,
    }
    if requested_course_ids is not None:
        requested = {_norm(c) for c in requested_course_ids}
        coverage["requested_courses_without_record"] = sorted(requested - fetched_ids)
    return coverage


# ---- top-level convenience -------------------------------------------------
def prereq_map_for_campus(campus, queries, *, client=None,
                          known_course_ids=None, requested_course_ids=None,
                          page_size=MAX_PAGE_SIZE, max_pages=DEFAULT_MAX_PAGES):
    """Fetch eLumen, build the CNF prereq map, and report coverage.

    Returns ``(prereq_map, results, coverage)``:
      - ``prereq_map``: ``dict[course_id -> catalog CNF string]`` from the
        EXISTING ``sources.elumen.build_prereq_map`` (DNF->CNF unchanged);
      - ``results``: ``dict[course_id -> ConversionResult]`` (the exact/fallback
        flags) from the same call;
      - ``coverage``: the ``compute_coverage`` summary.

    Imports ``sources.elumen`` lazily so the pure parser/normalizer above stay
    importable without pulling its (inert) dependency chain on the hot path.
    """
    from . import elumen as _elumen  # noqa: WPS433 (lazy, keeps hot path pure)

    records, _fetched = fetch_prereq_records(
        campus, queries, client=client, page_size=page_size, max_pages=max_pages
    )
    prereqs, results = _elumen.build_prereq_map(records)
    coverage = compute_coverage(
        records, known_course_ids, requested_course_ids=requested_course_ids
    )
    return prereqs, results, coverage
