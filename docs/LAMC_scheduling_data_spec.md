# LAMC 2-Year Completion Scheduling Analysis
## Data Request Specification

### Purpose
Identify scheduling and modality bottlenecks that prevent cohorts from completing 2-year LAMC programs in 2 years, and produce a recommendation report to Academic Affairs for term-by-term scheduling decisions.

### Scope
Aggregate and section-level data only. No student-level records. No FERPA exposure.

---

## 1. Time Frame

**Requested:** Eight consecutive main terms, plus the current upcoming term.

- Fall 2022
- Spring 2023
- Fall 2023
- Spring 2024
- Fall 2024
- Spring 2025
- Fall 2025
- Spring 2026

Winter and Summer intersessions are optional. The 2-year completion path is built on Fall + Spring, so those are the load-bearing terms.

**Minimum acceptable:** Four consecutive main terms (most recent 2 academic years).

**Rationale:** Rotation patterns ("offered Fall-only," "alternates years," "no evening section since 2023") only become visible across 4+ terms. Three years gives statistical confidence.

---

## 2. Section-Level Schedule Data (Core Dataset)

One row per section per term. Standard PeopleSoft export format is ideal.

### Identifiers
- Term code + Term description
- Campus (main vs Pacoima)
- Class Number (CRN)
- Subject
- Catalog Number
- Section
- Session

### Section Structure
- Class Type
- Component (LEC / LAB / DIS / etc.)
- Associated Class Number (for paired LEC+LAB)
- Combined Sections ID (for cross-listed)
- Class Status (Active / Cancelled)
- Cancel Date (if cancelled)

### Modality and Location
- Mode (modality code)
- In-Person flag
- Location / Building / Room
- Pacoima flag

### Meeting Schedule
- Meeting Start time
- Meeting End time
- Days pattern (e.g., MWF)
- Day-of-week flags (M, T, W, R, F, S, N, TBA)
- TBA Hours
- Class Start Date
- Class End Date
- Late-Start flag

### Enrollment and Capacity
- Enrollment Cap
- Total Enrolled
- Waitlist Cap
- Waitlist Total
- Combined Cap and Total (for cross-listed)
- Fill Percent

### Academic Context
- Academic Org (department)
- IGETC flag
- OER flag
- Class Workload Hours
- FTE
- Discipline

### Instructor (Internal Use)
- Name
- SAP Primary ID

These will be stripped or hashed before any external sharing.

---

## 3. Course Catalog Data (One-Time Pull)

From eLumen / official course outline of record. One row per course.

- Course ID (Subject + Catalog)
- Course title
- Units (lecture / lab / total)
- Prerequisites, parsed into structured logical form
- Corequisites, parsed
- Advisories
- GE area assignments
- IGETC area assignments
- Discipline code
- Course status (active / inactive / banked)
- Last revision date

**Critical:** Prerequisites must be parsed into structured form, not delivered as free text. Free-text prereqs like "MATH 125 or equivalent or qualifying placement" must be turned into actual logical expressions for the analysis to function.

---

## 4. Program Requirements (One-Time Pull)

From Program Mapper. One entry per program (AA / AS / AS-T / Certificate).

- Program code
- Program title
- Required courses (Subject + Catalog list)
- Restricted electives and required count per area
- Recommended semester-by-semester sequence
- GE pattern (Local AA / CSU GE-Breadth / IGETC)
- Total units required

---

## 5. Articulation Data (One-Time Pull)

From the Assist.org JSON API. For each LAMC course:

- CSU / UC course equivalencies
- CSU GE-Breadth area
- IGETC area
- Major prep mappings for the top transfer destinations (CSUN, CSULA, UCLA, Cal State Channel Islands)

---

## 6. Aggregate Context

- Term-level student demographics (ethnicity, gender, age bands)
- Full-time vs part-time enrollment ratio by term
- Cohort retention and completion rates from Vision for Success reports
- Local labor market hours by industry, from US Census ACS, for ZIPs 91342, 91331, and 91340

---

## 7. Delivery Format

Acceptable formats, in order of preference:

1. Excel (.xlsx) with consistent column headers across terms
2. CSV files with consistent column headers
3. Direct database extract from PeopleSoft if access is available

A single file per term is fine, as long as the schema is consistent across terms.

---

## 8. Privacy and Compliance

- No student-level data requested
- Instructor employee IDs and names are treated as internal directory information; will be stripped or hashed before any external publication
- Aggregate cells with fewer than 10 records will be suppressed in any published view
- No data sharing agreement or IRB review anticipated, as no individual records are involved

---

## 9. What This Enables

Once the full dataset is assembled, the following deliverables become possible:

1. **Bottleneck report** — required courses with chronic low fill, narrow rotation, or single-section offerings that block 2-year completion
2. **Modality mismatch report** — required courses offered only in modalities students do not fill
3. **Time-slot conflict map** — required courses scheduled against each other within a single program path
4. **Solver-recommended schedule** — minimum schedule changes that unblock the most cohort paths
5. **Equity stratification** — completion feasibility by student archetype (full-time day, working evening, part-time, parent, transfer-bound)

---

## 10. Point of Contact

| Field | Value |
| --- | --- |
| Name | _(requestor name)_ |
| Title / Role | _(e.g., Faculty, Researcher)_ |
| Department / Program | _(e.g., Academic Affairs)_ |
| Email | _(requestor email)_ |
| Phone | _(optional)_ |
| Date of request | _(YYYY-MM-DD)_ |
