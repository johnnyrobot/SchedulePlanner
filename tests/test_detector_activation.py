"""m7-s5: live-detector activation + enrichment wiring (OFFLINE, fixture-only).

This exercises build_live_workbook's m7 enrichment seam:
  * an IR-shaped enrollment join (outside engine.run, on raw section records),
  * an eLumen-derived DNF->CNF prereq map threaded into the catalog sheet,
  * the INERT_DETECTORS report flipping entries to "active" ONLY when the data
    is present AND (for enrollment) the (term, CRN) join matched >=1 section.

NO OVERCLAIMING — the synthetic-key contract:
  No committed schedule (term 2268) + enrollment (terms {2248, 2252}) fixture
  pair shares a (term, CRN), so a real live-schedule run matches NOTHING. The
  detector-ACTIVATION tests therefore drive the join with a HAND-KEYED INLINE
  enrollment map whose (term, bare-CRN) keys are chosen to match the term-2268
  schedule CRNs (synthetic key — labeled here and asserted-as-such). The
  zero-match guard test proves the honest contract: enriching the real term-2268
  schedule records with the real {2248,2252} enrollment fixture yields ZERO
  matches and the enrollment detectors stay INERT.

All offline: no socket is opened. Live schedule/PM fetch (when exercised) routes
through the shared longest-match FakeClient (`lamc_routes`).
"""
import json

import pytest

import build_live_workbook
from conftest import load_fixture
from sources import enrollment


ELUMEN_FIXTURE = "tests/fixtures/elumen_prereqs_LAMC.json"


def _detector(report, name):
    """Fetch the single inert_detectors entry by detector name."""
    matches = [d for d in report["inert_detectors"] if d["detector"] == name]
    assert len(matches) == 1, f"expected exactly one {name!r} entry, got {matches}"
    return matches[0]


# ---------------------------------------------------------------------------
# Detector ACTIVATION on a hand-keyed inline enrollment map (synthetic key).
# ---------------------------------------------------------------------------
def test_enrollment_detectors_activate_on_matched_inline_map(lamc_routes,
                                                             make_client, tmp_path):
    """With a SYNTHETIC inline enrollment map keyed to the term-2268 schedule
    CRNs, the (term, CRN) join matches >=1 section, low-fill / high-waitlist
    counts reach the engine, and modality_mismatch + under_supply ACTIVATE.

    SYNTHETIC-KEY LABEL: the inline map below is hand-keyed to the live-schedule
    fixture's own term (2268) and CRNs. The real IR enrollment fixture is terms
    {2248, 2252} with disjoint CRNs and would NOT match (see the zero-match guard
    test) — this map is a test fixture, not a claim that the live<->IR join works.
    """
    client = make_client(lamc_routes)
    sections, program = build_live_workbook.build("LAMC", [2268], "Biology",
                                                  client=client)

    # Hand-key an enrollment map to the schedule fixture's own (term, bare-CRN).
    # Plant low-fill (fill < 0.55 -> modality_mismatch) and high-waitlist
    # (Wait Tot > 15 -> under_supply) onto every section of a PROGRAM course
    # (engine.analyze only inspects program-required courses), so the per-course
    # sum trips both thresholds. BIOLOGY 006 is in the Biology program AND
    # offered in the term-2268 schedule fixture.
    target_course = "BIOLOGY 006"
    inline_map = {}
    for r in sections:
        crn = enrollment._crn(r["class_nbr"])
        if crn is None:
            continue
        if r["course"].upper() == target_course:
            inline_map[(int(r["term"]), crn)] = {
                "Cap Enrl": 100, "Tot Enrl": 10, "Wait Tot": 20,
            }
    assert inline_map, "expected to plant >=1 inline enrollment row"

    out = tmp_path / "live_activated.xlsx"
    report = build_live_workbook.analyze_live(
        "LAMC", [2268], "Biology", str(out), client=client,
        enrollment_map=inline_map, elumen_fixture=ELUMEN_FIXTURE,
    )
    json.dumps(report)  # JSON-serializable end to end

    analysis = report["results"]["analysis"]
    # The planted course now trips both enrollment detectors.
    norm_target = target_course.upper()
    assert any(d["course"] == norm_target for d in analysis["modality_mismatch"]), \
        f"modality_mismatch should fire for {norm_target}; got {analysis['modality_mismatch']}"
    assert any(d["course"] == norm_target for d in analysis["under_supply"]), \
        f"under_supply should fire for {norm_target}; got {analysis['under_supply']}"

    # The detectors are reported ACTIVE (join matched >=1 row).
    md = _detector(report, "modality_mismatch")
    us = _detector(report, "under_supply")
    assert md["status"] == "active"
    assert us["status"] == "active"
    # Enrollment activation labeled fixture-scoped (live<->IR not validated live).
    blob = (json.dumps(md) + json.dumps(us)).lower()
    assert "fixture-scoped" in blob
    assert "not validated" in blob
    # Honest match accounting surfaced.
    assert md.get("matched_sections", 0) >= 1


def test_prerequisite_ordering_activates_when_prereq_map_threaded(lamc_routes,
                                                                 make_client, tmp_path):
    """Threading the eLumen-derived prereq map flips prerequisite_ordering to
    active and the solver enforces the CNF ordering for CHEM 102, whose eLumen
    fixture prereq is (CHEM 101 OR CHEM 105): at least one of the OR-group must be
    scheduled strictly before CHEM 102. (CHEM 101 is a Biology-program course;
    CHEM 105 is pulled in by closure, so the engine satisfies the OR-group with
    either.)"""
    client = make_client(lamc_routes)
    out = tmp_path / "live_prereq.xlsx"
    report = build_live_workbook.analyze_live(
        "LAMC", [2268], "Biology", str(out), client=client,
        elumen_fixture=ELUMEN_FIXTURE,
    )

    pre = _detector(report, "prerequisite_ordering")
    assert pre["status"] == "active"
    # eLumen path labeled fixture-only.
    assert "fixture-only" in json.dumps(pre).lower()

    # The catalog actually carries the CNF string for CHEM 102.
    import openpyxl
    wb = openpyxl.load_workbook(out)
    catalog = wb["catalog"]
    header = [c.value for c in catalog[1]]
    cid_col = header.index("Course ID")
    pre_col = header.index("Prerequisites (structured)")
    chem102 = None
    for row in catalog.iter_rows(min_row=2, values_only=True):
        if row[cid_col] == "CHEM 102":
            chem102 = row[pre_col]
    assert chem102 == "(CHEM 101 OR CHEM 105)", \
        f"CHEM 102 prereq string should be threaded; got {chem102!r}"

    # The solver enforces the (CHEM 101 OR CHEM 105) ordering: at least one of
    # the OR-group is scheduled strictly before CHEM 102 in every cohort plan
    # that schedules CHEM 102.
    bio = report["results"]["programs"]["BIOLOGY"]
    checked = False
    for cohort in bio["cohorts"].values():
        if not cohort:
            continue
        plan = cohort["plan"]
        terms_by_course = {c: int(t) for t, cs in plan.items() for c in cs}
        if "CHEM 102" not in terms_by_course:
            continue
        chem102_t = terms_by_course["CHEM 102"]
        prereq_terms = [terms_by_course[p] for p in ("CHEM 101", "CHEM 105")
                        if p in terms_by_course]
        assert prereq_terms, f"OR-group prereq must be scheduled; plan={plan}"
        assert min(prereq_terms) < chem102_t, (
            f"a CHEM 102 prereq (CHEM 101/105) must precede it; plan={plan}")
        checked = True
    assert checked, "expected a cohort plan that schedules CHEM 102"


def test_budget_fallback_course_labeled_conservative_permissive(lamc_routes,
                                                               make_client, tmp_path):
    """A course whose DNF->CNF exceeds the (tightened) clause budget is reported
    as a conservative-permissive (not exact) approximation, never silently. We
    force this by passing max_clauses=1 through the wiring so any multi-product
    course exceeds the budget and falls back to the union clause: PHYS 102's DNF
    [[MATH 245, MATH 246], [PHYS 185]] distributes to a 2x1=2 product, and
    BIO 200's [[BIO 101, CHEM 101], [BIO 102, CHEM 101]] to a 2x2=4 product —
    both > max_clauses=1, so both fall back (assertions only require >=1)."""
    client = make_client(lamc_routes)
    out = tmp_path / "live_fallback.xlsx"
    report = build_live_workbook.analyze_live(
        "LAMC", [2268], "Biology", str(out), client=client,
        elumen_fixture=ELUMEN_FIXTURE, prereq_max_clauses=1,
    )

    pre = _detector(report, "prerequisite_ordering")
    assert pre["status"] == "active"
    summary = pre["prereq_summary"]
    # At least one course fell back; it is labeled conservative-permissive.
    assert summary["fallback_count"] >= 1
    fb_blob = json.dumps(summary).lower()
    assert "conservative-permissive" in fb_blob
    assert "not exact" in fb_blob
    # The exact courses are still distinguished from the fallback ones.
    assert summary["exact_count"] >= 1
    # eLumen path labeled fixture-only on the whole prereq slice.
    assert "fixture-only" in json.dumps(pre).lower()


# ---------------------------------------------------------------------------
# Human-readable banner must mirror the structured report (no overclaim-in-reverse).
# ---------------------------------------------------------------------------
def test_banner_reflects_active_prereq_ordering_under_elumen_fixture(
        lamc_routes, make_client, tmp_path, capsys):
    """Under --elumen-fixture the prerequisite_ordering detector is ACTIVE, so the
    human banner must NOT assert the solver runs 'without ordering constraints'
    (that would contradict the JSON report printed below it). It must instead name
    prerequisite_ordering as ACTIVE."""
    client = make_client(lamc_routes)
    out = tmp_path / "live_banner.xlsx"
    report = build_live_workbook.analyze_live(
        "LAMC", [2268], "Biology", str(out), client=client,
        elumen_fixture=ELUMEN_FIXTURE,
    )
    # Sanity: this run really does activate prerequisite_ordering.
    assert _detector(report, "prerequisite_ordering")["status"] == "active"

    build_live_workbook._print_banner(report)
    banner = capsys.readouterr().out
    assert "without ordering constraints" not in banner, (
        f"banner must not claim no ordering constraints when prereq is active:\n{banner}")
    assert "prerequisite_ordering ACTIVE" in banner
    # The enrollment detectors ARE still inert this run (no --enrollment input),
    # so the banner should still say so honestly.
    assert "modality_mismatch INERT" in banner
    assert "under_supply INERT" in banner


def test_banner_all_inert_on_bare_fetch(lamc_routes, make_client, tmp_path, capsys):
    """With no enrichment inputs every detector is inert, so the banner prints the
    INERT line for all three (mirroring the report) and never an ACTIVE line."""
    client = make_client(lamc_routes)
    out = tmp_path / "live_banner_bare.xlsx"
    report = build_live_workbook.analyze_live(
        "LAMC", [2268], "Biology", str(out), client=client)

    build_live_workbook._print_banner(report)
    banner = capsys.readouterr().out
    assert "modality_mismatch INERT" in banner
    assert "under_supply INERT" in banner
    assert "without ordering constraints" in banner
    assert "ACTIVE" not in banner


# ---------------------------------------------------------------------------
# Zero-match guard — the honest no-overclaim contract.
# ---------------------------------------------------------------------------
def test_real_enrollment_fixture_against_live_schedule_matches_zero(lamc_routes,
                                                                  make_client, tmp_path):
    """Enriching the REAL term-2268 schedule records with the REAL {2248,2252}
    enrollment fixture yields ZERO matches (term + CRN disjoint), so the
    enrollment detectors stay INERT. This proves the no-overclaim contract: a
    real --enrollment run with today's fixtures does NOT activate the detectors.
    """
    client = make_client(lamc_routes)
    sections, _ = build_live_workbook.build("LAMC", [2268], "Biology", client=client)

    enr = enrollment.load_enrollment("files/lamc_sample_enrollment.xlsx")
    enriched = enrollment.enrich_sections(sections, enr)

    # Direct join-level assertion: ZERO records gained enrollment keys.
    matched = [r for r in enriched if "Cap Enrl" in r]
    assert matched == [], (
        "live 2268 schedule must NOT join the {2248,2252} enrollment fixture; "
        f"unexpectedly matched {len(matched)} records")

    # Through the pipeline: detectors stay INERT (Cap/Tot/Wait = 0).
    out = tmp_path / "live_zero.xlsx"
    report = build_live_workbook.analyze_live(
        "LAMC", [2268], "Biology", str(out), client=client,
        enrollment_path="files/lamc_sample_enrollment.xlsx",
    )
    analysis = report["results"]["analysis"]
    assert analysis["modality_mismatch"] == []
    assert analysis["under_supply"] == []

    md = _detector(report, "modality_mismatch")
    us = _detector(report, "under_supply")
    assert md["status"] == "inert"
    assert us["status"] == "inert"
    # The inert reason honestly names the zero-match cause.
    assert "0" in (md.get("matched_sections_note", "") + str(md.get("matched_sections", "")))
    blob = (json.dumps(md) + json.dumps(us)).lower()
    assert "0 section" in blob or "matched 0" in blob or "zero" in blob


def test_no_enrichment_inputs_keeps_all_detectors_inert(lamc_routes, make_client,
                                                       tmp_path):
    """With NO enrollment + NO eLumen inputs the report is byte-compatible with
    the pre-m7 behavior: all three detectors INERT with honest reasons."""
    client = make_client(lamc_routes)
    out = tmp_path / "live_bare.xlsx"
    report = build_live_workbook.analyze_live(
        "LAMC", [2268], "Biology", str(out), client=client)

    for name in ("modality_mismatch", "under_supply", "prerequisite_ordering"):
        d = _detector(report, name)
        assert d["status"] == "inert", f"{name} should be inert without data"
        assert d["reason"]
    # No enrollment counts -> detectors empty by construction.
    analysis = report["results"]["analysis"]
    assert analysis["modality_mismatch"] == []
    assert analysis["under_supply"] == []


def test_cli_args_parse_enrollment_and_elumen_fixture(monkeypatch, tmp_path,
                                                     lamc_routes, make_client):
    """The CLI exposes --enrollment and --elumen-fixture; analyze_live receives
    them. We patch analyze_live to capture the kwargs without a network call."""
    captured = {}

    def fake_analyze(campus, terms, program, out, *, client=None,
                     enrollment_path=None, elumen_fixture=None,
                     elumen_live=False, enrollment_map=None,
                     prereq_max_clauses=None):
        captured.update(
            campus=campus, terms=terms, program=program, out=out,
            enrollment_path=enrollment_path, elumen_fixture=elumen_fixture,
            elumen_live=elumen_live,
        )
        return {"error": None, "program": {"title": "T", "course_count": 0},
                "reconciliation": {"matched_count": 0, "unmatched_count": 0,
                                   "unmatched": []},
                "section_count": 0, "terms": terms, "workbook": str(out),
                "inert_detectors": []}

    monkeypatch.setattr(build_live_workbook, "analyze_live", fake_analyze)
    monkeypatch.setattr("sys.argv", [
        "build_live_workbook.py", "--campus", "LAMC", "--program", "Biology",
        "--terms", "2268", "--out", str(tmp_path / "o.xlsx"),
        "--enrollment", "files/lamc_sample_enrollment.xlsx",
        "--elumen-fixture", ELUMEN_FIXTURE,
    ])
    build_live_workbook.main()
    assert captured["enrollment_path"] == "files/lamc_sample_enrollment.xlsx"
    assert captured["elumen_fixture"] == ELUMEN_FIXTURE
    # --elumen-live not passed here -> the flag defaults to False (opt-in only).
    assert captured["elumen_live"] is False
