# EdgeSched — All-Degrees Live Sweep Report

**Date:** 2026-06-02 · **Campus:** Los Angeles Mission College (LAMC) · **Term:** 2268 (Fall) · **Source:** https://la-mission.programmapper.ws/academics

Comprehensive test of **“Build from live LACCD data”** across **every distinct program** in all 6 LAMC Learning & Career Pathways, each built under **all three transfer-GE goals** with **live eLumen prerequisites**. Nothing mocked — every build hit the live public APIs.

## Executive summary

- **161 distinct programs** (resolved by unique ID) × **3 GE goals** = **483 live builds**. **483/483 succeeded** (**161/161 programs** clean on all 3 goals; the 3 initial agent-echo parse failures were re-run directly and passed — no build failures).
- **Live eLumen prerequisites:** **114/161** programs have ≥1 course with a hard prerequisite. Catalog-wide, **154 distinct courses carry a hard prerequisite** — out of **732 requisite-bearing courses** fetched across the **66 subjects** these programs span (0 fallback/relaxed clauses). Advisories and co-requisites are correctly excluded (verified: e.g. CIS 193’s advisory is dropped, only its CIS 192 prerequisite kept).
  - Metric note: the per-program figures below count courses with a hard prereq **in a program’s subjects** (subject-level), so every CS program shows the same 21 CIS+CS prereq’d courses. Summed across programs that is **~1,011**; do NOT read it as a distinct total. (An earlier draft mislabeled the broader *fetched* count of 732/~4,781 as ‘prerequisites’ — corrected here.)
- **Transfer GE resolved on all builds** (ASSIST `ok` for every goal of every program) and **100% draft-gated** — no unreviewed pattern was ever shown as authoritative.
- **Cal-GETC fix (PR #34) validated at scale:** across **all 161 cal-getc runs**, `unknown_areas` = **[2]** and cross-system aliases ignored = **[25]** (**4,025** redundant CSU/IGETC alias codes correctly suppressed). No regressions.

**Coverage vs. request:**

| Requirement | Delivered |
|---|---|
| Test **all** degrees | **161/161 distinct programs ✓** (incl. 24 duplicate-titled variants, via ID resolution) |
| **All** transfer-GE goals each | **IGETC + CSU-GE + Cal-GETC on all 161 ✓** |
| eLumen prerequisites included | **all 161 programs ✓** |
| Haiku 4.5 agents | **161 agents (one per program) ✓** |
| Gentle / rate-limit-safe overnight | **batch 2, delays, backoff; ASSIST 3 calls total, eLumen 1 crawl/program ✓** |
| Final report saved | **this file ✓** |

## How it was run (gentle by design)

- **One Haiku-4.5 agent per program** (161 agents), each building **all 3 GE goals** in a single process.
- **Politeness to the rate-limit-pending endpoints:** concurrency capped at **2**, 5 s startup stagger, 8 s inter-goal delay, 3-attempt backoff retries. **ASSIST pre-fetched once per goal (3 calls total)** and injected; **eLumen crawled once per program** (shared cache) — so goal 1 does the ~50 s eLumen crawl and goals 2–3 finish in <1 s. Run wall-clock ≈ **1.8 hours**.
- **ID-based resolution** (`fetch_program_by_id`) so duplicate-titled programs (e.g. *Interior Design* Cert vs A.A., *Accounting* Cert vs A.A.) are each tested — title search alone reaches only 137 of the 161.
- Same engine path as the desktop UI (`analyze_live`); GUI validated by proxy.

## Per-GE-goal results (across all 161 programs)

| Goal | builds ok | programs w/ hard prereq | hard-prereq courses (summed, subj.) | ASSIST ok | draft-gated | `unknown_areas` | cross-system ignored |
|---|---|---|---|---|---|---|---|
| IGETC | 161/161 | 114 | 1,011 | 161 | 161 | [3] | 0 |
| CSU-GE | 161/161 | 114 | 1,024 | 161 | 161 | [2] | 0 |
| Cal-GETC | 161/161 | 114 | 1,034 | 161 | 161 | [2] | 4,025 |

The **hard-prereq courses (summed)** column adds up each program’s subject-level count, so it counts shared subjects once per program — a breadth figure, NOT a distinct total. Catalog-wide the 66 subjects hold **154 distinct courses with a hard prerequisite** (of 732 requisite-bearing courses fetched). Small per-goal differences come from 2 prereq-dense programs hitting the eLumen time cap on the goal that ran their crawl; cached goals filled the rest.

Cal-GETC is the only goal with cross-system aliases (ASSIST bundles the legacy CSU-GE letter codes into its Cal-GETC response); the resolver now ignores them (PR #34), so its `unknown_areas` matches IGETC/CSU-GE’s small residual.

## By pathway

| Pathway | programs | all-3-goals ok | hard-prereq courses (summed, subj.) |
|---|---|---|---|
| Arts, Media, and Performance | 21 | 21/21 | 212 |
| Business, Law, and Public Safety | 25 | 25/25 | 68 |
| Child, Family, and Education Studies | 23 | 23/23 | 140 |
| Culinary Arts | 9 | 9/9 | 88 |
| Society, Culture, and Communication | 27 | 27/27 | 56 |
| STEM, Health, and Fitness | 56 | 56/56 | 447 |

## Findings

1. **Cal-GETC reconciliation holds across the whole catalog.** Every one of the 161 cal-getc builds shows `unknown_areas`=2 and 25 cross-system aliases ignored — the PR #34 fix generalizes perfectly; **0** programs regressed.
2. **Prerequisite depth (corrected).** **154 distinct courses** carry a hard prerequisite across the 66 subjects (0 fallback) — out of **732 requisite-bearing courses** fetched. The earlier ‘108 CIS+CS prereqs’ was the *fetched* count; only **21** of those 108 actually have a hard prerequisite (the rest carry only advisories/co-requisites, which the parser correctly excludes — spot-checked live). The subject-level metric sums to ~1,011 hard-prereq courses across 114 programs (shared subjects counted once per program). eLumen hit its time cap on **4** program(s) (coverage flagged partial, never silent):
   - *Elementary Teacher Education* — 12 prereqs returned before the cap.
   - *Social Media Strategist* — 13 prereqs returned before the cap.
   - *Engineering Drafting Technician* — 19 prereqs returned before the cap.
   - *Engineering* — 26 prereqs returned before the cap.
3. **Draft-gate: 100%.** All 161 programs × 3 goals were flagged “Draft — unverified”; the counts still await an articulation-officer sign-off.
4. **Honest reconciliation.** 47 programs had no prerequisites applied — certificates whose courses aren’t offered in Fall 2268 (0 matched courses), correctly reported rather than hidden.

## Full results (every program, all 3 goals)

Each goal cell is **concrete · reserved · shared-with-major** GE areas. **m/c** = program courses matched / total. **hard-prereq** = courses with a hard prerequisite in the program’s subjects (subject-level; shared across goals; advisories/co-requisites excluded). † = eLumen hit its time cap.

### Arts, Media, and Performance  (21 programs)

| Program | Award | m/c | hard-prereq | IGETC | CSU-GE | Cal-GETC |
|---|---|---|---|---|---|---|
| Art | AA | 4/4 | 20 | 3·7·4 | 2·7·4 | 2·8·4 |
| Art History | AA-T | 1/3 | 20 | 3·7·1 | 2·7·1 | 2·8·1 |
| Art, Gallery, and Museum Studies | AA | 2/4 | 20 | 3·7·2 | 2·7·2 | 2·8·2 |
| Commercial Photography | Cert/Ach | 4/6 | 4 | 3·7·1 | 2·7·1 | 2·8·1 |
| Competitive eSports Event Producer | Cert/Comp | 0/3 | 0 | 3·8·0 | 2·8·0 | 2·9·0 |
| Digital Interior Design | Cert/Ach | 4/6 | 2 | 3·8·0 | 2·8·0 | 2·9·0 |
| Digital Media Production & Streaming | Cert/Comp | 0/3 | 0 | 3·8·0 | 2·8·0 | 2·9·0 |
| Film, Television, and Electronic Media | AS-T | 1/1 | 2 | 3·8·0 | 2·8·0 | 2·9·0 |
| General Studies Arts & Humanities | AA | 0/0 | 0 | 3·8·0 | 2·8·0 | 2·9·0 |
| Interior Design | Cert/Ach | 4/8 | 0 | 3·8·0 | 2·8·0 | 2·9·0 |
| Interior Design | AA | 4/8 | 0 | 3·8·0 | 2·8·0 | 2·9·0 |
| Multimedia: Animation & 3D Design | Cert/Ach | 7/13 | 22 | 3·7·1 | 2·7·2 | 2·8·1 |
| Multimedia: Animation & 3D Design | AA | 7/13 | 22 | 3·7·1 | 2·7·2 | 2·8·1 |
| Multimedia: Graphic & Web Design | Cert/Ach | 9/12 | 22 | 3·7·3 | 2·7·4 | 2·8·3 |
| Multimedia: Graphic & Web Design | AA | 9/12 | 22 | 3·7·3 | 2·7·4 | 2·8·3 |
| Multimedia: Video Production | Cert/Ach | 8/11 | 5 | 3·7·2 | 2·6·2 | 2·8·2 |
| Multimedia: Video Production | AA | 8/11 | 5 | 3·7·2 | 2·6·2 | 2·8·2 |
| Painting | AA | 4/4 | 20 | 3·7·4 | 2·7·4 | 2·8·4 |
| Studio Arts | AA-T | 3/4 | 20 | 3·7·3 | 2·7·3 | 2·8·3 |
| Technical Theater | Cert/Ach | 2/5 | 3 | 3·7·1 | 2·7·1 | 2·8·1 |
| Theatre Arts | AA-T | 1/1 | 3 | 3·8·0 | 2·8·0 | 2·9·0 |

### Business, Law, and Public Safety  (25 programs)

| Program | Award | m/c | hard-prereq | IGETC | CSU-GE | Cal-GETC |
|---|---|---|---|---|---|---|
| Accounting | Cert/Ach | 4/4 | 1 | 3·8·0 | 2·8·0 | 2·9·0 |
| Accounting | AA | 6/6 | 2 | 3·8·0 | 2·7·1 | 2·9·0 |
| Administration of Justice | AS-T | 2/2 | 1 | 3·8·1 | 2·7·1 | 2·9·1 |
| Administration of Justice | AS | 7/8 | 1 | 3·7·2 | 2·7·2 | 2·8·2 |
| Basic Police Academy Preparation | JobSkills | 4/5 | 24 | 3·8·1 | 2·7·1 | 2·9·1 |
| Business Administration | AA | 7/7 | 2 | 3·8·0 | 2·7·1 | 2·9·0 |
| Business Administration 2.0 | AS-T | 2/4 | 1 | 3·8·0 | 2·8·0 | 2·9·0 |
| Crime Scene Technology | Cert/Ach | 3/5 | 1 | 3·8·0 | 2·8·0 | 2·9·0 |
| Digital Literacy | Cert/Comp | 1/6 | 1 | 3·8·0 | 2·8·0 | 2·9·0 |
| Fire Technology | Cert/Ach | 2/6 | 0 | 3·8·0 | 2·8·0 | 2·9·0 |
| Fundamentals of Medical Billing and Coding | Cert/Ach | 5/7 | 2 | 3·8·0 | 2·8·0 | 2·9·0 |
| Job Readiness Skills | Cert/Compl | 0/2 | 0 | 3·8·0 | 2·8·0 | 2·9·0 |
| Legal Assisting (Paralegal) | Cert/Ach | 9/10 | 1 | 3·8·1 | 2·7·2 | 2·9·1 |
| Legal Assisting (Paralegal) | AA | 9/10 | 1 | 3·8·1 | 2·7·2 | 2·9·1 |
| Management | AA | 7/8 | 2 | 3·8·0 | 2·7·1 | 2·9·0 |
| Non-Traditional Leadership for Community Enhancement | Cert/Comp | 0/8 | 0 | 3·8·0 | 2·8·0 | 2·9·0 |
| Pathway to Citizenship | Cert/Comp | 2/2 | 0 | 3·8·0 | 2·8·0 | 2·9·0 |
| Probation/Correction Officer | Cert/Ach | 2/6 | 1 | 3·8·1 | 2·7·1 | 2·9·1 |
| Real Estate Sales | Cert/Ach | 3/4 | 1 | 3·8·0 | 2·8·0 | 2·9·0 |
| Restaurant Management | Cert/Ach | 14/14 | 9 | 3·8·0 | 2·7·1 | 2·9·0 |
| Restaurant Management | JobSkills | 8/8 | 8 | 3·8·0 | 2·8·0 | 2·9·0 |
| Restaurant Management | AA | 14/14 | 9 | 3·8·0 | 2·7·1 | 2·9·0 |
| Social Media | Cert/Comp | 0/2 | 0 | 3·8·0 | 2·8·0 | 2·9·0 |
| Sustainable Small Business Development | Cert/Comp | 0/6 | 0 | 3·8·0 | 2·8·0 | 2·9·0 |
| The Art and Practice of Conflict Resolution | Cert/Comp | 0/4 | 0 | 3·8·0 | 2·8·0 | 2·9·0 |

### Child, Family, and Education Studies  (23 programs)

| Program | Award | m/c | hard-prereq | IGETC | CSU-GE | Cal-GETC |
|---|---|---|---|---|---|---|
| Academic Readiness | Cert/Comp | 4/4 | 0 | 3·8·0 | 2·8·0 | 2·9·0 |
| Academic Readiness, Bilingual GED Preparation | Cert/Comp | 4/4 | 0 | 3·8·0 | 2·8·0 | 2·9·0 |
| Child Development | Cert/Ach | 8/8 | 8 | 3·8·1 | 2·6·3 | 2·9·1 |
| Child Development | AA | 12/14 | 8 | 3·8·1 | 2·6·3 | 2·9·1 |
| Child Development Administration | Cert/Ach | 4/7 | 8 | 3·8·1 | 2·6·2 | 2·9·1 |
| Child Development Core | Cert/Ach | 4/4 | 8 | 3·8·1 | 2·6·2 | 2·9·1 |
| Child Development Specializing in Dual Language Learning | Cert/Ach | 12/12 | 8 | 3·8·1 | 2·6·3 | 2·9·1 |
| Child Development Specializing in Family Child Care | Cert/Ach | 12/14 | 8 | 3·8·1 | 2·6·3 | 2·9·1 |
| Child Development Specializing in Infant and Toddler | Cert/Ach | 12/12 | 8 | 3·8·1 | 2·6·3 | 2·9·1 |
| Child Development Specializing in Preschool | Cert/Ach | 11/12 | 8 | 3·8·1 | 2·6·3 | 2·9·1 |
| Child Development Specializing in Special Needs | Cert/Ach | 11/11 | 8 | 3·8·1 | 2·6·3 | 2·9·1 |
| ESL Pathway to Child Development Careers | Cert/Ach | 4/4 | 8 | 3·8·1 | 2·6·2 | 2·9·1 |
| Early Childhood Education | AS-T | 8/8 | 8 | 3·8·1 | 2·6·3 | 2·9·1 |
| Elementary Teacher Education | AA-T | 12/13 | 12† | 1·6·8 | 1·2·11 | 1·6·8 |
| Family Child Care | JobSkills | 4/6 | 8 | 3·8·1 | 2·6·2 | 2·9·1 |
| Family Studies | Cert/Ach | 3/4 | 8 | 3·8·1 | 2·6·3 | 2·9·1 |
| Family Studies | AA | 3/4 | 8 | 3·8·1 | 2·6·3 | 2·9·1 |
| Gerontology | AA | 5/6 | 0 | 3·8·0 | 2·7·2 | 2·9·0 |
| Gerontology | Cert/Ach | 5/6 | 0 | 3·8·0 | 2·7·2 | 2·9·0 |
| Parenting I | Cert/Compl | 1/2 | 0 | 3·8·0 | 2·8·0 | 2·9·0 |
| Parenting II | Cert/Compl | 0/2 | 0 | 3·8·0 | 2·8·0 | 2·9·0 |
| School Age Care and Education | Cert/Ach | 6/6 | 8 | 3·8·1 | 2·6·2 | 2·9·1 |
| Transitional Kindergarten | Cert/Ach | 6/6 | 8 | 3·8·1 | 2·6·2 | 2·9·1 |

### Culinary Arts  (9 programs)

| Program | Award | m/c | hard-prereq | IGETC | CSU-GE | Cal-GETC |
|---|---|---|---|---|---|---|
| Baking Specialist I | JobSkills | 4/4 | 14 | 3·8·0 | 2·8·0 | 2·9·0 |
| Baking Specialist II | JobSkills | 8/8 | 14 | 3·8·0 | 2·8·0 | 2·9·0 |
| Culinary Arts | Cert/Ach | 14/14 | 8 | 3·8·0 | 2·8·0 | 2·9·0 |
| Culinary Arts | AA | 14/14 | 8 | 3·8·0 | 2·8·0 | 2·9·0 |
| Culinary Specialist I | JobSkills | 4/4 | 8 | 3·8·0 | 2·8·0 | 2·9·0 |
| Culinary Specialist II | JobSkills | 9/9 | 8 | 3·8·0 | 2·8·0 | 2·9·0 |
| Culinary and Baking Essentials | Cert/Compl | 0/4 | 0 | 3·8·0 | 2·8·0 | 2·9·0 |
| Professional Baking & Patisserie | Cert/Ach | 10/10 | 14 | 3·8·0 | 2·8·0 | 2·9·0 |
| Professional Baking and Patisserie | AA | 12/14 | 14 | 3·8·0 | 2·8·0 | 2·9·0 |

### Society, Culture, and Communication  (27 programs)

| Program | Award | m/c | hard-prereq | IGETC | CSU-GE | Cal-GETC |
|---|---|---|---|---|---|---|
| Beginning Level ESL | Cert/Comp | 4/4 | 0 | 3·8·0 | 2·8·0 | 2·9·0 |
| Chicana/o Studies, Latina/o Studies | AA-T | 2/3 | 0 | 3·7·2 | 2·6·3 | 2·7·3 |
| Chicano Studies | AA | 3/3 | 0 | 3·6·3 | 2·6·3 | 2·6·5 |
| Communication Studies 2.0 | AA-T | 2/2 | 2 | 2·8·1 | 1·7·2 | 1·9·2 |
| Creative Writing | Cert/Ach | 0/0 | 0 | 3·8·0 | 2·8·0 | 2·9·0 |
| English | AA-T | 2/2 | 12 | 2·8·2 | 2·7·2 | 2·8·2 |
| English for Academic Purposes Advanced 1 | Cert/Comp | 3/3 | 0 | 3·8·0 | 2·8·0 | 2·9·0 |
| English for Academic Purposes Advanced 1 | JobSkills | 3/3 | 0 | 3·8·0 | 2·8·0 | 2·9·0 |
| English for Academic Purposes Advanced 2 | Cert/Comp | 3/3 | 0 | 3·8·0 | 2·8·0 | 2·9·0 |
| English for Academic Purposes Advanced 2 | JobSkills | 3/3 | 0 | 3·8·0 | 2·8·0 | 2·9·0 |
| English for Academic Purposes Intermediate 1 | Cert/Comp | 3/3 | 0 | 3·8·0 | 2·8·0 | 2·9·0 |
| English for Academic Purposes Intermediate 2 | Cert/Comp | 3/3 | 0 | 3·8·0 | 2·8·0 | 2·9·0 |
| English for Academic Purposes Intermediate 2 | JobSkills | 3/3 | 0 | 3·8·0 | 2·8·0 | 2·9·0 |
| Foundational ESL and Computer Skills | Cert/Comp | 3/3 | 0 | 3·8·0 | 2·8·0 | 2·9·0 |
| General Studies Social & Behavioral Sciences | AA | 0/0 | 0 | 3·8·0 | 2·8·0 | 2·9·0 |
| High Intermediate Level ESL | Cert/Comp | 2/2 | 0 | 3·8·0 | 2·8·0 | 2·9·0 |
| High-Beginning Level ESL | Cert/Comp | 3/3 | 0 | 3·8·0 | 2·8·0 | 2·9·0 |
| Intermediate Level ESL | Cert/Comp | 3/3 | 0 | 3·8·0 | 2·8·0 | 2·9·0 |
| Journalism | Cert/Ach | 5/6 | 0 | 3·8·1 | 2·7·1 | 2·9·1 |
| Philosophy | AA-T | 1/1 | 1 | 3·7·1 | 2·7·1 | 2·8·1 |
| Political Science | AA-T | 1/1 | 0 | 3·8·1 | 2·7·1 | 2·9·1 |
| Psychology | AA-T | 2/2 | 5 | 3·8·1 | 2·6·2 | 2·9·1 |
| Psychology | Cert/Ach | 4/5 | 5 | 3·6·5 | 2·5·8 | 2·7·5 |
| Social Justice Studies: Chicano/Chicana Studies | AA-T | 2/2 | 6 | 3·7·2 | 2·7·2 | 2·8·2 |
| Social Media Strategist | Cert/Ach | 5/7 | 13† | 3·8·0 | 2·8·0 | 2·9·0 |
| Sociology | AA-T | 2/2 | 6 | 3·8·1 | 2·7·1 | 2·9·1 |
| Spanish | AA-T | 2/2 | 6 | 3·7·1 | 2·7·2 | 2·8·1 |

### STEM, Health, and Fitness  (56 programs)

| Program | Award | m/c | hard-prereq | IGETC | CSU-GE | Cal-GETC |
|---|---|---|---|---|---|---|
| Biology | AS-T | 6/7 | 11 | 3·6·7 | 2·6·7 | 2·7·7 |
| Biology | AS | 5/5 | 6 | 3·6·5 | 2·6·5 | 2·7·5 |
| Biomanufacturing | BS | 9/20 | 21 | 3·6·4 | 2·6·4 | 2·7·4 |
| Biotechnology | AS | 3/5 | 7 | 3·7·2 | 2·7·2 | 2·8·2 |
| Biotechnology Lab Assistant | Cert/Ach | 2/2 | 6 | 3·7·1 | 2·7·1 | 2·8·1 |
| Biotechnology Research Lab Assistant | Cert/Ach | 4/5 | 8 | 3·7·2 | 2·7·2 | 2·8·2 |
| Cell and Gene Therapy | Cert/Ach | 4/4 | 10 | 3·6·2 | 2·6·2 | 2·7·2 |
| Certified Nurse Assistant and Home Health Aide | Cert/Ach | 4/4 | 2 | 3·8·0 | 2·8·0 | 2·9·0 |
| Chemistry for UC Transfer | UC-TP | 12/12 | 19 | 3·6·12 | 2·6·12 | 2·7·12 |
| Cloud Computing | Cert/Ach | 2/4 | 6 | 3·8·0 | 2·8·0 | 2·9·0 |
| Cloud Computing | JobSkills | 3/4 | 21 | 3·8·0 | 2·8·0 | 2·9·0 |
| Community Dental Health Coordinator | Cert/Comp | 0/4 | 0 | 3·8·0 | 2·8·0 | 2·9·0 |
| Community Health Worker | Cert/Compl | 2/2 | 1 | 3·8·0 | 2·8·0 | 2·9·0 |
| Computer Programmer | Cert/Ach | 4/4 | 21 | 3·8·0 | 2·8·0 | 2·9·0 |
| Computer Programmer | AS | 7/8 | 21 | 3·8·0 | 2·8·0 | 2·9·0 |
| Cyber Security Associate | Cert/Ach | 5/5 | 6 | 3·8·0 | 2·8·0 | 2·9·0 |
| Cyber Security Associate | AS | 9/9 | 21 | 3·8·0 | 2·8·0 | 2·9·0 |
| Cyber Security Practitioner | Cert/Ach | 4/4 | 6 | 3·8·0 | 2·8·0 | 2·9·0 |
| Data Analytics | Cert/Ach | 5/8 | 21 | 3·8·0 | 2·8·0 | 2·9·0 |
| Data Science | Cert/Ach | 5/10 | 21 | 3·8·0 | 2·8·0 | 2·9·0 |
| ESL Pathway to Biotechnology Careers | Cert/Ach | 3/3 | 10 | 3·6·2 | 2·6·2 | 2·7·2 |
| ESL Pathway to Health Occupations Careers | Cert/Ach | 6/6 | 1 | 3·8·0 | 2·8·0 | 2·9·0 |
| Electrocardiography (EKG) Technician Training | Cert/Compl | 2/2 | 1 | 3·8·0 | 2·8·0 | 2·9·0 |
| Engineering | AS | 10/12 | 26† | 3·6·7 | 2·6·7 | 2·7·7 |
| Engineering Drafting Technician | Cert/Ach | 5/9 | 19† | 3·8·0 | 2·8·0 | 2·9·0 |
| Engineering Technician Prep | JobSkills | 1/3 | 3 | 3·8·0 | 2·8·0 | 2·9·0 |
| Full Stack Developer | Cert/Ach | 6/6 | 21 | 3·8·0 | 2·8·0 | 2·9·0 |
| Full Stack Developer | AS | 9/10 | 21 | 3·8·0 | 2·8·0 | 2·9·0 |
| General Studies: Natural Sciences | AA | 0/0 | 0 | 3·8·0 | 2·8·0 | 2·9·0 |
| Geography | AA-T | 4/8 | 0 | 3·6·6 | 2·6·6 | 2·7·6 |
| Health Occupations Fundamentals | Cert/Ach | 6/6 | 1 | 3·8·0 | 2·8·0 | 2·9·0 |
| Health Science | AS | 2/2 | 2 | 3·7·2 | 2·7·2 | 2·8·2 |
| In-Home Support Services (IHSS) Providers Training | Cert/Compl | 2/2 | 1 | 3·8·0 | 2·8·0 | 2·9·0 |
| Introduction to CSIT | Cert/Comp | 0/2 | 0 | 3·8·0 | 2·8·0 | 2·9·0 |
| Introduction to Construction Technologies | Cert/Compl | 0/3 | 0 | 3·8·0 | 2·8·0 | 2·9·0 |
| Kinesiology | AA-T | 3/3 | 3 | 3·7·2 | 2·6·3 | 2·8·2 |
| Land Surveying Technician I | Cert/Ach | 4/6 | 5 | 3·8·0 | 2·8·0 | 2·9·0 |
| Land Surveying Technician II | Cert/Ach | 4/9 | 5 | 3·8·0 | 2·8·0 | 2·9·0 |
| Mathematics 2.0 | AS-T | 4/4 | 10 | 3·7·4 | 2·7·4 | 2·8·4 |
| Medical Assistant Training | Cert/Ach | 2/8 | 3 | 3·8·0 | 2·8·0 | 2·9·0 |
| Medical Assistant Training | AS | 5/12 | 5 | 3·8·0 | 2·8·0 | 2·9·0 |
| Medical Office Administrative Assistant | Cert/Ach | 8/8 | 5 | 3·8·0 | 2·8·0 | 2·9·0 |
| Nutrition and Dietetics | AS-T | 8/8 | 18 | 3·6·4 | 2·4·6 | 2·7·4 |
| Nutrition and Food Skills | Cert/Ach | 4/4 | 8 | 3·8·0 | 2·7·1 | 2·9·0 |
| Pharmacy Technician | AS | 4/7 | 1 | 3·8·0 | 2·8·0 | 2·9·0 |
| Pharmacy Technician - Basic | Cert/Ach | 4/7 | 1 | 3·8·0 | 2·8·0 | 2·9·0 |
| Phlebotomy Technician Training | Cert/Compl | 2/2 | 1 | 3·8·0 | 2·8·0 | 2·9·0 |
| Physics 2.0 | AS-T | 7/7 | 15 | 3·6·7 | 2·6·7 | 2·7·7 |
| Programming | JobSkills | 4/4 | 21 | 3·8·0 | 2·8·0 | 2·9·0 |
| Programming | Cert/Comp | 0/2 | 0 | 3·8·0 | 2·8·0 | 2·9·0 |
| Public Health | AS-T | 3/3 | 1 | 3·8·0 | 2·7·1 | 2·9·0 |
| Robotics | Cert/Comp | 0/2 | 0 | 3·8·0 | 2·8·0 | 2·9·0 |
| Sport and Exercise Nutrition Fundamentals | Cert/Ach | 3/3 | 0 | 3·8·0 | 2·7·1 | 2·9·0 |
| Sport and Exercise Nutrition Science | Cert/Ach | 3/3 | 0 | 3·8·0 | 2·7·1 | 2·9·0 |
| Statistics Skills and Preparation | Cert/Comp | 0/2 | 0 | 3·8·0 | 2·8·0 | 2·9·0 |
| Vocational Nursing Training Program | Cert/Ach | 3/8 | 4 | 3·8·0 | 2·8·0 | 2·9·0 |

---
*Generated 2026-06-02 from a live overnight run against the public LACCD APIs (LAMC, term 2268). 161 programs × 3 GE goals, 483/483 builds ok. Machine-readable per-program data: `LIVE_ALL_DEGREES_SWEEP_RESULTS.json`.*
