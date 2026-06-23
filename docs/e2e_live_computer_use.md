# Live LACCD Computer Use Acceptance Runbook

This is a non-gating desktop acceptance run for the **Build from live LACCD data**
feature. It uses public LACCD, eLumen, and ASSIST endpoints, so failures should
be triaged as possible upstream API/catalog drift before treating them as product
regressions.

## Scope

- Do test: live schedule fetch, Program Mapper pathway, eLumen prerequisites,
  transfer GE, UI rendering, and exported HTML report language.
- Do not test: enrollment exports. Leave **Add enrollment export** empty.
- Recommended rows:
  - `LAMC` · `2264,2266,2268` · `Biology` · `IGETC`
  - `LACC` · `2264,2266,2268` · `Psychology` · `Cal-GETC`
  - Optional alternates: `ELAC` · `2268` · `Business Administration` · `CSU GE`;
    `LATTC` · `2266,2268` · `Administration of Justice` · `IGETC`

## Run

1. From the repo root, launch the desktop app:

   ```bash
   python3 app.py
   ```

2. For each selected row, use Computer Use to fill the live form:
   - Campus: the row's campus code.
   - Terms: the row's term list.
   - Program: the row's program name.
   - Enable **Include prerequisites from eLumen**.
   - GE goal: the row's GE pattern.
   - Leave enrollment export blank.
   - Click **Build from live LACCD data**.

3. Capture evidence that the build completed:
   - Results card(s) render with at least one program plan.
   - **Live data reconciliation** is visible.
   - **General Education** is visible and shows the draft/unverified warning
     when the bundled pattern has not been reviewed.
   - **What this live build can measure** shows prerequisite status and says
     capacity / fill-rate needs enrollment/capacity counts.
   - **Supply diagnostics** includes plain-English guide text for rotation gaps,
     single-section risk, capacity / fill-rate, under-supply, time conflicts,
     and off-grid sections.
   - Under-supply, when present, is worded as waitlist pressure / planning signal
     and not as proof of student completion impact.
   - Off-grid sections, when present, are scoped to the selected program; broad
     campus grid pressure remains in the Grid conformance panel.

4. Click **Export HTML report**, save the report, and open it.

5. Capture evidence from the exported report:
   - The same Supply diagnostics guide text appears offline.
   - GE draft/unverified warning appears.
   - Capacity / fill-rate says live-only builds without enrollment counts are
     not yet measurable, not all clear.
   - No student-level data, enrollment export, or completion-causation claim is
     implied.

## Pass Criteria

- At least two matrix rows complete without a UI freeze or unhandled traceback.
- Each completed row renders a plan, live reconciliation, prerequisite status,
  GE coverage, and chair/dean-readable Supply diagnostics.
- Exported HTML preserves the same explanations and caveats.

## Failure Notes

- If the app returns **No program matched**, verify the current Program Mapper
  title for that campus before filing a product bug.
- If GE coverage fails, check ASSIST availability and API shape.
- If prerequisites are partial/inert, record the eLumen message; the endpoint is
  public and best-effort.
- If capacity / fill-rate is empty with no enrollment export, that is expected:
  live schedule APIs do not publish Cap/Tot counts.
