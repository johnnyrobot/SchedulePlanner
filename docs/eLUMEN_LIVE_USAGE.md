# Live eLumen prerequisite fetch (`--elumen-live`) — usage & approval gate

EdgeSched can enrich a live workbook with **course prerequisite ordering** pulled
from the eLumen Public Portal catalog API. This document defines how that path is
bounded, how it behaves under load/failure, its privacy posture, and — most
importantly — the **approval gate** that must clear before any production reliance.

> **Release policy (read this first).** Until **written institutional and/or
> eLumen-vendor sign-off** exists (Terms-of-Use review + an agreed rate-limit /
> acceptable-use posture), `--elumen-live` ships as **opt-in testing / admin
> functionality only**. EdgeSched does **not** claim production reliance on live
> eLumen crawling, and no product surface should imply it. Treat live results as
> best-effort until the gate below is signed off.

## What it is

- **Endpoint:** `GET https://portalapi-laccd.elumenapp.com/public/courses`
  (`status=approved`, `tenant=<campus>.elumenapp.com`, `query=<subject>`,
  `pageSize<=25`, `page`). Public and unauthenticated.
- **Client:** `sources/elumen_client.py`. **Wiring:** `build_live_workbook.py`
  `--elumen-live` (and `analyze_live(..., elumen_live=True)`).
- **What it extracts:** only leaf requisites whose `itemType == "Prerequisite"`
  become hard ordering constraints. **Corequisites and advisories are excluded.**
  The conversion is under-approximate (it never produces a false-infeasible plan).

## Guardrails (how it stays a polite, bounded client)

### Opt-in — never on by default
- `--elumen-live` is a `store_true` flag; the default is **off**.
  `analyze_live`'s `elumen_live` parameter defaults to `False`.
- Nothing in the test suite, CI, or app startup enables it. The one networked
  test is `@pytest.mark.live` and is **deselected by default** (`pytest.ini`
  `addopts = -m "not live"`; the QA gate `scripts/run_qa.sh` asserts the live set
  stays deselected). CI runs the default (offline) selection only.

### Bounded fetch — selected campus/program/subjects only
- A live build queries the **one selected campus** and **only the subjects the
  chosen program's fetched sections actually cover** (derived from the section
  list, sorted + de-duplicated). There is **no all-subjects sweep and no
  background crawl**.
- Hard caps: `pageSize` is clamped to `MAX_PAGE_SIZE = 25`; pages per subject are
  bounded by `DEFAULT_MAX_PAGES = 20`; paging stops as soon as a short page
  (`< pageSize`) or an empty/absent `_embedded.courses` is seen.

### Throttle (request spacing)
- A single shared rate limiter spaces **successive requests ≥
  `REQUEST_DELAY_SECONDS = 1.0` s apart**, across *all* subjects and pages in a
  build (not reset per subject). The first request of a batch is not delayed.
- Override per call via `request_delay=`; `request_delay=0` disables throttling
  (intended only for an injected client that manages its own pacing, e.g. tests).

### Caching (no redundant load)
- An optional per-session `cache` dict memoizes results by
  `(tenant, query, pageSize)`. A repeated subject (e.g. cross-listed) is fetched
  **once**; a cache hit performs no request and no throttle wait. The live build
  uses a fresh per-build cache. The cache is in-memory only (no disk
  persistence), so it never writes catalog data next to the app or bundle.

### Backoff / clean failure on 429 / 5xx / timeout
- **Transient** failures — HTTP **429** and **5xx** (`RETRYABLE_STATUS =
  {429, 500, 502, 503, 504}`) plus transport **timeouts** — are retried with
  bounded exponential backoff: up to `MAX_RETRIES = 3` retries, sleeping
  `min(BACKOFF_BASE_SECONDS * 2**attempt, BACKOFF_MAX_SECONDS)` =
  `min(1.0 * 2**n, 30.0)` seconds (1 s, 2 s, 4 s, …).
- **Non-transient** failures fail **immediately, no retry**: any other 4xx (bad
  campus/query) and any JSON/response-shape drift (`SourceDataError`). Retrying
  cannot fix bad data.
- After retries are exhausted, the **last error propagates as a clean
  `SourceError` subclass** (`SourceHTTPError` / `SourceDataError` / `SourceError`)
  naming the source and URL — never a raw `httpx` traceback.

### Defaults at a glance

| Knob | Constant | Default |
|---|---|---|
| Request spacing (throttle) | `REQUEST_DELAY_SECONDS` | `1.0` s |
| Max retries (transient only) | `MAX_RETRIES` | `3` |
| Backoff base / cap | `BACKOFF_BASE_SECONDS` / `BACKOFF_MAX_SECONDS` | `1.0` s / `30.0` s |
| Retryable statuses | `RETRYABLE_STATUS` | `429, 500, 502, 503, 504` |
| Page size cap | `MAX_PAGE_SIZE` | `25` |
| Pages per subject cap | `DEFAULT_MAX_PAGES` | `20` |
| Session cache | `cache=` (opt-in dict) | per-build dict in the live path |

## Privacy posture

- **Catalog data only.** The client reads **course / prerequisite catalog
  metadata** (course code, title, the requisites tree). It requests
  `status=approved` published catalog records.
- **No student data. No instructor PII.** The eLumen `/public/courses` path
  carries no enrollment, roster, grade, or student records, and EdgeSched does
  not request, parse, store, or emit any instructor personal data from it. The
  committed test fixture is sanitized real-shape catalog data with no PII.
- **No persistence side effects.** The cache is in-memory and per-build; the only
  artifact written is the analysis workbook the operator asked for.

## Engine isolation (unchanged invariant)

All eLumen network IO happens during the **workbook build** in `analyze_live`
(it opens/owns its own `httpx.Client`, or uses an injected one) — **never inside
`engine.run()`**. The OR-Tools engine remains offline and deterministic, reading
a finished workbook only. `engine.py` is unmodified by this feature.

## Usage

```bash
# Opt-in live prereq enrichment for one campus + program (network required):
python3 build_live_workbook.py --campus LAMC --program "Biology" \
    --terms 2264,2266,2268 --elumen-live --out data/live_LAMC.xlsx
```

The run attaches a **coverage report** to `report["elumen_coverage"]`
(`courses_fetched`, `courses_with_prereqs`, `unmatched_prereq_targets`,
`requested_courses_without_record`). The eLumen↔schedule/Program-Mapper course-id
join is validated **only** via this report — inspect it before trusting results.
If both `--elumen-fixture` and `--elumen-live` are supplied, **live wins** and a
warning is recorded in `report["warnings"]`.

## Approval gate (must clear before production reliance)

Before EdgeSched relies on live eLumen data in production, obtain and record:

1. **Terms-of-Use review** — confirmation that programmatic catalog access is
   permitted for this use, in writing, from the institution and/or eLumen.
2. **Rate-limit / acceptable-use agreement** — an agreed request budget; tune
   `REQUEST_DELAY_SECONDS` / `MAX_RETRIES` / `DEFAULT_MAX_PAGES` to match it.
3. **Data-handling sign-off** — confirmation the catalog-only, no-PII posture
   above meets institutional data-governance requirements.

Until all three are signed off, keep `--elumen-live` as **opt-in admin/testing**
only and do not represent live eLumen prerequisite data as production-ready.
