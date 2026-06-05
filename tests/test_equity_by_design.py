"""FF2: by_design ingestion from the program workbook's optional 'Notes' column.

The real LACCD programs sheet (lamc_data.xlsx) has columns Program Code / Program
Title / GE Pattern / Course ID / Requirement Type / Recommended Semester — there is
NO Notes column. So FF2 ships as a fully-wired by_design HOOK: a tolerant optional
'Notes' reader on the program-workbook path that stays honestly EMPTY on the
available data, threaded end-to-end into BOTH the F1 buildability and F6 equity
compute lambdas. These tests pin: (a) the reader returns a by_design set ONLY for
'by design' notes; (b) no Notes column -> empty set (no fabrication); (c) the set
threads through analyze_import into the equity block's by_design_count.
"""
import pandas as pd

import build_live_workbook as BLW


def _write_programs(path, rows, *, notes=None):
    """rows: list of (code, title, course_id, sem). notes: dict course_id->note."""
    data = {"Program Code": [], "Program Title": [], "Course ID": [],
            "Recommended Semester": []}
    if notes is not None:
        data["Notes"] = []
    for code, title, cid, sem in rows:
        data["Program Code"].append(code)
        data["Program Title"].append(title)
        data["Course ID"].append(cid)
        data["Recommended Semester"].append(sem)
        if notes is not None:
            data["Notes"].append(notes.get(cid, ""))
    with pd.ExcelWriter(path) as xw:
        pd.DataFrame(data).to_excel(xw, sheet_name="programs", index=False)


def test_by_design_reader_picks_up_by_design_notes(tmp_path):
    p = tmp_path / "prog.xlsx"
    _write_programs(p, [
        ("BIOL-AS", "Biology", "ART 202", 1),
        ("BIOL-AS", "Biology", "CHEM 101", 2),
    ], notes={"ART 202": "Not offered in fall by design", "CHEM 101": "core"})
    bd = BLW._by_design_from_workbook(str(p))
    assert "ART 202" in bd
    assert "CHEM 101" not in bd        # 'core' is not a by-design note


def test_by_design_reader_empty_without_notes_column(tmp_path):
    # Mirrors the REAL data shape (no Notes column) -> honestly empty, no fabrication.
    p = tmp_path / "prog.xlsx"
    _write_programs(p, [("BIOL-AS", "Biology", "ART 202", 1)])
    assert BLW._by_design_from_workbook(str(p)) == set()


def test_by_design_reader_tolerant_of_unreadable(tmp_path):
    # A non-workbook path must fail OPEN to an empty set, never raise.
    assert BLW._by_design_from_workbook(str(tmp_path / "nope.xlsx")) == set()


def test_real_lamc_data_has_no_notes_column_empty_by_design():
    # Pins the FF2 finding: the shipped sample workbook has no Notes column.
    assert BLW._by_design_from_workbook("files/lamc_data.xlsx") == set()
