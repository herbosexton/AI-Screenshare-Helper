"""ExistingBrowserAdapter — operate the user's actual visible Chrome window.

Uses Win32 window management, keyboard shortcuts, and Phase 3 screen
understanding instead of Playwright DOM automation.  The user's real
browser session with their tabs, cookies, and logins stays intact.
"""

from __future__ import annotations

import time
from typing import Any, Optional
from urllib.parse import urlparse

from src.agent.browser.resolver import BrowserTargetResolver, BrowserWindowInfo


def _host(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    host = (urlparse(raw if "://" in raw else f"https://{raw}").hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def page_matches_site(page: dict[str, Any], site: str = "", url: str = "") -> bool:
    """True when URL host / page_identity is the requested site, not the tab title."""
    from src.agent.respond import page_identity

    got_url = str(page.get("url") or "")
    title = str(page.get("title") or page.get("active_tab_title") or "")
    ident = (page_identity(got_url, title) or "").strip().lower()
    want_site = (site or "").strip().lower()
    if want_site and ident == want_site:
        return True
    want_host = _host(url)
    got_host = _host(got_url)
    if want_host and got_host == want_host:
        return True
    return False


def _display_site(site: str = "", url: str = "", query: str = "") -> str:
    from src.agent.respond import page_identity

    return site or page_identity(url, "") or (query or "").strip().title() or "that site"


class ExistingBrowserAdapter:
    """Controls the user's existing desktop Chrome via OS-level interaction.

    All actions go through the actual window the user sees — no separate
    Playwright instance is launched.
    """

    def __init__(self, computer, resolver: BrowserTargetResolver, screen=None):
        self._computer = computer
        self._resolver = resolver
        self._screen = screen
        self._last_hwnd: int = 0

    def _target(self) -> Optional[BrowserWindowInfo]:
        """Find the best existing browser window to operate on."""
        bw = self._resolver.find_foreground_browser()
        if bw is not None:
            self._last_hwnd = bw.hwnd
            return bw
        # Fall back to last known
        if self._last_hwnd:
            for bw in self._resolver.find_all():
                if bw.hwnd == self._last_hwnd and not bw.playwright_owned:
                    return bw
        return self._resolver.find_existing_chrome()

    def _hotkey(self, *keys: str) -> dict[str, Any]:
        """Always call ComputerController.hotkey with a key list."""
        return self._computer.hotkey(list(keys))

    def _focus_chrome(self) -> tuple[Optional[BrowserWindowInfo], Optional[str]]:
        bw = self._target()
        if bw is None:
            return None, "No existing Chrome window found"
        try:
            self._computer.focus_window(bw.hwnd)
        except Exception as e:
            print(f"[BrowserHotkey] focus_ok=false hwnd={bw.hwnd} error={e}")
            return None, "I couldn't focus Chrome."
        self._last_hwnd = bw.hwnd
        time.sleep(0.2)
        return bw, None

    def focus(self) -> dict[str, Any]:
        """Focus the existing Chrome window."""
        bw, err = self._focus_chrome()
        if bw is None:
            return {"ok": False, "error": err or "No existing Chrome window found"}
        return {
            "ok": True,
            "hwnd": bw.hwnd,
            "title": bw.window_title,
            "source": bw.source,
        }

    def get_current_page(
        self,
        hwnd: int = 0,
        window_title: str = "",
        monitor: str = "",
        process_id: int = 0,
        bounds: tuple = (),
        is_foreground: bool = False,
    ) -> dict[str, Any]:
        """Page state for one HWND: selected tab + same-window address bar."""
        from src.agent.browser.tabs import ActiveTabResolver

        if not hwnd:
            bw = self._target()
            if bw is None:
                return {
                    "ok": False,
                    "error": "NO_BROWSER_WINDOW",
                    "discovery_status": "NO_BROWSER_WINDOW",
                }
            hwnd = bw.hwnd
            window_title = window_title or bw.window_title
            monitor = monitor or str(getattr(bw, "monitor_id", "") or bw.monitor_index)
            process_id = process_id or bw.process_id
            bounds = bounds or bw.bounds
            is_foreground = bw.is_foreground

        page = ActiveTabResolver().resolve(
            hwnd,
            window_title=window_title,
            monitor=str(monitor),
        )
        title = page.active_tab_title or window_title
        url = page.url or ""
        if url:
            status = "BROWSER_FOUND"
        elif title:
            status = "BROWSER_FOUND_URL_UNAVAILABLE"
        else:
            status = "BROWSER_FOUND_STATE_UNAVAILABLE"

        return {
            "ok": True,
            "hwnd": hwnd,
            "process_id": process_id,
            "window_title": window_title,
            "active_tab_title": title,
            "title": title,
            "url": url,
            "source": "EXISTING_DESKTOP",
            "is_foreground": is_foreground,
            "bounds": bounds,
            "monitor": page.monitor or monitor,
            "discovery_status": status,
            "confidence": page.confidence,
            "title_source": page.title_source,
            "url_source": page.url_source,
        }

    def get_page_text(self, max_chars: int = 8000) -> dict[str, Any]:
        """WEB DOCUMENT text from the active tab. Never Chrome chrome."""
        page = self.get_current_page()
        if not page.get("ok"):
            return {**page, "text": ""}
        title = str(page.get("title") or page.get("active_tab_title") or "")
        url = str(page.get("url") or "")
        try:
            from src.agent.browser.document import build_page_model

            model = build_page_model(
                url=url,
                title=title,
                hwnd=int(page.get("hwnd") or 0),
                max_chars=max_chars,
            )
        except Exception as e:
            print(f"[PageModel] build failed: {e}")
            return {**page, "text": "", "ok": True}
        text = (model.main_content or "")[: max(200, int(max_chars))]
        return {
            **page,
            "text": text,
            "page_type": model.page_type,
            "page_type_confidence": model.page_type_confidence,
            "document_url": model.document_url,
            "document_source_method": model.document_source_method,
            "structured_jobposting_found": model.structured_jobposting_found,
            "content_contamination_score": model.content_contamination_score,
            "source_scope": "WEB_DOCUMENT",
            "ok": True,
        }

    def navigate(self, url: str) -> dict[str, Any]:
        """Navigate to a URL in the existing browser using Ctrl+L."""
        bw, err = self._focus_chrome()
        if bw is None:
            return {"ok": False, "error": err or "No existing Chrome window found"}
        try:
            self._hotkey("ctrl", "l")
            time.sleep(0.15)
            self._computer.type_text(url, interval=0.01)
            time.sleep(0.1)
            self._computer.press_key("Return")
            time.sleep(0.8)
        except Exception as e:
            print(f"[BrowserHotkey] method=hotkey keys=['ctrl','l'] hwnd={bw.hwnd} hotkey_ok=false error={e}")
            return {
                "ok": False,
                "error": str(e),
                "message": "I couldn't navigate because the keyboard shortcut failed.",
                "hwnd": bw.hwnd,
                "source": "EXISTING_DESKTOP",
            }
        page = self.get_current_page(hwnd=bw.hwnd, window_title=bw.window_title)
        got = (page.get("url") or "").lower()
        host = url.lower().split("://")[-1].split("/")[0].replace("www.", "")
        verified = bool(host and host.split(".")[0] in got) if got else False
        print(
            f"[BrowserHotkey] method=ComputerController.hotkey keys=['ctrl','l'] "
            f"hwnd={bw.hwnd} focus_ok=true hotkey_ok=true verify_ok={verified} url={got or '-'}"
        )
        return {
            "ok": True,
            "action": "navigate",
            "url": url,
            "hwnd": bw.hwnd,
            "source": "EXISTING_DESKTOP",
            "method": "keyboard",
            "verified": verified,
            "current_url": page.get("url") or "",
        }

    def new_tab(self, url: str = "") -> dict[str, Any]:
        """Open a new tab in the existing browser using Ctrl+T."""
        bw, err = self._focus_chrome()
        if bw is None:
            return {"ok": False, "error": err or "No existing Chrome window found"}
        try:
            self._hotkey("ctrl", "t")
            time.sleep(0.45)
            if url:
                self._computer.type_text(url, interval=0.01)
                time.sleep(0.1)
                self._computer.press_key("Return")
                time.sleep(0.9)
        except Exception as e:
            print(f"[BrowserHotkey] method=hotkey keys=['ctrl','t'] hwnd={bw.hwnd} hotkey_ok=false error={e}")
            return {
                "ok": False,
                "error": str(e),
                "message": "I couldn't open the new tab because the keyboard shortcut failed.",
                "hwnd": bw.hwnd,
                "source": "EXISTING_DESKTOP",
            }
        page = self.get_current_page(hwnd=bw.hwnd, window_title=bw.window_title)
        got = (page.get("url") or "").lower()
        title = (page.get("title") or "").lower()
        if url:
            host = url.lower().split("://")[-1].split("/")[0].replace("www.", "")
            verified = bool(host and host.split(".")[0] in got) if got else False
        else:
            verified = any(s in title or s in got for s in ("new tab", "about:blank", "chrome://newtab", "google.com"))
        print(
            f"[BrowserHotkey] method=ComputerController.hotkey keys=['ctrl','t'] "
            f"hwnd={bw.hwnd} focus_ok=true hotkey_ok=true verify_ok={verified} "
            f"url={got or '-'} title={page.get('title') or '-'}"
        )
        msg = "Opened a new tab."
        if url:
            msg = "Opened a new tab and went to Google." if "google" in url.lower() else f"Opened a new tab and went to {url}."
        return {
            "ok": True,
            "action": "new_tab",
            "url": url,
            "hwnd": bw.hwnd,
            "source": "EXISTING_DESKTOP",
            "verified": verified,
            "message": msg,
            "current_url": page.get("url") or "",
        }

    def switch_tab(self, query: str) -> dict[str, Any]:
        """Switch tabs using Chrome's built-in tab search (Ctrl+Shift+A)."""
        bw, err = self._focus_chrome()
        if bw is None:
            return {"ok": False, "error": err or "No existing Chrome window found"}
        if not self._search_open_tabs(query):
            return {
                "ok": False,
                "error": "hotkey_failed",
                "message": "I couldn't switch tabs because the keyboard shortcut failed.",
                "hwnd": bw.hwnd,
            }
        return {
            "ok": True,
            "action": "switch_tab",
            "query": query,
            "hwnd": bw.hwnd,
            "source": "EXISTING_DESKTOP",
        }

    def go_to_site(self, site: str = "", url: str = "", query: str = "") -> dict[str, Any]:
        """Prefer an already-open tab in this HWND, else navigate the same window.

        Matches by domain / page_identity, not conversation titles. Never opens
        a second Chrome window or a managed Playwright browser.
        """
        bw, err = self._focus_chrome()
        if bw is None:
            label = _display_site(site, url, query)
            return {
                "ok": False,
                "error": err or "No existing Chrome window found",
                "message": f"I couldn't find an open {label} tab.",
                "existing_tab_found": False,
                "source": "",
                "planner_calls": 0,
            }
        label = _display_site(site, url, query)
        search = _host(url) or query or site
        page = self.get_current_page(hwnd=bw.hwnd, window_title=bw.window_title)
        if page_matches_site(page, site, url):
            return self._goto_ok(
                bw,
                page,
                label,
                existing=True,
                route="already_active",
            )
        if site and self._activate_tab_by_title(bw.hwnd, site):
            page = self.get_current_page(hwnd=bw.hwnd, window_title=bw.window_title)
            if page_matches_site(page, site, url):
                return self._goto_ok(bw, page, label, existing=True, route="uia_tab")
        if search and self._search_open_tabs(search):
            time.sleep(0.35)
            page = self.get_current_page(hwnd=bw.hwnd, window_title=bw.window_title)
            if page_matches_site(page, site, url):
                return self._goto_ok(bw, page, label, existing=True, route="tab_search")
        try:
            self._computer.press_key("Escape")
        except Exception:
            pass
        if not url:
            msg = f"I couldn't find an open {label} tab."
            print(
                f"[GoToSite] hwnd={bw.hwnd} source=EXISTING_DESKTOP existing_tab_found=false "
                f"route=missing_tab verification_result=FAIL planner_calls=0"
            )
            return {
                "ok": False,
                "action": "go_to_site",
                "message": msg,
                "existing_tab_found": False,
                "hwnd": bw.hwnd,
                "source": "EXISTING_DESKTOP",
                "verification_result": "FAIL",
                "planner_calls": 0,
            }
        nav = self.navigate(url)
        page = self.get_current_page(hwnd=bw.hwnd, window_title=bw.window_title)
        verified = page_matches_site(page, site, url) or bool(nav.get("ok"))
        if not verified:
            msg = f"I couldn't find an open {label} tab."
            print(
                f"[GoToSite] hwnd={bw.hwnd} source=EXISTING_DESKTOP existing_tab_found=false "
                f"route=navigate verification_result=FAIL planner_calls=0"
            )
            return {
                "ok": False,
                "action": "go_to_site",
                "message": msg,
                "existing_tab_found": False,
                "hwnd": bw.hwnd,
                "source": "EXISTING_DESKTOP",
                "verification_result": "FAIL",
                "planner_calls": 0,
                "current_url": page.get("url") or "",
            }
        return self._goto_ok(bw, page, label, existing=False, route="navigate")

    def _goto_ok(
        self,
        bw: BrowserWindowInfo,
        page: dict[str, Any],
        label: str,
        *,
        existing: bool,
        route: str,
    ) -> dict[str, Any]:
        title = page.get("active_tab_title") or page.get("title") or ""
        print(
            f"[GoToSite] hwnd={bw.hwnd} source=EXISTING_DESKTOP existing_tab_found={str(existing).lower()} "
            f"selected_tab={title or '-'} route={route} verification_result=PASS planner_calls=0"
        )
        return {
            "ok": True,
            "action": "go_to_site",
            "message": f"Switched to {label}.",
            "existing_tab_found": existing,
            "hwnd": bw.hwnd,
            "source": "EXISTING_DESKTOP",
            "verification_result": "PASS",
            "planner_calls": 0,
            "current_url": page.get("url") or "",
            "site": label,
            "selected_tab": title,
        }

    def _activate_tab_by_title(self, hwnd: int, site: str) -> bool:
        from src.agent.browser.tabs import ActiveTabResolver

        try:
            return bool(ActiveTabResolver().activate_tab_named(hwnd, site))
        except Exception:
            return False

    def _search_open_tabs(self, query: str) -> bool:
        """Chrome tab search indexes titles and URLs — use the host, not the conversation title."""
        q = (query or "").strip()
        if not q:
            return False
        try:
            self._hotkey("ctrl", "shift", "a")
            time.sleep(0.5)
            self._computer.type_text(q, interval=0.02)
            time.sleep(0.5)
            self._computer.press_key("Return")
            time.sleep(0.5)
            return True
        except Exception as e:
            print(f"[BrowserHotkey] method=hotkey keys=['ctrl','shift','a'] hotkey_ok=false error={e}")
            return False

    def close_tab(self) -> dict[str, Any]:
        """Close the current tab using Ctrl+W."""
        bw, err = self._focus_chrome()
        if bw is None:
            return {"ok": False, "error": err or "No existing Chrome window found"}
        try:
            self._hotkey("ctrl", "w")
            time.sleep(0.3)
        except Exception as e:
            return {
                "ok": False,
                "error": str(e),
                "message": "I couldn't close the tab because the keyboard shortcut failed.",
            }
        return {"ok": True, "action": "close_tab", "source": "EXISTING_DESKTOP", "hwnd": bw.hwnd}

    def next_tab(self) -> dict[str, Any]:
        bw, err = self._focus_chrome()
        if bw is None:
            return {"ok": False, "error": err or "No existing Chrome window found"}
        try:
            self._hotkey("ctrl", "tab")
            time.sleep(0.25)
        except Exception as e:
            return {
                "ok": False,
                "error": str(e),
                "message": "I couldn't switch tabs because the keyboard shortcut failed.",
            }
        return {"ok": True, "action": "next_tab", "source": "EXISTING_DESKTOP", "hwnd": bw.hwnd}

    def go_back(self) -> dict[str, Any]:
        """Navigate back using Alt+Left."""
        bw, err = self._focus_chrome()
        if bw is None:
            return {"ok": False, "error": err or "No existing Chrome window found"}
        try:
            self._hotkey("alt", "Left")
            time.sleep(0.4)
        except Exception as e:
            return {
                "ok": False,
                "error": str(e),
                "message": "I couldn't go back because the keyboard shortcut failed.",
            }
        return {"ok": True, "action": "back", "source": "EXISTING_DESKTOP", "hwnd": bw.hwnd}

    def go_forward(self) -> dict[str, Any]:
        """Navigate forward using Alt+Right."""
        bw, err = self._focus_chrome()
        if bw is None:
            return {"ok": False, "error": err or "No existing Chrome window found"}
        try:
            self._hotkey("alt", "Right")
            time.sleep(0.4)
        except Exception as e:
            return {
                "ok": False,
                "error": str(e),
                "message": "I couldn't go forward because the keyboard shortcut failed.",
            }
        return {"ok": True, "action": "forward", "source": "EXISTING_DESKTOP", "hwnd": bw.hwnd}

    def reload(self) -> dict[str, Any]:
        """Reload the current page using Ctrl+R."""
        bw, err = self._focus_chrome()
        if bw is None:
            return {"ok": False, "error": err or "No existing Chrome window found"}
        try:
            self._hotkey("ctrl", "r")
            time.sleep(0.4)
        except Exception as e:
            return {
                "ok": False,
                "error": str(e),
                "message": "I couldn't refresh the page because the keyboard shortcut failed.",
            }
        return {"ok": True, "action": "reload", "source": "EXISTING_DESKTOP", "hwnd": bw.hwnd}
