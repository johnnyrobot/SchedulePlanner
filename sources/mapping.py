"""Map live-source records into the engine's workbook schema.

Emits exactly the columns engine.py reads. By default enrollment columns are 0
(the schedule API has no counts) and prerequisites are blank (needs eLumen) —
both are expected gaps documented in the design doc, not failures.

The m7 builders accept OPTIONAL enrichment, additively (defaults preserve the
all-zero / all-blank behavior byte-identically):
  - build_sections_df reads per-record ``Cap Enrl``/``Tot Enrl``/``Wait Tot``
    when present (records enriched from the IR export);
  - build_catalog_df / write_workbook accept a ``prereqs`` course-id -> CNF
    string map for the structured-prereq column.
The section sheet additionally carries one OPTIONAL column beyond
engine.REQUIRED_COLUMNS["sections"] — ``Avail Status`` — holding the schedule
API's per-section availability (Open/Waitlist/Closed). engine.analyze reads it
optionally (absent in demo/IR workbooks), so it is additive: it turns the live
waitlist STATUS into an under_supply signal without needing the IR counts.
"""
from __future__ import annotations

import math
import re

import pandas as pd

from .http import SourceDataError

# Human label so a malformed-record guard names where the bad data came from
# (the live source -> mapping step), mirroring sources/http.py's style.
SOURCE = "live-source mapping"

SECTION_COLUMNS = ["Term", "CLASS", "Class Status", "Cap Enrl", "Tot Enrl",
                   "Wait Tot", "Avail Status"]
CATALOG_COLUMNS = ["Course ID", "Units", "Prerequisites (structured)"]
PROGRAM_COLUMNS = ["Program Code", "Program Title", "Course ID", "Recommended Semester"]
GE_REQUIREMENT_COLUMNS = ["Program Code", "Pattern", "Area", "Area Title",
                          "Required Count", "Resolution", "Candidate Course IDs",
                          "Recommended Course", "Units"]


def _norm(code):
    return re.sub(r"\s+", " ", str(code).strip().upper())


def _to_units(value, default=3.0):
    """Coerce '3.00', '3-4', 5.0, '' -> float (solver does int(units))."""
    try:
        result = float(str(value).split("-")[0])
    except (ValueError, TypeError):
        return default
    return default if math.isnan(result) else result


def build_sections_df(section_records):
    rows = []
    for i, r in enumerate(section_records):
        if "term" not in r or "course" not in r:
            # A section record missing a required key is schema drift from the
            # schedule source: name the context instead of a bare KeyError.
            raise SourceDataError(
                f"{SOURCE}: section record #{i} missing required key "
                f"('term'/'course'); got keys {sorted(r)[:8]}. "
                "The schedule source shape may have changed."
            )
        try:
            term = int(r["term"])
        except (ValueError, TypeError) as exc:
            raise SourceDataError(
                f"{SOURCE}: section record #{i} has non-numeric term "
                f"{r['term']!r}; expected an integer term code."
            ) from exc
        rows.append({
            "Term": term,
            "CLASS": _norm(r["course"]),
            # The schedule API only returns offered sections (cancelled ones are
            # absent), so every fetched section is an active OFFERING (lifecycle).
            "Class Status": "Active",
            # Additive enrollment seam (m7): records enriched from the IR export
            # carry Cap/Tot/Wait Enrl; absent them (the schedule API alone) the
            # defaults stay 0, byte-identical to the pre-m7 behavior.
            "Cap Enrl": r.get("Cap Enrl", 0),
            "Tot Enrl": r.get("Tot Enrl", 0),
            "Wait Tot": r.get("Wait Tot", 0),
            # The schedule API's separate AVAILABILITY status (Open/Waitlist/
            # Closed). Carried through so engine.analyze can read a live waitlist
            # signal (Waitlist => section at capacity) without the IR counts.
            "Avail Status": str(r.get("status", "") or ""),
        })
    return pd.DataFrame(rows, columns=SECTION_COLUMNS)


def build_catalog_df(section_records, program, prereqs=None):
    units = {}
    for i, r in enumerate(section_records):
        if "course" not in r:
            raise SourceDataError(
                f"{SOURCE}: section record #{i} missing 'course'; got keys "
                f"{sorted(r)[:8]}. The schedule source shape may have changed."
            )
        units.setdefault(_norm(r["course"]), _to_units(r.get("units")))
    for i, c in enumerate((program or {}).get("courses", [])):
        if "course_id" not in c:
            raise SourceDataError(
                f"{SOURCE}: program course #{i} missing 'course_id'; got keys "
                f"{sorted(c)[:8]}. The Program Mapper shape may have changed."
            )
        units.setdefault(_norm(c["course_id"]), _to_units(c.get("units")))
    # Additive prereq seam (m7): an optional course-id -> CNF-string map
    # populates the structured-prereq column. prereqs=None (default) keeps the
    # column all-blank, byte-identical to the pre-m7 behavior.
    prereqs = prereqs or {}
    rows = [{"Course ID": cid, "Units": u,
             "Prerequisites (structured)": prereqs.get(cid, "")}
            for cid, u in sorted(units.items())]
    return pd.DataFrame(rows, columns=CATALOG_COLUMNS)


def build_programs_df(program):
    program = program or {}
    missing = [k for k in ("code", "title") if k not in program]
    if missing:
        raise SourceDataError(
            f"{SOURCE}: program missing required key(s) {missing}; got keys "
            f"{sorted(program)[:8]}. The Program Mapper shape may have changed."
        )
    rows = []
    for i, c in enumerate(program.get("courses", [])):
        if "course_id" not in c:
            raise SourceDataError(
                f"{SOURCE}: program course #{i} missing 'course_id'; got keys "
                f"{sorted(c)[:8]}. The Program Mapper shape may have changed."
            )
        rows.append({
            "Program Code": program["code"],
            "Program Title": program["title"],
            "Course ID": _norm(c["course_id"]),
            "Recommended Semester": c.get("recommended_semester"),
        })
    return pd.DataFrame(rows, columns=PROGRAM_COLUMNS)


def build_ge_requirements_df(program, pattern, ge_rows):
    """One row per GE requirement. ``ge_rows`` are the resolver's output dicts. Candidate IDs are semicolon-joined to survive multi-word subjects (e.g. 'PHYS SC 1'); split on ';' to recover the list."""
    code = (program or {}).get("code", "")
    rows = []
    for r in ge_rows or []:
        if "area" not in r:
            raise SourceDataError(
                f"{SOURCE}: ge_row missing required key 'area'; got keys {sorted(r)[:8]}.")
        rows.append({
            "Program Code": code,
            "Pattern": pattern or "",
            "Area": r["area"],
            "Area Title": r.get("area_title", ""),
            "Required Count": int(r.get("required_count", 1)),
            "Resolution": r.get("resolution", "reserve"),
            "Candidate Course IDs": ";".join(_norm(c) for c in r.get("candidates", [])),
            "Recommended Course": _norm(r["recommended"]) if r.get("recommended") else "",
            "Units": _to_units(r.get("units"), default=3.0),
        })
    return pd.DataFrame(rows, columns=GE_REQUIREMENT_COLUMNS)


def reconcile_courses(section_records, program):
    section_codes = {_norm(r["course"]) for r in section_records}
    program_codes = {_norm(c["course_id"]) for c in (program or {}).get("courses", [])}
    matched = sorted(program_codes & section_codes)
    unmatched = sorted(program_codes - section_codes)
    return matched, unmatched


def write_workbook(section_records, program, path, *, prereqs=None,
                   pattern=None, ge_rows=None):
    # `prereqs` is the optional course-id -> CNF-string map threaded into the
    # catalog sheet (m7). It is keyword-only and defaults to None so existing
    # positional callers stay byte-identical. Ownership note: write_workbook is
    # modified HERE (mapping.py owner); the build_live_workbook pipeline only
    # PASSES this kwarg, it does not touch mapping.py.
    # `pattern` and `ge_rows` are the optional GE requirements enrichment (Task 5).
    # Absent both -> no ge_requirements sheet -> byte-identical 3-sheet workbook.
    sections = build_sections_df(section_records)
    catalog = build_catalog_df(section_records, program, prereqs)
    programs = build_programs_df(program)
    with pd.ExcelWriter(path, engine="openpyxl") as xl:
        sections.to_excel(xl, sheet_name="sections", index=False)
        catalog.to_excel(xl, sheet_name="catalog", index=False)
        programs.to_excel(xl, sheet_name="programs", index=False)
        if ge_rows:
            build_ge_requirements_df(program, pattern, ge_rows).to_excel(
                xl, sheet_name="ge_requirements", index=False)
    return path
