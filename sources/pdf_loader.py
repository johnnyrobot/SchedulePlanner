"""
pdf_loader.py — the app's PDF-extraction layer (OpenDataLoader PDF, Apache-2.0).

Wraps the `opendataloader-pdf` package, whose default Fast mode runs a local,
deterministic Java extractor (no GPU/torch/server) and emits semantic JSON
(``kids`` of headings / paragraphs / tables / lists in reading order). It spawns
a JVM per call, so this is the ONLY module that needs Java.

Java resolution: the shipped macOS .app **bundles a JRE** (under
``Contents/Resources/jre``), so the catalog feature is zero-setup — no user Java
install. When a bundled JRE is found, ``extract`` prepends its ``bin`` to ``PATH``
(and sets ``JAVA_HOME``) around the OpenDataLoader call; the wrapper's runner
spawns ``["java", …]`` inheriting ``os.environ``, so it picks up the bundled
runtime with no wrapper patching. In a dev / unbundled run the code falls back to
system Java; if neither is present the feature degrades to an honest message
(gated via ``available()``, like the optional Ollama AI layer).
"""
from __future__ import annotations
import glob
import json
import os
import shutil
import sys
import tempfile

ADOPTIUM_URL = "https://adoptium.net/"
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class PdfLoadError(RuntimeError):
    """Raised when a PDF cannot be extracted (no Java/package, bad PDF, no output)."""


def _bundled_java():
    """Path to a bundled ``jre/bin/java`` if one ships with this build, else None.

    Candidates, first existing wins: an ``EDGESCHED_JRE`` override (tests / manual),
    the PyInstaller unpack dir (``sys._MEIPASS/jre``), the macOS app bundle
    (``…/Contents/Resources/jre`` derived from ``sys.executable``), and a dev
    ``build/jre`` in the repo.
    """
    candidates = []
    env = os.environ.get("EDGESCHED_JRE")
    if env:
        candidates.append(os.path.join(env, "bin", "java"))
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(os.path.join(meipass, "jre", "bin", "java"))
    exe = getattr(sys, "executable", "") or ""
    if exe:
        # macOS .app: …/Contents/MacOS/<exe> -> …/Contents/Resources/jre/bin/java
        contents = os.path.dirname(os.path.dirname(os.path.abspath(exe)))
        candidates.append(os.path.join(contents, "Resources", "jre", "bin", "java"))
    candidates.append(os.path.join(_REPO_ROOT, "build", "jre", "bin", "java"))
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return None


def java_present() -> bool:
    """A bundled JRE OR a system `java` on PATH."""
    return bool(_bundled_java()) or shutil.which("java") is not None


def package_present() -> bool:
    try:
        import opendataloader_pdf  # noqa: F401
        return True
    except Exception:
        return False


def available() -> bool:
    """True only when BOTH a Java runtime (bundled or system) and the wrapper exist."""
    return java_present() and package_present()


def _convert(input_path, out_dir, fmt):
    """Thin seam over opendataloader_pdf.convert (patched in tests)."""
    import opendataloader_pdf
    return opendataloader_pdf.convert(input_path=input_path, output_dir=out_dir, format=fmt)


def _run_convert(input_path, out_dir, bundled_java):
    """Run the convert, preferring the bundled JRE via a temporary env tweak.

    The wrapper spawns ``["java", …]`` inheriting ``os.environ``, so we prepend the
    bundled ``jre/bin`` to PATH (+ set JAVA_HOME) for the call and ALWAYS restore
    the environment afterward. Any failure becomes PdfLoadError.
    """
    saved = None
    if bundled_java:
        jre_home = os.path.dirname(os.path.dirname(bundled_java))   # …/jre
        saved = {k: os.environ.get(k) for k in ("PATH", "JAVA_HOME")}
        os.environ["JAVA_HOME"] = jre_home
        os.environ["PATH"] = (os.path.dirname(bundled_java) + os.pathsep
                              + os.environ.get("PATH", ""))
    try:
        return _convert(input_path, out_dir, "json")
    except Exception as e:  # noqa: BLE001 - any failure -> readable message
        raise PdfLoadError(f"PDF extraction failed: {type(e).__name__}: {e}") from e
    finally:
        if saved is not None:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


def extract(pdf_path: str) -> dict:
    """Extract ``pdf_path`` to OpenDataLoader semantic JSON (Fast mode, local).

    Returns the parsed JSON dict. Raises PdfLoadError on any failure (no Java,
    unreadable PDF, or no JSON produced) so callers degrade to a readable message
    instead of a raw traceback. Prefers a bundled JRE; falls back to system Java.
    Spawns a JVM — gate on ``available()`` before calling.
    """
    if not pdf_path or not os.path.exists(pdf_path):
        raise PdfLoadError(f"PDF not found: {pdf_path!r}")
    if not java_present():
        raise PdfLoadError(f"Java 11+ is required (install from {ADOPTIUM_URL}).")
    bundled = _bundled_java()
    with tempfile.TemporaryDirectory() as out_dir:
        result = _run_convert([pdf_path], out_dir, bundled)
        # Documented contract: convert writes <name>.json into out_dir. Some
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
