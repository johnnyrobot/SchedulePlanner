"""Supply-chain artifact-pinning regression guards (ship-review, Tests pillar).

The bundled Temurin JRE is the only externally-downloaded runtime artifact, so it
must be fetched against a PINNED sha256 and the download verified before staging —
otherwise a compromised mirror could inject a different JRE into the build. These
guard that the build-time integrity check in scripts/fetch_jre.sh is never silently
removed. (GPG-signature verification of the release asset remains a deeper
follow-up the 4-pillar review flags.)
"""
import pathlib
import re

JRE = (pathlib.Path(__file__).parent.parent / "scripts" / "fetch_jre.sh").read_text(
    encoding="utf-8")


def test_fetch_jre_pins_a_sha256_per_arch():
    for var in ("SHA256_aarch64", "SHA256_x64"):
        assert re.search(rf'{var}="[0-9a-f]{{64}}"', JRE), \
            f"{var} must pin a 64-hex sha256 in fetch_jre.sh"


def test_fetch_jre_verifies_the_download_and_aborts_on_mismatch():
    assert "shasum -a 256" in JRE, "fetch_jre.sh must compute the download's sha256"
    assert re.search(r'"\$\{ACTUAL\}"\s*!=\s*"\$\{SHA256\}"', JRE), \
        "fetch_jre.sh must compare the actual checksum to the pinned one"
    assert "checksum mismatch" in JRE
    # the mismatch branch aborts the build (no silent install of an unverified tarball)
    after_mismatch = JRE.split('!= "${SHA256}"', 1)[1][:200]
    assert "exit 1" in after_mismatch


def test_fetch_jre_records_a_checksum_marker_for_idempotent_reverify():
    assert "MARKER=" in JRE
    assert re.search(r'\$\(cat "\$\{MARKER\}"\)"\s*=\s*"\$\{SHA256\}"', JRE), \
        "a staged JRE must be re-verified against its recorded checksum marker"
