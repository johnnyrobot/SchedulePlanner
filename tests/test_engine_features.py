"""Regression tests for engine behavior against the bundled synthetic dataset (PRD §6 FRs + N11)."""
import json
import pathlib

import engine

DEMO = str(pathlib.Path(__file__).resolve().parent.parent / "files" / "lamc_data.xlsx")


def test_engine_run_is_deterministic():
    """PRD N11: identical inputs -> byte-identical results."""
    r1 = engine.run(DEMO)
    r2 = engine.run(DEMO)
    assert json.dumps(r1, sort_keys=True) == json.dumps(r2, sort_keys=True)
