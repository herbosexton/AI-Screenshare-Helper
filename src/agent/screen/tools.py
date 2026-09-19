from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

from src.agent.permissions import PermissionLevel
from src.agent.screen.understanding import ScreenUnderstandingService
from src.agent.tools.base import BaseTool, ToolResult


class ScreenStateParams(BaseModel):
    include_screenshot: bool = Field(
        False, description="Include screenshot metadata (not full image bytes)"
    )
    include_uia: bool = Field(
        False, description="Include UI Automation controls. Expensive — only if cheaper observation failed."
    )


class VerifyWindowParams(BaseModel):
    title_contains: str = Field(..., description="Substring expected in a window title")


class VerifyElementParams(BaseModel):
    name_contains: str = Field(..., description="Substring of the control name/label")
    kind: Optional[str] = Field(
        None, description="Optional control kind: button, edit, checkbox, hyperlink, ..."
    )


class GetScreenStateTool(BaseTool):
    name = "computer.get_screen_state"
    description = (
        "Desktop UI observation (windows/apps). Do NOT use this to learn the current "
        "browser URL or title — use browser session state. include_uia is expensive; "
        "leave it false unless cheaper observation failed."
    )
    permission_level = PermissionLevel.OBSERVE
    parameters_model = ScreenStateParams

    def __init__(self, service: ScreenUnderstandingService):
        self._service = service

    def execute(
        self,
        include_screenshot: bool = False,
        include_uia: bool = False,
        **kwargs: Any,
    ) -> ToolResult:
        try:
            state = self._service.get_state(
                include_screenshot=include_screenshot,
                include_uia=include_uia,
            )
            return ToolResult(
                success=True,
                data={
                    "summary": state.summary(),
                    "state": state.model_dump(mode="json"),
                },
            )
        except Exception as e:
            return ToolResult(success=False, error=str(e))


class VerifyWindowTool(BaseTool):
    name = "computer.verify_window"
    description = (
        "VERIFY that a window whose title contains the given text is visible after an action."
    )
    permission_level = PermissionLevel.OBSERVE
    parameters_model = VerifyWindowParams

    def __init__(self, service: ScreenUnderstandingService):
        self._service = service

    def execute(self, title_contains: str = "", **kwargs: Any) -> ToolResult:
        try:
            result = self._service.verify_window_appeared(title_contains)
            return ToolResult(success=True, data=result)
        except Exception as e:
            return ToolResult(success=False, error=str(e))


class VerifyElementTool(BaseTool):
    name = "computer.verify_element"
    description = (
        "VERIFY that an interactive UI element (button/edit/etc.) is present on screen "
        "via accessibility after an action."
    )
    permission_level = PermissionLevel.OBSERVE
    parameters_model = VerifyElementParams

    def __init__(self, service: ScreenUnderstandingService):
        self._service = service

    def execute(
        self, name_contains: str = "", kind: Optional[str] = None, **kwargs: Any
    ) -> ToolResult:
        try:
            result = self._service.verify_element_present(name_contains, kind=kind)
            return ToolResult(success=True, data=result)
        except Exception as e:
            return ToolResult(success=False, error=str(e))


def build_screen_tools(service: ScreenUnderstandingService) -> list[BaseTool]:
    return [
        GetScreenStateTool(service),
        VerifyWindowTool(service),
        VerifyElementTool(service),
    ]
