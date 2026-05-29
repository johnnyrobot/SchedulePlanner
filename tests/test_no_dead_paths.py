import pathlib

# Split so this test file is not itself a self-offender when scanned.
_DEAD = "/home/" + "claude"

ACTIVE = ["engine.py", "app.py", "llm_assist.py", "generate_synthetic.py",
          "build_live_workbook.py"]


def test_no_sandbox_paths_in_active_modules():
    repo = pathlib.Path(__file__).resolve().parent.parent
    this_file = pathlib.Path(__file__).resolve()
    offenders = []
    for rel in ACTIVE:
        text = (repo / rel).read_text()
        if _DEAD in text:
            offenders.append(rel)
    for src in (repo / "sources").glob("*.py"):
        if _DEAD in src.read_text():
            offenders.append(f"sources/{src.name}")
    for src in (repo / "tests").glob("*.py"):
        if src.resolve() == this_file:
            continue  # keep the split-token trick: skip self
        if _DEAD in src.read_text():
            offenders.append(f"tests/{src.name}")
    # legacy/ reference prototypes must also be path-clean.
    for src in (repo / "legacy").glob("*.py"):
        if _DEAD in src.read_text():
            offenders.append(f"legacy/{src.name}")
    # Committed Markdown (root + docs/ recursively + legacy/) must agree with a
    # repo-wide grep, so scan docs/ subdirs (plans/, specs/, superpowers/, ...) too.
    md_files = list(repo.glob("*.md"))
    md_files += (repo / "docs").rglob("*.md")
    md_files += (repo / "legacy").glob("*.md")
    for src in md_files:
        if src.resolve() == this_file:
            continue
        if _DEAD in src.read_text():
            offenders.append(str(src.relative_to(repo)))
    assert not offenders, f"dead sandbox path in active code/docs: {offenders}"
