from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

from src.agent.computer.controller import ComputerController
from src.agent.permissions import PermissionLevel
from src.agent.tools.base import BaseTool, ToolResult


class EmptyParams(BaseModel):
    pass


class TitleOrHandle(BaseModel):
    handle: Optional[int] = Field(None, description="Window handle if known")
    title_contains: str = Field(
        "", description="Substring to match in the window title"
    )


class OpenAppParams(BaseModel):
    name: str = Field(..., description="Application name or path, e.g. Chrome, Cursor, notepad")


class OpenFileParams(BaseModel):
    path: str = Field(..., description="Absolute file path to open")


class PointParams(BaseModel):
    x: int
    y: int


class ClickParams(BaseModel):
    x: Optional[int] = None
    y: Optional[int] = None
    button: str = Field("left", description="left | right | middle")


class DragParams(BaseModel):
    x1: int
    y1: int
    x2: int
    y2: int


class ScrollParams(BaseModel):
    clicks: int = Field(..., description="Positive scrolls up, negative scrolls down")
    x: Optional[int] = None
    y: Optional[int] = None


class TypeParams(BaseModel):
    text: str
    interval: float = Field(0.02, description="Delay between keystrokes in seconds")


class KeyParams(BaseModel):
    key: str = Field(..., description="Key name, e.g. enter, tab, esc, a")


class HotkeyParams(BaseModel):
    keys: list[str] = Field(..., description="Key combo in order, e.g. [ctrl, c]")


class WaitParams(BaseModel):
    seconds: float = Field(..., ge=0, le=60)


class FindWindowParams(BaseModel):
    title_contains: str


def _ok(data: Any) -> ToolResult:
    return ToolResult(success=True, data=data)


def _err(e: Exception) -> ToolResult:
    return ToolResult(success=False, error=str(e))


class ListWindowsTool(BaseTool):
    name = "computer.list_windows"
    description = "List visible windows with titles, handles, and process info."
    permission_level = PermissionLevel.OBSERVE
    parameters_model = EmptyParams

    def __init__(self, controller: ComputerController):
        self.c = controller

    def execute(self, **kwargs: Any) -> ToolResult:
        try:
            return _ok({"windows": self.c.list_windows()})
        except Exception as e:
            return _err(e)


class ActiveWindowTool(BaseTool):
    name = "computer.get_active_window"
    description = "Get the currently focused window."
    permission_level = PermissionLevel.OBSERVE
    parameters_model = EmptyParams

    def __init__(self, controller: ComputerController):
        self.c = controller

    def execute(self, **kwargs: Any) -> ToolResult:
        try:
            return _ok({"window": self.c.get_active_window()})
        except Exception as e:
            return _err(e)


class FindWindowTool(BaseTool):
    name = "computer.find_window"
    description = "Find a window by title substring."
    permission_level = PermissionLevel.OBSERVE
    parameters_model = FindWindowParams

    def __init__(self, controller: ComputerController):
        self.c = controller

    def execute(self, title_contains: str = "", **kwargs: Any) -> ToolResult:
        try:
            found = self.c.find_window(title_contains)
            if not found:
                return ToolResult(success=False, error=f"No window matching: {title_contains}")
            return _ok({"window": found})
        except Exception as e:
            return _err(e)


class FocusWindowTool(BaseTool):
    name = "computer.focus_window"
    description = "Focus/bring a window to the foreground by handle or title."
    permission_level = PermissionLevel.LOCAL_ACTION
    parameters_model = TitleOrHandle

    def __init__(self, controller: ComputerController):
        self.c = controller

    def execute(
        self, handle: Optional[int] = None, title_contains: str = "", **kwargs: Any
    ) -> ToolResult:
        try:
            return _ok(self.c.focus_window(handle=handle, title_contains=title_contains))
        except Exception as e:
            return _err(e)


class MinimizeWindowTool(BaseTool):
    name = "computer.minimize_window"
    description = "Minimize a window by handle or title."
    permission_level = PermissionLevel.LOCAL_ACTION
    parameters_model = TitleOrHandle

    def __init__(self, controller: ComputerController):
        self.c = controller

    def execute(
        self, handle: Optional[int] = None, title_contains: str = "", **kwargs: Any
    ) -> ToolResult:
        try:
            return _ok(self.c.minimize_window(handle=handle, title_contains=title_contains))
        except Exception as e:
            return _err(e)


class MaximizeWindowTool(BaseTool):
    name = "computer.maximize_window"
    description = "Maximize a window by handle or title."
    permission_level = PermissionLevel.LOCAL_ACTION
    parameters_model = TitleOrHandle

    def __init__(self, controller: ComputerController):
        self.c = controller

    def execute(
        self, handle: Optional[int] = None, title_contains: str = "", **kwargs: Any
    ) -> ToolResult:
        try:
            return _ok(self.c.maximize_window(handle=handle, title_contains=title_contains))
        except Exception as e:
            return _err(e)


class CloseWindowTool(BaseTool):
    name = "computer.close_window"
    description = "Close a window by handle or title (sends close request)."
    permission_level = PermissionLevel.LOCAL_ACTION
    parameters_model = TitleOrHandle

    def __init__(self, controller: ComputerController):
        self.c = controller

    def execute(
        self, handle: Optional[int] = None, title_contains: str = "", **kwargs: Any
    ) -> ToolResult:
        try:
            return _ok(self.c.close_window(handle=handle, title_contains=title_contains))
        except Exception as e:
            return _err(e)


class OpenApplicationTool(BaseTool):
    name = "computer.open_application"
    description = (
        "Launch an application by friendly name or path "
        "(Chrome, Edge, Cursor, Notepad, Explorer, Spotify, Excel, etc.)."
    )
    permission_level = PermissionLevel.LOCAL_ACTION
    parameters_model = OpenAppParams

    def __init__(self, controller: ComputerController):
        self.c = controller

    def execute(self, name: str = "", **kwargs: Any) -> ToolResult:
        try:
            return _ok(self.c.open_application(name))
        except Exception as e:
            return _err(e)


class OpenFileTool(BaseTool):
    name = "computer.open_file"
    description = "Open a file with the default associated application."
    permission_level = PermissionLevel.LOCAL_ACTION
    parameters_model = OpenFileParams

    def __init__(self, controller: ComputerController):
        self.c = controller

    def execute(self, path: str = "", **kwargs: Any) -> ToolResult:
        try:
            return _ok(self.c.open_file(path))
        except Exception as e:
            return _err(e)


class RunningAppsTool(BaseTool):
    name = "computer.get_running_applications"
    description = "List running applications derived from visible windows."
    permission_level = PermissionLevel.OBSERVE
    parameters_model = EmptyParams

    def __init__(self, controller: ComputerController):
        self.c = controller

    def execute(self, **kwargs: Any) -> ToolResult:
        try:
            return _ok({"applications": self.c.get_running_applications()})
        except Exception as e:
            return _err(e)


class MoveMouseTool(BaseTool):
    name = "computer.move_mouse"
    description = "Move the mouse cursor to screen coordinates."
    permission_level = PermissionLevel.LOCAL_ACTION
    parameters_model = PointParams

    def __init__(self, controller: ComputerController):
        self.c = controller

    def execute(self, x: int = 0, y: int = 0, **kwargs: Any) -> ToolResult:
        try:
            return _ok(self.c.move_mouse(x, y))
        except Exception as e:
            return _err(e)


class ClickTool(BaseTool):
    name = "computer.click"
    description = "Click the mouse at optional coordinates (current position if omitted)."
    permission_level = PermissionLevel.LOCAL_ACTION
    parameters_model = ClickParams

    def __init__(self, controller: ComputerController):
        self.c = controller

    def execute(
        self,
        x: Optional[int] = None,
        y: Optional[int] = None,
        button: str = "left",
        **kwargs: Any,
    ) -> ToolResult:
        try:
            return _ok(self.c.click(x, y, button=button))
        except Exception as e:
            return _err(e)


class DoubleClickTool(BaseTool):
    name = "computer.double_click"
    description = "Double-click at optional coordinates."
    permission_level = PermissionLevel.LOCAL_ACTION
    parameters_model = ClickParams

    def __init__(self, controller: ComputerController):
        self.c = controller

    def execute(
        self, x: Optional[int] = None, y: Optional[int] = None, **kwargs: Any
    ) -> ToolResult:
        try:
            return _ok(self.c.double_click(x, y))
        except Exception as e:
            return _err(e)


class RightClickTool(BaseTool):
    name = "computer.right_click"
    description = "Right-click at optional coordinates."
    permission_level = PermissionLevel.LOCAL_ACTION
    parameters_model = ClickParams

    def __init__(self, controller: ComputerController):
        self.c = controller

    def execute(
        self, x: Optional[int] = None, y: Optional[int] = None, **kwargs: Any
    ) -> ToolResult:
        try:
            return _ok(self.c.right_click(x, y))
        except Exception as e:
            return _err(e)


class DragTool(BaseTool):
    name = "computer.drag"
    description = "Drag the mouse from (x1,y1) to (x2,y2)."
    permission_level = PermissionLevel.LOCAL_ACTION
    parameters_model = DragParams

    def __init__(self, controller: ComputerController):
        self.c = controller

    def execute(
        self, x1: int = 0, y1: int = 0, x2: int = 0, y2: int = 0, **kwargs: Any
    ) -> ToolResult:
        try:
            return _ok(self.c.drag(x1, y1, x2, y2))
        except Exception as e:
            return _err(e)


class ScrollTool(BaseTool):
    name = "computer.scroll"
    description = "Scroll the mouse wheel (positive=up, negative=down)."
    permission_level = PermissionLevel.LOCAL_ACTION
    parameters_model = ScrollParams

    def __init__(self, controller: ComputerController):
        self.c = controller

    def execute(
        self,
        clicks: int = 0,
        x: Optional[int] = None,
        y: Optional[int] = None,
        **kwargs: Any,
    ) -> ToolResult:
        try:
            return _ok(self.c.scroll(clicks, x, y))
        except Exception as e:
            return _err(e)


class TypeTextTool(BaseTool):
    name = "computer.type_text"
    description = "Type text with the keyboard into the focused window."
    permission_level = PermissionLevel.LOCAL_ACTION
    parameters_model = TypeParams

    def __init__(self, controller: ComputerController):
        self.c = controller

    def execute(self, text: str = "", interval: float = 0.02, **kwargs: Any) -> ToolResult:
        try:
            return _ok(self.c.type_text(text, interval=interval))
        except Exception as e:
            return _err(e)


class PressKeyTool(BaseTool):
    name = "computer.press_key"
    description = "Press a single key (enter, tab, esc, letters, function keys)."
    permission_level = PermissionLevel.LOCAL_ACTION
    parameters_model = KeyParams

    def __init__(self, controller: ComputerController):
        self.c = controller

    def execute(self, key: str = "", **kwargs: Any) -> ToolResult:
        try:
            return _ok(self.c.press_key(key))
        except Exception as e:
            return _err(e)


class HotkeyTool(BaseTool):
    name = "computer.hotkey"
    description = "Press a key combination, e.g. keys=['ctrl','c']."
    permission_level = PermissionLevel.LOCAL_ACTION
    parameters_model = HotkeyParams

    def __init__(self, controller: ComputerController):
        self.c = controller

    def execute(self, keys: Optional[list[str]] = None, **kwargs: Any) -> ToolResult:
        try:
            return _ok(self.c.hotkey(keys or []))
        except Exception as e:
            return _err(e)


class ScreenSizeTool(BaseTool):
    name = "computer.get_screen_size"
    description = "Get primary screen width and height."
    permission_level = PermissionLevel.OBSERVE
    parameters_model = EmptyParams

    def __init__(self, controller: ComputerController):
        self.c = controller

    def execute(self, **kwargs: Any) -> ToolResult:
        try:
            return _ok(self.c.get_screen_size())
        except Exception as e:
            return _err(e)


class WaitTool(BaseTool):
    name = "computer.wait"
    description = "Wait a number of seconds (max 60), respecting emergency stop."
    permission_level = PermissionLevel.OBSERVE
    parameters_model = WaitParams

    def __init__(self, controller: ComputerController):
        self.c = controller

    def execute(self, seconds: float = 1.0, **kwargs: Any) -> ToolResult:
        try:
            return _ok(self.c.wait(seconds))
        except Exception as e:
            return _err(e)


def build_computer_tools(controller: ComputerController) -> list[BaseTool]:
    return [
        ListWindowsTool(controller),
        ActiveWindowTool(controller),
        FindWindowTool(controller),
        FocusWindowTool(controller),
        MinimizeWindowTool(controller),
        MaximizeWindowTool(controller),
        CloseWindowTool(controller),
        OpenApplicationTool(controller),
        OpenFileTool(controller),
        RunningAppsTool(controller),
        MoveMouseTool(controller),
        ClickTool(controller),
        DoubleClickTool(controller),
        RightClickTool(controller),
        DragTool(controller),
        ScrollTool(controller),
        TypeTextTool(controller),
        PressKeyTool(controller),
        HotkeyTool(controller),
        ScreenSizeTool(controller),
        WaitTool(controller),
    ]
