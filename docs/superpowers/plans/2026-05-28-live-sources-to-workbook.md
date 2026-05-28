# Live Data Sources → EdgeSched Workbook Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port the LACCD live-schedule and Program Mapper public APIs into edgesched as standalone sync clients, map their output into the engine's `.xlsx` workbook schema, and prove the round-trip by running `engine.run()` on the generated workbook.

**Architecture:** A new `sources/` package holds dependency-light clients (`httpx` + stdlib) and a pure-pandas mapping layer. Network IO stays entirely outside `engine.run()` — the flow is fetch → map → write `.xlsx` → `engine.run(path)`. A CLI (`build_live_workbook.py`) orchestrates the live smoke check. `engine.py`/`app.py` are untouched.

**Tech Stack:** Python 3.10+, httpx (sync), pandas, openpyxl, pytest. APIs are public/unauthenticated.

**Spec:** `docs/superpowers/specs/2026-05-28-live-sources-to-workbook-design.md`

---

## File Structure

```
edgesched/
  sources/
    __init__.py             # empty package marker
    http.py                 # get_json() — injectable-client + browser UA
    schedule.py             # LACCD schedule client + fetch_sections()
    program_mapper.py       # Program Mapper client + fetch_program()
    mapping.py              # records -> 3 engine sheets -> write_workbook()
  build_live_workbook.py    # CLI: fetch live + map + write + engine.run()
  tests/
    conftest.py             # FakeClient/FakeResponse + fixtures
    test_http.py
    test_schedule_client.py
    test_program_mapper_client.py
    test_mapping.py
    test_live_roundtrip.py
  pytest.ini                # pythonpath=. so tests import engine + sources
  requirements.txt          # + httpx, pytest
```

**Verified facts driving the code (from live API probing + reading engine.py):**
- Engine reads only: sections `Term, CLASS, Class Status, Cap Enrl, Tot Enrl, Wait Tot`; catalog `Course ID, Units, Prerequisites (structured)`; programs `Program Code, Program Title, Course ID, Recommended Semester`.
- `Cap/Tot/Wait` must be **present** (analyze() indexes them) but the schedule API has no counts → emit `0`.
- Solver does `int(units.get(c,3))` → units must be numeric; the API returns `"3.00"` → coerce to float.
- Program Mapper returns 403 to non-browser User-Agents → the HTTP helper sets a browser UA.
- Program Mapper `awardShortTitle` is the award *type* ("Associate in Science for Transfer"), repeated across programs → derive `Program Code` from the program **title**, not awardShortTitle.
- PM `pathwayElements[].name` is already in `"SUBJ CAT"` form (e.g. `"ARTHIST 110"`); semester is `recommendedOpportunity.term.termNumber`; units is `recommendedOpportunity.minUnits` (float); only emit elements where `recommendedOpportunity.type == "COURSE"`.

---

### Task 0: Dependencies, package skeleton, test harness

**Files:**
- Modify: `requirements.txt`
- Create: `pytest.ini`, `sources/__init__.py`, `tests/conftest.py`

- [ ] **Step 1: Add runtime + dev deps to `requirements.txt`**

Replace the file contents with:

```
pandas>=2.0
openpyxl>=3.1
ortools>=9.8
pywebview>=5.0
httpx>=0.27
# AI layer is optional and talks to Ollama over HTTP; no Python dep required.
# Install Ollama separately from https://ollama.com/download

# --- dev/test ---
pytest>=8.0
```

- [ ] **Step 2: Install deps**

Run: `pip install -r requirements.txt`
Expected: httpx and pytest install successfully (pandas/ortools/etc. already present).

- [ ] **Step 3: Create `pytest.ini`**

```ini
[pytest]
pythonpath = .
testpaths = tests
```

- [ ] **Step 4: Create `sources/__init__.py`**

```python
"""Live LACCD data-source clients for EdgeSched (schedule, Program Mapper)."""
```

- [ ] **Step 5: Create `tests/conftest.py`**

```python
"""Shared test harness: an injectable fake HTTP client.

FakeClient mimics the slice of httpx.Client our get_json() helper uses:
get(url, params, headers) -> response with .raise_for_status() and .json().
Routes map a URL substring to a JSON payload.
"""
import pytest


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class FakeClient:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def get(self, url, params=None, headers=None):
        self.calls.append({"url": url, "params": params, "headers": headers or {}})
        for fragment, payload in self.routes.items():
            if fragment in url:
                return FakeResponse(payload)
        raise AssertionError(f"FakeClient: no route matches {url}")

    def close(self):
        return None


@pytest.fixture
def make_client():
    return lambda routes: FakeClient(routes)
```

- [ ] **Step 6: Verify pytest collects (no tests yet is fine)**

Run: `pytest -q`
Expected: `no tests ran` (exit code 5) — confirms config loads without error.

- [ ] **Step 7: Commit**

```bash
git add requirements.txt pytest.ini sources/__init__.py tests/conftest.py
git commit -m "chore: add httpx/pytest deps, sources package, and test harness"
```

---

### Task 1: HTTP helper (`sources/http.py`)

**Files:**
- Create: `sources/http.py`
- Test: `tests/test_http.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_http.py
from sources.http import get_json


def test_get_json_uses_injected_client_and_returns_payload(make_client):
    client = make_client({"http://x/data": {"ok": True}})
    result = get_json("http://x/data", client=client)
    assert result == {"ok": True}


def test_get_json_sets_browser_user_agent(make_client):
    # Program Mapper 403s on non-browser UAs; the helper must always send one.
    client = make_client({"http://x/data": {"ok": True}})
    get_json("http://x/data", client=client)
    assert "Mozilla" in client.calls[0]["headers"].get("User-Agent", "")


def test_get_json_merges_caller_headers(make_client):
    client = make_client({"http://x/data": {"ok": True}})
    get_json("http://x/data", headers={"Origin": "https://example.org"}, client=client)
    sent = client.calls[0]["headers"]
    assert sent["Origin"] == "https://example.org"
    assert "User-Agent" in sent
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_http.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sources.http'`

- [ ] **Step 3: Write minimal implementation**

```python
# sources/http.py
"""Tiny HTTP helper with the injectable-client pattern.

Every source client accepts an optional httpx.Client so callers can reuse one
connection across many requests (and tests can inject a fake). When no client
is passed, get_json creates and closes its own. A browser User-Agent is always
sent because the Program Mapper API rejects script UAs with HTTP 403.
"""
from __future__ import annotations

import httpx

DEFAULT_TIMEOUT = 30.0
DEFAULT_HEADERS = {
    "Accept": "application/json",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
}


def get_json(url, *, params=None, headers=None, client=None, timeout=DEFAULT_TIMEOUT):
    owns_client = client is None
    client = client or httpx.Client(timeout=timeout)
    try:
        merged = {**DEFAULT_HEADERS, **(headers or {})}
        resp = client.get(url, params=params, headers=merged)
        resp.raise_for_status()
        return resp.json()
    finally:
        if owns_client:
            client.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_http.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add sources/http.py tests/test_http.py
git commit -m "feat: add injectable get_json HTTP helper with browser UA"
```

---

### Task 2: Schedule client (`sources/schedule.py`)

**Files:**
- Create: `sources/schedule.py`
- Test: `tests/test_schedule_client.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_schedule_client.py
from sources import schedule

LISTING_2268 = {
    "campuscode": "LAMC", "campusname": "Mission College",
    "termcode": "2268", "termname": "Fall 2026",
    "subjects": [{
        "code": "MATH", "name": "Mathematics", "courses": [{
            "subject": "MATH", "catalogNbr": "215", "descr": "Math Concepts",
            "units": "3.00", "sections": [{
                "classNbr": "13955 (LEC)", "seats": "35", "woi": "16",
                "dates": "08/31/26 - 12/20/26", "status": "Open",
                "meetings": [{"days": "T", "times": "8:50 AM", "room": "CMS 128",
                              "instr": "E. Sargsyan"}],
                "relsections": [{
                    "classNbr": "13956 (LAB)", "seats": "35", "woi": "16",
                    "status": "Open",
                    "meetings": [{"days": "Th", "times": "9:50 AM", "room": "CMS 128",
                                  "instr": "E. Sargsyan"}],
                    "relsections": [], "classType": ["HYFLEX", "OER"]}],
                "classType": ["HYFLEX", "OER"]}]}]}],
}


def test_fetch_sections_flattens_relsections(make_client):
    client = make_client({"/listing/LAMC/2268": LISTING_2268})
    records = schedule.fetch_sections("LAMC", [2268], client=client)
    # one LEC + its one LAB relsection
    assert len(records) == 2
    assert {r["course"] for r in records} == {"MATH 215"}
    assert records[0]["term"] == 2268
    assert records[0]["units"] == "3.00"
    assert records[0]["status"] == "Open"
    assert records[0]["modality"] == ["HYFLEX", "OER"]
    assert records[1]["class_nbr"] == "13956 (LAB)"


def test_fetch_sections_requests_each_term(make_client):
    client = make_client({"/listing/LAMC/": LISTING_2268})
    schedule.fetch_sections("LAMC", [2264, 2268], client=client)
    urls = [c["url"] for c in client.calls]
    assert any("/listing/LAMC/2264" in u for u in urls)
    assert any("/listing/LAMC/2268" in u for u in urls)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_schedule_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sources.schedule'`

- [ ] **Step 3: Write minimal implementation**

```python
# sources/schedule.py
"""LACCD live class-schedule client (public, unauthenticated).

Ported from project_laccd_chatbot live_schedule.py: synchronous, no langfuse,
no app.config, no cache. The API exposes section structure, modality, units and
an Open/Closed/Waitlist status — but NOT enrollment/capacity/waitlist counts.
"""
from __future__ import annotations

from .http import get_json

API_BASE = "https://services.laccd.edu/apps/api/classschedule"
# Currently-published terms as of 2026-05; override per call as needed.
DEFAULT_TERMS = [2264, 2266, 2268]


def get_subjects(campus, term, *, client=None):
    return get_json(f"{API_BASE}/subjects/{campus}/{term}", client=client)


def get_class_listing(campus, term, subjects=None, *, client=None):
    subjectlist = ",".join(sorted(subjects)) if subjects else ""
    return get_json(
        f"{API_BASE}/listing/{campus}/{term}",
        params={"subjectlist": subjectlist},
        client=client,
    )


def _iter_sections(course):
    """Yield each section then its relsections (lab/lecture linkage), flat."""
    for section in course.get("sections", []):
        yield section
        for rel in section.get("relsections", []):
            yield rel


def fetch_sections(campus, terms=None, *, client=None):
    """Return a flat list of section records across the given terms."""
    terms = terms or DEFAULT_TERMS
    records = []
    for term in terms:
        listing = get_class_listing(campus, str(term), client=client)
        for subject in listing.get("subjects", []):
            for course in subject.get("courses", []):
                subj = (course.get("subject") or "").strip()
                catalog = (course.get("catalogNbr") or "").strip()
                cls = f"{subj} {catalog}"
                for section in _iter_sections(course):
                    meeting = (section.get("meetings") or [{}])[0]
                    records.append({
                        "term": int(term),
                        "subject": subj,
                        "catalog": catalog,
                        "course": cls,
                        "title": course.get("descr", ""),
                        "units": course.get("units", ""),
                        "class_nbr": section.get("classNbr", ""),
                        "status": section.get("status", ""),
                        "seats": section.get("seats", ""),
                        "woi": section.get("woi", ""),
                        "modality": section.get("classType", []),
                        "days": meeting.get("days", ""),
                        "times": meeting.get("times", ""),
                        "room": meeting.get("room", ""),
                        "instructor": meeting.get("instr", ""),
                    })
    return records
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_schedule_client.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add sources/schedule.py tests/test_schedule_client.py
git commit -m "feat: add sync LACCD schedule client with section flattening"
```

---

### Task 3: Program Mapper client (`sources/program_mapper.py`)

**Files:**
- Create: `sources/program_mapper.py`
- Test: `tests/test_program_mapper_client.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_program_mapper_client.py
from sources import program_mapper as pm

HOME = {"programGroups": [{"masterRecordId": "g1", "title": "STEM"}]}
GROUP_G1 = {"programs": [{
    "masterRecordId": "p1", "title": "Computer Science",
    "awardShortTitle": "Associate in Science for Transfer"}]}
PROGRAM_P1 = {"pathways": [{"defaultPathway": True, "programMapId": "m1"}]}
MAP_M1 = {"pathwayElements": [
    {"name": "CS 101", "shortDescription": "Intro CS",
     "requirement": {"requirementType": "MAJOR_CORE"},
     "recommendedOpportunity": {"type": "COURSE", "term": {"termNumber": 1},
                                "courseCode": "CS 101", "minUnits": 3.0}},
    {"name": "MATH 245", "shortDescription": "Calculus I",
     "requirement": {"requirementType": "MAJOR_REQUIRED"},
     "recommendedOpportunity": {"type": "COURSE", "term": {"termNumber": 2},
                                "courseCode": "MATH 245", "minUnits": 5.0}},
    {"name": None, "recommendedOpportunity": {"type": "MILESTONE"}},
]}

ROUTES = {
    "/home-page-content": HOME,
    "/program-groups/g1": GROUP_G1,
    "/programs/p1": PROGRAM_P1,
    "/program-maps/m1": MAP_M1,
}


def test_search_program_matches_by_title(make_client):
    client = make_client(ROUTES)
    found = pm.search_program("LAMC", "computer science", client=client)
    assert found["masterRecordId"] == "p1"


def test_fetch_program_returns_courses_with_semester_and_units(make_client):
    client = make_client(ROUTES)
    prog = pm.fetch_program("LAMC", "computer science", client=client)
    assert prog["title"] == "Computer Science"
    assert prog["code"] == "COMPUTER-SCIENCE"          # derived from title, not award
    ids = [c["course_id"] for c in prog["courses"]]
    assert ids == ["CS 101", "MATH 245"]               # MILESTONE element skipped
    cs = prog["courses"][0]
    assert cs["recommended_semester"] == 1
    assert cs["units"] == 3.0


def test_fetch_program_returns_none_when_no_match(make_client):
    client = make_client(ROUTES)
    assert pm.fetch_program("LAMC", "underwater basket weaving", client=client) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_program_mapper_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sources.program_mapper'`

- [ ] **Step 3: Write minimal implementation**

```python
# sources/program_mapper.py
"""LACCD Program Mapper client (public, unauthenticated).

Ported from project_laccd_chatbot program_mapper.py: synchronous, no langfuse,
no app.config, no cache. Spoofs the campus PM frontend Origin/Referer (no
credentials). Resolves a program by name query and returns its required courses
with recommended semester + units, parsed from pathwayElements.
"""
from __future__ import annotations

import re

from .http import get_json

API_BASE = "https://b.api.programmapper.com"

COLLEGE_CONFIGS = {
    "LAMC":  {"name": "Los Angeles Mission College",     "origin": "https://la-mission.programmapper.ws",        "site_content_id": "0055f609-1a83-4937-8356-c67ec89cb496"},
    "LAVC":  {"name": "Los Angeles Valley College",      "origin": "https://programmap.lavc.edu",                 "site_content_id": "b42b1741-63ac-4bcf-95b6-48288af8733d"},
    "LAPC":  {"name": "Los Angeles Pierce College",      "origin": "https://programmapper.piercecollege.edu",     "site_content_id": "a10412a2-4b0f-493e-a7d0-2d8c4b1af0e2"},
    "LAHC":  {"name": "Los Angeles Harbor College",      "origin": "https://la-harbor.programmapper.com",         "site_content_id": "170b2c8d-6880-48fe-aea2-d2017ffabe27"},
    "LATTC": {"name": "Los Angeles Trade-Tech College",  "origin": "https://la-trade-tech.programmapper.ws",      "site_content_id": "3973c13e-2554-42a2-aede-02f223d887d0"},
    "LACC":  {"name": "Los Angeles City College",        "origin": "https://la-city.programmapper.ws",            "site_content_id": "82f8d72b-b23d-4f3b-8c4e-efc491c536ff"},
    "ELAC":  {"name": "East Los Angeles College",        "origin": "https://east-la.programmapper.com",           "site_content_id": "679f91e9-a94b-45f3-b0d5-4bae183a3f91"},
    "LASC":  {"name": "Los Angeles Southwest College",   "origin": "https://la-southwest.programmapper.ws",       "site_content_id": "c412a3e5-ac95-4de6-9def-f17f44deedfc"},
    "WLAC":  {"name": "West Los Angeles College",        "origin": "https://west-la.programmapper.ws",            "site_content_id": "b72f9ee4-f902-4c14-9088-f4298008f569"},
}


def _headers(campus):
    cfg = COLLEGE_CONFIGS[campus]
    return {"Origin": cfg["origin"], "Referer": f"{cfg['origin']}/"}


def _site_url(campus, suffix):
    scid = COLLEGE_CONFIGS[campus]["site_content_id"]
    return f"{API_BASE}/site-contents/{scid}{suffix}"


def get_all_programs(campus, *, client=None):
    home = get_json(_site_url(campus, "/home-page-content"),
                    headers=_headers(campus), client=client)
    programs = []
    for group in home.get("programGroups", []):
        data = get_json(_site_url(campus, f"/program-groups/{group['masterRecordId']}"),
                        headers=_headers(campus), client=client)
        for program in data.get("programs", []):
            program["group_title"] = group.get("title")
            programs.append(program)
    return programs


def search_program(campus, query, *, client=None):
    needle = re.sub(r"\s+", " ", query.strip().lower())
    for program in get_all_programs(campus, client=client):
        haystack = f"{program.get('title', '')} {program.get('awardShortTitle', '')}".lower()
        if needle in haystack:
            return program
    return None


def get_program_courses(campus, program_id, *, client=None):
    detail = get_json(_site_url(campus, f"/programs/{program_id}"),
                      headers=_headers(campus), client=client)
    pathways = detail.get("pathways", [])
    chosen = next((p for p in pathways if p.get("defaultPathway")),
                  pathways[0] if pathways else None)
    courses = []
    if chosen and chosen.get("programMapId"):
        reqs = get_json(_site_url(campus, f"/program-maps/{chosen['programMapId']}"),
                        headers=_headers(campus), client=client)
        for element in reqs.get("pathwayElements", []):
            opp = element.get("recommendedOpportunity") or {}
            if opp.get("type") != "COURSE":
                continue
            code = element.get("name") or opp.get("courseCode")
            if not code:
                continue
            term = opp.get("term") or {}
            courses.append({
                "course_id": code.strip(),
                "title": element.get("shortDescription") or opp.get("courseName", ""),
                "recommended_semester": term.get("termNumber"),
                "units": opp.get("minUnits"),
                "requirement_type": (element.get("requirement") or {}).get("requirementType"),
            })
    return courses


def _slug(text):
    return re.sub(r"[^A-Za-z0-9]+", "-", text.strip()).strip("-").upper()


def fetch_program(campus, query, *, client=None):
    program = search_program(campus, query, client=client)
    if program is None:
        return None
    courses = get_program_courses(campus, program["masterRecordId"], client=client)
    code = _slug(program.get("title", "")) or program["masterRecordId"][:8].upper()
    return {
        "code": code,
        "title": program.get("title", ""),
        "award": program.get("awardShortTitle", ""),
        "ge_pattern": "",
        "courses": courses,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_program_mapper_client.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add sources/program_mapper.py tests/test_program_mapper_client.py
git commit -m "feat: add sync Program Mapper client resolving program courses"
```

---

### Task 4: Mapping layer (`sources/mapping.py`)

**Files:**
- Create: `sources/mapping.py`
- Test: `tests/test_mapping.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_mapping.py
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


def test_build_sections_df_schema_and_zero_enrollment():
    df = mapping.build_sections_df(SECTIONS)
    assert list(df.columns) == ["Term", "CLASS", "Class Status",
                                "Cap Enrl", "Tot Enrl", "Wait Tot"]
    assert (df["Class Status"] == "Active").all()
    assert (df[["Cap Enrl", "Tot Enrl", "Wait Tot"]] == 0).all().all()
    assert set(df["CLASS"]) == {"CS 101", "MATH 245"}


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_mapping.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sources.mapping'`

- [ ] **Step 3: Write minimal implementation**

```python
# sources/mapping.py
"""Map live-source records into the engine's workbook schema.

Emits exactly the columns engine.py reads. Enrollment columns are 0 (the
schedule API has no counts) and prerequisites are blank (needs eLumen) — both
are expected gaps documented in the design doc, not failures.
"""
from __future__ import annotations

import re

import pandas as pd

SECTION_COLUMNS = ["Term", "CLASS", "Class Status", "Cap Enrl", "Tot Enrl", "Wait Tot"]
CATALOG_COLUMNS = ["Course ID", "Units", "Prerequisites (structured)"]
PROGRAM_COLUMNS = ["Program Code", "Program Title", "Course ID", "Recommended Semester"]


def _norm(code):
    return re.sub(r"\s+", " ", str(code).strip().upper())


def _to_units(value, default=3.0):
    """Coerce '3.00', '3-4', 5.0, '' -> float (solver does int(units))."""
    try:
        return float(str(value).split("-")[0])
    except (ValueError, TypeError):
        return default


def build_sections_df(section_records):
    rows = [{
        "Term": int(r["term"]),
        "CLASS": _norm(r["course"]),
        "Class Status": "Active",
        "Cap Enrl": 0,
        "Tot Enrl": 0,
        "Wait Tot": 0,
    } for r in section_records]
    return pd.DataFrame(rows, columns=SECTION_COLUMNS)


def build_catalog_df(section_records, program):
    units = {}
    for r in section_records:
        units.setdefault(_norm(r["course"]), _to_units(r.get("units")))
    for c in (program or {}).get("courses", []):
        units.setdefault(_norm(c["course_id"]), _to_units(c.get("units")))
    rows = [{"Course ID": cid, "Units": u, "Prerequisites (structured)": ""}
            for cid, u in sorted(units.items())]
    return pd.DataFrame(rows, columns=CATALOG_COLUMNS)


def build_programs_df(program):
    rows = [{
        "Program Code": program["code"],
        "Program Title": program["title"],
        "Course ID": _norm(c["course_id"]),
        "Recommended Semester": c.get("recommended_semester"),
    } for c in (program or {}).get("courses", [])]
    return pd.DataFrame(rows, columns=PROGRAM_COLUMNS)


def reconcile_courses(section_records, program):
    section_codes = {_norm(r["course"]) for r in section_records}
    program_codes = {_norm(c["course_id"]) for c in (program or {}).get("courses", [])}
    matched = sorted(program_codes & section_codes)
    unmatched = sorted(program_codes - section_codes)
    return matched, unmatched


def write_workbook(section_records, program, path):
    sections = build_sections_df(section_records)
    catalog = build_catalog_df(section_records, program)
    programs = build_programs_df(program)
    with pd.ExcelWriter(path, engine="openpyxl") as xl:
        sections.to_excel(xl, sheet_name="sections", index=False)
        catalog.to_excel(xl, sheet_name="catalog", index=False)
        programs.to_excel(xl, sheet_name="programs", index=False)
    return path
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_mapping.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add sources/mapping.py tests/test_mapping.py
git commit -m "feat: add mapping layer emitting engine workbook schema"
```

---

### Task 5: Round-trip smoke test (`tests/test_live_roundtrip.py`)

**Files:**
- Test: `tests/test_live_roundtrip.py`

This task adds no production code — it proves the boundary contract: mapped live
data produces a workbook `engine.run()` accepts and analyzes. Offline (no network).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_live_roundtrip.py
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
    ],
}


def test_mapped_workbook_runs_through_engine(tmp_path):
    out = tmp_path / "live.xlsx"
    mapping.write_workbook(SECTIONS, PROGRAM, str(out))

    results = engine.run(str(out))

    # data summary
    assert results["terms_in_data"] == 2
    # all four diagnostic buckets present (fill/waitlist will be empty by design)
    assert set(results["analysis"]) == {
        "rotation_gaps", "single_section", "modality_mismatch", "under_supply"}
    # CS 101 offered in only 1 of 2 terms -> a rotation gap is surfaced
    assert any(g["course"] == "CS 101" for g in results["analysis"]["rotation_gaps"])
    # enrollment-driven detectors are inert (Cap/Tot/Wait = 0)
    assert results["analysis"]["modality_mismatch"] == []
    assert results["analysis"]["under_supply"] == []
    # program solved for the full-time cohort
    prog = results["programs"]["COMPUTER-SCIENCE"]
    assert prog["title"] == "Computer Science"
    full_time = prog["cohorts"]["full_time"]
    assert full_time is not None
    assert "plan" in full_time
```

- [ ] **Step 2: Run test to verify it fails (then passes)**

Run: `pytest tests/test_live_roundtrip.py -v`
Expected: PASS immediately — Tasks 1–4 already provide everything. (If it FAILS,
the failure pinpoints a mapping↔engine schema mismatch to fix before continuing.)

- [ ] **Step 3: Run the whole suite**

Run: `pytest -q`
Expected: all tests pass (Tasks 1–5).

- [ ] **Step 4: Commit**

```bash
git add tests/test_live_roundtrip.py
git commit -m "test: prove mapped live data round-trips through engine.run"
```

---

### Task 6: CLI orchestrator + optional live test (`build_live_workbook.py`)

**Files:**
- Create: `build_live_workbook.py`
- Test: `tests/test_live_roundtrip.py` (append one network-gated test)

- [ ] **Step 1: Write the implementation**

```python
# build_live_workbook.py
"""Demo / smoke check: fetch live LACCD data, write a workbook, run the engine.

Network IO lives here, OUTSIDE engine.run(). Usage:
  python build_live_workbook.py --campus LAMC --terms 2264,2266,2268 \
      --program "Computer Science" --out data/live_LAMC.xlsx
"""
from __future__ import annotations

import argparse
import json
import os

import httpx

import engine
from sources import mapping, program_mapper, schedule


def build(campus, terms, program_query, out):
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with httpx.Client(timeout=30.0) as client:
        sections = schedule.fetch_sections(campus, terms, client=client)
        program = program_mapper.fetch_program(campus, program_query, client=client)
    return sections, program


def main():
    ap = argparse.ArgumentParser(description="Build an EdgeSched workbook from live LACCD sources.")
    ap.add_argument("--campus", default="LAMC")
    ap.add_argument("--terms", default="2264,2266,2268")
    ap.add_argument("--program", default="Computer Science")
    ap.add_argument("--out", default="data/live_LAMC.xlsx")
    args = ap.parse_args()
    terms = [int(t) for t in args.terms.split(",") if t.strip()]

    sections, program = build(args.campus, terms, args.program, args.out)
    if program is None:
        print(f"No program matched {args.program!r}. Try a different --program.")
        return

    matched, unmatched = mapping.reconcile_courses(sections, program)
    mapping.write_workbook(sections, program, args.out)

    print(f"Wrote {args.out}: {len(sections)} sections across {len(terms)} terms; "
          f"program {program['title']!r} ({len(program['courses'])} courses).")
    print(f"Course reconciliation: {len(matched)} matched, "
          f"{len(unmatched)} unmatched (not offered in fetched terms): {unmatched}")
    print("NOTE: Cap/Tot/Wait = 0 -> modality_mismatch and under_supply detectors are "
          "INERT (need the IR PeopleSoft enrollment export, PRD M4). Prerequisites are "
          "blank (need eLumen) -> solver runs without ordering constraints.")

    results = engine.run(args.out)
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Append a network-gated live test**

Add to the END of `tests/test_live_roundtrip.py`:

```python
import pytest


@pytest.mark.live
def test_live_lamc_end_to_end(tmp_path):
    """Hits the real LACCD APIs. Run with: pytest -m live"""
    import build_live_workbook
    out = tmp_path / "live_real.xlsx"
    sections, program = build_live_workbook.build(
        "LAMC", [2268], "Computer Science", str(out))
    assert len(sections) > 0
    if program is not None:
        mapping.write_workbook(sections, program, str(out))
        results = engine.run(str(out))
        assert results["terms_in_data"] >= 1
```

- [ ] **Step 3: Register the `live` marker so default runs skip it**

Modify `pytest.ini` to:

```ini
[pytest]
pythonpath = .
testpaths = tests
markers =
    live: hits real external APIs (deselected by default)
addopts = -m "not live"
```

- [ ] **Step 4: Run the default suite (live test skipped)**

Run: `pytest -q`
Expected: all offline tests pass; the `live` test is deselected (not run).

- [ ] **Step 5: Run the live smoke check manually (network required)**

Run: `python build_live_workbook.py --campus LAMC --terms 2268 --program "Computer Science"`
Expected: prints section/program counts, the reconciliation line, the INERT-detectors
NOTE, and a JSON results dict with `terms_in_data`, the four analysis keys, and at
least one program. (If `--program` finds no match, it prints a "No program matched"
message — pick another, e.g. `--program "Art History"`.)

- [ ] **Step 6: Commit**

```bash
git add build_live_workbook.py tests/test_live_roundtrip.py pytest.ini
git commit -m "feat: add live workbook CLI and network-gated smoke test"
```

---

## Self-Review

**1. Spec coverage:**
- §2 boundary (network outside engine.run) → Task 6 CLI is the only network caller; engine untouched. ✓
- §5 file layout → Tasks 0–6 create every listed file. ✓
- §6.1 http helper (injectable client) → Task 1. ✓
- §6.2 schedule client → Task 2. ✓
- §6.3 program mapper client → Task 3. ✓
- §6.4 mapping (table + reconcile + write_workbook) → Task 4. ✓
- §6.5 CLI orchestrator → Task 6. ✓
- §7 honest gaps (Cap/Tot/Wait=0, blank prereqs, banner) → Task 4 (zeros/blank) + Task 6 (banner) + Task 5 asserts detectors empty. ✓
- §9 testing (offline default + one @pytest.mark.live) → Tasks 1–5 offline, Task 6 live marker + addopts skip. ✓
- §10 out of scope (no eLumen/Assist, no cache, no engine wiring) → none added. ✓

**2. Placeholder scan:** No TBD/TODO; every code/test step contains complete code; every command has expected output. ✓

**3. Type consistency:** Section record keys (`term`, `course`, `units`) produced by `schedule.fetch_sections` (Task 2) are exactly the keys consumed by `mapping.build_*` (Task 4) and the round-trip fixtures (Task 5). `fetch_program` returns `{code, title, award, ge_pattern, courses:[{course_id, recommended_semester, units, ...}]}` (Task 3) — the same shape `mapping.build_programs_df`/`build_catalog_df` read (Task 4) and the CLI uses (Task 6). `write_workbook(section_records, program, path)` signature matches all call sites. ✓

**Deviation from spec (intentional, noted in plan header):** `Program Code` derives from the program **title** (`_slug`), not `awardShortTitle` — verified against live data where `awardShortTitle` is a non-unique award type. Captured in the "Verified facts" list.
