"""Tests for equity_exposure.py — the Equity / Archetype Exposure View (F6).

Deterministic, pure-function wrapper over the F1 buildability audit: re-run the
audit under constrained availability windows (evening-only, online-only,
two-days-a-week) and report which programs collapse (become structurally
unbuildable) under the constraint. No network, no solver.

Everything here is a STRUCTURAL availability PROXY, never a measured equity
outcome — 'collapse' is about the offered sections, not about real students.
"""
import buildability as B
import equity_exposure as E
from sources import mapping

NORM = mapping._norm


def _program():
    """A small program: 4 required courses, recommended over 3 semesters."""
    return {
        "code": "BIOL-AS", "title": "Biology AS-T", "award": "AS-T",
        "courses": [
            {"course_id": "BIOLOGY 3", "recommended_semester": 1},
            {"course_id": "CHEM 101", "recommended_semester": 1},
            {"course_id": "MATH 261", "recommended_semester": 2},
            {"course_id": "ENGLISH 101", "recommended_semester": 2},
        ],
    }


def _sections():
    """Baseline offered sections — every required course offered at least once.

    Mix of morning (9 AM), evening (>=5 PM), MWF (3-day) and TR (2-day) so the
    archetype filters each drop a distinct subset.
    """
    return [
        # BIOLOGY 3: morning MW (dropped by evening), 2-day (kept by two_day)
        {"course": "BIOLOGY 3", "term": 2268, "class_nbr": "1001", "days": "MW",
         "times": "9:00 AM - 10:15 AM", "modality": ["IN-PERSON"]},
        # CHEM 101: evening MWF (kept by evening), 3-day (dropped by two_day)
        {"course": "CHEM 101", "term": 2268, "class_nbr": "1002", "days": "MWF",
         "times": "6:00 PM - 7:15 PM", "modality": ["IN-PERSON"]},
        # MATH 261: online async (kept by ALL: no meeting + ONLINE token)
        {"course": "MATH 261", "term": 2268, "class_nbr": "1003", "days": "",
         "times": "", "modality": ["ONLINE"], "room": "Mission-Online"},
        # ENGLISH 101: morning TR in-person (dropped by evening+online, kept by two_day)
        {"course": "ENGLISH 101", "term": 2268, "class_nbr": "1004", "days": "T Th",
         "times": "9:00 AM - 10:15 AM", "modality": ["IN-PERSON"]},
    ]


# --------------------------------------------------------------- inert envelope
def test_inert_no_programs():
    rep = E.equity_exposure_report([], _sections())
    assert rep["status"] == "inert"
    assert "no program" in rep["reason"]
    assert rep["label"] == E.EQUITY_LABEL


def test_inert_no_sections():
    rep = E.equity_exposure_report([_program()], [])
    assert rep["status"] == "inert"
    assert "no offered sections" in rep["reason"]
    assert rep["label"] == E.EQUITY_LABEL


def test_inert_when_baseline_inert():
    # None of the program's required courses are offered -> baseline buildability
    # is itself inert, so F6 must carry that reason and never fabricate a score.
    foreign = [{"course": "ART 100", "term": 2268, "class_nbr": "9", "days": "MW",
                "times": "9:00 AM - 10:15 AM", "modality": ["IN-PERSON"]}]
    rep = E.equity_exposure_report([_program()], foreign)
    assert rep["status"] == "inert"
    assert "baseline" in rep["reason"].lower()
    assert rep["label"] == E.EQUITY_LABEL


def test_label_present_on_active_and_inert():
    assert E.equity_exposure_report([], []).get("label") == E.EQUITY_LABEL
    rep = E.equity_exposure_report([_program()], _sections())
    assert rep["label"] == E.EQUITY_LABEL


# --------------------------------------------------------------- active envelope
def _arch(rep, key):
    return next(a for a in rep["archetypes"] if a["key"] == key)


def _prog(arch, code="BIOL-AS"):
    return next(p for p in arch["programs"] if p["code"] == code)


def test_active_has_three_archetypes_in_order():
    rep = E.equity_exposure_report([_program()], _sections())
    assert rep["status"] == "active"
    assert [a["key"] for a in rep["archetypes"]] == ["evening", "online", "two_day"]
    for a in rep["archetypes"]:
        assert a.get("name")


def test_evening_filter_drops_morning_only_course():
    # BIOLOGY 3 (MW 9 AM) and ENGLISH 101 (TR 9 AM) are morning-only -> dropped by
    # evening; CHEM 101 (6 PM) and MATH 261 (async online) survive.
    rep = E.equity_exposure_report([_program()], _sections())
    ev = _arch(rep, "evening")
    assert ev["computable"] is True
    p = _prog(ev)
    assert "BIOLOGY 3" in p["newly_unavailable"]
    assert "ENGLISH 101" in p["newly_unavailable"]
    assert "CHEM 101" not in p["newly_unavailable"]
    assert "MATH 261" not in p["newly_unavailable"]
    assert p["collapsed"] is True
    assert p["score_delta"] < 0
    assert p["baseline_score"] == B.audit_program(_program(), _sections())["score"]


def test_evening_threshold_is_5pm_exact_boundary():
    # A 5:00 PM section is kept (>= EVENING_START); a 4:59 PM section is dropped.
    prog = {"code": "X", "title": "X", "courses": [
        {"course_id": "AAA 1"}, {"course_id": "BBB 1"}]}
    secs = [
        {"course": "AAA 1", "term": 2268, "class_nbr": "1", "days": "MW",
         "times": "5:00 PM - 6:00 PM", "modality": ["IN-PERSON"]},
        {"course": "BBB 1", "term": 2268, "class_nbr": "2", "days": "MW",
         "times": "4:59 PM - 5:59 PM", "modality": ["IN-PERSON"]},
    ]
    ev = _arch(E.equity_exposure_report([prog], secs), "evening")
    p = _prog(ev, "X")
    assert "AAA 1" not in p["newly_unavailable"]   # 5:00 PM kept
    assert "BBB 1" in p["newly_unavailable"]        # 4:59 PM dropped


def test_two_day_filter_drops_three_day_course():
    # CHEM 101 meets MWF (3 days) -> dropped by two_day; BIOLOGY 3 (MW),
    # ENGLISH 101 (TR) and MATH 261 (async) survive.
    rep = E.equity_exposure_report([_program()], _sections())
    td = _arch(rep, "two_day")
    assert td["computable"] is True
    p = _prog(td)
    assert "CHEM 101" in p["newly_unavailable"]
    assert "BIOLOGY 3" not in p["newly_unavailable"]
    assert "ENGLISH 101" not in p["newly_unavailable"]
    assert "MATH 261" not in p["newly_unavailable"]
    assert p["collapsed"] is True


def test_async_section_fits_evening_and_two_day():
    # MATH 261 is async/TBA (no meeting); it must survive BOTH evening and two_day.
    rep = E.equity_exposure_report([_program()], _sections())
    assert "MATH 261" not in _prog(_arch(rep, "evening"))["newly_unavailable"]
    assert "MATH 261" not in _prog(_arch(rep, "two_day"))["newly_unavailable"]


def test_online_active_on_live_modality_excludes_hybrid():
    # ONLINE survives; IN-PERSON and HYBRID drop (HYFLEX/HYBRID need in-person).
    prog = {"code": "Y", "title": "Y", "courses": [
        {"course_id": "ONL 1"}, {"course_id": "INP 1"}, {"course_id": "HYB 1"}]}
    secs = [
        {"course": "ONL 1", "term": 2268, "class_nbr": "1", "days": "", "times": "",
         "modality": ["ONLINE"], "room": "Mission-Online"},
        {"course": "INP 1", "term": 2268, "class_nbr": "2", "days": "MW",
         "times": "9:00 AM - 10:15 AM", "modality": ["IN-PERSON"]},
        {"course": "HYB 1", "term": 2268, "class_nbr": "3", "days": "M",
         "times": "9:00 AM - 10:15 AM", "modality": ["HYBRID"], "room": "Mission-CMS 110"},
    ]
    on = _arch(E.equity_exposure_report([prog], secs), "online")
    assert on["computable"] is True
    p = _prog(on, "Y")
    assert "ONL 1" not in p["newly_unavailable"]
    assert "INP 1" in p["newly_unavailable"]
    assert "HYB 1" in p["newly_unavailable"]     # HYBRID excluded from online


def test_online_excludes_hybrid_hyflex_with_online_room():
    # MUST-FIX regression: a roomless HYBRID/HYFLEX section with an explicit modality
    # and an ONLINE room label must NOT be inferred online. The room-from-label
    # fallback is only for ABSENT modality; an explicit HYBRID/HYFLEX always loses
    # (it requires in-person attendance), so these collapse the online window.
    prog = {"code": "H", "title": "H", "courses": [
        {"course_id": "ONL 1"}, {"course_id": "HYB 1"}, {"course_id": "HYF 1"}]}
    secs = [
        {"course": "ONL 1", "term": 2268, "class_nbr": "1", "days": "", "times": "",
         "modality": ["ONLINE"], "room": "Mission-Online"},
        # roomless HYBRID with an online ROOM label -> the buggy fallback fired here
        {"course": "HYB 1", "term": 2268, "class_nbr": "2", "days": "", "times": "",
         "modality": ["HYBRID", "OER"], "room": "Mission-Online"},
        {"course": "HYF 1", "term": 2268, "class_nbr": "3", "days": "", "times": "",
         "modality": ["HYFLEX"], "room": "Mission-Online Live"},
    ]
    on = _arch(E.equity_exposure_report([prog], secs), "online")
    assert on["computable"] is True
    p = _prog(on, "H")
    assert "ONL 1" not in p["newly_unavailable"]      # genuinely online -> survives
    assert "HYB 1" in p["newly_unavailable"]          # explicit HYBRID -> excluded
    assert "HYF 1" in p["newly_unavailable"]          # explicit HYFLEX -> excluded


def test_online_by_room_still_works_when_modality_absent():
    # The legitimate "modality ABSENT (or empty []) + online room -> infer online"
    # path must NOT regress: a roomless section with NO modality key and an online
    # room label is still treated as online.
    prog = {"code": "R", "title": "R", "courses": [
        {"course_id": "NOMOD 1"}, {"course_id": "EMPTY 1"}]}
    secs = [
        {"course": "NOMOD 1", "term": 2268, "class_nbr": "1", "days": "", "times": "",
         "room": "Mission-Online"},                            # modality key absent
        {"course": "EMPTY 1", "term": 2268, "class_nbr": "2", "days": "", "times": "",
         "modality": [], "room": "Mission-Online"},            # empty modality list
    ]
    on = _arch(E.equity_exposure_report([prog], secs), "online")
    assert on["computable"] is True
    p = _prog(on, "R")
    assert "NOMOD 1" not in p["newly_unavailable"]    # inferred online by room
    assert "EMPTY 1" not in p["newly_unavailable"]    # inferred online by room


def test_online_inert_on_import_no_modality():
    # Import-shape records carry no 'modality' key and no online room label ->
    # online archetype reports NOT ASSESSED, but evening/two_day still computable.
    prog = {"code": "Z", "title": "Z", "courses": [{"course_id": "AAA 1"}]}
    secs = [{"course": "AAA 1", "term": 2268, "class_nbr": "1", "days": "MW",
             "times": "9:00 AM - 10:15 AM", "room": "Mission-CMS 105"}]
    rep = E.equity_exposure_report([prog], secs)
    on = _arch(rep, "online")
    assert on["computable"] is False
    assert "modality" in on["reason"]
    assert "programs" not in on             # no fabricated scores
    assert _arch(rep, "evening")["computable"] is True
    assert _arch(rep, "two_day")["computable"] is True


def test_baseline_score_equals_f1():
    # Every archetype's baseline_score equals the standalone F1 audit, byte-for-byte.
    rep = E.equity_exposure_report([_program()], _sections())
    f1 = B.audit_program(_program(), _sections())["score"]
    for a in rep["archetypes"]:
        if a.get("computable"):
            assert _prog(a)["baseline_score"] == f1


def test_collapse_is_exactly_newly_unavailable():
    # OQ4: collapsed iff newly_unavailable is non-empty (no score floor).
    rep = E.equity_exposure_report([_program()], _sections())
    for a in rep["archetypes"]:
        if not a.get("computable"):
            continue
        for p in a["programs"]:
            assert p["collapsed"] == bool(p["newly_unavailable"])


def test_no_collapse_when_all_sections_fit():
    # A program whose only course is an async online section never collapses under
    # any window (async fits evening+two_day; online fits online).
    prog = {"code": "W", "title": "W", "courses": [{"course_id": "ONL 9"}]}
    secs = [{"course": "ONL 9", "term": 2268, "class_nbr": "1", "days": "", "times": "",
             "modality": ["ONLINE"], "room": "Mission-Online"}]
    rep = E.equity_exposure_report([prog], secs)
    for a in rep["archetypes"]:
        p = _prog(a, "W")
        assert p["collapsed"] is False
        assert p["newly_unavailable"] == []
        assert p["score_delta"] == 0


def test_no_silent_truncation_of_newly_unavailable():
    # More than MAX_NEWLY_UNAVAILABLE morning-only required courses -> capped list
    # + a positive truncation count surfaced at top level.
    n = E.MAX_NEWLY_UNAVAILABLE + 5
    courses = [{"course_id": f"SUBJ {i}"} for i in range(n)]
    prog = {"code": "BIG", "title": "Big", "courses": courses}
    secs = [{"course": f"SUBJ {i}", "term": 2268, "class_nbr": str(i), "days": "MW",
             "times": "9:00 AM - 10:15 AM", "modality": ["IN-PERSON"]} for i in range(n)]
    rep = E.equity_exposure_report([prog], secs)
    ev = _arch(rep, "evening")
    p = _prog(ev, "BIG")
    assert len(p["newly_unavailable"]) == E.MAX_NEWLY_UNAVAILABLE
    assert rep["truncated"]["newly_unavailable"] >= 5


def test_by_design_excludes_intentional_gap():
    # A required course flagged by_design that the evening filter drops must NOT be
    # counted as newly_unavailable / collapse (FF2 honesty).
    rep = E.equity_exposure_report([_program()], _sections(),
                                   by_design={"BIOLOGY 3", "ENGLISH 101"})
    assert rep["by_design_count"] == 2
    ev = _arch(rep, "evening")
    p = _prog(ev)
    # BIOLOGY 3 / ENGLISH 101 are by-design -> excluded from missing on BOTH the
    # baseline and filtered audits, so they never appear as newly_unavailable.
    assert "BIOLOGY 3" not in p["newly_unavailable"]
    assert "ENGLISH 101" not in p["newly_unavailable"]


def test_by_design_count_zero_by_default():
    rep = E.equity_exposure_report([_program()], _sections())
    assert rep["by_design_count"] == 0


def test_filtered_envelope_inert_still_yields_honest_collapse():
    # When a window strands EVERY required course the filtered buildability_report
    # itself goes inert; F6 must still emit an honest per-program score (0) + the
    # full newly-unavailable list, not swallow it behind the inert envelope.
    prog = {"code": "M", "title": "M", "courses": [
        {"course_id": "A 1"}, {"course_id": "B 1"}]}
    secs = [
        {"course": "A 1", "term": 2268, "class_nbr": "1", "days": "MW",
         "times": "9:00 AM - 10:15 AM", "modality": ["IN-PERSON"]},
        {"course": "B 1", "term": 2268, "class_nbr": "2", "days": "TR",
         "times": "9:00 AM - 10:15 AM", "modality": ["IN-PERSON"]},
    ]
    ev = _arch(E.equity_exposure_report([prog], secs), "evening")
    assert ev["computable"] is True and ev["sections_kept"] == 0
    p = _prog(ev, "M")
    assert p["score"] == 0
    assert p["score_delta"] == p["score"] - p["baseline_score"]
    assert sorted(p["newly_unavailable"]) == ["A 1", "B 1"]
    assert p["collapsed"] is True


def test_fits_two_day_counts_days_across_all_blocks():
    """'Two days a week' must count distinct days across EVERY meeting block (M1):
    M (block0) + W,F (block1) = 3 days, so the section does NOT fit a 2-day window."""
    r = {"days": "M", "times": "8:00 AM - 9:00 AM",
         "meetings": [{"days": "M", "times": "8:00 AM - 9:00 AM"},
                      {"days": "WF", "times": "1:00 PM - 2:00 PM"}]}
    assert E._fits_two_day(r) is False


def test_fits_evening_uses_earliest_block_start():
    """A morning block disqualifies a section from an evening-only window even if a
    later block is in the evening — the student is still forced into the morning."""
    r = {"days": "F", "times": "6:00 PM - 8:00 PM",
         "meetings": [{"days": "M", "times": "9:00 AM - 10:00 AM"},
                      {"days": "F", "times": "6:00 PM - 8:00 PM"}]}
    assert E._fits_evening(r) is False
