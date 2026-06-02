# tests/test_ge_patterns.py
import json
import pathlib

import pytest

from sources import ge

PATTERN_DIR = pathlib.Path(__file__).resolve().parents[1] / "data" / "ge_patterns"
FIX = pathlib.Path(__file__).parent / "fixtures" / "ge_pattern_test.json"


def test_load_pattern_reads_areas():
    pattern = ge.load_pattern("cal-getc", path=FIX)  # explicit path override
    assert pattern["display_name"] == "Test Pattern"
    assert {a["code"] for a in pattern["areas"]} == {"1A", "3A", "4", "5"}


def test_load_pattern_unknown_goal_raises():
    with pytest.raises(ge.PatternError):
        ge.load_pattern("nonexistent-goal")


@pytest.mark.parametrize("pfile", sorted(PATTERN_DIR.glob("*.json")))
def test_shipped_patterns_are_well_formed(pfile):
    data = json.loads(pfile.read_text())
    assert data.get("source"), f"{pfile.name} missing source"
    assert "reviewed_by" in data, f"{pfile.name} missing reviewed_by"
    assert data["areas"], f"{pfile.name} has no areas"
    for area in data["areas"]:
        assert area.get("code"), f"{pfile.name} area missing code"
        assert isinstance(area.get("count"), int) and area["count"] >= 1, \
            f"{pfile.name} area {area.get('code')} bad count"
