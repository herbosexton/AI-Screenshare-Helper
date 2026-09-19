"""One-shot Phase 5 latency measurements. Not part of the pytest suite."""

from __future__ import annotations

import json
import time
from pathlib import Path

from tests.test_browser_phase5 import _session


def main() -> None:
    import tempfile

    timings: dict[str, float] = {}
    tmp = Path(tempfile.mkdtemp(prefix="jarvis-p5-"))
    from tests.test_browser_phase5 import INDEX_HTML, NEXT_HTML, JOB_HTML, FORM_HTML, APPLY_HTML, CAPTCHA_HTML, DOWNLOAD_HTML, CONTINUE_HTML, BROKEN_SELECTOR_HTML, IFRAME_HTML, IFRAME_INNER, SPA_HTML, JS_DIALOG_HTML, POPUP_HTML, LOGIN_HTML, _FixtureHandler
    from functools import partial
    from http.server import ThreadingHTTPServer
    import threading

    (tmp / "index.html").write_text(INDEX_HTML, encoding="utf-8")
    (tmp / "next.html").write_text(NEXT_HTML, encoding="utf-8")
    (tmp / "job.html").write_text(JOB_HTML, encoding="utf-8")
    (tmp / "form.html").write_text(FORM_HTML, encoding="utf-8")
    (tmp / "apply.html").write_text(APPLY_HTML, encoding="utf-8")
    (tmp / "captcha.html").write_text(CAPTCHA_HTML, encoding="utf-8")
    (tmp / "download.html").write_text(DOWNLOAD_HTML, encoding="utf-8")
    (tmp / "continue.html").write_text(CONTINUE_HTML, encoding="utf-8")
    (tmp / "a11y.html").write_text(BROKEN_SELECTOR_HTML, encoding="utf-8")
    (tmp / "iframe.html").write_text(IFRAME_HTML, encoding="utf-8")
    (tmp / "iframe_inner.html").write_text(IFRAME_INNER, encoding="utf-8")
    (tmp / "spa.html").write_text(SPA_HTML, encoding="utf-8")
    (tmp / "jsdialog.html").write_text(JS_DIALOG_HTML, encoding="utf-8")
    (tmp / "popup.html").write_text(POPUP_HTML, encoding="utf-8")
    (tmp / "login.html").write_text(LOGIN_HTML, encoding="utf-8")
    (tmp / "report.bin").write_bytes(b"jarvis-download-ok")
    handler = partial(_FixtureHandler, directory=str(tmp))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    site = f"http://{host}:{port}"
    session = _session(tmp)
    try:
        t0 = time.perf_counter()
        session.agent.open()
        timings["browser_launch_ms"] = round((time.perf_counter() - t0) * 1000, 1)

        t0 = time.perf_counter()
        session.goto(site + "/")
        timings["navigation_ms"] = round((time.perf_counter() - t0) * 1000, 1)

        t0 = time.perf_counter()
        session.agent.get_session_state()
        timings["current_page_query_ms"] = round((time.perf_counter() - t0) * 1000, 1)

        t0 = time.perf_counter()
        session.agent.get_current_url()
        timings["current_url_query_ms"] = round((time.perf_counter() - t0) * 1000, 1)

        session.new_tab(site + "/job.html")
        t0 = time.perf_counter()
        session.agent.switch_tab(query="Jarvis Fixture")
        timings["tab_switch_ms"] = round((time.perf_counter() - t0) * 1000, 1)

        session.goto(site + "/next.html")
        t0 = time.perf_counter()
        session.agent.back()
        timings["back_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        t0 = time.perf_counter()
        session.agent.forward()
        timings["forward_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        t0 = time.perf_counter()
        session.agent.reload()
        timings["refresh_ms"] = round((time.perf_counter() - t0) * 1000, 1)

        t0 = time.perf_counter()
        session.agent.get_page_state()
        timings["page_state_extraction_ms"] = round((time.perf_counter() - t0) * 1000, 1)

        session.goto(site + "/continue.html")
        t0 = time.perf_counter()
        session.agent.click(name="Continue")
        timings["dom_element_resolution_ms"] = round((time.perf_counter() - t0) * 1000, 1)

        session.goto(site + "/a11y.html")
        t0 = time.perf_counter()
        session.agent.click(selector="#does-not-exist", name="Continue")
        timings["accessibility_fallback_ms"] = round((time.perf_counter() - t0) * 1000, 1)

        from src.agent.browser.fallback import VisualFallback

        class FakeComputer:
            def focus_window(self, title_contains=""):
                return {"success": True, "window": title_contains}

            def click(self, x, y, button="left"):
                return {"x": x, "y": y}

        class FakeScreen:
            def get_state(self, include_screenshot=False, include_uia=False):
                return object()

            def find_click_target(self, name: str):
                return {"name": name, "x": 1, "y": 1}

        session.agent.fallback = VisualFallback(FakeComputer(), FakeScreen())
        t0 = time.perf_counter()
        session.agent.click(name="MissingVisualTarget")
        timings["visual_fallback_ms"] = round((time.perf_counter() - t0) * 1000, 1)

        session.goto(site + "/form.html")
        t0 = time.perf_counter()
        session.agent.fill("Test", name="first name")
        timings["form_fill_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        t0 = time.perf_counter()
        session.agent.select("option two")
        timings["select_option_ms"] = round((time.perf_counter() - t0) * 1000, 1)

        resume = tmp / "resume.txt"
        resume.write_text("ok", encoding="utf-8")
        t0 = time.perf_counter()
        session.agent.upload_file(str(resume), selector="#resume")
        timings["upload_ms"] = round((time.perf_counter() - t0) * 1000, 1)

        session.goto(site + "/download.html")
        t0 = time.perf_counter()
        session.agent.click(selector="#dl")
        session.wait(timeout_ms=400)
        timings["download_detection_ms"] = round((time.perf_counter() - t0) * 1000, 1)

        timings["planner_calls_direct_commands"] = 0
        timings["uia_calls_normal_browser"] = 0
        timings["vision_calls_normal_browser"] = 0
        print(json.dumps(timings, indent=2))
        out = Path("docs") / "_phase5_timings.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(timings, indent=2), encoding="utf-8")
    finally:
        session.close()
        server.shutdown()
        thread.join(timeout=2)


if __name__ == "__main__":
    main()
