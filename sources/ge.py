"""Transfer-pattern GE: static pattern-rule loading + the GE requirement resolver.

PURE module (no network). ``load_pattern`` reads a reviewed pattern-rule file
from data/ge_patterns/; ``resolve`` (Task 4) turns pattern rules + ASSIST area
courses + offered sections + Program Mapper data into GE requirement rows plus an
honest coverage report. Per-area COUNTS/units/lab rules are POLICY shipped as
reviewed data — never invented here.
"""
from __future__ import annotations

import json
import os

from .elumen_client import normalize_course_code

_PATTERN_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "data", "ge_patterns")

# Transfer-goal value -> the shipped pattern filename (current effective year).
_PATTERN_FILES = {
    "cal-getc": "cal-getc-2025-2026.json",
    "igetc": "igetc-2026-2027.json",
    "csu-ge": "csu-ge-2026-2027.json",
}


class PatternError(ValueError):
    """Raised when a GE pattern file is unknown or malformed."""


def load_pattern(transfer_goal, *, path=None):
    """Load a reviewed pattern-rule dict for a transfer goal.

    ``path`` overrides the lookup (tests). Raises PatternError for an unknown
    goal or a file missing the required fields, so a bad pattern fails loudly.
    """
    if path is None:
        key = str(transfer_goal).strip().lower()
        fname = _PATTERN_FILES.get(key)
        if not fname:
            raise PatternError(
                f"unknown transfer goal {transfer_goal!r}; known goals are "
                f"{sorted(_PATTERN_FILES)}.")
        path = os.path.join(_PATTERN_DIR, fname)
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        raise PatternError(f"cannot read GE pattern file {path!r}: {exc}") from exc
    if not isinstance(data, dict) or not data.get("areas"):
        raise PatternError(f"GE pattern file {path!r} has no 'areas'.")
    return data


def _canon(course_id):
    """Canonical join key (leading zeros stripped), matching the eLumen join."""
    return normalize_course_code(course_id)
