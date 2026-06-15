"""Malformed / adversarial input hardening (ship-review, Tests pillar).

engine.run accepts ANY user-openable workbook, so a corrupt, hand-edited, or
hostile workbook must degrade to a NAMED error — never a bare traceback and never
code execution — and untrusted data rendered into the HTML report must be escaped.
The catalog PDF path (the app's only subprocess) must degrade to a readable message
when it cannot run. All offline; no live mark, no JVM spawned.
"""
import html
import inspect
import re

import pandas as pd
import pytest

import build_live_workbook
import engine
import report_export
from sources import mapping, pdf_loader, schedule, schedule_import, timeblocks


def _wb(tmp_path, sections, catalog=None, programs=None, name="wb.xlsx"):
    p = tmp_path / name
    with pd.ExcelWriter(p) as xl:
        pd.DataFrame(sections).to_excel(xl, sheet_name="sections", index=False)
        if catalog is not None:
            pd.DataFrame(catalog).to_excel(xl, sheet_name="catalog", index=False)
        if programs is not None:
            pd.DataFrame(programs).to_excel(xl, sheet_name="programs", index=False)
    return str(p)


# ---------------------------------------------------------------- graceful failure
def test_workbook_missing_required_sheet_raises_input_data_error(tmp_path):
    p = tmp_path / "nosheets.xlsx"
    with pd.ExcelWriter(p) as xl:
        pd.DataFrame([{"x": 1}]).to_excel(xl, sheet_name="wrong", index=False)
    with pytest.raises(engine.InputDataError):
        engine.run(str(p))


def test_sections_missing_required_column_raises_named_error(tmp_path):
    # 'CLASS' is required; its absence must raise a NAMED InputDataError, not a
    # bare KeyError deep in the solver.
    wb = _wb(tmp_path,
             sections=[{"Term": 2248, "Class Status": "Active",   # no CLASS column
                        "Cap Enrl": 0, "Tot Enrl": 0, "Wait Tot": 0}],
             catalog=[{"Course ID": "CS 101", "Units": 3, "Prerequisites (structured)": ""}],
             programs=[{"Program Code": "P", "Program Title": "P",
                        "Course ID": "CS 101", "Recommended Semester": 1}])
    with pytest.raises(engine.InputDataError) as ei:
        engine.run(wb)
    assert "CLASS" in str(ei.value)


# ---------------------------------------------------------------- data is not code
def test_ingestion_and_engine_never_eval_or_exec_cell_data():
    # A spreadsheet/CSV cell is DATA. The ingestion + engine path must never
    # eval/exec/__import__ a cell value — the one way a workbook could become code
    # execution. (\b-anchored so 'retrieval(' / '.evaluate(' don't false-positive.)
    for mod in (engine, mapping, schedule, schedule_import, timeblocks,
                build_live_workbook):
        src = inspect.getsource(mod)
        assert not re.search(r"(?<![\w.])eval\s*\(", src), f"{mod.__name__} must not eval() data"
        assert not re.search(r"(?<![\w.])exec\s*\(", src), f"{mod.__name__} must not exec() data"
        assert "__import__(" not in src, f"{mod.__name__} must not __import__() from data"


def test_formula_looking_cell_is_inert_data_not_executed(tmp_path):
    # A CLASS value that LOOKS like an Excel formula / command is just an (unknown)
    # course string: engine.run must complete without executing anything and without
    # crashing — it simply finds no program/catalog match for it.
    wb = _wb(tmp_path,
             sections=[{"Term": 2248, "CLASS": "ZZZ 999", "Class Status": "Active",
                        "Cap Enrl": 0, "Tot Enrl": 0, "Wait Tot": 0}],
             catalog=[{"Course ID": "ZZZ 999", "Units": 3,
                       "Prerequisites (structured)": "=cmd|'/c calc'!A1"}],
             programs=[{"Program Code": "P", "Program Title": "P",
                        "Course ID": "ZZZ 999", "Recommended Semester": 1}])
    results = engine.run(wb)                 # must not raise / execute
    assert "programs" in results


# ---------------------------------------------------------------- output escaping
def test_report_export_escapes_untrusted_field_in_html(tmp_path):
    # A hostile program title must be HTML-escaped in the report, never injected as
    # live markup (XSS / HTML injection on the export surface).
    payload = "<script>alert('x')</script>"
    wb = _wb(tmp_path,
             sections=[{"Term": 2248, "CLASS": "CS 101", "Class Status": "Active",
                        "Cap Enrl": 0, "Tot Enrl": 0, "Wait Tot": 0}],
             catalog=[{"Course ID": "CS 101", "Units": 3, "Prerequisites (structured)": ""}],
             programs=[{"Program Code": "P", "Program Title": payload,
                        "Course ID": "CS 101", "Recommended Semester": 1}])
    out = report_export.render_report(engine.run(wb))
    assert payload not in out                      # never rendered raw
    assert html.escape(payload) in out             # escaped form is present


def test_esc_escapes_html_metacharacters():
    assert report_export._esc("<b>&\"'") == "&lt;b&gt;&amp;&quot;&#x27;"
    assert "<script>" not in report_export._esc("=HYPERLINK('x')<script>")


# ---------------------------------------------------------------- pdf degradation
def test_pdf_loader_degrades_on_missing_file_without_spawning_java():
    # A non-existent path fails BEFORE any JVM spawn — a readable PdfLoadError.
    with pytest.raises(pdf_loader.PdfLoadError):
        pdf_loader.extract("/no/such/catalog.pdf")


def test_pdf_loader_degrades_when_no_java(tmp_path, monkeypatch):
    # With Java unavailable, an existing file degrades to a readable message naming
    # the Java requirement — never a crash or a hang.
    monkeypatch.setattr(pdf_loader, "java_present", lambda: False)
    f = tmp_path / "x.pdf"
    f.write_bytes(b"%PDF-1.4 not a real pdf")
    with pytest.raises(pdf_loader.PdfLoadError) as ei:
        pdf_loader.extract(str(f))
    assert "Java" in str(ei.value)
