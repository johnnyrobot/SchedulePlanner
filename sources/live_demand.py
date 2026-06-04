"""FF4 — bounded live cross-program demand fan-out.

F2 (``cross_program_bottleneck``) ranks the required courses most likely to be a
completion bottleneck across the WHOLE institution: *how many programs require a
given course* vs how few sections / seats / lab rooms it has. That headline
"programs-per-course" demand dimension comes from a
:class:`sources.program_lists.ProgramDemand` — and today that ONLY exists from an
OFFLINE "Program Course Lists" upload. On a BARE LIVE fetch the live path resolves
ONE program per run (``program_mapper.fetch_program``), so it carries no
cross-program counts and F2 is honestly INERT.

This producer fans out across MULTIPLE Program Mapper program maps and aggregates
their required-course sets into the SAME ``ProgramDemand`` shape the offline loader
emits (``required`` / ``listed`` / ``titles``), so F2's ``bottleneck_report``
consumes it with NO change. That activates F2 on the live path.

Honesty + safety (non-negotiable — fanning out over hundreds of program maps is
expensive/slow):

  * **Bounded + opt-in.** :func:`fan_out_demand` does nothing unless the caller
    passes an explicit, non-empty list of program identifiers, and it never fetches
    more than ``max_programs`` of them (default :data:`DEFAULT_MAX_PROGRAMS`). It
    NEVER discovers-and-fans-over the whole catalog itself; the caller chooses the
    bounded set (typically from ``program_mapper.get_all_programs``).
  * **Fails OPEN to inert.** An empty id list, a fully-failing fan-out, or any
    per-program error yields an EMPTY (or partial) ``ProgramDemand`` — F2 then
    reports its existing honest inert envelope. Demand is NEVER fabricated.
  * **Same caveats as F2.** The result is a live-derived supply-vs-demand PROXY,
    not a measured completion rate; it adds no new capability claim.

Plan codes are keyed on the program's UNIQUE ``masterRecordId`` (passed as
``program_id``), never the title slug, so two distinct programs that share a title
(the real "Biology A.S.-T" vs "Biology A.S." case) count as TWO programs rather
than collapsing to one.

The Program Mapper only surfaces concrete ``COURSE``-type pathway elements as a
program's ``courses`` (its required path), so each fetched course is recorded as
BOTH ``listed`` and ``required`` for that plan — mirroring how a Program Course
Lists "Required" row lands in both maps. Course keys are normalized via
:func:`sources.mapping._norm` (identical to the offline loader) so the demand map
joins against offered sections the same way.

No new network primitive: this reuses ``program_mapper.fetch_program_by_id`` and
runs entirely OUTSIDE ``engine.run`` (it is data-gathering, like the rest of
``build_live_workbook.analyze_live``'s fetching).
"""
from __future__ import annotations

from .mapping import _norm
from .program_lists import ProgramDemand
from .program_mapper import fetch_program_by_id

# Default cap on how many program maps a single fan-out will fetch. The fan-out is
# already opt-in (the caller must pass an explicit id list); this is a second,
# hard ceiling so a caller can never accidentally trigger hundreds of program-map
# round-trips. Override with ``max_programs`` for a deliberately wider sweep.
DEFAULT_MAX_PROGRAMS = 25


def _plan_code(spec):
    """Stable, UNIQUE plan code for one program spec.

    Keyed on the program's ``masterRecordId`` (``program_id``) so duplicate-titled
    programs stay distinct. Returns ``""`` when a spec carries no usable id (such a
    spec is skipped — it cannot be fetched or counted)."""
    pid = spec.get("program_id") or spec.get("masterRecordId") or ""
    return str(pid).strip()


def fan_out_demand(campus, program_specs, *, client=None,
                   max_programs=DEFAULT_MAX_PROGRAMS):
    """Aggregate a BOUNDED set of program maps into a :class:`ProgramDemand`.

    ``program_specs`` is an explicit, caller-chosen list of program identifiers
    (each a dict with ``program_id`` / ``masterRecordId`` and optional ``title`` /
    ``award``) — typically a slice of ``program_mapper.get_all_programs``. At most
    ``max_programs`` are fetched (the hard ceiling on top of the opt-in list).

    For each program it calls ``program_mapper.fetch_program_by_id`` and folds every
    resolved ``COURSE`` (the program's required path) into the demand map under that
    program's unique plan code:

      * ``required[_norm(course)] -> {plan_code, ...}``
      * ``listed[_norm(course)]   -> {plan_code, ...}`` (mirrors ``required``)
      * ``titles[plan_code]       -> program title`` (falls back to the plan code)

    FAILS OPEN: an empty/None ``program_specs`` returns an EMPTY ``ProgramDemand``;
    a spec missing an id is skipped; a per-program fetch error is swallowed and that
    program contributes nothing (the rest still aggregate). The result is therefore
    honest by construction — at worst empty, never fabricated — and an empty map
    leaves F2 in its existing inert envelope.

    Pure data-gathering: runs OUTSIDE ``engine.run``, like the rest of the live
    fetch phase. The injected ``client`` keeps it offline-testable.
    """
    demand = ProgramDemand()
    if not program_specs:
        return demand

    seen_plans = set()
    fetched = 0
    for spec in program_specs:
        if fetched >= max_programs:
            break
        plan = _plan_code(spec)
        if not plan or plan in seen_plans:
            continue  # no id, or already counted -> cannot/should-not refetch
        seen_plans.add(plan)
        fetched += 1
        try:
            program = fetch_program_by_id(
                campus, plan, title=spec.get("title", ""),
                award=spec.get("award", ""), client=client)
        except Exception:
            # FAIL OPEN: one program's fetch failing must not abort the whole
            # fan-out (that would silently drop ALL demand). Skip it; the rest of
            # the bounded set still aggregates. Demand is never fabricated.
            continue

        title = (program.get("title") or "").strip()
        demand.titles[plan] = title or plan
        for course in program.get("courses", []):
            cid = _norm(course.get("course_id", ""))
            if not cid:
                continue
            demand.listed.setdefault(cid, set()).add(plan)
            demand.required.setdefault(cid, set()).add(plan)
    return demand
