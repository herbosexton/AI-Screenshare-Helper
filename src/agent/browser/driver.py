"""Playwright driver. The rest of Jarvis depends on this interface, not Playwright types."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Optional, Protocol

from src.agent.browser.errors import (
    BROWSER_NOT_RUNNING,
    NAVIGATION_TIMEOUT,
    PAGE_LOAD_TIMEOUT,
    SESSION_DISCONNECTED,
    UNSUPPORTED_BROWSER,
    BrowserError,
    BrowserUnavailable,
)


class BrowserDriver(Protocol):
    def launch(self) -> Any: ...
    def close(self) -> None: ...
    def pages(self) -> list[Any]: ...
    def new_page(self) -> Any: ...


class PlaywrightBrowserDriver:
    """Headed Chrome, then Edge, then bundled Chromium. Isolated profile only."""

    def __init__(
        self,
        *,
        profile_dir: Path,
        headed: bool = True,
        persist_profile: bool = True,
        navigation_timeout_ms: int = 20000,
        channel: str = "",
        downloads_dir: Optional[Path] = None,
    ):
        self.profile_dir = Path(profile_dir)
        self.headed = headed
        self.persist_profile = persist_profile
        self.navigation_timeout_ms = navigation_timeout_ms
        self.channel_preference = (channel or "").strip().lower()
        self.downloads_dir = Path(downloads_dir) if downloads_dir else self.profile_dir.parent / "browser_downloads"
        self._lock = threading.RLock()
        self._playwright = None
        self._context = None
        self._browser = None
        self.browser_type = "chromium"
        self.status = "closed"

    def _import_playwright(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as e:
            raise BrowserUnavailable(
                "Playwright is not installed. Run: pip install playwright && playwright install chromium",
                code=UNSUPPORTED_BROWSER,
            ) from e
        return sync_playwright

    def _channel_order(self) -> list[Optional[str]]:
        preferred = self.channel_preference
        order: list[Optional[str]] = []
        if preferred in {"chrome", "msedge", "chromium"}:
            order.append(None if preferred == "chromium" else preferred)
        for ch in ("chrome", "msedge", None):
            if ch not in order:
                order.append(ch)
        return order

    def launch(self):
        with self._lock:
            if self._context is not None:
                return self._context
            self.status = "starting"
            # Relaunching after the window was closed reuses the driver we already
            # started. A second sync_playwright() in the same thread is an error.
            if self._playwright is None:
                sync_playwright = self._import_playwright()
                self._playwright = sync_playwright().start()
            chromium = self._playwright.chromium
            last_error: Optional[Exception] = None
            for channel in self._channel_order():
                try:
                    self._launch_channel(chromium, channel)
                    self.browser_type = channel or "chromium"
                    self.status = "active"
                    return self._context
                except Exception as e:
                    last_error = e
                    self._context = None
                    if self._browser is not None:
                        try:
                            self._browser.close()
                        except Exception:
                            pass
                        self._browser = None
                    continue
            self.status = "error"
            self.close()
            raise BrowserUnavailable(
                f"Chromium is not available. Run: playwright install chromium ({last_error})",
                code=UNSUPPORTED_BROWSER,
            )

    def _launch_channel(self, chromium, channel: Optional[str]) -> None:
        self.downloads_dir.mkdir(parents=True, exist_ok=True)
        extra: dict[str, Any] = {
            "ignore_default_args": ["--enable-automation", "--no-sandbox"],
            "args": ["--disable-blink-features=AutomationControlled"],
        }
        if channel:
            extra["channel"] = channel
        if self.persist_profile:
            self.profile_dir.mkdir(parents=True, exist_ok=True)
            self._context = chromium.launch_persistent_context(
                str(self.profile_dir),
                headless=not self.headed,
                viewport={"width": 1280, "height": 800},
                accept_downloads=True,
                downloads_path=str(self.downloads_dir),
                **extra,
            )
            if not self._context.pages:
                self._context.new_page()
        else:
            browser_kw: dict[str, Any] = {
                "headless": not self.headed,
                "downloads_path": str(self.downloads_dir),
            }
            browser_kw.update(extra)
            self._browser = chromium.launch(**browser_kw)
            self._context = self._browser.new_context(
                viewport={"width": 1280, "height": 800},
                accept_downloads=True,
            )
            self._context.new_page()
        for page in self._context.pages:
            page.set_default_timeout(self.navigation_timeout_ms)

    def close(self) -> None:
        with self._lock:
            for obj_name in ("_context", "_browser", "_playwright"):
                obj = getattr(self, obj_name, None)
                setattr(self, obj_name, None)
                if obj is None:
                    continue
                try:
                    if obj_name == "_playwright":
                        obj.stop()
                    else:
                        obj.close()
                except Exception:
                    pass
            self.status = "closed"

    def context(self):
        if self._context is None:
            raise BrowserError("Browser is not running", BROWSER_NOT_RUNNING, retryable=False)
        return self._context

    def pages(self) -> list[Any]:
        ctx = self.context()
        try:
            return list(ctx.pages)
        except Exception as e:
            raise BrowserError("Browser session disconnected", SESSION_DISCONNECTED) from e

    def new_page(self):
        page = self.context().new_page()
        page.set_default_timeout(self.navigation_timeout_ms)
        return page

    def goto(self, page, url: str):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=self.navigation_timeout_ms)
        except Exception as e:
            name = type(e).__name__
            if "Timeout" in name:
                raise BrowserError(f"Navigation timed out: {url}", NAVIGATION_TIMEOUT) from e
            raise BrowserError(str(e), PAGE_LOAD_TIMEOUT) from e
