"""Phase 3 — structured screen understanding (observe before/after actions)."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from pydantic import BaseModel, Field

from src.agent.screen.coords import (
    box_center_desktop,
    list_monitor_states,
    point_in_rect,
    resolve_monitor,
)
from src.agent.screen.errors import (
    ACTIVE_WINDOW_UNKNOWN,
    AMBIGUOUS_ELEMENT,
    ELEMENT_NOT_FOUND,
    SCREEN_CAPTURE_UNAVAILABLE,
    STALE_SCREEN_STATE,
    UIA_TIMEOUT,
    UIA_UNAVAILABLE,
    VISUAL_VERIFICATION_FAILED,
    ScreenError,
)
from src.agent.screen.fingerprint import ScreenChangeDetector, perceptual_hash, screen_fingerprint
from src.agent.screen.resolver import VisualElementResolver
from src.agent.screen.uia import get_foreground_uia_elements
from src.agent.screen.vision import (
    PROMPT_DESCRIBE,
    PROMPT_DIALOG,
    PROMPT_ERROR,
    PROMPT_FIND,
    PROMPT_VERIFY,
    LocalVisionProvider,
)


LEVEL_0_CACHED = 0
LEVEL_1_STRUCTURED = 1
LEVEL_2_TARGETED_VISION = 2
LEVEL_3_FULL_VISION = 3


class VisibleElement(BaseModel):
    kind: str = "window"  # window | button | edit | checkbox | dialog | ...
    name: str = ""
    handle: Optional[int] = None
    process_name: str = ""
    automation_id: str = ""
    bounds: dict[str, int] = Field(default_factory=dict)
    confidence: float = 0.0
    clickable: bool = True
    enabled: bool = True
    source: str = "uia"


class ScreenTextBlock(BaseModel):
    text: str = ""
    bounding_box: dict[str, int] = Field(default_factory=dict)
    confidence: float = 0.0
    source: str = "uia"


class ScreenState(BaseModel):
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    active_application: str = ""
    active_window: str = ""
    active_handle: Optional[int] = None
    screen_width: int = 0
    screen_height: int = 0
    visible_elements: list[VisibleElement] = Field(default_factory=list)
    interactive_elements: list[VisibleElement] = Field(default_factory=list)
    visible_text: list[str] = Field(default_factory=list)
    dialog: Optional[str] = None
    screenshot: Optional[dict[str, Any]] = None
    notes: str = ""
    monitors: list[dict[str, Any]] = Field(default_factory=list)
    active_monitor_id: str = ""
    source: str = "structured"
    fingerprint: str = ""
    dialogs: list[dict[str, Any]] = Field(default_factory=list)
    loading_state: str = "unknown"
    confidence: float = 0.7
    text_blocks: list[ScreenTextBlock] = Field(default_factory=list)
    observation_level: int = LEVEL_1_STRUCTURED
    uia_timed_out: bool = False

    def summary(self) -> str:
        titles = ", ".join(self.visible_text[:6]) or "(none)"
        controls = ", ".join(
            f"{e.kind}:{e.name}" for e in self.interactive_elements[:8] if e.name
        ) or "(none)"
        return (
            f"Active: {self.active_window or 'unknown'} "
            f"[{self.active_application or 'app'}]; "
            f"Windows: {titles}; "
            f"Controls: {controls}; "
            f"Screen: {self.screen_width}x{self.screen_height}"
        )

    def contains_window(self, needle: str) -> bool:
        n = needle.lower()
        if n in (self.active_window or "").lower():
            return True
        return any(n in t.lower() for t in self.visible_text)

    def find_elements(self, name_contains: str, kind: Optional[str] = None) -> list[VisibleElement]:
        needle = name_contains.lower()
        out = []
        for e in self.interactive_elements + self.visible_elements:
            if kind and e.kind != kind:
                continue
            if needle in (e.name or "").lower():
                out.append(e)
        return out


class ScreenUnderstandingService:
    """
    Builds a normalized ScreenState from capture + windows + UI Automation + optional vision.
    Extends existing ScreenCapture — does not replace it. Does not duplicate ComputerController.
    """

    def __init__(self, screen_capture, computer_controller=None, vision_provider=None):
        self._screen = screen_capture
        self._computer = computer_controller
        self.vision = vision_provider
        self.uia_calls = 0
        self.screenshot_calls = 0
        self.vision_calls = 0
        self.ocr_calls = 0
        self.resolver = VisualElementResolver()
        self.change = ScreenChangeDetector()
        self.on_event: Optional[Callable[[str, dict[str, Any]], None]] = None
        self._cache_state: Optional[ScreenState] = None
        self._cache_at = 0.0
        self._cache_ttl = 1.2
        self._last_capture: Optional[dict[str, Any]] = None
        self._last_fp = ""
        self._vision_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._before: Optional[ScreenState] = None
        self.last_perf: dict[str, float] = {}
        self.last_uia_timed_out = False

    def attach_vision(self, provider) -> None:
        if provider is None:
            return
        self.vision = provider if hasattr(provider, "analyze") else LocalVisionProvider(provider, timeout_s=40.0)

    def _emit(self, name: str, data: Optional[dict[str, Any]] = None) -> None:
        if self.on_event is None:
            return
        try:
            self.on_event(name, data or {})
        except Exception:
            pass

    def _mark(self, name: str, t0: float) -> None:
        self.last_perf[name] = round((time.perf_counter() - t0) * 1000, 1)

    def capture_status(self) -> dict[str, Any]:
        """Whether screen capture is connected. Never grabs a screenshot."""
        t0 = time.perf_counter()
        screen = self._screen
        if screen is None:
            self._mark("capture_status", t0)
            return {"available": False, "monitor_count": 0, "reason": "no_capture_service"}
        if hasattr(screen, "status"):
            try:
                out = dict(screen.status() or {})
                self._mark("capture_status", t0)
                return out
            except Exception as e:
                self._mark("capture_status", t0)
                return {"available": False, "monitor_count": 0, "error": str(e)}
        try:
            monitors = screen.get_monitors() if hasattr(screen, "get_monitors") else []
            self._mark("capture_status", t0)
            return {"available": True, "monitor_count": len(monitors or [])}
        except Exception as e:
            self._mark("capture_status", t0)
            return {"available": False, "monitor_count": 0, "error": str(e)}

    def invalidate(self, reason: str = "") -> None:
        self._cache_state = None
        self._cache_at = 0.0
        self._last_fp = ""
        if reason:
            self._emit("screenChanged", {"reason": reason})

    def _fresh_enough(self) -> bool:
        return self._cache_state is not None and (time.perf_counter() - self._cache_at) < self._cache_ttl

    def find_click_target(self, name: str, *, window_title: str = "") -> Optional[dict[str, Any]]:
        """Locate a named control. Prefer UIA. Never click the first of several equals."""
        needle = (name or "").strip()
        if not needle or needle.lower() in {"that", "this", "it"}:
            return None
        t0 = time.perf_counter()
        state = self.get_state(include_screenshot=False, include_uia=True, window_title=window_title)
        els = list(state.interactive_elements) + list(state.visible_elements)
        try:
            el, conf = self.resolver.resolve(els, needle)
        except ScreenError as e:
            if e.code == AMBIGUOUS_ELEMENT:
                return {
                    "ambiguous": True,
                    "candidates": (e.details or {}).get("candidates") or [],
                    "message": str(e),
                    "code": AMBIGUOUS_ELEMENT,
                }
            self._mark("element_resolution", t0)
            return None
        point = self._click_point(el, state)
        self._mark("element_resolution", t0)
        if point:
            point["confidence"] = conf
            point["source"] = el.source or "uia"
            point["fingerprint"] = state.fingerprint
        return point

    @staticmethod
    def _click_point(el: VisibleElement, state: ScreenState) -> Optional[dict[str, Any]]:
        b = el.bounds or {}
        try:
            x, y = box_center_desktop(b)
        except ScreenError:
            left, top = int(b.get("left") or 0), int(b.get("top") or 0)
            right, bottom = int(b.get("right") or 0), int(b.get("bottom") or 0)
            if right <= left or bottom <= top:
                return None
            x, y = (left + right) // 2, (top + bottom) // 2
        return {
            "name": el.name,
            "kind": el.kind,
            "x": x,
            "y": y,
            "bounds": b,
            "active_window": state.active_window,
        }

    def get_state(
        self,
        include_screenshot: bool = False,
        include_uia: bool = False,
        window_title: str = "",
    ) -> ScreenState:
        t_all = time.perf_counter()
        if not include_screenshot and not include_uia and self._fresh_enough() and self._cache_state:
            cached = self._cache_state.model_copy()
            cached.source = "cached"
            cached.observation_level = LEVEL_0_CACHED
            return cached

        width = height = 0
        elements: list[VisibleElement] = []
        interactive: list[VisibleElement] = []
        titles: list[str] = []
        text_blocks: list[ScreenTextBlock] = []
        active_title = ""
        active_app = ""
        active_handle = None
        active_rect: dict[str, int] = {}
        dialog = None
        dialogs: list[dict[str, Any]] = []
        notes = ""
        source = "structured"
        loading = "unknown"
        uia_timed_out = False

        t0 = time.perf_counter()
        monitors: list[dict[str, Any]] = []
        try:
            if self._screen is not None and hasattr(self._screen, "list_monitor_meta"):
                monitors = self._screen.list_monitor_meta()
            elif self._screen is not None and hasattr(self._screen, "get_monitors"):
                monitors = list_monitor_states(self._screen.get_monitors())
        except Exception:
            monitors = []

        if self._computer is not None:
            try:
                size = self._computer.get_screen_size()
                width = int(size.get("width") or 0)
                height = int(size.get("height") or 0)
            except Exception:
                pass

            try:
                active = self._computer.get_active_window()
                if active:
                    active_title = active.get("title") or ""
                    active_handle = active.get("handle")
                    path = active.get("process_name") or ""
                    active_app = path.split("\\")[-1] if path else ""
                    active_rect = dict(active.get("rect") or {})
            except Exception:
                pass
            self._mark("active_window", t0)

            try:
                for w in self._computer.list_windows():
                    title = w.get("title") or ""
                    titles.append(title)
                    rect = w.get("rect") or {}
                    elements.append(
                        VisibleElement(
                            kind="window",
                            name=title,
                            handle=w.get("handle"),
                            process_name=(w.get("process_name") or "").split("\\")[-1],
                            bounds={
                                "left": int(rect.get("left") or 0),
                                "top": int(rect.get("top") or 0),
                                "right": int(rect.get("right") or 0),
                                "bottom": int(rect.get("bottom") or 0),
                            },
                            source="window",
                        )
                    )
                    lower = title.lower()
                    if any(k in lower for k in ("error", "failed", "warning", "dialog")):
                        dialog = title
                        dialogs.append({"dialogType": "window", "title": title, "message": title, "buttons": []})
                    if "loading" in lower:
                        loading = "still_loading"
            except Exception as e:
                notes = f"Window enumeration failed: {e}"
        else:
            self._mark("active_window", t0)

        if include_uia:
            self.uia_calls += 1
            tu = time.perf_counter()
            try:
                for item in get_foreground_uia_elements(
                    max_elements=80,
                    timeout_s=1.5,
                    name_contains="",
                    window_title=window_title,
                ):
                    el = VisibleElement(
                        kind=item.get("kind") or "control",
                        name=item.get("name") or "",
                        automation_id=item.get("automation_id") or "",
                        bounds=item.get("bounds") or {},
                        source="uia",
                    )
                    interactive.append(el)
                    if el.name:
                        titles.append(el.name)
                        text_blocks.append(
                            ScreenTextBlock(
                                text=el.name,
                                bounding_box=el.bounds,
                                source="uia",
                                confidence=0.8,
                            )
                        )
                    if el.kind in {"progress_bar", "spinner"}:
                        loading = "still_loading"
                    if el.kind == "window" and any(
                        k in (el.name or "").lower() for k in ("error", "dialog", "save", "open")
                    ):
                        dialogs.append({"dialogType": "uia", "title": el.name, "message": el.name, "buttons": []})
            except ScreenError as e:
                notes = (notes + f" UIA {e.code}").strip()
                if e.code == UIA_TIMEOUT:
                    uia_timed_out = True
            except Exception as e:
                notes = (notes + f" UIA failed: {e}").strip()
            self._mark("uia", tu)
            self.last_uia_timed_out = uia_timed_out

        screenshot_meta = None
        image_hash = ""
        if include_screenshot and self._screen is not None:
            self.screenshot_calls += 1
            ts = time.perf_counter()
            try:
                cap = self._targeted_capture(window_title)
                self._last_capture = cap
                screenshot_meta = {
                    "monitor_count": 1,
                    "sizes": [list(cap.get("size") or [])],
                    "left": cap.get("left"),
                    "top": cap.get("top"),
                    "width": cap.get("width"),
                    "height": cap.get("height"),
                    # Never put base64 into ScreenState (privacy).
                }
                img = cap.get("image")
                if img is not None:
                    image_hash = perceptual_hash(img)
                    self.change.observe(img)
                if cap and not width:
                    size = cap.get("size") or (0, 0)
                    width, height = int(size[0]), int(size[1])
            except Exception as e:
                screenshot_meta = {"error": str(e)}
                notes = (notes + f" capture failed: {e}").strip()
            self._mark("capture", ts)
            source = "combined" if include_uia else "structured"

        if monitors and active_rect:
            try:
                hit = resolve_monitor(monitors, active_rect=active_rect)
                for m in monitors:
                    m["active"] = m.get("monitorId") == hit.get("monitorId")
            except ScreenError:
                pass
        active_mon = next((m for m in monitors if m.get("active")), monitors[0] if monitors else {})

        fp = screen_fingerprint(
            active_window=active_title,
            active_application=active_app,
            image_hash=image_hash,
            visible_text=titles,
        )
        prev = self._last_fp
        if prev and prev != fp:
            self._emit("screenChanged", {"fingerprint": fp})
        self._last_fp = fp

        if loading == "unknown" and interactive:
            loading = "loaded"

        state = ScreenState(
            active_application=active_app,
            active_window=active_title,
            active_handle=active_handle,
            screen_width=width,
            screen_height=height,
            visible_elements=elements,
            interactive_elements=interactive,
            visible_text=list(dict.fromkeys(titles)),
            dialog=dialog,
            screenshot=screenshot_meta,
            notes=notes,
            monitors=monitors,
            active_monitor_id=str(active_mon.get("monitorId") or ""),
            source=source,
            fingerprint=fp,
            dialogs=dialogs,
            loading_state=loading,
            confidence=0.85 if interactive else 0.6,
            text_blocks=text_blocks,
            observation_level=LEVEL_1_STRUCTURED if include_uia else LEVEL_0_CACHED,
            uia_timed_out=uia_timed_out,
        )
        if self._cache_state is not None and self._cache_state.fingerprint != fp:
            self._before = self._cache_state
        self._cache_state = state
        self._cache_at = time.perf_counter()
        self._mark("get_state", t_all)
        return state

    def _focus_named(self, window_title: str) -> Optional[dict[str, Any]]:
        if not window_title or self._computer is None:
            return None
        try:
            return self._computer.focus_window(title_contains=window_title)
        except Exception:
            return None

    def _window_rect(self, window_title: str = "") -> dict[str, Any]:
        if self._computer is None:
            return {}
        if window_title:
            found = self._computer.find_window(window_title)
            if found:
                return dict(found.get("rect") or {})
        active = self._computer.get_active_window() or {}
        return dict(active.get("rect") or {})

    def _targeted_capture(self, window_title: str = "") -> dict[str, Any]:
        """Smallest useful region: named/active window, else primary monitor, else all."""
        if self._screen is None:
            raise ScreenError("Screen capture unavailable", SCREEN_CAPTURE_UNAVAILABLE, retryable=False)
        if window_title:
            self._focus_named(window_title)
            time.sleep(0.05)
        if self._computer is not None:
            try:
                rect = self._window_rect(window_title)
                w = int(rect.get("right") or 0) - int(rect.get("left") or 0)
                h = int(rect.get("bottom") or 0) - int(rect.get("top") or 0)
                if w >= 32 and h >= 32 and hasattr(self._screen, "capture_window_rect"):
                    return self._screen.capture_window_rect(rect)
            except Exception:
                pass
        if hasattr(self._screen, "capture_monitor"):
            shot = self._screen.capture_monitor(0)
            if shot:
                return shot
        caps = self._screen.capture_all()
        if not caps:
            raise ScreenError("Screen capture unavailable", SCREEN_CAPTURE_UNAVAILABLE, retryable=False)
        return caps[0]

    def capture_active_window(self) -> dict[str, Any]:
        t0 = time.perf_counter()
        if self._computer is None:
            raise ScreenError("Active window unknown", ACTIVE_WINDOW_UNKNOWN, retryable=True)
        win = self._computer.get_active_window()
        if not win:
            raise ScreenError("Active window unknown", ACTIVE_WINDOW_UNKNOWN, retryable=True)
        if self._screen is None:
            raise ScreenError("Screen capture unavailable", SCREEN_CAPTURE_UNAVAILABLE, retryable=False)
        cap = self._screen.capture_window_rect(win.get("rect") or {})
        cap["window"] = win.get("title")
        self._last_capture = cap
        self.screenshot_calls += 1
        self._mark("capture", t0)
        return {k: v for k, v in cap.items() if k not in {"image", "base64"}} | {"has_image": True}

    def capture_monitor(self, index: int = 0) -> dict[str, Any]:
        if self._screen is None:
            raise ScreenError("Screen capture unavailable", SCREEN_CAPTURE_UNAVAILABLE, retryable=False)
        cap = self._screen.capture_monitor(index)
        if not cap:
            raise ScreenError(f"Monitor {index} not found", "MONITOR_NOT_FOUND", retryable=False)
        self._last_capture = cap
        self.screenshot_calls += 1
        return {k: v for k, v in cap.items() if k not in {"image", "base64"}} | {"has_image": True}

    def capture_region(self, left: int, top: int, width: int, height: int) -> dict[str, Any]:
        if self._screen is None:
            raise ScreenError("Screen capture unavailable", SCREEN_CAPTURE_UNAVAILABLE, retryable=False)
        cap = self._screen.capture_region(left, top, width, height)
        self._last_capture = cap
        self.screenshot_calls += 1
        return {k: v for k, v in cap.items() if k not in {"image", "base64"}} | {"has_image": True}

    def _vision(
        self,
        prompt: str,
        *,
        force_full: bool = False,
        monitor_index: Optional[int] = None,
        window_title: str = "",
    ) -> dict[str, Any]:
        if self.vision is None:
            raise ScreenError("Vision provider not configured", "VISION_PROVIDER_ERROR", retryable=False)
        t0 = time.perf_counter()
        cap: Optional[dict[str, Any]] = None
        if monitor_index is not None and self._screen is not None and hasattr(self._screen, "capture_monitor"):
            cap = self._screen.capture_monitor(int(monitor_index))
            if not cap:
                raise ScreenError(f"Monitor {monitor_index} not found", "MONITOR_NOT_FOUND", retryable=False)
        elif force_full and self._screen is not None and hasattr(self._screen, "capture_monitor"):
            cap = self._screen.capture_monitor(0) or self._targeted_capture(window_title)
        else:
            cap = self._targeted_capture(window_title)
        self._last_capture = cap
        self.screenshot_calls += 1
        img = cap.get("image")
        fp = perceptual_hash(img) if img is not None else ""
        b64 = cap.get("base64") or ""
        vision_size = (int(cap.get("width") or 0), int(cap.get("height") or 0))
        if img is not None:
            encode = getattr(self._screen, "encode_for_vision", None)
            if not callable(encode):
                from src.capture.screen import ScreenCapture

                encode = ScreenCapture.encode_for_vision
            b64, vision_size = encode(img)
        key = f"{fp}|{prompt[:80]}"
        hit = self._vision_cache.get(key)
        if hit and (time.perf_counter() - hit[0]) < 8.0:
            self._mark("vision", t0)
            return hit[1]
        self.vision_calls += 1
        result = self.vision.analyze(b64, prompt)
        result["image_width"] = vision_size[0]
        result["image_height"] = vision_size[1]
        result["region"] = {
            "left": cap.get("left"),
            "top": cap.get("top"),
            "width": cap.get("width"),
            "height": cap.get("height"),
            "image_width": vision_size[0],
            "image_height": vision_size[1],
        }
        result["fingerprint"] = fp
        self._vision_cache[key] = (time.perf_counter(), result)
        self._mark("vision", t0)
        return result

    def describe(self, *, monitor_query: str = "", region: str = "") -> dict[str, Any]:
        """Direct vision path. No planner."""
        t0 = time.perf_counter()
        state = self.get_state(include_screenshot=False, include_uia=False)
        app_hint = f"Active application appears to be {state.active_application or state.active_window}."
        prompt = PROMPT_DESCRIBE + " " + app_hint
        mon_idx: Optional[int] = None
        query = monitor_query or (region if region in {"left", "right"} else "")
        if query:
            hit = resolve_monitor(state.monitors or list_monitor_states(), query=query)
            mon_idx = int(hit.get("index") or 0)
            prompt += f" This capture is monitor {mon_idx} (id {hit.get('monitorId')})."
        try:
            vis = self._vision(prompt, force_full=mon_idx is not None, monitor_index=mon_idx)
            text = vis.get("text") or ""
        except ScreenError as e:
            return {"ok": False, "message": str(e), "error": e.as_dict(), "summary": state.summary()}
        self._mark("describe", t0)
        return {
            "ok": True,
            "message": text,
            "summary": state.summary(),
            "source": "vision",
            "model": vis.get("model"),
            "elapsed_ms": vis.get("elapsed_ms"),
            "image_width": vis.get("image_width"),
            "image_height": vis.get("image_height"),
            "perf": dict(self.last_perf),
        }

    def find_element(self, name: str, *, window_title: str = "") -> dict[str, Any]:
        t0 = time.perf_counter()
        if window_title:
            self._focus_named(window_title)
            time.sleep(0.08)
            self.invalidate("focus")
        uia_error = None
        try:
            target = self.find_click_target(name, window_title=window_title)
        except ScreenError as e:
            target = None
            uia_error = e
            if e.code != UIA_TIMEOUT:
                return {"ok": False, "error": e.as_dict(), "message": str(e)}
        if target and not target.get("ambiguous"):
            self._mark("find_element", t0)
            return {"ok": True, "source": "uia", **target}
        if target and target.get("ambiguous"):
            return {"ok": False, **target}
        timed_out = self.last_uia_timed_out or (uia_error is not None and uia_error.code == UIA_TIMEOUT)
        if self.vision is None:
            code = UIA_TIMEOUT if timed_out else ELEMENT_NOT_FOUND
            return {
                "ok": False,
                "code": code,
                "message": f"I could not find {name} on screen."
                + (" UI Automation timed out." if timed_out else ""),
            }
        try:
            vis = self._vision(PROMPT_FIND + f" Element: {name}", window_title=window_title)
        except ScreenError as e:
            return {"ok": False, "error": e.as_dict(), "message": str(e), "uia_timed_out": timed_out}
        data = vis.get("json") or {}
        cands = data.get("candidates") or []
        if len(cands) > 1:
            return {
                "ok": False,
                "ambiguous": True,
                "code": AMBIGUOUS_ELEMENT,
                "candidates": cands[:5],
                "message": f"I see {len(cands)} matches for {name}.",
            }
        if not cands:
            return {"ok": False, "code": ELEMENT_NOT_FOUND, "message": f"I could not find {name}."}
        c = cands[0]
        region = vis.get("region") or {}
        try:
            x, y = box_center_desktop(
                {"x": c.get("x") or 0, "y": c.get("y") or 0, "width": c.get("width") or 0, "height": c.get("height") or 0},
                region={
                    "left": region.get("left") or 0,
                    "top": region.get("top") or 0,
                    "width": region.get("width") or 1,
                    "height": region.get("height") or 1,
                    "image_width": vis.get("image_width"),
                    "image_height": vis.get("image_height"),
                },
            )
        except ScreenError as e:
            return {"ok": False, "error": e.as_dict(), "message": str(e)}
        self._mark("find_element", t0)
        return {
            "ok": True,
            "source": "vision",
            "name": c.get("label") or name,
            "x": x,
            "y": y,
            "bounds": c,
            "confidence": float(c.get("confidence") or 0.5),
            "fingerprint": vis.get("fingerprint") or "",
            "uia_timed_out": timed_out,
        }

    def visual_click(self, name: str, *, window_title: str = "") -> dict[str, Any]:
        """Fresh screenshot + locate + Phase 2 click + verify. Never uses stale coords."""
        if self._computer is None:
            raise ScreenError("Computer control unavailable", VISUAL_VERIFICATION_FAILED, retryable=False)
        self.invalidate("visual_click")
        focused = None
        if window_title:
            focused = self._focus_named(window_title)
            time.sleep(0.1)
        else:
            before_focus = self.get_state(include_screenshot=False, include_uia=False)
            if before_focus.active_window:
                try:
                    focused = self._computer.focus_window(
                        title_contains=before_focus.active_window.split(" - ")[0][:40]
                    )
                except Exception:
                    focused = None
        before = self.get_state(include_screenshot=True, include_uia=True, window_title=window_title)
        found = self.find_element(name, window_title=window_title)
        if not found.get("ok"):
            return found
        x, y = int(found["x"]), int(found["y"])
        rect = self._window_rect(window_title)
        relocated = False
        if rect and not point_in_rect(x, y, rect, pad=24):
            self.invalidate("stale")
            found = self.find_element(name, window_title=window_title)
            if not found.get("ok"):
                return {
                    "ok": False,
                    "code": STALE_SCREEN_STATE,
                    "message": "Target is outside the active window. I will not click stale coordinates.",
                    "previous": {"x": x, "y": y},
                }
            nx, ny = int(found["x"]), int(found["y"])
            if not point_in_rect(nx, ny, self._window_rect(window_title) or rect, pad=24):
                return {
                    "ok": False,
                    "code": STALE_SCREEN_STATE,
                    "message": "Target is outside the active window. I will not click stale coordinates.",
                    "previous": {"x": x, "y": y},
                }
            relocated = True
            x, y = nx, ny
        if found.get("fingerprint") and before.fingerprint and found.get("fingerprint") != before.fingerprint:
            found = self.find_element(name, window_title=window_title)
            if not found.get("ok"):
                return found
            x, y = int(found["x"]), int(found["y"])
            relocated = True
        self._computer.move_mouse(x, y)
        time.sleep(0.12)
        self._computer.click(x, y)
        time.sleep(0.25)
        after = self.get_state(include_screenshot=False, include_uia=False, window_title=window_title)
        changed = after.fingerprint != before.fingerprint
        return {
            "ok": True,
            "x": x,
            "y": y,
            "name": found.get("name") or name,
            "verified": changed,
            "screenChanged": changed,
            "fresh_screenshot": True,
            "relocated": relocated,
            "focus": focused,
            "source": found.get("source"),
            "confidence": found.get("confidence"),
            "bounds": found.get("bounds"),
            "uia_timed_out": bool(found.get("uia_timed_out") or before.uia_timed_out),
        }

    def compare(self, before: Optional[ScreenState] = None, after: Optional[ScreenState] = None) -> dict[str, Any]:
        a = after
        if a is None:
            self.invalidate("compare")
            a = self.get_state(include_screenshot=False, include_uia=False)
        b = before or self._before
        if b is None:
            return {"screenChanged": True, "changeMagnitude": 1.0, "likelySuccess": False, "confidence": 0.2, "summary": "No before state."}
        changed = (a.fingerprint or "") != (b.fingerprint or "")
        mag = 1.0 if changed else 0.0
        if a.active_window != b.active_window:
            mag = max(mag, 0.8)
            changed = True
        added = [t for t in a.visible_text if t and t not in (b.visible_text or [])]
        removed = [t for t in b.visible_text if t and t not in (a.visible_text or [])]
        if added or removed:
            changed = True
            mag = max(mag, 0.9)
        if a.active_window != b.active_window:
            summary = f"Window was {b.active_window or 'unknown'}, now {a.active_window or 'unknown'}."
        elif added or removed:
            summary = (
                "The UI changed. "
                + (f"Now showing: {added[0]}." if added else "")
                + (f" No longer showing: {removed[0]}." if removed and not added else "")
            ).strip()
        else:
            summary = "The screen changed." if changed else "The screen looks the same."
        return {
            "screenChanged": changed,
            "changeMagnitude": mag,
            "changedRegions": ["full"] if changed else [],
            "likelySuccess": changed,
            "confidence": 0.7 if changed else 0.55,
            "summary": summary,
            "added_text": added[:6],
            "removed_text": removed[:6],
            "before_fp": b.fingerprint,
            "after_fp": a.fingerprint,
        }

    def remember_before(self) -> ScreenState:
        self._before = self.get_state(include_screenshot=False, include_uia=False)
        return self._before

    def _dialog_from_state(self, state: ScreenState) -> Optional[dict[str, Any]]:
        buttons = [e.name for e in state.interactive_elements if e.kind == "button" and e.name][:8]
        uia_texts = [
            e.name
            for e in state.interactive_elements
            if e.kind in {"text", "document"} and e.name and e.name not in buttons
        ]
        title = (state.dialogs[0].get("title") if state.dialogs else "") or state.active_window or ""
        message = ""
        if "—" in title:
            message = title.split("—", 1)[-1].strip()
        elif "?" in title:
            message = title
        elif uia_texts:
            message = max(uia_texts, key=len)
        if state.dialogs or (len(buttons) >= 2 and message):
            return {
                "dialogType": (state.dialogs[0].get("dialogType") if state.dialogs else "application") or "window",
                "title": title,
                "message": message or title,
                "buttons": buttons,
            }
        return None

    def read_dialog(self, *, window_title: str = "") -> dict[str, Any]:
        if window_title:
            self._focus_named(window_title)
            time.sleep(0.08)
            self.invalidate("focus")
        state = self.get_state(include_uia=True, window_title=window_title)
        structured = self._dialog_from_state(state)
        if structured and (structured.get("message") or structured.get("buttons")):
            return {"ok": True, "source": "uia" if state.interactive_elements else "structured", **structured}
        if self.vision is None:
            if structured:
                return {"ok": True, "source": "structured", **structured}
            return {"ok": False, "code": ELEMENT_NOT_FOUND, "message": "I do not see a dialog."}
        try:
            vis = self._vision(PROMPT_DIALOG, window_title=window_title)
            data = vis.get("json") or {}
            if data.get("message") or data.get("title") or data.get("buttons"):
                return {"ok": True, "source": "vision", "uia_timed_out": state.uia_timed_out, **data}
            return {
                "ok": True,
                "source": "vision",
                "message": vis.get("text") or "I do not see a dialog.",
                "uia_timed_out": state.uia_timed_out,
            }
        except ScreenError as e:
            if structured:
                return {"ok": True, "source": "structured", **structured, "vision_error": e.as_dict()}
            return {"ok": False, "error": e.as_dict(), "message": str(e)}

    def read_error(self, *, window_title: str = "") -> dict[str, Any]:
        if window_title:
            self._focus_named(window_title)
            time.sleep(0.08)
            self.invalidate("focus")
        state = self.get_state(include_uia=True, window_title=window_title)
        pool = list(state.visible_text)
        pool.extend(e.name for e in state.interactive_elements if e.name)
        hits = [
            t
            for t in pool
            if any(k in t.lower() for k in ("error", "failed", "unable", "denied", "timeout", "504"))
        ]
        if hits:
            return {"ok": True, "source": "structured", "message": max(hits, key=len)}
        if self.vision is None:
            return {"ok": False, "code": ELEMENT_NOT_FOUND, "message": "I do not see an error."}
        try:
            vis = self._vision(PROMPT_ERROR, window_title=window_title)
            return {
                "ok": True,
                "source": "vision",
                "message": vis.get("text") or "",
                "uia_timed_out": state.uia_timed_out,
            }
        except ScreenError as e:
            return {"ok": False, "error": e.as_dict(), "message": str(e)}

    def loading_status(self) -> dict[str, Any]:
        state = self.get_state(include_uia=True)
        return {"ok": True, "loadingState": state.loading_state, "window": state.active_window}

    def verify_window_appeared(
        self,
        title_contains: str,
        before: Optional[ScreenState] = None,
    ) -> dict[str, Any]:
        after = self.get_state(include_screenshot=False, include_uia=True)
        found = after.contains_window(title_contains)
        return {
            "verified": found,
            "needle": title_contains,
            "before_active": before.active_window if before else None,
            "after_active": after.active_window,
            "after_summary": after.summary(),
            "interactive_count": len(after.interactive_elements),
            "state": after.model_dump(mode="json"),
        }

    def verify_element_present(self, name_contains: str, kind: Optional[str] = None) -> dict[str, Any]:
        state = self.get_state(include_uia=True)
        matches = state.find_elements(name_contains, kind=kind)
        return {
            "verified": len(matches) > 0,
            "needle": name_contains,
            "kind": kind,
            "matches": [m.model_dump() for m in matches[:10]],
            "summary": state.summary(),
        }

    def performance_report(self) -> str:
        lines = ["", "SCREEN PERFORMANCE"]
        order = (
            "capture_status",
            "active_window",
            "capture",
            "uia",
            "vision",
            "element_resolution",
            "describe",
            "find_element",
            "get_state",
        )
        total = 0.0
        for k in order:
            if k in self.last_perf:
                lines.append(f"{k:<22}{self.last_perf[k]:,.0f} ms")
                total += self.last_perf[k]
        lines.append(f"{'TOTAL':<22}{total:,.0f} ms")
        return "\n".join(lines)
