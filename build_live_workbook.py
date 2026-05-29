"""Demo / smoke check: fetch live LACCD data, write a workbook, run the engine.

Network IO lives here, OUTSIDE engine.run(). Usage:
  python build_live_workbook.py --campus LAMC --terms 2264,2266,2268 \
      --program "Biology" --out data/live_LAMC.xlsx
"""
from __future__ import annotations

import argparse
import json
import os

import httpx

import engine
from sources import mapping, program_mapper, schedule


def build(campus, terms, program_query):
    with httpx.Client(timeout=30.0) as client:
        sections = schedule.fetch_sections(campus, terms, client=client)
        program = program_mapper.fetch_program(campus, program_query, client=client)
    return sections, program


def main():
    ap = argparse.ArgumentParser(description="Build an EdgeSched workbook from live LACCD sources.")
    ap.add_argument("--campus", default="LAMC")
    ap.add_argument("--terms", default="2264,2266,2268")
    ap.add_argument("--program", default="Biology")
    ap.add_argument("--out", default="data/live_LAMC.xlsx")
    args = ap.parse_args()
    terms = [int(t) for t in args.terms.split(",") if t.strip()]

    sections, program = build(args.campus, terms, args.program)
    if program is None:
        print(f"No program matched {args.program!r}. Try a different --program.")
        raise SystemExit(1)

    matched, unmatched = mapping.reconcile_courses(sections, program)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    mapping.write_workbook(sections, program, args.out)

    print(f"Wrote {args.out}: {len(sections)} sections across {len(terms)} terms; "
          f"program {program['title']!r} ({len(program['courses'])} courses).")
    print(f"Course reconciliation: {len(matched)} matched, "
          f"{len(unmatched)} unmatched (not offered in fetched terms): {unmatched}")
    print("NOTE: Cap/Tot/Wait = 0 -> modality_mismatch and under_supply detectors are "
          "INERT (need the IR PeopleSoft enrollment export, PRD M4). Prerequisites are "
          "blank (need eLumen) -> solver runs without ordering constraints.")

    results = engine.run(args.out)
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
