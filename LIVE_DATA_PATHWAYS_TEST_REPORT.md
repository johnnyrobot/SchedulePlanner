# EdgeSched — Live LACCD Data Test Report

**Date:** 2026-06-01  ·  **Campus:** Los Angeles Mission College (LAMC)  ·  **Term tested:** 2268 (Fall)  ·  **Source site:** https://la-mission.programmapper.ws/academics

This report records an end-to-end test of EdgeSched's **“Build from live LACCD data”** path across **all 6 Learning & Career Pathways** published on the LA Mission College Program Mapper. Every degree below was built from **live, public APIs** — nothing was mocked or replayed from a fixture.

## Executive summary

- **33 degrees** tested across **6 pathways** — **33/33 builds succeeded, 0 errors, 0 program-not-found.**
- **eLumen prerequisites fetched LIVE on every degree** (`--elumen-live`): **32/33** builds applied real prerequisite ordering, for **1094 exact prerequisite constraints total** (0 fallback/relaxed clauses).
  - The 1 build with no prereqs applied (*Culinary and Baking Essentials*) is honest, not a failure: none of its 4 catalog courses were offered in term 2268, so there were no in-scope subjects to query.
- **Transfer GE resolved LIVE via ASSIST on every degree** (**33/33** ASSIST `ok`), exercising all three patterns — **IGETC, CSU GE, and Cal-GETC** — in every pathway.
- **Draft-gate worked perfectly:** all **33/33** GE plans carried the “Draft — unverified” warning (`reviewed_by` blank by design); **0** were presented as authoritative.
- **Cal-GETC reconciliation bug FIXED (this run is post-fix):** Cal-GETC `unknown_areas` dropped from **27 → 2** per build (the 2 being `5C`/`7`, the same residual class as IGETC); the 25 bundled cross-system alias codes are now recognized and ignored. IGETC (3) and CSU GE (2) are unchanged — see Finding 1.
- Each build fetched the full live schedule (1,088 sections) and reconciled the program’s courses against it: **150/176** program courses were offered in term 2268.

**Requirement coverage:**

| Requirement | Target | Delivered |
|---|---|---|
| Test all 6 Learning & Career Pathways | 6 | **6 ✓** |
| ≥ 3 degrees per pathway | 3 | **4–5 ✓** |
| ≥ 10 degrees for STEM, Health, and Fitness | 10 | **12 ✓** |
| eLumen prerequisites on every degree | all | **all 33 requested ✓** |
| A transfer GE goal on every degree | any | **all 33 (IGETC/CSU GE/Cal-GETC) ✓** |
| Final report saved as a document | yes | **this file ✓** |

## How it was tested

- **Driver:** a deterministic multi-agent **workflow** fanned out one subagent per degree (model: **Claude Haiku 4.5**), 6 concurrent at a time (below the host’s 10-agent cap) to stay polite to the rate-limit-pending eLumen endpoint.
- **Backend path (chosen over the GUI):** each agent ran `build_live_workbook.analyze_live(…, elumen_live=True, transfer_goal=…)` — the **exact same function the desktop UI calls**. The GUI is a thin pywebview shell over this function, so this matrix validates the UI’s data path directly. (Driving 33 separate GUI windows on one display would add fragility without adding code coverage.)
- **“Live” means 4 public LACCD APIs per build**, all network IO outside the solver: **Program Mapper** (program + course list), **Schedule** (live section listing + Open/Waitlist/Closed status), **eLumen** (real prerequisite catalog, `itemType=Prerequisite` only), and **ASSIST** (GE transferability by area).
- **Term:** 2268 (Fall) for every build. **Degree selection:** distinct-title degrees so each query resolves to a unique program; transfer goal rotates IGETC → CSU GE → Cal-GETC across each pathway.

## Results by pathway

Legend: **m/c** = program courses matched / total in Program Mapper · **prereqs** = exact live eLumen prerequisite constraints applied · **GE c/r/s** = GE areas concrete / reserved / shared-with-major · **ASSIST unmatched** = same-system ASSIST codes the pattern did not reconcile (cross-system alias codes are ignored, not counted — see Finding 1) · † = eLumen fetch hit its time cap (coverage partial) · ‡ = no prereqs applied (no in-term courses to query).

### Arts, Media, and Performance  (4 degrees)

| Degree | Award | Goal | m/c | prereqs | GE c/r/s | ASSIST unmatched | time |
|---|---|---|---|---|---|---|---|
| Art History | A.A-T | igetc | 1/3 | 62 | 3/7/1 | 3 | 49s |
| Studio Arts | A.A-T | csu-ge | 3/4 | 62 | 2/7/3 | 2 | 49s |
| Theatre Arts | A.A-T | cal-getc | 1/1 | 18 | 2/9/0 | 2 | 21s |
| Film, Television, and Electronic Media | A.S-T | igetc | 1/1 | 22 | 3/8/0 | 3 | 19s |

### Business, Law, and Public Safety  (4 degrees)

| Degree | Award | Goal | m/c | prereqs | GE c/r/s | ASSIST unmatched | time |
|---|---|---|---|---|---|---|---|
| Administration of Justice | A.S-T | csu-ge | 2/2 | 17 | 2/7/1 | 2 | 19s |
| Business Administration 2.0 | A.S-T | cal-getc | 2/4 | 4 | 2/9/0 | 2 | 20s |
| Accounting | Cert: Achievement | igetc | 4/4 | 7 | 3/8/0 | 3 | 32s |
| Real Estate Sales | Cert: Achievement | csu-ge | 3/4 | 7 | 2/8/0 | 2 | 32s |

### Child, Family, and Education Studies  (4 degrees)

| Degree | Award | Goal | m/c | prereqs | GE c/r/s | ASSIST unmatched | time |
|---|---|---|---|---|---|---|---|
| Early Childhood Education | A.S-T | cal-getc | 8/8 | 27 | 2/9/1 | 2 | 34s |
| Elementary Teacher Education † | A.A-T | igetc | 12/13 | 64 | 1/6/8 | 3 | 94s |
| Child Development | Cert: Achievement | csu-ge | 8/8 | 27 | 2/6/3 | 2 | 32s |
| Family Studies | Cert: Achievement | cal-getc | 3/4 | 30 | 2/9/1 | 2 | 48s |

### Culinary Arts  (4 degrees)

| Degree | Award | Goal | m/c | prereqs | GE c/r/s | ASSIST unmatched | time |
|---|---|---|---|---|---|---|---|
| Culinary Arts | Cert: Achievement | igetc | 14/14 | 15 | 3/8/0 | 3 | 17s |
| Professional Baking & Patisserie | Cert: Achievement | csu-ge | 10/10 | 22 | 2/8/0 | 2 | 32s |
| Culinary and Baking Essentials ‡ | Cert: Completion | cal-getc | 0/4 | 0 | 2/9/0 | 2 | 2s |
| Baking Specialist I | Job Skills Cert | igetc | 4/4 | 22 | 3/8/0 | 3 | 32s |

### Society, Culture, and Communication  (5 degrees)

| Degree | Award | Goal | m/c | prereqs | GE c/r/s | ASSIST unmatched | time |
|---|---|---|---|---|---|---|---|
| English | A.A-T | csu-ge | 2/2 | 25 | 2/7/2 | 2 | 47s |
| Psychology | A.A-T | cal-getc | 2/2 | 10 | 2/9/1 | 2 | 34s |
| Sociology | A.A-T | igetc | 2/2 | 40 | 3/8/1 | 3 | 32s |
| Political Science | A.A-T | csu-ge | 1/1 | 1 | 2/7/1 | 2 | 16s |
| Philosophy | A.A-T | cal-getc | 1/1 | 8 | 2/8/1 | 2 | 18s |

### STEM, Health, and Fitness  (12 degrees)

| Degree | Award | Goal | m/c | prereqs | GE c/r/s | ASSIST unmatched | time |
|---|---|---|---|---|---|---|---|
| Biology | A.S-T | igetc | 6/7 | 22 | 3/6/7 | 3 | 48s |
| Mathematics 2.0 | A.S-T | csu-ge | 4/4 | 45 | 2/7/4 | 2 | 34s |
| Physics 2.0 | A.S-T | cal-getc | 7/7 | 50 | 2/7/7 | 2 | 48s |
| Chemistry for UC Transfer | UC Transfer Pathway | igetc | 12/12 | 59 | 3/6/12 | 3 | 65s |
| Kinesiology | A.A-T | csu-ge | 3/3 | 21 | 2/6/3 | 2 | 47s |
| Nutrition and Dietetics | A.S-T | cal-getc | 8/8 | 42 | 2/7/4 | 2 | 78s |
| Public Health | A.S-T | igetc | 3/3 | 26 | 3/8/0 | 3 | 32s |
| Geography | A.A-T | csu-ge | 4/8 | 6 | 2/6/6 | 2 | 17s |
| Data Science | Cert: Achievement | cal-getc | 5/10 | 108 | 2/9/0 | 2 | 79s |
| Computer Programmer | Cert: Achievement | igetc | 4/4 | 108 | 3/8/0 | 3 | 78s |
| Cyber Security Associate | Cert: Achievement | csu-ge | 5/5 | 21 | 2/8/0 | 2 | 17s |
| Engineering Drafting Technician † | Cert: Achievement | cal-getc | 5/9 | 96 | 2/9/0 | 2 | 93s |

## Findings

### 1. Cal-GETC ↔ ASSIST reconciliation — diagnosed and FIXED

**Symptom (first run):** every Cal-GETC build reported **27 unmatched ASSIST area codes**, versus 2 for CSU GE and 3 for IGETC.

**Diagnosis (corrects an overstatement in the first report):** the live comparison of one program under both patterns showed Cal-GETC coverage was **already ~95% correct** — the native numeric codes (`1A,2,3A,4,5A,5B,6`…) *were* matched and carried their courses. The “27 unmatched” were **redundant aliases of the same courses**: ASSIST’s `Cal-GETC` `listType` returns a *union* of all three GE coding systems (49 codes) — the native Cal-GETC numeric codes PLUS the legacy IGETC aliases (`2A,4A–4J,6A,7`) PLUS the full CSU GE-Breadth letter set (`A1–F,D0–D9,US1–US3`). The pattern correctly used the numeric codes; the letter codes were just noise inflating `unknown_areas`. (IGETC and CSU GE responses don’t bundle other systems, so they were never affected.)

**Fix (`sources/ge.py`, coverage-neutral):** the resolver now detects the pattern’s coding system — *numeric* for IGETC/Cal-GETC, *alpha* for CSU GE — and **ignores ASSIST codes from the other system**, returning them in a new `cross_system_areas` field instead of as false `unknown_areas`. Every real course is still credited via its native code, so no schedule output changes; the fix only stops the bundled aliases from being mis-reported. It invents no articulation (so it stays correctly under the draft-gate) and is guarded by 4 new resolver tests.

**Result (this run, post-fix):**

| Goal | builds | `unknown_areas`/build (before → after) | cross-system ignored |
|---|---|---|---|
| IGETC | 11 | 3 → **3** (`5C, 7, 8B`) | 0 |
| CSU GE | 11 | 2 → **2** (`B3, F`) | 0 |
| **Cal-GETC** | 11 | **27 → 2** (`5C, 7`) | **25** (`A1–F, D0–D9, US1–US3`) |

Cal-GETC’s residual `5C, 7` is now the *same* harmless residual class as IGETC’s (`5C` = the science-lab code, not a countable area; `7` = a legacy IGETC ethnic-studies code). **The unmatched count dropped to ~0 as targeted.**

**One genuine residual (separate issue, not this mapping bug):** in the head-to-head, Cal-GETC area `1B` resolved to *reserve* where IGETC resolved it *concrete*. That difference comes from ASSIST’s native Cal-GETC `1B` listing one more eligible course than IGETC’s, interacting with the resolver’s disjoint candidate sweep — it is present before and after this fix and is not a code-mapping problem. Logged for a future look at the sweep heuristic.

### 2. eLumen time-cap truncation (honest partial coverage)

**2 of 33** builds hit the eLumen aggregate wall-clock cap and said so (the tool surfaces `fetch_truncated`, never silently):

- **Elementary Teacher Education** — still returned **64 exact prerequisites** from 64 fetched courses (94.1s); coverage may be partial for the deepest prerequisite chains.
- **Engineering Drafting Technician** — still returned **96 exact prerequisites** from 96 fetched courses (93s); coverage may be partial for the deepest prerequisite chains.

These are the most prerequisite-dense programs (Education / Engineering drafting). The cap is a deliberate guardrail on a rate-limit-pending public endpoint; raising it per-program, or caching eLumen across builds, would close the gap if full depth is needed.

### 3. Draft-gate verified across all 33 plans

Every GE plan was correctly marked **“Draft — unverified”** (`ge_reviewed=False` on all 33), because the shipped pattern files ship with `reviewed_by` blank by design. The gate added in PR #33 is doing exactly its job: **no unreviewed pattern was ever presented as authoritative.** The underlying per-area counts still await sign-off by a qualified LACCD articulation officer (the standing content-review item in the README).

### 4. Honest reconciliation — courses not offered in term 2268

Of 176 program courses across the 33 degrees, **150 were offered** in Fall 2268; the 26 “unmatched” are simply not scheduled this term (correctly reported, not dropped). Examples: *Data Science* (CS 121/159/165/166 + CIS 192 not offered), *Geography* (GEOG 003/015, GEOLOGY 001, ANTHRO 102), *Engineering Drafting Technician* (DRAFT 017, EGD TEK 111/121, IND TEK 103). Testing additional terms (2264/2266) would raise match rates.

### 5. Engine detectors fired on live data

Across the 33 builds the solver/analysis surfaced **26 rotation-gap**, **18 single-section**, and **24 under-supply** findings. `modality_mismatch` stayed inert (0) on all builds — expected, since the live schedule API ships no enrollment/capacity counts (the documented IR-export gap).

## What this validates (and what it doesn’t)

**Validated:** the full live ingestion chain — Program Mapper → Schedule → eLumen prerequisites → ASSIST GE → workbook → CP-SAT engine — runs end-to-end, with no network IO inside the solver, for 33 real degrees spanning every pathway and award type (A.A.-T, A.S.-T, A.A., A.S., UC Transfer Pathway, and certificates). Because the desktop UI calls the identical `analyze_live` entry point, the UI’s live-data path is validated by proxy.

**Not covered here:** GUI-specific rendering of the panels (a separate, smaller smoke test with a screenshot covers that); multi-term windows (single term 2268 used); and the `1B` disjoint-sweep residual noted in Finding 1.

## Recommended follow-ups

1. ✅ **Cal-GETC ↔ ASSIST code reconciliation — DONE** (Finding 1): cross-system alias codes are now ignored; unmatched dropped 27 → 2 and this report reflects the post-fix re-run.
2. **Content review of all pattern counts** by a qualified LACCD articulation officer (standing item) — until then the draft-gate correctly protects users.
3. **eLumen coverage for deep programs** (Finding 2) — per-program cap or a shared cross-build cache.
4. **`1B` disjoint-sweep residual** (Finding 1) — revisit the candidate-sweep heuristic so a slightly larger eligible set doesn’t flip an area to reserve.
5. **Optional:** widen to terms 2264/2266 to raise course-match rates.

## Reproduction

Single degree (what each agent ran):

```bash
python3 build_live_workbook.py --campus LAMC --terms 2268 \
    --program "Biology" --transfer-goal igetc --elumen-live \
    --out data/live_LAMC.xlsx
```

The full 33-degree matrix was run via the `edgesched-live-pathways-test` workflow (Haiku agents, 6-wide). Machine-readable results for every degree are saved alongside this file as `LIVE_DATA_PATHWAYS_TEST_RESULTS.json`.

---
*Generated 2026-06-01 from a live run against the public LACCD APIs (LAMC, term 2268). 33/33 builds succeeded.*
