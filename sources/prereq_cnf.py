"""Exact DNF->CNF prerequisite converter (PURE — no pandas, no mapping import).

eLumen prerequisite logic is naturally **DNF** (alternative pathways OR'd, each
pathway a set of co-required courses AND'd):

    "(MATH 245 and MATH 246) or MATH 260"  ==  [["MATH 245","MATH 246"], ["MATH 260"]]

The engine consumes **CNF** (AND of OR-groups): ``parse_prereq("(A OR B) AND (C)")
-> [["A","B"],["C"]]``. This module converts DNF->CNF by *exact distribution*
with a configurable clause-count expansion guard; when the predicted product
exceeds the guard it emits a FLAGGED, never-silent conservative **under-
approximation** (a single OR-clause union over the cleaned literals). The flag
travels out-of-band in ConversionResult.exact / .fallback_reason, never embedded
in the emitted catalog string (which always stays valid CNF grammar).

PURITY NOTE — this module must NOT do ``from .mapping import _norm``: importing
``sources.mapping`` transitively pulls ``pandas`` (and inertly ``httpx``) into
sys.modules, which would defeat keeping the core conversion algorithm dependency-
free. Instead ``_norm`` is INLINED below; the inlined body is pinned BYTE-
IDENTICAL to ``mapping._norm`` so converter output keys match catalog ``Course
ID`` after the same normalization.

Why UNDER-approximate is the safe fallback direction (validated against the real
engine.solve_cohort, not merely asserted): every assignment satisfying the true
DNF makes >=1 literal true, so it satisfies the union OR-clause — the fallback's
feasible region is a strict SUPERSET of the true one (it relaxes ordering, never
tightens). An OVER-approximation (ANDing every literal) can return a false "no
plan exists" under a binding unit cap (verified: for true prereq ``(A AND B) OR
D`` with H=2/max_units=6, exact CNF is FEASIBLE but ``(A) AND (B) AND (D)`` is
INFEASIBLE while the union ``(A OR B OR D)`` stays FEASIBLE). A missed ordering
constraint is the same soft, visible loss the engine already ships when the
prereq column is blank; a false-infeasible is catastrophic for an advising tool.
``closure`` (engine.py) pulls every literal of every OR-group into the scheduled
set, so the union retains all courses in the plan — only strictly-before ordering
strength relaxes.
"""
from __future__ import annotations

import itertools
import math
import re
from dataclasses import dataclass

from .http import SourceDataError

DEFAULT_MAX_CLAUSES = 64   # module constant; tunable without a code change
BRANCH_CAP = 12            # secondary guard on the #OR-branches (bounds the product)

# Human label so a malformed-payload guard names where the bad data came from,
# mirroring sources/http.py and sources/mapping.py style.
SOURCE = "eLumen DNF->CNF conversion"

# Serializer delimiters / paren substrings that would corrupt the round-trip
# through engine.parse_prereq (it splits naively on " AND "/" OR " and strips
# outer "()"). Any literal containing one of these is "unserializable".
_UNSERIALIZABLE = (" OR ", " AND ", "(", ")")


def _norm(x):
    # INLINED, must stay BYTE-IDENTICAL to sources.mapping._norm so converter
    # output keys match catalog `Course ID`. Do NOT replace this with
    # `from .mapping import _norm` — that pulls pandas/httpx into this pure module.
    return re.sub(r"\s+", " ", str(x).strip().upper())


@dataclass(frozen=True)
class ConversionResult:
    cnf_string: str                  # engine-ready, e.g. "(A OR D) AND (B OR D)"; "" = no prereq
    cnf_groups: list                 # list[list[str]] mirror of cnf_string
    exact: bool                      # True = exact distribution; False = flagged fallback
    fallback_reason: str | None      # structured reason when exact is False
    clause_count: int                # number of OR-clauses emitted
    clause_budget: int               # the max_clauses guard in force


def _is_blank_sentinel(dnf) -> bool:
    """Top-level 'no prereq' sentinels: None / NaN / '' / [] / list-of-only-empties."""
    if dnf is None:
        return True
    if isinstance(dnf, float) and math.isnan(dnf):
        return True
    if isinstance(dnf, str):
        # A blank/whitespace string is the no-prereq sentinel; a non-empty
        # string is a malformed payload (validated by the caller, not here).
        return dnf.strip() == ""
    if isinstance(dnf, (list, tuple)):
        return len(dnf) == 0
    return False


def _no_constraint(budget: int) -> ConversionResult:
    return ConversionResult(
        cnf_string="", cnf_groups=[], exact=True,
        fallback_reason=None, clause_count=0, clause_budget=budget,
    )


def to_catalog_string(cnf_groups) -> str:
    """Serialize CNF OR-groups to the engine's catalog string.

    Each clause is ALWAYS parenthesized (``"(A)"`` not bare ``"A"``) so
    engine.parse_prereq takes its structured branch unconditionally. Empty input
    -> "" (no prereq). The caller is responsible for the literal/clause ordering;
    this function does not re-sort.
    """
    if not cnf_groups:
        return ""
    return " AND ".join("(" + " OR ".join(grp) + ")" for grp in cnf_groups)


def _clean_dnf(dnf, gated_norm):
    """Normalize, dedup-within-branch, drop self-ref, absorption pre-pass.

    Returns ``(branches, unserializable_lit)``: ``branches`` is a list of
    frozensets of normalized literals (the cleaned DNF), ``unserializable_lit``
    is the first delimiter/paren-bearing literal found (or None).

    A branch that is ALREADY empty (an empty AND-term in the input, or one whose
    only literals normalize away to '') is a tautology (empty AND == TRUE) => the
    whole disjunction is a no-constraint; this is signalled by returning
    ``branches=None``. By contrast a branch emptied SOLELY by the self-reference
    drop is simply DROPPED (per spec §4.2-2): "(A) OR (C)" gated on C keeps "(A)".
    If every branch dies that way the caller reports the self-ref reason.
    """
    branches = []
    for and_term in dnf:
        lits = []
        had_only_self_ref = len(and_term) > 0
        for lit in and_term:
            nlit = _norm(lit)
            if any(tok in nlit for tok in _UNSERIALIZABLE):
                # Bail out of the whole conversion: this literal can't round-trip.
                return None, nlit
            if gated_norm is not None and nlit == gated_norm:
                continue  # self-reference drop (a course can't be its own prereq)
            if nlit:
                had_only_self_ref = False
                if nlit not in lits:
                    lits.append(nlit)
        if not lits:
            if had_only_self_ref:
                # Branch emptied SOLELY by the self-ref drop: drop the branch,
                # not the whole DNF (spec §4.2-2).
                continue
            # An already-empty (or all-blank) AND-term is the empty conjunction
            # == TRUE => the whole disjunction is a tautology => no constraint.
            return None, None
        branches.append(frozenset(lits))

    # Absorption pre-pass: drop any branch that is a (non-strict) superset of
    # another distinct branch — (A) OR (A AND B) == (A). Also dedups identical
    # branches. Equivalence-preserving; shrinks the product before distribution.
    kept = []
    for i, b in enumerate(branches):
        absorbed = False
        for j, other in enumerate(branches):
            if i == j:
                continue
            if other < b or (other == b and j < i):
                absorbed = True
                break
        if not absorbed:
            kept.append(b)
    return kept, None


def _minimize_clauses(clauses):
    """Dedup, clause subsumption, sort literals + clauses (list-key, byte-stable).

    ``clauses`` is an iterable of frozensets. Returns ``list[list[str]]`` with
    each clause's literals sorted and the clauses sorted by their sorted-literal
    LIST key (NOT the rendered string — the two diverge because ' ' (0x20) <
    ')' (0x29), so a string sort would reorder e.g. ['(A OR B)','(A OR B OR C)']).
    """
    # Dedup identical clauses.
    uniq = []
    seen = set()
    for c in clauses:
        if c not in seen:
            seen.add(c)
            uniq.append(c)
    # Clause subsumption: drop clause Q if some distinct clause P is a subset of Q
    # (the smaller clause is the stronger constraint, so Q is redundant).
    kept = []
    for i, q in enumerate(uniq):
        subsumed = False
        for j, p in enumerate(uniq):
            if i == j:
                continue
            if p < q or (p == q and j < i):
                subsumed = True
                break
        if not subsumed:
            kept.append(q)
    # Sort literals within each clause; then sort clauses by their sorted-literal
    # LIST key (byte-stable, not rendered-string order).
    sorted_clauses = [sorted(c) for c in kept]
    sorted_clauses.sort()
    return sorted_clauses


def _fallback_union(branches, budget, reason):
    """Single OR-clause over the union of every literal in the CLEANED branches.

    The union scope is the cleaned DNF (after self-ref drop + absorption,
    excluding unserializable literals), NEVER the original — re-scanning the
    original would let gated_course leak back in and re-create the catastrophic
    self-prereq on the very path meant to be safe.
    """
    union = sorted({lit for b in branches for lit in b})
    if not union:
        # Nothing safe to constrain on: emit no-constraint, still flagged.
        return ConversionResult(
            cnf_string="", cnf_groups=[], exact=False,
            fallback_reason=reason, clause_count=0, clause_budget=budget,
        )
    groups = [union]
    return ConversionResult(
        cnf_string=to_catalog_string(groups), cnf_groups=groups, exact=False,
        fallback_reason=reason, clause_count=1, clause_budget=budget,
    )


def dnf_to_cnf(dnf, *, gated_course=None, max_clauses=DEFAULT_MAX_CLAUSES) -> ConversionResult:
    """Convert a DNF prereq (list[list[str]], OUTER OR'd / INNER AND'd) to CNF.

    Returns a ConversionResult. ``exact=True`` means an exact distribution;
    ``exact=False`` means a flagged conservative under-approximation (the reason
    is in ``fallback_reason``). Top-level None/NaN/''/[] -> '' exact=True. A
    non-empty payload that is not a list-of-lists raises SourceDataError.
    """
    # --- top-level sentinels FIRST (these must never raise) ------------------
    if _is_blank_sentinel(dnf):
        return _no_constraint(max_clauses)

    # --- validate shape: non-empty payload must be a list-of-lists -----------
    if not isinstance(dnf, (list, tuple)) or not all(
        isinstance(branch, (list, tuple)) for branch in dnf
    ):
        raise SourceDataError(
            f"{SOURCE}: malformed prereq payload {dnf!r}; expected a list of "
            "AND-branches (each branch a list of course literals)."
        )

    gated_norm = _norm(gated_course) if gated_course is not None else None

    # --- self-reference: detect whether the gated course appears at all ------
    # If, after cleaning, ALL branches vanish to a self-reference, we report a
    # distinct reason (self_referential_prereq_dropped) rather than a tautology.
    self_referential = False
    if gated_norm is not None:
        for and_term in dnf:
            for lit in and_term:
                if _norm(lit) == gated_norm:
                    self_referential = True
                    break

    # --- clean: normalize, dedup, self-ref drop, absorption ------------------
    branches, unserializable = _clean_dnf(dnf, gated_norm)

    if unserializable is not None:
        # A delimiter/paren-bearing literal can't round-trip; route the WHOLE
        # course to the flagged fallback over the OTHER (safe) literals. Re-clean
        # ignoring the offending branch is not enough — the literal could appear
        # in many branches — so rebuild the union from all safe literals.
        return _fallback_for_unserializable(dnf, gated_norm, max_clauses, unserializable)

    if branches is None:
        # A genuine tautology (an already-empty / all-blank AND-term == TRUE).
        # This is NOT the self-ref case (that drops the branch and yields []).
        return _no_constraint(max_clauses)

    if not branches:
        # Every branch was dropped. If the gated course appeared (every surviving
        # branch died to the self-ref drop), report the distinct self-ref reason
        # so build_live_workbook can label it; otherwise no-constraint.
        if self_referential:
            return ConversionResult(
                cnf_string="", cnf_groups=[], exact=False,
                fallback_reason="self_referential_prereq_dropped",
                clause_count=0, clause_budget=max_clauses,
            )
        return _no_constraint(max_clauses)

    # --- perf guard on the PRE-minimization product (documented conservative)-
    # predicted = product of branch sizes — the size of itertools.product before
    # any subsumption shrinks it. Checked BEFORE materializing the product so the
    # Cartesian product is never allocated on reject. Intentionally pre-
    # minimization: an honest CNF whose FINAL clause count is small may still
    # fall back if its product exceeds the budget (relaxes ordering, never wrong).
    predicted = 1
    for b in branches:
        predicted *= len(b)
    if predicted > max_clauses or len(branches) > BRANCH_CAP:
        reason = (
            f"clause_budget_exceeded: product {predicted} > budget {max_clauses}; "
            f"emitted conservative single-OR union over the cleaned literals "
            f"(UNDER-approximate: ordering relaxed, all courses retained via closure)."
        )
        return _fallback_union(branches, max_clauses, reason)

    # --- exact distribution: one clause per product tuple --------------------
    clauses = [frozenset(combo) for combo in itertools.product(*branches)]
    cnf_groups = _minimize_clauses(clauses)
    return ConversionResult(
        cnf_string=to_catalog_string(cnf_groups),
        cnf_groups=cnf_groups,
        exact=True,
        fallback_reason=None,
        clause_count=len(cnf_groups),
        clause_budget=max_clauses,
    )


def _fallback_for_unserializable(dnf, gated_norm, budget, offending):
    """Flagged fallback when any literal can't round-trip through parse_prereq.

    Builds the union over every SAFE (serializable, non-self-ref) literal in the
    DNF; if no safe literal remains, emits '' (still flagged). Never emits a
    string that mis-parses.
    """
    safe = set()
    for and_term in dnf:
        for lit in and_term:
            nlit = _norm(lit)
            if any(tok in nlit for tok in _UNSERIALIZABLE):
                continue  # drop unserializable literals from the union
            if gated_norm is not None and nlit == gated_norm:
                continue  # never let the gated course back in
            if nlit:
                safe.add(nlit)
    reason = (
        f"unserializable_literal: {offending!r} contains a serializer "
        "delimiter/paren and would corrupt the parse_prereq round-trip; routed "
        "the whole course to the flagged fallback."
    )
    if not safe:
        return ConversionResult(
            cnf_string="", cnf_groups=[], exact=False,
            fallback_reason=reason, clause_count=0, clause_budget=budget,
        )
    groups = [sorted(safe)]
    return ConversionResult(
        cnf_string=to_catalog_string(groups), cnf_groups=groups, exact=False,
        fallback_reason=reason, clause_count=1, clause_budget=budget,
    )
