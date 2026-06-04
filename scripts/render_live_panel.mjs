#!/usr/bin/env node
// render_live_panel.mjs — capture ui.html's renderLivePanel() output for the
// golden guardrail.
//
// renderLivePanel is the live-data "card chain" (html += '<div class="card">…')
// inside ui.html's inline <script>. The rest of the UI test surface never renders
// it, so a whitespace / ordering / separator shift in the upcoming "fold the card
// chain into an append-only registry" refactor would go unnoticed. This script
// snapshots the PRE-refactor innerHTML so a later task can diff against it.
//
// It loads ui.html via file://, calls renderLivePanel(res) in page context with a
// MAXIMAL fixture (every card active), then prints ONLY #live.innerHTML to stdout.
//
// Run / diff:
//   node scripts/render_live_panel.mjs > /tmp/after.html
//   diff tests/fixtures/golden_live_panel.html /tmp/after.html
// Regenerate the committed golden:
//   node scripts/render_live_panel.mjs > tests/fixtures/golden_live_panel.html
//
// Requires Playwright chromium. If missing:  npx playwright install chromium

import { fileURLToPath, pathToFileURL } from 'node:url';
import { dirname, join } from 'node:path';
import { execSync } from 'node:child_process';

const __dirname = dirname(fileURLToPath(import.meta.url));
const UI_HTML = join(__dirname, '..', 'ui.html');

// Playwright may be installed project-local OR globally (this repo has no
// package.json dep on it; prior ui.html renders used the global install).
// Resolve it robustly: try the bare ESM import, then fall back to the global
// node_modules root so the script runs without adding a project dependency.
async function loadChromium() {
  try {
    return (await import('playwright')).chromium;
  } catch (e) {
    if (e?.code !== 'ERR_MODULE_NOT_FOUND') throw e;
    const root = execSync('npm root -g', { encoding: 'utf8' }).trim();
    const mod = await import(pathToFileURL(join(root, 'playwright', 'index.mjs')).href);
    return mod.chromium;
  }
}
const chromium = await loadChromium();

// Maximal fixture: activates the reconciliation card, the buildability card
// (incl. the F4 GE-inclusive geLine), the bottlenecks card, the grid_pressure
// card, the demand_supply card, the inert_detectors card (active + inert mix via
// DET_LABELS), AND the ge_coverage card. Field shapes mirror the engine /
// build_live_workbook report exactly as the Python goldens use them.
const res = {
  campus: 'LAMC',
  live_terms: [2268, 2272],
  program_info: { title: 'Biology AS-T', award: 'AS-T' },
  reconciliation: {
    matched_count: 6,
    unmatched_count: 2,
    unmatched: ['PHYSICS 6', 'GEOG 15'],
  },
  analysis: {
    buildability: {
      status: 'active',
      label: 'Structural-feasibility PROXY, not a measured completion rate.',
      ge_label: 'GE-inclusive buildability — a structural-coverage PROXY ...',
      horizon_terms: [2268, 2272],
      programs: [{
        code: 'BIOL-AS', title: 'Biology <AS-T>', required_total: 4,
        available: 3, missing: ['PHYSICS 6'], dead_requirements: ['GEOG 15'],
        single_section_required: ['BIOLOGY 3'],
        choice_groups: [{ options: ['ANTHRO 101', 'ANTHRO 102'], slack: -1 }],
        season_mismatches: [{ course: 'CHEM 101', recommended_season: 'Fall',
                              offered_seasons: ['Spring'] }],
        seat_pressure: [{ course: 'MATH 261', fill_pct: 98 }],
        time_conflict: {
          feasible: false,
          pairwise_hard: [['BIOLOGY 3', 'CHEM 101']],
          term_clashes: [{ recommended_semester: 2,
                           courses: ['MATH 261', 'CHEM 102'] }],
        },
        by_design_excluded: ['PE 100'],
        score: 47, score_major_only: 55, score_delta: -8,
        summary: '3/4 required courses offered; 1 missing; has time conflicts.',
        ge: { status: 'active', areas_in_denominator: 2, areas_schedulable: 1,
              gaps: ['4'], draft: true },
      }],
    },
    bottlenecks: {
      status: 'active',
      label: 'Cross-program bottleneck ranking — a structural supply-vs-demand '
           + 'PROXY, NOT a measured completion rate.',
      leaderboard: [{
        course: 'MATH <227>', n_programs: 15, n_listed: 18,
        programs: ['Biology AS-T', 'Chemistry AS-T'],
        n_sections: 1, min_sections_per_term: 1, fill_pct: 96,
        closed: false, is_lab: false, risk_score: 19.5,
        reasons: ['required by 15 programs',
                  'single section in at least one offered term', 'at 96% fill'],
      }],
      gaps: [{ course: 'PHYSICS 6', n_programs: 4, programs: ['Biology AS-T'] }],
      unmatched_program_courses: 2,
      truncated: { leaderboard: 3, gaps: 7 },
    },
    grid_pressure: {
      status: 'active',
      label: 'Grid-conformance & morning-compression — a structural time-block '
           + 'PROXY, not a measured completion rate.',
      conformance: { on_grid_rate: 0.9, off_grid_sample: [],
                     off_grid_truncated: 5, evaluated: 10, on_grid: 9,
                     off_grid: 1, skipped: 0 },
      morning_compression: { buckets: { early: 1, prime: 7, afternoon: 1, evening: 1 },
                             total_timed: 10, prime_share: 0.7,
                             morning_locked_count: 2 },
      mutual_exclusions: [{ courses: ['MATH <2>', 'CHEM 1'],
                            reason: 'both 9-1; overlap' }],
      what_if_caveat: 'feasibility is not verified',
      not_assessed: {
        end_time_duration: { status: 'inert', reason: 'no contact category' },
        holidays_session_dates: { status: 'inert', reason: 'no calendar' },
      },
      truncated: { pairs: 4, off_grid: 0 },
    },
    demand_supply: {
      status: 'active', label: 'Demand-vs-supply PROXY label',
      add_list: [{ course: 'MATH 227', action_score: 1.3, demand_ratio: 1.55,
                   wait_total: 22, n_sections: 2,
                   reasons: ['fill 1.00', '22 waitlisted', '<b>x</b>'] }],
      capacity_slack: [{ course: 'ART 101', fill: 0.14, n_sections: 2,
                         note: 'review only — not a cut recommendation' }],
      sections_with_counts: 4, program_weighted: true, not_assessed: 1,
      truncated: { add_list: 3, capacity_slack: 0 },
    },
  },
  inert_detectors: [
    { detector: 'modality_mismatch', status: 'active',
      label: 'Capacity / fill-rate analysis from your enrollment export',
      metric: 'fill-rate computed on 4 of 6 sections',
      matched_sections: 4, total_sections: 6 },
    { detector: 'prerequisite_ordering', status: 'active',
      label: 'Prerequisite ordering from eLumen',
      metric: 'applied on the program path',
      prereq_summary: { with_hard_prereq_count: 3, fallback_count: 1 } },
    { detector: 'time_block_conflict', status: 'inert',
      reason: 'some sections have no posted meeting time',
      remedy: 'supply day/time on every section' },
  ],
  ge_coverage: {
    requested: true, pattern: 'igetc', assist_status: 'ok',
    assist_caveat: 'GE areas are live from ASSIST; confirm with a counselor.',
    draft_warning: 'Draft — unverified: area mapping is a placeholder.',
    areas: [
      { area: '1A', title: 'English Composition', required: 1,
        resolution: 'concrete', flags: [] },
      { area: '2A', title: 'Mathematical Concepts', required: 1,
        resolution: 'shared', flags: [] },
      { area: '4', title: 'Social & Behavioral Sciences', required: 2,
        resolution: 'reserve', flags: ['no_offering', 'unknown_area'] },
    ],
    shared_with_major: [
      { area: '2A', course: 'MATH 261' },
      { area: '5A', course: 'BIOLOGY 3' },
    ],
  },
};

const browser = await chromium.launch();
try {
  const page = await browser.newPage();
  await page.goto(pathToFileURL(UI_HTML).href, { waitUntil: 'load' });
  const innerHTML = await page.evaluate((fixture) => {
    // renderLivePanel and the #live container both live in ui.html's page scope.
    renderLivePanel(fixture);
    return document.getElementById('live').innerHTML;
  }, res);
  // Print ONLY the innerHTML (no trailing newline beyond what the markup carries)
  // so the file is a clean, diffable snapshot.
  process.stdout.write(innerHTML);
} finally {
  await browser.close();
}
