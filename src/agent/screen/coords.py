"""Coordinate systems: capture-relative ↔ virtual desktop (mouse) ↔ UIA."""

from __future__ import annotations

from typing import Any, Optional

from src.agent.screen.errors import COORDINATE_CONVERSION_ERROR, MONITOR_NOT_FOUND, ScreenError


def _scale_at(x: int, y: int) -> float:
    try:
        import ctypes
        from ctypes import wintypes

        class POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

        pt = POINT(int(x), int(y))
        hmon = ctypes.windll.user32.MonitorFromPoint(pt, 2)  # MONITOR_DEFAULTTONEAREST
        dpi_x = ctypes.c_uint()
        dpi_y = ctypes.c_uint()
        ctypes.windll.shcore.GetDpiForMonitor(hmon, 0, ctypes.byref(dpi_x), ctypes.byref(dpi_y))
        return max(1.0, float(dpi_x.value) / 96.0)
    except Exception:
        return 1.0


def list_monitor_states(mss_monitors: list[dict] | None = None) -> list[dict[str, Any]]:
    """Physical monitors with virtual-desktop origin (may be negative)."""
    monitors: list[dict] = []
    if mss_monitors is None:
        try:
            import mss

            with mss.mss() as sct:
                mss_monitors = list(sct.monitors[1:])
        except Exception:
            mss_monitors = []
    for i, m in enumerate(mss_monitors or []):
        left = int(m.get("left") or 0)
        top = int(m.get("top") or 0)
        width = int(m.get("width") or 0)
        height = int(m.get("height") or 0)
        monitors.append(
            {
                "monitorId": str(i),
                "index": i,
                "x": left,
                "y": top,
                "width": width,
                "height": height,
                "scaleFactor": round(_scale_at(left + width // 2, top + height // 2), 3),
                "primary": i == 0,
                "active": False,
            }
        )
    if monitors:
        monitors[0]["primary"] = True
    return monitors


def monitor_containing(monitors: list[dict[str, Any]], x: int, y: int) -> Optional[dict[str, Any]]:
    for m in monitors:
        if m["x"] <= x < m["x"] + m["width"] and m["y"] <= y < m["y"] + m["height"]:
            return m
    return None


def capture_to_desktop(
    x: float,
    y: float,
    *,
    region_left: int,
    region_top: int,
    capture_width: int,
    capture_height: int,
    source_width: int | None = None,
    source_height: int | None = None,
) -> tuple[int, int]:
    """Map a point in a screenshot (possibly resized) onto the virtual desktop."""
    sw = source_width or capture_width
    sh = source_height or capture_height
    if sw <= 0 or sh <= 0 or capture_width <= 0 or capture_height <= 0:
        raise ScreenError("Invalid capture dimensions", COORDINATE_CONVERSION_ERROR, retryable=False)
    dx = region_left + (float(x) / sw) * capture_width
    dy = region_top + (float(y) / sh) * capture_height
    return int(round(dx)), int(round(dy))


def box_center_desktop(bounds: dict[str, Any], region: Optional[dict[str, Any]] = None) -> tuple[int, int]:
    """UIA/desktop bounds {left,top,right,bottom} or capture-relative {x,y,width,height}."""
    if "left" in bounds and "right" in bounds:
        left, top = int(bounds.get("left") or 0), int(bounds.get("top") or 0)
        right, bottom = int(bounds.get("right") or 0), int(bounds.get("bottom") or 0)
        if right <= left or bottom <= top:
            raise ScreenError("Empty bounding box", COORDINATE_CONVERSION_ERROR, retryable=False)
        return (left + right) // 2, (top + bottom) // 2
    x = float(bounds.get("x") or 0)
    y = float(bounds.get("y") or 0)
    w = float(bounds.get("width") or 0)
    h = float(bounds.get("height") or 0)
    cx, cy = x + w / 2.0, y + h / 2.0
    if region:
        return capture_to_desktop(
            cx,
            cy,
            region_left=int(region.get("x") or region.get("left") or 0),
            region_top=int(region.get("y") or region.get("top") or 0),
            capture_width=int(region.get("width") or 0),
            capture_height=int(region.get("height") or 0),
            source_width=int(region.get("image_width") or region.get("width") or 0),
            source_height=int(region.get("image_height") or region.get("height") or 0),
        )
    return int(cx), int(cy)


def point_in_rect(x: int, y: int, rect: dict[str, Any], pad: int = 4) -> bool:
    left = int(rect.get("left") or rect.get("x") or 0) - pad
    top = int(rect.get("top") or rect.get("y") or 0) - pad
    right = int(rect.get("right") or (left + int(rect.get("width") or 0))) + pad
    bottom = int(rect.get("bottom") or (top + int(rect.get("height") or 0))) + pad
    return left <= x <= right and top <= y <= bottom


def resolve_monitor(
    monitors: list[dict[str, Any]],
    *,
    query: str = "",
    index: Optional[int] = None,
    active_rect: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    if not monitors:
        raise ScreenError("No monitors available", MONITOR_NOT_FOUND, retryable=False)
    if index is not None:
        for m in monitors:
            if int(m.get("index") or -1) == index:
                return m
        raise ScreenError(f"Monitor {index} not found", MONITOR_NOT_FOUND, retryable=False)
    q = (query or "").lower()
    if "primary" in q or (not q and not active_rect):
        for m in monitors:
            if m.get("primary"):
                return m
        return monitors[0]
    if any(w in q for w in ("second", "other", "2")):
        if len(monitors) < 2:
            raise ScreenError("Only one monitor is connected", MONITOR_NOT_FOUND, retryable=False)
        return monitors[1]
    if "left" in q:
        return min(monitors, key=lambda m: int(m["x"]))
    if "right" in q:
        return max(monitors, key=lambda m: int(m["x"]))
    if active_rect:
        cx = (int(active_rect.get("left") or 0) + int(active_rect.get("right") or 0)) // 2
        cy = (int(active_rect.get("top") or 0) + int(active_rect.get("bottom") or 0)) // 2
        hit = monitor_containing(monitors, cx, cy)
        if hit:
            return hit
    return monitors[0]
