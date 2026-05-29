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
