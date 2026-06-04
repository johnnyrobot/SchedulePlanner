r"""Tolerant LACCD program-course-lists reader (multi-program requirements map).

The institutional **Program Course Lists** export lists, for every degree/cert
plan, the courses on its required path and its choice/elective lists — one row
per ``(plan, course, bucket)``. edgesched's live path fetches ONE program per run
(``program_mapper`` resolves a single program), so the **cross-program demand**
signal — *how many programs require a given course* — lives only in this file.
That is the headline dimension of the F2 bottleneck leaderboard.

Columns (real export, 8 Oct 2024)::

    Plan Code | Program | Type of  List | Class Selection Criteria | Course | Special Notes or Limitations

Note the DOUBLE space in ``Type of  List`` (kept verbatim in :data:`TYPE_COL`).
A row is a **hard requirement** when its ``Type of  List`` *or* ``Class Selection
Criteria`` contains ``"required"`` (case-insensitive) — this also catches variants
like ``"Upper Division Coursework (Required)"``. List A/B/C, ``"Select N"``,
``"Minimum of 18 units"``, electives, etc. are choice/elective buckets and count
toward the broader ``listed`` signal only.

Returns a :class:`ProgramDemand`:

  * ``required[course] -> set(plan_code)`` — plans where the course is hard-Required.
  * ``listed[course]   -> set(plan_code)`` — plans where it appears in ANY bucket.
  * ``titles[plan_code] -> program_title``.

Course keys are normalized via :func:`sources.mapping._norm` (which uppercases and
collapses whitespace but does **not** collapse leading zeros — ``"BIOLOGY 003"`` ≠
``"BIOLOGY 3"``), so any downstream join must report unmatched courses honestly
rather than silently undercount.

Reuses the tolerant frame helpers from :mod:`sources.enrollment_ir` (CSV / xlsx
sheet auto-pick, blank-tolerant cells). No network; a program-course list is plan
structure, not people — no PII.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .enrollment_ir import _empty, _read_frame
from .http import SourceDataError
from .mapping import _norm

SOURCE = "program course lists"

# The export's headers. NOTE the double space in the "Type of  List" header — it
# is literal in the real file, so the lookup must match it exactly.
PLAN_COL = "Plan Code"
COURSE_COL = "Course"
PROGRAM_COL = "Program"
TYPE_COL = "Type of  List"
CRITERIA_COL = "Class Selection Criteria"

# Columns we cannot work without (the join needs a plan id and a course id).
REQUIRED_COLUMNS = [PLAN_COL, COURSE_COL]


@dataclass
class ProgramDemand:
    """Cross-program requirements demand parsed from a Program Course Lists file.

    ``required`` / ``listed`` map a normalized course id to the SET of plan codes
    that (hard-)require / list it. ``titles`` maps a plan code to its program name.
    """

    required: dict = field(default_factory=dict)   # course -> set(plan_code)
    listed: dict = field(default_factory=dict)     # course -> set(plan_code)
    titles: dict = field(default_factory=dict)     # plan_code -> program_title

    @property
    def n_plans(self):
        return len(self.titles)

    @property
    def n_courses(self):
        return len(self.listed)


def _is_required(type_of_list, criteria):
    """A row is a hard requirement when "required" appears (case-insensitive) in
    its ``Type of  List`` or ``Class Selection Criteria`` cell."""
    blob = f"{type_of_list or ''} {criteria or ''}".lower()
    return "required" in blob


def load_program_lists(path, *, sheet=None):
    """Read a Program Course Lists export into a :class:`ProgramDemand`.

    Raises :class:`~sources.http.SourceDataError` (naming the file) when the
    ``Plan Code`` or ``Course`` column is absent. Blank plan/course rows (trailing
    rows, section headers) are skipped. Pure file read — no network, no PII."""
    df = _read_frame(path, sheet)
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise SourceDataError(
            f"{SOURCE}: program lists {path!r} is missing required column(s) "
            f"{missing}; got columns {list(df.columns)[:14]}. Is this a LACCD "
            "Program Course Lists export?")
    has_type = TYPE_COL in df.columns
    has_criteria = CRITERIA_COL in df.columns
    has_program = PROGRAM_COL in df.columns

    demand = ProgramDemand()
    for rd in df.to_dict("records"):
        plan = "" if _empty(rd.get(PLAN_COL)) else str(rd.get(PLAN_COL)).strip()
        course = "" if _empty(rd.get(COURSE_COL)) else _norm(rd.get(COURSE_COL))
        if not plan or not course:
            continue
        if has_program and plan not in demand.titles:
            title = str(rd.get(PROGRAM_COL) or "").strip()
            if title:
                demand.titles[plan] = title
        demand.titles.setdefault(plan, plan)  # known even if its title is blank
        demand.listed.setdefault(course, set()).add(plan)
        if _is_required(rd.get(TYPE_COL) if has_type else None,
                        rd.get(CRITERIA_COL) if has_criteria else None):
            demand.required.setdefault(course, set()).add(plan)
    return demand


if __name__ == "__main__":  # pragma: no cover - manual operator check
    import json
    import sys

    if len(sys.argv) < 2:
        print("usage: python -m sources.program_lists "
              "<program_lists.(xlsx|csv)> [sheet]")
        raise SystemExit(2)
    _d = load_program_lists(
        sys.argv[1], sheet=(sys.argv[2] if len(sys.argv) > 2 else None))
    _top = sorted(_d.required.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:10]
    print(json.dumps({
        "plans": _d.n_plans,
        "courses": _d.n_courses,
        "required_courses": len(_d.required),
        "top_required_by_program_count": [
            {"course": c, "n_programs": len(p)} for c, p in _top],
    }, indent=2))
