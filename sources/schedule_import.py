r"""Tolerant LACCD schedule-export ADAPTER (real export shapes -> engine records).

Turns a real OneDrive/PeopleSoft schedule export into the SAME section-record
dict shape ``sources/schedule.py:fetch_sections`` emits, so the whole offline
pipeline (``mapping.build_*_df`` -> ``mapping.write_workbook`` -> ``engine.run`` +
the raw-record time-block / room detectors) is reused UNCHANGED. This is the
offline counterpart of the live fetch — it lets the engine audit real multi-term
HISTORY (rotation, fill, under-supply, time + room conflicts) with no network.

Two real shapes are handled tolerantly:
  * ``FALL 2025 Schedule.xlsx`` — numeric ``Term`` (2258), ``DAYS`` day string,
    24h ``Mtg Start``/``Mtg End``, ``Facil ID`` / ``Room Descr``, ``Comb Sects ID``.
  * ``Scheduling Data Fall 2022 to Spring 2024.xlsx`` — ``"2024 Spring"`` string
    ``Term``, ``Meetings`` day string (no ``DAYS``), the same 24h times, counts.

Normalization (the parts the live API already gives us for free):
  * Term: numeric passthrough or ``"2024 Spring"`` -> ``2242`` via
    :func:`enrollment_ir.parse_term` (exact inverse of the engine's decoders).
  * Course id: ``f"{Subject} {Catalog}"`` (falls back to ``Course ID`` / ``CLASS``)
    — the same format the live fetch produces, so reconciliation keys line up.
  * Days: the exports use ``R`` for Thursday and ``TBA`` for no meeting; this maps
    ``R`` -> ``Th`` (and ``U`` -> ``Su``) so the canonical ``timeblocks.parse_days``
    parses Thursday instead of silently dropping it.
  * Times: 24h ``"09:00:00"`` -> ``"9:00 AM"`` so ``timeblocks.parse_times`` reads
    them; blank/NaN (online/TBA) -> no meeting.
  * Cancelled rows dropped; meeting-pattern grain deduped on ``(term, CRN)``;
    instructor PII (``Name`` / ``Emails`` / ``INSTRUCTOR``) is never read.
  * Cap/Tot/Wait counts are carried through WHEN the export has them (both real
    files do), so modality_mismatch / under_supply light up offline with no
    separate IR upload.

Reuses the tolerant frame + cell helpers from :mod:`sources.enrollment_ir`.
"""
from __future__ import annotations

from .enrollment import _crn
from .enrollment_ir import _empty, _is_active, _read_frame, _to_int, parse_term
from .http import SourceDataError

SOURCE = "schedule export adapter"

# Day strings live here (first non-blank wins); 2022-24 uses Meetings, FALL25 DAYS.
_DAY_COLUMNS = ["DAYS", "Meetings"]
# Human room label (the live fetch's "room"); Facil ID is separate (capacity join).
# NB: "Location" is the campus (LAMC/ONLINE), not a room — deliberately excluded so
# the room label never collapses to the campus name (the 2022-24 shape has only
# Facil ID + Location, so its room joins on Facil ID).
_ROOM_COLUMNS = ["Room Descr", "ROOM"]
_CLASS_NBR_COLUMNS = ["Class Nbr", "Class Number"]
_NO_MEETING_DAYS = {"", "TBA", "ARR", "ARRANGED", "N/A", "ONLINE", "ASYNC"}
# FF5 CAPTURE-ONLY: weeks-of-instruction + the per-section session date range.
# These survive onto the record so a FUTURE calendar/duration check can read them;
# nothing consumes them yet (no check is activated). First non-blank column wins;
# a single combined date column OR a separate start/end pair both fold to a
# "start - end" string. Missing -> "" (fail open, never invented).
_WOI_COLUMNS = ["WOI", "Weeks of Instruction", "Weeks", "Wks"]
_DATES_COLUMNS = ["Session Dates", "Dates", "Mtg Dates", "Meeting Dates",
                  "Class Dates", "Date Range"]
_START_DATE_COLUMNS = ["Class Start Date", "Start Date", "Mtg Start Date",
                       "Session Start Date", "Begin Date"]
_END_DATE_COLUMNS = ["Class End Date", "End Date", "Mtg End Date",
                     "Session End Date"]


def _first(rd, columns):
    for c in columns:
        v = rd.get(c)
        if not _empty(v):
            return str(v).strip()
    return ""


def _course_id(rd):
    subj = str(rd.get("Subject") or "").strip()
    cat = str(rd.get("Catalog") or "").strip()
    if subj and cat:
        return f"{subj} {cat}"
    return _first(rd, ["Course ID", "CLASS"])


def norm_days(value):
    """Map an export day string to canonical tokens ``timeblocks.parse_days`` reads.

    Exports use single-letter day codes with ``R`` = Thursday / ``S`` = Saturday /
    ``U`` = Sunday and ``TBA`` for no meeting; ``timeblocks`` knows ``Th`` / ``Su``.
    ``"TR"`` -> ``"TTh"``, ``"MTWR"`` -> ``"MTWTh"``. Empty / TBA -> ``""``."""
    s = str(value or "").strip().upper()
    if not s or s in _NO_MEETING_DAYS:
        return ""
    out = []
    for ch in s:
        if ch == "R":
            out.append("Th")
        elif ch == "U":
            out.append("Su")
        elif ch in ("M", "T", "W", "F", "S"):
            out.append(ch)
        # anything else (spaces, stray punctuation) is skipped
    return "".join(out)


def _fmt_clock(value):
    """24h ``"09:00:00"`` / ``"18:50:00"`` -> ``"9:00 AM"`` / ``"6:50 PM"``.

    Blank / NaN / unparseable -> ``""`` (treated downstream as no meeting)."""
    s = str(value or "").strip()
    if not s or s.lower() == "nan":
        return ""
    parts = s.split(":")
    try:
        h = int(parts[0])
        m = int(parts[1]) if len(parts) > 1 else 0
    except (ValueError, IndexError):
        return ""
    if not (0 <= h <= 23 and 0 <= m <= 59):
        return ""
    ap = "AM" if h < 12 else "PM"
    return f"{(h % 12) or 12}:{m:02d} {ap}"


def _times(rd):
    start = _fmt_clock(rd.get("Mtg Start"))
    end = _fmt_clock(rd.get("Mtg End"))
    return f"{start} - {end}" if start and end else ""


def _session_dates(rd):
    """The per-section session date range as a ``"start - end"`` string.

    CAPTURE-ONLY (FF5): carried so a future calendar/holiday check can read it;
    nothing consumes it yet. A single combined date column wins if present;
    otherwise a separate start/end pair is folded. Missing -> ``""`` (fail open —
    the date range is never invented from other fields)."""
    combined = _first(rd, _DATES_COLUMNS)
    if combined:
        return combined
    start = _first(rd, _START_DATE_COLUMNS)
    end = _first(rd, _END_DATE_COLUMNS)
    if start and end:
        return f"{start} - {end}"
    return start or end or ""


def load_schedule_export(path, *, sheet=None):
    """Read a real schedule export into ``(records, summary)``.

    ``records`` match ``schedule.fetch_sections``' shape. ``summary`` =
    ``{rows_in, sections_out, dropped_cancelled, terms, with_counts,
    total_tot_enrl}``. Raises ``SourceDataError`` (naming the file) when the term /
    course / class-number columns are absent."""
    df = _read_frame(path, sheet)
    if "Term" not in df.columns:
        raise SourceDataError(
            f"{SOURCE}: schedule export {path!r} has no 'Term' column; got "
            f"{list(df.columns)[:14]}. Is this a LACCD schedule export?")
    has_course = (("Subject" in df.columns and "Catalog" in df.columns)
                  or "Course ID" in df.columns or "CLASS" in df.columns)
    if not has_course:
        raise SourceDataError(
            f"{SOURCE}: schedule export {path!r} has no course identifier "
            "(need Subject+Catalog, or Course ID, or CLASS).")
    class_col = next((c for c in _CLASS_NBR_COLUMNS if c in df.columns), None)
    if class_col is None:
        raise SourceDataError(
            f"{SOURCE}: schedule export {path!r} has no 'Class Nbr'/'Class Number' "
            "column.")
    has_status = "Class Status" in df.columns
    has_comb = "Comb Sects ID" in df.columns
    has_counts = "Tot Enrl" in df.columns or "Cap Enrl" in df.columns

    records, seen = [], {}
    rows_in = dropped_cancelled = with_counts = total_tot = 0
    for i, rd in enumerate(df.to_dict("records")):
        if _empty(rd.get("Term")) and _empty(rd.get(class_col)):
            continue  # trailing / fully-blank row
        rows_in += 1
        if has_status and not _is_active(rd.get("Class Status")):
            dropped_cancelled += 1
            continue
        crn = _crn(rd.get(class_col, ""))
        if crn is None:
            raise SourceDataError(
                f"{SOURCE}: export {path!r} row #{i} has a non-numeric "
                f"{class_col} {rd.get(class_col)!r} (a subtotal/footer row?).")
        try:
            term = parse_term(rd.get("Term"))
        except SourceDataError:
            raise
        days_raw = rd.get("DAYS") if not _empty(rd.get("DAYS")) else rd.get("Meetings")
        block = {
            "days": norm_days(days_raw),
            "times": _times(rd),
            "room": _first(rd, _ROOM_COLUMNS),
            "facil_id": str(rd.get("Facil ID") or "").strip(),
        }
        key = (term, crn)
        if key in seen:
            # Secondary meeting pattern for an already-captured section (same Term +
            # Class Nbr, different days/times/room): KEEP the block (M1 — never silently
            # drop it). counts / comb / room of the first row stand; the meeting block
            # is additive and only the outside-engine detectors read it.
            seen[key]["meetings"].append(block)
            continue

        rec = {
            "term": term,
            "course": _course_id(rd),
            "class_nbr": crn,
            "days": block["days"],
            "times": block["times"],
            "room": block["room"],
            "facil_id": block["facil_id"],
            # All meeting blocks (>=1); the flat days/times/room above are block[0],
            # so the engine workbook is byte-identical. See timeblocks.section_meeting.
            "meetings": [block],
            # FF5 CAPTURE-ONLY: weeks-of-instruction + session date range survive
            # so a future calendar/duration check can read them (the live fetch
            # already carries these keys). No consumer reads them yet.
            "woi": _first(rd, _WOI_COLUMNS),
            "dates": _session_dates(rd),
            "status": "Active",
        }
        if has_comb and not _empty(rd.get("Comb Sects ID")):
            rec["Comb Sects ID"] = str(rd.get("Comb Sects ID")).strip()
        if has_counts and not (_empty(rd.get("Cap Enrl")) and _empty(rd.get("Tot Enrl"))):
            try:
                rec["Cap Enrl"] = _to_int(rd.get("Cap Enrl"))
                rec["Tot Enrl"] = _to_int(rd.get("Tot Enrl"))
                rec["Wait Tot"] = _to_int(rd.get("Wait Tot"))
            except (ValueError, TypeError):
                pass
            else:
                with_counts += 1
                total_tot += rec["Tot Enrl"]
        seen[key] = rec
        records.append(rec)

    summary = {
        "rows_in": rows_in,
        "sections_out": len(records),
        "dropped_cancelled": dropped_cancelled,
        "terms": sorted({r["term"] for r in records}),
        "with_counts": with_counts,
        "total_tot_enrl": total_tot,
        # Honest count of sections that collapsed >1 meeting-pattern row into one
        # section record (the secondary blocks are kept in record["meetings"], not
        # silently dropped). 0 when every section meets on a single pattern.
        "multi_block_sections": sum(1 for r in records if len(r.get("meetings", [])) > 1),
    }
    return records, summary


if __name__ == "__main__":  # pragma: no cover - manual operator check
    import json
    import sys

    if len(sys.argv) < 2:
        print("usage: python -m sources.schedule_import <export.(xlsx|csv)> [sheet]")
        raise SystemExit(2)
    _records, _summary = load_schedule_export(
        sys.argv[1], sheet=(sys.argv[2] if len(sys.argv) > 2 else None))
    print(json.dumps(_summary, indent=2))
