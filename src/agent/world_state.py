"""Lightweight cached world state. Updated from tool/browser events, not rediscovered every command."""

from __future__ import annotations

from typing import Any, Optional


class WorldState:
    def __init__(self):
        self.browser: dict[str, Any] = {
            "open": False,
            "sessionId": "",
            "browserType": "",
            "url": "",
            "title": "",
            "tabs": 0,
            "activeTabId": "",
            "lastNavigation": "",
            "pageFingerprint": "",
            "connectionStatus": "closed",
        }
        self.active_window: dict[str, Any] = {}
        self.last_action: str = ""

    def update_browser(self, data: Optional[dict[str, Any]]) -> None:
        if not data:
            return
        if data.get("open") is False or data.get("action") == "close":
            self.browser.update(
                {
                    "open": False,
                    "url": "",
                    "title": "",
                    "tabs": 0,
                    "activeTabId": "",
                    "connectionStatus": "closed",
                    "pageFingerprint": "",
                }
            )
            return
        url = data.get("url") or data.get("currentUrl") or self.browser.get("url") or ""
        title = data.get("title") or data.get("currentTitle") or self.browser.get("title") or ""
        self.browser.update(
            {
                "open": True,
                "sessionId": data.get("sessionId") or self.browser.get("sessionId") or "",
                "browserType": data.get("browserType") or self.browser.get("browserType") or "",
                "url": url,
                "title": title,
                "tabs": data.get("tabs") if data.get("tabs") is not None else self.browser.get("tabs") or 0,
                "activeTabId": data.get("id") or data.get("activeTabId") or self.browser.get("activeTabId") or "",
                "lastNavigation": data.get("lastNavigation") or self.browser.get("lastNavigation") or "",
                "pageFingerprint": data.get("pageFingerprint") or self.browser.get("pageFingerprint") or "",
                "connectionStatus": data.get("connectionStatus") or "active",
            }
        )
        if isinstance(data.get("data"), dict):
            bt = data["data"].get("browserType")
            if bt:
                self.browser["browserType"] = bt

    def on_browser_event(self, name: str, data: Optional[dict[str, Any]] = None) -> None:
        self.last_action = name
        self.update_browser(data)

    def on_computer_event(self, name: str, data: Optional[dict[str, Any]] = None) -> None:
        self.last_action = name
        payload = data or {}
        win = payload.get("window") if isinstance(payload.get("window"), dict) else payload
        if isinstance(win, dict) and (win.get("title") or win.get("handle") or win.get("process_name")):
            self.active_window = win

    def snapshot(self) -> dict[str, Any]:
        return {
            "browser": dict(self.browser),
            "active_window": dict(self.active_window),
            "last_action": self.last_action,
        }
