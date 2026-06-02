"""LACCD Program Mapper client (public, unauthenticated).

Ported from project_laccd_chatbot program_mapper.py: synchronous, no langfuse,
no app.config, no cache. Spoofs the campus PM frontend Origin/Referer (no
credentials). Resolves a program by name query and returns its required courses
with recommended semester + units, parsed from pathwayElements.
"""
from __future__ import annotations

import re

from .http import SourceDataError, get_json

API_BASE = "https://b.api.programmapper.com"
SOURCE = "Program Mapper"

# "Choose a course from Area 3A." / "... Area 4." -> the area code ("3A" / "4").
_AREA_RE = re.compile(r"\bArea\s+([0-9]+[A-Z]{0,2})\b", re.IGNORECASE)
# "MATH 261 or MATH 247" / "MATH 261 / MATH 247" -> ["MATH 261", "MATH 247"].
_CHOICE_SPLIT_RE = re.compile(r"\s+or\s+|\s*/\s*", re.IGNORECASE)


def _area_code(text):
    m = _AREA_RE.search(str(text or ""))
    return m.group(1).upper() if m else ""

COLLEGE_CONFIGS = {
    "LAMC":  {"name": "Los Angeles Mission College",     "origin": "https://la-mission.programmapper.ws",        "site_content_id": "0055f609-1a83-4937-8356-c67ec89cb496"},
    "LAVC":  {"name": "Los Angeles Valley College",      "origin": "https://programmap.lavc.edu",                 "site_content_id": "b42b1741-63ac-4bcf-95b6-48288af8733d"},
    "LAPC":  {"name": "Los Angeles Pierce College",      "origin": "https://programmapper.piercecollege.edu",     "site_content_id": "a10412a2-4b0f-493e-a7d0-2d8c4b1af0e2"},
    "LAHC":  {"name": "Los Angeles Harbor College",      "origin": "https://la-harbor.programmapper.com",         "site_content_id": "170b2c8d-6880-48fe-aea2-d2017ffabe27"},
    "LATTC": {"name": "Los Angeles Trade-Tech College",  "origin": "https://la-trade-tech.programmapper.ws",      "site_content_id": "3973c13e-2554-42a2-aede-02f223d887d0"},
    "LACC":  {"name": "Los Angeles City College",        "origin": "https://la-city.programmapper.ws",            "site_content_id": "82f8d72b-b23d-4f3b-8c4e-efc491c536ff"},
    "ELAC":  {"name": "East Los Angeles College",        "origin": "https://east-la.programmapper.com",           "site_content_id": "679f91e9-a94b-45f3-b0d5-4bae183a3f91"},
    "LASC":  {"name": "Los Angeles Southwest College",   "origin": "https://la-southwest.programmapper.ws",       "site_content_id": "c412a3e5-ac95-4de6-9def-f17f44deedfc"},
    "WLAC":  {"name": "West Los Angeles College",        "origin": "https://west-la.programmapper.ws",            "site_content_id": "b72f9ee4-f902-4c14-9088-f4298008f569"},
}


def _headers(campus):
    cfg = COLLEGE_CONFIGS[campus]
    return {"Origin": cfg["origin"], "Referer": f"{cfg['origin']}/"}


def _site_url(campus, suffix):
    scid = COLLEGE_CONFIGS[campus]["site_content_id"]
    return f"{API_BASE}/site-contents/{scid}{suffix}"


def get_all_programs(campus, *, client=None):
    home = get_json(_site_url(campus, "/home-page-content"),
                    headers=_headers(campus), client=client,
                    source=f"{SOURCE} home-page-content ({campus})")
    if not isinstance(home, dict) or "programGroups" not in home:
        raise SourceDataError(
            f"{SOURCE} home-page-content ({campus}): response missing "
            f"'programGroups' key (got {type(home).__name__}). "
            "The Program Mapper schema may have changed."
        )
    programs = []
    for group in home.get("programGroups", []):
        gid = group.get("masterRecordId")
        if not gid:
            continue
        data = get_json(_site_url(campus, f"/program-groups/{gid}"),
                        headers=_headers(campus), client=client,
                        source=f"{SOURCE} program-groups/{gid} ({campus})")
        for program in data.get("programs", []):
            prog = dict(program)
            prog["group_title"] = group.get("title")
            programs.append(prog)
    return programs


def search_program(campus, query, *, client=None):
    needle = re.sub(r"\s+", " ", query.strip().lower())
    for program in get_all_programs(campus, client=client):
        title = program.get("title", "").lower()
        award = program.get("awardShortTitle", "").lower()
        if needle in title or needle in award:
            return program
    return None


def get_program_courses(campus, program_id, *, client=None):
    detail = get_json(_site_url(campus, f"/programs/{program_id}"),
                      headers=_headers(campus), client=client,
                      source=f"{SOURCE} programs/{program_id} ({campus})")
    pathways = detail.get("pathways", []) if isinstance(detail, dict) else []
    chosen = next((p for p in pathways if p.get("defaultPathway")),
                  pathways[0] if pathways else None)
    courses, ge_requirements, major_choices = [], [], []
    if chosen and chosen.get("programMapId"):
        map_id = chosen["programMapId"]
        reqs = get_json(_site_url(campus, f"/program-maps/{map_id}"),
                        headers=_headers(campus), client=client,
                        source=f"{SOURCE} program-maps/{map_id} ({campus})")
        if not isinstance(reqs, dict) or "pathwayElements" not in reqs:
            raise SourceDataError(
                f"{SOURCE} program-maps/{map_id} ({campus}): response missing "
                f"'pathwayElements' key (got {type(reqs).__name__}). "
                "The Program Mapper schema may have changed.")
        for element in reqs.get("pathwayElements", []):
            opp = element.get("recommendedOpportunity") or {}
            otype = opp.get("type")
            req_type = (element.get("requirement") or {}).get("requirementType")
            term = opp.get("term") or {}
            if otype == "COURSE":
                code = element.get("name") or opp.get("courseCode")
                if not code:
                    continue
                courses.append({
                    "course_id": code.strip(),
                    "title": element.get("shortDescription") or opp.get("courseName", ""),
                    "recommended_semester": term.get("termNumber"),
                    "units": opp.get("minUnits"),
                    "requirement_type": req_type,
                })
            # v1 captures only GE-area and MAJOR_CORE choices; other CHOICE requirementTypes are intentionally skipped.
            elif otype == "CHOICE" and req_type == "GENERAL_EDUCATION":
                area = _area_code(element.get("shortDescription") or element.get("name"))
                if not area:
                    continue
                ge_requirements.append({
                    "area": area,
                    "label": (element.get("name") or "").strip(),
                    "recommended_course": (opp.get("courseCode") or "").strip(),
                    "recommended_semester": term.get("termNumber"),
                })
            elif otype == "CHOICE" and req_type == "MAJOR_CORE":
                options = [o.strip() for o in _CHOICE_SPLIT_RE.split(element.get("name") or "")
                           if o.strip()]
                if len(options) >= 2:
                    major_choices.append({"options": options,
                                          "recommended_semester": term.get("termNumber")})
    return {"courses": courses, "ge_requirements": ge_requirements,
            "major_choices": major_choices}


def _slug(text):
    return re.sub(r"[^A-Za-z0-9]+", "-", text.strip()).strip("-").upper()


def fetch_program_by_id(campus, program_id, *, title="", award="", client=None):
    """Fetch a program by its EXACT masterRecordId (no title search).

    Mirrors fetch_program but targets a specific program id, so duplicate-titled
    programs (e.g. an Interior Design certificate vs the A.A.) are individually
    addressable -- title search can only ever reach the first match. ``title`` /
    ``award`` are the display metadata the caller already holds from the program
    listing; passing them avoids a redundant home-page crawl just to recover the
    program's name. Returns the same dict shape fetch_program returns.

    Contract note: an UNKNOWN id raises (the underlying /programs/{id} fetch
    errors) rather than returning None — ids are expected to come from the
    program listing (get_all_programs), unlike fetch_program which returns None
    for an unknown title query.
    """
    detail = get_program_courses(campus, program_id, client=client)
    code = _slug(title) or str(program_id)[:8].upper()
    return {
        "code": code,
        "title": title,
        "award": award,
        "ge_pattern": "",
        "courses": detail["courses"],
        "ge_requirements": detail["ge_requirements"],
        "major_choices": detail["major_choices"],
    }


def fetch_program(campus, query, *, client=None):
    program = search_program(campus, query, client=client)
    if program is None:
        return None
    pid = program.get("masterRecordId")
    if not pid:
        # Matched a program by title/award but it carries no id to fetch its
        # map with: schema drift. Name the source instead of a bare KeyError.
        raise SourceDataError(
            f"{SOURCE} ({campus}): matched program {program.get('title', '')!r} "
            "is missing 'masterRecordId'; cannot fetch its courses. "
            "The Program Mapper schema may have changed."
        )
    detail = get_program_courses(campus, pid, client=client)
    code = _slug(program.get("title", "")) or program["masterRecordId"][:8].upper()
    return {
        "code": code,
        "title": program.get("title", ""),
        "award": program.get("awardShortTitle", ""),
        "ge_pattern": "",
        "courses": detail["courses"],
        "ge_requirements": detail["ge_requirements"],
        "major_choices": detail["major_choices"],
    }
