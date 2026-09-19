"""HWND-first desktop browser discovery and BrowserWindowRegistry.

Discovery starts from visible top-level windows, then checks the owning
process executable. Playwright attachment is irrelevant here.

ComputerController.list_windows() returns dicts, not WindowInfo objects.
This module never depends on that shape — it enumerates Win32 directly
when available, and accepts WindowInfo *or* dicts as a test fallback.
"""

from __future__ import annotations

import os
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional


BROWSER_EXES = frozenset({
    "chrome.exe",
    "msedge.exe",
    "firefox.exe",
    "brave.exe",
    "opera.exe",
    "chromium.exe",
})

BROWSER_CLASSES = frozenset({
    "Chrome_WidgetWin_1",
    "MozillaWindowClass",
    "ApplicationFrameWindow",  # Edge / Store browsers
})

SUPPORTING_CLASSES = frozenset({
    "Chrome_WidgetWin_0",
    "Chrome_WidgetWin_1",
})

_BROWSER_TITLE_SUFFIX = re.compile(
    r"\s*[-–—]\s*(?:Google Chrome|Microsoft[\u200b ]?Edge|Mozilla Firefox|Brave|Opera|Chromium)\s*$",
    re.I,
)

_JARVIS_TITLE = re.compile(r"j\.?a\.?r\.?v\.?i\.?s", re.I)

# Minimized windows are parked at this sentinel by Windows.
_ICONIC_SENTINEL = -32000


def extract_tab_title(window_title: str) -> str:
    return _BROWSER_TITLE_SUFFIX.sub("", window_title or "").strip()


def exe_basename(path_or_name: str) -> str:
    name = (path_or_name or "").replace("/", "\\")
    return name.rsplit("\\", 1)[-1].lower()


def is_browser_exe(path_or_name: str) -> bool:
    return exe_basename(path_or_name) in BROWSER_EXES


def is_jarvis_window(title: str, process_name: str = "") -> bool:
    if _JARVIS_TITLE.search(title or ""):
        return True
    base = exe_basename(process_name)
    return base in {"python.exe", "pythonw.exe"} and _JARVIS_TITLE.search(title or "")


@dataclass
class BrowserWindowRef:
    hwnd: int = 0
    pid: int = 0
    process_name: str = ""
    exe_path: str = ""
    window_class: str = ""
    title: str = ""
    bounds: tuple[int, int, int, int] = (0, 0, 0, 0)
    monitor_id: str = ""
    monitor_index: int = -1
    visible: bool = True
    minimized: bool = False
    is_foreground: bool = False
    last_foreground_at: float = 0.0
    last_seen_at: float = 0.0
    source: str = "EXISTING_DESKTOP"
    rejected_reason: str = ""
    playwright_owned: bool = False

    @property
    def active_tab_title(self) -> str:
        return extract_tab_title(self.title)

    def as_info(self):
        from src.agent.browser.resolver import BrowserWindowInfo

        return BrowserWindowInfo(
            hwnd=self.hwnd,
            process_id=self.pid,
            process_name=self.exe_path or self.process_name,
            window_title=self.title,
            bounds=self.bounds,
            monitor_index=self.monitor_index,
            is_foreground=self.is_foreground,
            is_minimized=self.minimized,
            is_visible=self.visible,
            source=self.source,
            active_tab_title=self.active_tab_title,
            playwright_owned=self.playwright_owned,
            window_class=self.window_class,
            exe_path=self.exe_path,
            monitor_id=self.monitor_id,
        )


def _process_exe(pid: int) -> str:
    if not pid:
        return ""
    try:
        import win32api
        import win32con
        import win32process

        access = win32con.PROCESS_QUERY_LIMITED_INFORMATION
        try:
            handle = win32api.OpenProcess(access, False, pid)
        except Exception:
            handle = win32api.OpenProcess(
                win32con.PROCESS_QUERY_INFORMATION | win32con.PROCESS_VM_READ,
                False,
                pid,
            )
        try:
            try:
                return win32process.GetModuleFileNameEx(handle, 0)
            except Exception:
                import ctypes
                from ctypes import wintypes

                buf = ctypes.create_unicode_buffer(32768)
                size = wintypes.DWORD(len(buf))
                ok = ctypes.windll.kernel32.QueryFullProcessImageNameW(
                    int(handle), 0, buf, ctypes.byref(size)
                )
                return buf.value if ok else ""
        finally:
            win32api.CloseHandle(handle)
    except Exception:
        return ""


def _is_cloaked(hwnd: int) -> bool:
    try:
        import ctypes
        from ctypes import wintypes

        cloaked = wintypes.DWORD()
        DWMWA_CLOAKED = 14
        hr = ctypes.windll.dwmapi.DwmGetWindowAttribute(
            wintypes.HWND(hwnd),
            ctypes.c_uint(DWMWA_CLOAKED),
            ctypes.byref(cloaked),
            ctypes.sizeof(cloaked),
        )
        return hr == 0 and cloaked.value != 0
    except Exception:
        return False


def _monitor_for_rect(rect: tuple[int, int, int, int]) -> tuple[int, str]:
    """Return (index, id). Negative coordinates on a left monitor are valid."""
    try:
        import win32api

        monitors = win32api.EnumDisplayMonitors(None, None)
        cx = (rect[0] + rect[2]) // 2
        cy = (rect[1] + rect[3]) // 2
        for idx, (_hmon, _hdc, mon_rect) in enumerate(monitors):
            ml, mt, mr, mb = mon_rect
            if ml <= cx < mr and mt <= cy < mb:
                return idx, f"DISPLAY{idx + 1}"
    except Exception:
        pass
    return -1, ""


def _hwnd_alive(hwnd: int) -> bool:
    try:
        import win32gui

        return bool(win32gui.IsWindow(hwnd))
    except Exception:
        return False


def enumerate_top_level_windows() -> tuple[list[dict[str, Any]], list[BrowserWindowRef], list[str]]:
    """HWND-first scan. Returns (all_visible_meta, browser_candidates, rejection_reasons)."""
    if os.name != "nt":
        return [], [], ["not_windows"]

    import win32gui
    import win32process

    now = time.time()
    try:
        fg_hwnd = int(win32gui.GetForegroundWindow() or 0)
    except Exception:
        fg_hwnd = 0

    total = 0
    visible_count = 0
    metas: list[dict[str, Any]] = []
    candidates: list[BrowserWindowRef] = []
    rejections: list[str] = []

    def callback(hwnd, _):
        nonlocal total, visible_count
        total += 1
        hwnd = int(hwnd)
        try:
            visible = bool(win32gui.IsWindowVisible(hwnd))
        except Exception:
            return True
        if not visible:
            return True
        visible_count += 1

        title = ""
        window_class = ""
        try:
            title = win32gui.GetWindowText(hwnd) or ""
        except Exception:
            title = ""
        try:
            window_class = win32gui.GetClassName(hwnd) or ""
        except Exception:
            window_class = ""
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            pid = int(pid)
        except Exception:
            pid = 0
        try:
            bounds = tuple(int(v) for v in win32gui.GetWindowRect(hwnd))
        except Exception:
            bounds = (0, 0, 0, 0)
        try:
            minimized = bool(win32gui.IsIconic(hwnd))
        except Exception:
            minimized = False

        exe_path = _process_exe(pid)
        process_name = exe_basename(exe_path)
        monitor_index, monitor_id = _monitor_for_rect(bounds)
        cloaked = _is_cloaked(hwnd)

        meta = {
            "hwnd": hwnd,
            "pid": pid,
            "process_name": process_name,
            "exe_path": exe_path,
            "window_class": window_class,
            "title": title,
            "visible": True,
            "minimized": minimized,
            "cloaked": cloaked,
            "bounds": bounds,
            "monitor": monitor_id or str(monitor_index),
            "is_foreground": hwnd == fg_hwnd,
        }
        metas.append(meta)

        rejected = ""
        # Process identity is authoritative. Electron apps (Cursor, Slack, etc.)
        # also use Chrome_WidgetWin_1 — class is supporting evidence only.
        if not is_browser_exe(exe_path):
            return True
        if cloaked:
            rejected = "cloaked"
        elif bounds[0] <= _ICONIC_SENTINEL or bounds[1] <= _ICONIC_SENTINEL:
            rejected = "iconic_offscreen"
        elif (bounds[2] - bounds[0]) < 50 or (bounds[3] - bounds[1]) < 50:
            rejected = "too_small"
        elif window_class.startswith("Chrome_") and window_class not in SUPPORTING_CLASSES:
            rejected = f"utility_class:{window_class}"
        elif window_class == "Chrome_WidgetWin_0" and not title.strip():
            rejected = "chrome_host_no_title"
        elif is_jarvis_window(title, exe_path):
            rejected = "jarvis_window"

        ref = BrowserWindowRef(
            hwnd=hwnd,
            pid=pid,
            process_name=process_name,
            exe_path=exe_path,
            window_class=window_class,
            title=title,
            bounds=bounds,
            monitor_id=monitor_id,
            monitor_index=monitor_index,
            visible=True,
            minimized=minimized,
            is_foreground=hwnd == fg_hwnd,
            last_seen_at=now,
            rejected_reason=rejected,
        )
        if rejected:
            rejections.append(f"hwnd={hwnd}:{rejected}")
            return True
        candidates.append(ref)
        return True

    try:
        win32gui.EnumWindows(callback, None)
    except Exception as e:
        rejections.append(f"enum_failed:{e}")

    return metas, candidates, rejections, total, visible_count, fg_hwnd


# enumerate returns a long tuple — keep a typed wrapper
def scan_desktop() -> dict[str, Any]:
    raw = enumerate_top_level_windows()
    if len(raw) == 3:
        metas, candidates, rejections = raw
        return {
            "metas": metas,
            "candidates": candidates,
            "rejections": rejections,
            "total": 0,
            "visible": 0,
            "foreground_hwnd": 0,
        }
    metas, candidates, rejections, total, visible, fg = raw
    return {
        "metas": metas,
        "candidates": candidates,
        "rejections": rejections,
        "total": total,
        "visible": visible,
        "foreground_hwnd": fg,
    }


def refs_from_computer_windows(
    windows: list[Any],
    fg_hwnd: int = 0,
    managed_pids: Optional[set[int]] = None,
) -> list[BrowserWindowRef]:
    """Normalize WindowInfo objects *or* ComputerController dicts."""
    managed_pids = managed_pids or set()
    now = time.time()
    out: list[BrowserWindowRef] = []
    for w in windows or []:
        if isinstance(w, dict):
            hwnd = int(w.get("handle") or w.get("hwnd") or 0)
            title = w.get("title") or w.get("window_title") or ""
            pid = int(w.get("process_id") or w.get("pid") or 0)
            exe = w.get("process_name") or w.get("exe_path") or ""
            rect = w.get("rect") or w.get("bounds") or (0, 0, 0, 0)
            if isinstance(rect, dict):
                rect = (
                    int(rect.get("left", 0)),
                    int(rect.get("top", 0)),
                    int(rect.get("right", 0)),
                    int(rect.get("bottom", 0)),
                )
            visible = bool(w.get("is_visible", w.get("visible", True)))
            minimized = bool(w.get("is_minimized", w.get("minimized", False)))
            monitor_index = int(w.get("monitor_index", -1))
            window_class = w.get("window_class") or ""
        else:
            hwnd = int(getattr(w, "handle", 0) or getattr(w, "hwnd", 0) or 0)
            title = getattr(w, "title", "") or getattr(w, "window_title", "") or ""
            pid = int(getattr(w, "process_id", 0) or getattr(w, "pid", 0) or 0)
            exe = getattr(w, "process_name", "") or getattr(w, "exe_path", "") or ""
            rect = getattr(w, "rect", None) or getattr(w, "bounds", (0, 0, 0, 0))
            visible = bool(getattr(w, "is_visible", True))
            minimized = bool(getattr(w, "is_minimized", False))
            monitor_index = int(getattr(w, "monitor_index", -1))
            window_class = getattr(w, "window_class", "") or ""

        if not hwnd:
            continue
        if not is_browser_exe(exe) and not _BROWSER_TITLE_SUFFIX.search(title):
            continue
        if not is_browser_exe(exe):
            continue
        if is_jarvis_window(title, exe):
            continue
        if rect and len(rect) == 4 and (rect[0] <= _ICONIC_SENTINEL or rect[1] <= _ICONIC_SENTINEL):
            continue

        managed = pid in managed_pids
        mon_id = f"DISPLAY{monitor_index + 1}" if monitor_index >= 0 else ""
        out.append(
            BrowserWindowRef(
                hwnd=hwnd,
                pid=pid,
                process_name=exe_basename(exe),
                exe_path=exe,
                window_class=window_class,
                title=title,
                bounds=tuple(rect) if rect else (0, 0, 0, 0),
                monitor_id=mon_id,
                monitor_index=monitor_index,
                visible=visible,
                minimized=minimized,
                is_foreground=hwnd == fg_hwnd,
                last_seen_at=now,
                source="MANAGED_PLAYWRIGHT" if managed else "EXISTING_DESKTOP",
                playwright_owned=managed,
            )
        )
    return out


def _fg_hwnd_from_computer(computer) -> int:
    if computer is None:
        return 0
    try:
        active = computer.get_active_window()
    except Exception:
        return 0
    if active is None:
        return 0
    if isinstance(active, dict):
        return int(active.get("handle") or active.get("hwnd") or 0)
    return int(getattr(active, "handle", 0) or getattr(active, "hwnd", 0) or 0)


def print_discovery_log(
    *,
    total: int,
    visible: int,
    candidates: list[BrowserWindowRef],
    foreground_hwnd: int,
    last_user_hwnd: int,
    selected: Optional[BrowserWindowRef],
    selection_reason: str,
    rejections: list[str],
) -> None:
    print(
        "[BrowserDiscovery]\n"
        f"total_top_level_windows={total}\n"
        f"visible_windows={visible}\n"
        f"browser_candidates={len(candidates)}\n"
        f"foreground_hwnd={foreground_hwnd}\n"
        f"last_user_browser_hwnd={last_user_hwnd}\n"
        f"rejection_reasons={rejections}"
    )
    print("[BrowserCandidates]")
    for c in candidates:
        print(
            f"hwnd={c.hwnd}\n"
            f"title={c.title}\n"
            f"monitor={c.monitor_id or c.monitor_index}\n"
            f"foreground={str(c.is_foreground).lower()}\n"
            f"last_foreground_at={c.last_foreground_at}\n"
        )
    if selected:
        print(
            "[BrowserTarget]\n"
            f"selected_hwnd={selected.hwnd}\n"
            f"selection_reason={selection_reason}\n"
            f"monitor={selected.monitor_id or selected.monitor_index}\n"
            f"selected_source={selected.source}"
        )


class BrowserWindowRegistry:
    """Tracks existing desktop browser HWNDs independently of Playwright."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._windows: dict[int, BrowserWindowRef] = {}
        self.last_user_browser_hwnd: int = 0
        self.last_user_browser_pid: int = 0
        self.last_user_browser_monitor: str = ""
        self.last_user_browser_title: str = ""
        self.last_user_browser_foreground_at: float = 0.0
        self._hook = None
        self._hook_thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    def upsert(self, ref: BrowserWindowRef) -> None:
        with self._lock:
            prev = self._windows.get(ref.hwnd)
            if prev and prev.last_foreground_at and not ref.last_foreground_at:
                ref.last_foreground_at = prev.last_foreground_at
            self._windows[ref.hwnd] = ref
            if ref.is_foreground and not ref.playwright_owned:
                ref.last_foreground_at = time.time()
                self._remember_user(ref)

    def _remember_user(self, ref: BrowserWindowRef) -> None:
        self.last_user_browser_hwnd = ref.hwnd
        self.last_user_browser_pid = ref.pid
        self.last_user_browser_monitor = ref.monitor_id or str(ref.monitor_index)
        self.last_user_browser_title = ref.title
        self.last_user_browser_foreground_at = ref.last_foreground_at or time.time()

    def seed(self, refs: list[BrowserWindowRef]) -> None:
        with self._lock:
            seen = set()
            for ref in refs:
                if ref.rejected_reason or ref.playwright_owned:
                    continue
                self.upsert(ref)
                seen.add(ref.hwnd)
            for hwnd in list(self._windows):
                if hwnd not in seen and not _hwnd_alive(hwnd):
                    self._windows.pop(hwnd, None)
            # Do not invent a "last used" window from whatever was enumerated
            # first at startup. That pins JARVIS to the wrong monitor.

    def note_foreground(
        self,
        hwnd: int,
        *,
        title: str = "",
        exe: str = "",
        pid: int = 0,
        window_class: str = "",
    ) -> None:
        if not hwnd:
            return
        if exe and not is_browser_exe(exe):
            return
        if is_jarvis_window(title, exe):
            return
        with self._lock:
            ref = self._windows.get(int(hwnd))
            if ref is None:
                mon_i, mon_id = (-1, "")
                try:
                    import win32gui

                    bounds = tuple(int(v) for v in win32gui.GetWindowRect(int(hwnd)))
                    mon_i, mon_id = _monitor_for_rect(bounds)
                except Exception:
                    bounds = (0, 0, 0, 0)
                ref = BrowserWindowRef(
                    hwnd=int(hwnd),
                    pid=int(pid),
                    process_name=exe_basename(exe),
                    exe_path=exe,
                    window_class=window_class,
                    title=title,
                    bounds=bounds,
                    monitor_id=mon_id,
                    monitor_index=mon_i,
                    visible=True,
                    source="EXISTING_DESKTOP",
                )
                self._windows[int(hwnd)] = ref
            elif title:
                ref.title = title
            ref.is_foreground = True
            ref.last_foreground_at = time.time()
            ref.last_seen_at = time.time()
            if ref.playwright_owned:
                return
            self._remember_user(ref)

    def prune(self) -> None:
        with self._lock:
            for hwnd in list(self._windows):
                if not _hwnd_alive(hwnd):
                    self._windows.pop(hwnd, None)
            if self.last_user_browser_hwnd and self.last_user_browser_hwnd not in self._windows:
                self.last_user_browser_hwnd = 0

    def get(self, hwnd: int) -> Optional[BrowserWindowRef]:
        with self._lock:
            return self._windows.get(int(hwnd))

    def visible_existing(self) -> list[BrowserWindowRef]:
        with self._lock:
            return [
                w
                for w in self._windows.values()
                if w.visible and not w.playwright_owned and not w.rejected_reason
            ]

    def _best_unlocked(self) -> Optional[BrowserWindowRef]:
        living = [
            w
            for w in self._windows.values()
            if w.visible and not w.minimized and not w.playwright_owned
        ]
        if not living:
            living = [w for w in self._windows.values() if not w.playwright_owned]
        if not living:
            return None
        living.sort(key=lambda w: (w.last_foreground_at, w.last_seen_at), reverse=True)
        return living[0]

    def last_user(self) -> Optional[BrowserWindowRef]:
        with self._lock:
            if self.last_user_browser_hwnd:
                ref = self._windows.get(self.last_user_browser_hwnd)
                if ref:
                    if os.name == "nt" and not _hwnd_alive(ref.hwnd):
                        self.last_user_browser_hwnd = 0
                    else:
                        return ref
            return self._best_unlocked()

    def start_foreground_hook(self) -> None:
        if os.name != "nt" or self._hook_thread is not None:
            return

        def _run() -> None:
            try:
                import ctypes
                from ctypes import wintypes

                user32 = ctypes.windll.user32
                EVENT_SYSTEM_FOREGROUND = 0x0003
                WINEVENT_OUTOFCONTEXT = 0x0000
                WINEVENT_SKIPOWNPROCESS = 0x0002

                WinEventProcType = ctypes.WINFUNCTYPE(
                    None,
                    wintypes.HANDLE,
                    wintypes.DWORD,
                    wintypes.HWND,
                    wintypes.LONG,
                    wintypes.LONG,
                    wintypes.DWORD,
                    wintypes.DWORD,
                )

                def _cb(hhook, event, hwnd, id_obj, id_child, thread, time_ms):
                    if not hwnd or id_obj != 0:
                        return
                    try:
                        import win32gui
                        import win32process

                        title = win32gui.GetWindowText(int(hwnd)) or ""
                        cls = win32gui.GetClassName(int(hwnd)) or ""
                        _, pid = win32process.GetWindowThreadProcessId(int(hwnd))
                        exe = _process_exe(int(pid))
                    except Exception:
                        return
                    if is_jarvis_window(title, exe):
                        return
                    if not is_browser_exe(exe):
                        return
                    self.note_foreground(
                        int(hwnd),
                        title=title,
                        exe=exe,
                        pid=int(pid),
                        window_class=cls,
                    )

                self._cb_ref = WinEventProcType(_cb)  # keep alive
                self._hook = user32.SetWinEventHook(
                    EVENT_SYSTEM_FOREGROUND,
                    EVENT_SYSTEM_FOREGROUND,
                    0,
                    self._cb_ref,
                    0,
                    0,
                    WINEVENT_OUTOFCONTEXT | WINEVENT_SKIPOWNPROCESS,
                )
                msg = wintypes.MSG()
                while not self._stop.is_set():
                    while user32.PeekMessageW(ctypes.byref(msg), 0, 0, 0, 1):
                        user32.TranslateMessage(ctypes.byref(msg))
                        user32.DispatchMessageW(ctypes.byref(msg))
                    time.sleep(0.05)
                if self._hook:
                    user32.UnhookWinEvent(self._hook)
            except Exception as e:
                print(f"[BrowserDiscovery] foreground hook unavailable: {e}")

        self._stop.clear()
        self._hook_thread = threading.Thread(
            target=_run, daemon=True, name="browser-hwnd-hook"
        )
        self._hook_thread.start()

    def stop(self) -> None:
        self._stop.set()


def read_address_bar_uia(hwnd: int) -> str:
    """Read Chrome/Edge omnibox via UI Automation. Does not steal focus."""
    if not hwnd:
        return ""
    try:
        import uiautomation as auto
    except ImportError:
        return ""
    try:
        from src.agent.screen.uia import _co_init

        _co_init()
    except Exception:
        pass
    try:
        root = auto.ControlFromHandle(hwnd)
        if root is None:
            return ""
        names = (
            "Address and search bar",
            "Address bar",
        )
        for name in names:
            try:
                edit = root.EditControl(Name=name, searchDepth=10)
                if edit.Exists(maxSearchSeconds=0.08):
                    try:
                        val = edit.GetValuePattern().Value
                    except Exception:
                        val = getattr(edit, "Value", "") or ""
                    if val and not val.startswith("Address"):
                        return val.strip()
            except Exception:
                continue
        return ""
    except Exception:
        return ""


def cursor_monitor_index() -> int:
    """Monitor containing the mouse. Works across the full virtual desktop."""
    try:
        import win32api
        import win32gui

        x, y = win32gui.GetCursorPos()
        for idx, (_hmon, _hdc, mon_rect) in enumerate(win32api.EnumDisplayMonitors(None, None)):
            ml, mt, mr, mb = mon_rect
            if ml <= x < mr and mt <= y < mb:
                return idx
    except Exception:
        pass
    return -1


_PREFERRED_EXE = {
    "chrome": ("chrome.exe",),
    "edge": ("msedge.exe",),
    "firefox": ("firefox.exe",),
    "brave": ("brave.exe",),
    "opera": ("opera.exe",),
}


def parse_preferred_browser(text: str) -> str:
    """'on google chrome' is a browser filter, not a page named Google Chrome."""
    t = (text or "").lower()
    if re.search(r"\b(google\s+)?chrome\b", t):
        return "chrome"
    if re.search(r"\b(microsoft\s+)?edge\b", t):
        return "edge"
    if re.search(r"\bfirefox\b", t):
        return "firefox"
    return ""


def select_browser(
    candidates: list[BrowserWindowRef],
    *,
    foreground_hwnd: int,
    last_user_hwnd: int,
    managed_pids: Optional[set[int]] = None,
    preferred_browser: str = "",
) -> tuple[Optional[BrowserWindowRef], str]:
    """HWND-level selection. Process name is a filter, not the identity.

    Priority:
    1. foreground browser HWND
    2. last-user-active browser HWND
    3. most-recently-active visible HWND (last_foreground_at)
    4. deterministic fallback (highest hwnd among visible)
    """
    managed_pids = managed_pids or set()
    usable = [
        c
        for c in candidates
        if not c.playwright_owned
        and not c.rejected_reason
        and c.pid not in managed_pids
    ]
    wanted = _PREFERRED_EXE.get((preferred_browser or "").lower())
    if wanted:
        filtered = [c for c in usable if exe_basename(c.exe_path or c.process_name) in wanted]
        if filtered:
            usable = filtered
    if not usable:
        return None, "NO_CANDIDATES"

    for c in usable:
        if c.hwnd == foreground_hwnd:
            return c, "FOREGROUND_CHROME"

    if last_user_hwnd:
        for c in usable:
            if c.hwnd == last_user_hwnd and c.visible:
                return c, "LAST_USER_ACTIVE_BROWSER"

    visible = [c for c in usable if c.visible and not c.minimized]
    if visible:
        visible.sort(key=lambda c: (c.last_foreground_at, c.last_seen_at, c.hwnd), reverse=True)
        return visible[0], "MOST_RECENTLY_ACTIVE"

    usable.sort(key=lambda c: (c.last_foreground_at, c.hwnd), reverse=True)
    return usable[0], "VISIBLE_CHROME"
