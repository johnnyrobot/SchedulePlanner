"""Demo / smoke check: fetch live LACCD data, write a workbook, run the engine.

Network IO lives here, OUTSIDE engine.run(). Usage:

  python3 build_live_workbook.py --campus LAMC --terms 2264,2266,2268 \
      --program "Biology" --out data/live_LAMC.xlsx

LIVE REALITY (what one run actually produces):
  - Program Mapper returns ONE program per run (the first whose title or award
    matches --program), with its default-pathway course list. There is no bulk
    "all programs" export; pick one program per workbook.
  - --terms defaults to the three currently-published terms
    (schedule.DEFAULT_TERMS = 2264, 2266, 2268). Each term is a separate
    schedule API call; pass a comma list to widen or narrow the window.
  - The schedule API has NO enrollment/capacity/waitlist counts and NO
    prerequisites, so the modality_mismatch and under_supply detectors are
    INERT and the solver runs without prerequisite ordering. These gaps are
    surfaced as structured fields in the report below (not hidden).

To produce a representative MULTI-TERM sample for the Biology AS-T at Mission
College across a full year window, run exactly:

  python3 build_live_workbook.py --campus LAMC --program "Biology" \
      --terms 2264,2266,2268 --out data/live_LAMC.xlsx

The command prints a JSON report (campus, terms, program, section_count,
reconciliation, inert_detectors, results) followed by a human-readable banner.
"""
from __future__ import annotations

import argparse
import json
import os

import httpx

import engine
from sources import elumen, enrollment, mapping, program_mapper, schedule


def build(campus, terms, program_query, *, client=None):
    """Fetch sections + program from the live sources (or an injected client).

    Network IO lives HERE, outside engine.run(). The m7 enrichment (enrollment
    join + eLumen prereq map) is applied downstream in analyze_live on the raw
    records this returns, BEFORE the workbook write — never inside engine.run().
    """
    if client is not None:
        sections = schedule.fetch_sections(campus, terms, client=client)
        program = program_mapper.fetch_program(campus, program_query, client=client)
        return sections, program
    with httpx.Client(timeout=30.0) as owned:
        sections = schedule.fetch_sections(campus, terms, client=owned)
        program = program_mapper.fetch_program(campus, program_query, client=owned)
    return sections, program


# The schedule API ships no enrollment counts and no prerequisites, so two
# detectors are INERT on live-sourced data by default. We surface that honestly
# as structured fields rather than letting an empty result look like a clean
# bill. The m7 enrichment can FLIP an entry to "active" — but ONLY when its data
# is present AND (for enrollment) the (term, CRN) join actually matched a
# section. _detector_report() below builds the per-run report from this baseline.
INERT_DETECTORS = [
    {
        "detector": "modality_mismatch",
        "status": "inert",
        "reason": ("the LACCD schedule API returns no enrollment/capacity counts "
                   "(Cap Enrl / Tot Enrl = 0), so fill ratio cannot be computed"),
        "remedy": "load the IR PeopleSoft enrollment export (PRD M4)",
    },
    {
        "detector": "under_supply",
        "status": "inert",
        "reason": ("the LACCD schedule API returns no waitlist counts "
                   "(Wait Tot = 0), so waitlist pressure cannot be measured"),
        "remedy": "load the IR PeopleSoft enrollment export (PRD M4)",
    },
    {
        "detector": "prerequisite_ordering",
        "status": "inert",
        "reason": ("prerequisites are blank (Program Mapper does not expose them); "
                   "the solver runs without ordering constraints"),
        "remedy": "wire eLumen prerequisite data into the catalog sheet",
    },
]


def _enrollment_detector_entries(*, source, matched, total):
    """Build the two enrollment detectors' report entries.

    They flip to "active" ONLY when an enrollment export was joined AND the join
    matched >=1 section. Absent enrollment, or a zero-match join, keeps the
    honest inert reason. The activation is LABELED fixture-scoped — the
    live-schedule <-> IR (term, CRN) join is NOT validated on real data (no
    committed schedule (2268) + enrollment ({2248,2252}) fixture pair overlaps).
    """
    if source is None:
        # No enrollment input at all: keep the honest baseline inert entries.
        return [dict(d) for d in INERT_DETECTORS
                if d["detector"] in ("modality_mismatch", "under_supply")]

    if matched >= 1:
        active_note = ("FIXTURE-SCOPED: the live-schedule <-> IR (term, CRN) join "
                       "is NOT validated on real data; activated here via a "
                       "self-consistent / hand-keyed enrollment map.")
        return [
            {
                "detector": "modality_mismatch",
                "status": "active",
                "source": source,
                "matched_sections": matched,
                "label": active_note,
                "metric": "fill ratio < 0.55 (Tot Enrl / Cap Enrl)",
            },
            {
                "detector": "under_supply",
                "status": "active",
                "source": source,
                "matched_sections": matched,
                "label": active_note,
                "metric": "Wait Tot sum > 15",
            },
        ]

    # Enrollment present but the join matched ZERO rows: stay INERT, honestly.
    zero_reason = (
        f"enrollment export {source!r} was loaded but the (term, CRN) join "
        f"matched 0 sections (live-schedule <-> IR fixtures disjoint: schedule "
        f"term 2268 vs enrollment terms {{2248, 2252}}, CRN sets disjoint), so "
        f"Cap/Tot/Wait stay 0 and the detector cannot fire")
    return [
        {
            "detector": "modality_mismatch",
            "status": "inert",
            "source": source,
            "matched_sections": 0,
            "matched_sections_note": "join matched 0 sections",
            "reason": zero_reason,
            "remedy": ("supply an enrollment export whose (term, CRN) keys overlap "
                       "the fetched schedule (validated end-to-end only on a "
                       "self-consistent fixture set)"),
        },
        {
            "detector": "under_supply",
            "status": "inert",
            "source": source,
            "matched_sections": 0,
            "matched_sections_note": "join matched 0 sections",
            "reason": zero_reason,
            "remedy": ("supply an enrollment export whose (term, CRN) keys overlap "
                       "the fetched schedule (validated end-to-end only on a "
                       "self-consistent fixture set)"),
        },
    ]


def _prereq_detector_entry(*, source, results):
    """Build the prerequisite_ordering report entry.

    Flips to "active" when a prereq map was threaded in (``results`` is not
    None). Per-course it distinguishes exact-CNF courses from budget/fallback
    courses (the latter labeled *conservative-permissive, not exact*). The whole
    eLumen path is labeled *fixture-only* (no real eLumen endpoint/response).
    """
    if results is None:
        return dict(next(d for d in INERT_DETECTORS
                         if d["detector"] == "prerequisite_ordering"))

    exact, fallback = [], []
    for cid, res in sorted(results.items()):
        if res.exact:
            exact.append(cid)
        else:
            fallback.append({"course": cid, "reason": res.fallback_reason})
    return {
        "detector": "prerequisite_ordering",
        "status": "active",
        "source": source,
        "label": ("FIXTURE-ONLY: the eLumen prereq slice is parsed from a "
                  "self-defined committed fixture and is NOT validated on real "
                  "eLumen data."),
        "prereq_summary": {
            "exact_count": len(exact),
            "fallback_count": len(fallback),
            "exact_courses": exact,
            "fallback_courses": fallback,
            "fallback_label": ("budget/fallback courses are conservative-permissive "
                               "(an UNDER-approximate union clause), NOT exact — "
                               "ordering for those courses is relaxed but flagged"),
        },
    }


def analyze_live(campus, terms, program_query, out_path, *, client=None,
                 enrollment_path=None, elumen_fixture=None, enrollment_map=None,
                 prereq_max_clauses=None):
    """Run the full live pipeline and return a structured, JSON-serializable report.

    The report carries the reconciliation (matched/unmatched program courses)
    and the inert-detector notes as machine-readable fields so a UI can render
    them, in addition to the human banner main() prints.

    m7 enrichment (ALL outside engine.run, on the raw records, before the write):
      - ``enrollment_path`` loads an IR PeopleSoft export and joins counts onto
        the fetched sections via enrollment.enrich_sections; ``enrollment_map``
        supplies a ready-made join dict instead (used by offline tests with a
        hand-keyed synthetic key, since no committed schedule+enrollment fixture
        pair overlaps — the live<->IR join is fixture-only / not validated live).
      - ``elumen_fixture`` loads the FIXTURE-ONLY eLumen DNF records and builds a
        course-id -> CNF-string prereq map (elumen.build_prereq_map), threaded
        into the catalog sheet. ``prereq_max_clauses`` overrides the DNF->CNF
        clause-budget guard (a budget-exceeded course falls back to a flagged
        conservative-permissive union clause).

    The enrollment/prereq enrichment runs HERE, never inside engine.run(): the
    engine still reads a finished workbook only.
    """
    sections, program = build(campus, terms, program_query, client=client)

    report = {
        "campus": campus,
        "terms": list(terms),
        "section_count": len(sections),
        "program": None,
        "reconciliation": None,
        "inert_detectors": list(INERT_DETECTORS),
        "results": None,
        "error": None,
    }

    if program is None:
        report["error"] = (f"No program matched {program_query!r} at {campus}. "
                            "Try a different --program.")
        return report

    # --- enrollment join (outside engine.run, on raw records) ----------------
    # enrich_sections returns a NEW list; matched records gain Cap/Tot/Wait keys
    # that build_sections_df then reads. matched==0 keeps every count at 0 and
    # the enrollment detectors INERT (the honest fixture-only contract).
    enrollment_source = None
    matched_sections = 0
    if enrollment_map is not None:
        enrollment_source = enrollment_path or "inline enrollment map (synthetic key)"
        sections = enrollment.enrich_sections(sections, enrollment_map)
        matched_sections = sum(1 for r in sections if "Cap Enrl" in r)
    elif enrollment_path is not None:
        enrollment_source = enrollment_path
        enrollment_data = enrollment.load_enrollment(enrollment_path)
        sections = enrollment.enrich_sections(sections, enrollment_data)
        matched_sections = sum(1 for r in sections if "Cap Enrl" in r)

    # --- eLumen prereq map (outside engine.run, fixture-only) ----------------
    prereq_map = None
    prereq_results = None
    elumen_source = None
    if elumen_fixture is not None:
        elumen_source = elumen_fixture
        records = elumen.load_elumen_fixture(elumen_fixture)
        kwargs = {}
        if prereq_max_clauses is not None:
            kwargs["max_clauses"] = prereq_max_clauses
        prereq_map, prereq_results = elumen.build_prereq_map(records, **kwargs)

    matched, unmatched = mapping.reconcile_courses(sections, program)
    report["program"] = {
        "code": program["code"],
        "title": program["title"],
        "award": program.get("award", ""),
        "course_count": len(program["courses"]),
    }
    report["reconciliation"] = {
        "matched": matched,
        "unmatched": unmatched,
        "matched_count": len(matched),
        "unmatched_count": len(unmatched),
        "note": ("'unmatched' = program courses not offered in the fetched terms"),
    }

    # Build the detector report from the enrichment outcomes (honest activation).
    report["inert_detectors"] = (
        _enrollment_detector_entries(source=enrollment_source,
                                     matched=matched_sections,
                                     total=len(sections))
        + [_prereq_detector_entry(source=elumen_source, results=prereq_results)]
    )

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    # Task 5 only PASSES the prereq map + enriched records; write_workbook's
    # prereqs kwarg is owned by mapping.py (Task 4). engine.py is untouched.
    mapping.write_workbook(sections, program, out_path, prereqs=prereq_map)
    report["workbook"] = out_path
    report["results"] = engine.run(out_path)
    return report


def _print_banner(report):
    if report["error"]:
        print(report["error"])
        return
    prog = report["program"]
    rec = report["reconciliation"]
    print(f"Wrote {report['workbook']}: {report['section_count']} sections across "
          f"{len(report['terms'])} terms; program {prog['title']!r} "
          f"({prog['course_count']} courses).")
    print(f"Course reconciliation: {rec['matched_count']} matched, "
          f"{rec['unmatched_count']} unmatched (not offered in fetched terms): "
          f"{rec['unmatched']}")
    print("NOTE: Cap/Tot/Wait = 0 -> modality_mismatch and under_supply detectors "
          "are INERT (need the IR PeopleSoft enrollment export, PRD M4). "
          "Prerequisites are blank (need eLumen) -> solver runs without ordering "
          "constraints.")


def main():
    ap = argparse.ArgumentParser(
        description="Build an EdgeSched workbook from live LACCD sources.")
    ap.add_argument("--campus", default="LAMC")
    ap.add_argument("--terms", default="2264,2266,2268")
    ap.add_argument("--program", default="Biology")
    ap.add_argument("--out", default="data/live_LAMC.xlsx")
    ap.add_argument(
        "--enrollment", default=None,
        help=("optional IR PeopleSoft enrollment export (.xlsx) to join onto the "
              "fetched sections. FIXTURE-ONLY caveat: with today's committed "
              "fixtures the live-schedule <-> IR (term, CRN) join matches ZERO "
              "sections (term + CRN disjoint), so the enrollment detectors stay "
              "INERT — this is the documented limitation, not a bug."))
    ap.add_argument(
        "--elumen-fixture", default=None, dest="elumen_fixture",
        help=("optional FIXTURE-ONLY eLumen DNF prereq fixture (.json) to thread "
              "into the catalog sheet (not validated on real eLumen data)."))
    args = ap.parse_args()
    terms = [int(t) for t in args.terms.split(",") if t.strip()]

    report = analyze_live(args.campus, terms, args.program, args.out,
                          enrollment_path=args.enrollment,
                          elumen_fixture=args.elumen_fixture)
    if report["error"]:
        _print_banner(report)
        raise SystemExit(1)

    _print_banner(report)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
