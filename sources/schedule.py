"""LACCD live class-schedule client (public, unauthenticated).

Ported from project_laccd_chatbot live_schedule.py: synchronous, no langfuse,
no app.config, no cache. The API exposes section structure, modality, units and
an Open/Closed/Waitlist status — but NOT enrollment/capacity/waitlist counts.
"""
from __future__ import annotations

from .http import get_json

API_BASE = "https://services.laccd.edu/apps/api/classschedule"
# Currently-published terms as of 2026-05; override per call as needed.
DEFAULT_TERMS = [2264, 2266, 2268]


def get_subjects(campus, term, *, client=None):
    return get_json(f"{API_BASE}/subjects/{campus}/{term}", client=client)


def get_class_listing(campus, term, subjects=None, *, client=None):
    params = {"subjectlist": ",".join(sorted(subjects))} if subjects else None
    return get_json(
        f"{API_BASE}/listing/{campus}/{term}",
        params=params,
        client=client,
    )


def _iter_sections(course):
    """Yield each section then its relsections (lab/lecture linkage), flat."""
    for section in course.get("sections", []):
        yield section
        for rel in section.get("relsections", []):
            yield rel


def fetch_sections(campus, terms=None, *, client=None):
    """Return a flat list of section records across the given terms."""
    terms = terms or DEFAULT_TERMS
    records = []
    for term in terms:
        listing = get_class_listing(campus, str(term), client=client)
        for subject in listing.get("subjects", []):
            for course in subject.get("courses", []):
                subj = (course.get("subject") or "").strip()
                catalog = (course.get("catalogNbr") or "").strip()
                course_id = f"{subj} {catalog}"
                for section in _iter_sections(course):
                    meetings = section.get("meetings") or []
                    meeting = meetings[0] if meetings else {}
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
                        "woi": section.get("woi", ""),
                        "modality": section.get("classType", []),
                        "days": meeting.get("days", ""),
                        "times": meeting.get("times", ""),
                        "room": meeting.get("room", ""),
                        "instructor": meeting.get("instr", ""),
                    })
    return records
