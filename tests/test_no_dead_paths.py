import pathlib

# Split so this test file is not itself a self-offender when scanned.
_DEAD = "/home/" + "claude"

ACTIVE = ["engine.py", "app.py", "llm_assist.py", "generate_synthetic.py",
          "build_live_workbook.py"]


def test_no_sandbox_paths_in_active_modules():
    repo = pathlib.Path(__file__).resolve().parent.parent
    offenders = []
    for rel in ACTIVE:
        text = (repo / rel).read_text()
        if _DEAD in text:
            offenders.append(rel)
    for src in (repo / "sources").glob("*.py"):
        if _DEAD in src.read_text():
            offenders.append(f"sources/{src.name}")
    for src in (repo / "tests").glob("*.py"):
        if _DEAD in src.read_text():
            offenders.append(f"tests/{src.name}")
    assert not offenders, f"dead sandbox path in active code: {offenders}"
