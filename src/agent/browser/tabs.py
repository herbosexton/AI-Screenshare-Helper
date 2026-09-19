"""Active tab + URL resolution for a single existing-desktop browser HWND."""

from __future__ import annotations

from dataclasses import dataclass

from src.agent.browser.discovery import extract_tab_title, read_address_bar_uia


@dataclass
class BrowserPageState:
    hwnd: int = 0
    monitor: str = ""
    active_tab_title: str = ""
    url: str = ""
    source: str = "EXISTING_DESKTOP"
    confidence: float = 0.0
    title_source: str = ""
    url_source: str = ""
    window_title: str = ""


class ActiveTabResolver:
    """Resolve the selected tab and omnibox of one Chrome HWND.

    Never walks a different window. First matching TabItem in the tree is
    ignored unless SelectionItem.IsSelected (or acc selected) is true.
    """

    def resolve(self, hwnd: int, *, window_title: str = "", monitor: str = "") -> BrowserPageState:
        tab, tab_src = self._selected_tab_title(hwnd)
        url, url_src = self._url(hwnd)
        if tab:
            title = tab
            title_source = tab_src
        else:
            title = extract_tab_title(window_title)
            title_source = "WINDOW_TITLE_FALLBACK" if title else ""
        if url:
            confidence = 0.95 if title_source == "UIA_SELECTED_TAB" else 0.85
        elif title:
            confidence = 0.7 if title_source == "UIA_SELECTED_TAB" else 0.55
        else:
            confidence = 0.3
        state = BrowserPageState(
            hwnd=hwnd,
            monitor=monitor,
            active_tab_title=title,
            url=url,
            source="EXISTING_DESKTOP",
            confidence=confidence,
            title_source=title_source,
            url_source=url_src,
            window_title=window_title,
        )
        print(
            "[ActiveTab]\n"
            f"selected_tab_title={state.active_tab_title or '-'}\n"
            f"tab_source={state.title_source or '-'}\n"
            f"url={state.url or '-'}\n"
            f"url_source={state.url_source or '-'}\n"
            f"hwnd={hwnd}"
        )
        return state

    def _selected_tab_title(self, hwnd: int) -> tuple[str, str]:
        if not hwnd:
            return "", ""
        try:
            import uiautomation as auto
        except ImportError:
            return "", ""
        try:
            from src.agent.screen.uia import _co_init

            _co_init()
        except Exception:
            pass
        try:
            root = auto.ControlFromHandle(hwnd)
            if root is None:
                return "", ""
        except Exception:
            return "", ""

        deadline = __import__("time").monotonic() + 0.04
        selected = self._walk_selected_tab(root, depth=0, max_depth=6, deadline=deadline)
        if selected:
            return selected, "UIA_SELECTED_TAB"
        return "", ""

    def _walk_selected_tab(self, ctrl, depth: int, max_depth: int, deadline: float = 0.0) -> str:
        if ctrl is None or depth > max_depth:
            return ""
        if deadline and __import__("time").monotonic() > deadline:
            return ""
        try:
            ctype = (getattr(ctrl, "ControlTypeName", "") or "")
        except Exception:
            ctype = ""
        if ctype in {"TabItem", "TabItemControl"} or ctype.endswith("TabItem"):
            name = ""
            try:
                name = (ctrl.Name or "").strip()
            except Exception:
                name = ""
            if name and self._is_selected_tab(ctrl):
                return name
        try:
            children = ctrl.GetChildren()
        except Exception:
            children = []
        for child in children or []:
            found = self._walk_selected_tab(child, depth + 1, max_depth, deadline=deadline)
            if found:
                return found
        return ""

    @staticmethod
    def _is_selected_tab(ctrl) -> bool:
        try:
            pat = ctrl.GetSelectionItemPattern()
            if pat is not None and bool(pat.IsSelected):
                return True
        except Exception:
            pass
        try:
            acc = ctrl.GetLegacyIAccessiblePattern()
            # STATE_SYSTEM_SELECTED = 0x2, STATE_SYSTEM_FOCUSED = 0x4
            if acc is not None and (int(acc.State) & 0x2):
                return True
        except Exception:
            pass
        try:
            if bool(getattr(ctrl, "HasKeyboardFocus", False)):
                return True
        except Exception:
            pass
        return False

    def list_tab_titles(self, hwnd: int) -> list[str]:
        """TabItem names in this HWND only. Inactive ChatGPT tabs may not say ChatGPT."""
        names: list[str] = []
        self._walk_tabs(hwnd, collect=names, activate=None)
        return names

    def activate_tab_named(self, hwnd: int, name: str) -> bool:
        """Click the TabItem whose name matches. Same HWND only."""
        want = (name or "").strip().lower()
        if not hwnd or not want:
            return False
        return self._walk_tabs(hwnd, collect=None, activate=want)

    def _walk_tabs(
        self,
        hwnd: int,
        *,
        collect: list[str] | None,
        activate: str | None,
        max_depth: int = 6,
    ) -> bool:
        if not hwnd:
            return False
        try:
            import uiautomation as auto
        except ImportError:
            return False
        try:
            from src.agent.screen.uia import _co_init

            _co_init()
        except Exception:
            pass
        try:
            root = auto.ControlFromHandle(hwnd)
            if root is None:
                return False
        except Exception:
            return False
        deadline = __import__("time").monotonic() + 0.08

        def walk(ctrl, depth: int) -> bool:
            if ctrl is None or depth > max_depth:
                return False
            if __import__("time").monotonic() > deadline:
                return False
            try:
                ctype = getattr(ctrl, "ControlTypeName", "") or ""
            except Exception:
                ctype = ""
            if ctype in {"TabItem", "TabItemControl"} or str(ctype).endswith("TabItem"):
                try:
                    tab_name = (ctrl.Name or "").strip()
                except Exception:
                    tab_name = ""
                if tab_name and collect is not None:
                    collect.append(tab_name)
                if tab_name and activate and activate in tab_name.lower():
                    try:
                        inv = ctrl.GetInvokePattern()
                        if inv is not None:
                            inv.Invoke()
                            return True
                    except Exception:
                        pass
                    try:
                        ctrl.Click()
                        return True
                    except Exception:
                        pass
            try:
                children = ctrl.GetChildren()
            except Exception:
                children = []
            for child in children or []:
                if walk(child, depth + 1):
                    return True
            return False

        return bool(walk(root, 0))

    @staticmethod
    def _url(hwnd: int) -> tuple[str, str]:
        url = read_address_bar_uia(hwnd) or ""
        if url:
            if url.startswith("http://") or url.startswith("https://") or url.startswith("chrome"):
                return url, "ADDRESS_BAR_UIA"
            if "." in url and " " not in url:
                return url, "ADDRESS_BAR_UIA"
        return "", ""
