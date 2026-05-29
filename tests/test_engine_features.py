"""Regression tests for engine behavior against the bundled synthetic dataset (PRD §6 FRs + N11)."""
import json
import os
import pathlib
import subprocess
import sys

import engine

DEMO = str(pathlib.Path(__file__).resolve().parent.parent / "files" / "lamc_data.xlsx")


def test_engine_run_is_deterministic():
    """PRD N11: identical inputs -> byte-identical results."""
    r1 = engine.run(DEMO)
    r2 = engine.run(DEMO)
    assert json.dumps(r1, sort_keys=True) == json.dumps(r2, sort_keys=True)


def test_default_data_path_exists():
    p = engine._default_data_path()
    assert p.endswith(os.path.join("files", "lamc_data.xlsx"))
    assert os.path.exists(p)


def test_engine_cli_no_args_runs(tmp_path):
    """`python3 engine.py` (no args) must run on the bundled demo, not a dead path."""
    repo = pathlib.Path(__file__).resolve().parent.parent
    out = subprocess.run([sys.executable, "engine.py"], cwd=repo,
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    data = json.loads(out.stdout)
    assert data["terms_in_data"] == 8


def test_llm_assist_cli_no_args_runs():
    """`python3 llm_assist.py` (no args, no Ollama) must run via fallback and exit 0."""
    repo = pathlib.Path(__file__).resolve().parent.parent
    out = subprocess.run([sys.executable, "llm_assist.py"], cwd=repo,
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip()  # non-empty briefing output
