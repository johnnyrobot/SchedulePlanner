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
    These paths carry honest caveats: the enrollment export is read by the
    tolerant enrollment_ir adapter (real LACCD CSV/xlsx, term auto-normalized) and
    its (term, CRN) join is validated end-to-end, but it lands only when the
    export's term overlaps the fetched term (the live API serves only current
    terms); the --elumen-fixture prereq slice is fixture-only (not
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
from collections import namedtuple
from types import SimpleNamespace

import httpx

import buildability
import corequisite_availability
import cross_program_bottleneck
import demand_supply
import equity_exposure
import evidence
import gateway_momentum
import grid_pressure
import engine
from sources import (assist, catalog_ge, course_master, elumen, elumen_client,
                     enrollment, enrollment_ir, ge, live_demand, mapping,
                     pdf_loader, program_lists, program_mapper, schedule,
                     schedule_import, timeblocks)
# Imported under an alias so the module name does not shadow the loaded ``facility``
# room-map that analyze_live / analyze_import pass around as a local variable.
from sources import facility as facilities
from sources.http import SourceDataError


def build(campus, terms, program_query, *, client=None,
          program_id=None, program_title="", program_award=""):
    """Fetch sections + program from the live sources (or an injected client).

    Network IO lives HERE, outside engine.run(). The m7 enrichment (enrollment
    join + eLumen prereq map) is applied downstream in analyze_live on the raw
    records this returns, BEFORE the workbook write — never inside engine.run().

    ``program_id`` resolves the program by its EXACT masterRecordId (passing the
    known ``program_title``/``program_award`` as display metadata) instead of by
    title query, so duplicate-titled programs are individually addressable.
    """
    def _fetch_program(c):
        if program_id is not None:
            return program_mapper.fetch_program_by_id(
                campus, program_id, title=program_title, award=program_award, client=c)
        return program_mapper.fetch_program(campus, program_query, client=c)

    if client is not None:
        sections = schedule.fetch_sections(campus, terms, client=client)
        return sections, _fetch_program(client)
    with httpx.Client(timeout=30.0) as owned:
        sections = schedule.fetch_sections(campus, terms, client=owned)
        return sections, _fetch_program(owned)


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


def _fmt_secs(s):
    """Format a seconds value without a trailing '.0' for whole numbers (90.0 -> '90')."""
    try:
        f = float(s)
    except (TypeError, ValueError):
        return str(s)
    return str(int(f)) if f.is_integer() else str(f)


def _truncation_phrase(ft):
    """Human phrase for an eLumen fetch that hit its aggregate wall-clock cap.

    Honest in BOTH truncation shapes: when whole subjects were skipped it names
    the fetched/total count; when the clock crossed mid-way through the final
    subject (so fetched == total but coverage is still partial) it says so
    instead of the self-contradictory "after N/N subjects".
    """
    cap = _fmt_secs(ft.get("deadline_seconds"))
    fetched, total = ft.get("queries_fetched"), ft.get("queries_total")
    if fetched is not None and total is not None and fetched < total:
        where = f"after {fetched}/{total} subjects"
    else:
        where = "while fetching the last subject"
    return f"hit its {cap}s time cap {where}"


def _enrollment_detector_entries(*, source, matched, total, inline=False):
    """Build the enrollment-gated detector's report entry (modality_mismatch).

    It flips to "active" ONLY when an enrollment export was joined AND the join
    matched >=1 section. Absent enrollment, or a zero-match join, keeps the
    honest inert reason. The IR adapter (sources/enrollment_ir) now ingests real
    LACCD/PeopleSoft exports (CSV + xlsx, term auto-normalized) and the join is
    validated end-to-end; the remaining real-world caveat is that the live
    schedule API serves only CURRENT terms, so an uploaded export matches a live
    fetch only when its term equals the fetched term.

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
        # The inline-map path (offline tests) is a hand-keyed SYNTHETIC join, so
        # it stays honestly fixture-scoped; a real uploaded file goes through the
        # enrollment_ir adapter and is a validated real-export join; the offline
        # schedule-import path carries Cap/Tot/Wait in the export ITSELF (no join).
        synthetic = "synthetic" in str(source).lower()
        if inline:
            active_note = ("the schedule export carries Cap/Tot/Wait enrollment "
                           "columns directly (no join needed), so the fill ratio is "
                           "computed from the export's own counts. Caveat: "
                           "combined-section caps may overstate, and under_supply "
                           "stays a demand proxy — never completion causation.")
        elif synthetic:
            active_note = ("FIXTURE-SCOPED: activated via a self-consistent / "
                           "hand-keyed enrollment map (synthetic key) — NOT "
                           "validated on real data; a real upload uses the "
                           "enrollment_ir adapter joined on (term, CRN).")
        else:
            active_note = ("the enrollment export was ingested by the IR adapter "
                           "(real LACCD/PeopleSoft CSV or xlsx; term "
                           "auto-normalized) and joined to the fetched schedule on "
                           "(term, CRN). Caveat: combined-section caps may "
                           "overstate, and under_supply stays a demand proxy — "
                           "never completion causation.")
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
        f"enrollment export {source!r} was ingested but the (term, CRN) join "
        f"matched 0 sections — the export's term(s) do not overlap the fetched "
        f"schedule term(s) (the live schedule API serves only CURRENT terms, so a "
        f"historical IR export will not match a live fetch), so Cap/Tot stay 0 and "
        f"the detector cannot fire")
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
            "remedy": ("supply an enrollment export whose term matches the fetched "
                       "term (a current-term IR export for a live build), or use "
                       "Option 1 with a workbook that already carries Cap/Tot/Wait"),
        },
    ]


def _prereq_detector_entry(*, source, results, live=False, coverage=None):
    """Build the prerequisite_ordering report entry.

    Flips to "active" only when the threaded prereq map applies >=1 ACTUAL
    ordering constraint — a course with a hard prerequisite, or a flagged
    fallback. A map containing only advisory/co-requisite courses (every CNF
    empty) reports INERT, because the solver has zero constraints. The
    ``prereq_summary`` splits ``fetched_count`` (every requisite-bearing course
    fetched, context only — many carry just advisories) from
    ``with_hard_prereq_count`` (courses that actually have a hard prerequisite)
    and ``fallback_count`` (budget-exceeded, conservative-permissive).

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

    # Split the threaded prereq map into honest buckets. A FETCHED course may
    # carry only an advisory / co-requisite, which the itemType filter drops to an
    # EMPTY CNF — requisite-bearing, but NOT a hard ordering constraint:
    #   - hard:     exact CNF with a non-empty clause set (a real prerequisite)
    #   - fallback: budget-exceeded -> a flagged conservative-permissive prereq
    #   - (neither) exact but empty CNF -> fetched, no hard prereq (advisory only)
    # Only hard + fallback are constraints the solver actually enforces.
    fetched_count = len(results)
    hard, fallback = [], []
    for cid, res in sorted(results.items()):
        if not res.exact:
            fallback.append({"course": cid, "reason": res.fallback_reason})
        elif res.cnf_string:
            hard.append(cid)

    # Did the live eLumen fetch hit its aggregate wall-clock cap? If so, coverage
    # may be partial — surface that honestly wherever this detector is described.
    ft = (coverage or {}).get("fetch_truncated") or {}
    trunc_note = ""
    if ft.get("exceeded"):
        trunc_note = (f" NOTE: the eLumen fetch {_truncation_phrase(ft)}, so "
                      "prerequisite coverage may be partial.")

    # A prereq map was threaded in, but it applied ZERO ordering constraints —
    # e.g. eLumen returned no HARD prerequisites for this program's courses (only
    # advisories / co-requisites, which the itemType filter excludes), or none of
    # the fetched prereqs keyed onto a program course. For the solver that is
    # IDENTICAL to having no prereq data at all, so report INERT honestly rather
    # than a misleading green "active" with zero constraints — mirroring the
    # enrollment panel's honest "joined 0 sections — counts not applied".
    if not hard and not fallback:
        provenance = "Live eLumen" if live else "The eLumen fixture"
        if ft.get("exceeded"):
            # Truncated before any hard prereq was collected: the honest reason is
            # the time cap, NOT "this program has no prerequisites".
            reason = (f"the eLumen fetch {_truncation_phrase(ft)} before any hard "
                      "prerequisites were collected, so the solver ran without "
                      "prerequisite ordering")
        elif fetched_count:
            # Requisite-bearing courses WERE fetched, but none carry a hard
            # prerequisite (advisories / co-requisites only) -- honest, specific.
            reason = (f"{provenance} returned {fetched_count} requisite-bearing "
                      "course(s) for this program's subjects but NONE carried a "
                      "hard prerequisite (only advisories / co-requisites, which "
                      "don't constrain ordering), so the solver ran without "
                      "prerequisite ordering")
        else:
            reason = (f"{provenance} returned no prerequisite records for this "
                      "program's courses, so the solver ran without prerequisite "
                      "ordering")
        entry = {
            "detector": "prerequisite_ordering",
            "status": "inert",
            "source": source,
            "live": bool(live),
            "reason": reason,
            "remedy": ("none needed if these courses truly have no prerequisites; "
                       "otherwise check the eLumen coverage report for the "
                       "course-id join"),
            "prereq_summary": {"fetched_count": fetched_count,
                               "with_hard_prereq_count": 0, "fallback_count": 0,
                               "with_hard_prereq_courses": [], "fallback_courses": []},
        }
        if coverage is not None:
            entry["coverage"] = coverage
        return entry

    if live:
        label = ("REAL eLumen (live public catalog endpoint, "
                 "itemType=Prerequisite only; coreqs/advisories excluded). "
                 "NOT production-ready: ToU / rate-limit / human-approval review "
                 "PENDING. The eLumen<->schedule/Program-Mapper course-id join is "
                 "validated ONLY via the coverage report (normalized course ids, "
                 "e.g. leading zeros stripped: 'BIOLOGY 03' -> 'BIOLOGY 3')." + trunc_note)
    else:
        label = ("FIXTURE-ONLY: the eLumen prereq slice is parsed from a "
                 "self-defined committed fixture and is NOT validated on real "
                 "eLumen data." + trunc_note)

    entry = {
        "detector": "prerequisite_ordering",
        "status": "active",
        "source": source,
        "live": bool(live),
        "label": label,
        "prereq_summary": {
            "fetched_count": fetched_count,
            "with_hard_prereq_count": len(hard),
            "fallback_count": len(fallback),
            "with_hard_prereq_courses": hard,
            "fallback_courses": fallback,
            "fallback_label": ("budget/fallback courses are conservative-permissive "
                               "(an UNDER-approximate union clause), NOT exact — "
                               "ordering for those courses is relaxed but flagged"),
        },
    }
    if coverage is not None:
        entry["coverage"] = coverage
    return entry


def _ge_detector_entry(coverage):
    """Detector-style entry so _print_banner + UI render GE like the others."""
    if not coverage or not coverage.get("requested"):
        return {"detector": "ge_scheduling", "status": "inert",
                "reason": "no transfer GE goal selected; major courses only."}
    areas = coverage.get("areas", [])
    return {
        "detector": "ge_scheduling", "status": "active",
        "pattern": coverage.get("pattern"),
        "academic_year": coverage.get("academic_year"),
        "assist_status": coverage.get("assist_status"),
        "label": coverage.get("assist_caveat", ""),
        # Content-review gate: an unreviewed pattern (blank reviewed_by) is a DRAFT.
        # The warning self-clears once a qualified reviewer signs the pattern file.
        "reviewed": coverage.get("reviewed", False),
        "draft_warning": coverage.get("draft_warning", ""),
        "summary": {
            "areas_total": len(areas),
            "concrete": sum(1 for a in areas if a["resolution"] == "concrete"),
            "reserved": sum(1 for a in areas if a["resolution"] == "reserve"),
            "shared_with_major": len(coverage.get("shared_with_major", [])),
            "flagged": sum(1 for a in areas if a.get("flags")),
        },
    }


_GE_CAVEAT = ("ASSIST is public but ToU/rate-limit/human-approval review is "
              "PENDING; bounded fetch (one call per pattern per college per "
              "year); best-effort, not production-ready.")

_LOCAL_GE_NAME = "local Associate-degree (AA/AS) GE"
_LOCAL_GE_CAVEAT = ("Parsed from the catalog PDF you provided (OpenDataLoader, "
                    "on-device). A planning aid, not an official articulation — "
                    "confirm GE areas and eligible courses with a counselor.")


def _resolve_local_ge(sections, program, *, catalog_pdf=None, odl_json=None):
    """Build (ge_rows, ge_coverage) for the LOCAL AA/AS GE goal from a catalog PDF.

    Sources the (pattern, area->courses) from the catalog via OpenDataLoader +
    catalog_ge, then reuses the SAME ge.resolve path the transfer goals use; the
    result is always DRAFT-gated (a parsed pattern is never pre-reviewed).
    Network/JVM stays here (outside engine.run). Never raises — a parse failure or
    an empty parse degrades to an honest all-reserve coverage with the error
    surfaced (and no ge_requirements sheet, so nothing is silently scheduled).
    """
    coverage = {"requested": True, "pattern": "local", "source": "catalog",
                "assist_status": "catalog", "assist_caveat": _LOCAL_GE_CAVEAT,
                "academic_year": None, "areas": [], "shared_with_major": [],
                "unknown_areas": [], "cross_system_areas": [],
                "reviewed": False,
                "draft_warning": _ge_draft_warning({"display_name": _LOCAL_GE_NAME})}
    try:
        odl = odl_json if odl_json is not None else pdf_loader.extract(catalog_pdf)
        cat_pattern, area_courses, cat_diag = catalog_ge.extract_local_ge(odl)
    except Exception as exc:  # noqa: BLE001 - honest degradation, never raise
        coverage["error"] = f"catalog parse failed: {type(exc).__name__}: {exc}"
        coverage["catalog_diagnostics"] = {"section_found": False, "area_count": 0,
                                           "total_courses": 0, "areas": [],
                                           "notes": [coverage["error"]]}
        return None, coverage
    coverage["catalog_diagnostics"] = cat_diag
    if not cat_pattern.get("areas"):
        notes = cat_diag.get("notes") or ["no GE areas were parsed from the catalog PDF"]
        coverage["error"] = notes[0]
        return None, coverage
    offered = {mapping._norm(r["course"]) for r in sections}
    ge_rows, resolved = ge.resolve(cat_pattern, area_courses, offered, program)
    coverage["areas"] = resolved["areas"]
    coverage["shared_with_major"] = resolved["shared_with_major"]
    coverage["unknown_areas"] = resolved["unknown_areas"]
    coverage["cross_system_areas"] = resolved.get("cross_system_areas", [])
    return ge_rows, coverage


def _ge_draft_warning(pattern):
    """Plain-language DRAFT notice for an unreviewed GE pattern (CLI + UI share it).

    Fired whenever ge.is_reviewed(pattern) is False — i.e. the pattern file's
    per-area counts/units have NOT been verified against the official standard by
    a qualified reviewer. Kept jargon-free so it reads the same in the banner and
    the desktop panel; the wording deliberately frames the GE plan as a planning
    aid, not an authoritative articulation.
    """
    name = pattern.get("display_name") or str(pattern.get("pattern") or "this")
    return (f"Draft — unverified: the {name} requirement counts and units in this "
            "build have not been verified against the official standard by a "
            "qualified reviewer. Use as a planning aid only — confirm with a "
            "counselor before relying on it.")


def _program_subjects(sections, program):
    """Subjects to query eLumen for: the subjects of fetched sections that
    belong to a PROGRAM course — NOT every subject in the campus listing.

    The full multi-term LAMC listing spans ~50-60 subjects; querying eLumen for
    all of them is slow and is a broad crawl of a real, rate-limit/ToU-pending
    endpoint. Prereqs are only needed for the program's own (gated) courses (their
    targets still resolve from the already-built catalog), so this bounds the
    fetch to the handful of subjects that matter (e.g. Biology -> BIOLOGY / CHEM /
    PHYSICS / ANATOMY / ...).

    The section<->program-course match is keyed with eLumen's OWN normalizer
    (``elumen_client.normalize_course_code``), NOT ``mapping._norm``: the schedule
    and Program Mapper format catalog numbers independently ("BIOLOGY 3" vs
    "BIOLOGY 03") and ``_norm`` does not strip leading zeros, so a ``_norm``-keyed
    filter could silently exclude a gated course's subject and never fetch its
    prereq. ``normalize_course_code`` collapses "BIOLOGY 03" -> "BIOLOGY 3" on both
    sides, matching how the eLumen<->catalog join already keys.
    """
    program_keys = {elumen_client.normalize_course_code(c["course_id"])
                    for c in program["courses"]}
    return sorted({
        str(r.get("subject")).strip()
        for r in sections
        if str(r.get("subject") or "").strip()
        and elumen_client.normalize_course_code(r.get("course")) in program_keys
    })


def _section_meetings_by_course(sections):
    """Map normalized course id -> list of meeting-blocks (one entry per offered section)."""
    by_course = {}
    for r in sections:
        cid = mapping._norm(r.get("course", ""))
        if not cid:
            continue
        by_course.setdefault(cid, []).append(
            timeblocks.parse_meeting(r.get("days", ""), r.get("times", "")))
    return by_course


def _time_block_collisions(sections, program, results):
    """Detect class-meeting-time conflicts among a program's required courses.

    Two non-redundant levels (uses only data already fetched — section days/times):
      - PAIRWISE HARD: two required courses whose EVERY section overlaps, so a student
        literally cannot take both as scheduled (program-wide, plan-independent).
      - JOINT TERM: the courses the solver placed together in one cohort-term have no
        conflict-free section combination AND it is not reducible to a single hard pair
        (a genuine 3+-way clash); reported only when no pair in that term is already hard,
        so findings never double-count.

    Returns a JSON-serializable list of finding dicts, each with a human ``summary``.
    """
    by_course = _section_meetings_by_course(sections)
    required = [mapping._norm(c["course_id"]) for c in (program or {}).get("courses", [])]
    required = [c for c in required if c in by_course]  # only courses actually offered

    findings, hard_pairs = [], set()
    for i in range(len(required)):
        for j in range(i + 1, len(required)):
            a, b = required[i], required[j]
            if timeblocks.pairwise_hard_conflict(by_course[a], by_course[b]):
                key = tuple(sorted((a, b)))
                if key not in hard_pairs:
                    hard_pairs.add(key)
                    findings.append({
                        "kind": "pair", "courses": list(key),
                        "summary": (f"{key[0]} & {key[1]} — every offered section overlaps; "
                                    "a student cannot take both as scheduled"),
                    })

    for _pcode, entry in (results or {}).get("programs", {}).items():
        for ck, res in (entry.get("cohorts") or {}).items():
            if not res or not res.get("plan"):
                continue
            label = engine.COHORTS.get(ck, {}).get("label", ck)
            for term, items in res["plan"].items():
                cts = {cid: by_course[cid] for cid in (mapping._norm(it) for it in items)
                       if cid in by_course}
                if len(cts) < 2:
                    continue
                term_ids = sorted(cts)
                # skip terms already explained by a reported hard pair (no dup noise)
                if any(tuple(sorted((x, y))) in hard_pairs
                       for k, x in enumerate(term_ids) for y in term_ids[k + 1:]):
                    continue
                feasible, culprits = timeblocks.feasible_selection(cts)
                if not feasible:
                    findings.append({
                        "kind": "term", "cohort": ck, "term": int(term),
                        "courses": culprits,
                        "summary": (f"{label} term {term}: {', '.join(culprits)} can't all be "
                                    "scheduled together without a time conflict"),
                    })
    return findings


def _time_block_detector_entry(sections, collisions):
    """Honest active/inert entry for the time-block conflict detector.

    Active whenever any fetched section carries a parseable meeting time (the live schedule
    does); inert only if no meeting data is present (e.g. an all-async/TBA fetch).
    """
    has_meeting = any(timeblocks.parse_meeting(r.get("days", ""), r.get("times", ""))
                      for r in sections)
    if not has_meeting:
        return {
            "detector": "time_block_conflict", "status": "inert",
            "reason": ("no section in the fetched terms has a scheduled meeting time "
                       "(all async/TBA), so time-of-day conflicts cannot be computed"),
        }
    return {
        "detector": "time_block_conflict", "status": "active", "found": len(collisions),
        "reason": ("checks whether a program's required courses have a conflict-free set of "
                   "meeting times each term, using the live schedule's days/times"),
    }


def _off_grid_sections(sections):
    """Sections whose start time is off the college's standardized time-block grid
    (deduped by course + days + times + term). Async/TBA sections and term lengths
    without a known grid are skipped (timeblocks.on_grid fails open)."""
    findings, seen = [], set()
    for r in sections:
        meeting = timeblocks.parse_meeting(r.get("days", ""), r.get("times", ""))
        if not meeting or timeblocks.on_grid(r.get("term"), meeting):
            continue
        cid = mapping._norm(r.get("course", ""))
        key = (cid, r.get("days", ""), r.get("times", ""), r.get("term"))
        if key in seen:
            continue
        seen.add(key)
        findings.append({
            "course": cid, "term": r.get("term"),
            "days": r.get("days", ""), "times": r.get("times", ""),
            "summary": (f"{cid} ({r.get('days', '')} {r.get('times', '')}) starts off the "
                        "standard time-block grid"),
        })
    return findings


def _room_collisions(sections):
    """Physical room double-bookings: two DIFFERENT sections placed in the same
    room + term at overlapping meeting times.

    Like ``_time_block_collisions``, this reads only the raw section days/times/room
    (which the workbook schema drops) and runs OUTSIDE engine.run. It joins on
    ``Facil ID`` when present (imported exports) and falls back to the room label
    (the live fetch exposes a label, not an id). Async/TBA and online sections (no
    physical slot) never collide; combined cross-lists sharing a ``Comb Sects ID``
    are the SAME physical meeting under multiple course numbers, so they are
    excluded; two rows of the same section (an undeduped meeting pattern) are too.
    """
    by_room = {}
    for r in sections:
        meeting = timeblocks.parse_meeting(r.get("days", ""), r.get("times", ""))
        if not meeting:
            continue  # async / TBA: no physical time slot to clash over
        if facilities.is_physical_room(r.get("facil_id", "")):
            room_key = facilities.norm_facil(r.get("facil_id", ""))
        else:
            label = mapping._norm(str(r.get("room", "") or ""))
            if not label or "ONLINE" in label or label == "TBA":
                continue
            room_key = label
        by_room.setdefault((r.get("term"), room_key), []).append({
            "course": mapping._norm(r.get("course", "")),
            "class_nbr": str(r.get("class_nbr", "") or ""),
            "comb": str(r.get("Comb Sects ID", "") or "").strip(),
            "meeting": meeting,
        })

    findings, seen = [], set()
    for (term, room_key), secs in sorted(
            by_room.items(), key=lambda kv: (str(kv[0][0]), kv[0][1])):
        for i in range(len(secs)):
            for j in range(i + 1, len(secs)):
                a, b = secs[i], secs[j]
                if a["class_nbr"] and a["class_nbr"] == b["class_nbr"]:
                    continue  # same physical section (undeduped meeting-pattern row)
                if a["comb"] and a["comb"] == b["comb"]:
                    continue  # combined cross-list shares one physical room slot
                if not timeblocks.meetings_overlap(a["meeting"], b["meeting"]):
                    continue
                cls = tuple(sorted((a["class_nbr"], b["class_nbr"])))
                key = (str(term), room_key, cls)
                if key in seen:
                    continue
                seen.add(key)
                pair = sorted({a["course"], b["course"]})
                who = " & ".join(pair) if len(pair) > 1 else pair[0]
                findings.append({
                    "kind": "double_book",
                    "term": int(term) if term not in (None, "") else None,
                    "room": room_key,
                    "courses": pair,
                    "class_nbrs": list(cls),
                    "summary": (f"Room {room_key}"
                                + (f" (term {term})" if term not in (None, "") else "")
                                + f": {who} booked into the same room at overlapping "
                                f"times (classes {', '.join(cls)})"),
                })
    return findings


def _room_capacity_findings(sections, facility):
    """Sections enrolled beyond their assigned room's seat capacity.

    Meaningful only with BOTH a facility map (Facil ID -> capacity) AND per-section
    ``Tot Enrl`` (an enrollment-bearing export). Returns ``[]`` when either is
    absent — not an error, just nothing to say. Deterministic order.
    """
    if not facility:
        return []
    findings, seen = [], set()
    for r in sections:
        meta = facility.get(facilities.norm_facil(r.get("facil_id", "")))
        if not meta or not meta.get("capacity"):
            continue  # capacity None / 0 = unset seat count, not a real over-enroll
        cap = meta["capacity"]
        try:
            tot = int(r.get("Tot Enrl"))
        except (TypeError, ValueError):
            continue
        if tot <= cap:
            continue
        course = mapping._norm(r.get("course", ""))
        cls = str(r.get("class_nbr", "") or "")
        facil = facilities.norm_facil(r.get("facil_id", ""))
        key = (str(r.get("term")), facil, cls)
        if key in seen:
            continue
        seen.add(key)
        findings.append({
            "kind": "over_capacity", "course": course, "class_nbr": cls,
            "term": int(r["term"]) if r.get("term") not in (None, "") else None,
            "room": facil, "capacity": cap, "enrolled": tot,
            "summary": (f"{course} (class {cls}) has {tot} enrolled in room {facil} "
                        f"(seats {cap}) — over capacity by {tot - cap}"),
        })
    findings.sort(key=lambda f: (str(f["term"]), f["room"], f["course"], f["class_nbr"]))
    return findings


def _lab_pool_stats(sections, facility):
    """Descriptive lab-pool use: how many scarce lab rooms (LAB/CMLB) the sections
    actually occupy, of the total in the facility table. Honest context for the
    detector entry, NOT an alarm. ``None`` when no facility table / no labs."""
    if not facility:
        return None
    total_labs = sum(1 for m in facility.values() if facilities.is_lab(m))
    if not total_labs:
        return None
    in_use = {facilities.norm_facil(r.get("facil_id", "")) for r in sections
              if facilities.is_lab(facility.get(facilities.norm_facil(r.get("facil_id", ""))))}
    return {"total_labs": total_labs, "labs_in_use": len(in_use)}


def _room_detector_entry(sections, collisions, *, facility_used, capacity=None,
                         lab_stats=None):
    """Honest active/inert entry for the room detector (mirrors
    ``_time_block_detector_entry``).

    Active when >=1 section has both a meeting time and a physical room key (Facil
    ID or a non-online room label); inert when every section is
    online/roomless/async. The capacity sub-analysis reports active only when a
    facility table was supplied.
    """
    has_room = False
    for r in sections:
        if not timeblocks.parse_meeting(r.get("days", ""), r.get("times", "")):
            continue
        if facilities.is_physical_room(r.get("facil_id", "")):
            has_room = True
            break
        label = mapping._norm(str(r.get("room", "") or ""))
        if label and "ONLINE" not in label and label != "TBA":
            has_room = True
            break
    if not has_room:
        return {
            "detector": "room_conflict", "status": "inert",
            "reason": ("no fetched section has both a meeting time and a physical "
                       "room (all online/async/TBA), so room double-bookings cannot "
                       "be computed"),
        }
    entry = {
        "detector": "room_conflict", "status": "active", "found": len(collisions),
        "reason": ("checks whether two different sections are booked into the same "
                   "room at overlapping times (combined cross-lists excluded), from "
                   "the section room + days/times"),
    }
    if facility_used:
        entry["capacity"] = {
            "status": "active", "over_capacity_found": len(capacity or []),
            "note": ("sections enrolled beyond their assigned room's seat capacity, "
                     "joined on Facil ID against the facility table"),
        }
        if lab_stats:
            entry["capacity"]["lab_pool"] = lab_stats
    else:
        entry["capacity"] = {
            "status": "inert",
            "reason": ("no facility table supplied, so room capacity / lab-pool use "
                       "was not computed (the live fetch exposes a room label, not a "
                       "Facil ID + seats — supply the facility export on the offline "
                       "import path)"),
        }
    return entry


def _buildability_detector_entry(block):
    """Honest active/inert entry for the program-buildability audit (mirrors the
    other detector entries). Inert carries the audit's own reason."""
    if not block or block.get("status") != "active":
        return {
            "detector": "program_buildability", "status": "inert",
            "reason": ((block or {}).get("reason")
                       or "no program / no offered sections to audit"),
        }
    ge_active = any((p.get("ge") or {}).get("status") == "active"
                    for p in block.get("programs", []))
    reason = ("scores whether each program's required path is offered, "
              "time-conflict-free, on its recommended season, and seat-available "
              "in the audited terms — a structural-feasibility PROXY, not a "
              "measured completion rate")
    if ge_active:
        reason += ("; GE requirements fold into the denominator "
                   "(GE-inclusive score + signed major-only delta)")
    return {
        "detector": "program_buildability", "status": "active",
        "found": len(block.get("programs", [])), "reason": reason,
    }


def _bottleneck_detector_entry(block):
    """Honest active/inert entry for the cross-program bottleneck leaderboard
    (mirrors ``_buildability_detector_entry``). Inert carries the audit's own
    reason (e.g. no program-lists demand map on a bare live fetch)."""
    if not block or block.get("status") != "active":
        return {
            "detector": "program_bottleneck", "status": "inert",
            "remedy": ("supply a Program Course Lists export on the offline import "
                       "path so cross-program demand can be counted"),
            "reason": ((block or {}).get("reason")
                       or "no program-lists demand map / no offered sections"),
        }
    return {
        "detector": "program_bottleneck", "status": "active",
        "found": len(block.get("leaderboard", [])),
        "reason": ("ranks required courses by how many programs depend on each vs "
                   "how few sections / seats / lab rooms they have — a structural "
                   "supply-vs-demand PROXY, not a measured completion rate"),
    }


def _demand_supply_detector_entry(block):
    """Honest active/inert entry for the demand-vs-supply action list (F5;
    mirrors ``_bottleneck_detector_entry``). Inert carries the report's own
    reason (e.g. no seat counts on a bare live fetch)."""
    if not block or block.get("status") != "active":
        return {
            "detector": "demand_supply", "status": "inert",
            "remedy": ("upload an IR enrollment export (live path) or import a "
                       "schedule export that carries Cap/Tot/Wait counts"),
            "reason": ((block or {}).get("reason")
                       or "no seat counts to assess demand against"),
        }
    return {
        "detector": "demand_supply", "status": "active",
        "found": len(block.get("add_list", [])),
        "reason": ("ranks courses whose seats fall short of enrolled+waitlisted "
                   "demand into an 'add a section' list (waitlist counts only "
                   "when paired with high fill / a closed status); a structural "
                   "supply-vs-demand PROXY, not a measured completion rate"),
    }


def _grid_pressure_detector_entry(block):
    """Honest active/inert entry for the grid-conformance + morning-compression
    audit (mirrors ``_bottleneck_detector_entry``)."""
    if not block or block.get("status") != "active":
        return {
            "detector": "grid_pressure", "status": "inert",
            "reason": ((block or {}).get("reason")
                       or "no timed sections to analyze"),
        }
    return {
        "detector": "grid_pressure", "status": "active",
        "found": len(block.get("mutual_exclusions", [])),
        "reason": ("flags how concentrated required courses are in the 9 AM-1 PM "
                   "window and which morning-locked required-course pairs are "
                   "mutually exclusive — a structural PROXY, not a measured "
                   "completion rate"),
    }


def _equity_exposure_detector_entry(block):
    """Honest active/inert entry for the equity / archetype-exposure view (F6;
    mirrors ``_grid_pressure_detector_entry``). ``found`` counts collapsing
    (program, archetype) pairs."""
    if not block or block.get("status") != "active":
        return {
            "detector": "equity_exposure", "status": "inert",
            "reason": ((block or {}).get("reason")
                       or "no program / no sections / baseline audit inert"),
        }
    n_collapsed = sum(1 for a in block.get("archetypes", [])
                      for p in a.get("programs", []) if p.get("collapsed"))
    return {
        "detector": "equity_exposure", "status": "active",
        "found": n_collapsed,
        "reason": ("re-runs the buildability audit under evening-only / online-only "
                   "/ two-days-a-week windows and flags programs whose required path "
                   "collapses (becomes structurally unbuildable) under the "
                   "constraint — a structural exposure PROXY, not a measured equity "
                   "outcome"),
    }


def _gateway_momentum_detector_entry(block):
    """Honest active/inert entry for the first-year gateway-momentum detector (F8;
    mirrors ``_equity_exposure_detector_entry``). Inert carries the report's own
    reason + remedy (no sections, or neither English/Math gateway identifiable).
    ``found`` counts the gateways that are schedulable in the first-year window."""
    if not block or block.get("status") != "active":
        return {
            "detector": "gateway_momentum", "status": "inert",
            "remedy": ((block or {}).get("remedy")
                       or "supply a program whose GE requirements name a "
                          "transfer-level English/Math course, or a required "
                          "ENGL/MATH major course"),
            "reason": ((block or {}).get("reason")
                       or "no gateway course identifiable / no offered sections"),
        }
    found = sum(1 for k in ("english", "math")
                if (block.get(k) or {}).get("schedulable_year1"))
    return {
        "detector": "gateway_momentum", "status": "active",
        "found": found,
        "reason": ("checks whether each program's English-Composition (GE Area 1A) "
                   "and Math (Area 2) gateway course can be SCHEDULED in the first "
                   "year of the analyzed schedule — an OFFERING proxy, not a measured "
                   "completion rate; a gateway found via the ENGL/MATH subject "
                   "fallback is discipline-level, not verified transfer-level"),
    }


def _corequisite_availability_detector_entry(block):
    """Honest active/inert entry for the AB1705 corequisite co-availability detector
    (F9; mirrors ``_gateway_momentum_detector_entry``). Inert carries the report's
    own reason + remedy (no sections, no coreq linkage supplied — the default path,
    no gateway, or no corequisite for the gateway). ``found`` counts the identified
    gateways whose corequisite is co-offered in a first-year term."""
    if not block or block.get("status") != "active":
        return {
            "detector": "corequisite_availability", "status": "inert",
            "remedy": ((block or {}).get("remedy")
                       or "run with --elumen-live so the catalog corequisite "
                          "(itemType=Co-Requisite) leaves are captured"),
            "reason": ((block or {}).get("reason")
                       or "no corequisite linkage / no gateway / no offered sections"),
        }
    found = sum(1 for k in ("english", "math")
                if (block.get(k) or {}).get("co_offered_year1"))
    return {
        "detector": "corequisite_availability", "status": "active",
        "found": found,
        "reason": ("checks whether a transfer-level English (GE Area 1A) / Math "
                   "(Area 2) gateway's catalog corequisite (itemType=Co-Requisite) is "
                   "scheduled in the SAME first-year term — a co-OFFERING STRUCTURE "
                   "proxy, NOT a measured or causal outcome (per AB1705, direct "
                   "placement was the dominant lever; corequisite is one supported form)"),
    }


# Append-only registry of the feature-analysis detectors (F1+F4, F2, F5, F3, F6, F8, F9).
# Each entry pairs a results["analysis"] key with a compute fn(ctx) -> block and
# the block's inert-detector entry helper; analyze_live runs them in ONE loop
# below the five heterogeneous detectors (modality/prereq/ge/time_block/room),
# preserving the exact compute + inject + append ORDER (the determinism gate is
# byte-identity on the canonicalized results + inert_detectors). To add a future
# feature, APPEND one AnalysisDetector here — do NOT reorder existing ones.
# Note: analysis_key ("bottlenecks") is intentionally DISTINCT from the detector
# key ("program_bottleneck", which lives inside the entry dict).
# F1 and F6 both honor ``c.by_design`` (FF2 intentional-gap exclusions): None/empty
# on the live path (no Notes column) -> byte-identical to the pre-FF2 output.
AnalysisDetector = namedtuple("AnalysisDetector", "analysis_key compute entry")
ANALYSIS_DETECTORS = (
    AnalysisDetector(
        "buildability",
        lambda c: buildability.buildability_report(
            [c.program], c.sections, ge_coverage=c.ge_coverage,
            active_courses=c.active_courses, by_design=c.by_design),
        _buildability_detector_entry),
    AnalysisDetector(
        "bottlenecks",
        lambda c: cross_program_bottleneck.bottleneck_report(
            c.program_demand, c.sections, c.facility),
        _bottleneck_detector_entry),
    AnalysisDetector(
        "demand_supply",
        lambda c: demand_supply.demand_supply_report(
            c.sections, program_demand=c.program_demand, facility=c.facility),
        _demand_supply_detector_entry),
    AnalysisDetector(
        "grid_pressure",
        lambda c: grid_pressure.grid_pressure_report(
            c.sections, program=c.program, program_demand=c.program_demand),
        _grid_pressure_detector_entry),
    AnalysisDetector(
        "equity_exposure",
        lambda c: equity_exposure.equity_exposure_report(
            [c.program], c.sections, ge_coverage=c.ge_coverage,
            active_courses=c.active_courses, by_design=c.by_design),
        _equity_exposure_detector_entry),
    AnalysisDetector(
        "gateway_momentum",
        lambda c: gateway_momentum.gateway_momentum_report(
            c.sections, program=c.program),
        _gateway_momentum_detector_entry),
    AnalysisDetector(
        "corequisite_availability",
        lambda c: corequisite_availability.corequisite_availability_report(
            c.sections, program=c.program, coreq_map=c.coreq_map),
        _corequisite_availability_detector_entry),
)


def analyze_live(campus, terms, program_query, out_path, *, client=None,
                 enrollment_path=None, elumen_fixture=None, elumen_live=False,
                 enrollment_map=None, prereq_max_clauses=None,
                 transfer_goal="none", assist_year_id=None, ge_pattern_path=None,
                 program_id=None, program_title="", program_award="",
                 assist_areas=None, elumen_cache=None,
                 catalog_pdf=None, odl_json=None, facility=None,
                 active_courses=None, program_demand=None,
                 demand_program_ids=None,
                 sections_override=None, program_override=None, by_design=None,
                 elumen_coreq=None):
    """Run the full live pipeline and return a structured, JSON-serializable report.

    The report carries the reconciliation (matched/unmatched program courses)
    and the inert-detector notes as machine-readable fields so a UI can render
    them, in addition to the human banner main() prints.

    m7 enrichment (ALL outside engine.run, on the raw records, before the write):
      - ``enrollment_path`` loads an IR PeopleSoft export via the tolerant
        ``enrollment_ir.load_ir_export`` adapter (real LACCD CSV/xlsx shapes, term
        auto-normalized, meeting-pattern deduped) and joins its counts onto the
        fetched sections via enrollment.enrich_sections; ``enrollment_map``
        supplies a ready-made join dict instead (offline tests). The join lands
        when the export's term overlaps the fetched term (the live API serves only
        current terms).
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

    ``demand_program_ids`` (FF4) OPTS IN to the bounded live cross-program demand
    fan-out: an explicit list of program-id dicts (typically a capped slice of
    ``program_mapper.get_all_programs``) whose program maps are fetched and
    aggregated into the ProgramDemand that activates F2's cross-program bottleneck
    leaderboard on the live path. Default off (None/empty -> no fan-out). Bounded:
    at most ``live_demand.DEFAULT_MAX_PROGRAMS`` are fetched. Fails open: an empty
    or fully-failing fan-out leaves the demand map falsy so F2 stays honestly
    inert. An explicit ``program_demand`` (offline import path) always wins over
    the fan-out. This fetch runs HERE in the data-gathering phase, never inside
    engine.run().
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

    # OFFLINE IMPORT path: a caller (analyze_import) may inject pre-read section
    # records + a program instead of fetching live, so the ENTIRE downstream
    # (enrollment join, GE, reconcile, write_workbook, engine.run, time-block /
    # room detectors) is reused byte-for-byte with zero network. sections_override
    # is None on the live path, so build() runs exactly as before.
    if sections_override is not None:
        sections, program = sections_override, program_override
    else:
        sections, program = build(campus, terms, program_query, client=client,
                                  program_id=program_id, program_title=program_title,
                                  program_award=program_award)

    # --- FF4: bounded live cross-program demand fan-out (outside engine.run) ---
    # F2's cross-program bottleneck leaderboard needs a programs-per-course demand
    # map, which a bare live fetch (one program per run) cannot carry -> F2 inert.
    # OPT-IN: only when the caller hands over an explicit, bounded list of program
    # ids (``demand_program_ids``, typically a capped slice of
    # program_mapper.get_all_programs) do we fan out across those program maps and
    # aggregate the SAME ProgramDemand shape the offline Program Course Lists loader
    # emits, so the F2 inject below activates with NO change. This is network IO, so
    # it lives HERE in the data-gathering phase, never inside engine.run(). An
    # explicit ``program_demand`` (offline import path) always wins; the fan-out
    # FAILS OPEN — an empty/failing fan-out leaves program_demand falsy and F2 stays
    # honestly inert (never a fabricated map).
    if program_demand is None and demand_program_ids:
        program_demand = live_demand.fan_out_demand(
            campus, demand_program_ids, client=client)

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
    # enrich_sections returns a NEW list; the join is MERGE-not-strip (FF3), so a
    # record carries "Cap Enrl" when EITHER the IR join matched it OR it already
    # carried native counts (an imported schedule export ships Cap/Tot/Wait in the
    # file). So this counter is sections-WITH-counts, not IR-matched-only: on a
    # bare live fetch the only counts come from IR (matched), but on the import+IR
    # path it includes IR-matched AND native-carried sections. 0 -> every count
    # stays 0 and the enrollment detectors are INERT (the honest contract).
    enrollment_source = None
    sections_with_counts = 0
    if enrollment_map is not None:
        enrollment_source = enrollment_path or "inline enrollment map (synthetic key)"
        sections = enrollment.enrich_sections(sections, enrollment_map)
        sections_with_counts = sum(1 for r in sections if "Cap Enrl" in r)
    elif enrollment_path is not None:
        enrollment_source = enrollment_path
        # Tolerant real-export adapter (CSV/xlsx, aliased columns, "2024 Fall"
        # term crosswalk, meeting-pattern dedup, cancelled/combined handling);
        # emits the same (term, CRN) -> counts map the strict reader does, so the
        # enrich_sections join below is unchanged.
        enrollment_data = enrollment_ir.load_ir_export(enrollment_path)
        sections = enrollment.enrich_sections(sections, enrollment_data)
        sections_with_counts = sum(1 for r in sections if "Cap Enrl" in r)

    # OFFLINE IMPORT: the schedule export itself carries Cap/Tot/Wait columns, so
    # the counts arrive inline on the records (no enrollment join). Reflect that
    # honestly so modality_mismatch reports ACTIVE-from-export, not "no counts".
    inline_counts = False
    if (enrollment_source is None and sections_override is not None):
        n_inline = sum(1 for r in sections if "Cap Enrl" in r)
        if n_inline:
            enrollment_source = "schedule export (counts in file)"
            sections_with_counts = n_inline
            inline_counts = True

    # --- eLumen prereq map (outside engine.run) ------------------------------
    # Two mutually-exclusive sources feed the SAME elumen.build_prereq_map
    # (DNF->CNF unchanged). LIVE wins over FIXTURE (precedence enforced above).
    prereq_map = None
    prereq_results = None
    elumen_source = None
    elumen_live_active = False
    elumen_coverage = None
    # F9 corequisite linkage: an injected map (offline/test) by default; the live
    # path DERIVES it from the SAME fetched records below (coreqs ride the same
    # fetch — itemType=Co-Requisite leaves the prereq filter drops). The FIXTURE
    # path carries no requisites tree, so it leaves this as-injected (usually None).
    coreq_map = elumen_coreq
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

        # Bound the eLumen fetch to the program's own subjects (leading-zero
        # tolerant — see _program_subjects), never the whole campus listing.
        subjects = _program_subjects(sections, program)

        # Network IO stays OUTSIDE engine.run: reuse an injected client, else
        # open + own one (mirrors build()'s pattern). A per-build cache dedupes
        # repeated subjects; the client's throttle + bounded backoff retry apply
        # automatically (see sources.elumen_client guardrails). This fetch is
        # BOUNDED to the program's own subjects — never a broad background crawl.
        # A caller may pass a shared elumen_cache so several analyze_live calls
        # (e.g. the same program across multiple transfer goals) fetch each eLumen
        # subject ONCE instead of re-crawling per goal — a deliberate kindness to
        # the rate-limit-pending endpoint.
        elumen_cache = elumen_cache if elumen_cache is not None else {}
        if client is not None:
            records, _fetched, fetch_status = elumen_client.fetch_prereq_records(
                campus, subjects, client=client, cache=elumen_cache)
        else:
            with httpx.Client(timeout=30.0) as owned:
                records, _fetched, fetch_status = elumen_client.fetch_prereq_records(
                    campus, subjects, client=owned, cache=elumen_cache)

        kwargs = {}
        if prereq_max_clauses is not None:
            kwargs["max_clauses"] = prereq_max_clauses
        prereq_map, prereq_results = elumen.build_prereq_map(records, **kwargs)
        # F9: derive the corequisite map from the SAME records (no extra fetch).
        coreq_map = elumen_client.corequisite_map(records)
        elumen_coverage = elumen_client.compute_coverage(
            records, known_course_ids, requested_course_ids=requested_course_ids)
        # Surface an aggregate wall-clock-cap truncation honestly: prerequisite
        # coverage may be partial (some subjects skipped). Never silent.
        if fetch_status.get("exceeded"):
            elumen_coverage["fetch_truncated"] = fetch_status
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

    # --- transfer-pattern GE (outside engine.run, on raw records, before write) ---
    ge_rows = None
    ge_coverage = None
    goal_l = str(transfer_goal).lower() if transfer_goal else "none"
    if goal_l == "local":
        # Local AA/AS GE: source (pattern, area->courses) from the catalog PDF,
        # then reuse the SAME resolver/solver/panel path as the transfer goals.
        ge_rows, ge_coverage = _resolve_local_ge(
            sections, program, catalog_pdf=catalog_pdf, odl_json=odl_json)
    elif goal_l != "none":
        ge_coverage = {"requested": True, "pattern": str(transfer_goal).lower(),
                       "assist_caveat": _GE_CAVEAT}
        try:
            pattern = ge.load_pattern(transfer_goal, path=ge_pattern_path)
        except ge.PatternError as exc:
            ge_coverage.update({"assist_status": "unavailable",
                                "areas": [], "shared_with_major": [],
                                "error": f"pattern unavailable: {exc}"})
            pattern = None
        if pattern is not None:
            # Content-review gate: surface a DRAFT warning whenever the pattern's
            # per-area counts haven't been signed off (reviewed_by blank). This is
            # independent of ASSIST availability — it's about the static rules.
            ge_coverage["reviewed"] = ge.is_reviewed(pattern)
            if not ge_coverage["reviewed"]:
                ge_coverage["draft_warning"] = _ge_draft_warning(pattern)
            offered = {mapping._norm(r["course"]) for r in sections}
            # A caller may inject a pre-fetched ASSIST area map (one fetch per goal
            # reused across many programs) so a large sweep makes 3 ASSIST calls,
            # not one per program — ASSIST's ToU note is "one call per pattern per
            # college per year", so this honours it. None -> fetch live as usual.
            if assist_areas is not None:
                # Injected map: trust ONLY a non-empty dict. An empty or malformed
                # injection is treated exactly like an ASSIST outage (honest
                # "unavailable" -> every area reserves) — never silently labelled
                # "ok" with no data, which would overclaim coverage.
                if isinstance(assist_areas, dict) and assist_areas:
                    year_id = assist_year_id
                    ge_coverage["assist_status"] = "ok"
                else:
                    assist_areas, year_id = {}, assist_year_id
                    ge_coverage["assist_status"] = "unavailable"
                    ge_coverage["error"] = "injected ASSIST map was empty or malformed"
            else:
                try:
                    assist_areas, year_id = assist.fetch_ge_courses(
                        campus, transfer_goal, academic_year_id=assist_year_id, client=client)
                    ge_coverage["assist_status"] = "ok"
                except Exception as exc:  # noqa: BLE001 - ASSIST down -> full reserve, honest
                    assist_areas, year_id = {}, assist_year_id
                    ge_coverage["assist_status"] = "unavailable"
                    ge_coverage["error"] = f"ASSIST unavailable: {type(exc).__name__}: {exc}"
            ge_coverage["academic_year"] = {"id": year_id}
            ge_rows, resolved = ge.resolve(pattern, assist_areas, offered, program)
            ge_coverage["areas"] = resolved["areas"]
            ge_coverage["shared_with_major"] = resolved["shared_with_major"]
            ge_coverage["unknown_areas"] = resolved["unknown_areas"]
            # Other-GE-system alias codes ASSIST bundled in and the resolver
            # ignored (e.g. CSU letter codes inside a Cal-GETC response) — surfaced
            # honestly so the drop in unknown_areas is explained, not hidden.
            ge_coverage["cross_system_areas"] = resolved.get("cross_system_areas", [])
    if ge_coverage is not None:
        report["ge_coverage"] = ge_coverage

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
                                     matched=sections_with_counts,
                                     total=len(sections), inline=inline_counts)
        + [_prereq_detector_entry(source=elumen_source, results=prereq_results,
                                  live=elumen_live_active,
                                  coverage=elumen_coverage)]
    )
    report["inert_detectors"].append(_ge_detector_entry(ge_coverage))

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    mapping.write_workbook(sections, program, out_path, prereqs=prereq_map,
                           pattern=(str(transfer_goal).lower()
                                    if transfer_goal and str(transfer_goal).lower() != "none"
                                    else None),
                           ge_rows=ge_rows)
    report["workbook"] = out_path
    report["results"] = engine.run(out_path)

    # Time-block collisions: computed HERE (outside engine.run) from the raw section
    # days/times — which the workbook schema drops — joined with the engine's cohort
    # plans, then injected into results["analysis"] alongside the other diagnostics.
    collisions = _time_block_collisions(sections, program, report["results"])
    room_conflicts = _room_collisions(sections)
    room_capacity = _room_capacity_findings(sections, facility) if facility else []
    if isinstance(report["results"], dict) and isinstance(report["results"].get("analysis"), dict):
        analysis = report["results"]["analysis"]
        analysis["time_block_collisions"] = collisions
        analysis["off_grid_sections"] = _off_grid_sections(sections)
        # Room double-bookings (and, when a facility table is supplied, over-capacity)
        # are computed HERE from the raw section room/days/times the workbook drops.
        analysis["room_conflicts"] = room_conflicts
        if facility:
            analysis["room_capacity"] = room_capacity
    report["inert_detectors"].append(_time_block_detector_entry(sections, collisions))
    report["inert_detectors"].append(_room_detector_entry(
        sections, room_conflicts, facility_used=bool(facility),
        capacity=room_capacity, lab_stats=_lab_pool_stats(sections, facility)))

    # Feature-analysis detectors (F1+F4 buildability, F2 bottlenecks, F5
    # demand_supply, F3 grid_pressure): each is deterministic, advisory, and
    # computed HERE outside engine.run from the raw fetched sections + program /
    # demand / facility context. They share one compute + inject + append shape,
    # so they live in the append-only ANALYSIS_DETECTORS registry and run in ONE
    # loop below — IN REGISTRY ORDER, which keeps the inert_detectors append
    # sequence (and the byte-identity determinism gate) unchanged. The detector
    # entry is appended UNCONDITIONALLY; the analysis injection is guarded by the
    # same presence check the heterogeneous detectors above use, so a results-less
    # run still appends the detector but skips the analysis assignment.
    ctx = SimpleNamespace(
        sections=sections, program=program, ge_coverage=ge_coverage,
        active_courses=active_courses, program_demand=program_demand,
        facility=facility, by_design=by_design, coreq_map=coreq_map)
    for d in ANALYSIS_DETECTORS:
        block = d.compute(ctx)
        if isinstance(report["results"], dict) and isinstance(report["results"].get("analysis"), dict):
            report["results"]["analysis"][d.analysis_key] = block
        report["inert_detectors"].append(d.entry(block))
    # F7: map the now-fully-computed structural flags to curated ✅ research
    # evidence (PURE consumer — reads results["analysis"][...] / ge_coverage, writes
    # only this JSON key, OUTSIDE engine.run → the workbook bytes are untouched, so
    # the determinism gate stays green). No detector entry: F7 is static curated
    # evidence, not a data-derived detector signal.
    if isinstance(report["results"], dict) and isinstance(report["results"].get("analysis"), dict):
        report["results"]["analysis"]["evidence"] = evidence.evidence_appendix(report["results"])
    return report


def _pseudo_program(sections, course_units=None, *, terms=None):
    """An 'all offered courses' program so the WHOLE imported schedule is audited
    (rotation / fill / under-supply / time + room conflicts) with no Program Mapper.

    Each distinct offered course becomes a required course; units come from a course
    master map when supplied, else the catalog's default. Deterministic ordering."""
    course_units = course_units or {}
    seen, courses = set(), []
    for r in sections:
        cid = mapping._norm(r.get("course", ""))
        if not cid or cid in seen:
            continue
        seen.add(cid)
        c = {"course_id": cid}
        if cid in course_units:
            c["units"] = course_units[cid]
        courses.append(c)
    courses.sort(key=lambda c: c["course_id"])
    label = "All offered courses"
    if terms:
        label += f" ({', '.join(str(t) for t in terms)})"
    return {"code": "ALL", "title": label, "award": "", "courses": courses}


def _program_from_workbook(path, course_units=None):
    """Build a program dict from the 'programs' sheet of an engine workbook (the
    optional 'narrow to one degree path' choice). Takes the first Program Code."""
    import pandas as pd

    course_units = course_units or {}
    try:
        xl = pd.ExcelFile(path)
    except Exception as exc:  # noqa: BLE001 - surface a clear, named error
        raise SourceDataError(
            f"{mapping.SOURCE}: cannot open program file {path!r} as an .xlsx "
            "workbook with a 'programs' sheet.") from exc
    if "programs" not in xl.sheet_names:
        raise SourceDataError(
            f"{mapping.SOURCE}: program file {path!r} has no 'programs' sheet "
            f"(sheets: {xl.sheet_names}). Provide an engine workbook's programs "
            "sheet (Program Code / Program Title / Course ID).")
    df = xl.parse("programs")
    for col in ("Program Code", "Course ID"):
        if col not in df.columns:
            raise SourceDataError(
                f"{mapping.SOURCE}: programs sheet in {path!r} missing column {col!r}.")
    code = str(df["Program Code"].iloc[0])
    g = df[df["Program Code"].astype(str) == code]
    title = (str(g["Program Title"].iloc[0])
             if "Program Title" in g.columns and len(g) else code)
    courses = []
    for _, r in g.iterrows():
        cid = mapping._norm(r["Course ID"])
        c = {"course_id": cid,
             "recommended_semester": r.get("Recommended Semester")}
        if cid in course_units:
            c["units"] = course_units[cid]
        courses.append(c)
    return {"code": code, "title": title, "award": "", "courses": courses}


def _by_design_from_workbook(path):
    """FF2: by-design exclusions from the program workbook's OPTIONAL 'Notes' column.

    A course whose 'Notes' cell says "by design" (case-insensitive substring) is an
    INTENTIONAL gap — F1/F6 must not flag it. Returns the set of such Course IDs.

    Honest about the real data: the shipped LACCD programs sheet has NO Notes column
    (Program Code / Program Title / GE Pattern / Course ID / Requirement Type /
    Recommended Semester), so this stays EMPTY on the available data — it is a
    fully-wired HOOK, never fabricated. Fails OPEN to an empty set on any read error
    or missing sheet/column (never raises)."""
    import pandas as pd

    try:
        xl = pd.ExcelFile(path)
        if "programs" not in xl.sheet_names:
            return set()
        df = xl.parse("programs")
    except Exception:  # noqa: BLE001 - by_design is advisory; never break the run
        return set()
    if "Notes" not in df.columns or "Course ID" not in df.columns:
        return set()
    out = set()
    for _, r in df.iterrows():
        note = str(r.get("Notes", "") or "").lower()
        if "by design" in note:
            cid = mapping._norm(r["Course ID"])
            if cid:
                out.add(cid)
    return out


def analyze_import(schedule_path, out_path, *, program=None, program_path=None,
                   facility_path=None, course_master_path=None,
                   program_lists_path=None, sheet=None, transfer_goal="none",
                   enrollment_path=None, enrollment_map=None):
    """Offline historical audit: convert a real LACCD schedule export into engine
    records and run the SAME pipeline ``analyze_live`` runs — with NO network.

    Program (the 'Both' choice): an explicit ``program`` dict, or a ``program_path``
    (an engine workbook whose 'programs' sheet names one degree path), narrows the
    audit; absent both, an all-offered-courses pseudo-program audits the WHOLE
    schedule. Optional ``facility_path`` enables the room-capacity detector;
    ``course_master_path`` supplies real units (else every course defaults to 3).

    The schedule export typically carries Cap/Tot/Wait columns, so modality_mismatch
    / under_supply light up from the export itself (no separate IR upload). Returns
    the same report shape ``analyze_live`` does, plus an ``import_summary``.

    FF3 — optional IR enrollment overlay (``enrollment_path`` / ``enrollment_map``):
    an IR PeopleSoft enrollment export can be JOINED onto the imported schedule the
    SAME way the live path joins it, MERGE-not-strip (see
    ``enrollment.enrich_sections``): on a (term, CRN) match the IR counts WIN (IR is
    authoritative); the schedule export's OWN native Cap/Tot/Wait on UNMATCHED
    sections are PRESERVED, never wiped. So F5 demand_supply reads
    IR-on-matched + native-on-unmatched, with nothing silently lost. Mirrors
    ``analyze_live``'s ``enrollment_path`` (real IR adapter) /
    ``enrollment_map`` (hand-keyed offline) seam; both stay OUTSIDE engine.run.
    """
    records, summary = schedule_import.load_schedule_export(schedule_path, sheet=sheet)
    if not records:
        return {"campus": "LAMC", "terms": [], "section_count": 0, "program": None,
                "reconciliation": None, "inert_detectors": list(INERT_DETECTORS),
                "results": None, "import_summary": summary,
                "error": f"No active sections found in {schedule_path!r}."}

    terms = summary["terms"]
    # The course master gives BOTH units (solver) and the active-course set (the
    # buildability dead-requirement check). Load once; live path has neither.
    course_units, active_courses = (
        course_master.load_course_master(course_master_path) if course_master_path
        else ({}, None))
    if course_units:
        for r in records:
            cid = mapping._norm(r.get("course", ""))
            if cid in course_units and "units" not in r:
                r["units"] = course_units[cid]

    # FF2: by-design (intentional-gap) exclusions from the program workbook's
    # optional Notes column. The live path has no Notes, so this is import-only; it
    # stays an honestly EMPTY set on the real LACCD workbook (no Notes column).
    by_design = _by_design_from_workbook(program_path) if program_path else None
    if program is None and program_path:
        program = _program_from_workbook(program_path, course_units)
    if program is None:
        program = _pseudo_program(records, course_units, terms=terms)

    facility = facilities.load_facility(facility_path) if facility_path else None
    # The Program Course Lists export carries the cross-program demand (how many
    # programs require each course) — the headline F2 signal, which the live path
    # cannot supply (one program per run). Absent it, the bottleneck leaderboard
    # stays honestly inert.
    program_demand = (program_lists.load_program_lists(program_lists_path)
                      if program_lists_path else None)

    report = analyze_live(
        "LAMC", terms, "(historical import)", out_path,
        sections_override=records, program_override=program,
        facility=facility, active_courses=active_courses,
        program_demand=program_demand, by_design=by_design,
        # FF3: overlay an IR enrollment export onto the imported schedule. The
        # join is merge-not-strip (enrollment.enrich_sections), so unmatched
        # sections keep the schedule export's own native counts — F5
        # demand_supply then reads IR-on-matched + native-on-unmatched.
        enrollment_path=enrollment_path, enrollment_map=enrollment_map,
        elumen_live=False, transfer_goal=transfer_goal)
    report["import_summary"] = summary
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
        if d["detector"] == "ge_scheduling":
            # An unreviewed GE pattern is a DRAFT — say so loudly, never silently
            # present placeholder counts as authoritative.
            if d.get("draft_warning"):
                print(f"NOTE: {d['draft_warning']}")
            continue
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
            # Mirror the detector's truncation note so the banner doesn't read as a
            # clean full-coverage run when the eLumen fetch was actually capped.
            ft = (d.get("coverage") or {}).get("fetch_truncated") or {}
            if ft.get("exceeded"):
                line += f" NOTE: fetch {_truncation_phrase(ft)} — coverage may be partial."
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
    ap.add_argument("--transfer-goal", default="none",
                    choices=["none", "cal-getc", "igetc", "csu-ge"],
                    dest="transfer_goal",
                    help="add transfer-pattern GE to the plan (default: none).")
    args = ap.parse_args()
    terms = [int(t) for t in args.terms.split(",") if t.strip()]

    report = analyze_live(args.campus, terms, args.program, args.out,
                          enrollment_path=args.enrollment,
                          elumen_fixture=args.elumen_fixture,
                          elumen_live=args.elumen_live,
                          transfer_goal=args.transfer_goal)
    if report["error"]:
        _print_banner(report)
        raise SystemExit(1)

    _print_banner(report)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
