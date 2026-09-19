"""DOM → accessibility → visual computer-control fallback (Phase 3 screen understanding)."""

from __future__ import annotations

from typing import Any

from src.agent.browser.errors import VISUAL_FALLBACK_REQUIRED, BrowserError


class VisualFallback:
    def __init__(self, computer=None, screen=None, on_status=None):
        self.computer = computer
        self.screen = screen
        self.on_status = on_status or (lambda _m: None)

    def available(self) -> bool:
        return self.computer is not None and self.screen is not None

    def focus_browser_window(self) -> dict[str, Any]:
        if self.computer is None:
            return {"focused": False, "reason": "Computer control unavailable"}
        for needle in ("Chrome", "Edge", "Chromium", "Playwright"):
            try:
                result = self.computer.focus_window(title_contains=needle)
                if result.get("success"):
                    self.on_status(f"Focusing {needle} window…")
                    return {"focused": True, "window": result.get("window")}
            except Exception:
                continue
        return {"focused": False, "reason": "Browser window not found"}

    def click_named(self, name: str) -> dict[str, Any]:
        """Fresh screenshot + Phase 3 target + mouse. Never click from stale bounds."""
        self.on_status("DOM failed — trying visual fallback…")
        focus = self.focus_browser_window()
        if self.computer is None or self.screen is None:
            raise BrowserError(
                "Structured browser click failed; visual fallback required",
                VISUAL_FALLBACK_REQUIRED,
                retryable=True,
                details={"focus": focus, "name": name},
            )
        getter = getattr(self.screen, "get_state", None)
        if getter is None:
            raise BrowserError(
                "Screen understanding unavailable for visual fallback",
                VISUAL_FALLBACK_REQUIRED,
                retryable=True,
                details={"focus": focus, "name": name},
            )
        try:
            state = getter(include_screenshot=True, include_uia=True)
        except TypeError:
            state = getter(include_screenshot=True)

        target = None
        if state is not None and hasattr(state, "find_elements"):
            matches = state.find_elements(name) or []
            if matches:
                el = matches[0]
                bounds = getattr(el, "bounds", None) or {}
                left = int(bounds.get("left") or 0)
                top = int(bounds.get("top") or 0)
                right = int(bounds.get("right") or 0)
                bottom = int(bounds.get("bottom") or 0)
                if right > left and bottom > top:
                    target = {
                        "name": getattr(el, "name", "") or name,
                        "x": (left + right) // 2,
                        "y": (top + bottom) // 2,
                        "bounds": bounds,
                        "viewport": {
                            "width": int(getattr(state, "screen_width", 0) or 0),
                            "height": int(getattr(state, "screen_height", 0) or 0),
                        },
                    }
        if not target:
            finder = getattr(self.screen, "find_click_target", None)
            if callable(finder):
                target = finder(name)
        if target and target.get("ambiguous"):
            raise BrowserError(
                f"Visual target {name!r} is ambiguous",
                VISUAL_FALLBACK_REQUIRED,
                retryable=True,
                details={"focus": focus, "name": name, "candidates": target.get("candidates")},
            )
        if not target:
            fe = getattr(self.screen, "find_element", None)
            if callable(fe):
                found = fe(name)
                if found.get("ok") and found.get("x") is not None:
                    target = found
        if not target:
            raise BrowserError(
                f"Visual target {name!r} not found after fresh screenshot",
                VISUAL_FALLBACK_REQUIRED,
                retryable=True,
                details={"focus": focus, "name": name},
            )
        x, y = int(target["x"]), int(target["y"])
        self.computer.click(x, y)
        return {
            "attempted": True,
            "verified": True,
            "fresh_screenshot": True,
            "focus": focus,
            "x": x,
            "y": y,
            "name": target.get("name") or name,
        }
