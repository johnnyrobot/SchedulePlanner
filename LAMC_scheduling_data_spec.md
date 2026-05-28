# LAMC 2-Year Completion Scheduling Analysis
## Data Request Specification

### Purpose
Identify scheduling and modality bottlenecks that prevent cohorts from completing 2-year LAMC programs in 2 years, and produce a recommendation report to Academic Affairs for term-by-term scheduling decisions.

### Scope
Aggregate and section-level data only. No student-level records. No FERPA exposure.

---

## 1. Time Frame

**Requested:** Eight consecutive main terms.

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

One row per section per term. Column labels below match the standard LACCD PeopleSoft enrollment report export format.

### Identifiers
- `Term`
- `Descr`
- `Campus`
- `Class Nbr`
- `Subject`
- `Catalog`
- `Section`
- `Session`

### Section Structure
- `Class Type`
- `Component`
- `Assoc`
- `Comb Sects ID`
- `Class Status`
- `Cancel Dt`

### Modality and Location
- `Mode`
- `IN_PERSON`
- `Location`
- `BUILDING`
- `Room Descr`
- `Facil ID`
- `Pacoima`

### Meeting Schedule
- `Mtg Start`
- `Mtg End`
- `Meetings`
- `M`
- `T`
- `W`
- `R`
- `F`
- `S`
- `N`
- `TBA`
- `TBA Hours`
- `DAYBLOCK`
- `HOURS`
- `STARTEND`
- `Class Start Date`
- `Class End Date`
- `Mtg Start Date`
- `Mtg End Date`
- `Nbr Mtgs`
- `LATE-START`

### Enrollment and Capacity
- `Cap Enrl`
- `Tot Enrl`
- `Wait Cap`
- `Wait Tot`
- `Combined Cap Enrl`
- `Combined Tot Enrl`
- `Combined Wait Cap`
- `Combined Wait Tot`
- `FILLD`
- `.FILLPERCNT`
- `ENRL`
- `LMT`

### Academic Context
- `Acad Org`
- `Dep`
- `Discipline`
- `IGETC`
- `OER`
- `FTE`
- `Class Workload Hrs`
- `LEVEL`
- `LOAD`
- `Banked`
- `Advanced`
- `Topic`
- `Grd Basis`
- `Primary Comp`
- `Graded Comp`

### Computed / Derived Fields
These are useful Excel-formula-derived fields that appear in existing reports and should be preserved if generated:

- `CLASS`
- `SEC`
- `DAYS`
- `ROOM`
- `(FT)`
- `LM`
- `COMBINED`
- `OvrUndLd`

---

## 3. Course Catalog Data (One-Time Pull)

From eLumen / official course outline of record. One row per course.

- Course ID (`Subject` + `Catalog`)
- Course title
- Units (lecture / lab / total)
- Prerequisites — **parsed into structured logical form, not free text**
- Corequisites — parsed
- Advisories
- GE area assignments
- IGETC area assignments
- Discipline code
- Course status (active / inactive / banked)
- Last revision date

**Critical:** Prerequisites must be delivered as structured logic, not free text. Free-text prereqs like "MATH 125 or equivalent or qualifying placement" must be parsed into actual logical expressions for the analysis to function.

---

## 4. Program Requirements (One-Time Pull)

From Program Mapper. One entry per program (AA / AS / AS-T / Certificate).

- Program code
- Program title
- Required courses (list of `Subject` + `Catalog`)
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

### Fields to exclude from delivery

The following identifying fields, which typically appear in PeopleSoft section dumps, **must be removed before delivery**:

- `ID`
- `SAP Primary ID`
- `SAP Secondary ID`
- `SAP Asgn ID (PNI #)`
- `Name`
- `Emails`
- `INSTRUCTOR`
- `INSTRUCTOR-LONG`
- `Instr SFP`
- `Instr Print`
- Any home address, phone, or personal contact field

If instructor *continuity* across terms is needed analytically (e.g., "the same person teaches BIOL 6 every Fall"), please provide a **hashed or anonymized instructor key** — a stable per-person identifier with no recoverable identity.

### Student-level data

No student-level data of any kind is requested. Specifically excluded:

- No student names, IDs, or SAP IDs
- No roster data
- No individual grades or GPAs
- No individual demographics
- No birth dates, addresses, phones, or emails
- No financial aid records at the individual level
- No transcripts or course-completion records tied to individuals

### Small cell suppression

For all aggregate or demographic data:

- Suppress any cell with fewer than 10 individuals
- Apply to ethnicity × program, gender × program × term, age × program, and any cross-tab
- Round date fields to month granularity where day-level precision is not analytically necessary

### Compliance posture

No data sharing agreement or IRB review anticipated, as no individual records are involved. This dataset is intended to support internal scheduling decisions only.

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

[To be filled in by requestor]
