"""Windows UI Automation helpers for Phase 3. Bounded time and depth."""

from __future__ import annotations

import threading
from typing import Any, Optional

from src.agent.screen.errors import UIA_TIMEOUT, UIA_UNAVAILABLE, ScreenError

CONTROL_TYPE_MAP = {
    50000: "button",
    50004: "edit",
    50003: "checkbox",
    50006: "combo_box",
    50007: "list_item",
    50008: "list",
    50020: "text",
    50005: "hyperlink",
    50002: "calendar",
    50011: "menu",
    50010: "menu_item",
    50019: "tab",
    50018: "tab_item",
    50029: "document",
    50025: "tool_bar",
    50032: "window",
    50033: "pane",
    50026: "tree",
    50027: "tree_item",
    50031: "progress_bar",
    50022: "spinner",
}


def _co_init() -> None:
    try:
        import pythoncom

        pythoncom.CoInitialize()
    except Exception:
        try:
            import ctypes

            ctypes.windll.ole32.CoInitialize(None)
        except Exception:
            pass


def _bounds_of(control) -> dict[str, int]:
    try:
        rect = control.BoundingRectangle
        return {
            "left": int(rect.left),
            "top": int(rect.top),
            "right": int(rect.right),
            "bottom": int(rect.bottom),
        }
    except Exception:
        return {}


def _element(kind: str, name: str, control) -> dict[str, Any]:
    return {
        "kind": kind,
        "name": (name or "")[:200],
        "automation_id": getattr(control, "AutomationId", "") or "",
        "bounds": _bounds_of(control),
    }


def get_foreground_uia_elements(
    max_elements: int = 80,
    *,
    timeout_s: float = 1.5,
    max_depth: int = 6,
    name_contains: str = "",
    role: str = "",
    window_title: str = "",
) -> list[dict[str, Any]]:
    """
    Walk a window accessibility tree for interactive controls.
    Prefer a named window when given; otherwise use the foreground window.
    Never blocks longer than timeout_s.
    """
    holder: dict[str, Any] = {"items": [], "error": None}

    def work() -> None:
        _co_init()
        try:
            import uiautomation as auto
        except ImportError:
            holder["error"] = ScreenError("UI Automation package not installed", UIA_UNAVAILABLE, retryable=False)
            return
        try:
            root = None
            if window_title:
                try:
                    root = auto.WindowControl(searchDepth=1, SubName=window_title)
                    if not root.Exists(maxSearchSeconds=min(0.8, max(0.2, timeout_s - 0.4))):
                        root = None
                except Exception:
                    root = None
            if root is None:
                root = auto.GetForegroundControl()
            if root is None:
                return
            elements: list[dict[str, Any]] = []
            if name_contains:
                try:
                    btn = root.ButtonControl(SubName=name_contains, searchDepth=8)
                    if btn.Exists(maxSearchSeconds=0.35):
                        elements.append(_element("button", btn.Name or name_contains, btn))
                except Exception:
                    pass
            if len(elements) < max_elements:
                _walk_uia(root, elements, max_elements, depth=0, max_depth=max_depth)
            if name_contains or role:
                needle = (name_contains or "").lower()
                filtered = []
                for el in elements:
                    if role and role.lower() not in (el.get("kind") or "").lower():
                        continue
                    if needle and needle not in (el.get("name") or "").lower():
                        continue
                    filtered.append(el)
                holder["items"] = filtered or elements
            else:
                holder["items"] = elements
        except Exception as e:
            holder["error"] = e

    thread = threading.Thread(target=work, daemon=True, name="uia-walk")
    thread.start()
    thread.join(timeout=max(0.2, timeout_s))
    if thread.is_alive():
        raise ScreenError("UI Automation timed out", UIA_TIMEOUT, retryable=True)
    err = holder.get("error")
    if isinstance(err, ScreenError):
        raise err
    return list(holder.get("items") or [])


def _walk_uia(control, out: list[dict[str, Any]], limit: int, depth: int, max_depth: int) -> None:
    if len(out) >= limit or depth > max_depth:
        return
    try:
        name = (control.Name or "").strip()
        ctype = int(getattr(control, "ControlType", 0) or 0)
        kind = CONTROL_TYPE_MAP.get(ctype, f"control_{ctype}")
        interesting = kind in {
            "button",
            "edit",
            "checkbox",
            "combo_box",
            "hyperlink",
            "menu_item",
            "tab_item",
            "list_item",
            "text",
            "document",
            "window",
            "progress_bar",
            "spinner",
        } or bool(name)
        if interesting and (name or kind in {"button", "edit", "checkbox", "hyperlink", "progress_bar", "text"}):
            item = _element(kind, name, control)
            if not any(
                x.get("name") == item["name"]
                and x.get("kind") == item["kind"]
                and x.get("bounds") == item["bounds"]
                for x in out
            ):
                out.append(item)
        child = control.GetFirstChildControl()
        while child is not None and len(out) < limit:
            _walk_uia(child, out, limit, depth + 1, max_depth)
            child = child.GetNextSiblingControl()
    except Exception:
        return


_CHROME_CHROME = frozenset(
    {
        "minimize",
        "maximize",
        "close",
        "restore",
        "new tab",
        "new tab button",
        "google chrome",
        "chrome",
        "address and search bar",
        "reload",
        "back",
        "forward",
        "bookmarks",
        "extensions",
        "menu",
        "chrome legacy window",
    }
)


def collect_document_text(hwnd: int, max_chars: int = 8000, *, timeout_s: float = 2.2) -> str:
    """WEB DOCUMENT subtree only. Never the Chrome toolbar / extensions tree."""
    if not hwnd:
        return ""
    holder: dict[str, Any] = {"text": ""}

    def work() -> None:
        _co_init()
        try:
            import uiautomation as auto
        except ImportError:
            return
        try:
            root = auto.ControlFromHandle(int(hwnd))
            if root is None:
                return
            doc = None
            try:
                cand = root.DocumentControl(searchDepth=14)
                if cand.Exists(maxSearchSeconds=0.5):
                    doc = cand
            except Exception:
                doc = None
            if doc is None:
                return
            parts: list[str] = []
            try:
                pattern = doc.GetTextPattern()
                if pattern is not None:
                    raw = pattern.DocumentRange.GetText(-1) or ""
                    if raw.strip():
                        parts.append(raw)
            except Exception:
                pass
            names: list[str] = []
            _collect_names(doc, names, depth=0, max_depth=10, limit=220)
            parts.extend(names)
            seen: set[str] = set()
            ordered: list[str] = []
            for part in parts:
                line = (part or "").strip()
                if len(line) < 2:
                    continue
                key = line.lower()
                if key in _CHROME_CHROME or key in seen:
                    continue
                seen.add(key)
                ordered.append(line)
            holder["text"] = "\n".join(ordered)[: max(200, int(max_chars))]
        except Exception:
            return

    thread = threading.Thread(target=work, daemon=True, name="uia-document")
    thread.start()
    thread.join(timeout=max(0.3, timeout_s))
    return str(holder.get("text") or "")


def collect_window_text(hwnd: int, max_chars: int = 8000, *, timeout_s: float = 2.0) -> str:
    """Visible accessibility text from one HWND. Bounded time and size."""
    if not hwnd:
        return ""
    holder: dict[str, Any] = {"text": ""}

    def work() -> None:
        _co_init()
        try:
            import uiautomation as auto
        except ImportError:
            return
        try:
            root = auto.ControlFromHandle(int(hwnd))
            if root is None:
                return
            parts: list[str] = []
            try:
                doc = root.DocumentControl(searchDepth=12)
                if doc.Exists(maxSearchSeconds=0.45):
                    try:
                        pattern = doc.GetTextPattern()
                        if pattern is not None:
                            raw = pattern.DocumentRange.GetText(-1) or ""
                            if raw.strip():
                                parts.append(raw)
                    except Exception:
                        pass
                    if doc.Name:
                        parts.append(doc.Name)
            except Exception:
                pass
            names: list[str] = []
            _collect_names(root, names, depth=0, max_depth=10, limit=280)
            parts.extend(names)
            seen: set[str] = set()
            ordered: list[str] = []
            for part in parts:
                line = (part or "").strip()
                if len(line) < 2:
                    continue
                key = line.lower()
                if key in _CHROME_CHROME or key in seen:
                    continue
                seen.add(key)
                ordered.append(line)
            holder["text"] = "\n".join(ordered)[: max(200, int(max_chars))]
        except Exception:
            return

    thread = threading.Thread(target=work, daemon=True, name="uia-text")
    thread.start()
    thread.join(timeout=max(0.3, timeout_s))
    return str(holder.get("text") or "")


def _collect_names(control, out: list[str], depth: int, max_depth: int, limit: int) -> None:
    if len(out) >= limit or depth > max_depth:
        return
    try:
        name = (getattr(control, "Name", None) or "").strip()
        if name:
            out.append(name)
        try:
            value = (getattr(control, "GetValuePattern")() or None)
            if value is not None:
                raw = (getattr(value, "Value", None) or "").strip()
                if raw:
                    out.append(raw)
        except Exception:
            pass
        child = control.GetFirstChildControl()
        while child is not None and len(out) < limit:
            _collect_names(child, out, depth + 1, max_depth, limit)
            child = child.GetNextSiblingControl()
    except Exception:
        return
