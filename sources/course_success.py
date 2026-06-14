"""course_success.py — E9: offline CCCCO Data Mart course success/retention adapter.

Reads an OFFLINE CCCCO Data Mart "Credit Course Retention/Success" export — public,
AGGREGATE, no student rows ([the report page](https://datamart.cccco.edu/outcomes/
course_ret_success.aspx)) — into a ``course/discipline -> {success_rate,
retention_rate, enrollment}`` map, so a detector can escalate a course that is BOTH
supply-constrained AND historically lower-success.

GRANULARITY + HONESTY (the #17 no-student-data ceiling): this is a MEASURED
aggregate COURSE outcome (retention / success), at the granularity of the supplied
export. The Data Mart aggregates by college x term x TOP discipline x distance-ed,
so the join key is whatever identifying column the export carries (a course id if
course-level, else a TOP/subject/discipline code) — recorded in the returned
``granularity`` so nothing overclaims course specificity. It is explicitly NOT a
program-completion label, NOT a student-level record, and NOT this schedule's
outcome (it is historical). Inert with remedy when no file is supplied (the live
LACCD APIs expose no success data). FIXTURE-VALIDATED only: the real-export shape
is assumed + documented + tolerantly read, NOT validated against a real download.

Pure file read (CSV / xlsx); no network. SourceDataError (named) on schema drift.
"""
from __future__ import annotations

import os

import pandas as pd

from .http import SourceDataError
from .mapping import _norm

SOURCE = "CCCCO Data Mart course-success adapter (OFFLINE, FIXTURE-VALIDATED)"

# The identifying (join-key) column, most course-specific first. The first one
# present in the export is used; its name is reported as the join ``granularity``.
_KEY_COLUMNS = ("Course", "Course ID", "Course Id", "TOP Code", "TOP", "Subject",
                "Discipline")
# Rate / count column aliases -> canonical names. Real Data Mart headers vary
# ("Success Rate" / "Success %" / "Success Rate (%)"); all collapse here.
_SUCCESS_ALIASES = ("Success Rate", "Success %", "Success Rate (%)", "Success")
_RETENTION_ALIASES = ("Retention Rate", "Retention %", "Retention Rate (%)",
                      "Retention")
_ENROLL_ALIASES = ("Enrollment", "Enrollment Count", "Enrolled", "Count")
# Demographic-subgroup column for the DISAGGREGATED (E13) reader.
_SUBGROUP_COLUMNS = ("Subgroup", "Race/Ethnicity", "Ethnicity", "Demographic",
                     "Gender", "Group")
# The published Cal-PASS small-cell suppression rule: counts BELOW this are
# suppressed (https://www.calpassplus.org/Launchboard/Suppression). NOTE the
# earlier-circulated "size 6 or greater" rule was wrong/inverted.
DEFAULT_SUPPRESSION_MIN = 10
# Cells the export itself already suppressed: an asterisk / "N/A" / "S" / a
# "<N" range / blank are NOT a numeric rate — treat as suppressed, never 0.
_SUPPRESSION_MARKERS = ("*", "n/a", "na", "s", "redacted", "suppressed")


def _read_frame(path):
    """Read the export into an all-str DataFrame (CSV or the first xlsx sheet that
    carries a usable key + a success column)."""
    if os.path.splitext(path)[1].lower() == ".csv":
        return pd.read_csv(path, dtype=str)
    xl = pd.ExcelFile(path)
    for name in xl.sheet_names:
        cols = list(xl.parse(name, nrows=0).columns)
        if _pick(cols, _KEY_COLUMNS) and _pick(cols, _SUCCESS_ALIASES):
            return xl.parse(name, dtype=str)
    return xl.parse(xl.sheet_names[0], dtype=str)


def _pick(cols, candidates):
    """First candidate column present in ``cols`` (case-insensitive), else None."""
    lower = {str(c).strip().lower(): c for c in cols}
    for cand in candidates:
        if cand.lower() in lower:
            return lower[cand.lower()]
    return None


def _rate(value):
    """Parse a success/retention rate cell to a float in [0, 1], or None if blank.

    Accepts ``"85%"`` / ``"85"`` / ``"0.85"`` / ``85`` — a value > 1 is read as a
    percentage and divided by 100 (so a bare ``"1"`` reads as 100%, and ``"0.5"``
    as 50%). Raises ``SourceDataError`` on a non-numeric non-blank cell (loud drift,
    never a silent 0)."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    s = str(value).strip().rstrip("%").strip()
    if not s:
        return None
    try:
        v = float(s)
    except ValueError:
        raise SourceDataError(
            f"{SOURCE}: non-numeric rate cell {value!r}; expected a percent (85) or "
            "a fraction (0.85).") from None
    return v / 100.0 if v > 1 else v


def _int(value):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    s = str(value).strip().replace(",", "")
    if not s:
        return None
    try:
        n = int(float(s))
    except ValueError:
        return None
    return n if n >= 0 else None   # a negative enrollment is malformed -> None


def _is_suppression_marker(value):
    """True when a success-rate cell is an EXPORT suppression marker (not a number):
    blank, ``*``, ``N/A``, ``S``, or a ``<N`` range. Such a cell is suppressed
    upstream — never read as 0."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return True
    s = str(value).strip().lower()
    if s == "" or s in _SUPPRESSION_MARKERS:
        return True
    return "<" in s   # e.g. "<11", "< 10"


def _read_frame_disagg(path):
    """Read the export (CSV or the first xlsx sheet carrying key + subgroup + success)."""
    if os.path.splitext(path)[1].lower() == ".csv":
        return pd.read_csv(path, dtype=str)
    xl = pd.ExcelFile(path)
    for name in xl.sheet_names:
        cols = list(xl.parse(name, nrows=0).columns)
        if (_pick(cols, _KEY_COLUMNS) and _pick(cols, _SUBGROUP_COLUMNS)
                and _pick(cols, _SUCCESS_ALIASES)):
            return xl.parse(name, dtype=str)
    return xl.parse(xl.sheet_names[0], dtype=str)


def load_course_success_disaggregated(path, *, suppression_min=DEFAULT_SUPPRESSION_MIN):
    """Read a DISAGGREGATED (by demographic subgroup) success export (E13).

    Long format: one row per course/discipline x subgroup. Returns
    ``(disagg_map, granularity, suppression_min)`` where ``disagg_map`` is
    ``dict[_norm(key) -> dict[subgroup -> {"success_rate": float|None, "count":
    int|None, "suppressed": bool}]]``.

    ENFORCES small-cell suppression for honesty AND privacy: a subgroup cell is
    SUPPRESSED (``success_rate``/``count`` set to None) when (a) the export already
    suppressed it (an ``*`` / ``N/A`` / ``<N`` / blank marker), OR (b) its count is
    BELOW ``suppression_min`` (default 10, the published Cal-PASS rule). A count
    column is therefore REQUIRED — without it the <10 rule cannot be enforced, so
    the read is refused rather than risk leaking a small cell. Raises
    ``SourceDataError`` (named) on any missing required column / unreadable file.
    """
    try:
        df = _read_frame_disagg(path)
    except (OSError, ValueError) as exc:
        raise SourceDataError(f"{SOURCE}: could not read {path} ({exc}).") from exc
    cols = list(df.columns)
    key_col = _pick(cols, _KEY_COLUMNS)
    sub_col = _pick(cols, _SUBGROUP_COLUMNS)
    success_col = _pick(cols, _SUCCESS_ALIASES)
    count_col = _pick(cols, _ENROLL_ALIASES)
    if not key_col or not success_col:
        raise SourceDataError(
            f"{SOURCE}: {path} is missing a course/discipline key and/or success "
            f"column; got {cols[:12]}.")
    if not sub_col:
        raise SourceDataError(
            f"{SOURCE}: {path} has no subgroup column (one of {list(_SUBGROUP_COLUMNS)}); "
            "a disaggregated equity export needs one.")
    if not count_col:
        raise SourceDataError(
            f"{SOURCE}: {path} has no enrollment/count column (one of "
            f"{list(_ENROLL_ALIASES)}); it is REQUIRED to enforce the <"
            f"{suppression_min} small-cell suppression (privacy + honesty).")

    disagg = {}
    for _, row in df.iterrows():
        raw_key = row.get(key_col)
        raw_sub = row.get(sub_col)
        if raw_key is None or (isinstance(raw_key, float) and pd.isna(raw_key)):
            continue
        key = _norm(raw_key)
        sub = "" if (raw_sub is None or (isinstance(raw_sub, float) and pd.isna(raw_sub))) \
            else str(raw_sub).strip()
        if not key or not sub:
            continue
        count = _int(row.get(count_col))
        suppressed = (_is_suppression_marker(row.get(success_col))
                      or count is None or count < suppression_min)
        disagg.setdefault(key, {})[sub] = {
            "success_rate": None if suppressed else _rate(row.get(success_col)),
            # A suppressed cell leaks NOTHING (not even the small count).
            "count": None if suppressed else count,
            "suppressed": suppressed,
        }
    if not disagg:
        raise SourceDataError(
            f"{SOURCE}: {path} parsed to zero usable rows. The assumed disaggregated "
            "export shape may have drifted.")
    return disagg, key_col, suppression_min


def load_course_success(path):
    """Read a CCCCO Data Mart success/retention export into a join map.

    Returns ``(success_map, granularity)`` where ``success_map`` is
    ``dict[_norm(key) -> {"success_rate": float|None, "retention_rate": float|None,
    "enrollment": int|None}]`` and ``granularity`` is the export's join-key column
    name (e.g. "Course" or "TOP Code") — so a consumer can disclose whether the
    join is course-specific or discipline-level. Raises ``SourceDataError`` (named)
    when the file is unreadable or carries no usable key + success column. Last row
    wins on a duplicate key (the export is already aggregated; duplicates are rare).
    """
    try:
        df = _read_frame(path)
    except (OSError, ValueError) as exc:
        raise SourceDataError(
            f"{SOURCE}: could not read {path} ({exc}).") from exc
    cols = list(df.columns)
    key_col = _pick(cols, _KEY_COLUMNS)
    success_col = _pick(cols, _SUCCESS_ALIASES)
    if not key_col or not success_col:
        raise SourceDataError(
            f"{SOURCE}: {path} is missing a course/discipline key column "
            f"(one of {list(_KEY_COLUMNS)}) and/or a success-rate column "
            f"(one of {list(_SUCCESS_ALIASES)}); got {cols[:12]}. Is this a CCCCO "
            "Data Mart Credit Course Retention/Success export?")
    ret_col = _pick(cols, _RETENTION_ALIASES)
    enr_col = _pick(cols, _ENROLL_ALIASES)

    success_map = {}
    for _, row in df.iterrows():
        raw_key = row.get(key_col)
        # Guard NaN/None BEFORE _norm: str(NaN) would become the literal "NAN".
        if raw_key is None or (isinstance(raw_key, float) and pd.isna(raw_key)):
            continue
        key = _norm(raw_key)
        if not key:
            continue
        success_map[key] = {
            "success_rate": _rate(row.get(success_col)),
            "retention_rate": _rate(row.get(ret_col)) if ret_col else None,
            "enrollment": _int(row.get(enr_col)) if enr_col else None,
        }
    if not success_map:
        raise SourceDataError(
            f"{SOURCE}: {path} parsed to zero usable rows (every {key_col!r} cell "
            "was blank). The assumed export shape may have drifted.")
    return success_map, key_col
