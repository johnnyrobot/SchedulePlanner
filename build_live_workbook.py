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
  - The schedule API has NO enrollment/capacity COUNTS and NO prerequisites, so
    on a BARE fetch the modality_mismatch detector is INERT (needs a fill ratio)
    and the solver runs without prerequisite ordering. It DOES expose a
    per-section availability status (Open/Waitlist/Closed), so under_supply IS
    active on live data — a Waitlist section is at capacity, giving a coarse
    waitlist-pressure signal (breadth, not a student headcount). These gaps are
    surfaced as structured fields in the report below (not hidden). The optional
    m7 enrichment flags --enrollment (an IR PeopleSoft export joined onto the
    fetched sections, adding the precise Wait Tot headcount + the fill ratio) and
    --elumen-fixture / --elumen-live (a DNF->CNF prereq map threaded into the
    catalog) can FLIP modality_mismatch / prerequisite_ordering to active and
    sharpen under_supply; that enrichment runs OUTSIDE engine.run, before write.
    These paths carry honest caveats: the enrollment join is fixture-scoped (the
    live-schedule <-> IR (term, CRN) join is not validated on real data, and
    today's committed fixtures match zero sections so a real --enrollment run
    stays inert); the --elumen-fixture prereq slice is fixture-only (not
    validated on real eLumen data); and --elumen-live (below) is best-effort
    against a real-but-unreviewed endpoint.

eLumen prerequisites (two mutually-exclusive sources):
  - --elumen-fixture PATH  loads a committed JSON DNF capture (offline,
    reproducible) and threads a course-id -> CNF-string prereq map into the
    catalog sheet. Labeled FIXTURE-ONLY.
  - --elumen-live          fetches prerequisites from the REAL public eLumen
    catalog endpoint for the campus tenant (sources.elumen_client). Network is
    required; this is opt-in only and never default-on.

Live eLumen semantics & caveats (NO OVERCLAIMING):
  - Only leaf requisites whose itemType is "Prerequisite" become hard ordering
    constraints; corequisites and advisories are deliberately EXCLUDED.
  - The eLumen catalog joins to the schedule / Program Mapper catalogs on a
    normalized course id (uppercased, trimmed, leading zeros stripped, e.g.
    "BIOLOGY 03" -> "BIOLOGY 3"). This join is validated ONLY via the per-build
    coverage report (report["elumen_coverage"]); inspect it before trusting the
    result.
  - The endpoint is public + unauthenticated, but ToU / rate-limit /
    human-approval review are PENDING. Live fetches are best-effort, not
    production-ready.
  - If BOTH --elumen-fixture and --elumen-live are supplied, LIVE wins and a
    warning is recorded in report["warnings"].

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
from sources import elumen, elumen_client, enrollment, mapping, program_mapper, schedule


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


# The schedule API ships no enrollment COUNTS and no prerequisites, so two
# detectors are INERT on bare-fetch live data by default: modality_mismatch
# (needs a fill ratio) and prerequisite_ordering (needs eLumen). under_supply is
# NOT here — the schedule API's per-section Waitlist status is a live signal it
# fires on (see engine.analyze + mapping's "Avail Status"); the IR export only
# sharpens it from breadth (sections waitlisted) to depth (Wait Tot headcount).
# We surface the remaining gaps honestly as structured fields rather than letting
# an empty result look like a clean bill. The m7 enrichment can FLIP an entry to
# "active" — but ONLY when its data is present AND (for enrollment) the
# (term, CRN) join actually matched a section.
INERT_DETECTORS = [
    {
        "detector": "modality_mismatch",
        "status": "inert",
        "reason": ("the LACCD schedule API returns no enrollment/capacity counts "
                   "(Cap Enrl / Tot Enrl = 0), so fill ratio cannot be computed"),
        "remedy": ("add an enrollment export on the live form (experimental), or "
                   "use Option 1 with a workbook that has Cap/Tot/Wait counts"),
    },
    {
        "detector": "prerequisite_ordering",
        "status": "inert",
        "reason": ("prerequisites are blank (Program Mapper does not expose them); "
                   "the solver runs without ordering constraints"),
        "remedy": ("turn on 'Include prerequisites from eLumen' on the live form "
                   "(experimental)"),
    },
]


def _enrollment_detector_entries(*, source, matched, total):
    """Build the enrollment-gated detector's report entry (modality_mismatch).

    It flips to "active" ONLY when an enrollment export was joined AND the join
    matched >=1 section. Absent enrollment, or a zero-match join, keeps the
    honest inert reason. The activation is LABELED fixture-scoped — the
    live-schedule <-> IR (term, CRN) join is NOT validated on real data (no
    committed schedule (2268) + enrollment ({2248,2252}) fixture pair overlaps).

    under_supply is NOT built here: it fires live from the schedule's Waitlist
    status (engine.analyze + mapping's "Avail Status"), so it is no longer
    enrollment-gated — the IR export only sharpens it from breadth (sections
    waitlisted) to depth (Wait Tot headcount). modality_mismatch still needs the
    Cap/Tot fill ratio, so it remains the only enrollment-gated detector.

    ``total`` is the fetched-section count; it is surfaced alongside ``matched``
    as a matched/total ratio for honest match accounting (so the report shows how
    much of the fetched schedule the join actually covered, not just the raw hit
    count).
    """
    if source is None:
        # No enrollment input at all: keep the honest baseline inert entry.
        return [dict(d) for d in INERT_DETECTORS
                if d["detector"] == "modality_mismatch"]

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
                "total_sections": total,
                "match_ratio": round(matched / total, 4) if total else None,
                "label": active_note,
                "metric": "fill ratio < 0.55 (Tot Enrl / Cap Enrl)",
            },
        ]

    # Enrollment present but the join matched ZERO rows: stay INERT, honestly.
    zero_reason = (
        f"enrollment export {source!r} was loaded but the (term, CRN) join "
        f"matched 0 sections (live-schedule <-> IR fixtures disjoint: schedule "
        f"term 2268 vs enrollment terms {{2248, 2252}}, CRN sets disjoint), so "
        f"Cap/Tot stay 0 and the detector cannot fire")
    return [
        {
            "detector": "modality_mismatch",
            "status": "inert",
            "source": source,
            "matched_sections": 0,
            "total_sections": total,
            "match_ratio": 0.0 if total else None,
            "matched_sections_note": "join matched 0 sections",
            "reason": zero_reason,
            "remedy": ("supply an enrollment export whose (term, CRN) keys overlap "
                       "the fetched schedule (validated end-to-end only on a "
                       "self-consistent fixture set)"),
        },
    ]


def _prereq_detector_entry(*, source, results, live=False, coverage=None):
    """Build the prerequisite_ordering report entry.

    Flips to "active" when a prereq map was threaded in (``results`` is not
    None). Per-course it distinguishes exact-CNF courses from budget/fallback
    courses (the latter labeled *conservative-permissive, not exact*).

    Provenance label depends on ``live``:
      - live=False -> FIXTURE-ONLY (parsed from a committed self-defined fixture,
        not validated on real eLumen data).
      - live=True  -> a REAL eLumen source (itemType=Prerequisite only;
        coreqs/advisories excluded) with honest caveats: ToU / rate-limit /
        human-approval pending, and the eLumen<->schedule/Program-Mapper
        course-id join validated ONLY via the coverage report (normalized course
        ids, e.g. leading zeros stripped). The ``coverage`` dict is attached so
        consumers can audit that join.
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

    if live:
        label = ("REAL eLumen (live public catalog endpoint, "
                 "itemType=Prerequisite only; coreqs/advisories excluded). "
                 "NOT production-ready: ToU / rate-limit / human-approval review "
                 "PENDING. The eLumen<->schedule/Program-Mapper course-id join is "
                 "validated ONLY via the coverage report (normalized course ids, "
                 "e.g. leading zeros stripped: 'BIOLOGY 03' -> 'BIOLOGY 3').")
    else:
        label = ("FIXTURE-ONLY: the eLumen prereq slice is parsed from a "
                 "self-defined committed fixture and is NOT validated on real "
                 "eLumen data.")

    entry = {
        "detector": "prerequisite_ordering",
        "status": "active",
        "source": source,
        "live": bool(live),
        "label": label,
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
    if coverage is not None:
        entry["coverage"] = coverage
    return entry


def analyze_live(campus, terms, program_query, out_path, *, client=None,
                 enrollment_path=None, elumen_fixture=None, elumen_live=False,
                 enrollment_map=None, prereq_max_clauses=None):
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
      - ``elumen_live`` fetches the SAME shape of records from the REAL public
        eLumen catalog endpoint (sources.elumen_client) instead of a fixture, for
        the subjects the fetched sections cover, then runs the SAME
        elumen.build_prereq_map. A per-build coverage report is attached to
        report["elumen_coverage"]. Caveat: ToU / rate-limit / human-approval are
        PENDING; the eLumen<->catalog join is validated only via that coverage.
        If both ``elumen_live`` and ``elumen_fixture`` are given, LIVE wins,
        ``elumen_fixture`` is ignored, and a warning is recorded in
        report["warnings"].

    The enrollment/prereq enrichment runs HERE, never inside engine.run(): the
    engine still reads a finished workbook only. When ``elumen_live`` is set and
    no client is injected, this opens its own httpx.Client (mirroring build()),
    so all network IO stays OUTSIDE engine.run().
    """
    # Precedence: LIVE beats FIXTURE. Record a human-readable warning if both
    # were requested, so the report makes the override explicit (never silent).
    warnings = []
    if elumen_live and elumen_fixture is not None:
        warnings.append(
            "Both --elumen-live and --elumen-fixture were supplied; using LIVE "
            "eLumen and ignoring the fixture "
            f"({elumen_fixture!r})."
        )
        elumen_fixture = None

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
    if warnings:
        report["warnings"] = warnings

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

    # --- eLumen prereq map (outside engine.run) ------------------------------
    # Two mutually-exclusive sources feed the SAME elumen.build_prereq_map
    # (DNF->CNF unchanged). LIVE wins over FIXTURE (precedence enforced above).
    prereq_map = None
    prereq_results = None
    elumen_source = None
    elumen_live_active = False
    elumen_coverage = None
    if elumen_live:
        # The course-id universe we can JOIN a prereq onto: every section course
        # plus every program course, normalized the same way the catalog is.
        program_course_ids = {mapping._norm(c["course_id"])
                              for c in program["courses"]}
        known_course_ids = (
            {mapping._norm(r.get("course")) for r in sections}
            | program_course_ids
        )
        requested_course_ids = {c["course_id"] for c in program["courses"]}

        # Subjects to query eLumen for = ONLY the subjects of fetched sections
        # that belong to a PROGRAM course (not every subject in the campus
        # listing). The full multi-term listing spans ~50-60 subjects; querying
        # eLumen for all of them is slow and is a broad crawl of a real,
        # rate-limit/ToU-pending endpoint. Prereqs are only needed for the
        # program's own (gated) courses — their targets still resolve from the
        # already-built catalog — so this bounds the fetch to the handful of
        # subjects that actually matter (e.g. Biology -> BIOLOGY/CHEM/PHYSICS/...).
        subjects = sorted({
            str(r.get("subject")).strip()
            for r in sections
            if str(r.get("subject") or "").strip()
            and mapping._norm(r.get("course")) in program_course_ids
        })

        # Network IO stays OUTSIDE engine.run: reuse an injected client, else
        # open + own one (mirrors build()'s pattern). A per-build cache dedupes
        # repeated subjects; the client's throttle + bounded backoff retry apply
        # automatically (see sources.elumen_client guardrails). This fetch is
        # BOUNDED to the program's own subjects — never a broad background crawl.
        elumen_cache = {}
        if client is not None:
            records, _fetched = elumen_client.fetch_prereq_records(
                campus, subjects, client=client, cache=elumen_cache)
        else:
            with httpx.Client(timeout=30.0) as owned:
                records, _fetched = elumen_client.fetch_prereq_records(
                    campus, subjects, client=owned, cache=elumen_cache)

        kwargs = {}
        if prereq_max_clauses is not None:
            kwargs["max_clauses"] = prereq_max_clauses
        prereq_map, prereq_results = elumen.build_prereq_map(records, **kwargs)
        elumen_coverage = elumen_client.compute_coverage(
            records, known_course_ids, requested_course_ids=requested_course_ids)
        report["elumen_coverage"] = elumen_coverage
        elumen_source = f"eLumen live: {elumen_client.tenant_for(campus)}"
        elumen_live_active = True
    elif elumen_fixture is not None:
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
        + [_prereq_detector_entry(source=elumen_source, results=prereq_results,
                                  live=elumen_live_active,
                                  coverage=elumen_coverage)]
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
    # The NOTE must mirror the per-detector status in the structured report
    # printed immediately below: claim INERT only for detectors that are still
    # inert this run, and ACTIVE (fixture-scoped/fixture-only) for ones the m7
    # enrichment flipped on — never a hardcoded "all inert" line, which would
    # contradict the JSON report on any --enrollment / --elumen-fixture run.
    _BANNER_LINES = {
        ("modality_mismatch", "inert"): (
            "Cap/Tot = 0 -> modality_mismatch INERT (need the IR PeopleSoft "
            "enrollment export, PRD M4)."),
        ("modality_mismatch", "active"): (
            "modality_mismatch ACTIVE (fixture-scoped: live-schedule <-> IR join "
            "not validated on real data)."),
        ("prerequisite_ordering", "inert"): (
            "Prerequisites blank (need eLumen) -> solver runs without ordering "
            "constraints."),
        # ("prerequisite_ordering", "active") is handled below by provenance
        # (live vs fixture): a static "fixture-only" line here would mislabel an
        # --elumen-live run, so it is intentionally NOT in this dict.
    }
    for d in report["inert_detectors"]:
        if d["detector"] == "prerequisite_ordering" and d["status"] == "active":
            # The ACTIVE prereq note must reflect the real provenance; a hardcoded
            # "fixture-only" line would contradict the JSON detector on a live run.
            if d.get("live"):
                line = ("prerequisite_ordering ACTIVE (REAL eLumen: live public "
                        "catalog, itemType=Prerequisite only; ToU/rate-limit/"
                        "human-approval PENDING, join validated only via coverage "
                        "report) -> solver enforces ordering constraints.")
            else:
                line = ("prerequisite_ordering ACTIVE (fixture-only: eLumen prereq "
                        "CNF threaded; not validated on real eLumen data) -> solver "
                        "enforces ordering constraints.")
        else:
            line = _BANNER_LINES.get((d["detector"], d["status"]))
        if line is not None:
            print(f"NOTE: {line}")


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
    ap.add_argument(
        "--elumen-live", action="store_true", dest="elumen_live",
        help=("fetch prerequisites from the REAL public eLumen catalog endpoint "
              "for the campus tenant (network required; itemType=Prerequisite "
              "only, coreqs/advisories excluded). NO OVERCLAIMING: ToU / "
              "rate-limit / human-approval review are PENDING, so this is "
              "best-effort and NOT production-ready; the eLumen<->catalog join "
              "is validated only via the coverage report. If --elumen-fixture is "
              "also given, --elumen-live wins."))
    args = ap.parse_args()
    terms = [int(t) for t in args.terms.split(",") if t.strip()]

    report = analyze_live(args.campus, terms, args.program, args.out,
                          enrollment_path=args.enrollment,
                          elumen_fixture=args.elumen_fixture,
                          elumen_live=args.elumen_live)
    if report["error"]:
        _print_banner(report)
        raise SystemExit(1)

    _print_banner(report)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
