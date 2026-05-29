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
from sources import mapping, program_mapper, schedule


def build(campus, terms, program_query, *, client=None):
    """Fetch sections + program from the live sources (or an injected client)."""
    if client is not None:
        sections = schedule.fetch_sections(campus, terms, client=client)
        program = program_mapper.fetch_program(campus, program_query, client=client)
        return sections, program
    with httpx.Client(timeout=30.0) as owned:
        sections = schedule.fetch_sections(campus, terms, client=owned)
        program = program_mapper.fetch_program(campus, program_query, client=owned)
    return sections, program


# The schedule API ships no enrollment counts and no prerequisites, so two
# detectors can never fire on live-sourced data. We surface that honestly as
# structured fields rather than letting an empty result look like a clean bill.
INERT_DETECTORS = [
    {
        "detector": "modality_mismatch",
        "reason": ("the LACCD schedule API returns no enrollment/capacity counts "
                   "(Cap Enrl / Tot Enrl = 0), so fill ratio cannot be computed"),
        "remedy": "load the IR PeopleSoft enrollment export (PRD M4)",
    },
    {
        "detector": "under_supply",
        "reason": ("the LACCD schedule API returns no waitlist counts "
                   "(Wait Tot = 0), so waitlist pressure cannot be measured"),
        "remedy": "load the IR PeopleSoft enrollment export (PRD M4)",
    },
    {
        "detector": "prerequisite_ordering",
        "reason": ("prerequisites are blank (Program Mapper does not expose them); "
                   "the solver runs without ordering constraints"),
        "remedy": "wire eLumen prerequisite data into the catalog sheet",
    },
]


def analyze_live(campus, terms, program_query, out_path, *, client=None):
    """Run the full live pipeline and return a structured, JSON-serializable report.

    The report carries the reconciliation (matched/unmatched program courses)
    and the inert-detector notes as machine-readable fields so a UI can render
    them, in addition to the human banner main() prints.
    """
    sections, program = build(campus, terms, program_query, client=client)

    report = {
        "campus": campus,
        "terms": list(terms),
        "section_count": len(sections),
        "program": None,
        "reconciliation": None,
        "inert_detectors": INERT_DETECTORS,
        "results": None,
        "error": None,
    }

    if program is None:
        report["error"] = (f"No program matched {program_query!r} at {campus}. "
                            "Try a different --program.")
        return report

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

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    mapping.write_workbook(sections, program, out_path)
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
    args = ap.parse_args()
    terms = [int(t) for t in args.terms.split(",") if t.strip()]

    report = analyze_live(args.campus, terms, args.program, args.out)
    if report["error"]:
        _print_banner(report)
        raise SystemExit(1)

    _print_banner(report)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
