"""Tests for the grid-conformance + morning-compression analyzer (grid_pressure.py).

Pure / deterministic — no network, no solver, no files. Sections are raw dicts
shaped like the live / import pipeline's (course / term / days / times). Times use
minutes-since-midnight buckets: 9:00 AM = 540, 1:00 PM = 780, 5:00 PM = 1020.
"""
import grid_pressure as G


def sec(course, *, term=2268, days="MW", times="9:00 AM - 10:15 AM"):
    return {"course": course, "term": term, "days": days, "times": times}


def async_sec(course, *, term=2268):
    return {"course": course, "term": term, "days": "", "times": ""}


# ----------------------------------------------------------------- buckets / dedup
def test_bucket_boundaries():
    assert G._bucket(539) == "early"        # 8:59 AM
    assert G._bucket(540) == "prime"        # 9:00 AM
    assert G._bucket(779) == "prime"        # 12:59 PM
    assert G._bucket(780) == "afternoon"    # 1:00 PM
    assert G._bucket(1019) == "afternoon"
    assert G._bucket(1020) == "evening"     # 5:00 PM


def test_timed_dedup_and_excludes_async():
    secs = [sec("CS 1"), sec("CS 1"),                      # identical -> 1 row
            sec("CS 1", days="F"),                          # distinct meeting pattern
            async_sec("CS 1")]                              # async/TBA -> excluded
    timed = G._timed(secs)
    assert len(timed) == 2
    assert all(t["course"] == "CS 1" for t in timed)


# ----------------------------------------------------------------- conformance
def test_conformance_rate_with_injected_grid():
    grid = {"16-week": {540}}                               # only 9:00 AM on-grid
    secs = [sec("A", times="9:00 AM - 10:15 AM"),           # 540 -> on-grid
            sec("B", times="9:06 AM - 10:21 AM")]           # 546 -> off (>5 min)
    conf = G.conformance(secs, grid=grid)
    assert conf["evaluated"] == 2
    assert conf["on_grid"] == 1
    assert conf["off_grid"] == 1
    assert conf["on_grid_rate"] == 0.5
    assert conf["off_grid_sample"][0]["course"] == "B"


# ----------------------------------------------------------------- compression
def test_compression_buckets_and_prime_share():
    secs = [sec("A", times="9:00 AM - 10:15 AM"),           # prime
            sec("B", times="11:00 AM - 12:15 PM"),          # prime
            sec("C", times="2:00 PM - 3:15 PM"),            # afternoon
            sec("D", times="6:00 PM - 7:15 PM")]            # evening
    comp = G.compression(secs)
    assert comp["buckets"] == {"early": 0, "prime": 2, "afternoon": 1, "evening": 1}
    assert comp["total_timed"] == 4
    assert comp["prime_share"] == 0.5


# ----------------------------------------------------------------- morning-locked
def test_morning_locked_excludes_nonprime_and_async():
    secs = [
        sec("LOCK", times="9:00 AM - 10:15 AM"),                  # prime
        sec("LOCK", days="TR", times="11:00 AM - 12:15 PM"),     # also prime
        sec("ESCAPE", times="9:00 AM - 10:15 AM"),               # prime
        sec("ESCAPE", days="TR", times="2:00 PM - 3:15 PM"),     # afternoon -> not locked
        sec("ASYNCY", times="9:00 AM - 10:15 AM"),               # prime
        async_sec("ASYNCY"),                                     # async escape -> not locked
    ]
    locked = G.morning_locked_courses(secs)
    assert set(locked) == {"LOCK"}
    assert locked["LOCK"]["n_sections"] == 2


# ----------------------------------------------------------------- mutual exclusions
def test_mutual_exclusions_finds_overlapping_locked_pairs():
    secs = [sec("A", days="MW", times="9:00 AM - 10:15 AM"),    # 540-615
            sec("B", days="MW", times="9:30 AM - 10:45 AM"),    # 570-645 overlaps A
            sec("C", days="MW", times="11:00 AM - 12:15 PM")]   # 660-735 no overlap
    locked = G.morning_locked_courses(secs)
    pairs, trunc = G.mutual_exclusions(secs, locked)
    pc = {tuple(p["courses"]) for p in pairs}
    assert ("A", "B") in pc
    assert ("A", "C") not in pc and ("B", "C") not in pc
    assert trunc["pairs"] == 0


def test_mutual_exclusions_truncates_pairs():
    secs = [sec(f"CRS {i}", days="MW", times="9:00 AM - 10:15 AM") for i in range(6)]
    locked = G.morning_locked_courses(secs)                      # 6 courses, all overlap
    pairs, trunc = G.mutual_exclusions(secs, locked, top=10)     # C(6,2)=15 pairs
    assert len(pairs) == 10
    assert trunc["pairs"] == 5


# ----------------------------------------------------------------- report envelope
def test_report_inert_when_no_timed_sections():
    rep = G.grid_pressure_report([async_sec("X")])
    assert rep["status"] == "inert"
    assert "PROXY" in rep["label"] and rep.get("reason")


def test_report_active_envelope_has_not_assessed_and_caveat():
    secs = [sec("A", days="MW", times="9:00 AM - 10:15 AM"),
            sec("B", days="MW", times="9:30 AM - 10:45 AM")]
    rep = G.grid_pressure_report(secs, grid={"16-week": {540}})
    assert rep["status"] == "active"
    assert "PROXY" in rep["label"]
    assert rep["morning_compression"]["buckets"]["prime"] == 2
    assert rep["not_assessed"]["end_time_duration"]["status"] == "inert"
    assert rep["not_assessed"]["holidays_session_dates"]["status"] == "inert"
    assert "feasibility is not verified" in rep["what_if_caveat"]
    assert {tuple(p["courses"]) for p in rep["mutual_exclusions"]} == {("A", "B")}


def test_report_relevant_scoping_limits_pairs_to_program():
    secs = [sec("A", days="MW", times="9:00 AM - 10:15 AM"),
            sec("B", days="MW", times="9:30 AM - 10:45 AM"),
            sec("Z", days="MW", times="9:15 AM - 10:30 AM")]
    program = {"courses": [{"course_id": "A"}, {"course_id": "B"}]}   # Z not required
    rep = G.grid_pressure_report(secs, program=program)
    pc = {tuple(p["courses"]) for p in rep["mutual_exclusions"]}
    assert ("A", "B") in pc
    assert all("Z" not in p for p in pc)


def test_report_is_deterministic():
    secs = [sec("B", days="MW", times="9:30 AM - 10:45 AM"),
            sec("A", days="MW", times="9:00 AM - 10:15 AM")]
    assert G.grid_pressure_report(secs) == G.grid_pressure_report(secs)


def test_timed_unions_secondary_meeting_blocks():
    """A section meeting on two patterns contributes ALL its blocks to its meeting
    (M1), so conformance / hard-conflict see the secondary block, not just block[0]."""
    r = {"course": "BIO 3", "term": 2268, "days": "M", "times": "8:50 AM - 10:00 AM",
         "meetings": [{"days": "M", "times": "8:50 AM - 10:00 AM"},
                      {"days": "W", "times": "8:50 AM - 10:00 AM"}]}
    timed = G._timed([r])
    assert len(timed) == 1
    assert {b[0] for b in timed[0]["meeting"]} == {"M", "W"}


def test_async_courses_excludes_section_with_timed_secondary_block():
    """A section async on its first block but TIMED on a secondary block is not async
    (it has a real meeting) — the old meetings[0]-only logic mislabeled it."""
    r = {"course": "BIO 3", "days": "", "times": "",
         "meetings": [{"days": "", "times": ""},
                      {"days": "W", "times": "8:50 AM - 10:00 AM"}]}
    assert "BIO 3" not in G._async_courses([r])
