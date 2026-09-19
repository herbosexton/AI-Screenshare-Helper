from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Optional

import pyperclip
from pynput.keyboard import Controller as KeyboardController
from pynput.keyboard import Key
from pynput.mouse import Button, Controller as MouseController

from src.agent.computer.platform import ComputerPlatform, ScreenInfo, WindowInfo
from src.agent.emergency import GLOBAL_EMERGENCY_STOP


def _app_paths_executable(name: str) -> str:
    """Resolve a program the way the Run box does, for apps that are not on PATH."""
    try:
        import winreg
    except ImportError:
        return ""
    key = "SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\App Paths\\"
    stem = name if name.lower().endswith(".exe") else f"{name}.exe"
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(root, key + stem) as handle:
                path = str(winreg.QueryValueEx(handle, "")[0]).strip('"')
        except OSError:
            continue
        if path and Path(os.path.expandvars(path)).exists():
            return os.path.expandvars(path)
    return ""


# Friendly name → launch command / path candidates
APP_ALIASES: dict[str, list[str]] = {
    "chrome": [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        "chrome",
    ],
    "google chrome": [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        "chrome",
    ],
    "edge": [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        "msedge",
    ],
    "microsoft edge": [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        "msedge",
    ],
    "firefox": [
        r"C:\Program Files\Mozilla Firefox\firefox.exe",
        "firefox",
    ],
    "notepad": ["notepad.exe"],
    "calculator": ["calc.exe"],
    "calc": ["calc.exe"],
    "explorer": ["explorer.exe"],
    "file explorer": ["explorer.exe"],
    "cmd": ["cmd.exe"],
    "powershell": ["powershell.exe"],
    "cursor": [
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\cursor\Cursor.exe"),
        "cursor",
    ],
    "spotify": [
        os.path.expandvars(r"%APPDATA%\Spotify\Spotify.exe"),
        "spotify",
    ],
    "excel": ["excel.exe"],
    "word": ["winword.exe"],
    "outlook": ["outlook.exe"],
    "vscode": [
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Microsoft VS Code\Code.exe"),
        "code",
    ],
    "vs code": [
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Microsoft VS Code\Code.exe"),
        "code",
    ],
    "chatgpt": [
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\ChatGPT\ChatGPT.exe"),
        "https://chatgpt.com",
    ],
}


KEY_MAP = {
    "enter": Key.enter,
    "return": Key.enter,
    "tab": Key.tab,
    "esc": Key.esc,
    "escape": Key.esc,
    "space": Key.space,
    "backspace": Key.backspace,
    "delete": Key.delete,
    "up": Key.up,
    "down": Key.down,
    "left": Key.left,
    "right": Key.right,
    "home": Key.home,
    "end": Key.end,
    "pageup": Key.page_up,
    "pagedown": Key.page_down,
    "ctrl": Key.ctrl,
    "control": Key.ctrl,
    "alt": Key.alt,
    "shift": Key.shift,
    "cmd": Key.cmd,
    "win": Key.cmd,
    "windows": Key.cmd,
    "f1": Key.f1,
    "f2": Key.f2,
    "f3": Key.f3,
    "f4": Key.f4,
    "f5": Key.f5,
    "f6": Key.f6,
    "f7": Key.f7,
    "f8": Key.f8,
    "f9": Key.f9,
    "f10": Key.f10,
    "f11": Key.f11,
    "f12": Key.f12,
}


class WindowsComputerPlatform(ComputerPlatform):
    """Windows-native computer control via Win32 + pynput."""

    def __init__(self):
        self._mouse = MouseController()
        self._keyboard = KeyboardController()
        self._ensure_win32()

    def _ensure_win32(self) -> None:
        try:
            import win32gui  # noqa: F401
            import win32con  # noqa: F401
            import win32process  # noqa: F401
            import win32api  # noqa: F401
        except ImportError as e:
            raise RuntimeError(
                "pywin32 is required for Windows computer control. "
                "Install with: python -m pip install pywin32"
            ) from e

    def _check_stop(self) -> None:
        GLOBAL_EMERGENCY_STOP.check()

    def _monitor_for_rect(self, rect: tuple[int, int, int, int]) -> int:
        """Return the 0-based monitor index whose area contains the window centre."""
        try:
            import win32api
            monitors = win32api.EnumDisplayMonitors(None, None)
            cx = (rect[0] + rect[2]) // 2
            cy = (rect[1] + rect[3]) // 2
            for idx, (hmon, _hdc, mon_rect) in enumerate(monitors):
                ml, mt, mr, mb = mon_rect
                if ml <= cx < mr and mt <= cy < mb:
                    return idx
        except Exception:
            pass
        return -1

    def list_windows(self) -> list[WindowInfo]:
        import win32gui
        import win32process

        results: list[WindowInfo] = []

        def callback(hwnd, _):
            if not win32gui.IsWindowVisible(hwnd):
                return True
            title = win32gui.GetWindowText(hwnd)
            if not title.strip():
                return True
            try:
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
            except Exception:
                pid = 0
            rect = win32gui.GetWindowRect(hwnd)
            results.append(
                WindowInfo(
                    handle=int(hwnd),
                    title=title,
                    process_id=int(pid),
                    process_name=self._process_name(pid),
                    rect=rect,
                    is_visible=True,
                    is_minimized=bool(win32gui.IsIconic(hwnd)),
                    monitor_index=self._monitor_for_rect(rect),
                )
            )
            return True

        win32gui.EnumWindows(callback, None)
        return results

    def _process_name(self, pid: int) -> str:
        if not pid:
            return ""
        try:
            import win32api
            import win32con
            import win32process

            handle = win32api.OpenProcess(
                win32con.PROCESS_QUERY_INFORMATION | win32con.PROCESS_VM_READ,
                False,
                pid,
            )
            try:
                return win32process.GetModuleFileNameEx(handle, 0)
            finally:
                win32api.CloseHandle(handle)
        except Exception:
            return ""

    def get_active_window(self) -> Optional[WindowInfo]:
        import win32gui
        import win32process

        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return None
        title = win32gui.GetWindowText(hwnd)
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
        except Exception:
            pid = 0
        rect = win32gui.GetWindowRect(hwnd)
        return WindowInfo(
            handle=int(hwnd),
            title=title,
            process_id=int(pid),
            process_name=self._process_name(pid),
            rect=rect,
            is_visible=bool(win32gui.IsWindowVisible(hwnd)),
            is_minimized=bool(win32gui.IsIconic(hwnd)),
            monitor_index=self._monitor_for_rect(rect),
        )

    def focus_window(self, handle: int) -> bool:
        """Bring a window to the foreground and report whether it actually got there.

        Windows refuses SetForegroundWindow from a process that does not own the
        current foreground window, so the plain call can succeed while the window
        stays buried. Escalate through the documented workarounds and verify.
        """
        self._check_stop()
        import win32api
        import win32con
        import win32gui
        import win32process

        def _is_foreground() -> bool:
            try:
                return win32gui.GetForegroundWindow() == handle
            except Exception:
                return False

        try:
            if win32gui.IsIconic(handle):
                win32gui.ShowWindow(handle, win32con.SW_RESTORE)
            else:
                win32gui.ShowWindow(handle, win32con.SW_SHOW)
        except Exception:
            pass

        for attempt in range(3):
            if _is_foreground():
                return True
            try:
                if attempt == 0:
                    win32gui.SetForegroundWindow(handle)
                elif attempt == 1:
                    # Share input state with the current foreground thread so the
                    # foreground lock does not silently reject the request.
                    fg = win32gui.GetForegroundWindow()
                    fg_tid, _ = win32process.GetWindowThreadProcessId(fg)
                    cur_tid = win32api.GetCurrentThreadId()
                    attached = fg_tid and fg_tid != cur_tid
                    if attached:
                        win32process.AttachThreadInput(cur_tid, fg_tid, True)
                    try:
                        win32gui.BringWindowToTop(handle)
                        win32gui.SetForegroundWindow(handle)
                    finally:
                        if attached:
                            win32process.AttachThreadInput(cur_tid, fg_tid, False)
                else:
                    # Last resort: a brief topmost flip raises the window above
                    # whatever is covering it without leaving it pinned.
                    flags = win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE
                    win32gui.SetWindowPos(handle, win32con.HWND_TOPMOST, 0, 0, 0, 0, flags)
                    win32gui.SetWindowPos(handle, win32con.HWND_NOTOPMOST, 0, 0, 0, 0, flags)
                    win32gui.SetForegroundWindow(handle)
            except Exception:
                pass
            time.sleep(0.05)

        return _is_foreground()

    def minimize_window(self, handle: int) -> bool:
        self._check_stop()
        import win32con
        import win32gui

        win32gui.ShowWindow(handle, win32con.SW_MINIMIZE)
        return True

    def maximize_window(self, handle: int) -> bool:
        self._check_stop()
        import win32con
        import win32gui

        win32gui.ShowWindow(handle, win32con.SW_MAXIMIZE)
        return True

    def close_window(self, handle: int) -> bool:
        self._check_stop()
        import win32con
        import win32gui

        win32gui.PostMessage(handle, win32con.WM_CLOSE, 0, 0)
        return True

    def _is_browser_app(self, key: str) -> bool:
        """True if the key corresponds to a browser application."""
        return key in {"chrome", "google chrome", "edge", "microsoft edge", "chromium", "browser"}

    def _find_existing_browser_window(self, key: str) -> Optional[WindowInfo]:
        """Find an existing browser window matching the requested app.

        Returns the foreground one if it exists, otherwise the first visible one.
        """
        import re
        if not self._is_browser_app(key):
            return None

        exe_patterns = {
            "chrome": re.compile(r"chrome\.exe$", re.I),
            "google chrome": re.compile(r"chrome\.exe$", re.I),
            "edge": re.compile(r"msedge\.exe$", re.I),
            "microsoft edge": re.compile(r"msedge\.exe$", re.I),
            "chromium": re.compile(r"chrom(?:e|ium)\.exe$", re.I),
            "browser": re.compile(r"(?:chrome|msedge|firefox)\.exe$", re.I),
        }
        pat = exe_patterns.get(key)
        if pat is None:
            return None

        try:
            active = self.get_active_window()
            fg_hwnd = active.handle if active else 0
        except Exception:
            fg_hwnd = 0

        windows = self.list_windows()
        matches = [w for w in windows if pat.search(w.process_name)]
        if not matches:
            return None

        # Prefer the foreground one
        for w in matches:
            if w.handle == fg_hwnd:
                return w
        # Otherwise prefer visible, non-minimized
        for w in matches:
            if w.is_visible and not w.is_minimized:
                return w
        return matches[0]

    def open_application(self, name_or_path: str) -> dict[str, Any]:
        self._check_stop()
        raw = name_or_path.strip()
        key = raw.lower()

        # For browser apps: focus existing window instead of launching a new one
        existing = self._find_existing_browser_window(key)
        if existing is not None:
            focused = self.focus_window(existing.handle)
            return {
                "launched": existing.process_name,
                "method": "focused_existing",
                "hwnd": existing.handle,
                "title": existing.title,
                "process_id": existing.process_id,
                "monitor_index": existing.monitor_index,
                "new_process": False,
                "focused": focused,
            }

        candidates = APP_ALIASES.get(key, [raw])
        last_error = ""
        for candidate in candidates:
            # URL
            if candidate.startswith("http://") or candidate.startswith("https://"):
                os.startfile(candidate)  # noqa: S606
                time.sleep(1.0)
                return {"launched": candidate, "method": "url"}

            path = Path(os.path.expandvars(candidate))
            try:
                if path.exists():
                    subprocess.Popen([str(path)], shell=False)  # noqa: S603
                    time.sleep(1.2)
                    return {"launched": str(path), "method": "path"}
                exe = shutil.which(candidate) or _app_paths_executable(candidate)
                if exe:
                    subprocess.Popen([exe], shell=False)  # noqa: S603
                    time.sleep(1.2)
                    return {"launched": exe, "method": "resolved"}
                last_error = f"no installed application named '{candidate}'"
                continue
            except Exception as e:
                last_error = str(e)
                continue

        raise RuntimeError(f"Could not open application '{name_or_path}': {last_error}")

    def open_file(self, path: str) -> dict[str, Any]:
        self._check_stop()
        p = Path(os.path.expandvars(path)).expanduser()
        if not p.exists():
            raise FileNotFoundError(f"File not found: {p}")
        os.startfile(str(p))  # noqa: S606
        return {"opened": str(p)}

    def get_running_applications(self) -> list[dict[str, Any]]:
        apps: dict[int, dict[str, Any]] = {}
        for win in self.list_windows():
            if win.process_id and win.process_id not in apps:
                apps[win.process_id] = {
                    "process_id": win.process_id,
                    "process_name": Path(win.process_name).name if win.process_name else "",
                    "path": win.process_name,
                    "window_title": win.title,
                }
        return list(apps.values())

    def move_mouse(self, x: int, y: int) -> None:
        self._check_stop()
        self._mouse.position = (int(x), int(y))

    def click(self, x: Optional[int] = None, y: Optional[int] = None, button: str = "left") -> None:
        self._check_stop()
        if x is not None and y is not None:
            self.move_mouse(x, y)
        btn = Button.left if button == "left" else Button.right if button == "right" else Button.middle
        self._mouse.click(btn, 1)

    def double_click(self, x: Optional[int] = None, y: Optional[int] = None) -> None:
        self._check_stop()
        if x is not None and y is not None:
            self.move_mouse(x, y)
        self._mouse.click(Button.left, 2)

    def right_click(self, x: Optional[int] = None, y: Optional[int] = None) -> None:
        self.click(x, y, button="right")

    def drag(self, x1: int, y1: int, x2: int, y2: int) -> None:
        self._check_stop()
        self.move_mouse(x1, y1)
        self._mouse.press(Button.left)
        time.sleep(0.05)
        self.move_mouse(x2, y2)
        time.sleep(0.05)
        self._mouse.release(Button.left)

    def scroll(self, clicks: int, x: Optional[int] = None, y: Optional[int] = None) -> None:
        self._check_stop()
        if x is not None and y is not None:
            self.move_mouse(x, y)
        self._mouse.scroll(0, int(clicks))

    def type_text(self, text: str, interval: float = 0.02) -> None:
        self._check_stop()
        for ch in text:
            self._check_stop()
            self._keyboard.type(ch)
            if interval:
                time.sleep(interval)

    def _resolve_key(self, key: str):
        k = key.lower().strip()
        if k in KEY_MAP:
            return KEY_MAP[k]
        if len(key) == 1:
            return key
        raise ValueError(f"Unknown key: {key}")

    def press_key(self, key: str) -> None:
        self._check_stop()
        resolved = self._resolve_key(key)
        self._keyboard.press(resolved)
        self._keyboard.release(resolved)

    def hotkey(self, *keys: str) -> None:
        self._check_stop()
        resolved = [self._resolve_key(k) for k in keys]
        for k in resolved:
            self._keyboard.press(k)
        for k in reversed(resolved):
            self._keyboard.release(k)

    def get_clipboard(self) -> str:
        return pyperclip.paste() or ""

    def set_clipboard(self, text: str) -> None:
        pyperclip.copy(text)

    def get_screen_size(self) -> ScreenInfo:
        import ctypes

        user32 = ctypes.windll.user32
        SM_XVIRTUALSCREEN = 76
        SM_YVIRTUALSCREEN = 77
        SM_CXVIRTUALSCREEN = 78
        SM_CYVIRTUALSCREEN = 79
        width = int(user32.GetSystemMetrics(SM_CXVIRTUALSCREEN) or user32.GetSystemMetrics(0))
        height = int(user32.GetSystemMetrics(SM_CYVIRTUALSCREEN) or user32.GetSystemMetrics(1))
        vx = int(user32.GetSystemMetrics(SM_XVIRTUALSCREEN))
        vy = int(user32.GetSystemMetrics(SM_YVIRTUALSCREEN))
        monitors = []
        try:
            from src.agent.screen.coords import list_monitor_states

            monitors = list_monitor_states()
        except Exception:
            monitors = [{"width": width, "height": height, "x": vx, "y": vy}]
        return ScreenInfo(width=width, height=height, monitors=monitors)

    def wait(self, seconds: float) -> None:
        end = time.time() + max(0.0, float(seconds))
        while time.time() < end:
            self._check_stop()
            time.sleep(min(0.1, end - time.time()))


def create_platform() -> ComputerPlatform:
    if os.name != "nt":
        raise RuntimeError("Only WindowsComputerPlatform is implemented in Phase 2")
    return WindowsComputerPlatform()
