"""
app.py — native desktop entry point (pywebview).

Wraps the headless engine + Gemma 4 layer in a native window. The user picks a
data file; the OR-Tools solver produces the schedule; Gemma 4 (E2B, via Ollama)
optionally parses messy prerequisites and writes the admin explanation.

Run (dev):   python app.py
Package:     see BUILD.md  (PyInstaller -> single binary)
"""
import datetime
import os
import sys
import tempfile
import threading
import webview

import build_live_workbook
import engine
import llm_assist
import report_export

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
        """Report Ollama/model availability for the UI.

        A broken or absent Ollama (probe raising, a stale daemon, a bad URL)
        must NEVER raise into the JS bridge — that would freeze the status line.
        Each probe is independent and any failure degrades to a safe "absent"
        reading, with an ``error`` note so the UI can show why if it wants.
        """
        try:
            return {
                "installed": llm_assist.ollama_installed(),
                "running": llm_assist.ollama_running(),
                "model": llm_assist.model_present(),
                "model_name": llm_assist.MODEL,
            }
        except Exception as e:
            # Treat any unexpected probe failure as "AI unavailable" rather than
            # surfacing a traceback; the templated fallback still works without it.
            return {
                "installed": False,
                "running": False,
                "model": False,
                "model_name": getattr(llm_assist, "MODEL", ""),
                "error": f"{type(e).__name__}: {e}",
            }

    def setup_ai(self):
        """Pull the Gemma 4 model. Long-running; UI shows a spinner.

        Wrapped so a failed pull (no Ollama, network/daemon error) returns a
        readable {ok: False, error: ...} dict instead of raising into the JS
        bridge — the analysis still runs via the templated fallback.
        """
        try:
            ok = llm_assist.ensure_model(progress=lambda s: None)
            return {"ok": bool(ok)}
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    # ---- core ---------------------------------------------------------
    def analyze(self, path):
        if not path or not os.path.exists(path):
            return {"error": "File not found."}
        try:
            parser = llm_assist.make_prereq_parser()      # None if no Gemma 4
            results = engine.run(path, llm=parser)
            self._last_results = results
            # "available", not "definitely used": engine only calls the parser
            # for UNstructured prereq text, so structured prereqs are regex-
            # parsed even when this is True. The UI label reflects availability.
            results["ai_used"] = parser is not None
            return results
        except Exception as e:
            return {"error": f"{type(e).__name__}: {e}"}

    # ---- live LACCD data ---------------------------------------------
    def fetch_live(self, campus, terms, program, enrollment_path=None,
                   elumen_live=False, transfer_goal="none", client=None):
        """Pull live LACCD data and analyze it, entirely inside the app.

        Parses the comma-separated `terms` string into ints, runs the live
        pipeline via build_live_workbook.analyze_live (Program Mapper +
        schedule API -> workbook -> engine), and returns a flat dict the UI
        can hand straight to showResult(): the engine `results` (terms_in_data
        / analysis / programs) plus the live-only `reconciliation` and
        `inert_detectors` fields.

        Two optional enrichments (both default off, surfaced as EXPERIMENTAL in
        the live form) reach capacity/prereq depth the bare schedule API can't:
          - `enrollment_path`: an IR PeopleSoft export (.xlsx) joined onto the
            live sections by (term, CRN) to add Cap/Tot/Wait counts (the
            capacity / fill-rate diagnostic). The live<->IR join is NOT
            validated on real data, so a real upload may match 0 sections; the
            detector report carries the honest matched/total count either way.
          - `elumen_live`: fetch prerequisites from the public eLumen catalog
            (itemType=Prerequisite only) so the solver can enforce ordering.
            Best-effort: the endpoint's ToU / rate-limit / approval review is
            pending. Network for this stays inside analyze_live, outside
            engine.run.

        The optional `client` is passed through so tests can inject a
        FakeClient (replaying committed fixtures, no network). On a no-match
        program, any SourceError, or any other failure, returns
        {'error': <clear message>} so the UI shows a readable card instead of
        freezing or silently failing.
        """
        # Term codes are always positive integers; .isdigit() rejects empty,
        # signed ("-2268") and non-numeric tokens, so a negative/zero term is
        # treated as invalid rather than slipping through.
        parsed_terms = [int(t) for t in str(terms).split(",")
                        if t.strip().isdigit() and int(t.strip()) > 0]
        if not parsed_terms:
            return {"error": (f"No valid term codes in {terms!r}. Enter one or "
                              "more positive numeric term codes, e.g. "
                              "2264,2266,2268.")}
        # Normalize the optional enrollment upload: an empty/whitespace path
        # from the UI (no file chosen) means "no enrollment", not a path to
        # load. A non-empty path that does not exist is a user-visible error
        # rather than a traceback out of enrollment.load_enrollment.
        enroll = (enrollment_path or "").strip() or None
        if enroll is not None and not os.path.exists(enroll):
            return {"error": f"Enrollment export not found: {enroll}"}
        try:
            # analyze_live writes a workbook as a side effect; route it to a
            # throwaway temp file so nothing leaks into the user's workspace.
            with tempfile.TemporaryDirectory() as tmp:
                out_path = os.path.join(tmp, "live_workbook.xlsx")
                report = build_live_workbook.analyze_live(
                    campus, parsed_terms, program, out_path,
                    enrollment_path=enroll, elumen_live=bool(elumen_live),
                    transfer_goal=transfer_goal, client=client)
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
        out["ge_coverage"] = report.get("ge_coverage")
        # Live data has no prerequisite/LLM parse step; mark accordingly so the
        # status line does not falsely claim a Gemma 4 parse.
        out["ai_used"] = False
        self._last_results = out
        return out

    def explain(self):
        """Return a plain-language briefing for the last analysis.

        Guards two failure modes so the UI always gets readable text, never a
        traceback marshalled across the JS bridge:
          - no analysis has run yet (``_last_results`` is None/empty); and
          - a partial/None-cohort results dict (missing ``programs``, a program
            missing ``cohorts``, a cohort missing ``terms_used``, etc.), which
            would otherwise raise inside the templated summary.
        """
        if not self._last_results:
            return {"text": "Run an analysis first."}
        try:
            return {"text": llm_assist.explain(self._last_results)}
        except Exception as e:
            return {"text": ("Could not summarize the analysis "
                             f"({type(e).__name__}: {e}). The full results are "
                             "still shown above.")}

    # ---- export -------------------------------------------------------
    def export_report(self, path=None):
        """Write the last analysis as a self-contained, accessible HTML report.

        Returns ``{"path": <written path>}`` on success, ``{"path": ""}`` if the
        native Save dialog was cancelled, or ``{"error": ...}`` on any failure —
        never raises into the JS bridge (mirrors fetch_live / analyze). The
        optional ``path`` lets tests bypass the dialog (same injection style as
        fetch_live's ``client``); JS calls it with no args so the user picks a
        location. The plain-language briefing is generated the same way as
        ``explain`` and embedded; a briefing failure degrades to an empty briefing
        rather than blocking the export (the schedule is the point of the report).
        """
        if not self._last_results:
            return {"error": "Run an analysis first."}
        try:
            try:
                briefing = llm_assist.explain(self._last_results)
            except Exception:
                briefing = ""
            if not path:
                chosen = webview.windows[0].create_file_dialog(
                    webview.SAVE_DIALOG,
                    save_filename="schedule-report.html",
                    file_types=("HTML file (*.html)", "All files (*.*)"))
                if not chosen:
                    return {"path": ""}
                # pywebview returns a str or a 1-tuple depending on backend.
                path = chosen[0] if isinstance(chosen, (list, tuple)) else chosen
            html_doc = report_export.render_report(
                self._last_results, briefing=briefing,
                generated_at=datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(html_doc)
            return {"path": path}
        except Exception as e:
            return {"error": f"Could not export report: {type(e).__name__}: {e}"}


def main():
    api = Api()
    webview.create_window(
        "LACCD Schedule Planner",
        resource_path("ui.html"),
        js_api=api,
        width=1100, height=820, min_size=(900, 640),
    )
    webview.start()


if __name__ == "__main__":
    main()
