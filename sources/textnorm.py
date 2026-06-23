"""Low-level course-id / units text normalization.

Pure, stdlib-only helpers shared by the offline readers (``course_master`` etc.)
and the workbook assembler (``mapping``), so neither layer has to import the
other's privates. Byte-identical to the historical ``mapping._norm`` /
``mapping._to_units`` (still re-exported there for backward compatibility, so the
~30 existing ``mapping._norm`` call sites are unchanged).
"""
from __future__ import annotations

import math
import re


def _norm(code):
    return re.sub(r"\s+", " ", str(code).strip().upper())


def _to_units(value, default=3.0):
    """Coerce '3.00', '3-4', 5.0, '' -> float (solver does int(units))."""
    try:
        result = float(str(value).split("-")[0])
    except (ValueError, TypeError):
        return default
    return default if math.isnan(result) else result
