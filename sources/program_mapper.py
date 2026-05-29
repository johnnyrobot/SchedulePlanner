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
    courses = []
    if chosen and chosen.get("programMapId"):
        map_id = chosen["programMapId"]
        reqs = get_json(_site_url(campus, f"/program-maps/{map_id}"),
                        headers=_headers(campus), client=client,
                        source=f"{SOURCE} program-maps/{map_id} ({campus})")
        for element in reqs.get("pathwayElements", []):
            opp = element.get("recommendedOpportunity") or {}
            if opp.get("type") != "COURSE":
                continue
            code = element.get("name") or opp.get("courseCode")
            if not code:
                continue
            term = opp.get("term") or {}
            courses.append({
                "course_id": code.strip(),
                "title": element.get("shortDescription") or opp.get("courseName", ""),
                "recommended_semester": term.get("termNumber"),
                "units": opp.get("minUnits"),
                "requirement_type": (element.get("requirement") or {}).get("requirementType"),
            })
    return courses


def _slug(text):
    return re.sub(r"[^A-Za-z0-9]+", "-", text.strip()).strip("-").upper()


def fetch_program(campus, query, *, client=None):
    program = search_program(campus, query, client=client)
    if program is None:
        return None
    courses = get_program_courses(campus, program["masterRecordId"], client=client)
    code = _slug(program.get("title", "")) or program["masterRecordId"][:8].upper()
    return {
        "code": code,
        "title": program.get("title", ""),
        "award": program.get("awardShortTitle", ""),
        "ge_pattern": "",
        "courses": courses,
    }
