r"""Tolerant LACCD facility-table reader (LAC_SRC_FACILITY_DATA -> room map).

The institutional facility export (``LAC_SRC_FACILITY_DATA_*.xlsx``) lists one row
per physical room: ``Facil ID``, ``Building``, ``Room``, ``Capacity`` and a room
``Type`` (``LCTR`` lecture / ``LAB`` / ``CMLB`` computer-lab / ``AUD`` / ``OTRN``).
This module normalizes it into a ``{facil_id -> {capacity, type, building, room}}``
map the room-capacity detector joins onto fetched/imported sections by ``Facil ID``.

It reuses the tolerant frame helpers from :mod:`sources.enrollment_ir` (CSV/xlsx
sheet auto-pick, blank-tolerant int coercion). Online / off-campus sentinels
(``MONLINE`` / ``MONLINELIV``) are NOT physical rooms and are dropped. Real exports
carry a few effective-dated duplicate ids (299 rows / 297 ids); the first row for
an id wins (deterministic).

No network, no PII: a facility table is rooms, not people.
"""
from __future__ import annotations

from .enrollment_ir import _empty, _read_frame, _to_int
from .http import SourceDataError

SOURCE = "facility data"

# Columns the join needs (after no aliasing — the export already uses these names).
REQUIRED_COLUMNS = ["Facil ID", "Capacity"]

# Room types that are scarce, specialized teaching space (the "lab pool").
LAB_TYPES = {"LAB", "CMLB"}

# Online / off-campus Facil ID sentinels — not a physical room to double-book.
_ONLINE_PREFIX = "MONLINE"


def norm_facil(value):
    """Normalize a Facil ID for joining (strip the export's padding, upper-case).

    Real exports pad Facil IDs with leading/trailing spaces (e.g. ``"  MAMP212"``),
    so a raw-string join would silently miss; this collapses both sides to the same
    key."""
    return str(value or "").strip().upper()


def is_physical_room(value):
    """True when a Facil ID denotes a real room (not an ``MONLINE*`` sentinel)."""
    fid = norm_facil(value)
    return bool(fid) and not fid.startswith(_ONLINE_PREFIX)


def is_lab(room_meta):
    """True when a room's ``type`` is in the scarce lab pool (LAB / CMLB)."""
    return (room_meta or {}).get("type", "") in LAB_TYPES


def load_facility(path, *, sheet=None):
    """Read a facility export into ``{facil_id -> {capacity, type, building, room}}``.

    ``capacity`` is an ``int`` (blank / non-numeric -> ``None``, so a missing
    capacity never fabricates a 0-seat room). Online sentinels are skipped;
    duplicate effective-dated ids keep the first row. Raises ``SourceDataError``
    naming the file if a required column is absent."""
    df = _read_frame(path, sheet)
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise SourceDataError(
            f"{SOURCE}: facility export {path!r} is missing required column(s) "
            f"{missing}; got columns {list(df.columns)[:14]}. Is this a LACCD "
            "LAC_SRC_FACILITY_DATA export?")
    rooms = {}
    for rd in df.to_dict("records"):
        fid = norm_facil(rd.get("Facil ID"))
        if not fid or fid.startswith(_ONLINE_PREFIX):
            continue
        if fid in rooms:
            continue  # effective-dated duplicate: first row wins (deterministic)
        cap = None
        if not _empty(rd.get("Capacity")):
            try:
                cap = _to_int(rd.get("Capacity"))
            except (ValueError, TypeError):
                cap = None
        rooms[fid] = {
            "capacity": cap,
            "type": str(rd.get("Type") or "").strip().upper(),
            "building": str(rd.get("Building") or "").strip(),
            "room": str(rd.get("Room") or "").strip(),
        }
    return rooms


if __name__ == "__main__":  # pragma: no cover - manual operator check
    import json
    import sys

    if len(sys.argv) < 2:
        print("usage: python -m sources.facility <facility.(xlsx|csv)> [sheet]")
        raise SystemExit(2)
    _rooms = load_facility(sys.argv[1],
                           sheet=(sys.argv[2] if len(sys.argv) > 2 else None))
    _labs = sum(1 for m in _rooms.values() if is_lab(m))
    _with_cap = sum(1 for m in _rooms.values() if m["capacity"] is not None)
    print(json.dumps({
        "rooms": len(_rooms),
        "labs": _labs,
        "rooms_with_capacity": _with_cap,
        "types": sorted({m["type"] for m in _rooms.values() if m["type"]}),
    }, indent=2))
