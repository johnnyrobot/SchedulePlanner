"""Regression tests for engine behavior against the bundled synthetic dataset (PRD §6 FRs + N11)."""
import json
import os
import pathlib
import subprocess
import sys

import pandas as pd
import pytest

import engine
import llm_assist

DEMO = str(pathlib.Path(__file__).resolve().parent.parent / "files" / "lamc_data.xlsx")
FILES_DIR = str(pathlib.Path(__file__).resolve().parent.parent / "files")


def test_engine_run_is_deterministic():
    """PRD N11: identical inputs -> byte-identical results."""
    r1 = engine.run(DEMO)
    r2 = engine.run(DEMO)
    assert json.dumps(r1, sort_keys=True) == json.dumps(r2, sort_keys=True)


def test_default_data_path_exists():
    p = engine._default_data_path()
    assert p.endswith(os.path.join("files", "lamc_data.xlsx"))
    assert os.path.exists(p)


def test_engine_cli_no_args_runs():
    """`python3 engine.py` (no args) must run on the bundled demo, not a dead path."""
    repo = pathlib.Path(__file__).resolve().parent.parent
    out = subprocess.run([sys.executable, "engine.py"], cwd=repo,
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    data = json.loads(out.stdout)
    assert data["terms_in_data"] == 8


@pytest.mark.live
def test_llm_assist_cli_no_args_runs():
    """`python3 llm_assist.py` (no args, no Ollama) must run via fallback and exit 0."""
    repo = pathlib.Path(__file__).resolve().parent.parent
    out = subprocess.run([sys.executable, "llm_assist.py"], cwd=repo,
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip()  # non-empty briefing output


# ----------------------------------------------------------------- PRD F3: input validation
def test_non_workbook_input_raises_clear_error():
    with pytest.raises(engine.InputDataError) as exc:
        engine.run(str(pathlib.Path(__file__).resolve().parent.parent / "files" / "programs.csv"))
    msg = str(exc.value).lower()
    assert "workbook" in msg or "csv" in msg
    assert "excel file format cannot be determined" not in msg  # not the raw pandas text


def test_missing_required_column_raises_clear_error(tmp_path):
    # a 3-sheet workbook whose sections sheet is missing 'Cap Enrl'
    wb = tmp_path / "bad.xlsx"
    sections = pd.DataFrame([{"Term": 2248, "CLASS": "CS 101", "Class Status": "Active",
                              "Tot Enrl": 10, "Wait Tot": 0}])  # no 'Cap Enrl'
    catalog = pd.DataFrame([{"Course ID": "CS 101", "Units": 3, "Prerequisites (structured)": ""}])
    programs = pd.DataFrame([{"Program Code": "P", "Program Title": "T",
                              "Course ID": "CS 101", "Recommended Semester": 1}])
    with pd.ExcelWriter(wb) as xl:
        sections.to_excel(xl, sheet_name="sections", index=False)
        catalog.to_excel(xl, sheet_name="catalog", index=False)
        programs.to_excel(xl, sheet_name="programs", index=False)
    with pytest.raises(engine.InputDataError) as exc:
        engine.run(str(wb))
    assert "Cap Enrl" in str(exc.value)
    assert "sections" in str(exc.value)


def test_csv_directory_missing_file_raises_clear_error(tmp_path):
    (tmp_path / "sections.csv").write_text(
        "Term,CLASS,Class Status,Cap Enrl,Tot Enrl,Wait Tot\n2248,CS 101,Active,30,10,0\n")
    (tmp_path / "programs.csv").write_text(
        "Program Code,Program Title,Course ID,Recommended Semester\nP,T,CS 101,1\n")
    # catalog.csv intentionally missing
    with pytest.raises(engine.InputDataError):
        engine.run(str(tmp_path))


# ================================================================= PRD §6 regression backbone
# F1: XLSX workbook load
def test_f1_xlsx_loads():
    """PRD F1: engine.run accepts an .xlsx workbook and returns correct term count."""
    assert engine.run(DEMO)["terms_in_data"] == 8


# F2: CSV folder load
def test_f2_csv_folder_loads():
    """PRD F2: engine.run accepts a folder of CSVs and returns same term count."""
    assert engine.run(FILES_DIR)["terms_in_data"] == 8


# F4: rotation gaps detection
def test_f4_rotation_gaps_fires():
    """PRD F4: rotation-gap detector fires for fall-only courses (ENGR 101, BIOL 7, etc.)."""
    r = engine.run(DEMO)
    gaps = r["analysis"]["rotation_gaps"]
    assert len(gaps) > 0
    gap_courses = {g["course"] for g in gaps}
    # Fall-only courses known from the data generator
    assert "ENGR 101" in gap_courses or "BIOL 7" in gap_courses


# F5: single-section detection
def test_f5_single_section_fires():
    """PRD F5: single-section flag fires when a course runs with only one section per term."""
    r = engine.run(DEMO)
    assert len(r["analysis"]["single_section"]) > 0
    assert any(x["course"] == "ENGR 101" for x in r["analysis"]["single_section"])


# F6: modality mismatch detection
def test_f6_modality_mismatch_fires():
    """PRD F6: low-fill in-person courses appear in modality_mismatch (MATH 246 or ACCTG 2)."""
    r = engine.run(DEMO)
    mm = r["analysis"]["modality_mismatch"]
    assert len(mm) > 0
    mm_courses = {m["course"] for m in mm}
    assert "MATH 246" in mm_courses or "ACCTG 2" in mm_courses


# F7: under-supply detection
def test_f7_under_supply_fires():
    """PRD F7: heavily waitlisted courses appear in under_supply; ENGL 101 must be present."""
    r = engine.run(DEMO)
    us = r["analysis"]["under_supply"]
    assert len(us) > 0
    us_courses = {u["course"] for u in us}
    assert "ENGL 101" in us_courses


def _prog(cid):
    return pd.DataFrame([{"Course ID": cid}])


def test_under_supply_fires_on_live_waitlist_status_without_counts():
    """Live signal: with NO IR counts (Wait Tot=0) but an 'Avail Status' of
    Waitlist on some sections, under_supply fires on section BREADTH."""
    sec = pd.DataFrame([
        {"Term": 2268, "CLASS": "BIO 1", "Class Status": "Active", "Cap Enrl": 0,
         "Tot Enrl": 0, "Wait Tot": 0, "Avail Status": "Waitlist"},
        {"Term": 2268, "CLASS": "BIO 1", "Class Status": "Active", "Cap Enrl": 0,
         "Tot Enrl": 0, "Wait Tot": 0, "Avail Status": "Open"},
        {"Term": 2268, "CLASS": "BIO 1", "Class Status": "Active", "Cap Enrl": 0,
         "Tot Enrl": 0, "Wait Tot": 0, "Avail Status": "Closed"},
    ])
    out = engine.analyze(sec, _prog("BIO 1"), n_terms=1)
    assert out["under_supply"] == [{"course": "BIO 1", "waitlisted": 0,
                                    "sections_waitlisted": 1, "sections_total": 3}]


def test_under_supply_ir_headcount_beats_live_breadth():
    """When IR Wait Tot (>15) is present it wins (precise headcount), and the
    section breadth still rides alongside."""
    sec = pd.DataFrame([
        {"Term": 2268, "CLASS": "BIO 1", "Class Status": "Active", "Cap Enrl": 30,
         "Tot Enrl": 30, "Wait Tot": 20, "Avail Status": "Waitlist"},
    ])
    out = engine.analyze(sec, _prog("BIO 1"), n_terms=1)
    assert out["under_supply"] == [{"course": "BIO 1", "waitlisted": 20,
                                    "sections_waitlisted": 1, "sections_total": 1}]


def test_under_supply_silent_without_waitlist_or_counts():
    """No waitlist status and no IR counts -> no false under_supply flag."""
    sec = pd.DataFrame([
        {"Term": 2268, "CLASS": "BIO 1", "Class Status": "Active", "Cap Enrl": 0,
         "Tot Enrl": 0, "Wait Tot": 0, "Avail Status": "Open"},
    ])
    assert engine.analyze(sec, _prog("BIO 1"), n_terms=1)["under_supply"] == []


def test_under_supply_ignores_avail_status_when_column_absent():
    """A demo/IR workbook with no 'Avail Status' column still works (optional);
    under_supply then relies on Wait Tot alone."""
    sec = pd.DataFrame([
        {"Term": 2268, "CLASS": "BIO 1", "Class Status": "Active", "Cap Enrl": 0,
         "Tot Enrl": 0, "Wait Tot": 0},
    ])
    assert engine.analyze(sec, _prog("BIO 1"), n_terms=1)["under_supply"] == []


# F8: official map violation detection
def test_f8_official_map_violation():
    """PRD F8: official map issues detected for AS-T-CSCI; CS 103 season conflict flagged."""
    r = engine.run(DEMO)
    issues = r["programs"]["AS-T-CSCI"]["official_map_issues"]
    assert len(issues) > 0
    assert any("CS 103" in issue for issue in issues)


# F9/F10: both cohorts solved for all programs
def test_f9_f10_both_cohorts_all_programs():
    """PRD F9/F10: every program has both full_time and part_time cohort results (not None)."""
    r = engine.run(DEMO)
    for pcode, pdata in r["programs"].items():
        for cohort_key in ("full_time", "part_time"):
            result = pdata["cohorts"].get(cohort_key)
            assert result is not None, (
                f"{pcode} {cohort_key} cohort returned None (infeasible)"
            )
            assert isinstance(result, dict), (
                f"{pcode} {cohort_key} cohort result is not a dict"
            )
            assert "plan" in result, (
                f"{pcode} {cohort_key} cohort result missing 'plan' key"
            )
            assert len(result["plan"]) > 0, (
                f"{pcode} {cohort_key} plan is empty"
            )


# F11: ENGR minimum fix
def test_f11_engr_minfix():
    """PRD F11: AS-T-ENGR full_time requires exactly one fix: ENGR 102 in Spring."""
    r = engine.run(DEMO)
    ft = r["programs"]["AS-T-ENGR"]["cohorts"]["full_time"]
    assert ft["needs_fix"] is True
    assert ft["fixes"] == [{"course": "ENGR 102", "season": "Spring"}]


# F12: prereq ordering in CSCI plan
def test_f12_prereq_ordering():
    """PRD F12: AS-T-CSCI full_time plan schedules prereqs before their dependents."""
    r = engine.run(DEMO)
    plan = r["programs"]["AS-T-CSCI"]["cohorts"]["full_time"]["plan"]
    # Build course -> term map
    course_term = {}
    for t, courses in plan.items():
        for c in courses:
            course_term[c] = int(t)
    # Guard: all checked courses must be present (clear failure, not KeyError)
    assert {"MATH 245", "MATH 246", "MATH 247",
            "CS 101", "CS 102", "CS 103"} <= set(course_term)
    # MATH 245 -> MATH 246 -> MATH 247
    assert course_term["MATH 245"] < course_term["MATH 246"], (
        "MATH 246 scheduled before its prereq MATH 245"
    )
    assert course_term["MATH 246"] < course_term["MATH 247"], (
        "MATH 247 scheduled before its prereq MATH 246"
    )
    # CS 101 -> CS 102 -> CS 103
    assert course_term["CS 101"] < course_term["CS 102"], (
        "CS 102 scheduled before its prereq CS 101"
    )
    assert course_term["CS 102"] < course_term["CS 103"], (
        "CS 103 scheduled before its prereq CS 102"
    )


# F13: unit caps respected
def test_f13_unit_caps_respected():
    """PRD F13: no plan term exceeds the cohort's max_units."""
    sec, cat, prog = engine.load_data(DEMO)
    _, _, units, _ = engine.build_model(sec, cat, prog)
    r = engine.run(DEMO)
    for pcode, pdata in r["programs"].items():
        for ck, cohort_spec in engine.COHORTS.items():
            result = pdata["cohorts"].get(ck)
            if result is None:
                continue
            max_u = cohort_spec["max_units"]
            for t, courses in result["plan"].items():
                total = sum(int(units.get(c, 3)) for c in courses)
                assert total <= max_u, (
                    f"{pcode} {ck} term {t}: {total} units exceeds cap {max_u}"
                )


# F14: season constraints respected in a no-fix plan
def test_f14_season_constraints_respected():
    """PRD F14: in a no-fix plan, every scheduled course sits in a term whose season it is offered."""
    sec, cat, prog = engine.load_data(DEMO)
    _, course_seasons, _, _ = engine.build_model(sec, cat, prog)
    r = engine.run(DEMO)
    # Use AS-T-CSCI full_time: solves without fixes, so all seasons must be valid
    csci_ft = r["programs"]["AS-T-CSCI"]["cohorts"]["full_time"]
    assert not csci_ft.get("needs_fix"), "Expected AS-T-CSCI full_time to solve without fixes"
    for t, courses in csci_ft["plan"].items():
        t_int = int(t)
        season = engine.term_season(t_int)
        for c in courses:
            avail = course_seasons.get(c)
            if avail is None:
                continue  # course not in course_seasons (no active sections) — skip
            assert season in avail, (
                f"AS-T-CSCI full_time: {c} placed in term {t_int} ({season}) "
                f"but only offered in {avail}"
            )


# F18: official-map violations reported ALONGSIDE a corrected plan
def test_f18_corrected_plan_solves():
    """PRD F18: official-map violations are reported AND a corrected plan still solves."""
    prog = engine.run(DEMO)["programs"]["AS-T-CSCI"]
    assert prog["official_map_issues"]            # violation(s) detected
    ft = prog["cohorts"]["full_time"]
    assert ft is not None and ft["plan"]          # corrected plan still generated


# parse_prereq forms
def test_parse_prereq_forms():
    """parse_prereq handles structured, simple, empty, None, and llm-delegate forms."""
    # Structured AND/OR expression
    assert engine.parse_prereq("(A OR B) AND (C)") == [["A", "B"], ["C"]]
    # Single course (short alphanumeric, ≤2 words) — treated as structured
    assert engine.parse_prereq("MATH 245") == [["MATH 245"]]
    # Empty string
    assert engine.parse_prereq("") == []
    # None (NaN-like)
    assert engine.parse_prereq(None) == []
    # Unstructured text with llm delegate — "see catalog page 5" is not structured
    result = engine.parse_prereq("see catalog page 5", llm=lambda s: [["X 1"]])
    assert result == [["X 1"]], (
        f"Expected llm delegate to fire for unstructured text, got {result!r}"
    )


# F17: briefing fallback (offline, no Ollama required)
def test_f17_briefing_fallback(monkeypatch):
    """PRD F17: explain() returns a non-empty templated briefing when Ollama is unavailable."""
    # Force the no-AI path by patching the availability gate
    monkeypatch.setattr(llm_assist, "available", lambda model=llm_assist.MODEL: False)
    r = engine.run(DEMO)
    text = llm_assist.explain(r)
    assert text.strip(), "explain() returned empty string on fallback path"
    # Template includes program titles; at least one of these should appear
    assert "Computer Science" in text or "Engineering" in text, (
        f"Expected program title in briefing, got:\n{text}"
    )


import pandas as pd
import engine


def _ge_workbook(tmp_path, ge_rows):
    """Minimal 4-sheet workbook: one major course + GE requirements."""
    out = tmp_path / "ge_wb.xlsx"
    sections = pd.DataFrame([
        {"Term": 20248, "CLASS": "BIOLOGY 7", "Class Status": "Active",
         "Cap Enrl": 0, "Tot Enrl": 0, "Wait Tot": 0},
        {"Term": 20248, "CLASS": "ART 101", "Class Status": "Active",
         "Cap Enrl": 0, "Tot Enrl": 0, "Wait Tot": 0},
        {"Term": 20248, "CLASS": "ART 105", "Class Status": "Active",
         "Cap Enrl": 0, "Tot Enrl": 0, "Wait Tot": 0},
    ])
    catalog = pd.DataFrame([
        {"Course ID": "BIOLOGY 7", "Units": 4, "Prerequisites (structured)": ""},
        {"Course ID": "ART 101", "Units": 3, "Prerequisites (structured)": ""},
        {"Course ID": "ART 105", "Units": 3, "Prerequisites (structured)": ""},
    ])
    programs = pd.DataFrame([
        {"Program Code": "BIO", "Program Title": "Biology", "Course ID": "BIOLOGY 7",
         "Recommended Semester": 1}])
    with pd.ExcelWriter(out, engine="openpyxl") as xl:
        sections.to_excel(xl, sheet_name="sections", index=False)
        catalog.to_excel(xl, sheet_name="catalog", index=False)
        programs.to_excel(xl, sheet_name="programs", index=False)
        pd.DataFrame(ge_rows, columns=engine_ge_cols()).to_excel(
            xl, sheet_name="ge_requirements", index=False)
    return str(out)


def engine_ge_cols():
    from sources.mapping import GE_REQUIREMENT_COLUMNS
    return GE_REQUIREMENT_COLUMNS


def test_engine_schedules_concrete_ge_choice(tmp_path):
    rows = [{"Program Code": "BIO", "Pattern": "igetc", "Area": "3A",
             "Area Title": "Arts", "Required Count": 1, "Resolution": "concrete",
             "Candidate Course IDs": "ART 101;ART 105", "Recommended Course": "ART 101",
             "Units": 3.0}]
    res = engine.run(_ge_workbook(tmp_path, rows))
    cohort = res["programs"]["BIO"]["cohorts"]["full_time"]
    scheduled = [c for terms in cohort["plan"].values() for c in terms]
    # Exactly one of the two candidates is scheduled (choose-from-set, count == 1).
    assert len(set(scheduled) & {"ART 101", "ART 105"}) == 1
    assert "BIOLOGY 7" in scheduled                      # major course still scheduled
    assert cohort["ge"]["3A"]["resolution"] == "concrete"


def test_engine_reserves_ge_slot(tmp_path):
    rows = [{"Program Code": "BIO", "Pattern": "igetc", "Area": "1A",
             "Area Title": "English", "Required Count": 1, "Resolution": "reserve",
             "Candidate Course IDs": "", "Recommended Course": "", "Units": 3.0}]
    res = engine.run(_ge_workbook(tmp_path, rows))
    cohort = res["programs"]["BIO"]["cohorts"]["full_time"]
    flat = [c for terms in cohort["plan"].values() for c in terms]
    assert any(c.startswith("GE:") and "1A" in c for c in flat)   # reserve slot token in plan


def test_no_ge_sheet_is_byte_identical(tmp_path):
    # A workbook without the 4th sheet must behave exactly as before this feature.
    out = tmp_path / "no_ge.xlsx"
    sections = pd.DataFrame([{"Term": 20248, "CLASS": "BIOLOGY 7", "Class Status": "Active",
                              "Cap Enrl": 0, "Tot Enrl": 0, "Wait Tot": 0}])
    catalog = pd.DataFrame([{"Course ID": "BIOLOGY 7", "Units": 4,
                             "Prerequisites (structured)": ""}])
    programs = pd.DataFrame([{"Program Code": "BIO", "Program Title": "Biology",
                              "Course ID": "BIOLOGY 7", "Recommended Semester": 1}])
    with pd.ExcelWriter(out, engine="openpyxl") as xl:
        sections.to_excel(xl, sheet_name="sections", index=False)
        catalog.to_excel(xl, sheet_name="catalog", index=False)
        programs.to_excel(xl, sheet_name="programs", index=False)
    res = engine.run(str(out))
    assert "ge" not in res["programs"]["BIO"]["cohorts"]["full_time"]


# ---- D: time-block slot avoidance in the solver -----------------------------
def _write_wb(path, sections_rows):
    """Tiny 3-sheet workbook: program P requires courses A 1 + B 1."""
    catalog = pd.DataFrame([
        {"Course ID": "A 1", "Units": 3, "Prerequisites (structured)": ""},
        {"Course ID": "B 1", "Units": 3, "Prerequisites (structured)": ""}])
    programs = pd.DataFrame([
        {"Program Code": "P", "Program Title": "P", "Course ID": "A 1",
         "Recommended Semester": ""},
        {"Program Code": "P", "Program Title": "P", "Course ID": "B 1",
         "Recommended Semester": ""}])
    with pd.ExcelWriter(path) as xl:
        pd.DataFrame(sections_rows).to_excel(xl, sheet_name="sections", index=False)
        catalog.to_excel(xl, sheet_name="catalog", index=False)
        programs.to_excel(xl, sheet_name="programs", index=False)


def test_solver_separates_hard_time_conflict(tmp_path):
    """Two required courses whose every section overlaps in time are placed in
    DIFFERENT terms by the solver. Both are offered Fall(2248)+Spring(2252) so
    season never forces the separation — only the time-block constraint does."""
    rows = []
    for cls in ("A 1", "B 1"):
        for term in (2248, 2252):
            rows.append({"Term": term, "CLASS": cls, "Class Status": "Active",
                         "Cap Enrl": 0, "Tot Enrl": 0, "Wait Tot": 0,
                         "Days": "MW", "Times": "9:00 AM - 10:00 AM"})
    wb = tmp_path / "conflict.xlsx"
    _write_wb(str(wb), rows)
    plan = engine.run(str(wb))["programs"]["P"]["cohorts"]["full_time"]["plan"]
    term_of = {c: t for t, cs in plan.items() for c in cs}
    assert term_of["A 1"] != term_of["B 1"]


def test_solver_separates_conflict_on_secondary_meeting_block(tmp_path):
    """A hard time conflict that exists ONLY on a section's SECONDARY meeting block
    (carried in the optional ``Meetings`` column, not the first-block Days/Times)
    still separates the two required courses.

    A 1's first block (MW 9-10) does NOT overlap B 1 (F 9-10); the overlap lives
    only on A 1's SECOND block (F 9-10), encoded in Meetings. Separation therefore
    proves the engine reads the full meeting footprint, not just block[0]."""
    a_blocks = json.dumps([{"days": "MW", "times": "9:00 AM - 10:00 AM"},
                           {"days": "F", "times": "9:00 AM - 10:00 AM"}])
    rows = []
    for term in (2248, 2252):                       # both offered Fall+Spring
        rows.append({"Term": term, "CLASS": "A 1", "Class Status": "Active",
                     "Cap Enrl": 0, "Tot Enrl": 0, "Wait Tot": 0,
                     "Days": "MW", "Times": "9:00 AM - 10:00 AM",
                     "Meetings": a_blocks})
        rows.append({"Term": term, "CLASS": "B 1", "Class Status": "Active",
                     "Cap Enrl": 0, "Tot Enrl": 0, "Wait Tot": 0,
                     "Days": "F", "Times": "9:00 AM - 10:00 AM",
                     "Meetings": ""})                # single block -> Days/Times fallback
    wb = tmp_path / "secondary.xlsx"
    _write_wb(str(wb), rows)
    plan = engine.run(str(wb))["programs"]["P"]["cohorts"]["full_time"]["plan"]
    term_of = {c: t for t, cs in plan.items() for c in cs}
    assert term_of["A 1"] != term_of["B 1"]


def test_engine_tolerates_malformed_meetings_cell(tmp_path):
    """A corrupt / hand-edited ``Meetings`` cell must NOT crash engine.run. The
    engine degrades to the visible first-block Days/Times for that row (fail open,
    mirroring the other optional meeting columns), never raising JSONDecodeError or
    TypeError. ``engine.run`` accepts any user-openable workbook, so a tampered cell
    cannot be allowed to take down the whole solve."""
    rows = []
    for term in (2248, 2252):
        rows.append({"Term": term, "CLASS": "A 1", "Class Status": "Active",
                     "Cap Enrl": 0, "Tot Enrl": 0, "Wait Tot": 0,
                     "Days": "MW", "Times": "9:00 AM - 10:00 AM",
                     "Meetings": "{bad json"})              # malformed -> JSONDecodeError
        rows.append({"Term": term, "CLASS": "B 1", "Class Status": "Active",
                     "Cap Enrl": 0, "Tot Enrl": 0, "Wait Tot": 0,
                     "Days": "F", "Times": "9:00 AM - 10:00 AM",
                     "Meetings": "Infinity"})               # valid JSON, non-list float
    wb = tmp_path / "bad_meetings.xlsx"
    _write_wb(str(wb), rows)
    # must not raise; falls back to block[0] Days/Times (MW vs F -> no overlap)
    plan = engine.run(str(wb))["programs"]["P"]["cohorts"]["full_time"]["plan"]
    term_of = {c: t for t, cs in plan.items() for c in cs}
    assert term_of["A 1"] == term_of["B 1"]


def test_solver_byte_identical_without_meeting_data(tmp_path):
    """Non-conflicting Days/Times produce the SAME plan as omitting the columns
    entirely — the additive no-op contract that protects determinism."""
    base = [{"Term": 2248, "CLASS": c, "Class Status": "Active",
             "Cap Enrl": 0, "Tot Enrl": 0, "Wait Tot": 0} for c in ("A 1", "B 1")]
    no_times = tmp_path / "no.xlsx"
    with_times = tmp_path / "yes.xlsx"
    _write_wb(str(no_times), base)
    timed = [dict(r) for r in base]
    timed[0].update({"Days": "MW", "Times": "9:00 AM - 10:00 AM"})
    timed[1].update({"Days": "MW", "Times": "10:00 AM - 11:00 AM"})  # no overlap
    _write_wb(str(with_times), timed)
    a = engine.run(str(no_times))["programs"]["P"]["cohorts"]
    b = engine.run(str(with_times))["programs"]["P"]["cohorts"]
    assert a == b


# ---- F: Summer/Winter + year term model -------------------------------------
def test_season_and_year_decode():
    assert engine.season_of_code(2248) == "Fall"
    assert engine.season_of_code(2252) == "Spring"
    assert engine.season_of_code(2256) == "Summer"
    assert engine.season_of_code(2251) == "Winter"
    assert engine.year_of_code(2248) == 2024
    assert engine.year_of_code(2252) == 2025


def test_cadence_fallback_and_extension():
    assert engine._cadence({"Fall", "Spring"}) == ["Fall", "Spring"]
    assert engine._cadence(set()) == ["Fall", "Spring"]            # determinism default
    assert engine._cadence({"Fall", "Summer"}) == ["Fall", "Summer"]
    assert engine._cadence({"Summer", "Winter", "Spring", "Fall"}) == [
        "Fall", "Winter", "Spring", "Summer"]                      # academic order


def test_solver_schedules_summer_only_course(tmp_path):
    """When Summer is offered in the data, the cadence includes it and a summer-only
    course is scheduled into a Summer term (not mislabeled/blocked)."""
    rows = [
        {"Term": 2248, "CLASS": "A 1", "Class Status": "Active",   # Fall
         "Cap Enrl": 0, "Tot Enrl": 0, "Wait Tot": 0},
        {"Term": 2256, "CLASS": "B 1", "Class Status": "Active",   # Summer
         "Cap Enrl": 0, "Tot Enrl": 0, "Wait Tot": 0},
    ]
    wb = tmp_path / "summer.xlsx"
    _write_wb(str(wb), rows)
    plan = engine.run(str(wb))["programs"]["P"]["cohorts"]["full_time"]["plan"]
    term_of = {c: t for t, cs in plan.items() for c in cs}
    cadence = engine._cadence({"Fall", "Summer"})
    assert engine.term_season(term_of["B 1"], cadence) == "Summer"
    assert engine.term_season(term_of["A 1"], cadence) == "Fall"


def test_solve_cohort_surfaces_terms_per_year(tmp_path):
    """Every cohort result carries ``terms_per_year`` = len(cadence) — the exact
    cadence length the solver used to scale the horizon — so the report/UI can turn
    abstract ``terms_used`` into calendar years for ANY cadence, not a hardcoded 2.

    A Fall+Spring+Summer dataset is a 3-season cadence; a Fall+Spring one is the
    legacy 2-season cadence."""
    def tpy(terms):
        rows = [{"Term": t, "CLASS": cls, "Class Status": "Active",
                 "Cap Enrl": 0, "Tot Enrl": 0, "Wait Tot": 0}
                for t in terms for cls in ("A 1", "B 1")]
        wb = tmp_path / f"cad_{'_'.join(map(str, terms))}.xlsx"
        _write_wb(str(wb), rows)
        return engine.run(str(wb))["programs"]["P"]["cohorts"]

    three = tpy((2248, 2252, 2256))            # Fall, Spring, Summer
    assert three["full_time"]["terms_per_year"] == 3
    assert three["part_time"]["terms_per_year"] == 3
    two = tpy((2248, 2252))                     # Fall, Spring (legacy)
    assert two["full_time"]["terms_per_year"] == 2
