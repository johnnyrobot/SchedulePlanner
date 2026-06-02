"""
pdf_loader.py — the app's PDF-extraction layer (OpenDataLoader PDF, Apache-2.0).

Wraps the `opendataloader-pdf` package, whose default Fast mode runs a local,
deterministic Java extractor (no GPU/torch/server) and emits semantic JSON
(``kids`` of headings / paragraphs / tables / lists in reading order). It spawns
a JVM per call, so this is the ONLY module that needs Java 11+. The PDF feature
is opt-in and gated on ``available()`` — mirroring the optional Ollama/Gemma layer
in llm_assist.py — so an absent JDK or package degrades to an honest message
instead of crashing, and normal/CI runs never touch Java.
"""
from __future__ import annotations
import glob
import json
import os
import shutil
import tempfile

ADOPTIUM_URL = "https://adoptium.net/"


class PdfLoadError(RuntimeError):
    """Raised when a PDF cannot be extracted (no Java/package, bad PDF, no output)."""


def java_present() -> bool:
    return shutil.which("java") is not None


def package_present() -> bool:
    try:
        import opendataloader_pdf  # noqa: F401
        return True
    except Exception:
        return False


def available() -> bool:
    """True only when BOTH the Java runtime and the wrapper are usable."""
    return java_present() and package_present()


def extract(pdf_path: str) -> dict:
    """Extract ``pdf_path`` to OpenDataLoader semantic JSON (Fast mode, local).

    Returns the parsed JSON dict. Raises PdfLoadError on any failure (missing
    Java/package, unreadable PDF, or no JSON produced) so callers degrade to a
    readable message instead of a raw traceback. Spawns a JVM — gate on
    ``available()`` before calling.
    """
    if not pdf_path or not os.path.exists(pdf_path):
        raise PdfLoadError(f"PDF not found: {pdf_path!r}")
    if not java_present():
        raise PdfLoadError(f"Java 11+ is required (install from {ADOPTIUM_URL}).")
    try:
        import opendataloader_pdf
    except Exception as e:  # noqa: BLE001 - any import failure -> readable message
        raise PdfLoadError(f"opendataloader-pdf is not installed: {e}") from e
    with tempfile.TemporaryDirectory() as out_dir:
        try:
            result = opendataloader_pdf.convert(
                input_path=[pdf_path], output_dir=out_dir, format="json")
        except Exception as e:  # noqa: BLE001
            raise PdfLoadError(f"PDF extraction failed: {type(e).__name__}: {e}") from e
        # Documented contract: convert writes <name>.json into output_dir. Some
        # wrapper versions also return the content directly — accept either.
        files = sorted(glob.glob(os.path.join(out_dir, "**", "*.json"), recursive=True))
        if files:
            try:
                with open(files[0], encoding="utf-8") as fh:
                    return json.load(fh)
            except Exception as e:  # noqa: BLE001
                raise PdfLoadError(f"could not read extractor output: {e}") from e
        if isinstance(result, dict):
            return result
        if isinstance(result, str) and result.strip():
            try:
                return json.loads(result)
            except Exception as e:  # noqa: BLE001
                raise PdfLoadError(f"extractor returned non-JSON output: {e}") from e
        raise PdfLoadError("PDF extraction produced no JSON output.")
