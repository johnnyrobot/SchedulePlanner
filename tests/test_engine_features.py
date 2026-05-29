import json

import engine

DEMO = "files/lamc_data.xlsx"


def test_engine_run_is_deterministic():
    """PRD N11: identical inputs -> byte-identical results."""
    r1 = engine.run(DEMO)
    r2 = engine.run(DEMO)
    assert json.dumps(r1, sort_keys=True) == json.dumps(r2, sort_keys=True)
