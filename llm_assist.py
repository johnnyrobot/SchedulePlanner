"""
llm_assist.py — optional Gemma 4 (E2B) layer via Ollama.

Two jobs only:
  1. parse_prereq_text(text)  -> structured prereq logic [[A,B],[C]]
  2. explain(results)         -> plain-language summary for advisors/admin

If Ollama or the model is unavailable, BOTH degrade gracefully:
  - prereq parsing falls back to the regex parser in engine.py
  - explanation falls back to a templated text summary

The LLM never decides the schedule. It only reads/writes text.
"""
from __future__ import annotations
import json
import shutil
import subprocess
import urllib.request

OLLAMA_URL = "http://localhost:11434"
# Default model (real published tag, ollama.com/library/gemma4): gemma4:e2b.
# NOTE: despite the "e2b" (effective-2B) label, the first-run pull is ~7 GB on
# disk — a large, slow download, NOT the couple-hundred-MB grab the name
# implies. Budget for that on the one-click first-run download (PRD F20).
# Heavier swaps gemma4:e4b / gemma4:31b are valid where more RAM is available —
# set MODEL accordingly and pull the matching tag. Matching is tag-exact, so an
# un-pulled tag reports absent and the engine uses the templated fallback.
MODEL = "gemma4:e2b"


# ----------------------------------------------------------- availability
def ollama_installed() -> bool:
    return shutil.which("ollama") is not None


def ollama_running() -> bool:
    try:
        urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=2)
        return True
    except Exception:
        return False


def _name_matches(installed: str, wanted: str) -> bool:
    """Match an installed Ollama model name against a wanted tag.

    Matching is tag-exact: 'gemma4:e2b' must NOT be satisfied by 'gemma4:31b'.
    A wanted name with no ':' (bare family) matches the ':latest' tag.
    """
    if not installed:
        return False
    if installed == wanted:
        return True
    if ":" not in wanted:
        return installed == f"{wanted}:latest"
    return False


def model_present(model: str = MODEL) -> bool:
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=3) as r:
            tags = json.load(r)
        return any(_name_matches(m.get("name", ""), model)
                   for m in tags.get("models", []))
    except Exception:
        return False


def ensure_model(model: str = MODEL, progress=lambda s: None) -> bool:
    """Pull the model if missing. Returns True if available afterward."""
    if not ollama_installed():
        progress("Ollama not installed — see https://ollama.com/download")
        return False
    if model_present(model):
        return True
    progress(f"Downloading {model} (first run only)…")
    try:
        subprocess.run(["ollama", "pull", model], check=True)
        return model_present(model)
    except Exception as e:
        progress(f"Model download failed: {e}")
        return False


def available(model: str = MODEL) -> bool:
    return ollama_running() and model_present(model)


# ----------------------------------------------------------- chat helper
def _chat(prompt: str, model: str = MODEL, system: str = "", options=None) -> str:
    body = {"model": model, "stream": False,
            "messages": ([{"role": "system", "content": system}] if system else [])
                        + [{"role": "user", "content": prompt}]}
    # E3: callers that need a REPRODUCIBLE generation (the in-engine prereq parse)
    # pass Ollama sampling options (temperature 0 + a fixed seed). Omitted by
    # default so explain()/chat() outside engine.run keep Ollama's default sampling.
    if options:
        body["options"] = options
    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/chat",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)["message"]["content"]


# ----------------------------------------------------------- job 1: parse
PREREQ_SYS = (
    "You convert college course prerequisite text into strict JSON. "
    "Output ONLY a JSON array of arrays. Each inner array is an OR-group; "
    "the outer array is ANDed. Use canonical course IDs like 'MATH 245'. "
    "Ignore phrases like 'or equivalent', 'or placement', 'advisory'. "
    "Example: 'MATH 125 or MATH 134, and ENGL 101' -> "
    '[["MATH 125","MATH 134"],["ENGL 101"]]. '
    "If there are no real course prerequisites, output []."
)


# E3: this parse feeds engine.run (via make_prereq_parser -> engine.parse_prereq),
# so its output must be reproducible. Pin Ollama to greedy decoding (temperature 0)
# with a fixed seed. HONEST residual: cross-machine GPU float nondeterminism can
# still vary the raw text; the regex/structured pre-pass in engine.parse_prereq —
# NOT the LLM — remains the byte-identity guarantee for the derived CNF.
_PREREQ_OPTIONS = {"temperature": 0, "seed": 42}


def parse_prereq_text(text: str, model: str = MODEL):
    """LLM prereq parse with safe JSON handling. Returns list-of-lists or None."""
    if not available(model):
        return None
    try:
        raw = _chat(text, model=model, system=PREREQ_SYS,
                    options=_PREREQ_OPTIONS).strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        data = json.loads(raw)
        if isinstance(data, list) and all(isinstance(g, list) for g in data):
            return [[str(c).strip() for c in g] for g in data]
    except Exception:
        return None
    return None


def make_prereq_parser(model: str = MODEL):
    """Return a callable for engine.parse_prereq's `llm` arg, or None."""
    if not available(model):
        return None
    return lambda text: (parse_prereq_text(text, model) or [])


# ----------------------------------------------------------- job 2: explain
def explain(results: dict, model: str = MODEL) -> str:
    """Plain-language briefing. LLM if available, else the templated fallback.

    Both paths share the SAME rich `_template_summary` (per-program status,
    time-to-complete, broken official maps, concrete fixes, and the course-level
    supply bottlenecks), so improving the summary improves the OFFLINE fallback
    too — not just the LLM prompt.
    """
    summary = _template_summary(results)
    if not summary:
        return "No programs were analyzed, so there is nothing to brief on yet."
    if not available(model):
        return summary
    try:
        prompt = (
            "You are writing a short internal briefing for a community college "
            "dean, based ONLY on the scheduling analysis below. Use only the "
            "facts given — never invent course numbers, counts, terms, or fixes. "
            "Write 150-300 words of clear prose: open with the single most "
            "important takeaway and the recommended actions, then each program's "
            "time-to-complete and any broken official map, then the supply "
            "bottlenecks (courses offered too rarely or with only one section). "
            "Be specific and actionable. No preamble, no markdown headings.\n\n"
            "ANALYSIS\n" + summary)
        return _chat(prompt, model=model).strip()
    except Exception:
        return summary


def _status_label(p: dict, ft, pt) -> str:
    """Mirror the UI badge: no plan / needs fix / map broken / on track."""
    if not ft and not pt:
        return "no plan"
    if p.get("official_map_issues") and ft and ft.get("needs_fix"):
        return "needs fix"
    if p.get("official_map_issues"):
        return "map broken"
    return "on track"


def _diagnostics_lines(analysis: dict) -> list:
    """One bullet per NON-EMPTY supply-diagnostic category (course-level).

    Empty categories (e.g. modality_mismatch / under_supply on live data, which
    need the IR enrollment export) are simply omitted rather than printed as
    'none', so the briefing stays focused on real bottlenecks.
    """
    out = []
    rg = analysis.get("rotation_gaps") or []
    if rg:
        out.append("- Rotation gaps (required course offered in too few terms): "
                   + ", ".join(f"{x['course']} ({x['offered']}/{x['of']})" for x in rg))
    ss = analysis.get("single_section") or []
    if ss:
        out.append("- Single-section risk (only one section — a single point of "
                   "failure): " + ", ".join(x["course"] for x in ss))
    mm = analysis.get("modality_mismatch") or []
    if mm:
        out.append("- Modality mismatch (offered only in a low-fill delivery mode): "
                   + ", ".join(f"{x['course']} ({x['fill_pct']}% fill)" for x in mm))
    us = analysis.get("under_supply") or []
    if us:
        def _u(x):
            if x.get("waitlisted", 0) > 0:                 # IR headcount (precise)
                return f"{x['course']} ({x['waitlisted']} waitlisted)"
            sw = x.get("sections_waitlisted")              # live status (breadth)
            if sw is not None:
                return f"{x['course']} ({sw}/{x.get('sections_total')} sections waitlisted)"
            return str(x["course"])
        out.append("- Under-supply (sections at capacity / waitlist pressure): "
                   + ", ".join(_u(x) for x in us))
    return out


def _template_summary(results: dict) -> str:
    """Rich plain-text briefing facts — the single source of truth fed to BOTH
    the LLM prompt and the offline fallback.

    Per program: status (mirrors the UI badge), full-time/part-time
    time-to-complete, any broken official map, and the concrete fix. Then a
    course-level supply-bottlenecks section from the analysis block. Returns ""
    when there are no programs (callers treat that as 'nothing to brief')."""
    programs = results.get("programs", {})
    prog_lines = []
    for pcode, p in programs.items():
        ft = p["cohorts"].get("full_time")
        pt = p["cohorts"].get("part_time")
        bits = []
        if ft:
            tag = " (needs schedule change)" if ft.get("needs_fix") else ""
            bits.append(f"full-time {ft['terms_used']} terms{tag}")
        if pt:
            tag = " (needs schedule change)" if pt.get("needs_fix") else ""
            bits.append(f"part-time {pt['terms_used']} terms{tag}")
        timing = "; ".join(bits) if bits else "no feasible plan found"
        line = f"- {p['title']} — {_status_label(p, ft, pt)}: {timing}."
        if p["official_map_issues"]:
            line += f"\n    published map broken: {'; '.join(p['official_map_issues'])}"
        # surface any concrete fixes (first cohort that has them)
        for ck in ("full_time", "part_time"):
            c = p["cohorts"].get(ck)
            if c and c.get("fixes"):
                fx = ", ".join(f"add {f['course']} in {f['season']}" for f in c["fixes"])
                line += f"\n    recommended fix: {fx}"
                break
        prog_lines.append(line)
    if not prog_lines:
        return ""
    n_prog = len(programs)
    n_terms = results.get("terms_in_data")
    header = f"Scheduling analysis of {n_prog} program" + ("s" if n_prog != 1 else "")
    if n_terms:
        header += f" across {n_terms} terms of schedule data"
    out = [header + ".", "", "PROGRAMS", *prog_lines]
    diag = _diagnostics_lines(results.get("analysis") or {})
    if diag:
        out += ["", "SUPPLY BOTTLENECKS (course-level, across all programs)", *diag]
    return "\n".join(out)


if __name__ == "__main__":
    print("ollama installed:", ollama_installed())
    print("ollama running  :", ollama_running())
    print("model present   :", model_present())
    # fallback demo on engine output
    import engine
    res = engine.run(engine._default_data_path(),
                     llm=make_prereq_parser())   # None if no Ollama -> regex
    print("\n--- explanation (templated fallback if no Gemma 4) ---")
    print(explain(res))
