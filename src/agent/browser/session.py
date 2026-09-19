"""Compatibility facade. New code should use BrowserAgent."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from src.agent.browser.agent import BrowserAgent
from src.agent.browser.driver import PlaywrightBrowserDriver
from src.agent.browser.errors import BrowserError, BrowserUnavailable
from src.agent.browser.policy import UrlPolicy
from src.agent.files.permissions import FilePathPolicy

__all__ = ["BrowserSession", "BrowserError", "BrowserUnavailable"]


class BrowserSession:
    def __init__(
        self,
        *,
        policy: UrlPolicy,
        profile_dir: Path,
        headed: bool = True,
        persist_profile: bool = True,
        navigation_timeout_ms: int = 20000,
        channel: str = "",
        file_policy: Optional[FilePathPolicy] = None,
        computer=None,
        screen=None,
        on_status=None,
    ):
        self.policy = policy
        self._agent = BrowserAgent(
            policy=policy,
            profile_dir=profile_dir,
            headed=headed,
            persist_profile=persist_profile,
            navigation_timeout_ms=navigation_timeout_ms,
            channel=channel,
            file_policy=file_policy,
            computer=computer,
            screen=screen,
            on_status=on_status,
        )

    def _import_playwright(self):
        return PlaywrightBrowserDriver._import_playwright(self._agent.driver)

    @property
    def agent(self) -> BrowserAgent:
        self._agent.driver._import_playwright = self._import_playwright
        return self._agent

    def ensure(self):
        return self.agent._ensure()

    def close(self) -> dict[str, Any]:
        return self.agent.close()

    def status(self) -> dict[str, Any]:
        return self.agent.status()

    def goto(self, url: str) -> dict[str, Any]:
        return self.agent.goto(url)

    def snapshot(self, limit: int = 80) -> dict[str, Any]:
        return self.agent.snapshot(limit=limit)

    def get_text(self, max_chars: int = 8000) -> dict[str, Any]:
        return self.agent.get_text(max_chars=max_chars)

    def screenshot(self) -> dict[str, Any]:
        return self.agent.screenshot()

    def click(self, ref: str = "", selector: str = "", name: str = "") -> dict[str, Any]:
        result = self.agent.click(ref=ref, selector=selector, name=name)
        if result.get("success") is False:
            raise BrowserError(result.get("error", {}).get("message") or "click failed")
        return result

    def type_text(self, text: str, ref: str = "", selector: str = "", name: str = "") -> dict[str, Any]:
        result = self.agent.fill(text, ref=ref, selector=selector, name=name)
        if result.get("success") is False:
            raise BrowserError(result.get("error", {}).get("message") or "type failed")
        return result

    def press(self, key: str) -> dict[str, Any]:
        return self.agent.press(key)

    def wait(self, selector: str = "", timeout_ms: int = 5000) -> dict[str, Any]:
        return self.agent.wait(selector=selector, timeout_ms=timeout_ms)

    def new_tab(self, url: str = "") -> dict[str, Any]:
        return self.agent.new_tab(url=url)

    def close_tab(self) -> dict[str, Any]:
        return self.agent.close_tab()
