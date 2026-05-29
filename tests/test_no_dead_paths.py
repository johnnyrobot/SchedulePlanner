import pathlib

ACTIVE = ["engine.py", "app.py", "llm_assist.py", "generate_synthetic.py",
          "build_live_workbook.py"]


def test_no_sandbox_paths_in_active_modules():
    repo = pathlib.Path(__file__).resolve().parent.parent
    offenders = []
    for rel in ACTIVE:
        text = (repo / rel).read_text()
        if "/home/claude" in text:
            offenders.append(rel)
    for src in (repo / "sources").glob("*.py"):
        if "/home/claude" in src.read_text():
            offenders.append(f"sources/{src.name}")
    assert not offenders, f"dead sandbox path in active code: {offenders}"
