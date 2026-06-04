r"""Tolerant IR / PeopleSoft enrollment ADAPTER (real LACCD export shapes -> join map).

``sources/enrollment.py:load_enrollment`` is the STRICT reader for the committed
fixture format (an ``.xlsx`` sheet literally named ``sections``, a numeric
``Term``, a ``Class Nbr`` column). Real LACCD / PeopleSoft exports vary:

  * a CSV with a ``Class Number`` column and a ``"2024 Fall"`` *string* Term;
  * an ``.xlsx`` whose data lives on a ``Data and formulas`` sheet and carries
    ``Class Status`` / ``Comb Sects ID`` columns;
  * both at MEETING-PATTERN grain (one row per meeting pattern, so a section
    repeats), and both carrying instructor PII (``Name`` / ``Emails``).

This module normalizes any of those into the SAME
``(int term, str bare-CRN) -> {"Cap Enrl", "Tot Enrl", "Wait Tot"}`` map that
``enrollment.enrich_sections`` + ``engine.analyze`` already consume, so the whole
downstream stack (the join, the ``modality_mismatch`` / ``under_supply``
detectors, ``app.fetch_live``, the UI upload button) is reused UNCHANGED. The
strict reader and its committed fixture stay pristine (back-compat / the
byte-identical determinism gate).

Normalization performed:
  * Container: ``.csv`` or ``.xlsx`` (sheet auto-picked: explicit ``sheet`` arg ->
    else the first sheet whose columns satisfy the required set, preferring
    ``data and formulas`` / ``sections`` / ``enrollment``).
  * Column aliases: ``Class Number`` -> ``Class Nbr``.
  * Term: a ``"2024 Fall"`` / ``"Fall 2024"`` string -> PeopleSoft numeric (2248)
    via the inverse of ``engine.season_of_code`` / ``engine.year_of_code``; an
    already-numeric code passes through.
  * Cancelled rows dropped (``Class Status`` present and not Active).
  * Meeting-pattern grain DEDUPED on ``(term, CRN)`` (Cap/Tot/Wait are constant
    within a section, so first row wins). Combined cross-listed members keep their
    OWN counts (the ``Combined *`` columns are ignored); a ``Comb Sects ID`` group
    that shares one physical cap may therefore slightly overstate -- surfaced in
    the summary's ``combined_rows`` so a reader can discount it.

PII: instructor ``Name`` / ``Emails`` columns (when present in a real export) are
never read into the map -- the map values are only Cap/Tot/Wait.

Caveat (unchanged from ``enrollment.py``): the LACCD live schedule API serves only
CURRENT terms, so an uploaded export joins a live fetch ONLY when its term matches
the fetched term. ``under_supply`` / ``modality_mismatch`` stay DEMAND proxies,
never completion causation.
"""
from __future__ import annotations

import os

import pandas as pd

from .enrollment import _crn  # reuse the bare-CRN extractor (suffix-strip semantics)
from .http import SourceDataError

SOURCE = "IR enrollment adapter"

# Columns required after aliasing (same set the strict reader / engine need).
REQUIRED_COLUMNS = ["Term", "Class Nbr", "Cap Enrl", "Tot Enrl", "Wait Tot"]

# Real-export header variants -> the canonical name the engine reads.
_COLUMN_ALIASES = {"Class Number": "Class Nbr"}

# Inverse of engine.season_of_code's last-digit map (season name -> term digit).
SEASON_DIGIT = {"spring": 2, "summer": 6, "fall": 8, "winter": 1}

# Sheet-name hints, most-preferred first, for auto-picking the data sheet.
_SHEET_PREFERENCE = ("data and formulas", "sections", "enrollment")


def parse_term(value):
    """Normalize a Term cell to a PeopleSoft numeric term code (int).

    Accepts an already-numeric code (``2248`` / ``"2248"`` / ``"2248.0"``) or a
    human string in either word order (``"2024 Fall"`` / ``"Fall 2024"``). The
    encoding mirrors ``engine``'s decoders so it round-trips:
    ``engine.season_of_code(parse_term("2024 Fall")) == "Fall"`` and
    ``engine.year_of_code(parse_term("2024 Fall")) == 2024``. Raises
    ``SourceDataError`` on a blank / unparseable value.
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        raise SourceDataError(
            f"{SOURCE}: blank Term cell; expected a PeopleSoft code (2248) or a "
            "'YYYY Season' string like '2024 Fall'.")
    s = str(value).strip()
    if s.replace(".", "", 1).isdigit():  # already numeric: 2248 / "2248" / "2248.0"
        return int(float(s))
    return _parse_human_term(s)


def _parse_human_term(s):
    year = season = None
    for tok in s.replace(",", " ").split():
        low = tok.lower()
        if low in SEASON_DIGIT:
            season = low
        elif tok.isdigit() and len(tok) == 4:
            year = int(tok)
    if year is None or season is None:
        raise SourceDataError(
            f"{SOURCE}: cannot parse Term {s!r}; expected a PeopleSoft code (2248) "
            "or a 'YYYY Season' string like '2024 Fall'.")
    # PeopleSoft code = '2' + YY (year-2000, 2 digits) + season digit.
    return 2000 + (year - 2000) * 10 + SEASON_DIGIT[season]


def _empty(v):
    return v is None or (isinstance(v, float) and pd.isna(v)) or str(v).strip() == ""


def _is_active(status):
    """True when a Class Status cell means the section was delivered. Blank counts
    as active (some exports omit it); 'Cancelled' / 'Stop-Futher' are dropped."""
    s = str(status or "").strip().lower()
    return s == "" or s.startswith("active")


def _to_int(v):
    """Coerce a count cell to int. Blank -> 0 (a common 'no waitlist' convention);
    a non-numeric non-blank value raises ValueError for the caller to wrap."""
    if _empty(v):
        return 0
    return int(float(str(v).strip()))


def _apply_aliases(cols):
    return [_COLUMN_ALIASES.get(c, c) for c in cols]


def _read_frame(path, sheet):
    """Read the export into a DataFrame (all str) from a CSV or the right xlsx sheet."""
    if os.path.splitext(path)[1].lower() == ".csv":
        return pd.read_csv(path, dtype=str)
    xl = pd.ExcelFile(path)
    if sheet is not None:
        return xl.parse(sheet, dtype=str)
    # Auto-pick: the first sheet (by name preference) whose columns, after
    # aliasing, satisfy the required set; fall back to the first sheet so the
    # missing-column error below names a concrete sheet's headers.
    ordered = sorted(xl.sheet_names, key=lambda n: next(
        (i for i, pref in enumerate(_SHEET_PREFERENCE) if pref in n.lower()),
        len(_SHEET_PREFERENCE)))
    for name in ordered:
        cols = set(_apply_aliases(list(xl.parse(name, nrows=0).columns)))
        if set(REQUIRED_COLUMNS) <= cols:
            return xl.parse(name, dtype=str)
    return xl.parse(xl.sheet_names[0], dtype=str)


def load_ir_export(path, *, sheet=None):
    """Read a real LACCD/PeopleSoft enrollment export into the join map.

    Returns ``dict[(int term, str bare-CRN) -> {"Cap Enrl", "Tot Enrl",
    "Wait Tot"}]`` -- identical in shape to ``enrollment.load_enrollment`` so
    ``enrollment.enrich_sections`` consumes it unchanged. Pure file read."""
    return load_ir_export_with_report(path, sheet=sheet)[0]


def load_ir_export_with_report(path, *, sheet=None):
    """Like :func:`load_ir_export` but also returns an honest ingest summary
    ``{rows_in, sections_out, dropped_cancelled, combined_rows, terms,
    total_tot_enrl}`` (for the CLI and for sentinel checks against real exports)."""
    df = _read_frame(path, sheet).rename(columns=_COLUMN_ALIASES)
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise SourceDataError(
            f"{SOURCE}: enrollment export {path!r} is missing required column(s) "
            f"{missing}; got columns {list(df.columns)[:14]}. Is this a LACCD / "
            "PeopleSoft enrollment export?")
    has_status = "Class Status" in df.columns
    has_comb = "Comb Sects ID" in df.columns

    enrollment = {}
    rows_in = dropped_cancelled = combined_rows = total_tot = 0
    for i, rd in enumerate(df.to_dict("records")):
        if all(_empty(rd.get(c)) for c in REQUIRED_COLUMNS):
            continue  # trailing / fully-blank row
        rows_in += 1
        if has_status and not _is_active(rd.get("Class Status")):
            dropped_cancelled += 1
            continue
        crn = _crn(rd.get("Class Nbr", ""))
        if crn is None:
            # A non-blank row with a non-numeric Class Nbr is a subtotal/footer
            # ('Total'); surface it like the strict reader instead of leaking a
            # raw traceback.
            raise SourceDataError(
                f"{SOURCE}: export {path!r} row #{i} has a non-numeric Class Nbr "
                f"{rd.get('Class Nbr')!r} (a subtotal/footer row?).")
        try:
            term = parse_term(rd.get("Term"))
            counts = {
                "Cap Enrl": _to_int(rd.get("Cap Enrl")),
                "Tot Enrl": _to_int(rd.get("Tot Enrl")),
                "Wait Tot": _to_int(rd.get("Wait Tot")),
                # FF5 CAPTURE-ONLY: PeopleSoft Component (contact category, e.g.
                # LEC/LAB). Carried so a future Title-5 category map can read it;
                # no check consumes it yet. Not PII. "" when the export omits it.
                "Component": str(rd.get("Component") or "").strip(),
            }
        except SourceDataError:
            raise
        except (ValueError, TypeError) as exc:
            bad = {c: rd.get(c) for c in REQUIRED_COLUMNS}
            raise SourceDataError(
                f"{SOURCE}: export {path!r} row #{i} has a non-numeric value in a "
                f"required column ({bad}); expected integers for Cap/Tot/Wait Enrl. "
                "A subtotal/footer row?") from exc
        key = (term, crn)
        if key in enrollment:
            continue  # meeting-pattern dedup: counts are constant within a section
        enrollment[key] = counts
        total_tot += counts["Tot Enrl"]
        if has_comb and not _empty(rd.get("Comb Sects ID")):
            combined_rows += 1

    summary = {
        "rows_in": rows_in,
        "sections_out": len(enrollment),
        "dropped_cancelled": dropped_cancelled,
        "combined_rows": combined_rows,
        "terms": sorted({k[0] for k in enrollment}),
        "total_tot_enrl": total_tot,
    }
    return enrollment, summary


if __name__ == "__main__":  # pragma: no cover - manual operator check
    import json
    import sys

    if len(sys.argv) < 2:
        print("usage: python -m sources.enrollment_ir <export.(csv|xlsx)> [sheet]")
        raise SystemExit(2)
    _, _summary = load_ir_export_with_report(
        sys.argv[1], sheet=(sys.argv[2] if len(sys.argv) > 2 else None))
    print(json.dumps(_summary, indent=2))
