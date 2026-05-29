"""Regression tests for engine behavior against the bundled synthetic dataset (PRD §6 FRs + N11)."""
import json
import os
import pathlib
import subprocess
import sys

import pytest

import engine

DEMO = str(pathlib.Path(__file__).resolve().parent.parent / "files" / "lamc_data.xlsx")


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
import pandas as pd


def test_non_workbook_input_raises_clear_error():
    with pytest.raises(engine.InputDataError) as exc:
        engine.run("files/programs.csv")
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
