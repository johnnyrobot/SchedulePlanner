"""Tests for the PDF-extraction layer (sources/pdf_loader.py).

The availability gate and error paths are tested with no Java and no package
(monkeypatched), so CI never needs a JVM. The real extraction is a single
``@pytest.mark.live`` test (deselected by default) that also self-skips unless
Java + the package are actually present.
"""
import os

import pytest

from sources import catalog_ge, pdf_loader


def test_available_false_without_java(monkeypatch):
    # Neutralize BOTH Java sources: a system `java` AND any bundled/dev JRE
    # (build/jre may exist locally after a real build).
    monkeypatch.setattr(pdf_loader.shutil, "which", lambda _name: None)
    monkeypatch.setattr(pdf_loader, "_bundled_java", lambda: None)
    assert pdf_loader.java_present() is False
    assert pdf_loader.available() is False


def test_available_false_without_package(monkeypatch):
    monkeypatch.setattr(pdf_loader, "java_present", lambda: True)
    monkeypatch.setattr(pdf_loader, "package_present", lambda: False)
    assert pdf_loader.available() is False


def test_extract_missing_file_raises():
    with pytest.raises(pdf_loader.PdfLoadError):
        pdf_loader.extract("/no/such/file.pdf")


def test_extract_without_java_raises(monkeypatch, tmp_path):
    f = tmp_path / "x.pdf"
    f.write_bytes(b"%PDF-1.4\n")
    monkeypatch.setattr(pdf_loader, "java_present", lambda: False)
    with pytest.raises(pdf_loader.PdfLoadError):
        pdf_loader.extract(str(f))


def _fake_jre(tmp_path):
    """Create a fake bundled JRE layout (tmp/jre/bin/java) and return tmp."""
    jbin = tmp_path / "jre" / "bin"
    jbin.mkdir(parents=True)
    java = jbin / "java"
    java.write_text("#!/bin/sh\nexit 0\n")
    java.chmod(0o755)
    return str(tmp_path)


def test_bundled_java_detected_via_meipass(monkeypatch, tmp_path):
    base = _fake_jre(tmp_path)
    monkeypatch.setattr(pdf_loader.sys, "_MEIPASS", base, raising=False)
    monkeypatch.setattr(pdf_loader.shutil, "which", lambda _name: None)  # no system java
    monkeypatch.delenv("EDGESCHED_JRE", raising=False)
    assert pdf_loader._bundled_java() == os.path.join(base, "jre", "bin", "java")
    assert pdf_loader.java_present() is True


def test_bundled_java_env_override(monkeypatch, tmp_path):
    base = _fake_jre(tmp_path)
    monkeypatch.setenv("EDGESCHED_JRE", os.path.join(base, "jre"))
    assert pdf_loader._bundled_java() == os.path.join(base, "jre", "bin", "java")


def test_no_java_anywhere_unavailable(monkeypatch):
    monkeypatch.setattr(pdf_loader, "_bundled_java", lambda: None)
    monkeypatch.setattr(pdf_loader.shutil, "which", lambda _name: None)
    assert pdf_loader.java_present() is False
    assert pdf_loader.available() is False


def test_extract_prefers_bundled_jre_env(monkeypatch, tmp_path):
    # A bundled JRE makes extract prepend its bin to PATH + set JAVA_HOME for the
    # convert call, then restore os.environ afterward. _convert is patched so no
    # real JVM runs; it records the env it saw and writes a JSON file.
    base = _fake_jre(tmp_path)
    jre_home = os.path.join(base, "jre")
    monkeypatch.setattr(pdf_loader, "_bundled_java",
                        lambda: os.path.join(jre_home, "bin", "java"))
    pdf = tmp_path / "c.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    before_path = os.environ.get("PATH")
    seen = {}

    def fake_convert(input_path, out_dir, fmt):
        seen["JAVA_HOME"] = os.environ.get("JAVA_HOME")
        seen["PATH"] = os.environ.get("PATH")
        with open(os.path.join(out_dir, "c.json"), "w", encoding="utf-8") as fh:
            fh.write('{"kids": []}')
        return None
    monkeypatch.setattr(pdf_loader, "_convert", fake_convert)

    out = pdf_loader.extract(str(pdf))
    assert out == {"kids": []}
    assert seen["JAVA_HOME"] == jre_home
    assert seen["PATH"].startswith(os.path.join(jre_home, "bin") + os.pathsep)
    # env restored after the call
    assert os.environ.get("PATH") == before_path
    assert "JAVA_HOME" not in os.environ or os.environ.get("JAVA_HOME") != jre_home


@pytest.mark.live
def test_extract_real_pdf_roundtrip(tmp_path):
    """Real OpenDataLoader extraction (needs Java 11+ and the package)."""
    if not pdf_loader.available():
        pytest.skip("opendataloader-pdf / Java not available")
    pdf = tmp_path / "mini.pdf"
    pdf.write_bytes(_make_pdf(["General Education", "BIOL 3"]))
    odl = pdf_loader.extract(str(pdf))
    assert isinstance(odl, dict) and "kids" in odl
    # And the extractor output flows through the catalog GE parser without error.
    pattern, _area_courses, diag = catalog_ge.extract_local_ge(odl)
    assert isinstance(pattern, dict) and "areas" in pattern
    assert isinstance(diag, dict) and "section_found" in diag


def _make_pdf(lines):
    """A valid minimal one-page PDF with correct xref offsets (live smoke only)."""
    ops = b"BT /F1 18 Tf 72 720 Td 22 TL\n"
    for ln in lines:
        ops += b"(" + ln.encode("latin-1") + b") Tj T*\n"
    ops += b"ET"
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(ops), ops),
    ]
    out = b"%PDF-1.4\n"
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (i, body)
    xref_pos = len(out)
    n = len(objs) + 1
    out += b"xref\n0 %d\n0000000000 65535 f \n" % n
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (n, xref_pos)
    return out
