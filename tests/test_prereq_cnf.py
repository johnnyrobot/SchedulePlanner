"""Tests for the exact DNF->CNF prereq converter (sources/prereq_cnf.py).

The converter is PURE (no pandas/mapping import — _norm is inlined). It mirrors
the engine's CNF grammar so every emitted catalog string round-trips through the
REAL engine.parse_prereq parser. Tests exercise: top-level sentinels, malformed
payloads, exact distribution + dedup + clause subsumption, the byte-stable
sorted-literal LIST clause sort, single-clause parenthesization, normalization
merges, self-reference drop (gated_course normalized first), the
delimiter/paren-literal guard, and the configurable clause-budget guard with its
FLAGGED conservative single-OR-union under-approximation fallback — including the
load-bearing end-to-end UNIT-CAP feasibility property against engine.solve_cohort.
"""
import pytest

import engine
from sources.http import SourceDataError
from sources.prereq_cnf import (
    dnf_to_cnf,
    to_catalog_string,
    DEFAULT_MAX_CLAUSES,
    BRANCH_CAP,
)


def _groups(s):  # parse with the REAL engine parser
    return engine.parse_prereq(s)


# --------------------------------------------------- top-level sentinels
def test_blank_empty_none_nan_are_no_constraint():
    # [] (top-level empty disjunction), None, NaN, [[]] (one empty AND-branch),
    # and a DNF with one empty AND-branch alongside a real one all mean
    # "no prereq" -> '' exact=True (the cases the engine treats as no-constraint).
    for dnf in ([], None, float("nan"), "", [[]], [[], ["A"]]):
        r = dnf_to_cnf(dnf)
        assert r.cnf_string == "", dnf
        assert r.exact is True, dnf
        assert _groups(r.cnf_string) == []


def test_malformed_non_empty_payload_raises():
    with pytest.raises(SourceDataError):
        dnf_to_cnf("MATH 245")          # bare non-empty string, not list-of-lists
    with pytest.raises(SourceDataError):
        dnf_to_cnf([["A"], "B"])        # mixed/wrong nesting (a member is not a list)


# --------------------------------------------------- serialization / round-trip
def test_single_course_is_parenthesized():
    r = dnf_to_cnf([["A"]])
    assert r.cnf_string == "(A)"          # NOT bare 'A'
    assert r.exact is True
    assert _groups(r.cnf_string) == [["A"]]


def test_exact_distribution_common_path():
    # (A AND B) OR D  ->  (A OR D) AND (B OR D)
    r = dnf_to_cnf([["A", "B"], ["D"]])
    assert r.exact is True
    assert r.cnf_string == "(A OR D) AND (B OR D)"
    assert _groups(r.cnf_string) == [["A", "D"], ["B", "D"]]


def test_within_clause_dedup_and_subsumption():
    # (A AND B) OR (A AND C) -> distribute -> minimize -> (A) AND (B OR C)
    r = dnf_to_cnf([["A", "B"], ["A", "C"]])
    assert r.exact is True
    assert r.cnf_string == "(A) AND (B OR C)"
    assert _groups(r.cnf_string) == [["A"], ["B", "C"]]


def test_common_literal_factoring():
    # (A AND X) OR (B AND X) -> (A OR B) AND (X)  [canonical shared-coreq pattern]
    r = dnf_to_cnf([["A", "X"], ["B", "X"]])
    assert r.exact is True
    assert r.cnf_string == "(A OR B) AND (X)"
    assert _groups(r.cnf_string) == [["A", "B"], ["X"]]   # verified round-trip


def test_clause_sort_key_is_list_not_rendered_string():
    # list-vs-string sort diverges: a non-subsumed pair neither a subset of the
    # other exercises the SORT itself. (A AND C) OR (B AND C) distributes to
    # clauses {A,B},{A,C},{B,C},{C}; subsumption drops the supersets of {C},
    # leaving (A OR B) AND (C) — list-key byte-stable order.
    r2 = dnf_to_cnf([["A", "C"], ["B", "C"]])
    assert r2.cnf_string == "(A OR B) AND (C)"
    assert _groups(r2.cnf_string) == [["A", "B"], ["C"]]


def test_single_disjunctive_branch_collapses():
    # (A OR B OR C) expressed as DNF [[A],[B],[C]] -> CNF (A OR B OR C)
    r = dnf_to_cnf([["A"], ["B"], ["C"]])
    assert r.exact is True
    assert r.cnf_string == "(A OR B OR C)"
    assert _groups(r.cnf_string) == [["A", "B", "C"]]


def test_normalization_merges_spellings():
    r = dnf_to_cnf([["MATH 245"], ["math  245"]])
    assert r.cnf_string == "(MATH 245)"   # _norm-merged, canonical
    assert r.exact is True


def test_to_catalog_string_helper_is_pure():
    # to_catalog_string is a standalone serializer on already-built groups.
    assert to_catalog_string([]) == ""
    assert to_catalog_string([["A"]]) == "(A)"
    assert to_catalog_string([["A", "D"], ["B", "D"]]) == "(A OR D) AND (B OR D)"


# --------------------------------------------------- delimiter/paren literal guard
def test_delimiter_or_paren_literal_routes_to_flagged_fallback():
    # A literal carrying ' OR '/' AND '/'('/')' would corrupt the parse_prereq
    # round-trip. It must NOT emit a mis-parsing string; the course is flagged.
    for dnf in ([["BIO 3 OR 4"]], [["MATH 125 (FORMERLY 120)"]]):
        r = dnf_to_cnf(dnf)
        assert r.exact is False, dnf
        assert "unserializable_literal" in (r.fallback_reason or ""), dnf
        # the emitted string must not mis-parse into phantom/lost literals:
        groups = _groups(r.cnf_string)
        assert "4" not in (groups[0] if groups else [])           # no phantom literal
        assert all(")" not in lit and "(" not in lit for g in groups for lit in g)


def test_delimiter_literal_only_content_emits_blank_flagged():
    # When the unserializable literal is the ONLY content, emit '' (no constraint),
    # still flagged — never a string that mis-parses.
    r = dnf_to_cnf([["BIO 3 OR 4"]])
    assert r.cnf_string == ""
    assert r.exact is False
    assert "unserializable_literal" in (r.fallback_reason or "")


# --------------------------------------------------- self-reference drop
def test_self_reference_dropped_to_no_constraint():
    r = dnf_to_cnf([["C"]], gated_course="C")
    assert r.cnf_string == ""
    assert r.exact is False
    assert r.fallback_reason == "self_referential_prereq_dropped"


def test_self_reference_drop_normalizes_gated_course():
    # gated_course must be _norm'd BEFORE comparison, else a raw-cased self-ref
    # leaks through and makes solve_cohort false-INFEASIBLE. Lower-case 'c' +
    # mixed spellings must still drop the self-ref AND merge spellings.
    r = dnf_to_cnf([["math 245"], ["C"]], gated_course="c")
    assert "C" not in [lit for g in _groups(r.cnf_string) for lit in g]   # self-ref gone
    assert r.cnf_string == "(MATH 245)"                                   # spelling merged


# --------------------------------------------------- clause-budget guard + fallback
def test_guard_exceeded_emits_flagged_union_fallback():
    # 3 branches x 3 literals -> product 27 clauses; budget 4 forces the fallback.
    dnf = [["A", "B", "C"], ["D", "E", "F"], ["G", "H", "I"]]
    r = dnf_to_cnf(dnf, max_clauses=4)
    assert r.exact is False
    assert "clause_budget_exceeded" in r.fallback_reason
    assert _groups(r.cnf_string) == [["A", "B", "C", "D", "E", "F", "G", "H", "I"]]
    # every source literal retained (closure unchanged)
    assert set(_groups(r.cnf_string)[0]) == {"A", "B", "C", "D", "E", "F", "G", "H", "I"}


def test_fallback_union_excludes_self_reference():
    # On the budget-exceeded path the union must be over the CLEANED dnf, so a
    # self-referential literal must NOT leak back into the union OR-clause (that
    # would re-create the catastrophic self-prereq on the 'safe' fallback path).
    big = [["X", "A1", "B1"], ["X", "A2", "B2"], ["X", "A3", "B3"], ["X", "A4", "B4"]]
    r = dnf_to_cnf(big, gated_course="X", max_clauses=4)
    assert r.exact is False
    assert "X" not in _groups(r.cnf_string)[0]    # gated_course not in the union


def test_just_under_budget_stays_exact():
    # 2x2 predicts 4 clauses; budget 4 -> exact (boundary)
    r = dnf_to_cnf([["A", "B"], ["C", "D"]], max_clauses=4)
    assert r.exact is True


def test_branch_cap_trigger_names_branch_cap_not_product():
    # The SECONDARY guard (len(branches) > BRANCH_CAP) must fire INDEPENDENTLY of
    # the product and report the ACCURATE trigger. Build 13 branches (> cap 12)
    # whose product is small (2 <= default budget 64): 12 single-literal branches
    # plus one 2-literal branch. The old code reported "product 2 > budget 64"
    # (factually false); the reason must now name the branch-cap trigger and the
    # product clause-budget message must NOT appear.
    dnf = [[f"S{i}"] for i in range(BRANCH_CAP)] + [["X", "Y"]]
    assert len(dnf) == BRANCH_CAP + 1
    r = dnf_to_cnf(dnf)                       # default budget 64; product is only 2
    assert r.exact is False
    assert "branch_cap_exceeded" in r.fallback_reason
    assert "clause_budget_exceeded" not in r.fallback_reason
    assert "product 2 > budget" not in r.fallback_reason   # no false product claim
    assert f"{BRANCH_CAP + 1} branches > cap {BRANCH_CAP}" in r.fallback_reason


def test_pure_or_wider_than_branch_cap_stays_exact_single_clause():
    # A pure-OR DNF (every branch a single literal) has product 1 and IS the
    # union exactly, so even when it is wider than BRANCH_CAP it must be emitted
    # as ONE EXACT clause — never routed through the flagged fallback (that was an
    # avoidable over-conservative exact=False loss on a representable input).
    dnf = [[f"C{i}"] for i in range(BRANCH_CAP + 1)]   # 13 single-literal OR-branches
    r = dnf_to_cnf(dnf)
    assert r.exact is True
    assert r.fallback_reason is None
    assert r.clause_count == 1
    parsed = _groups(r.cnf_string)
    assert parsed == [sorted(f"C{i}" for i in range(BRANCH_CAP + 1))]   # one OR-clause, all literals


def test_blank_only_branch_is_tautology_not_dropped():
    # A branch whose only literal normalizes to blank (['  ']) is the empty
    # conjunction == TRUE => the whole disjunction is a tautology => no
    # constraint, MATCHING the truly-empty [[]] case and the _clean_dnf
    # docstring. (Previously it was silently DROPPED via had_only_self_ref.)
    r = dnf_to_cnf([["  "]])
    assert r.cnf_string == ""
    assert r.exact is True
    assert r.fallback_reason is None
    assert _groups(r.cnf_string) == []


def test_blank_branch_makes_whole_dnf_no_constraint_even_with_gated_course():
    # [['  '], ['X','A']] gated on X: the blank branch is TRUE, so the whole DNF
    # is a no-constraint '' (NOT '(A)'). Pins the documented all-blank-AND==TRUE
    # semantics over the prior conservative-tightening divergence.
    r = dnf_to_cnf([["  "], ["X", "A"]], gated_course="X")
    assert r.cnf_string == ""
    assert r.exact is True
    assert r.fallback_reason is None
    assert _groups(r.cnf_string) == []


def test_guard_is_pre_minimization_conservative():
    # Documents the chosen behavior: the guard checks the PRE-minimization product.
    # (A AND B) OR (A AND C) has product 4 but minimizes to 2 clauses. With budget 3,
    # product (4) > budget so it falls back EARLY even though minimized clauses (2) fit.
    r = dnf_to_cnf([["A", "B"], ["A", "C"]], max_clauses=3)
    assert r.exact is False                       # fell back on pre-min product, by design
    assert "clause_budget_exceeded" in r.fallback_reason


def test_fallback_is_under_approx_keeps_engine_feasible():
    # The load-bearing safety property, validated end-to-end against solve_cohort.
    # UNIT-CAP bind (NOT season): H=2, max_units=6, all of {X,A,B,D} every season,
    # X's true prereq (A AND B) OR D. Exact CNF FEASIBLE, over-approx INFEASIBLE,
    # union FEASIBLE. (A season bind would be vacuous — closure schedules all
    # literals regardless of clause strength.)
    import pandas as pd

    def solve(prereq_for_X):
        terms = [2248, 2252]   # two distinct seasons; both offer every course
        sec = pd.DataFrame([
            {"Term": t, "CLASS": c, "Class Status": "Active",
             "Cap Enrl": 0, "Tot Enrl": 0, "Wait Tot": 0}
            for c in ("X", "A", "B", "D") for t in terms])
        cat = pd.DataFrame([
            {"Course ID": c, "Units": 3,
             "Prerequisites (structured)": (prereq_for_X if c == "X" else "")}
            for c in ("X", "A", "B", "D")])
        prog = pd.DataFrame([
            {"Program Code": "P", "Program Title": "T", "Course ID": c,
             "Recommended Semester": 1} for c in ("X", "A", "B", "D")])
        active, cs, units, prq = engine.build_model(sec, cat, prog)
        return engine.solve_cohort("P", prog, cs, units, prq,
                                   {"horizon": 2, "max_units": 6}, False)

    exact = dnf_to_cnf([["A", "B"], ["D"]], gated_course="X")
    assert exact.cnf_string == "(A OR D) AND (B OR D)" and exact.exact is True
    assert solve(exact.cnf_string) is not None           # exact FEASIBLE
    assert solve("(A) AND (B) AND (D)") is None           # over-approx false-INFEASIBLE
    union = dnf_to_cnf([["A", "B"], ["D"]], gated_course="X", max_clauses=1)
    assert union.exact is False and "clause_budget_exceeded" in union.fallback_reason
    assert solve(union.cnf_string) is not None           # union UNDER-approx stays FEASIBLE


# --------------------------------------------------- result metadata
def test_result_carries_groups_and_budget():
    r = dnf_to_cnf([["A", "B"], ["D"]], max_clauses=DEFAULT_MAX_CLAUSES)
    assert r.cnf_groups == [["A", "D"], ["B", "D"]]
    assert r.clause_count == 2
    assert r.clause_budget == DEFAULT_MAX_CLAUSES
    assert r.fallback_reason is None
