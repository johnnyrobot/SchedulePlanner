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


# --- unverifiable-license gate (ship-review fast-follow: Licenses bypass) ----------
# license_of() returns "" for a package NOT installed on the gate runner
# (platform-conditional deps: clr-loader/colorama/pythonnet/qtpy/tzdata don't
# install on macOS CI). Before the fix that became license "UNKNOWN", which
# is_copyleft() treats as not-copyleft -> a GPL win32-only package would SILENTLY
# PASS the gate. The gate must instead resolve such licenses from a human-vetted
# map and FAIL on any license it still cannot verify.
def test_unverified_unknown_license_cannot_silently_pass():
    # A copyleft platform-conditional package fails by ONE of two paths, never slips:
    gpl = {"name": "x", "version": "1", "license": "GPL-3.0", "gate_exempt": False}
    assert gpl in sbom.copyleft_offenders([gpl])              # (a) vetted GPL -> offender
    unk = {"name": "y", "version": "1", "license": "UNKNOWN", "gate_exempt": False}
    assert unk in sbom.unverified_components([unk])           # (b) unverifiable -> flagged
    # a resolved-permissive or gate-exempt component is neither
    ok = {"name": "z", "version": "1", "license": "MIT", "gate_exempt": False}
    exempt = {"name": "w", "version": "1", "license": "UNKNOWN", "gate_exempt": True}
    assert sbom.unverified_components([ok, exempt]) == []


def test_unresolvable_license_fails_the_gate_via_main(tmp_path, monkeypatch):
    # A lock package whose license cannot be resolved (not installed, not vetted)
    # must FAIL the gate (exit 2), not pass silently as UNKNOWN.
    monkeypatch.setattr(sbom, "license_of", lambda name: "")
    lock = tmp_path / "x.lock"
    lock.write_text("totally-unknown-pkg==1.0.0\n")
    rc = sbom.main(["--lock", str(lock), "--out", str(tmp_path / "s.json")])
    assert rc == 2


def test_platform_conditional_license_resolved_from_vetted_map(tmp_path, monkeypatch):
    # When a platform-conditional package is not installed (license_of -> ""), its
    # license comes from the vetted map, NOT a silent UNKNOWN, so the gate evaluates
    # its REAL license (and would catch it if it were copyleft).
    monkeypatch.setattr(sbom, "license_of", lambda name: "")
    lock = tmp_path / "pc.lock"
    lock.write_text("pythonnet==3.1.0\n")
    comp = next(c for c in sbom.build_components(str(lock)) if c["name"] == "pythonnet")
    assert comp["license"] != "UNKNOWN"
    assert not sbom.is_copyleft(comp["license"])


def test_real_lock_has_no_unverifiable_license():
    # Every shipped component's license is resolved (installed metadata OR the vetted
    # fallback map OR gate-exempt) — no UNKNOWN ships ungated.
    assert sbom.unverified_components(sbom.build_components(str(LOCK))) == []


def test_metadata_less_packages_resolve_from_the_vetted_map(monkeypatch):
    # Regression for the CI failure: cffi/pycparser expose NO readable license
    # metadata on some runners (their license is resolvable locally, so the bug only
    # showed on CI). Simulate that — they must resolve from VETTED_LICENSE_FALLBACKS,
    # not fail the gate as UNKNOWN.
    real = sbom.license_of
    monkeypatch.setattr(
        sbom, "license_of",
        lambda n: "" if n.lower().replace("_", "-") in ("cffi", "pycparser") else real(n))
    comps = sbom.build_components(str(LOCK))
    assert sbom.unverified_components(comps) == []
    for name in ("cffi", "pycparser"):
        c = next((x for x in comps if x["name"].lower().replace("_", "-") == name), None)
        if c is not None:                       # only assert if the lock carries it
            assert c["license"] != "UNKNOWN" and not sbom.is_copyleft(c["license"])
