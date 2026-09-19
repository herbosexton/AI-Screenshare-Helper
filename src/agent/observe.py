"""Observation levels. Escalate only when cheaper state cannot answer."""

from __future__ import annotations

import logging
import time
from typing import Any


LEVEL_0 = 0  # cached window/browser
LEVEL_1 = 1  # structured windows + UIA
LEVEL_2 = 2  # targeted vision
LEVEL_3 = 3  # full-monitor vision
LEVEL_A = "A"  # cached browser/window state
LEVEL_B = "B"  # DOM / targeted accessibility
LEVEL_C = "C"  # full UIA, screenshot, vision, OCR

log = logging.getLogger("jarvis.observe")

_LAST_PAGE_STATE: dict[str, Any] = {}
_LAST_PAGE_AT: float = 0.0
_LAST_PAGE_TTL_S = 300.0


def remember_page_state(state: dict[str, Any] | None) -> None:
    global _LAST_PAGE_STATE, _LAST_PAGE_AT
    if not state or not (state.get("open") or state.get("url") or state.get("title")):
        return
    _LAST_PAGE_STATE = dict(state)
    _LAST_PAGE_AT = time.time()


def last_page_state() -> dict[str, Any]:
    if not _LAST_PAGE_STATE:
        return {}
    if (time.time() - _LAST_PAGE_AT) > _LAST_PAGE_TTL_S:
        return {}
    return dict(_LAST_PAGE_STATE)


def _desktop_page_state(utterance: str = "", *, remember: bool = True) -> dict[str, Any]:
    from src.agent.browser.discovery import BrowserWindowRegistry, parse_preferred_browser
    from src.agent.browser.existing import ExistingBrowserAdapter
    from src.agent.browser.resolver import BrowserTargetResolver
    from src.agent.computer import ComputerController

    preferred = parse_preferred_browser(utterance or "")
    computer = ComputerController()
    registry = BrowserWindowRegistry()
    resolver = BrowserTargetResolver(computer=computer, registry=registry, use_native=True)
    adapter = ExistingBrowserAdapter(computer, resolver)
    state = cheapest_web_state(
        None,
        resolver=resolver,
        existing_adapter=adapter,
        preferred_browser=preferred,
    )
    if remember:
        remember_page_state(state)
    return state


def cheapest_web_state(
    browser_agent,
    world=None,
    *,
    existing_adapter=None,
    resolver=None,
    preferred_browser: str = "",
) -> dict[str, Any]:
    """Prefer the user's existing desktop browser. Never invent about:blank.

    Discovery and Playwright control are separate. A normal Chrome window is
    a valid target even when Playwright does not own it.
    """
    if resolver is not None:
        try:
            target = (
                resolver.resolve_target(log=True, preferred_browser=preferred_browser)
                if hasattr(resolver, "resolve_target")
                else resolver.find_foreground_browser()
            )
        except Exception as exc:
            print(f"[BrowserDiscovery] resolver error: {exc}")
            log.warning("BrowserTargetResolver failed: %s", exc)
            target = None

        if target is not None and not target.playwright_owned:
            title = target.active_tab_title or target.window_title
            url = target.url_if_known or ""
            status = "BROWSER_FOUND" if (url or title) else "BROWSER_FOUND_STATE_UNAVAILABLE"
            title_source = "WINDOW_TITLE_FALLBACK"
            url_source = ""
            if existing_adapter is not None:
                try:
                    page_info = existing_adapter.get_current_page(
                        hwnd=target.hwnd,
                        window_title=target.window_title,
                        monitor=str(getattr(target, "monitor_id", "") or target.monitor_index),
                        process_id=target.process_id,
                        bounds=target.bounds,
                        is_foreground=target.is_foreground,
                    )
                    if page_info.get("ok"):
                        url = page_info.get("url") or url
                        title = page_info.get("active_tab_title") or page_info.get("title") or title
                        status = page_info.get("discovery_status") or status
                        title_source = page_info.get("title_source") or title_source
                        url_source = page_info.get("url_source") or url_source
                except Exception as exc:
                    print(f"[BrowserDiscovery] page-state error: {exc}")
            if url:
                status = "BROWSER_FOUND"
            elif title:
                status = "BROWSER_FOUND_URL_UNAVAILABLE"
            reason = getattr(target, "selection_reason", "") or "EXISTING_DESKTOP"
            print(
                "[BrowserState]\n"
                f"title={title}\n"
                f"title_source={title_source}\n"
                f"url={url or '(unavailable)'}\n"
                f"url_source={url_source or '-'}\n"
                f"status={status}\n"
                f"hwnd={target.hwnd}\n"
                f"monitor={getattr(target, 'monitor_id', '') or target.monitor_index}"
            )
            return {
                "open": True,
                "url": url,
                "title": title,
                "connectionStatus": "existing_desktop",
                "browser_target_source": "EXISTING_DESKTOP",
                "browser_hwnd": target.hwnd,
                "browser_pid": target.process_id,
                "browser_monitor": getattr(target, "monitor_id", "") or target.monitor_index,
                "browser_window_title": target.window_title,
                "browser_selection_reason": reason,
                "discovery_status": status,
                "source": "EXISTING_DESKTOP",
                "process_name": target.process_name,
                "exe_path": getattr(target, "exe_path", "") or target.process_name,
                "bounds": target.bounds,
                "title_source": title_source,
                "url_source": url_source,
            }

        # Desktop scan ran and found nothing. Do not pretend Playwright is the page.
        return {
            "open": False,
            "url": "",
            "title": "",
            "browser_target_source": "NONE",
            "browser_selection_reason": "NO_BROWSER_WINDOW",
            "discovery_status": "NO_BROWSER_WINDOW",
        }

    if browser_agent is not None and hasattr(browser_agent, "get_session_state"):
        pw_state = browser_agent.get_session_state()
        pw_state["browser_target_source"] = "MANAGED_PLAYWRIGHT"
        pw_state["browser_selection_reason"] = "PLAYWRIGHT_SESSION"
        pw_state["discovery_status"] = "BROWSER_FOUND" if pw_state.get("open") else "NO_BROWSER_WINDOW"
        return pw_state

    if world is not None:
        snap = world.snapshot()
        return snap.get("browser") or {"open": False, "discovery_status": "NO_BROWSER_WINDOW"}
    return {"open": False, "discovery_status": "NO_BROWSER_WINDOW"}


def speak_current_page(utterance: str = "") -> str:
    """Spoken site identity from the existing desktop browser. No planner."""
    from src.agent.respond import format_current_page

    return format_current_page(_desktop_page_state(utterance))


def speak_page_entity(utterance: str = "", kind: str = "course") -> str:
    """Spoken course/article name from the existing desktop browser. No planner."""
    from src.agent.respond import format_page_entity

    return format_page_entity(_desktop_page_state(utterance), kind=kind)


def speak_page_about(utterance: str = "") -> str:
    """What the last discussed page is about. No planner."""
    from src.agent.respond import format_page_about, page_identity

    last = last_page_state()
    current = _desktop_page_state(utterance, remember=False)
    state = current
    if last.get("url") or last.get("title"):
        last_site = page_identity(str(last.get("url") or ""), str(last.get("title") or ""))
        now_site = page_identity(str(current.get("url") or ""), str(current.get("title") or ""))
        if last_site and now_site != last_site:
            state = last
        elif last.get("title"):
            state = last
    else:
        remember_page_state(current)
    return format_page_about(state=state)


def should_use_uia(question: str, browser_open: bool) -> bool:
    """UIA is not for URL/title/tab questions when a browser session exists."""
    t = (question or "").lower()
    web_q = any(k in t for k in ("page", "url", "website", "tab", "browser"))
    if web_q and browser_open:
        return False
    return False
