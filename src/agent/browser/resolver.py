"""Element and tab scoring for browser tools.

Also provides BrowserTargetResolver for distinguishing existing desktop browsers
from JARVIS-managed Playwright sessions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional

from src.agent.browser.models import BrowserElement


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").strip().lower()).strip()


def score_element(el: BrowserElement, query: str, role: str = "") -> float:
    q = _norm(query)
    if not q:
        return 0.0
    qw = set(q.split())
    s = 0.0
    name = _norm(el.name)
    text = _norm(el.text)
    aria = _norm(el.aria_label)
    placeholder = _norm(el.placeholder)
    eid = _norm(el.selector.lstrip("#"))
    if q == name or q == text:
        s += 1.0
    elif q == aria or q == placeholder:
        s += 0.95
    else:
        best = 0.0
        for surface in (name, text, aria, placeholder, eid):
            if not surface:
                continue
            if q in surface:
                best = max(best, 0.8)
            elif surface in q:
                best = max(best, 0.5)
            else:
                sw = set(surface.split())
                overlap = qw & sw
                if overlap:
                    best = max(best, len(overlap) / max(len(qw), 1) * 0.6)
        s += best
    if role:
        if el.role == role or (role in (el.tag or "")):
            s *= 1.1
    if not el.visible:
        s *= 0.3
    if el.disabled:
        s *= 0.5
    return min(s, 1.0)


def resolve_candidates(
    elements: list[BrowserElement], query: str, role: str = "", threshold: float = 0.2
) -> list[tuple[BrowserElement, float]]:
    scored = [(el, score_element(el, query, role)) for el in elements]
    scored = [(e, s) for e, s in scored if s >= threshold]
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored


def pick_element(
    elements: list[BrowserElement], query: str, role: str = "", threshold: float = 0.3
) -> tuple[BrowserElement, float]:
    from src.agent.browser.errors import AMBIGUOUS_ELEMENT, ELEMENT_NOT_FOUND, BrowserError

    ranked = resolve_candidates(elements, query, role=role, threshold=threshold)
    if not ranked:
        raise BrowserError(f"No element found for {query!r}", ELEMENT_NOT_FOUND)
    if len(ranked) >= 2:
        gap = ranked[0][1] - ranked[1][1]
        # Standard ambiguity: scores very close
        if gap < 0.02 and ranked[0][1] < 0.8:
            names = [e.name or e.text or e.id for e, _ in ranked[:4]]
            raise BrowserError(
                f"Ambiguous — multiple matches for {query!r}: {names}",
                AMBIGUOUS_ELEMENT,
                retryable=True,
            )
        # Substring ambiguity: both elements have the same role and the runner-up
        # contains the query as a substring (e.g. "Apply" vs "Apply with LinkedIn").
        # The user likely needs to be more specific.
        q = _norm(query)
        if (
            ranked[1][1] >= 0.7
            and ranked[0][0].role == ranked[1][0].role
            and q in _norm(ranked[1][0].name or ranked[1][0].text)
        ):
            names = [e.name or e.text or e.id for e, _ in ranked[:4]]
            raise BrowserError(
                f"Ambiguous — multiple matches for {query!r}: {names}",
                AMBIGUOUS_ELEMENT,
                retryable=True,
            )
    return ranked[0]


def score_tab(title: str, url: str, query: str) -> float:
    q = _norm(query)
    if not q:
        return 0.0
    t = _norm(title)
    u = _norm(url)
    if q == t:
        return 1.0
    if q in t:
        return 0.8
    if q in u:
        return 0.6
    qw = set(q.split())
    tw = set(t.split())
    overlap = qw & tw
    if overlap:
        return len(overlap) / max(len(qw), 1) * 0.5
    return 0.0


# =====================================================================
# BrowserTargetResolver — distinguish existing Chrome from Playwright
# =====================================================================


@dataclass
class BrowserWindowInfo:
    """Describes a discovered browser window on the desktop."""

    hwnd: int = 0
    process_id: int = 0
    process_name: str = ""
    window_title: str = ""
    bounds: tuple[int, int, int, int] = (0, 0, 0, 0)  # left, top, right, bottom
    monitor_index: int = -1
    is_foreground: bool = False
    is_minimized: bool = False
    is_visible: bool = True
    source: str = "EXISTING_DESKTOP"  # or "MANAGED_PLAYWRIGHT"
    active_tab_title: str = ""
    url_if_known: str = ""
    playwright_owned: bool = False
    window_class: str = ""
    exe_path: str = ""
    monitor_id: str = ""
    selection_reason: str = ""


_CHROME_EXE = re.compile(r"chrome\.exe$", re.I)
_EDGE_EXE = re.compile(r"msedge\.exe$", re.I)
_BROWSER_TITLE_SUFFIX = re.compile(
    r"\s*[-–—]\s*(?:Google Chrome|Microsoft Edge|Mozilla Firefox|Chromium)\s*$",
    re.I,
)


def _extract_tab_title(window_title: str) -> str:
    """Strip ' - Google Chrome' suffix to get the tab/page title."""
    from src.agent.browser.discovery import extract_tab_title

    return extract_tab_title(window_title)


class BrowserTargetResolver:
    """Finds existing desktop browser windows across all monitors.

    Uses HWND → PID → chrome.exe discovery. JARVIS owning the foreground
    does not mean no browser exists.
    """

    def __init__(
        self,
        computer=None,
        managed_pids: Optional[set[int]] = None,
        registry=None,
        use_native: Optional[bool] = None,
    ):
        from src.agent.browser.discovery import BrowserWindowRegistry

        self._computer = computer
        self._managed_pids: set[int] = managed_pids or set()
        self.registry = registry if registry is not None else BrowserWindowRegistry()
        if use_native is None:
            # Tests inject FakeComputer WindowInfo lists. Prefer that path so
            # unit tests stay deterministic. Live JARVIS sets use_native=True.
            use_native = computer is None
        self._use_native = bool(use_native)
        self.last_discovery: dict[str, Any] = {}

    def mark_managed(self, pid: int) -> None:
        """Register a Playwright-launched browser PID."""
        self._managed_pids.add(pid)

    def seed_from_desktop(self) -> list[BrowserWindowInfo]:
        """Enumerate now and register whatever Chrome is already open."""
        found = self.find_all(log=True)
        print(f"[BrowserDiscovery] startup seed: {len(found)} existing desktop browser(s)")
        return found

    def find_all(self, *, log: bool = False, preferred_browser: str = "") -> list[BrowserWindowInfo]:
        """Enumerate browser windows across the full virtual desktop."""
        from src.agent.browser.discovery import (
            refs_from_computer_windows,
            scan_desktop,
            _fg_hwnd_from_computer,
            print_discovery_log,
            select_browser,
            parse_preferred_browser,
        )

        rejections: list[str] = []
        total = 0
        visible = 0
        fg_hwnd = 0
        refs = []

        if self._use_native:
            scan = scan_desktop()
            refs = list(scan.get("candidates") or [])
            rejections = list(scan.get("rejections") or [])
            total = int(scan.get("total") or 0)
            visible = int(scan.get("visible") or 0)
            fg_hwnd = int(scan.get("foreground_hwnd") or 0)
            for ref in refs:
                if ref.pid in self._managed_pids:
                    ref.playwright_owned = True
                    ref.source = "MANAGED_PLAYWRIGHT"
        else:
            windows: list[Any] = []
            if self._computer is not None:
                try:
                    windows = self._computer.list_windows()
                except Exception as e:
                    rejections.append(f"list_windows:{e}")
                    windows = []
            fg_hwnd = _fg_hwnd_from_computer(self._computer)
            total = len(windows)
            visible = len(windows)
            refs = refs_from_computer_windows(windows, fg_hwnd, self._managed_pids)

        self.registry.prune()
        self.registry.seed(refs)
        for ref in refs:
            stored = self.registry.get(ref.hwnd)
            if stored and stored.last_foreground_at:
                ref.last_foreground_at = stored.last_foreground_at

        preferred = preferred_browser or parse_preferred_browser(preferred_browser)
        selected_ref, reason = select_browser(
            refs,
            foreground_hwnd=fg_hwnd,
            last_user_hwnd=self.registry.last_user_browser_hwnd,
            managed_pids=self._managed_pids,
            preferred_browser=preferred,
        )
        self.last_discovery = {
            "total": total,
            "visible": visible,
            "candidates": refs,
            "foreground_hwnd": fg_hwnd,
            "last_user_hwnd": self.registry.last_user_browser_hwnd,
            "selected": selected_ref,
            "selection_reason": reason,
            "rejections": rejections,
        }
        if log:
            print_discovery_log(
                total=total,
                visible=visible,
                candidates=refs,
                foreground_hwnd=fg_hwnd,
                last_user_hwnd=self.registry.last_user_browser_hwnd,
                selected=selected_ref,
                selection_reason=reason,
                rejections=rejections,
            )
        return [r.as_info() for r in refs]

    def resolve_target(self, *, log: bool = True, preferred_browser: str = "") -> Optional[BrowserWindowInfo]:
        """Best existing-desktop browser, even if JARVIS currently has focus."""
        self.find_all(log=log, preferred_browser=preferred_browser)
        d = self.last_discovery
        selected = d.get("selected")
        if selected is None:
            return None
        info = selected.as_info()
        info.selection_reason = d.get("selection_reason") or ""
        return info

    def find_foreground_browser(self) -> Optional[BrowserWindowInfo]:
        """User's actual browser — not 'whatever is foreground right now'."""
        return self.resolve_target()

    def find_existing_chrome(self) -> Optional[BrowserWindowInfo]:
        """Find any existing non-managed Chrome window."""
        return self.resolve_target(log=False)

    def should_launch_new(self, intent: str = "") -> bool:
        """True only if no suitable existing browser exists or user explicitly requested a new one."""
        explicit_managed = any(
            kw in intent.lower()
            for kw in ("separate browser", "automation browser", "jarvis browser", "new browser window")
        )
        if explicit_managed:
            return True
        return self.find_existing_chrome() is None
