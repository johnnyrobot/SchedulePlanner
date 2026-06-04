"""
chat_assist.py — grounded, offline data assistant (local Gemma via Ollama).

Answers natural-language questions about the CURRENT scheduling analysis and, when
a question needs information beyond it, performs a small fixed set of READ-ONLY
live LACCD lookups. Reliability over flaky tool-calling: the model emits ONE
strict-JSON intent from a fixed menu (validated the same way as
llm_assist.parse_prereq_text); the actual fetch is deterministic Python via the
existing ``sources/`` clients.

Three steps per question: route -> (optional) lookup -> answer (two model calls;
the fetch in between is deterministic). All network IO lives here (outside
engine.run); nothing raises to the caller — every model call and every lookup
degrades to an honest message. The model never decides the schedule and never
issues anything but one of the four whitelisted, side-effect-free lookups.
"""
from __future__ import annotations
import json

import llm_assist
from sources import assist, elumen_client, mapping, program_mapper, schedule

LOOKUP_TYPES = {"none", "offering", "program", "prereqs", "ge"}
GE_GOALS = {"igetc", "cal-getc", "csu-ge"}
MAX_COURSES = 6          # bound a single lookup
MAX_HISTORY = 6          # recent turns fed to the answer pass

ROUTER_SYS = (
    "You decide whether answering a question about a college scheduling analysis "
    "needs a LIVE data lookup beyond the analysis already given to the user. "
    "Output ONLY one JSON object — no prose, no markdown fences. It must be one of:\n"
    '{"lookup":"none"}\n'
    '{"lookup":"offering","campus":"LAMC","terms":[2268],"courses":["BIOLOGY 6"]}\n'
    '{"lookup":"program","campus":"LAMC","program":"Chemistry"}\n'
    '{"lookup":"prereqs","campus":"LAMC","courses":["MATH 261"]}\n'
    '{"lookup":"ge","campus":"LAMC","goal":"igetc","area":"2A"}\n'
    'Use {"lookup":"none"} when the analysis already answers it, or for general or '
    "drafting requests. `offering` checks whether courses are scheduled; `program` "
    "fetches another program's required courses; `prereqs` fetches a course's "
    "prerequisites; `ge` fetches courses satisfying a transfer GE area. `goal` is "
    "one of igetc, cal-getc, csu-ge. Course codes look like 'MATH 261'. Omit "
    "fields you don't know — campus/terms default to the current build."
)

ANSWER_SYS = (
    "You are a concise assistant for a community-college course-scheduling tool. "
    "Answer using ONLY the ANALYSIS DATA and any LIVE LOOKUP facts provided below. "
    "If the answer is not in that data, say you don't have that information — never "
    "invent course numbers, counts, terms, or fixes. You may draft emails or "
    "summaries FROM the data when asked. Be brief and specific. Plain text, no "
    "markdown headings."
)


# ----------------------------------------------------------------- small helpers
def _defaults(results: dict) -> dict:
    """campus/terms to fall back on when the router omits them."""
    return {"campus": results.get("campus") or "LAMC",
            "terms": list(results.get("live_terms") or schedule.DEFAULT_TERMS)}


def _history_tail(history, n: int) -> str:
    if not history:
        return ""
    out = []
    for m in list(history)[-n:]:
        if not isinstance(m, dict):
            continue
        content = str(m.get("content", "")).strip()
        if not content:
            continue
        out.append(("User: " if m.get("role") == "user" else "Assistant: ") + content)
    return "\n".join(out)


def _subject_of(code: str) -> str:
    """'MATH 261' -> 'MATH'; 'CO SCI 487' -> 'CO SCI'. '' if no number split."""
    s = str(code or "").strip()
    return s.rsplit(" ", 1)[0] if " " in s else ""


def _clean_campus(value):
    s = str(value or "").strip().upper()
    return s if s.isalnum() and 1 <= len(s) <= 8 else None


def _clean_courses(value):
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    out = []
    for c in value:
        c = str(c).strip()
        if c and c not in out:
            out.append(c)
    return out[:MAX_COURSES]


def _clean_terms(value):
    if not isinstance(value, list):
        return []
    out = []
    for t in value:
        try:
            n = int(t)
        except (TypeError, ValueError):
            continue
        if n > 0 and n not in out:
            out.append(n)
    return out[:6]


# ----------------------------------------------------------------- grounding text
def _context(results: dict) -> str:
    """Rich, compact plain-text grounding: the template summary (programs +
    diagnostics) plus what it omits — per-term plans, GE coverage, reconciliation,
    and live build metadata. This is the single source of truth fed to the model."""
    base = llm_assist._template_summary(results) or "No analysis is loaded yet."
    extra = []

    meta = []
    if results.get("campus"):
        meta.append(f"campus {results['campus']}")
    if results.get("live_terms"):
        meta.append("terms " + ", ".join(str(t) for t in results["live_terms"]))
    pi = results.get("program_info")
    if pi:
        award = f" ({pi.get('award')})" if pi.get("award") else ""
        meta.append(f"program {pi.get('title', '')}{award}")
    if meta:
        extra += ["BUILD: " + " · ".join(meta)]

    plan_lines = []
    for _code, p in (results.get("programs") or {}).items():
        for ck, label in (("full_time", "Full-time"), ("part_time", "Part-time")):
            c = (p.get("cohorts") or {}).get(ck)
            if c and c.get("plan"):
                terms = " | ".join(
                    f"T{t}: {', '.join(v)}"
                    for t, v in sorted(c["plan"].items(), key=lambda kv: int(kv[0])))
                plan_lines.append(f"- {p.get('title')} [{label}]: {terms}")
    if plan_lines:
        extra += ["", "TERM-BY-TERM PLANS", *plan_lines]

    ge = results.get("ge_coverage")
    if ge and ge.get("requested"):
        gl = [f"Pattern {ge.get('pattern')} (ASSIST status: {ge.get('assist_status')})."]
        for a in (ge.get("areas") or []):
            gl.append(f"- Area {a.get('area')} {a.get('title', '')}: "
                      f"need {a.get('required')}, plan {a.get('resolution')}")
        shared = ge.get("shared_with_major") or []
        if shared:
            gl.append("Met by major: "
                      + ", ".join(sorted({s.get("course", "") for s in shared})))
        extra += ["", "GENERAL EDUCATION", *gl]

    rec = results.get("reconciliation")
    if rec:
        line = (f"{rec.get('matched_count')} program courses offered in the fetched "
                f"terms, {rec.get('unmatched_count')} not offered.")
        if rec.get("unmatched"):
            line += " Not offered: " + ", ".join(rec["unmatched"]) + "."
        extra += ["", "LIVE RECONCILIATION", line]

    tbc = (results.get("analysis") or {}).get("time_block_collisions") or []
    if tbc:
        extra += ["", "TIME CONFLICTS (required courses that clash by meeting time)",
                  *[f"- {f.get('summary')}" for f in tbc]]

    bld = (results.get("analysis") or {}).get("buildability")
    if bld and bld.get("status") == "active":
        bl = []
        for p in bld.get("programs", []):
            bits = [f"{p.get('available')}/{p.get('required_total')} required offered"]
            if p.get("missing"):
                bits.append("missing " + ", ".join(p["missing"]))
            bits.append("time-conflict-free" if (p.get("time_conflict") or {}).get("feasible")
                        else "has time conflicts")
            if p.get("single_section_required"):
                bits.append(f"{len(p['single_section_required'])} single-section")
            bl.append(f"- {p.get('title') or p.get('code')} (score {p.get('score')}/100): "
                      + "; ".join(bits) + ".")
        # The honest framing must travel with the numbers (structural proxy, not a
        # measured completion rate) so the assistant never overclaims.
        extra += ["", "PROGRAM BUILDABILITY (structural-feasibility PROXY, NOT a measured "
                  "completion rate)", *bl]

    bnk = (results.get("analysis") or {}).get("bottlenecks")
    if bnk and bnk.get("status") == "active":
        trunc = bnk.get("truncated") or {}
        board = bnk.get("leaderboard") or []
        nl = []
        for r in board[:8]:
            nl.append(f"- {r.get('course')} (risk {r.get('risk_score')}): required by "
                      f"{r.get('n_programs')} programs, {r.get('n_sections')} section(s).")
        # Honest count of what this grounding leaves out: the rows beyond the [:8]
        # shown here PLUS any the leaderboard itself dropped past its cap — never a
        # silent truncation.
        hidden = max(0, len(board) - 8) + (trunc.get("leaderboard") or 0)
        if hidden:
            nl.append(f"(+{hidden} more ranked bottleneck course(s) not shown.)")
        gaps = bnk.get("gaps") or []
        if gaps:
            nl.append("Required across programs but not offered: "
                      + ", ".join(f"{g.get('course')} (x{g.get('n_programs')})"
                                  for g in gaps[:8]) + ".")
            hidden_gaps = max(0, len(gaps) - 8) + (trunc.get("gaps") or 0)
            if hidden_gaps:
                nl.append(f"(+{hidden_gaps} more required-but-not-offered course(s) "
                          "not shown.)")
        # Honest framing rides with the ranking so the assistant never overclaims:
        # it is a structural supply-vs-demand proxy, not a measured completion rate.
        extra += ["", "CROSS-PROGRAM BOTTLENECKS (supply-vs-demand PROXY, NOT a measured "
                  "completion rate)", *nl]

    return base + ("\n" + "\n".join(extra) if extra else "")


# ----------------------------------------------------------------- router
def route(question: str, defaults: dict, history=None, *, model=llm_assist.MODEL) -> dict:
    """Ask the model for ONE validated lookup intent. Any failure -> 'none'."""
    hist = _history_tail(history, 4)
    prompt = (f"CURRENT BUILD: campus={defaults['campus']}, terms={defaults['terms']}.\n"
              + (f"RECENT:\n{hist}\n" if hist else "")
              + f"QUESTION: {question}\nJSON:")
    try:
        raw = llm_assist._chat(prompt, model=model, system=ROUTER_SYS).strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        i, j = raw.find("{"), raw.rfind("}")
        if i == -1 or j == -1:
            return {"lookup": "none"}
        data = json.loads(raw[i:j + 1])
    except Exception:
        return {"lookup": "none"}
    if not isinstance(data, dict):
        return {"lookup": "none"}
    lk = str(data.get("lookup", "none")).lower().strip()
    if lk not in LOOKUP_TYPES or lk == "none":
        return {"lookup": "none"}
    return _validate_intent(lk, data, defaults)


def _validate_intent(lk: str, data: dict, defaults: dict) -> dict:
    campus = _clean_campus(data.get("campus")) or defaults["campus"]
    if lk == "offering":
        courses = _clean_courses(data.get("courses"))
        if not courses:
            return {"lookup": "none"}
        return {"lookup": "offering", "campus": campus,
                "terms": _clean_terms(data.get("terms")) or defaults["terms"],
                "courses": courses}
    if lk == "program":
        prog = str(data.get("program", "")).strip()
        return {"lookup": "program", "campus": campus, "program": prog} if prog \
            else {"lookup": "none"}
    if lk == "prereqs":
        courses = _clean_courses(data.get("courses"))
        return {"lookup": "prereqs", "campus": campus, "courses": courses} if courses \
            else {"lookup": "none"}
    if lk == "ge":
        goal = str(data.get("goal", "")).lower().strip()
        if goal not in GE_GOALS:
            return {"lookup": "none"}
        return {"lookup": "ge", "campus": campus, "goal": goal,
                "area": str(data.get("area", "")).strip()}
    return {"lookup": "none"}


# ----------------------------------------------------------------- lookups
def run_lookup(intent: dict, *, client=None):
    """Run one validated lookup. Returns (label, facts). Never raises — any
    SourceError / failure degrades to an honest 'failed' fact."""
    lk = intent.get("lookup", "none")
    try:
        if lk == "offering":
            return _lk_offering(intent, client)
        if lk == "program":
            return _lk_program(intent, client)
        if lk == "prereqs":
            return _lk_prereqs(intent, client)
        if lk == "ge":
            return _lk_ge(intent, client)
    except Exception as e:  # noqa: BLE001 - honest degradation, never into the bridge
        return (lk, f"Live {lk} lookup failed: {type(e).__name__}: {e}")
    return (None, "")


def _lk_offering(intent, client):
    campus, terms, courses = intent["campus"], intent["terms"], intent["courses"]
    label = f"offering · {campus} · terms {', '.join(str(t) for t in terms)}"
    secs = schedule.fetch_sections(campus, terms, client=client)
    wanted = {mapping._norm(c) for c in courses}
    by_course = {}
    for s in secs:
        if mapping._norm(s.get("course", "")) in wanted:
            by_course.setdefault(s.get("course"), []).append(s)
    if not by_course:
        return (label, f"No sections found for {', '.join(courses)} in {campus} "
                       f"terms {', '.join(str(t) for t in terms)}.")
    lines = []
    for course, ss in sorted(by_course.items()):
        bits = []
        for s in ss[:8]:
            seg = f"term {s.get('term')} {s.get('status', '')}".strip()
            when = f"{s.get('days', '')} {s.get('times', '')}".strip()
            bits.append(f"{seg} {when}".strip())
        lines.append(f"{course}: {len(ss)} section(s) — " + "; ".join(bits))
    return (label, "\n".join(lines))


def _lk_program(intent, client):
    campus, query = intent["campus"], intent["program"]
    label = f"program pathway · {query}"
    prog = program_mapper.fetch_program(campus, query, client=client)
    if not prog:
        return (label, f"No program matched '{query}' at {campus}.")
    courses = [c.get("course_id", "") for c in (prog.get("courses") or [])]
    award = f" ({prog.get('award')})" if prog.get("award") else ""
    head = (f"{prog.get('title', query)}{award} at {campus} — "
            f"{len([c for c in courses if c])} required courses:")
    return (f"program pathway · {prog.get('title', query)}",
            head + "\n" + ", ".join(c for c in courses if c))


def _lk_prereqs(intent, client):
    campus, courses = intent["campus"], intent["courses"]
    label = f"prerequisites · {campus}"
    subjects = sorted({_subject_of(c) for c in courses if _subject_of(c)})
    if not subjects:
        return (label, f"Couldn't read a subject from {', '.join(courses)}.")
    records, _ids, _status = elumen_client.fetch_prereq_records(
        campus, subjects, client=client, cache={})
    wanted = {elumen_client.normalize_course_code(c) for c in courses}
    hits = [r for r in records
            if elumen_client.normalize_course_code(r.get("course_id", "")) in wanted]
    if not hits:
        return (label, f"eLumen returned no prerequisite record for "
                       f"{', '.join(courses)} (subjects: {', '.join(subjects)}).")
    lines = [f"{r.get('course_id')}: {r.get('raw') or 'no prerequisites listed'}"
             for r in hits]
    return (label, "\n".join(lines))


def _lk_ge(intent, client):
    campus, goal, area = intent["campus"], intent["goal"], intent.get("area", "")
    label = f"GE / {goal.upper()} · {campus}"
    areas, _year = assist.fetch_ge_courses(campus, goal, client=client)
    if not areas:
        return (label, f"ASSIST returned no GE areas for {goal} at {campus}.")
    items = sorted(areas.items())
    if area:
        filtered = [(a, info) for a, info in items if a.lower() == area.lower()]
        items = filtered or items
    lines = []
    for a, info in items[:12]:
        cs = info.get("courses") or []
        lines.append(f"Area {a} ({info.get('title', '')}): "
                     + (", ".join(cs[:12]) if cs else "no courses listed"))
    return (label, "\n".join(lines))


# ----------------------------------------------------------------- public entry
def chat(question, results, history=None, *, client=None, model=llm_assist.MODEL) -> dict:
    """Answer ``question`` grounded in ``results`` (+ optional live lookup).

    Returns ``{"answer": str, "lookup": <label or None>}``. Caller (app.Api.chat)
    gates on model availability; this assumes the model is reachable but still
    degrades every model call and lookup to a readable message rather than raising.
    """
    q = (question or "").strip()
    if not q:
        return {"answer": "Ask a question about the analysis above.", "lookup": None}

    context = _context(results)
    intent = route(q, _defaults(results), history, model=model)
    label, facts = (None, "")
    if intent.get("lookup", "none") != "none":
        label, facts = run_lookup(intent, client=client)

    hist = _history_tail(history, MAX_HISTORY)
    prompt = ("ANALYSIS DATA\n" + context
              + (f"\n\nLIVE LOOKUP ({label})\n{facts}" if facts else "")
              + (f"\n\nCONVERSATION SO FAR\n{hist}" if hist else "")
              + f"\n\nQUESTION: {q}\nANSWER:")
    try:
        answer = llm_assist._chat(prompt, model=model, system=ANSWER_SYS).strip()
    except Exception as e:  # noqa: BLE001 - never raise into the caller / JS bridge
        answer = f"Sorry — I couldn't reach the local model just now ({type(e).__name__})."
    return {"answer": answer, "lookup": label}
