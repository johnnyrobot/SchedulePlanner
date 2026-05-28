"""Map live-source records into the engine's workbook schema.

Emits exactly the columns engine.py reads. Enrollment columns are 0 (the
schedule API has no counts) and prerequisites are blank (needs eLumen) — both
are expected gaps documented in the design doc, not failures.
"""
from __future__ import annotations

import math
import re

import pandas as pd

SECTION_COLUMNS = ["Term", "CLASS", "Class Status", "Cap Enrl", "Tot Enrl", "Wait Tot"]
CATALOG_COLUMNS = ["Course ID", "Units", "Prerequisites (structured)"]
PROGRAM_COLUMNS = ["Program Code", "Program Title", "Course ID", "Recommended Semester"]


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
    rows = [{
        "Term": int(r["term"]),
        "CLASS": _norm(r["course"]),
        # The schedule API only returns offered sections (cancelled ones are
        # absent); its status field is enrollment availability (Open/Closed/
        # Waitlist), not lifecycle. So every fetched section is an active offering.
        "Class Status": "Active",
        "Cap Enrl": 0,
        "Tot Enrl": 0,
        "Wait Tot": 0,
    } for r in section_records]
    return pd.DataFrame(rows, columns=SECTION_COLUMNS)


def build_catalog_df(section_records, program):
    units = {}
    for r in section_records:
        units.setdefault(_norm(r["course"]), _to_units(r.get("units")))
    for c in (program or {}).get("courses", []):
        units.setdefault(_norm(c["course_id"]), _to_units(c.get("units")))
    rows = [{"Course ID": cid, "Units": u, "Prerequisites (structured)": ""}
            for cid, u in sorted(units.items())]
    return pd.DataFrame(rows, columns=CATALOG_COLUMNS)


def build_programs_df(program):
    rows = [{
        "Program Code": program["code"],
        "Program Title": program["title"],
        "Course ID": _norm(c["course_id"]),
        "Recommended Semester": c.get("recommended_semester"),
    } for c in (program or {}).get("courses", [])]
    return pd.DataFrame(rows, columns=PROGRAM_COLUMNS)


def reconcile_courses(section_records, program):
    section_codes = {_norm(r["course"]) for r in section_records}
    program_codes = {_norm(c["course_id"]) for c in (program or {}).get("courses", [])}
    matched = sorted(program_codes & section_codes)
    unmatched = sorted(program_codes - section_codes)
    return matched, unmatched


def write_workbook(section_records, program, path):
    sections = build_sections_df(section_records)
    catalog = build_catalog_df(section_records, program)
    programs = build_programs_df(program)
    with pd.ExcelWriter(path, engine="openpyxl") as xl:
        sections.to_excel(xl, sheet_name="sections", index=False)
        catalog.to_excel(xl, sheet_name="catalog", index=False)
        programs.to_excel(xl, sheet_name="programs", index=False)
    return path
