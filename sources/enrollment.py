r"""IR PeopleSoft enrollment ingest + CRN-suffix-stripping join (pure, offline).

STATUS: UNWIRED SCAFFOLD — NOT a working seam fill (no overclaiming).
------------------------------------------------------------------------------
This module reads an IR PeopleSoft enrollment export and joins its counts onto
live section records. It is a PURE file read (no network) plus a pure,
idempotent, non-aliasing join. BUT it is NOT wired into any pipeline yet:

  * Neither `load_enrollment` nor `enrich_sections` is called by
    `build_live_workbook.py` or `app.py` (grep confirms: only defined here and
    exercised by `tests/test_enrollment_ingest.py`). There is NO `--enrollment`
    CLI flag.
  * The only sections-DataFrame builder, `mapping.build_sections_df`, currently
    HARD-CODES `Cap Enrl` / `Tot Enrl` / `Wait Tot` = 0 (mapping.py:63-65) and
    does NOT read the enrichment keys this module writes. So even if a caller
    ran `enrich_sections`, `build_sections_df` would DROP the enriched counts
    and the engine would still see 0.

In other words: the engine's enrollment seam is NOT filled by this module today.
To actually fill it, a future slice must (a) add a `--enrollment` flag that runs
`enrich_sections` over the fetched sections, AND (b) teach
`build_sections_df` to read `r.get('Cap Enrl', 0)` / `'Tot Enrl'` / `'Wait Tot'`
instead of writing 0 — only then do these counts reach `engine.analyze`. Until
both land, treat this file as a validated-but-inert scaffold, not a seam fill.

Expected real IR PeopleSoft export schema (the `sections` sheet of an `.xlsx`)
------------------------------------------------------------------------------
The real export mirrors the committed fixture `files/lamc_sample_enrollment.xlsx`.
The columns this ingest REQUIRES (and the engine reads) are marked *required*;
the rest are carried-but-ignored context, documented so a real export is
recognizable. Instructor PII columns must be ABSENT (enforced by
`tests/test_sample_enrollment_fixture.py`).

  Column            | Type  | Required | Notes
  ------------------|-------|----------|------------------------------------------
  Term              | int   | yes      | PeopleSoft term code, e.g. 2248 (Fall 24),
                    |       |          | 2252 (Spring 25). (Stored as a string in
                    |       |          | the fixture; pandas coerces to int64.)
  Descr             | str   | no       | e.g. "2024 Fall"
  Campus            | str   | no       | e.g. "LAMC"
  Class Nbr         | int   | yes      | PeopleSoft CRN, a BARE int (20001). Joins
                    |       |          | to the CRN extracted from the schedule
                    |       |          | side's DECORATED class_nbr (see below).
  Subject, Catalog, | str   | no       | section identity context
    Section         |       |          |
  Session,          | str   | no       |
    Class Type,     |       |          |
    Component       |       |          |
  Class Status      | str   | no       | "Active"; cancelled filtered upstream
  Mode, IN_PERSON   | str   | no       | modality context
  Cap Enrl          | int   | yes      | seat capacity -> analyze fill denominator
  Tot Enrl          | int   | yes      | enrolled count -> analyze fill numerator
  Wait Cap          | int   | no       | waitlist capacity
  Wait Tot          | int   | yes      | waitlisted count -> under_supply (sum > 15)
  FILLD, .FILLPERCNT| num   | no       | precomputed fill stats (we recompute)
    ENRL, LMT       |       |          |
  Acad Org, Dep,    | str   | no       |
    Discipline      |       |          |
  IGETC, OER, FTE,  | mixed | no       |
    Class Workload  |       |          |
    Hrs, LEVEL      |       |          |
  CLASS             | str   | optional | "SUBJ CAT" form; plan-only degraded join
  SEC, Class Start  | str   | no       |
    Date, Class End |       |          |
    Date            |       |          |

Join contract: `(int(Term), canonical_CRN)` where `canonical_CRN` is the bare
integer CRN as a string. The IR side is `str(int(Class Nbr))`. The schedule side
is DECORATED — `schedule.fetch_sections` emits `class_nbr` as a string like
`'17818 (LEC)'` / `'17819 (LAB)'` (verified: all 81 records in the committed
schedule fixture carry the ` (LEC)`/` (LAB)` suffix) — so the join strips that
suffix to a bare CRN via `re.match(r'\s*(\d+)', str(class_nbr)).group(1)` before
comparing. **Plain `str()` on the schedule side NEVER reconciles '17818 (LEC)'
with the IR key '17818'; the suffix strip is mandatory.** A blank/non-numeric
`class_nbr` is SKIPPED (never keyed on '').

FIXTURE-ONLY JOIN CAVEAT (no overclaiming) — the live-schedule ↔ IR join is NOT
validated end-to-end against the real schedule source. No committed schedule +
enrollment fixture pair shares a `(term, CRN)`: the committed schedule fixture is
term 2268, the committed enrollment fixture is terms {2248, 2252} — ZERO term
overlap — and the CRN sets do not intersect even after stripping the suffix.
Even if a future `--enrollment` flag existed (it does NOT yet — see STATUS
above), running it against today's live/2268 schedule would match ZERO sections,
so the enrollment detectors would stay INERT (Cap/Tot/Wait = 0). The join is
exercised only WITHIN one self-consistent set of records (the enrollment
fixture's own terms, or a hand-keyed inline map) — never across the live
schedule↔IR boundary. NOTE: `build_live_workbook`'s report does NOT yet mention
this join; its INERT_DETECTORS notes still point at "PRD M4" generically and are
not aware of this scaffold (consistent with the unwired status above).
"""
from __future__ import annotations

import re

import pandas as pd

from .http import SourceDataError

SOURCE = "IR enrollment ingest"

# Columns the ingest requires (engine reads these via build_sections_df ->
# analyze). Missing any -> SourceDataError naming the file.
REQUIRED_COLUMNS = ["Term", "Class Nbr", "Cap Enrl", "Tot Enrl", "Wait Tot"]

# Leading-integer CRN extractor. The schedule side is decorated ('17818 (LEC)');
# we want the bare CRN ('17818'). A blank / non-numeric-prefix value yields None.
_CRN_RE = re.compile(r"\s*(\d+)")


def _crn(class_nbr):
    """Extract the bare integer CRN from a (possibly decorated) class_nbr.

    Returns the canonical bare-CRN string (e.g. '17818') or None when the value
    is blank / has no leading integer (so the caller skips the join — never keys
    on '').
    """
    match = _CRN_RE.match(str(class_nbr))
    return match.group(1) if match else None


def load_enrollment(path):
    """Read an IR PeopleSoft enrollment workbook into a join map.

    Returns dict[(int term, str bare-CRN) -> {'Cap Enrl', 'Tot Enrl',
    'Wait Tot'}]. Pure file read — no network. Raises SourceDataError naming the
    file if any required column is absent.
    """
    df = pd.read_excel(path, sheet_name="sections")
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise SourceDataError(
            f"{SOURCE}: enrollment workbook {path!r} is missing required "
            f"column(s) {missing}; got columns {list(df.columns)[:12]}. "
            "The IR PeopleSoft export shape may have changed."
        )
    enrollment = {}
    # to_dict('records') preserves the original column names (unlike itertuples,
    # which mangles names containing spaces like 'Class Nbr').
    for i, rd in enumerate(df.to_dict("records")):
        # Term is stored as a string in the fixture but pandas coerces it to an
        # int64; int() handles both. Class Nbr is a bare int -> canonical CRN.
        # A dirty real export (a 'Total'/subtotal footer row, a blank/TBD
        # Class Nbr, or any non-numeric cell) makes int() raise a raw ValueError;
        # catch it and re-raise SourceDataError naming the file, row, and the
        # offending column/value (matching mapping.build_sections_df's pattern)
        # so a raw traceback never escapes — per the sources/http.py contract.
        try:
            key = (int(rd["Term"]), str(int(rd["Class Nbr"])))
            counts = {
                "Cap Enrl": int(rd["Cap Enrl"]),
                "Tot Enrl": int(rd["Tot Enrl"]),
                "Wait Tot": int(rd["Wait Tot"]),
            }
        except (ValueError, TypeError) as exc:
            # Surface which column/value tripped the coercion so the operator can
            # find the bad row (e.g. a subtotal footer) in the real export.
            bad = {c: rd.get(c) for c in REQUIRED_COLUMNS}
            raise SourceDataError(
                f"{SOURCE}: enrollment workbook {path!r} row #{i} has a "
                f"non-numeric value in a required column ({bad}); expected "
                "integers for Term/Class Nbr/Cap Enrl/Tot Enrl/Wait Tot. "
                "Is there a subtotal/footer row or a blank/TBD cell?"
            ) from exc
        enrollment[key] = counts
    return enrollment


def enrich_sections(section_records, enrollment):
    """Thread enrollment counts onto live section records.

    Returns a NEW list of NEW dicts (does NOT mutate the caller's records in
    place — this keeps it idempotent and non-aliasing). Matched records gain
    'Cap Enrl' / 'Tot Enrl' / 'Wait Tot'; unmatched (live-only) records carry no
    enrollment keys (so they stay 0 downstream via build_sections_df defaults).

    Join key: (int(term), bare-CRN), where the schedule-side class_nbr is
    stripped of its ` (LEC)`/` (LAB)` suffix. Blank / non-numeric class_nbr is
    SKIPPED (never keyed on '') so blank-CRN relsections are not falsely matched.
    The counts are written once per matching record (no aggregation), matching
    engine.analyze's per-row sum.

    DISTINCT-CRN ASSUMPTION (latent double-count caveat): this per-record write
    assumes each live record carries a DISTINCT CRN. engine.analyze SUMS Cap/Tot/
    Wait per row (engine.py:138-143), so if two live records ever shared a single
    parent CRN (e.g. linked LEC/LAB sections collapsed onto one class_nbr), each
    would receive the FULL counts and analyze would DOUBLE-COUNT that course's
    capacity/enrollment. The committed fixtures are safe (schedule.fetch_sections
    emits a distinct class_nbr per relsection — '17818 (LEC)' vs '17818 (LAB)'
    strip to the same CRN only in the hand-keyed test, not in real fixture data,
    where all 81 CRNs are unique). If linked sections ever share a parent CRN,
    dedup must happen UPSTREAM before this join.
    """
    out = []
    for record in section_records:
        # Fresh copy so the caller's dict is never mutated and re-running is a
        # no-op (idempotent); also strip any stale enrollment keys from a prior
        # enrich so a now-unmatched record does not retain old counts.
        new = {k: v for k, v in record.items()
               if k not in ("Cap Enrl", "Tot Enrl", "Wait Tot")}
        crn = _crn(record.get("class_nbr", ""))
        if crn is not None:
            try:
                term = int(record.get("term"))
            except (ValueError, TypeError):
                term = None
            if term is not None:
                counts = enrollment.get((term, crn))
                if counts is not None:
                    new["Cap Enrl"] = counts["Cap Enrl"]
                    new["Tot Enrl"] = counts["Tot Enrl"]
                    new["Wait Tot"] = counts["Wait Tot"]
        out.append(new)
    return out
