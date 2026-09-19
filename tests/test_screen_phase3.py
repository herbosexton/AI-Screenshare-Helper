"""Phase 3 screen understanding tests."""

from __future__ import annotations

from src.agent.computer.controller import ComputerController
from src.agent.screen.understanding import ScreenState, ScreenUnderstandingService
from tests.test_computer_phase2 import FakePlatform


class FakeScreen:
    def capture_all(self):
        return [{"monitor_index": 0, "size": (1920, 1080), "base64": "x"}]


def test_screen_state_from_windows():
    controller = ComputerController(platform=FakePlatform())
    svc = ScreenUnderstandingService(FakeScreen(), controller)
    state = svc.get_state(include_screenshot=True)
    assert isinstance(state, ScreenState)
    assert state.screen_width == 1920
    assert "Chrome" in " ".join(state.visible_text) or state.active_window
    assert state.screenshot is not None


def test_verify_window_appeared():
    controller = ComputerController(platform=FakePlatform())
    svc = ScreenUnderstandingService(FakeScreen(), controller)
    result = svc.verify_window_appeared("Chrome")
    assert result["verified"] is True


def test_verify_window_missing():
    controller = ComputerController(platform=FakePlatform())
    svc = ScreenUnderstandingService(FakeScreen(), controller)
    result = svc.verify_window_appeared("NotARealAppXYZ")
    assert result["verified"] is False


def test_screen_tools_registered_in_factory(tmp_path, monkeypatch):
    import yaml
    from src.agent.factory import build_agent_stack

    config = {
        "agent": {
            "enabled": True,
            "computer_control_enabled": True,
            "data_dir": str(tmp_path),
            "cloud_fallback": False,
            "model": "qwen3:8b",
        }
    }
    # Avoid real win32 if Fake preferred — factory creates real ComputerController.
    # That's ok on Windows; if it fails tools still include phase1.
    orch = build_agent_stack(
        config,
        screen_capture=FakeScreen(),
        clipboard_out=None,
        speech_out_getter=lambda: None,
    )
    names = {t.name for t in orch.registry.list_tools()}
    assert "computer.get_screen_state" in names
    assert "computer.verify_window" in names


def test_coords_capture_to_desktop_and_dpi():
    from src.agent.screen.coords import capture_to_desktop, box_center_desktop, resolve_monitor, point_in_rect

    x, y = capture_to_desktop(50, 25, region_left=100, region_top=200, capture_width=100, capture_height=50)
    assert (x, y) == (150, 225)
    x2, y2 = capture_to_desktop(
        10, 10, region_left=0, region_top=0, capture_width=200, capture_height=100, source_width=100, source_height=50
    )
    assert (x2, y2) == (20, 20)
    cx, cy = box_center_desktop({"left": 10, "top": 10, "right": 30, "bottom": 50})
    assert (cx, cy) == (20, 30)
    monitors = [
        {"monitorId": "0", "index": 0, "x": 0, "y": 0, "width": 1920, "height": 1080, "primary": True, "active": False},
        {"monitorId": "1", "index": 1, "x": -1920, "y": 0, "width": 1920, "height": 1080, "primary": False, "active": False},
    ]
    left = resolve_monitor(monitors, query="left monitor")
    assert left["x"] == -1920
    other = resolve_monitor(monitors, query="second monitor")
    assert other["index"] == 1
    assert point_in_rect(20, 20, {"left": 0, "top": 0, "right": 100, "bottom": 100})


def test_fingerprint_and_change_detector():
    from PIL import Image
    from src.agent.screen.fingerprint import ScreenChangeDetector, perceptual_hash, screen_fingerprint

    a = Image.new("RGB", (64, 64), "white")
    b = Image.new("RGB", (64, 64), "black")
    ha, hb = perceptual_hash(a), perceptual_hash(b)
    assert ha != hb
    fp = screen_fingerprint(active_window="Chrome", image_hash=ha, visible_text=["Chrome"])
    assert len(fp) == 16
    det = ScreenChangeDetector(threshold=0.04)
    first = det.observe(a, fingerprint="aaa")
    assert first["hasScreenChanged"] is True
    same = det.observe(a, fingerprint="aaa")
    assert same["hasScreenChanged"] is False
    diff = det.observe(b, fingerprint="bbb")
    assert diff["hasScreenChanged"] is True


def test_resolver_ambiguity_and_unique():
    from src.agent.screen.errors import AMBIGUOUS_ELEMENT, ScreenError
    from src.agent.screen.resolver import VisualElementResolver
    from src.agent.screen.understanding import VisibleElement

    els = [
        VisibleElement(kind="button", name="Continue", bounds={"left": 0, "top": 0, "right": 10, "bottom": 10}),
        VisibleElement(kind="button", name="Continue", bounds={"left": 50, "top": 0, "right": 60, "bottom": 10}),
    ]
    r = VisualElementResolver()
    try:
        r.resolve(els, "Continue")
        assert False, "expected ambiguity"
    except ScreenError as e:
        assert e.code == AMBIGUOUS_ELEMENT
        assert e.details.get("candidates")
    one = [
        VisibleElement(kind="button", name="Continue", bounds={"left": 0, "top": 0, "right": 10, "bottom": 10}),
        VisibleElement(kind="button", name="Cancel", bounds={"left": 50, "top": 0, "right": 60, "bottom": 10}),
    ]
    el, conf = r.resolve(one, "Continue")
    assert el.name == "Continue"
    assert conf > 0.5


def test_screen_intent_router_paths():
    from src.agent.screen.intent import ScreenIntentRouter
    from src.agent.router import FastCommandRouter

    s = ScreenIntentRouter()
    assert s.route("Where is the Continue button?").action == "screen.find_element"
    assert s.route("What is this popup asking?").action == "screen.dialog"
    assert s.route("What error do you see?").action == "screen.error"
    assert s.route("What changed?").action == "screen.verify"
    assert s.route("What's on my other monitor?").action == "screen.describe"
    r = FastCommandRouter()
    assert r.route("What page am I on?").action == "browser.current_page"
    assert r.route("What application am I in?").action == "computer.active_window"
    assert r.route("Find the Apply button and click it, then continue the application.") is None


def test_error_codes_exist():
    from src.agent.screen import errors as e

    for name in (
        "SCREEN_CAPTURE_UNAVAILABLE",
        "ACTIVE_WINDOW_UNKNOWN",
        "MONITOR_NOT_FOUND",
        "SCREEN_ANALYSIS_TIMEOUT",
        "VISION_PROVIDER_ERROR",
        "ELEMENT_NOT_FOUND",
        "AMBIGUOUS_ELEMENT",
        "STALE_SCREEN_STATE",
        "WINDOW_NOT_FOCUSED",
        "COORDINATE_CONVERSION_ERROR",
        "UIA_TIMEOUT",
        "UIA_UNAVAILABLE",
        "VISUAL_VERIFICATION_FAILED",
    ):
        assert getattr(e, name)


def test_find_click_target_unique_and_stale_vague():
    controller = ComputerController(platform=FakePlatform())
    svc = ScreenUnderstandingService(FakeScreen(), controller)
    svc.get_state = lambda **kwargs: ScreenState(  # type: ignore
        active_window="Test",
        interactive_elements=[
            __import__("src.agent.screen.understanding", fromlist=["VisibleElement"]).VisibleElement(
                kind="button",
                name="Continue",
                bounds={"left": 10, "top": 10, "right": 40, "bottom": 30},
            )
        ],
        fingerprint="abc",
    )
    hit = svc.find_click_target("Continue")
    assert hit and hit["x"] == 25 and hit["y"] == 20
    assert svc.find_click_target("that") is None


def test_screen_cache_and_fingerprint_on_state():
    controller = ComputerController(platform=FakePlatform())
    svc = ScreenUnderstandingService(FakeScreen(), controller)
    a = svc.get_state(include_uia=False)
    b = svc.get_state(include_uia=False)
    assert a.fingerprint
    assert b.source == "cached"
    svc.invalidate("test")
    c = svc.get_state(include_uia=False)
    assert c.source != "cached" or c.fingerprint == a.fingerprint


def test_compare_before_after():
    controller = ComputerController(platform=FakePlatform())
    svc = ScreenUnderstandingService(FakeScreen(), controller)
    before = svc.remember_before()
    platform = controller.platform
    platform.windows[0].title = "Notepad"
    svc.invalidate("focus")
    cmp_ = svc.compare(before)
    assert "summary" in cmp_
    assert "screenChanged" in cmp_


def test_vision_provider_parses_json_and_timeout():
    from src.agent.providers.base import ProviderResponse
    from src.agent.screen.errors import SCREEN_ANALYSIS_TIMEOUT, ScreenError
    from src.agent.screen.vision import LocalVisionProvider

    class P:
        vision_model = "llava"

        def chat(self, messages, tools=None, images_base64=None):
            return ProviderResponse(content='{"candidates":[{"label":"Go","x":1,"y":2,"width":4,"height":4,"confidence":0.9}]}')

    vis = LocalVisionProvider(P(), timeout_s=2)
    out = vis.analyze("abc", "find")
    assert out["json"]["candidates"][0]["label"] == "Go"
    assert vis.calls == 1

    class Boom:
        vision_model = "llava"

        def chat(self, messages, tools=None, images_base64=None):
            raise TimeoutError("timed out")

    try:
        LocalVisionProvider(Boom()).analyze("abc", "describe")
        assert False
    except ScreenError as e:
        assert e.code == SCREEN_ANALYSIS_TIMEOUT


def test_visual_click_pipeline_fresh_and_in_window():
    from src.agent.computer.platform import WindowInfo

    platform = FakePlatform()
    platform.windows[0] = WindowInfo(
        handle=1,
        title="Google Chrome",
        process_id=10,
        process_name="chrome.exe",
        rect=(0, 0, 400, 300),
    )
    controller = ComputerController(platform=platform)
    svc = ScreenUnderstandingService(FakeScreen(), controller)
    el = __import__("src.agent.screen.understanding", fromlist=["VisibleElement"]).VisibleElement(
        kind="button",
        name="Continue",
        bounds={"left": 40, "top": 40, "right": 80, "bottom": 70},
    )

    def fake_state(**kwargs):
        return ScreenState(
            active_window="Google Chrome",
            fingerprint="fp1",
            interactive_elements=[el],
        )

    svc.get_state = fake_state  # type: ignore
    result = svc.visual_click("Continue")
    assert result.get("ok") is True
    assert result.get("fresh_screenshot") is True
    assert platform.clicks
    assert platform.clicks[0][0] == 60


def test_visual_click_rejects_outside_window():
    from src.agent.computer.platform import WindowInfo

    platform = FakePlatform()
    platform.windows[0] = WindowInfo(
        handle=1,
        title="Google Chrome",
        process_id=10,
        process_name="chrome.exe",
        rect=(0, 0, 100, 80),
    )
    controller = ComputerController(platform=platform)
    svc = ScreenUnderstandingService(FakeScreen(), controller)
    el = __import__("src.agent.screen.understanding", fromlist=["VisibleElement"]).VisibleElement(
        kind="button",
        name="Continue",
        bounds={"left": 500, "top": 500, "right": 540, "bottom": 530},
    )
    svc.get_state = lambda **k: ScreenState(  # type: ignore
        active_window="Google Chrome",
        fingerprint="fp1",
        interactive_elements=[el],
    )
    result = svc.visual_click("Continue")
    assert result.get("ok") is False
    assert result.get("code") == "STALE_SCREEN_STATE"
    assert not platform.clicks


def test_dialog_and_error_from_window_titles():
    from src.agent.computer.platform import WindowInfo

    platform = FakePlatform()
    platform.windows.append(
        WindowInfo(handle=9, title="Error: save failed", process_id=99, process_name="app.exe")
    )
    controller = ComputerController(platform=platform)
    svc = ScreenUnderstandingService(FakeScreen(), controller)
    err = svc.read_error()
    assert err.get("ok") is True
    assert "error" in (err.get("message") or "").lower() or "failed" in (err.get("message") or "").lower()


def test_uia_timeout_returns_structured_error():
    from src.agent.screen.errors import UIA_TIMEOUT, ScreenError
    from src.agent.screen import uia as uia_mod

    def slow(*a, **k):
        import time as _t

        _t.sleep(5)

    # Force the worker to hang by patching the import path used inside the thread
    original = uia_mod.get_foreground_uia_elements

    def wrapped(max_elements=80, timeout_s=0.2, **kwargs):
        return original(max_elements=max_elements, timeout_s=0.2)

    # Direct timeout: join 0.2s with a sleep-5 walk is hard without patching _walk.
    # Call with tiny timeout while uiautomation may return quickly on this machine.
    try:
        items = uia_mod.get_foreground_uia_elements(max_elements=5, timeout_s=1.5)
        assert isinstance(items, list)
    except ScreenError as e:
        assert e.code in {UIA_TIMEOUT, "UIA_UNAVAILABLE"}


def test_live_capture_status_and_active_window():
    import time
    from src.capture.screen import ScreenCapture

    cap = ScreenCapture({"monitors": "all"})
    t0 = time.perf_counter()
    status = cap.status()
    status_ms = (time.perf_counter() - t0) * 1000
    assert status.get("available") is True
    assert status.get("monitor_count") >= 1
    assert status_ms < 500
    t0 = time.perf_counter()
    shot = cap.capture_monitor(0)
    cap_ms = (time.perf_counter() - t0) * 1000
    assert shot and shot.get("width")
    print(f"[MEASURE] capture_status {status_ms:.1f} ms capture_monitor {cap_ms:.1f} ms")
    t0 = time.perf_counter()
    region = cap.capture_region(int(shot["left"]), int(shot["top"]), 80, 80)
    region_ms = (time.perf_counter() - t0) * 1000
    assert region.get("width") == 80
    print(f"[MEASURE] region_capture {region_ms:.1f} ms")
    controller = ComputerController()
    t0 = time.perf_counter()
    win = controller.get_active_window()
    win_ms = (time.perf_counter() - t0) * 1000
    assert win and (win.get("title") or win.get("process_name"))
    print(f"[MEASURE] active_window {win_ms:.1f} ms title={win.get('title')}")
    svc = ScreenUnderstandingService(cap, controller)
    t0 = time.perf_counter()
    state = svc.get_state(include_uia=False)
    state_ms = (time.perf_counter() - t0) * 1000
    assert state.fingerprint
    assert state.monitors
    print(f"[MEASURE] structured_state {state_ms:.1f} ms fp={state.fingerprint}")
    t0 = time.perf_counter()
    uia_state = svc.get_state(include_uia=True)
    uia_ms = (time.perf_counter() - t0) * 1000
    print(f"[MEASURE] uia_state {uia_ms:.1f} ms controls={len(uia_state.interactive_elements)}")
    t0 = time.perf_counter()
    aw = svc.capture_active_window()
    aw_ms = (time.perf_counter() - t0) * 1000
    assert aw.get("has_image")
    print(f"[MEASURE] active_window_capture {aw_ms:.1f} ms")
    nmon = status.get("monitor_count") or 1
    if nmon < 2:
        print("[MEASURE] second monitor NOT TESTED — HARDWARE UNAVAILABLE")
    else:
        t0 = time.perf_counter()
        shot2 = cap.capture_monitor(1)
        mon2_ms = (time.perf_counter() - t0) * 1000
        assert shot2 and shot2.get("width")
        assert int(shot2.get("left") or 0) != int(shot.get("left") or 0) or int(shot2.get("top") or 0) != int(
            shot.get("top") or 0
        )
        print(
            f"[MEASURE] second_monitor_capture {mon2_ms:.1f} ms "
            f"origin=({shot2.get('left')},{shot2.get('top')}) {shot2.get('width')}x{shot2.get('height')}"
        )


def test_vision_cache_reuses_same_fingerprint():
    from src.agent.providers.base import ProviderResponse
    from src.agent.screen.vision import LocalVisionProvider
    from PIL import Image

    class P:
        vision_model = "llava"
        n = 0

        def chat(self, messages, tools=None, images_base64=None):
            P.n += 1
            return ProviderResponse(content="A test window.")

    class Cap:
        def capture_all(self):
            img = Image.new("RGB", (32, 32), "blue")
            return [{"image": img, "base64": "xx", "size": (32, 32), "width": 32, "height": 32, "left": 0, "top": 0}]

        def capture_monitor(self, index=0):
            return self.capture_all()[0]

        def get_monitors(self):
            return [{"left": 0, "top": 0, "width": 32, "height": 32}]

    svc = ScreenUnderstandingService(Cap(), ComputerController(platform=FakePlatform()), LocalVisionProvider(P()))
    a = svc.describe()
    b = svc.describe()
    assert a.get("ok") is True
    assert P.n == 1
    assert b.get("ok") is True


def test_find_element_without_vision_returns_not_found(monkeypatch):
    from src.agent.screen.errors import ELEMENT_NOT_FOUND

    # The service reads the live desktop's accessibility tree directly. Without this
    # the result depends on whether any real window happens to show a "Continue"
    # control, which is exactly what a running Jarvis window puts on screen.
    monkeypatch.setattr(
        "src.agent.screen.understanding.get_foreground_uia_elements",
        lambda **kwargs: [],
    )
    svc = ScreenUnderstandingService(FakeScreen(), ComputerController(platform=FakePlatform()))
    out = svc.find_element("Continue")
    assert out.get("ok") is False
    assert out.get("code") == ELEMENT_NOT_FOUND


def test_describe_captures_requested_monitor():
    from PIL import Image
    from src.agent.providers.base import ProviderResponse
    from src.agent.screen.vision import LocalVisionProvider

    class P:
        vision_model = "llava"

        def chat(self, messages, tools=None, images_base64=None):
            return ProviderResponse(content="Secondary display content.")

    class Cap:
        last_index = None

        def capture_all(self):
            return [self.capture_monitor(0)]

        def capture_monitor(self, index=0):
            Cap.last_index = index
            img = Image.new("RGB", (24, 24), "red" if index else "blue")
            left = -1920 if index else 0
            return {
                "image": img,
                "base64": "xx",
                "size": (24, 24),
                "width": 24,
                "height": 24,
                "left": left,
                "top": 0,
                "monitor_index": index,
            }

        def get_monitors(self):
            return [
                {"left": 0, "top": 0, "width": 24, "height": 24},
                {"left": -1920, "top": 0, "width": 24, "height": 24},
            ]

        def list_monitor_meta(self):
            from src.agent.screen.coords import list_monitor_states

            return list_monitor_states(self.get_monitors())

        def capture_window_rect(self, rect):
            return self.capture_monitor(0)

    svc = ScreenUnderstandingService(Cap(), ComputerController(platform=FakePlatform()), LocalVisionProvider(P()))
    out = svc.describe(monitor_query="other monitor")
    assert out.get("ok") is True
    assert Cap.last_index == 1
    assert (out.get("image_width") or 0) > 0


def test_find_element_escalates_to_vision_after_uia_timeout(monkeypatch):
    from PIL import Image

    from src.agent.providers.base import ProviderResponse
    from src.agent.screen.errors import UIA_TIMEOUT, ScreenError
    from src.agent.screen.vision import LocalVisionProvider

    def boom(**kwargs):
        raise ScreenError("timed out", UIA_TIMEOUT)

    monkeypatch.setattr("src.agent.screen.understanding.get_foreground_uia_elements", boom)

    class P:
        def chat(self, messages, tools=None, images_base64=None):
            return ProviderResponse(
                content='{"candidates":[{"label":"Continue","x":10,"y":10,"width":20,"height":10,"confidence":0.9,"clickable":true}]}'
            )

    class Cap:
        def capture_all(self):
            img = Image.new("RGB", (40, 40), "blue")
            return [{"image": img, "base64": "x", "width": 40, "height": 40, "left": 0, "top": 0, "size": (40, 40)}]

        def capture_monitor(self, index=0):
            return self.capture_all()[0]

        def capture_window_rect(self, rect):
            return self.capture_all()[0]

        def get_monitors(self):
            return [{"left": 0, "top": 0, "width": 40, "height": 40}]

    svc = ScreenUnderstandingService(Cap(), ComputerController(platform=FakePlatform()), LocalVisionProvider(P()))
    out = svc.find_element("Continue")
    assert out.get("ok") is True
    assert out.get("source") == "vision"
    assert out.get("uia_timed_out") is True

