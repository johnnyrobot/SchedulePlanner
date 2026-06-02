"""Tests for the PDF-extraction layer (sources/pdf_loader.py).

The availability gate and error paths are tested with no Java and no package
(monkeypatched), so CI never needs a JVM. The real extraction is a single
``@pytest.mark.live`` test (deselected by default) that also self-skips unless
Java + the package are actually present.
"""
import pytest

from sources import catalog_ge, pdf_loader

FIXED_PDF_NEEDLE = "General Education"


def test_available_false_without_java(monkeypatch):
    monkeypatch.setattr(pdf_loader.shutil, "which", lambda _name: None)
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
