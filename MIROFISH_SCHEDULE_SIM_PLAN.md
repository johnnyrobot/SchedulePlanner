# Using MiroFish-Offline to simulate schedule configurations for optimal outcomes (AGPL-safe)

> **Status:** research/strategy plan. **No code is written or changed by this document.** It records
> how to run an internal, offline experiment using MiroFish-Offline to explore whether agent-based
> simulation of schedule configurations can surface better outcomes for edgesched.
>
> **Not legal advice.** The AGPL analysis below is an engineering read of the license. The chosen path
> (internal-only spike) is low-risk; consult counsel before *distributing* anything (see the
> "ship later" section).

---

## Context — what was asked and what was found

Goal: explore **MiroFish-Offline** (source reviewed from `~/Desktop/nikmcfly-mirofish-offline.txt`, a
gitingest dump) to see whether *running schedule configurations through an agent-based simulation* can
surface the "most optimal outcomes" for edgesched — the one stated worry being **AGPL licensing**.

Decisions driving this plan: **(1) end goal = internal research spike only** (run it offline on this
Mac to learn; nothing ships); **(2) outcome metric = a proposed blend** (below).

**What MiroFish actually is (read directly from the dump):**
- An **AGPL-3.0** multi-agent **social-opinion** simulator. You feed it a document (press release,
  policy draft); it spawns hundreds of LLM agents with personas who *post/argue/shift opinion* on
  simulated Twitter/Reddit, then a ReportAgent summarizes the reaction. (`README.md`; `LICENSE` =
  GNU AGPL v3; `package.json` and `backend/pyproject.toml` both declare `AGPL-3.0`.)
- Stack: Flask + Vue, **Neo4j CE** (graph memory) + **Ollama** (local LLM, e.g. `qwen2.5:32b`) +
  **OASIS** (`camel-oasis==0.2.5`, `camel-ai==0.2.78`) as the actual agent-simulation engine.
- **The simulation runs as a separate subprocess**, config-driven by JSON. MiroFish writes a
  `simulation_config.json` (the `SimulationParameters` dataclass in
  `backend/app/services/simulation_config_generator.py`) + agent-profile files, then spawns
  `python scripts/run_*_simulation.py --config <json>` (`simulation_manager.py`). The run scripts call
  `oasis.make(...)` (`backend/scripts/run_twitter_simulation.py`,
  `backend/scripts/run_reddit_simulation.py`). Live control of a running sim uses **filesystem JSON
  IPC** (`simulation_ipc.py` — commands/responses dirs). **An arm's-length process boundary already
  exists in the design.**

**Honest fit assessment (this matters):** MiroFish is built for *social-media opinion dynamics*, **not
scheduling or enrollment**. Its platform mechanics (feeds, viral thresholds, echo chambers), its Neo4j
graph-memory layer, and its ReportAgent are **irrelevant** to schedules. The **only reusable kernel**
is the *agent-persona + LLM-decision substrate* — and that kernel is **OASIS** (`camel-ai`), which
MiroFish merely orchestrates. So "use MiroFish for schedules" really means "borrow the agent-based-
modeling idea, run it on OASIS, and treat MiroFish as a worked example you read for ideas."

**Where this would attach in edgesched (for orientation, not for now):**
- edgesched already **optimizes a proxy objective**: `engine.py:367` minimizes
  `100*last + 1*sum(rec_misses)` (makespan + recommended-semester misses) via CP-SAT, single-shot,
  fixed seed (`engine.py:374`). An ABM's contribution would be a *richer outcome signal* than that
  hand-coded proxy — ranking candidate schedules by *simulated student behavior* instead of
  term-count alone.
- Non-deterministic/raw signals already attach to `results["analysis"]` **outside `engine.run`**
  (`build_live_workbook.py:1067-1075`, the shipped time-block + room detectors). That is exactly where
  an advisory ABM score would belong — never inside the deterministic solve.
- Precedents for an **arm's-length external process** already exist: `sources/pdf_loader.py` spawns
  `["java", …]` (OpenDataLoader, Apache-2.0; JRE bundled in the .app), and `chat_assist.py` runs
  local **Gemma via Ollama** — the *same Ollama* MiroFish uses, already on this Mac.

---

## Part 1 — AGPL licensing (the #1 concern), resolved

**The decisive fact: the chosen path is internal-only use. AGPL obligations attach on *conveyance*
(distribution) or *§13 remote-network interaction by third parties* — neither of which an internal
spike does.** GPL/AGPL §2 lets you run and privately modify a covered work with no conditions as long
as you do not convey it; AGPL's only extra clause, **§13**, triggers only when *remote users interact
with your modified copy over a network*. Running MiroFish locally on this Mac, single-user, for
research:

- **No distribution** → the copyleft "license the whole as AGPL" requirement never fires.
- **No third-party network users** → §13 never fires.
- **You may even modify it freely** for the experiment and never publish a line.

➡️ **Conclusion: as a purely internal/offline research spike, MiroFish's AGPL imposes *no* obligation
on edgesched. There is nothing to mitigate.** The risk is effectively zero **as long as two simple
hygiene rules hold** (below).

### Two hygiene rules that keep it zero-risk
1. **Physical separation.** Clone/keep MiroFish **outside** the edgesched repo (e.g.
   `~/code/mirofish-offline/`, never under `~/code/edgesched/`). Never copy MiroFish source, prompts,
   or text into edgesched. (edgesched ships with **no LICENSE file** → it is effectively proprietary,
   "all rights reserved"; do not contaminate it.) This also matches the existing commit-hygiene rule
   to never stage stray files.
2. **Don't expose it.** Don't put your (possibly modified) MiroFish instance behind a network endpoint
   that other people use — that is the one thing §13 cares about. Localhost-only, single user, is fine.

### If this ever graduates to a *shippable* feature (out of scope now — documented so future-you is safe)
Ranked safest → riskiest. **Do not ship anything on this list without legal review.**
- **(A) Clean-room rebuild on OASIS (Apache-2.0).** Copyright protects *expression (code)*, not
  *ideas/methods/architecture*. Reimplement the schedule-outcome ABM **directly on OASIS**, taking
  only the *concept* from MiroFish — copy **no** MiroFish code/prompts. Result: **no AGPL at all**;
  edgesched stays proprietary. **Gating prerequisite: verify OASIS's own license is permissive**
  (expected Apache-2.0 for the CAMEL-AI ecosystem, but **must be confirmed** — see Phase 0). If OASIS
  is itself GPL/AGPL, this path collapses.
- **(B) Strict arm's-length separate process.** edgesched ↔ a *separately distributed* AGPL MiroFish
  tool, talking only via files/CLI/JSON (the boundary MiroFish already uses for OASIS). Keep it in its
  own repo, **never PyInstaller-bundle it into the .app**, ship its source under AGPL with a source
  offer, and never expose it as a network service. The process/IPC boundary is the generally-accepted
  "separate work" line, but it is a legal gray area — counsel required.
- **(C) Bundle MiroFish into the app.** ❌ Don't. Combining AGPL code into your signed/notarized
  proprietary .app is the scenario AGPL is designed to catch.

### A determinism note that constrains *any* future integration
edgesched's core value is **byte-identical determinism** (CP-SAT, single worker, fixed seed). An
LLM-driven ABM (MiroFish runs at `temperature 0.7` in `simulation_config_generator.py`) is
**inherently non-deterministic and slow**. So even in path A/B, an ABM score must live **outside
`engine.run`**, in `results["analysis"]`, clearly labeled as a non-deterministic *advisory estimate*
(honesty doctrine), exactly like the existing detectors. It can never enter the deterministic solve.

---

## Part 2 — The method: agent-based simulation of schedule configurations

### Conceptual mapping (MiroFish social-sim → schedule-outcome sim)
| MiroFish concept | Repurposed for schedules |
|---|---|
| Input document (press release) | **A candidate schedule configuration** — sections offered (course, days/times, room, capacity, modality), per term |
| Graph entities (people/orgs) | Student archetypes + course catalog + program requirements |
| Agent personas (Twitter/Reddit users) | **Student agents** — archetype (working part-time, full-time, parent, morning-only, evening-only, online-only), declared program, courses still needed, prereq state, unit appetite, day/time/modality preferences, risk tolerance |
| Initial posts / hot topics / narrative | The **registration event**: "here is the schedule; build your term" |
| Activity loop (post/comment/react) | **Enrollment-choice loop**: each agent picks sections given availability + conflicts + seat caps (contention), or drops/defers a course |
| Sentiment evolution | **Outcome telemetry**: fill rate, unmet demand, conflict-forced drops, realized terms-to-completion, satisfaction proxy |
| ReportAgent summary | **Config scorecard** → a comparable score per candidate |

### The "optimization" loop (simulation-in-the-loop)
1. **Generate candidates.** Perturb edgesched inputs to produce N candidate configs — vary section
   time slots, rotation (which terms offer which courses), modality mix, seat caps, add/remove
   sections. (edgesched already holds the section records + the solver; an *outer* script varies
   inputs — no change to `engine.run`.)
2. **Simulate each candidate.** Write the config as JSON; the ABM (separate process) loads a **fixed
   cohort** of student agents and simulates their enrollment choices against that schedule. Handle
   seat contention with a fixed arrival order (deterministic) or repeated stochastic draws
   (report mean ± spread).
3. **Score (the proposed blend).**
   - **Primary — Enrollment & completion:** predicted fill, realized time-to-completion under real
     availability, conflict-driven drops. (Most decision-relevant vs. edgesched's makespan proxy.)
   - **Secondary — Operational feasibility:** room/capacity/time-conflict stress + scarce-lab
     contention — **reuse the already-shipped room-conflict detector** signals
     (`build_live_workbook.py:1071-1075`) rather than re-deriving them in the ABM.
   - **Secondary — Equity:** variance of outcomes across archetypes (does a config help full-timers
     while stranding working students?).
4. **Rank & compare.** Pick the best-simulated config(s); **compare against edgesched's current
   proxy-objective winner** — the real research question is *whether the ABM disagrees with makespan*,
   and if so, why.

### Honesty caveats (must travel with any result — matches edgesched doctrine)
- LLM-agent ABM is **non-deterministic, slow, and unvalidated**. Outputs are **hypotheses / sensitivity
  analysis**, not predictions. Use them to surface *which schedule levers matter*, not as a
  ground-truth ranking.
- **Validation needs real enrollment data** — the standing IR-export blocker (#17). Without it, the
  ABM's "optimal" is unfalsifiable. So the spike's honest goal is *"is this promising, and what would
  validating it require?"* — not *"produce the optimal schedule."*
- **Try the cheap baseline first.** A **rule-based / discrete-choice model** (e.g. multinomial-logit
  over student preferences) may deliver ~80% of the insight **deterministically, fast, with zero AGPL
  code**. Only reach for an LLM-agent ABM (MiroFish/OASIS) if the rule-based model is too crude to
  capture the behavior you care about.

---

## Part 3 — The spike, step by step (internal, AGPL-safe)

All work happens **outside** the edgesched repo. Only a final, original-words findings writeup comes
back into edgesched.

- **Phase 0 — Setup & license verification (gating).**
  - Clone MiroFish to `~/code/mirofish-offline/` (not under `~/code/edgesched/`).
  - Re-confirm MiroFish = AGPL-3.0 (done) and **verify OASIS's license**: `pip show camel-oasis
    camel-ai` and check the `camel-ai/oasis` GitHub LICENSE. Record it — this gates the future
    clean-room path (Part 1-A).
  - Confirm it runs fully offline on **Ollama** (already on this Mac from the Gemma/Tongyi work);
    Neo4j via the README's Docker one-liner. No cloud keys.
- **Phase 1 — Smoke the stock pipeline.** Run MiroFish as-is on a sample document to learn the agent
  loop, the `SimulationParameters` schema, the profile formats (Reddit JSON / Twitter CSV), and the
  output shape first-hand. Pure learning, no edits.
- **Phase 2 — Minimal repurpose experiment.** Hand-craft **one** candidate schedule + **10–30**
  student-archetype agent profiles in MiroFish's profile format; replace the social-platform stimulus
  with a "here is the schedule — build your term" prompt; run it; inspect whether agents make
  *sensible enrollment choices* (respect prereqs, avoid time conflicts, prefer their stated
  availability). This is the cheap feasibility test.
- **Phase 3 — Decide & write up.** In your own words (no MiroFish code), capture: did the agent
  substrate produce believable enrollment behavior? Is a proper schedule-outcome ABM worth building?
  Recommend the next step — **(a)** clean-room ABM on OASIS, **(b)** the cheaper rule-based
  discrete-choice model, or **(c)** drop it. Save that writeup into edgesched as a follow-on doc.

### If/when it graduates (sketch only — not part of this spike)
edgesched writes candidate configs → a **separate** ABM process (clean-room on OASIS per Part 1-A)
reads them, runs the sim, writes outcome JSON → edgesched reads outcomes and attaches them to
`results["analysis"]["sim_outcomes"]` (outside `engine.run`, advisory + non-deterministic, honestly
labeled), rendered in `ui.html` like the other detectors. Mirror the existing `pdf_loader.py` Java
subprocess and `chat_assist.py` Ollama precedents. Re-read Part 1's guardrails before shipping.

---

## Out of scope (explicit)
- Any code change to edgesched (this is a doc only).
- Shipping/bundling MiroFish or an ABM in the distributed app (deferred; guardrails documented).
- Validation against real enrollment data (blocked on the #17 IR export).
- MiroFish's Neo4j graph-memory layer, ReportAgent, and social-platform mechanics (not relevant to
  schedules).

## Verification (that the spike "worked")
- **Phase 0:** OASIS license recorded; MiroFish boots offline on Ollama+Neo4j; **nothing from MiroFish
  copied into `~/code/edgesched/`** (`git status` in edgesched stays clean of MiroFish files).
- **Phase 2:** at least one run where student agents produce enrollment choices that respect prereqs,
  avoid time conflicts, and honor stated availability — i.e. the substrate is behaviorally plausible.
- **Phase 3:** a written go/no-go recommendation with the cheaper rule-based baseline explicitly
  considered, saved into edgesched as a follow-on doc.
