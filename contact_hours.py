"""contact_hours.py — F10/E15: Title 5 §55002.5 contact-hour conformance.

The HONEST REVERSE of the ambiguous units->duration map (which is exactly why
``grid_pressure`` leaves an end-time/duration check INERT): instead of guessing a
meeting's expected length from its units, this reads the OBSERVED scheduled time
and asks whether it is plausible for the units.

For each section that carries enough data to normalize — a real meeting time,
units, AND weeks-of-instruction (woi) — it computes:

    observed weekly contact minutes  (summed over every meeting block in the week)
    -> term contact hours            (weekly_minutes / 60 * woi)
    -> per-unit term contact hours   (term hours / units)

and compares that to a WIDE Title 5 §55002.5 band (~18 lecture-contact-hours per
unit per semester, ~54 for laboratory). It flags ONLY implausible outliers — a
section scheduled far BELOW or far ABOVE the band — never the ambiguous middle.

Honesty (heavily gated, doctrine 2 + #17):
  * A CONFORMANCE PROXY of observed scheduled time vs an implied band — NOT a
    compliance ruling and NOT the official contact-hour-of-record.
  * Weeks-of-instruction normalizes out term length, so an 8-week section is not
    false-flagged against a 16-week band; sections missing units / woi / a meeting
    time are NOT assessed and the counts are SURFACED (never silently dropped).
  * The Activity (2:1) vs Laboratory (3:1) distinction inside the LAB token stays a
    WIDE, low-confidence band; when the contact category is unknown the band widens
    to a lecture-low..lab-high union and the unknown count is disclosed.
  * Only the FIRST meeting block is visible unless the record carries a multi-block
    ``meetings`` list (forward-compatible with E1/#57); the single-block undercount
    risk is disclosed so a multi-block section is never silently undercounted.
  * Outside-class study hours are NOT counted — only in-class scheduled time.

Determinism: pure Python (stdlib + timeblocks), every collection sorted before it
is emitted; runs OUTSIDE engine.run; JSON-serializable.
"""
from __future__ import annotations

from sources import mapping, timeblocks

CONTACT_HOURS_LABEL = (
    "Contact-hour conformance — a Title 5 §55002.5 CONFORMANCE PROXY comparing each "
    "section's OBSERVED scheduled in-class time (weekly meeting minutes × "
    "weeks-of-instruction) to a WIDE implied per-unit band (~18 lecture / ~54 lab "
    "contact hours per unit, per semester). NOT a compliance ruling and NOT the "
    "official contact-hour record; only implausible outliers are flagged, the "
    "Activity-vs-Laboratory band stays wide and low-confidence, outside-class study "
    "hours are not counted, and sections without units / weeks-of-instruction / a "
    "meeting time are not assessed (and surfaced).")

# Title 5 §55002.5 per-unit term contact-hour centers (semester).
LEC_PER_UNIT = 18.0       # lecture: ~1 contact hour/unit/week × ~18 weeks
LAB_PER_UNIT = 54.0       # laboratory: ~3 contact hours/unit/week (3:0 ratio)
LOW_FACTOR = 0.5          # below 0.5× the band center -> implausibly low
HIGH_FACTOR = 1.5         # above 1.5× the band center -> implausibly high
TOP = 20


def _num(v):
    """Tolerant positive float (handles None / '' / '3.0' / NaN), else None."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or f <= 0:   # NaN or non-positive
        return None
    return f


def _category(r):
    """Binary Title 5 contact category from any token the record carries: 'lecture'
    (LEC), 'lab' (LAB / activity — the wide LAB-token family), else None.

    Case-INSENSITIVE lookup: the IR enrollment join attaches the PeopleSoft contact
    category under the key ``Component`` (capital C, sources/enrollment.py), while
    other sources may use lowercase — read both, or the LEC/LAB band selection never
    fires and every section falls back to the wide union band."""
    lower = {str(k).lower(): v for k, v in (r or {}).items()}
    for f in ("contact", "component", "instruction_format", "instructionformat"):
        v = str(lower.get(f, "") or "").upper()
        if "LAB" in v or "ACT" in v:
            return "lab"
        if "LEC" in v:
            return "lecture"
    return None


def _band(category):
    """(low, high) per-unit term contact-hour band for a contact category. Unknown
    category -> a wide lecture-low..lab-high UNION band (only gross outliers fire)."""
    if category == "lecture":
        return LEC_PER_UNIT * LOW_FACTOR, LEC_PER_UNIT * HIGH_FACTOR
    if category == "lab":
        return LAB_PER_UNIT * LOW_FACTOR, LAB_PER_UNIT * HIGH_FACTOR
    return LEC_PER_UNIT * LOW_FACTOR, LAB_PER_UNIT * HIGH_FACTOR


def _weekly_minutes(r):
    """(weekly_scheduled_minutes, used_all_blocks). Sums EVERY meeting block in the
    week. Reads a multi-block ``meetings`` list when present (E1/#57 forward-compat),
    else the single days/times block. No parseable meeting -> 0 minutes."""
    blocks = r.get("meetings")
    if isinstance(blocks, list) and blocks:
        total = 0
        for b in blocks:
            m = timeblocks.parse_meeting(b.get("days", ""), b.get("times", ""))
            total += sum(e - s for (_d, s, e) in m)
        return total, True
    m = timeblocks.parse_meeting(r.get("days", ""), r.get("times", ""))
    return sum(e - s for (_d, s, e) in m), False


def contact_hours_report(sections, *, top=TOP):
    """Honest active/inert envelope for the contact-hour conformance proxy.

    Inert (with a remedy) when there is no section, or when NO section carries the
    units + weeks-of-instruction + meeting time the normalization needs — the bare
    live fetch often lacks woi, so it stays inert until a richer fetch / import
    supplies it. Never an empty 'all good'.
    """
    if not sections:
        return {"status": "inert", "label": CONTACT_HOURS_LABEL,
                "reason": "no sections to assess scheduled contact time against"}

    assessed_rows, flagged = [], []
    na = {"no_meeting_time": 0, "missing_units": 0, "missing_weeks": 0,
          "category_unknown": 0}
    any_all_blocks = False
    any_single_block = False
    seen = set()

    for r in sections:
        cid = mapping._norm(r.get("course", ""))
        if not cid:
            continue
        # Dedup meeting-pattern duplicate ROWS of the same section. Live records
        # carry a real (decorated) class_nbr so distinct sections never collide; two
        # distinct sections that BOTH lack a class_nbr is a latent edge, not a live
        # case (mirrors buildability.offered_by_course's class-nbr-or-time dedup).
        key = (cid, r.get("term"), str(r.get("class_nbr", "") or ""))
        if key in seen:
            continue
        seen.add(key)

        weekly, used_all = _weekly_minutes(r)
        units = _num(r.get("units"))
        woi = _num(r.get("woi"))
        if weekly <= 0:
            na["no_meeting_time"] += 1
            continue
        if units is None:
            na["missing_units"] += 1
            continue
        if woi is None:
            na["missing_weeks"] += 1
            continue

        any_all_blocks = any_all_blocks or used_all
        any_single_block = any_single_block or not used_all
        category = _category(r)
        if category is None:
            na["category_unknown"] += 1
        low, high = _band(category)
        term_hours = weekly / 60.0 * woi
        per_unit = term_hours / units
        within = low <= per_unit <= high
        row = {
            "course": cid, "term": r.get("term"), "units": round(units, 2),
            "weekly_minutes": int(round(weekly)), "woi": round(woi, 1),
            "contact_category": category or "unknown",
            "term_contact_hours": round(term_hours, 1),
            "per_unit_term_hours": round(per_unit, 1),
            "expected_band": [round(low, 1), round(high, 1)],
            "within_band": within,
        }
        assessed_rows.append(row)
        if not within:
            direction = "low" if per_unit < low else "high"
            flagged.append({**row, "direction": direction,
                            "summary": (f"{cid}: {row['per_unit_term_hours']} contact "
                                        f"hours/unit is implausibly {direction} for the "
                                        f"{row['contact_category']} band "
                                        f"{row['expected_band']}")})

    if not assessed_rows:
        return {"status": "inert", "label": CONTACT_HOURS_LABEL,
                "reason": ("no section carries the units, weeks-of-instruction, and "
                           "meeting time needed to normalize contact hours"),
                "remedy": ("supply per-section units, weeks-of-instruction (woi), and "
                           "a meeting day/time — the bare live fetch often omits woi"),
                "not_assessed": na}

    assessed_rows.sort(key=lambda x: (str(x["course"]), str(x["term"])))
    flagged.sort(key=lambda x: (x["direction"], str(x["course"]), str(x["term"])))

    na["meeting_block_coverage"] = (
        "all meeting blocks summed (multi-block sections fully counted)"
        if any_all_blocks and not any_single_block else
        "only the first meeting block was visible for some sections — a multi-block "
        "section (e.g. lecture + lab) may be UNDERCOUNTED until all blocks are captured")

    return {
        "status": "active", "label": CONTACT_HOURS_LABEL,
        "assessed": len(assessed_rows),
        "consistent": sum(1 for r in assessed_rows if r["within_band"]),
        "flagged": flagged[:top],
        "assessed_rows": assessed_rows[:top],
        "used_all_blocks": any_all_blocks and not any_single_block,
        "not_assessed": na,
        "truncated": {"flagged": max(0, len(flagged) - top),
                      "assessed_rows": max(0, len(assessed_rows) - top)},
    }
