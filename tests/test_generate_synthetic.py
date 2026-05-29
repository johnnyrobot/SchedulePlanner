import pathlib
import subprocess
import sys

import pandas as pd

REPO = pathlib.Path(__file__).resolve().parent.parent


def test_generator_emits_valid_three_sheet_workbook(tmp_path):
    import engine

    out = tmp_path / "regen.xlsx"
    proc = subprocess.run(
        [sys.executable, "generate_synthetic.py", "--out", str(out)],
        cwd=REPO, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert out.exists()
    assert set(pd.ExcelFile(out).sheet_names) == {"sections", "catalog", "programs"}
    res = engine.run(str(out))
    assert res["terms_in_data"] == 8  # generate_synthetic TERMS has 8 entries
    for detector in ("rotation_gaps", "single_section", "modality_mismatch", "under_supply"):
        assert res["analysis"][detector], f"{detector} should be non-empty"
