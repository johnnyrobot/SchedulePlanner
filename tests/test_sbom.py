"""E16 — CycloneDX SBOM + no-copyleft license gate tests.

The license-classification + gate logic is pure and unit-tested here; an
integration test runs the generator over the real requirements.lock and asserts
the SBOM is valid CycloneDX and the gate PASSES (the lock is copyleft-clean).
"""
import importlib.util
import json
import pathlib

_SPEC = importlib.util.spec_from_file_location(
    "generate_sbom",
    pathlib.Path(__file__).parent.parent / "scripts" / "generate_sbom.py")
sbom = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(sbom)

LOCK = pathlib.Path(__file__).parent.parent / "requirements.lock"


# --------------------------------------------------------- license classification
def test_strong_copyleft_is_flagged():
    assert sbom.is_copyleft("GNU General Public License v2 (GPLv2)")
    assert sbom.is_copyleft("GPL-3.0")
    assert sbom.is_copyleft("GNU Affero General Public License v3")
    assert sbom.is_copyleft("AGPL-3.0")


def test_weak_copyleft_and_permissive_are_allowed():
    assert not sbom.is_copyleft("GNU Lesser General Public License v3 (LGPLv3)")
    assert not sbom.is_copyleft("LGPL-2.1")
    assert not sbom.is_copyleft("MIT License")
    assert not sbom.is_copyleft("BSD License")
    assert not sbom.is_copyleft("Apache Software License")
    assert not sbom.is_copyleft("")


def test_gpl_with_classpath_exception_is_not_strong_copyleft():
    # The bundled Temurin JRE carries this; it must not trip the bare-GPL match.
    assert not sbom.is_copyleft("GPL-2.0-with-classpath-exception")


# ------------------------------------------------------------------ lock parsing
def test_parse_lock_reads_name_version_only():
    pkgs = dict(sbom.parse_lock(LOCK))
    assert "ortools" in pkgs and "httpx" in pkgs
    # ignores --hash / comment lines and dedupes
    assert all(v and "--hash" not in v for v in pkgs.values())
    assert len(pkgs) >= 30


# --------------------------------------------------------------- the gate logic
def test_gate_flags_a_copyleft_component_but_not_an_exempt_one():
    comps = [
        {"name": "clean", "license": "MIT", "gate_exempt": False},
        {"name": "bad", "license": "AGPL-3.0", "gate_exempt": False},
        {"name": "temurin", "license": "GPL-2.0-with-classpath-exception",
         "gate_exempt": True},
    ]
    offenders = sbom.copyleft_offenders(comps)
    assert [c["name"] for c in offenders] == ["bad"]   # exempt JRE never flagged


def test_bundled_externals_are_carried_with_disclosed_licenses():
    # No silent drops: the JRE + CPython runtime appear in the component set, the
    # JRE keeps its real GPL+classpath license AND is marked exempt with a reason.
    comps = sbom.build_components(LOCK)
    jre = next(c for c in comps if "Temurin" in c["name"])
    assert jre["license"] == "GPL-2.0-with-classpath-exception"
    assert jre["gate_exempt"] is True and jre.get("exempt_reason")
    assert any("CPython" in c["name"] for c in comps)


# ------------------------------------------------------- CycloneDX structure
def test_build_sbom_is_valid_cyclonedx_shape():
    doc = sbom.build_sbom(sbom.build_components(LOCK))
    assert doc["bomFormat"] == "CycloneDX" and doc["specVersion"] == "1.5"
    assert doc["metadata"]["component"]["name"] == "SchedulePlanner"
    for c in doc["components"]:
        assert c["name"] and c["licenses"]                  # every component licensed
        assert c["licenses"][0]["license"].get("name")
    # the JRE component discloses its exemption in properties (no silent drop)
    jre = next(c for c in doc["components"] if "Temurin" in c["name"])
    props = " ".join(p["value"] for p in jre.get("properties", []))
    assert "exempt" in props.lower() and "classpath" in props.lower()


def test_sbom_is_deterministic():
    a = json.dumps(sbom.build_sbom(sbom.build_components(LOCK)), sort_keys=True)
    b = json.dumps(sbom.build_sbom(sbom.build_components(LOCK)), sort_keys=True)
    assert a == b


# ------------------------------------------------------------------ integration
def test_real_lock_passes_the_gate(tmp_path):
    # The shipped lock must be copyleft-clean (PyMuPDF/pyinstaller are NOT in it).
    rc = sbom.main(["--lock", str(LOCK), "--out", str(tmp_path / "sbom.json")])
    assert rc == 0
    doc = json.loads((tmp_path / "sbom.json").read_text())
    assert doc["bomFormat"] == "CycloneDX"
