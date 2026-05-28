"""
app.py — native desktop entry point (pywebview).

Wraps the headless engine + Gemma 4 layer in a native window. The user picks a
data file; the OR-Tools solver produces the schedule; Gemma 4 (E2B, via Ollama)
optionally parses messy prerequisites and writes the admin explanation.

Run (dev):   python app.py
Package:     see BUILD.md  (PyInstaller -> single binary)
"""
import os
import threading
import webview

import engine
import llm_assist

HERE = os.path.dirname(os.path.abspath(__file__))


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

    def explain(self):
        if not self._last_results:
            return {"text": "Run an analysis first."}
        return {"text": llm_assist.explain(self._last_results)}


def main():
    api = Api()
    webview.create_window(
        "LAMC Schedule Planner",
        os.path.join(HERE, "ui.html"),
        js_api=api,
        width=1100, height=820, min_size=(900, 640),
    )
    webview.start()


if __name__ == "__main__":
    main()
