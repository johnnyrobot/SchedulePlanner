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
