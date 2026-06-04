"""Tests for the cross-program bottleneck analyzer: bottleneck.py.

Pure / deterministic — no network, no solver, no files. Demand maps are built
directly as ProgramDemand objects; sections are raw dicts shaped like the live /
import pipeline's (course / term / class_nbr / days / times / Cap Enrl / Tot Enrl
/ status / facil_id).
"""
import cross_program_bottleneck as B
from sources.program_lists import ProgramDemand


# --------------------------------------------------------------- builders
def demand(required, titles=None, listed=None):
    """ProgramDemand from {course -> [plan,...]} (required ⊆ listed)."""
    d = ProgramDemand()
    d.required = {c: set(ps) for c, ps in required.items()}
    d.listed = {c: set(ps) for c, ps in (listed or required).items()}
    for c, ps in d.required.items():
        d.listed.setdefault(c, set()).update(ps)
    d.titles = dict(titles or {})
    for ps in d.required.values():
        for p in ps:
            d.titles.setdefault(p, p)
    return d


def sec(course, term, class_nbr, *, days="MW", times="9:00 AM - 10:15 AM",
        cap=None, tot=None, status="Open", facil_id=""):
    return {"course": course, "term": term, "class_nbr": class_nbr, "days": days,
            "times": times, "Cap Enrl": cap, "Tot Enrl": tot, "status": status,
            "facil_id": facil_id}


# --------------------------------------------------------------- supply metrics
def test_section_dedup_and_min_per_term():
    secs = [
        sec("CS 101", 2268, "1", days="MW"),
        sec("CS 101", 2268, "1", days="F"),          # same (term,class_nbr) -> deduped
        sec("CS 101", 2268, "2"),                     # 2 sections in term 2268
        sec("CS 101", 2272, "3"),                     # 1 section in term 2272 -> min=1
    ]
    d = demand({"CS 101": ["P1"]})
    board, _ = B.leaderboard(d, secs)
    row = next(r for r in board if r["course"] == "CS 101")
    assert row["n_sections"] == 3                      # the duplicate row dropped
    assert row["min_sections_per_term"] == 1


def test_risk_score_orders_high_demand_low_supply_first():
    secs = [sec("CH DEV 1", 2268, "10")]              # 1 section
    secs += [sec("MATH 227", 2268, "20"), sec("MATH 227", 2268, "21")]  # 2 sections
    d = demand({
        "CH DEV 1": [f"P{i}" for i in range(16)],     # 16 programs / 1 section -> 16.0
        "MATH 227": ["A", "B", "C", "D"],             # 4 programs / 2 sections -> 2.0
    })
    board, _ = B.leaderboard(d, secs)
    assert [r["course"] for r in board] == ["CH DEV 1", "MATH 227"]
    assert board[0]["risk_score"] == 16.0
    assert board[1]["risk_score"] == 2.0


def test_lab_amplifier_raises_score():
    secs = [sec("BIOLOGY 3", 2268, "1", facil_id="MAMP212")]
    d = demand({"BIOLOGY 3": ["P1", "P2"]})           # 2 programs / 1 section -> 2.0
    facility = {"MAMP212": {"type": "LAB", "capacity": 24}}
    base, _ = B.leaderboard(d, secs)
    amp, _ = B.leaderboard(d, secs, facility)
    assert base[0]["risk_score"] == 2.0
    assert base[0]["is_lab"] is False
    assert amp[0]["is_lab"] is True
    assert amp[0]["risk_score"] == 2.6                # 2.0 * 1.3


def test_fill_pressure_amplifier():
    secs = [sec("CHEM 101", 2268, "1", cap=30, tot=29)]   # ~97% fill -> pressure
    d = demand({"CHEM 101": ["P1", "P2"]})                # 2 programs / 1 section
    board, _ = B.leaderboard(d, secs)
    assert board[0]["fill_pct"] == 97
    assert board[0]["risk_score"] == 2.6                  # 2.0 * 1.3


def test_closed_status_amplifier_without_counts():
    secs = [sec("CHEM 101", 2268, "1", status="Waitlist")]
    d = demand({"CHEM 101": ["P1", "P2"]})
    board, _ = B.leaderboard(d, secs)
    assert board[0]["closed"] is True
    assert board[0]["risk_score"] == 2.6


def test_low_fill_no_amplifier():
    secs = [sec("CHEM 101", 2268, "1", cap=30, tot=5)]    # 17% fill -> no pressure
    d = demand({"CHEM 101": ["P1", "P2"]})
    board, _ = B.leaderboard(d, secs)
    assert board[0]["risk_score"] == 2.0


def test_programs_sample_uses_titles():
    secs = [sec("CS 101", 2268, "1")]
    d = demand({"CS 101": ["M1", "M2"]},
               titles={"M1": "Computer Science AS", "M2": "Data Science AS"})
    board, _ = B.leaderboard(d, secs)
    assert board[0]["programs"] == ["Computer Science AS", "Data Science AS"]
    assert board[0]["n_programs"] == 2


def test_reasons_are_human_strings():
    secs = [sec("CS 101", 2268, "1", cap=30, tot=29, facil_id="LAB1")]
    d = demand({"CS 101": ["P1", "P2", "P3"]})
    board, _ = B.leaderboard(d, secs, {"LAB1": {"type": "CMLB"}})
    reasons = " | ".join(board[0]["reasons"])
    assert "required by 3 programs" in reasons
    assert "single section" in reasons
    assert "lab" in reasons.lower()


# --------------------------------------------------------------- gaps
def test_cross_program_gaps_lists_required_not_offered():
    secs = [sec("MATH 227", 2268, "1")]
    d = demand({"MATH 227": ["P1"], "PHYSICS 6": ["P1", "P2", "P3"]})
    gaps, _ = B.cross_program_gaps(d, secs)
    assert [g["course"] for g in gaps] == ["PHYSICS 6"]
    assert gaps[0]["n_programs"] == 3
    # an offered course is NOT a gap
    assert "MATH 227" not in {g["course"] for g in gaps}


# --------------------------------------------------------------- report envelope
def test_report_active_envelope():
    secs = [sec("MATH 227", 2268, "1"), sec("GONE 1", 9999, "x")]  # GONE not required
    d = demand({"MATH 227": ["P1", "P2"], "PHYSICS 6": ["P1"]})    # PHYSICS not offered
    rep = B.bottleneck_report(d, secs)
    assert rep["status"] == "active"
    assert "PROXY" in rep["label"]
    assert any(r["course"] == "MATH 227" for r in rep["leaderboard"])
    assert any(g["course"] == "PHYSICS 6" for g in rep["gaps"])
    # PHYSICS 6 is required but unmatched to an offered section
    assert rep["unmatched_program_courses"] == 1
    assert "leaderboard" in rep["truncated"] and "gaps" in rep["truncated"]


def test_report_inert_no_demand():
    rep = B.bottleneck_report(None, [sec("CS 101", 2268, "1")])
    assert rep["status"] == "inert"
    assert "PROXY" in rep["label"] and rep.get("reason")


def test_report_inert_empty_demand():
    rep = B.bottleneck_report(ProgramDemand(), [sec("CS 101", 2268, "1")])
    assert rep["status"] == "inert"


def test_report_inert_no_sections():
    rep = B.bottleneck_report(demand({"CS 101": ["P1"]}), [])
    assert rep["status"] == "inert"
    assert "section" in rep["reason"].lower()


def test_report_inert_zero_overlap():
    # demand and schedule share no courses -> the join is empty -> inert (honest:
    # we cannot tell a campus/term mismatch from genuine non-offering).
    rep = B.bottleneck_report(demand({"ZZZ 1": ["P1"]}), [sec("ABC 1", 2268, "1")])
    assert rep["status"] == "inert"
    assert "join" in rep["reason"].lower() or "match" in rep["reason"].lower()


def test_report_truncates_and_counts():
    secs = [sec(f"C {i}", 2268, str(i)) for i in range(25)]
    d = demand({f"C {i}": ["P1"] for i in range(25)})
    rep = B.bottleneck_report(d, secs, top=20)
    assert len(rep["leaderboard"]) == 20
    assert rep["truncated"]["leaderboard"] == 5


# --------------------------------------------------------------- FF1 subject-alias crosswalk
def test_subject_alias_collapses_engl_english_to_one_match():
    """A demand course written ``ENGL 101`` joins an offered ``ENGLISH 101`` via
    the verified subject crosswalk: it ranks (not a gap) and is NOT counted as an
    unmatched program course. This is the SOURCE-side encoding mess F2's smoke
    surfaced (schedule-import emits ENGL; the live schedule emits ENGLISH)."""
    secs = [sec("ENGLISH 101", 2268, "1")]            # offered under the long form
    d = demand({"ENGL 101": ["P1", "P2"]})            # required under the short form
    rep = B.bottleneck_report(d, secs)
    assert rep["status"] == "active"
    # the aliased course is matched -> on the board, NOT in gaps, NOT unmatched
    assert any(r["course"] == "ENGLISH 101" for r in rep["leaderboard"])
    assert all(g["course"] != "ENGL 101" for g in rep["gaps"])
    assert rep["unmatched_program_courses"] == 0


def test_subject_alias_rescues_reverse_direction():
    """The SYMMETRIC case: the program list uses the canonical spelling
    ``ENGLISH 101`` while the schedule offers it under the aliased short form
    ``ENGL 101``. The match must be rescued via the alias INDEX (the
    ``alias_idx.get(course)`` fallback the forward ENGL->ENGLISH test never hits),
    so the course ranks, is not a gap, and is not counted unmatched."""
    secs = [sec("ENGL 101", 2268, "1")]               # offered under the short form
    d = demand({"ENGLISH 101": ["P1", "P2"]})         # required under the long form
    rep = B.bottleneck_report(d, secs)
    assert rep["status"] == "active"
    # rescued via the alias index -> on the board, NOT a gap, NOT unmatched
    assert any(r["course"] == "ENGLISH 101" for r in rep["leaderboard"])
    assert all(g["course"] != "ENGLISH 101" for g in rep["gaps"])
    assert rep["unmatched_program_courses"] == 0


def test_subject_alias_does_not_force_match_ambiguous_eng():
    """GUARD: ``ENG`` is the classic ambiguous code (English vs Engineering) and is
    NOT in the verified crosswalk, so an ``ENG 101`` demand must stay honestly
    unmatched against an ``ENGLISH 101`` offering — a false match here would be
    worse than an honest miss."""
    secs = [sec("ENGLISH 101", 2268, "1"), sec("MATH 227", 2268, "2")]
    d = demand({"ENG 101": ["P1", "P2"], "MATH 227": ["P1"]})
    rep = B.bottleneck_report(d, secs)
    assert rep["status"] == "active"
    # MATH 227 matches; ENG 101 does NOT collapse onto ENGLISH 101
    assert any(r["course"] == "MATH 227" for r in rep["leaderboard"])
    assert all(r["course"] != "ENGLISH 101" for r in rep["leaderboard"])
    assert any(g["course"] == "ENG 101" for g in rep["gaps"])
    assert rep["unmatched_program_courses"] == 1


def test_subject_alias_leaves_non_aliased_unmatched_count_unchanged():
    """A non-aliased required-but-not-offered course is still counted unmatched
    exactly as before (the crosswalk is additive — it only rescues real aliases,
    never changes matched/unmatched semantics for other courses)."""
    secs = [sec("MATH 227", 2268, "1")]
    d = demand({"MATH 227": ["P1"], "PHYSICS 6": ["P1", "P2"]})   # PHYSICS 6 not offered
    rep = B.bottleneck_report(d, secs)
    assert rep["unmatched_program_courses"] == 1
    assert any(g["course"] == "PHYSICS 6" for g in rep["gaps"])


# --------------------------------------------------------------- determinism
def test_offered_param_matches_recompute():
    """Passing a pre-computed offered_by_course map (the bottleneck_report
    optimization) is behavior-identical to recomputing it internally."""
    import buildability
    secs = [sec("CS 101", 2268, "1"), sec("MATH 227", 2268, "2"),
            sec("MATH 227", 2268, "3")]
    d = demand({"CS 101": ["A"], "MATH 227": ["A", "B"], "PHYS 1": ["A"]})
    offered = buildability.offered_by_course(secs)
    assert B.leaderboard(d, secs, offered=offered) == B.leaderboard(d, secs)
    assert B.cross_program_gaps(d, secs, offered=offered) == B.cross_program_gaps(d, secs)


def test_leaderboard_is_deterministic():
    secs = [sec("CS 101", 2268, "1"), sec("MATH 227", 2268, "2"),
            sec("CHEM 101", 2268, "3")]
    d = demand({"CS 101": ["A", "B"], "MATH 227": ["A", "B"], "CHEM 101": ["A"]})
    a, _ = B.leaderboard(d, secs)
    b, _ = B.leaderboard(d, secs)
    assert a == b
    # ties (CS 101 vs MATH 227 both 2 programs/1 section) broken by course asc
    courses = [r["course"] for r in a if r["risk_score"] == 2.0]
    assert courses == sorted(courses)
