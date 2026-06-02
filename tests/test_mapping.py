import pandas as pd

import engine
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
                                "Cap Enrl", "Tot Enrl", "Wait Tot", "Avail Status"]
    assert (df["Class Status"] == "Active").all()
    assert (df[["Cap Enrl", "Tot Enrl", "Wait Tot"]] == 0).all().all()
    # these synthetic records carry no live 'status' -> Avail Status blank
    assert (df["Avail Status"] == "").all()
    assert set(df["CLASS"]) == {"CS 101", "MATH 245"}
    assert pd.api.types.is_integer_dtype(df["Term"])


def test_build_sections_df_carries_live_waitlist_status():
    """A live record's availability status flows into the optional Avail Status
    column (the under_supply live signal); lifecycle (Class Status) stays Active."""
    df = mapping.build_sections_df([
        {"term": 2268, "course": "BIOLOGY 006", "units": "4.00", "status": "Waitlist"},
        {"term": 2268, "course": "BIOLOGY 006", "units": "4.00", "status": "Open"},
    ])
    assert list(df["Avail Status"]) == ["Waitlist", "Open"]
    assert (df["Class Status"] == "Active").all()


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


# --- m7-s4: additive enrollment + prereq seams -------------------------------

# Sections carrying IR-derived enrollment counts (the build_sections_df seam).
SECTIONS_WITH_ENRL = [
    {"term": 2268, "course": "CS 101", "units": "3.00",
     "Cap Enrl": 40, "Tot Enrl": 35, "Wait Tot": 12},
    {"term": 2264, "course": "MATH 245", "units": "5.00",
     "Cap Enrl": 30, "Tot Enrl": 30, "Wait Tot": 18},
]


def test_build_sections_df_emits_enrollment_when_present():
    # When records carry Cap/Tot/Wait Enrl, build_sections_df passes them through
    # (the mapping.py:63-65 r.get seam) instead of hard-coding 0.
    df = mapping.build_sections_df(SECTIONS_WITH_ENRL)
    by_class = {row["CLASS"]: row for _, row in df.iterrows()}
    assert by_class["CS 101"]["Cap Enrl"] == 40
    assert by_class["CS 101"]["Tot Enrl"] == 35
    assert by_class["CS 101"]["Wait Tot"] == 12
    assert by_class["MATH 245"]["Wait Tot"] == 18


def test_build_sections_df_defaults_zero_without_enrollment():
    # Default (no Cap/Tot/Wait keys) stays byte-identical to today's behavior: 0.
    df = mapping.build_sections_df(SECTIONS)
    assert (df[["Cap Enrl", "Tot Enrl", "Wait Tot"]] == 0).all().all()


def test_build_catalog_df_threads_prereqs_map():
    # The prereqs map populates the structured-prereq column for matching course
    # ids; non-listed courses stay blank.
    df = mapping.build_catalog_df(SECTIONS, PROGRAM,
                                  prereqs={"CS 101": "(MATH 245)"})
    by_cid = dict(zip(df["Course ID"], df["Prerequisites (structured)"]))
    assert by_cid["CS 101"] == "(MATH 245)"
    assert by_cid["MATH 245"] == ""
    assert by_cid["PHYS 101"] == ""


def test_build_catalog_df_prereqs_none_keeps_all_blank():
    # Default prereqs=None reproduces today's all-blank column byte-identically.
    df_none = mapping.build_catalog_df(SECTIONS, PROGRAM)
    df_explicit_none = mapping.build_catalog_df(SECTIONS, PROGRAM, prereqs=None)
    assert (df_none["Prerequisites (structured)"] == "").all()
    assert df_none.equals(df_explicit_none)


def test_write_workbook_threads_prereqs_into_catalog(tmp_path):
    out = tmp_path / "wb_prereq.xlsx"
    mapping.write_workbook(SECTIONS, PROGRAM, str(out),
                           prereqs={"CS 101": "(MATH 245)"})
    catalog = pd.read_excel(out, sheet_name="catalog").fillna("")
    by_cid = dict(zip(catalog["Course ID"],
                      catalog["Prerequisites (structured)"]))
    assert by_cid["CS 101"] == "(MATH 245)"
    assert by_cid["MATH 245"] == ""
    # And the threaded prereq round-trips through the engine's CNF parser.
    assert engine.parse_prereq(by_cid["CS 101"]) == [["MATH 245"]]


def test_write_workbook_default_prereqs_keeps_catalog_blank(tmp_path):
    out = tmp_path / "wb_default.xlsx"
    mapping.write_workbook(SECTIONS, PROGRAM, str(out))
    catalog = pd.read_excel(out, sheet_name="catalog").fillna("")
    assert (catalog["Prerequisites (structured)"] == "").all()


def test_column_constants_match_engine_required_columns():
    # Drift guard. catalog/programs equal engine's contract exactly.
    assert mapping.CATALOG_COLUMNS == engine.REQUIRED_COLUMNS["catalog"]
    assert mapping.PROGRAM_COLUMNS == engine.REQUIRED_COLUMNS["programs"]
    # Sections carries every REQUIRED column (as a prefix) plus ONE optional
    # additive column the engine reads optionally — "Avail Status" (the live
    # waitlist signal) — intentionally NOT in REQUIRED_COLUMNS so demo / IR
    # workbooks without it still validate.
    req = engine.REQUIRED_COLUMNS["sections"]
    assert mapping.SECTION_COLUMNS[:len(req)] == req
    assert mapping.SECTION_COLUMNS == req + ["Avail Status"]
    assert "Avail Status" not in req


def test_ge_requirements_sheet_roundtrips(tmp_path):
    sections = [{"term": 2268, "course": "ART 101", "units": "3"}]
    program = {"code": "BIO", "title": "Biology", "courses": []}
    ge_rows = [
        {"area": "3A", "area_title": "Arts", "required_count": 1, "resolution": "concrete",
         "candidates": ["ART 101"], "recommended": "ART 101", "units": 3.0},
        {"area": "1A", "area_title": "English", "required_count": 1, "resolution": "reserve",
         "candidates": [], "recommended": "", "units": 3.0},
    ]
    out = tmp_path / "wb.xlsx"
    mapping.write_workbook(sections, program, str(out), pattern="igetc", ge_rows=ge_rows)
    xl = pd.ExcelFile(out)
    assert "ge_requirements" in xl.sheet_names
    df = xl.parse("ge_requirements")
    assert list(df.columns) == mapping.GE_REQUIREMENT_COLUMNS
    arts = df[df["Area"] == "3A"].iloc[0]
    assert arts["Pattern"] == "igetc"
    assert arts["Resolution"] == "concrete"
    assert arts["Candidate Course IDs"] == "ART 101"


def test_write_workbook_without_ge_omits_sheet(tmp_path):
    out = tmp_path / "wb.xlsx"
    mapping.write_workbook([{"term": 2268, "course": "ART 101"}],
                           {"code": "BIO", "title": "Biology", "courses": []}, str(out))
    assert "ge_requirements" not in pd.ExcelFile(out).sheet_names


def test_ge_requirements_none_units_defaults(tmp_path):
    df = mapping.build_ge_requirements_df(
        {"code": "BIO", "title": "Biology", "courses": []}, "igetc",
        [{"area": "1A", "area_title": "English", "required_count": 1,
          "resolution": "reserve", "candidates": [], "recommended": "", "units": None}])
    assert df.iloc[0]["Units"] == 3.0
