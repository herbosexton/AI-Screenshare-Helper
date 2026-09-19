from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class WindowInfo:
    handle: int
    title: str
    process_id: int = 0
    process_name: str = ""
    rect: tuple[int, int, int, int] = (0, 0, 0, 0)  # left, top, right, bottom
    is_visible: bool = True
    is_minimized: bool = False
    monitor_index: int = -1  # which monitor contains the window center


@dataclass
class ScreenInfo:
    width: int
    height: int
    monitors: list[dict[str, Any]] = field(default_factory=list)


class ComputerPlatform(ABC):
    """OS abstraction for computer control. Windows implements this; others can later."""

    @abstractmethod
    def list_windows(self) -> list[WindowInfo]:
        raise NotImplementedError

    @abstractmethod
    def get_active_window(self) -> Optional[WindowInfo]:
        raise NotImplementedError

    @abstractmethod
    def focus_window(self, handle: int) -> bool:
        raise NotImplementedError

    @abstractmethod
    def minimize_window(self, handle: int) -> bool:
        raise NotImplementedError

    @abstractmethod
    def maximize_window(self, handle: int) -> bool:
        raise NotImplementedError

    @abstractmethod
    def close_window(self, handle: int) -> bool:
        raise NotImplementedError

    @abstractmethod
    def open_application(self, name_or_path: str) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def open_file(self, path: str) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def get_running_applications(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def move_mouse(self, x: int, y: int) -> None:
        raise NotImplementedError

    @abstractmethod
    def click(self, x: Optional[int] = None, y: Optional[int] = None, button: str = "left") -> None:
        raise NotImplementedError

    @abstractmethod
    def double_click(self, x: Optional[int] = None, y: Optional[int] = None) -> None:
        raise NotImplementedError

    @abstractmethod
    def right_click(self, x: Optional[int] = None, y: Optional[int] = None) -> None:
        raise NotImplementedError

    @abstractmethod
    def drag(self, x1: int, y1: int, x2: int, y2: int) -> None:
        raise NotImplementedError

    @abstractmethod
    def scroll(self, clicks: int, x: Optional[int] = None, y: Optional[int] = None) -> None:
        raise NotImplementedError

    @abstractmethod
    def type_text(self, text: str, interval: float = 0.02) -> None:
        raise NotImplementedError

    @abstractmethod
    def press_key(self, key: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def hotkey(self, *keys: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_clipboard(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def set_clipboard(self, text: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_screen_size(self) -> ScreenInfo:
        raise NotImplementedError

    @abstractmethod
    def wait(self, seconds: float) -> None:
        raise NotImplementedError
