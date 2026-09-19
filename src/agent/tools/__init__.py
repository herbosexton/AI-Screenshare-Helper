from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

from src.agent.permissions import PermissionLevel
from src.agent.tools.base import BaseTool, ToolResult


class EmptyParams(BaseModel):
    pass


class ClipboardSetParams(BaseModel):
    text: str = Field(..., description="Text to place on the clipboard")


class SpeakParams(BaseModel):
    text: str = Field(..., description="Text to speak aloud")


class ScreenshotTool(BaseTool):
    name = "computer.get_screenshot"
    description = "Capture the current screen(s) and return metadata plus base64 PNG(s)."
    permission_level = PermissionLevel.OBSERVE
    parameters_model = EmptyParams
    max_retries = 1

    def __init__(self, screen_capture):
        self._screen = screen_capture

    def execute(self, **kwargs: Any) -> ToolResult:
        captures = self._screen.capture_all()
        payload = []
        for cap in captures:
            w, h = cap.get("size", (0, 0))
            payload.append(
                {
                    "monitor_index": cap.get("monitor_index"),
                    "width": w,
                    "height": h,
                    "base64_png": cap.get("base64", ""),
                }
            )
        return ToolResult(
            success=True,
            data={
                "monitor_count": len(payload),
                "screenshots": payload,
            },
        )


class ClipboardGetTool(BaseTool):
    name = "computer.get_clipboard"
    description = "Read the current system clipboard text."
    permission_level = PermissionLevel.OBSERVE
    parameters_model = EmptyParams

    def execute(self, **kwargs: Any) -> ToolResult:
        import pyperclip

        try:
            text = pyperclip.paste()
            return ToolResult(success=True, data={"text": text})
        except Exception as e:
            return ToolResult(success=False, error=str(e))


class ClipboardSetTool(BaseTool):
    name = "computer.set_clipboard"
    description = "Write text to the system clipboard."
    permission_level = PermissionLevel.LOCAL_ACTION
    parameters_model = ClipboardSetParams

    def __init__(self, clipboard_out=None):
        self._clipboard = clipboard_out

    def execute(self, text: str = "", **kwargs: Any) -> ToolResult:
        try:
            if self._clipboard is not None:
                self._clipboard.copy(text)
            else:
                import pyperclip

                pyperclip.copy(text)
            return ToolResult(success=True, data={"copied_chars": len(text)})
        except Exception as e:
            return ToolResult(success=False, error=str(e))


class ListToolsTool(BaseTool):
    name = "agent.list_tools"
    description = "List available agent tools and their permission levels."
    permission_level = PermissionLevel.OBSERVE
    parameters_model = EmptyParams

    def __init__(self, registry_getter):
        self._get_registry = registry_getter

    def execute(self, **kwargs: Any) -> ToolResult:
        registry = self._get_registry()
        tools = [
            {
                "name": t.name,
                "description": t.description,
                "permission_level": int(t.permission_level),
            }
            for t in registry.list_tools()
        ]
        return ToolResult(success=True, data={"tools": tools})


class AgentStatusTool(BaseTool):
    name = "agent.get_status"
    description = "Get current task status, emergency stop state, and autonomy mode."
    permission_level = PermissionLevel.OBSERVE
    parameters_model = EmptyParams

    def __init__(self, status_getter):
        self._get_status = status_getter

    def execute(self, **kwargs: Any) -> ToolResult:
        return ToolResult(success=True, data=self._get_status())


class SpeakTool(BaseTool):
    name = "speech.speak"
    description = "Speak text aloud using the local TTS engine."
    permission_level = PermissionLevel.LOCAL_ACTION
    parameters_model = SpeakParams

    def __init__(self, speech_out_getter):
        self._get_speech = speech_out_getter

    def execute(self, text: str = "", **kwargs: Any) -> ToolResult:
        speech = self._get_speech()
        if speech is None:
            return ToolResult(
                success=False,
                error="Speech output is not initialized",
            )
        try:
            speech.speak(text)
            return ToolResult(success=True, data={"spoken_chars": len(text)})
        except Exception as e:
            return ToolResult(success=False, error=str(e))


def build_phase1_tools(
    *,
    screen_capture,
    clipboard_out,
    speech_out_getter,
    registry_getter,
    status_getter,
) -> list[BaseTool]:
    return [
        ScreenshotTool(screen_capture),
        ClipboardGetTool(),
        ClipboardSetTool(clipboard_out),
        ListToolsTool(registry_getter),
        AgentStatusTool(status_getter),
        SpeakTool(speech_out_getter),
    ]
