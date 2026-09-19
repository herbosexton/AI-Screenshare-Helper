"""Phase 2 computer control tests â€” uses mocks; optional live Windows checks."""

from __future__ import annotations

from typing import Any, Optional
from unittest.mock import MagicMock

import pytest

from src.agent.computer.controller import ComputerController
from src.agent.computer.platform import ComputerPlatform, ScreenInfo, WindowInfo
from src.agent.computer.tools import build_computer_tools
from src.agent.emergency import EmergencyStop
from src.agent.permissions import AutonomyMode, PermissionEngine, PermissionLevel
from src.agent.tools.base import ToolRegistry


class FakePlatform(ComputerPlatform):
    def __init__(self):
        self.windows = [
            WindowInfo(handle=1, title="Google Chrome", process_id=10, process_name="chrome.exe"),
            WindowInfo(handle=2, title="Cursor", process_id=20, process_name="Cursor.exe"),
        ]
        self.opened: list[str] = []
        self.typed: list[str] = []
        self.clicks: list[tuple] = []
        self.focused: list[int] = []

    def list_windows(self):
        return list(self.windows)

    def get_active_window(self):
        return self.windows[0]

    def focus_window(self, handle: int) -> bool:
        self.focused.append(handle)
        return True

    def minimize_window(self, handle: int) -> bool:
        return True

    def maximize_window(self, handle: int) -> bool:
        return True

    def close_window(self, handle: int) -> bool:
        return True

    def open_application(self, name_or_path: str):
        self.opened.append(name_or_path)
        return {"launched": name_or_path, "method": "fake"}

    def open_file(self, path: str):
        return {"opened": path}

    def get_running_applications(self):
        return [{"process_id": 10, "process_name": "chrome.exe"}]

    def move_mouse(self, x: int, y: int) -> None:
        pass

    def click(self, x=None, y=None, button="left") -> None:
        self.clicks.append((x, y, button))

    def double_click(self, x=None, y=None) -> None:
        self.clicks.append((x, y, "double"))

    def right_click(self, x=None, y=None) -> None:
        self.clicks.append((x, y, "right"))

    def drag(self, x1, y1, x2, y2) -> None:
        pass

    def scroll(self, clicks, x=None, y=None) -> None:
        pass

    def type_text(self, text: str, interval: float = 0.02) -> None:
        self.typed.append(text)

    def press_key(self, key: str) -> None:
        pass

    def hotkey(self, *keys: str) -> None:
        pass

    def get_clipboard(self) -> str:
        return "clip"

    def set_clipboard(self, text: str) -> None:
        pass

    def get_screen_size(self) -> ScreenInfo:
        return ScreenInfo(width=1920, height=1080)

    def wait(self, seconds: float) -> None:
        pass


@pytest.fixture
def controller() -> ComputerController:
    return ComputerController(platform=FakePlatform())


@pytest.fixture
def registry(controller: ComputerController) -> ToolRegistry:
    perms = PermissionEngine(
        autonomy_mode=AutonomyMode.ASSIST,
        computer_control_enabled=True,
    )
    reg = ToolRegistry(perms, emergency_stop=EmergencyStop())
    for tool in build_computer_tools(controller):
        reg.register(tool)
    return reg


def test_open_application_tool(registry: ToolRegistry, controller: ComputerController):
    result = registry.execute(
        "computer.open_application", {"name": "Chrome"}, task_id="t1"
    )
    assert result.success
    assert "Chrome" in controller.platform.opened  # type: ignore[attr-defined]


def test_list_windows_tool(registry: ToolRegistry):
    result = registry.execute("computer.list_windows", {}, task_id="t1")
    assert result.success
    assert len(result.data["windows"]) == 2


def test_focus_window_by_title(registry: ToolRegistry, controller: ComputerController):
    result = registry.execute(
        "computer.focus_window", {"title_contains": "Cursor"}, task_id="t1"
    )
    assert result.success
    assert 2 in controller.platform.focused  # type: ignore[attr-defined]


def test_click_and_type(registry: ToolRegistry, controller: ComputerController):
    r1 = registry.execute("computer.click", {"x": 10, "y": 20}, task_id="t1")
    r2 = registry.execute("computer.type_text", {"text": "hello"}, task_id="t1")
    assert r1.success and r2.success
    assert controller.platform.typed == ["hello"]  # type: ignore[attr-defined]


def test_hotkey_accepts_list_and_star_args(controller: ComputerController):
    assert controller.hotkey("ctrl", "t") == {"keys": ["ctrl", "t"]}
    assert controller.hotkey(["ctrl", "c"]) == {"keys": ["ctrl", "c"]}
    assert controller.hotkey("ctrl+r") == {"keys": ["ctrl", "r"]}


def test_computer_control_disabled_blocks_open():
    perms = PermissionEngine(
        autonomy_mode=AutonomyMode.ASSIST,
        computer_control_enabled=False,
    )
    reg = ToolRegistry(perms)
    for tool in build_computer_tools(ComputerController(platform=FakePlatform())):
        reg.register(tool)
    result = reg.execute("computer.open_application", {"name": "Chrome"}, task_id="t1")
    assert result.success is False
    assert "disabled" in (result.error or "").lower()


def test_observe_tools_still_work_when_control_disabled():
    perms = PermissionEngine(
        autonomy_mode=AutonomyMode.ASSIST,
        computer_control_enabled=False,
    )
    reg = ToolRegistry(perms)
    for tool in build_computer_tools(ComputerController(platform=FakePlatform())):
        reg.register(tool)
    result = reg.execute("computer.list_windows", {}, task_id="t1")
    assert result.success is True


def test_emergency_stop_blocks_click(registry: ToolRegistry):
    registry.emergency.engage("test")
    with pytest.raises(RuntimeError):
        registry.execute("computer.click", {"x": 1, "y": 2}, task_id="t1")
    registry.emergency.clear()


def test_find_window(controller: ComputerController):
    found = controller.find_window("chrome")
    assert found is not None
    assert found["handle"] == 1


@pytest.mark.skipif(
    __import__("os").name != "nt",
    reason="Windows only",
)
def test_windows_platform_list_windows_smoke():
    try:
        from src.agent.computer.windows import WindowsComputerPlatform
    except Exception:
        pytest.skip("pywin32 not available")
    try:
        platform = WindowsComputerPlatform()
    except RuntimeError:
        pytest.skip("pywin32 not installed")
    windows = platform.list_windows()
    assert isinstance(windows, list)
    size = platform.get_screen_size()
    assert size.width > 0 and size.height > 0


def test_a_command_the_shell_cannot_find_is_not_reported_as_launched():
    """Popen(shell=True) never raises, so "ChatGBT" came back as successfully launched."""
    from src.agent.computer.windows import WindowsComputerPlatform

    with pytest.raises(RuntimeError):
        WindowsComputerPlatform().open_application("ChatGBT")


def test_a_real_program_still_launches_by_name():
    from src.agent.computer.windows import WindowsComputerPlatform

    result = WindowsComputerPlatform().open_application("notepad")
    assert result.get("launched")

