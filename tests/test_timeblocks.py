"""Tests for sources/timeblocks.py — meeting parsing, overlap, and conflict feasibility.

Uses the exact day/time string formats the live schedule source emits (see committed
fixtures): days "M W F" / "MW" / "T Th" / "Th" / "" and times "10:35 AM - 11:25 AM" / "".
Pure-Python module, so no network/JVM/solver needed.
"""
from sources import timeblocks as tb


# --- parse_days ---------------------------------------------------------------
def test_parse_days_spaced_and_compact():
    assert tb.parse_days("M W F") == ["M", "W", "F"]
    assert tb.parse_days("MW") == ["M", "W"]
    assert tb.parse_days("MWF") == ["M", "W", "F"]


def test_parse_days_two_char_tokens():
    assert tb.parse_days("T Th") == ["T", "Th"]
    assert tb.parse_days("TTh") == ["T", "Th"]
    assert tb.parse_days("Th") == ["Th"]


def test_parse_days_empty_and_tba():
    assert tb.parse_days("") == []
    assert tb.parse_days("TBA") == []
    assert tb.parse_days(None) == []


def test_parse_days_dedup_preserves_order():
    assert tb.parse_days("M W M") == ["M", "W"]


# --- parse_times --------------------------------------------------------------
def test_parse_times_am_pm():
    assert tb.parse_times("10:35 AM - 11:25 AM") == (635, 685)
    assert tb.parse_times("1:50 PM - 5:00 PM") == (830, 1020)
    assert tb.parse_times("12:00 PM - 12:50 PM") == (720, 770)   # noon = 12 PM
    assert tb.parse_times("12:30 AM - 1:00 AM") == (30, 60)      # 12 AM = midnight


def test_parse_times_empty_or_bad():
    assert tb.parse_times("") is None
    assert tb.parse_times("TBA") is None
    assert tb.parse_times("11:00 AM - 9:00 AM") is None          # non-positive span


# --- meetings / conflict ------------------------------------------------------
def test_parse_meeting_async_is_empty():
    assert tb.parse_meeting("", "") == []
    assert tb.parse_meeting("MW", "") == []      # no time => no meeting


def test_sections_conflict_overlap_truth():
    a = tb.parse_meeting("MW", "9:00 AM - 10:00 AM")
    b = tb.parse_meeting("MW", "9:30 AM - 10:30 AM")   # same days, overlapping time
    c = tb.parse_meeting("MW", "10:00 AM - 11:00 AM")  # adjacent, no overlap
    d = tb.parse_meeting("T Th", "9:00 AM - 10:00 AM")  # different days
    assert tb.sections_conflict(a, b) is True
    assert tb.sections_conflict(a, c) is False
    assert tb.sections_conflict(a, d) is False


def test_async_never_conflicts():
    a = tb.parse_meeting("MW", "9:00 AM - 10:00 AM")
    async_sec = tb.parse_meeting("", "")
    assert tb.sections_conflict(a, async_sec) is False


# --- pairwise_hard_conflict ---------------------------------------------------
def test_pairwise_hard_conflict_all_overlap():
    a = [tb.parse_meeting("MW", "9:00 AM - 10:00 AM")]
    b = [tb.parse_meeting("MW", "9:30 AM - 10:30 AM")]
    assert tb.pairwise_hard_conflict(a, b) is True


def test_pairwise_not_hard_when_an_alternative_exists():
    a = [tb.parse_meeting("MW", "9:00 AM - 10:00 AM")]
    b = [tb.parse_meeting("MW", "9:30 AM - 10:30 AM"),
         tb.parse_meeting("T Th", "9:00 AM - 10:00 AM")]   # second section avoids A
    assert tb.pairwise_hard_conflict(a, b) is False


def test_pairwise_not_hard_when_async_option():
    a = [tb.parse_meeting("MW", "9:00 AM - 10:00 AM")]
    b = [tb.parse_meeting("", "")]   # async option
    assert tb.pairwise_hard_conflict(a, b) is False


# --- feasible_selection -------------------------------------------------------
def test_feasible_selection_ok():
    cts = {
        "A": [tb.parse_meeting("MW", "9:00 AM - 10:00 AM")],
        "B": [tb.parse_meeting("MW", "10:00 AM - 11:00 AM")],
    }
    feasible, conflicts = tb.feasible_selection(cts)
    assert feasible is True and conflicts == []


def test_feasible_selection_two_way_infeasible():
    cts = {
        "A": [tb.parse_meeting("MW", "9:00 AM - 10:00 AM")],
        "B": [tb.parse_meeting("MW", "9:30 AM - 10:30 AM")],
    }
    feasible, conflicts = tb.feasible_selection(cts)
    assert feasible is False
    assert set(conflicts) == {"A", "B"}


def test_feasible_selection_three_way_uses_alternatives():
    # Each course has two sections; a conflict-free combination exists only by
    # choosing the right alternates -> backtracking must find it.
    cts = {
        "A": [tb.parse_meeting("MW", "9:00 AM - 10:00 AM"),
              tb.parse_meeting("MW", "1:00 PM - 2:00 PM")],
        "B": [tb.parse_meeting("MW", "9:00 AM - 10:00 AM"),
              tb.parse_meeting("MW", "10:00 AM - 11:00 AM")],
        "C": [tb.parse_meeting("MW", "10:00 AM - 11:00 AM"),
              tb.parse_meeting("MW", "11:00 AM - 12:00 PM")],
    }
    feasible, _ = tb.feasible_selection(cts)
    assert feasible is True


def test_feasible_selection_async_course_is_free():
    cts = {
        "A": [tb.parse_meeting("MW", "9:00 AM - 10:00 AM")],
        "B": [tb.parse_meeting("MW", "9:00 AM - 10:00 AM")],   # hard-conflicts with A
        "ONLINE": [tb.parse_meeting("", "")],                  # async, irrelevant
    }
    feasible, conflicts = tb.feasible_selection(cts)
    assert feasible is False
    assert set(conflicts) == {"A", "B"}


# --- grid conformance ---------------------------------------------------------
def test_term_length_decode():
    assert tb.term_length(2248) == "16-week"   # Fall (ends 8)
    assert tb.term_length("2252") == "16-week"  # Spring (ends 2)
    assert tb.term_length(2256) == "summer"     # Summer (ends 6)
    assert tb.term_length("2251") == "winter"   # Winter (ends 1)
    assert tb.term_length(None) == "16-week"    # unknown default


def test_on_grid_matches_standard_start():
    # 8:55 AM (535) is a standard 16-week 2x/week start.
    m = tb.parse_meeting("MW", "8:55 AM - 10:20 AM")
    assert tb.on_grid(2248, m) is True


def test_off_grid_flags_nonstandard_start():
    # 9:05 AM is NOT on the 16-week grid (nearest standard is 8:55, >5 min away).
    m = tb.parse_meeting("MW", "9:05 AM - 10:30 AM")
    assert tb.on_grid(2248, m) is False


def test_on_grid_async_is_vacuously_true():
    assert tb.on_grid(2248, tb.parse_meeting("", "")) is True


def test_on_grid_unknown_grid_fails_open():
    # A custom grid dict without the term length -> never flag (fail open).
    assert tb.on_grid(2248, tb.parse_meeting("MW", "9:05 AM - 10:00 AM"),
                      grid={"summer": {480}}) is True
