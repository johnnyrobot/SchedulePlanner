"""ASSIST course-articulation client (public, unauthenticated; CSRF-gated).

Verified live 2026-06-01. The public ASSIST API gates every /api/* call behind a
double-submit CSRF token: GET https://assist.org/ sets a non-HttpOnly
``X-XSRF-TOKEN`` cookie; that value must be echoed as an ``X-XSRF-TOKEN`` request
header (and the cookie resent) on each /api/* call, or the server returns HTTP
400. On a 400 we refresh the token once and retry.

This module supplies, for a campus + transfer goal, the GE-area -> eligible
course-id map (the live equivalent of ASSIST's flat extract files). It is the
authoritative source for *which courses satisfy each GE area*; the per-area
required COUNT/units are policy (see sources/ge.py + data/ge_patterns/).

NO OVERCLAIMING / ToU CAVEAT: ASSIST is public + unauthenticated, but its
terms-of-use / rate-limit / human-approval review is PENDING. This client is
OPT-IN (nothing here runs unless a caller invokes fetch_ge_courses) and BOUNDED
(one /transferability/courses call per pattern per college per year, plus a
cached /AcademicYears). It is best-effort, not production-cleared to crawl.
"""
from __future__ import annotations

import httpx

from .elumen_client import normalize_course_code
from .http import (DEFAULT_TIMEOUT, SourceDataError, SourceError,
                   SourceHTTPError, get_json)

API_ROOT = "https://assist.org/"
API_BASE = "https://assist.org/api"
SOURCE = "ASSIST"

# Campus -> ASSIST institution id (verified live 2026-06-01 from /api/institutions).
CAMPUS_INSTITUTION_IDS = {
    "LACC": 3, "LATTC": 25, "LAHC": 31, "LAVC": 44, "LAMC": 47,
    "LAPC": 86, "WLAC": 91, "ELAC": 118, "LASC": 130,
}

# Transfer goal (UI value) -> ASSIST listType param value.
LIST_TYPES = {"cal-getc": "Cal-GETC", "igetc": "IGETC", "csu-ge": "CSUGE"}


def institution_id_for(campus):
    """Map a campus code (e.g. 'LAMC') to its ASSIST institution id.

    Raises SourceDataError (naming the source) for an unknown campus rather than
    silently defaulting, so a typo surfaces loudly.
    """
    key = str(campus).strip().upper()
    try:
        return CAMPUS_INSTITUTION_IDS[key]
    except KeyError:
        raise SourceDataError(
            f"{SOURCE}: unknown campus {campus!r}; known campuses are "
            f"{sorted(CAMPUS_INSTITUTION_IDS)}."
        ) from None


def _list_type_for(transfer_goal):
    key = str(transfer_goal).strip().lower()
    try:
        return LIST_TYPES[key]
    except KeyError:
        raise SourceDataError(
            f"{SOURCE}: unknown transfer goal {transfer_goal!r}; known goals are "
            f"{sorted(LIST_TYPES)}."
        ) from None


def _bootstrap_token(client):
    """Fetch the CSRF token by hitting the site root so the cookie jar is set.

    Returns the X-XSRF-TOKEN cookie value, or "" when the client does not model
    cookies (the test FakeClient) — in which case the token header is sent empty
    and the fake serves the routed payload regardless.
    """
    cookies = getattr(client, "cookies", None)
    if cookies is None:
        return ""
    try:
        client.get(API_ROOT, headers={"User-Agent": "Mozilla/5.0"})
    except httpx.HTTPError as exc:
        raise SourceError(f"{SOURCE}: CSRF handshake to {API_ROOT} failed: {exc}") from exc
    try:
        return cookies.get("X-XSRF-TOKEN") or ""
    except Exception:  # noqa: BLE001 - any cookie-jar shape -> treat as no token
        return ""


def _auth_headers(token):
    return {
        "X-XSRF-TOKEN": token,
        "Accept": "application/json, text/plain, */*",
        "Referer": API_ROOT,
        "User-Agent": "Mozilla/5.0",
    }


def _get(url, *, params, client, token, source):
    """get_json with the CSRF header; on HTTP 400, refresh the token once + retry.

    Returns ``(payload, token)`` so the caller threads the (possibly refreshed)
    token through subsequent calls.
    """
    try:
        return get_json(url, params=params, headers=_auth_headers(token),
                        client=client, source=source), token
    except SourceHTTPError as exc:
        status = getattr(getattr(exc.__cause__, "response", None), "status_code", None)
        if status != 400:
            raise
        token = _bootstrap_token(client)
        return get_json(url, params=params, headers=_auth_headers(token),
                        client=client, source=source), token


def _latest_academic_year_id(client, token):
    data, token = _get(f"{API_BASE}/AcademicYears", params=None, client=client,
                       token=token, source=f"{SOURCE} AcademicYears")
    if not isinstance(data, list) or not data:
        raise SourceDataError(
            f"{SOURCE} AcademicYears: expected a non-empty list, got "
            f"{type(data).__name__}. The ASSIST response shape may have changed.")
    try:
        latest = max(data, key=lambda y: int(y["Id"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise SourceDataError(
            f"{SOURCE} AcademicYears: entries missing integer 'Id' ({exc}).") from exc
    return int(latest["Id"]), token


def _areas_from_courses(data, *, source):
    if not isinstance(data, dict) or "courseInformationList" not in data:
        raise SourceDataError(
            f"{source}: response missing 'courseInformationList' "
            f"(got {type(data).__name__}). The ASSIST response shape may have changed.")
    areas = {}
    for course in data.get("courseInformationList", []):
        if str(course.get("endTermCode") or "").strip():
            continue  # non-empty endTermCode -> discontinued mapping
        prefix = str(course.get("prefixCode") or "").strip()
        number = str(course.get("courseNumber") or "").strip()
        if not prefix or not number:
            continue  # missing either component -> would yield a bare-subject id
        cid = normalize_course_code(f"{prefix}{number}")
        if not cid:
            continue
        for area in course.get("transferAreas", []):
            code = str(area.get("code") or "").strip()
            if not code:
                continue
            entry = areas.setdefault(
                code, {"title": str(area.get("codeDescription") or "").strip(),
                       "courses": []})
            if cid not in entry["courses"]:
                entry["courses"].append(cid)
    for entry in areas.values():
        entry["courses"].sort()
    return areas


def fetch_ge_courses(campus, transfer_goal, *, academic_year_id=None, client=None):
    """Return ``({area_code: {'title': str, 'courses': [normalized course ids]}}, year_id)``.

    Course ids are normalized with elumen_client.normalize_course_code (leading
    zeros stripped) so they JOIN to the schedule/program course ids the resolver
    canonicalizes the same way. Only currently-active mappings (endTermCode == "")
    are returned. Network IO stays here (or in an injected client) — never inside
    engine.run.
    """
    list_type = _list_type_for(transfer_goal)
    inst_id = institution_id_for(campus)
    owns = client is None
    client = client or httpx.Client(timeout=DEFAULT_TIMEOUT)
    try:
        token = _bootstrap_token(client)
        if academic_year_id is None:
            academic_year_id, token = _latest_academic_year_id(client, token)
        source = (f"{SOURCE} transferability/courses "
                  f"({campus} {list_type} ay={academic_year_id})")
        data, token = _get(
            f"{API_BASE}/transferability/courses",
            params={"institutionId": inst_id, "academicYearId": academic_year_id,
                    "listType": list_type},
            client=client, token=token, source=source)
        return _areas_from_courses(data, source=source), academic_year_id
    finally:
        if owns:
            client.close()
