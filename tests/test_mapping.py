import pandas as pd
from sources import mapping

SECTIONS = [
    {"term": 2268, "course": "CS 101", "units": "3.00"},
    {"term": 2264, "course": "MATH 245", "units": "5.00"},
    {"term": 2268, "course": "MATH 245", "units": "5.00"},
]
PROGRAM = {
    "code": "COMPUTER-SCIENCE", "title": "Computer Science", "award": "AS-T",
    "ge_pattern": "", "courses": [
        {"course_id": "CS 101", "title": "Intro", "recommended_semester": 1, "units": 3.0},
        {"course_id": "MATH 245", "title": "Calc I", "recommended_semester": 2, "units": 5.0},
        {"course_id": "PHYS 101", "title": "Physics", "recommended_semester": 2, "units": 4.0},
    ],
}


def test_to_units_coercion():
    assert mapping._to_units("3.00") == 3.0
    assert mapping._to_units("3-4") == 3.0
    assert mapping._to_units(5.0) == 5.0
    assert mapping._to_units("") == 3.0          # default
    assert mapping._to_units(None) == 3.0        # default
    assert mapping._to_units(float("nan")) == 3.0   # missing numeric cell


def test_build_sections_df_schema_and_zero_enrollment():
    df = mapping.build_sections_df(SECTIONS)
    assert list(df.columns) == ["Term", "CLASS", "Class Status",
                                "Cap Enrl", "Tot Enrl", "Wait Tot"]
    assert (df["Class Status"] == "Active").all()
    assert (df[["Cap Enrl", "Tot Enrl", "Wait Tot"]] == 0).all().all()
    assert set(df["CLASS"]) == {"CS 101", "MATH 245"}
    assert pd.api.types.is_integer_dtype(df["Term"])


def test_build_catalog_df_numeric_units_and_union():
    df = mapping.build_catalog_df(SECTIONS, PROGRAM)
    assert list(df.columns) == ["Course ID", "Units", "Prerequisites (structured)"]
    # PHYS 101 comes only from the program but must appear (closure needs units)
    assert "PHYS 101" in set(df["Course ID"])
    assert df["Units"].map(lambda v: isinstance(v, float)).all()
    assert (df["Prerequisites (structured)"] == "").all()


def test_build_programs_df_schema():
    df = mapping.build_programs_df(PROGRAM)
    assert list(df.columns) == ["Program Code", "Program Title",
                                "Course ID", "Recommended Semester"]
    assert (df["Program Code"] == "COMPUTER-SCIENCE").all()
    assert dict(zip(df["Course ID"], df["Recommended Semester"]))["MATH 245"] == 2


def test_reconcile_courses_reports_unmatched():
    matched, unmatched = mapping.reconcile_courses(SECTIONS, PROGRAM)
    assert set(matched) == {"CS 101", "MATH 245"}
    assert unmatched == ["PHYS 101"]              # not offered in fetched terms


def test_write_workbook_has_three_named_sheets(tmp_path):
    out = tmp_path / "wb.xlsx"
    mapping.write_workbook(SECTIONS, PROGRAM, str(out))
    xl = pd.ExcelFile(out)
    assert set(xl.sheet_names) == {"sections", "catalog", "programs"}
