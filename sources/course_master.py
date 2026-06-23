r"""Tolerant LACCD course-master reader (LAC_SRC_COURSE_MASTER -> units / active set).

Optional enrichment for the offline schedule-import path: the schedule exports carry
no ``Units`` column, so a converted workbook defaults every course to 3 units (fine
for the audit detectors, which ignore units, but coarse for the solver's unit caps).
This reads the institutional course master to supply real units, and exposes the set
of ACTIVE course ids (the "current catalog" universe — seeds a future
dead-requirement detector: a required course that is no longer active).

Course ids are keyed as ``f"{Subject} {Catalog}"`` (normalized like the schedule
records), so the join lines up with imported sections. Reuses the tolerant frame +
cell helpers from :mod:`sources.enrollment_ir`. No network, no PII.
"""
from __future__ import annotations

from .enrollment_ir import _empty, _read_frame
from .http import SourceDataError
from .textnorm import _norm, _to_units

SOURCE = "course master"


def _course_key(rd):
    subj = str(rd.get("Subject") or "").strip()
    cat = str(rd.get("Catalog") or "").strip()
    if subj and cat:
        return _norm(f"{subj} {cat}")
    cid = str(rd.get("Course ID") or "").strip()
    return _norm(cid) if cid else ""


def load_course_master(path, *, sheet=None):
    """Return ``(units, active)`` where ``units`` maps normalized course id -> float
    and ``active`` is the set of course ids whose ``Status`` is active.

    Raises ``SourceDataError`` (naming the file) when the course / units columns are
    absent."""
    df = _read_frame(path, sheet)
    has_course = (("Subject" in df.columns and "Catalog" in df.columns)
                  or "Course ID" in df.columns)
    if not has_course or "Units" not in df.columns:
        raise SourceDataError(
            f"{SOURCE}: course master {path!r} needs Subject+Catalog (or Course ID) "
            f"and a Units column; got {list(df.columns)[:14]}.")
    has_status = "Status" in df.columns
    units, active = {}, set()
    for rd in df.to_dict("records"):
        key = _course_key(rd)
        if not key:
            continue
        units.setdefault(key, _to_units(rd.get("Units")))
        if has_status and str(rd.get("Status") or "").strip().upper().startswith("A"):
            active.add(key)
        elif not has_status:
            active.add(key)
    return units, active


def load_units(path, *, sheet=None):
    """Convenience: just the ``{course id -> units}`` map."""
    return load_course_master(path, sheet=sheet)[0]


if __name__ == "__main__":  # pragma: no cover - manual operator check
    import json
    import sys

    if len(sys.argv) < 2:
        print("usage: python -m sources.course_master <master.(xlsx|csv)> [sheet]")
        raise SystemExit(2)
    _units, _active = load_course_master(
        sys.argv[1], sheet=(sys.argv[2] if len(sys.argv) > 2 else None))
    print(json.dumps({"courses": len(_units), "active": len(_active),
                      "with_blank_check": int(any(_empty(v) for v in _units.values()))},
                     indent=2))
