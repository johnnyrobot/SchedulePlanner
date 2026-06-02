"""
catalog_ge.py — extract local Associate-degree (AA/AS) GE from a college catalog.

Input is the semantic JSON produced by OpenDataLoader PDF (sources/pdf_loader.py):
a tree of ``kids`` whose nodes are ``heading`` / ``paragraph`` / ``table`` /
``list`` in reading order. This module is pure-Python, deterministic, and never
touches the JVM — so it is fully unit-testable on committed JSON fixtures.

It locates the General-Education section, splits it into AREAS (by area headings),
and harvests the course codes under each area, returning the SAME pair the ASSIST
path produces so the rest of the GE pipeline is reused unchanged:

    extract_local_ge(odl_json) -> (pattern, area_courses, diagnostics)

  * pattern      : {"areas": [{"code","title","count","units_min"}], "reviewed_by": ""}
                   (empty reviewed_by => auto draft-gated by build_live_workbook)
  * area_courses : {area_code: {"title": str, "courses": [course_id, ...]}}
                   (identical shape to assist.fetch_ge_courses' first return value)
  * diagnostics  : honest summary (section found?, per-area course counts, notes)

Extraction is heuristic and tolerant by design; the app shows the result as a
DRAFT the user confirms, so a misparse is surfaced, never silently scheduled.
"""
from __future__ import annotations
import re

# A California CC course code: 1-4 uppercase subject tokens (letters, & or /),
# then a 1-3 digit catalog number with an optional single-letter suffix.
# e.g. "BIOL 3", "MATH 261", "CO SCI 487", "PHYS SC 1", "ENGL 101A".
COURSE_RE = re.compile(r"\b([A-Z][A-Z&/]*(?:\s+[A-Z&/]+){0,3})\s+(\d{1,3}[A-Z]?)\b")

# Headings that open a local-GE section.
_GE_SECTION_RE = re.compile(
    r"general education|ge\s+(?:pattern|plan|requirement)|associate degree.*requirement",
    re.IGNORECASE)
# Heading that ends the GE section when it is a peer (same-or-higher level) and
# is clearly a different catalog section.
_END_SECTION_RE = re.compile(
    r"graduation requirement|degrees?\s+and\s+certificates?|programs? of study|"
    r"transfer requirement|course descriptions?|associate degrees?\b",
    re.IGNORECASE)
# "Area A", "Area 1", "Area II", optional separator, then an optional title.
_AREA_RE = re.compile(
    r"^\s*area\s+([A-Z]|\d{1,2}|[IVX]{1,4})\b[\s:.–—-]*(.*)$",
    re.IGNORECASE)
# Named GE areas with no explicit "Area X" label (substring match, lowercased).
_AREA_NAME_HINTS = (
    "natural science", "social and behavioral", "social & behavioral",
    "behavioral science", "humanities", "english composition", "communication",
    "analytical thinking", "critical thinking", "american institution",
    "mathematic", "quantitative reasoning", "physical education", "health",
    "language and rationality", "arts and humanities", "fine arts",
    "ethnic studies", "lifelong learning", "self-development",
)
# Words that mean a heading is NOT a GE area even if short (avoid false splits).
_NOT_AREA = re.compile(r"note|footnote|total|unit|see |catalog|page\b", re.IGNORECASE)


def _text_of(node) -> str:
    """All descendant text content of a node, in order (cells/list items joined)."""
    if not isinstance(node, dict):
        return ""
    parts = []
    content = node.get("content")
    if isinstance(content, str) and content.strip():
        parts.append(content.strip())
    for key in ("kids", "rows", "cells"):
        for child in node.get(key, []) or []:
            sub = _text_of(child)
            if sub:
                parts.append(sub)
    return " ".join(parts)


def _blocks(odl_json):
    """Flatten the top-level reading order into (kind, level, text) blocks.

    kind is 'heading' or 'text'. Tables/lists are aggregated into a single text
    block (so course codes inside them are captured) — boundary detection relies
    on headings, which catalogs place at the top level.
    """
    for node in (odl_json or {}).get("kids", []) or []:
        if not isinstance(node, dict):
            continue
        ntype = node.get("type")
        if ntype == "heading":
            yield ("heading", int(node.get("heading level", 1) or 1),
                   (node.get("content") or "").strip())
        else:
            text = _text_of(node)
            if text:
                yield ("text", 0, text)


def _courses_in(text: str):
    """Ordered, de-duplicated course codes found in a text block."""
    out, seen = [], set()
    for subj, num in COURSE_RE.findall(text or ""):
        subj = re.sub(r"\s+", " ", subj).strip()
        code = f"{subj} {num}"
        if code not in seen:
            seen.add(code)
            out.append(code)
    return out


def _area_from_heading(text: str):
    """(code, title) if a heading opens a GE area, else None."""
    if not text or _NOT_AREA.search(text):
        return None
    m = _AREA_RE.match(text)
    if m:
        code = m.group(1).upper()
        title = m.group(2).strip(" :.–—-") or text.strip()
        return code, title
    low = text.lower()
    if any(h in low for h in _AREA_NAME_HINTS) and len(text) <= 80:
        return None, text.strip()      # named area; code synthesized by caller
    return None


def extract_local_ge(odl_json):
    """Extract (pattern, area_courses, diagnostics) from OpenDataLoader JSON."""
    blocks = list(_blocks(odl_json))
    start = next((i for i, (k, _l, t) in enumerate(blocks)
                  if k == "heading" and _GE_SECTION_RE.search(t)), None)
    if start is None:
        return ({"areas": [], "reviewed_by": ""}, {},
                {"section_found": False, "area_count": 0, "total_courses": 0,
                 "areas": [], "notes": ["No General Education section heading found."]})

    ge_level = blocks[start][1]
    areas = []                 # ordered [{code, title, courses[]}]
    by_code = {}               # code -> areas entry
    current = None
    synth = 0
    for kind, level, text in blocks[start + 1:]:
        if kind == "heading":
            area = _area_from_heading(text)
            if area is not None:
                code, title = area
                if code is None:
                    synth += 1
                    code = f"G{synth}"
                while code in by_code:           # keep codes unique
                    synth += 1
                    code = f"G{synth}"
                current = {"code": code, "title": title, "courses": [], "_seen": set()}
                areas.append(current)
                by_code[code] = current
                continue
            # A peer/higher heading that's a different catalog section ends GE.
            if level <= ge_level and _END_SECTION_RE.search(text):
                break
            # Other deeper headings inside an area are ignored (stay in current).
            continue
        if current is not None:
            for c in _courses_in(text):
                if c not in current["_seen"]:
                    current["_seen"].add(c)
                    current["courses"].append(c)

    pattern_areas, area_courses, area_diag = [], {}, []
    for a in areas:
        if not a["courses"]:
            continue                              # drop areas we found no courses for
        pattern_areas.append({"code": a["code"], "title": a["title"],
                              "count": 1, "units_min": 3})
        area_courses[a["code"]] = {"title": a["title"], "courses": a["courses"]}
        area_diag.append({"code": a["code"], "title": a["title"],
                          "course_count": len(a["courses"])})

    notes = []
    if not pattern_areas:
        notes.append("GE section found but no areas with courses were parsed.")
    diagnostics = {"section_found": True, "area_count": len(pattern_areas),
                   "total_courses": sum(d["course_count"] for d in area_diag),
                   "areas": area_diag, "notes": notes}
    return ({"areas": pattern_areas, "reviewed_by": ""}, area_courses, diagnostics)
