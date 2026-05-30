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
def _chat(prompt: str, model: str = MODEL, system: str = "") -> str:
    body = {"model": model, "stream": False,
            "messages": ([{"role": "system", "content": system}] if system else [])
                        + [{"role": "user", "content": prompt}]}
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


def parse_prereq_text(text: str, model: str = MODEL):
    """LLM prereq parse with safe JSON handling. Returns list-of-lists or None."""
    if not available(model):
        return None
    try:
        raw = _chat(text, model=model, system=PREREQ_SYS).strip()
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
    """Plain-language summary. LLM if available, else templated fallback."""
    summary = _template_summary(results)
    if not available(model):
        return summary
    try:
        prompt = ("Rewrite the following scheduling findings as a concise briefing "
                  "for a community college dean. Lead with the most actionable "
                  "items. Keep it under 200 words.\n\n" + summary)
        return _chat(prompt, model=model).strip()
    except Exception:
        return summary


def _template_summary(results: dict) -> str:
    lines = []
    for pcode, p in results.get("programs", {}).items():
        ft = p["cohorts"].get("full_time")
        pt = p["cohorts"].get("part_time")
        bits = []
        if ft:
            tag = " (needs schedule change)" if ft.get("needs_fix") else ""
            bits.append(f"full-time {ft['terms_used']} terms{tag}")
        if pt:
            tag = " (needs schedule change)" if pt.get("needs_fix") else ""
            bits.append(f"part-time {pt['terms_used']} terms{tag}")
        line = f"{p['title']}: " + "; ".join(bits)
        if p["official_map_issues"]:
            line += f"  [published map broken: {'; '.join(p['official_map_issues'])}]"
        # surface any concrete fixes
        for ck in ("full_time", "part_time"):
            c = p["cohorts"].get(ck)
            if c and c.get("fixes"):
                fx = ", ".join(f"add {f['course']} in {f['season']}" for f in c["fixes"])
                line += f"  [fix: {fx}]"
                break
        lines.append(line)
    return "\n".join(lines)


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
