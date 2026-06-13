"""LACCD live class-schedule client (public, unauthenticated).

Ported from project_laccd_chatbot live_schedule.py: synchronous, no langfuse,
no app.config, no cache. The API exposes section structure, modality, units and
an Open/Closed/Waitlist status — but NOT enrollment/capacity/waitlist counts.
"""
from __future__ import annotations

from .http import SourceDataError, get_json

API_BASE = "https://services.laccd.edu/apps/api/classschedule"
# Currently-published terms as of 2026-05; override per call as needed.
DEFAULT_TERMS = [2264, 2266, 2268]
SOURCE = "LACCD schedule"


def get_subjects(campus, term, *, client=None):
    return get_json(
        f"{API_BASE}/subjects/{campus}/{term}", client=client,
        source=f"{SOURCE} subjects endpoint ({campus} {term})",
    )


def get_class_listing(campus, term, subjects=None, *, client=None):
    params = {"subjectlist": ",".join(sorted(subjects))} if subjects else None
    return get_json(
        f"{API_BASE}/listing/{campus}/{term}",
        params=params,
        client=client,
        source=f"{SOURCE} listing endpoint ({campus} {term})",
    )


def _require_dict(value, where, path):
    """Raise a named SourceDataError when a payload *element* is not an object.

    The list-type guards in ``fetch_sections`` only ensure we iterate a list;
    this ensures each element is a dict before we call ``.get()`` on it, so
    element-level schema drift (a string/null/number where an object is
    expected) surfaces by endpoint + JSON path instead of a bare AttributeError
    one nesting level deeper (review M5). ``path`` is a dotted breadcrumb such
    as ``subjects[3].courses[1].sections[0]``.
    """
    if not isinstance(value, dict):
        raise SourceDataError(
            f"{where}: {path} is {type(value).__name__}, expected a JSON object. "
            "The schedule API schema may have changed.")


def _iter_sections(course, where, course_path):
    """Yield ``(section, path)`` then its relsections (lab/lecture linkage), flat.

    Each yielded element is validated to be a dict first (review M5); the path
    is threaded back so the caller can name a malformed meeting under it.
    """
    for i, section in enumerate(course.get("sections", [])):
        sec_path = f"{course_path}.sections[{i}]"
        _require_dict(section, where, sec_path)
        yield section, sec_path
        for j, rel in enumerate(section.get("relsections", [])):
            rel_path = f"{sec_path}.relsections[{j}]"
            _require_dict(rel, where, rel_path)
            yield rel, rel_path


def fetch_sections(campus, terms=None, *, client=None):
    """Return a flat list of section records across the given terms.

    A term that legitimately has no published classes contributes no records;
    a malformed (non-dict / missing-``subjects``) payload raises SourceDataError
    so schema drift surfaces by endpoint name instead of a bare AttributeError.
    """
    terms = terms or DEFAULT_TERMS
    records = []
    for term in terms:
        where = f"{SOURCE} listing endpoint ({campus} {term})"
        listing = get_class_listing(campus, str(term), client=client)
        if not isinstance(listing, dict) or "subjects" not in listing:
            raise SourceDataError(
                f"{where}: response missing "
                f"'subjects' key (got {type(listing).__name__} with keys "
                f"{sorted(listing)[:8] if isinstance(listing, dict) else 'n/a'}). "
                "The schedule API schema may have changed."
            )
        if not isinstance(listing["subjects"], list):
            # 'subjects' present but the wrong type (e.g. a dict): iterating it
            # would blow up opaquely on subject.get(...). Name the endpoint.
            raise SourceDataError(
                f"{where}: 'subjects' is "
                f"{type(listing['subjects']).__name__}, expected a list. "
                "The schedule API schema may have changed."
            )
        for si, subject in enumerate(listing["subjects"]):
            _require_dict(subject, where, f"subjects[{si}]")
            for ci, course in enumerate(subject.get("courses", [])):
                course_path = f"subjects[{si}].courses[{ci}]"
                _require_dict(course, where, course_path)
                subj = (course.get("subject") or "").strip()
                catalog = (course.get("catalogNbr") or "").strip()
                course_id = f"{subj} {catalog}"
                for section, sec_path in _iter_sections(course, where, course_path):
                    meetings = section.get("meetings") or []
                    meeting = meetings[0] if meetings else {}
                    # Gate on the LIST, not the element: a falsy non-dict first
                    # meeting (null/0/"") must still fail loud by path rather than
                    # slip through and bare-crash on meeting.get(...) below.
                    if meetings:
                        _require_dict(meeting, where, f"{sec_path}.meetings[0]")
                    records.append({
                        "term": int(term),
                        "subject": subj,
                        "catalog": catalog,
                        "course": course_id,
                        "title": course.get("descr", ""),
                        "units": course.get("units", ""),
                        "class_nbr": section.get("classNbr", ""),
                        "status": section.get("status", ""),
                        "seats": section.get("seats", ""),
                        # Weeks-of-instruction + the per-section session date range
                        # (e.g. "08/31/26 - 12/20/26"). CAPTURE-ONLY (FF5): carried
                        # so a future calendar/duration check can read them; nothing
                        # consumes them yet. Tolerant default "" when absent.
                        "woi": section.get("woi", ""),
                        "dates": section.get("dates", ""),
                        "modality": section.get("classType", []),
                        "days": meeting.get("days", ""),
                        "times": meeting.get("times", ""),
                        "room": meeting.get("room", ""),
                        # Physical facility id when the API exposes one (tolerant
                        # default ""): lets the room-capacity detector join the
                        # facility table. The schedule-export importer populates it
                        # from the export's "Facil ID" column.
                        "facil_id": meeting.get("facilityId", "") or meeting.get("facilId", ""),
                        "instructor": meeting.get("instr", ""),
                    })
    return records
