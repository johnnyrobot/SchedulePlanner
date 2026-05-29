"""
app.py — native desktop entry point (pywebview).

Wraps the headless engine + Gemma 4 layer in a native window. The user picks a
data file; the OR-Tools solver produces the schedule; Gemma 4 (E2B, via Ollama)
optionally parses messy prerequisites and writes the admin explanation.

Run (dev):   python app.py
Package:     see BUILD.md  (PyInstaller -> single binary)
"""
import os
import sys
import tempfile
import threading
import webview

import build_live_workbook
import engine
import llm_assist

HERE = os.path.dirname(os.path.abspath(__file__))


def resource_path(*parts):
    """Resolve a bundled resource for both dev and PyInstaller-frozen runs.

    When frozen, PyInstaller unpacks data files under sys._MEIPASS; in dev we
    fall back to this file's own directory. Use for any file shipped with the
    app (ui.html, files/lamc_data.xlsx) so paths work in both modes.
    """
    base = getattr(sys, "_MEIPASS", HERE)
    return os.path.join(base, *parts)


class Api:
    def __init__(self):
        self._last_results = None

    # ---- file picking -------------------------------------------------
    def choose_file(self):
        result = webview.windows[0].create_file_dialog(
            webview.OPEN_DIALOG,
            file_types=("Data files (*.xlsx;*.xls)", "All files (*.*)"))
        if not result:
            return {"path": ""}
        return {"path": result[0]}

    # ---- bundled demo data -------------------------------------------
    def _demo_path(self):
        """Absolute path to the bundled synthetic demo workbook.

        Underscore-prefixed so pywebview does not expose it to JS (it is an
        internal helper; JS only needs load_demo). Resolved relative to the app
        (sys._MEIPASS-aware) so it works in dev and when frozen — the user
        never has to hunt for a file.
        """
        return resource_path("files", "lamc_data.xlsx")

    def load_demo(self):
        """Run the analysis on the bundled demo workbook.

        Goes through the exact same code path as analyzing a user-picked file
        so the one-click demo and a normal analyze are identical.
        """
        return self.analyze(self._demo_path())

    # ---- AI status / setup -------------------------------------------
    def ai_status(self):
        return {
            "installed": llm_assist.ollama_installed(),
            "running": llm_assist.ollama_running(),
            "model": llm_assist.model_present(),
            "model_name": llm_assist.MODEL,
        }

    def setup_ai(self):
        """Pull the Gemma 4 model. Long-running; UI shows a spinner."""
        ok = llm_assist.ensure_model(progress=lambda s: None)
        return {"ok": bool(ok)}

    # ---- core ---------------------------------------------------------
    def analyze(self, path):
        if not path or not os.path.exists(path):
            return {"error": "File not found."}
        try:
            parser = llm_assist.make_prereq_parser()      # None if no Gemma 4
            results = engine.run(path, llm=parser)
            self._last_results = results
            results["ai_used"] = parser is not None
            return results
        except Exception as e:
            return {"error": f"{type(e).__name__}: {e}"}

    # ---- live LACCD data ---------------------------------------------
    def fetch_live(self, campus, terms, program, client=None):
        """Pull live LACCD data and analyze it, entirely inside the app.

        Parses the comma-separated `terms` string into ints, runs the live
        pipeline via build_live_workbook.analyze_live (Program Mapper +
        schedule API -> workbook -> engine), and returns a flat dict the UI
        can hand straight to showResult(): the engine `results` (terms_in_data
        / analysis / programs) plus the live-only `reconciliation` and
        `inert_detectors` fields.

        The optional `client` is passed through so tests can inject a
        FakeClient (replaying committed fixtures, no network). On a no-match
        program, any SourceError, or any other failure, returns
        {'error': <clear message>} so the UI shows a readable card instead of
        freezing or silently failing.
        """
        parsed_terms = [int(t) for t in str(terms).split(",")
                        if t.strip().lstrip("-").isdigit()]
        if not parsed_terms:
            return {"error": (f"No valid term codes in {terms!r}. Enter one or "
                              "more numeric term codes, e.g. 2264,2266,2268.")}
        try:
            # analyze_live writes a workbook as a side effect; route it to a
            # throwaway temp file so nothing leaks into the user's workspace.
            with tempfile.TemporaryDirectory() as tmp:
                out_path = os.path.join(tmp, "live_workbook.xlsx")
                report = build_live_workbook.analyze_live(
                    campus, parsed_terms, program, out_path, client=client)
        except Exception as e:
            return {"error": (f"Could not fetch live LACCD data: "
                              f"{type(e).__name__}: {e}")}

        if report.get("error"):
            # analyze_live already returns the "No program matched ..." guidance.
            return {"error": report["error"]}

        results = report.get("results")
        if not isinstance(results, dict):
            return {"error": "Live fetch produced no analysis results."}

        # Flatten so the existing showResult()/render() path renders the engine
        # results, then attach the live-only panels.
        out = dict(results)
        out["reconciliation"] = report.get("reconciliation")
        out["inert_detectors"] = report.get("inert_detectors")
        out["campus"] = report.get("campus")
        out["live_terms"] = report.get("terms")
        out["program_info"] = report.get("program")
        # Live data has no prerequisite/LLM parse step; mark accordingly so the
        # status line does not falsely claim a Gemma 4 parse.
        out["ai_used"] = False
        self._last_results = out
        return out

    def explain(self):
        if not self._last_results:
            return {"text": "Run an analysis first."}
        return {"text": llm_assist.explain(self._last_results)}


def main():
    api = Api()
    webview.create_window(
        "LAMC Schedule Planner",
        resource_path("ui.html"),
        js_api=api,
        width=1100, height=820, min_size=(900, 640),
    )
    webview.start()


if __name__ == "__main__":
    main()
