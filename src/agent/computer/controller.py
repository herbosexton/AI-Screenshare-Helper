from __future__ import annotations

from typing import Any, Callable, Optional

from src.agent.computer.platform import ComputerPlatform, WindowInfo
from src.agent.computer.windows import create_platform
from src.agent.emergency import GLOBAL_EMERGENCY_STOP


def _normalize_hotkeys(keys: list[str] | str, *more: str) -> list[str]:
    if more:
        head = [keys] if isinstance(keys, str) else list(keys)
        return [str(k) for k in (*head, *more) if str(k).strip()]
    if isinstance(keys, str):
        return [k for k in keys.replace("+", " ").split() if k]
    return [str(k) for k in (keys or []) if str(k).strip()]


class ComputerController:
    """
    High-level computer control API used by agent tools.
    Delegates OS-specific work to ComputerPlatform.
    """

    def __init__(self, platform: Optional[ComputerPlatform] = None):
        self.platform = platform or create_platform()
        self.on_event: Optional[Callable[[str, dict[str, Any]], None]] = None

    def _emit(self, name: str, data: Optional[dict[str, Any]] = None) -> None:
        if self.on_event is None:
            return
        try:
            self.on_event(name, data or {})
        except Exception as e:
            print(f"[Computer] event {name} listener error: {e}")

    def _stop(self) -> None:
        GLOBAL_EMERGENCY_STOP.check()

    def list_windows(self) -> list[dict[str, Any]]:
        return [self._window_dict(w) for w in self.platform.list_windows()]

    def get_active_window(self) -> Optional[dict[str, Any]]:
        w = self.platform.get_active_window()
        return None if w is None else self._window_dict(w)

    def focus_window(self, handle: Optional[int] = None, title_contains: str = "") -> dict[str, Any]:
        self._stop()
        target = self._resolve_window(handle=handle, title_contains=title_contains)
        ok = self.platform.focus_window(target.handle)
        result = {"success": ok, "window": self._window_dict(target)}
        self._emit("windowFocused", result)
        return result

    def minimize_window(self, handle: Optional[int] = None, title_contains: str = "") -> dict[str, Any]:
        self._stop()
        target = self._resolve_window(handle=handle, title_contains=title_contains)
        ok = self.platform.minimize_window(target.handle)
        return {"success": ok, "window": self._window_dict(target)}

    def maximize_window(self, handle: Optional[int] = None, title_contains: str = "") -> dict[str, Any]:
        self._stop()
        target = self._resolve_window(handle=handle, title_contains=title_contains)
        ok = self.platform.maximize_window(target.handle)
        return {"success": ok, "window": self._window_dict(target)}

    def close_window(self, handle: Optional[int] = None, title_contains: str = "") -> dict[str, Any]:
        self._stop()
        target = self._resolve_window(handle=handle, title_contains=title_contains)
        ok = self.platform.close_window(target.handle)
        result = {"success": ok, "window": self._window_dict(target)}
        self._emit("applicationClosed", result)
        return result

    def open_application(self, name: str) -> dict[str, Any]:
        self._stop()
        result = self.platform.open_application(name)
        # Try to surface a matching window shortly after launch
        time_windows = self.platform.list_windows()
        matched = [
            self._window_dict(w)
            for w in time_windows
            if name.lower() in w.title.lower()
            or name.lower() in (w.process_name or "").lower()
        ]
        result["windows"] = matched[:5]
        self._emit("applicationOpened", result)
        return result

    def open_file(self, path: str) -> dict[str, Any]:
        self._stop()
        return self.platform.open_file(path)

    def get_running_applications(self) -> list[dict[str, Any]]:
        return self.platform.get_running_applications()

    def move_mouse(self, x: int, y: int) -> dict[str, Any]:
        self._stop()
        self.platform.move_mouse(x, y)
        return {"x": x, "y": y}

    def click(self, x: Optional[int] = None, y: Optional[int] = None, button: str = "left") -> dict[str, Any]:
        self._stop()
        self.platform.click(x, y, button=button)
        return {"x": x, "y": y, "button": button}

    def double_click(self, x: Optional[int] = None, y: Optional[int] = None) -> dict[str, Any]:
        self._stop()
        self.platform.double_click(x, y)
        return {"x": x, "y": y}

    def right_click(self, x: Optional[int] = None, y: Optional[int] = None) -> dict[str, Any]:
        self._stop()
        self.platform.right_click(x, y)
        return {"x": x, "y": y}

    def drag(self, x1: int, y1: int, x2: int, y2: int) -> dict[str, Any]:
        self._stop()
        self.platform.drag(x1, y1, x2, y2)
        return {"from": [x1, y1], "to": [x2, y2]}

    def scroll(self, clicks: int, x: Optional[int] = None, y: Optional[int] = None) -> dict[str, Any]:
        self._stop()
        self.platform.scroll(clicks, x, y)
        return {"clicks": clicks, "x": x, "y": y}

    def type_text(self, text: str, interval: float = 0.02) -> dict[str, Any]:
        self._stop()
        self.platform.type_text(text, interval=interval)
        return {"typed_chars": len(text)}

    def press_key(self, key: str) -> dict[str, Any]:
        self._stop()
        self.platform.press_key(key)
        return {"key": key}

    def hotkey(self, keys: list[str] | str, *more: str) -> dict[str, Any]:
        """Canonical hotkey API: hotkey(['ctrl', 't']) or hotkey('ctrl', 't')."""
        self._stop()
        seq = _normalize_hotkeys(keys, *more)
        self.platform.hotkey(*seq)
        return {"keys": seq}

    def get_clipboard(self) -> dict[str, Any]:
        return {"text": self.platform.get_clipboard()}

    def set_clipboard(self, text: str) -> dict[str, Any]:
        self.platform.set_clipboard(text)
        return {"copied_chars": len(text)}

    def get_screen_size(self) -> dict[str, Any]:
        info = self.platform.get_screen_size()
        return {"width": info.width, "height": info.height, "monitors": info.monitors}

    def wait(self, seconds: float) -> dict[str, Any]:
        self.platform.wait(seconds)
        return {"waited": seconds}

    def find_window(self, title_contains: str) -> Optional[dict[str, Any]]:
        needle = title_contains.lower().strip()
        for w in self.platform.list_windows():
            if needle in w.title.lower():
                return self._window_dict(w)
        return None

    def _resolve_window(
        self,
        handle: Optional[int] = None,
        title_contains: str = "",
    ) -> WindowInfo:
        if handle:
            for w in self.platform.list_windows():
                if w.handle == handle:
                    return w
            # Still allow focusing by handle even if not in visible list
            active = self.platform.get_active_window()
            return WindowInfo(handle=handle, title=active.title if active else "")

        if title_contains:
            needle = title_contains.lower()
            matches = [w for w in self.platform.list_windows() if needle in w.title.lower()]
            if not matches:
                raise ValueError(f"No window found containing title: {title_contains}")
            return matches[0]

        active = self.platform.get_active_window()
        if active is None:
            raise ValueError("No active window")
        return active

    @staticmethod
    def _window_dict(w: WindowInfo) -> dict[str, Any]:
        return {
            "handle": w.handle,
            "hwnd": w.handle,
            "title": w.title,
            "process_id": w.process_id,
            "process_name": w.process_name,
            "rect": {
                "left": w.rect[0],
                "top": w.rect[1],
                "right": w.rect[2],
                "bottom": w.rect[3],
            },
            "is_visible": w.is_visible,
            "is_minimized": w.is_minimized,
            "monitor_index": getattr(w, "monitor_index", -1),
        }
