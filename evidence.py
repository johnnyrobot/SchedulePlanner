"""F7 — Evidence-Cited Reporting & Chat Grounding.

A pure, stdlib-only CONSUMER of the already-computed analysis (F1-F4 + the engine's
time-block collisions). It holds the curated claim→source map — ONLY the ✅
well-grounded research findings — and surfaces the citations RELEVANT to whatever
structural flags this build fired, so the exported report and the chat assistant can
explain *why* a flagged condition plausibly matters.

Honesty doctrine (load-bearing):
  * Every claim here is graded ``vetted`` — the HARD-EXCLUDED ❌/⚠️/❓ claims
    (CCD +40%, Austin Peay / Degree Maps +23%, UCF 45%, Civitas 5-7%) are NOT in
    this module, so they are structurally impossible to emit.
  * Every figure rendered is a verbatim ``metric`` / ``statement`` field of a curated
    claim — F7 never computes or fabricates a number.
  * This is SECTOR-WIDE published evidence from OTHER institutions, never a
    measurement or prediction of THIS campus — ``EVIDENCE_LABEL`` rides every surface.

No network, no solver, no pandas, no workbook mutation; reads keys, writes nothing
(the one-line post-pass in build_live_workbook attaches the returned envelope to
results["analysis"]["evidence"] OUTSIDE engine.run — determinism gate stays green).
"""

GRADE = "vetted"  # the only grade representable here

EVIDENCE_LABEL = (
    "Sector-wide research evidence — published findings from OTHER institutions that "
    "explain WHY a flagged structural condition plausibly matters. This is NOT a "
    "measurement of this campus and NOT a prediction of this campus's outcomes."
)

# Curated claim→source map. Source order == display order. ONLY ✅ well-grounded
# claims (COMPLETION_FEATURE_ROADMAP.md lines 26-30); figures verbatim. The
# ``supports`` tuple is the single source of truth for the condition→citation map
# (its inverse is the table in the F7 spec). Equity sub-figures (Kilgore Black
# 9%→25%, BCTC URM 16.2%→27%) are deliberately deferred to F6 and kept OUT here.
CLAIMS = [
    {
        "id": "guided_pathways",
        "statement": (
            "Guided-pathway course maps with structured, data-informed advising raised "
            "graduation rates: Central Arizona College 30%→43%, Community College of "
            "Philadelphia 9%→~18% (doubled), and Bluegrass / BCTC 23.7%→34.8%."),
        "metric": "30%→43%; 9%→~18%; 23.7%→34.8%",
        "source": (
            "Central Arizona College, Community College of Philadelphia, Bluegrass "
            "Community & Technical College; reported via Community College Daily and CCRC"),
        "grade": GRADE,
        "supports": ("buildability_gap", "ge_in_denominator", "no_flags"),
    },
    {
        "id": "standardized_blocks",
        "statement": (
            "Eight-week terms and standardized scheduling blocks improved outcomes: "
            "Odessa College in-class retention +12 percentage points and credentials "
            "awarded +13%; Kilgore College 150% graduation rate 26%→33%."),
        "metric": "Odessa: +12pp retention / +13% credentials; Kilgore: 26%→33%",
        "source": ("Odessa College, Kilgore College; reported via Community College Daily"),
        "grade": GRADE,
        "supports": ("grid_conformance", "morning_compression", "no_flags"),
    },
    {
        "id": "required_course_unavailable",
        "statement": (
            "57% of students report having to spend more time and money because required "
            "courses are not offered at the times they need them."),
        "metric": "57% of students affected",
        "source": (
            "Student-survey finding aggregated in the completion-research review "
            "(ScienceDirect / Community College Daily source set)"),
        "grade": GRADE,
        "supports": ("buildability_gap", "bottleneck", "demand_oversubscribed"),
    },
    {
        "id": "course_shutout",
        "statement": (
            "Being shut out of a required course raises the probability of enrolling in no "
            "classes at all that term by 2.3–2.8 percentage points — a 22–28% relative "
            "increase over a roughly 10% baseline stop-out rate."),
        "metric": "+2.3–2.8pp stop-out (22–28% relative)",
        "source": (
            "Robles et al., 'The effect of course shutouts on community college students,' "
            "ScienceDirect"),
        "grade": GRADE,
        "supports": ("buildability_gap", "bottleneck", "demand_oversubscribed"),
    },
    {
        "id": "conflict_aware_tools",
        "statement": (
            "Scheduling tools that reduce conflicts and increase course availability raised "
            "persistence by an average of 5.41 percentage points (up to 7.39 points for "
            "new students)."),
        "metric": "+5.41pp persistence (+7.39pp new students)",
        "source": (
            "Completion-research review of scheduling-technology studies "
            "(Community College Daily / SAGE persistence study set)"),
        "grade": GRADE,
        "supports": ("time_conflict", "mutual_exclusivity", "buildability_gap"),
    },
]

# Claim ids surfaced in the no-flags default (positive context only — NOT the
# problem-claims). Kept as a constant so the renderer/grounder and the inert
# envelope agree.
_NO_FLAGS_CLAIMS = ("guided_pathways", "standardized_blocks")

_BY_ID = {c["id"]: c for c in CLAIMS}


# --------------------------------------------------------------- condition predicates
# Each predicate reads ONLY an already-computed analysis block and returns a short
# human "trigger" string when the condition fired, else None. They never recompute.
def _buildability_gap(results):
    bld = (results.get("analysis") or {}).get("buildability") or {}
    if bld.get("status") != "active":
        return None
    for p in bld.get("programs", []):
        name = p.get("title") or p.get("code") or "a program"
        if p.get("missing"):
            return f"{name}: {len(p['missing'])} required course(s) not offered"
        if p.get("dead_requirements"):
            return f"{name}: de-catalogued required course(s)"
        if any((g.get("slack") or 0) < 0 for g in (p.get("choice_groups") or [])):
            return f"{name}: an unsatisfiable choice group"
        if p.get("single_section_required"):
            return f"{name}: single-section required course(s)"
        if (p.get("time_conflict") or {}).get("feasible") is False:
            return f"{name}: the required path has a time conflict"
    return None


def _ge_in_denominator(results):
    bld = (results.get("analysis") or {}).get("buildability") or {}
    if bld.get("status") != "active":
        return None
    for p in bld.get("programs", []):
        ge = p.get("ge") or {}
        if ge.get("status") == "active" and ge.get("gaps"):
            name = p.get("title") or p.get("code") or "a program"
            return f"{name}: GE area(s) not schedulable in the denominator"
    return None


def _bottleneck(results):
    bnk = (results.get("analysis") or {}).get("bottlenecks") or {}
    if bnk.get("status") == "active" and bnk.get("leaderboard"):
        return f"{len(bnk['leaderboard'])} cross-program bottleneck course(s) ranked"
    return None


def _demand_oversubscribed(results):
    dsl = (results.get("analysis") or {}).get("demand_supply") or {}
    if dsl.get("status") == "active" and dsl.get("add_list"):
        return f"{len(dsl['add_list'])} over-subscribed course(s) flagged to add a section"
    return None


def _time_conflict(results):
    tbc = (results.get("analysis") or {}).get("time_block_collisions") or []
    if tbc:
        return f"{len(tbc)} required-course time conflict(s)"
    return None


def _mutual_exclusivity(results):
    gp = (results.get("analysis") or {}).get("grid_pressure") or {}
    if gp.get("status") == "active" and gp.get("mutual_exclusions"):
        return (f"{len(gp['mutual_exclusions'])} morning-locked mutually-exclusive "
                "required-course pair(s)")
    return None


def _grid_conformance(results):
    gp = (results.get("analysis") or {}).get("grid_pressure") or {}
    if gp.get("status") != "active":
        return None
    rate = (gp.get("conformance") or {}).get("on_grid_rate")
    if rate is not None and rate < 1.0:
        return "off-grid start times reduce meeting-time standardization"
    return None


def _morning_compression(results):
    gp = (results.get("analysis") or {}).get("grid_pressure") or {}
    if gp.get("status") != "active":
        return None
    if (gp.get("morning_compression") or {}).get("morning_locked_count"):
        return "required courses are locked into the 9 AM-1 PM window"
    return None


# Ordered registry of (condition_key, predicate). Source order = the order fired
# conditions appear in the envelope's ``conditions`` list.
_CONDITIONS = (
    ("buildability_gap", _buildability_gap),
    ("ge_in_denominator", _ge_in_denominator),
    ("bottleneck", _bottleneck),
    ("demand_oversubscribed", _demand_oversubscribed),
    ("time_conflict", _time_conflict),
    ("mutual_exclusivity", _mutual_exclusivity),
    ("grid_conformance", _grid_conformance),
    ("morning_compression", _morning_compression),
)


def _claims_for(condition_key):
    """The curated claims whose ``supports`` lists this condition, in CLAIMS order."""
    return [c for c in CLAIMS if condition_key in c["supports"]]


# ------------------------------------------------------------------------- public
def evidence_appendix(results: dict) -> dict:
    """Map this build's fired structural flags to the curated ✅ research claims.

    PURE / no I/O. Reads only results["analysis"][...] (buildability, bottlenecks,
    demand_supply, grid_pressure, time_block_collisions) and returns the
    relevant-to-flags claims, each carrying its source. Never fabricates a number.

    Returns ``{status, label, conditions, claims[, reason]}`` — ``active`` when at
    least one flag fired (claims relevant to those flags), else ``inert`` with the
    positive guided-pathways context only.
    """
    results = results or {}
    conditions = []
    seen_ids = set()
    union = []  # de-duplicated, in first-seen order

    for key, pred in _CONDITIONS:
        trigger = pred(results)
        if not trigger:
            continue
        claims = _claims_for(key)
        conditions.append({"condition": key, "trigger": trigger, "claims": claims})
        for c in claims:
            if c["id"] not in seen_ids:
                seen_ids.add(c["id"])
                union.append(c)

    if conditions:
        return {
            "status": "active",
            "label": EVIDENCE_LABEL,
            "conditions": conditions,
            "claims": union,
        }

    # No structural flag fired: positive guided-pathways context only — the
    # problem-claims (shutout / 57% / conflict) are deliberately excluded.
    return {
        "status": "inert",
        "label": EVIDENCE_LABEL,
        "conditions": [],
        "claims": [_BY_ID[i] for i in _NO_FLAGS_CLAIMS],
        "reason": "no structural flags fired; showing general guided-pathways context only",
    }
