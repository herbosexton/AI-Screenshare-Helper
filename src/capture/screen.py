import io
import base64
from typing import Optional

import mss
import mss.tools
import numpy as np
from PIL import Image


class ScreenCapture:
    """Multi-monitor screen capture using mss."""

    def __init__(self, config: dict):
        self.config = config
        self.monitors_setting = config.get("monitors", "all")
        self._previous_frames: list[Optional[np.ndarray]] = []

    def get_monitors(self) -> list[dict]:
        """Physical monitors with virtual-desktop origin (mss). Index 0 in mss is the virtual union."""
        with mss.mss() as sct:
            return list(sct.monitors[1:])

    def list_monitor_meta(self) -> list[dict]:
        from src.agent.screen.coords import list_monitor_states

        return list_monitor_states(self.get_monitors())

    def status(self) -> dict:
        """Cheap capture-service health. Does not grab pixels."""
        try:
            metas = self.list_monitor_meta()
            return {
                "available": True,
                "backend": "mss",
                "monitor_count": len(metas),
                "monitors": metas,
            }
        except Exception as e:
            return {"available": False, "backend": "mss", "monitor_count": 0, "error": str(e)}

    def _grab(self, region: dict, monitor_index: int = 0) -> dict:
        with mss.mss() as sct:
            shot = sct.grab(region)
            img = Image.frombytes("RGB", shot.size, shot.rgb)
            left = int(region.get("left") or 0)
            top = int(region.get("top") or 0)
            return {
                "image": img,
                "base64": self._image_to_base64(img),
                "monitor_index": monitor_index,
                "size": shot.size,
                "width": int(shot.size[0]),
                "height": int(shot.size[1]),
                "left": left,
                "top": top,
                "x": left,
                "y": top,
                "timestamp": __import__("time").time(),
                "format": "png",
                "scale": 1.0,
            }

    def capture_desktop(self) -> dict:
        """Virtual desktop (all monitors combined). Prefer capture_monitor for cost."""
        with mss.mss() as sct:
            virtual = sct.monitors[0]
            return self._grab(virtual, monitor_index=-1)

    def capture_all(self) -> list[dict]:
        """
        Capture all configured monitors.
        Returns list of dicts with 'image' (PIL Image), 'base64' (str), and 'monitor_index' (int).
        """
        metas = self.get_monitors()
        if self.monitors_setting != "all":
            try:
                idx = int(self.monitors_setting)
                metas = [metas[idx]] if idx < len(metas) else metas
            except (ValueError, IndexError):
                pass
        results = []
        for i, monitor in enumerate(metas):
            results.append(self._grab(monitor, monitor_index=i))
        self._update_previous_frames(results)
        return results

    def capture_monitor(self, index: int) -> Optional[dict]:
        """Capture a specific monitor by index."""
        monitors = self.get_monitors()
        if index < 0 or index >= len(monitors):
            return None
        return self._grab(monitors[index], monitor_index=index)

    def capture_region(self, left: int, top: int, width: int, height: int) -> dict:
        if width < 2 or height < 2:
            raise ValueError("Region too small")
        return self._grab(
            {"left": int(left), "top": int(top), "width": int(width), "height": int(height)},
            monitor_index=-2,
        )

    def capture_window_rect(self, rect: dict) -> dict:
        left = int(rect.get("left") or 0)
        top = int(rect.get("top") or 0)
        right = int(rect.get("right") or 0)
        bottom = int(rect.get("bottom") or 0)
        return self.capture_region(left, top, max(2, right - left), max(2, bottom - top))

    def has_significant_change(self, current: list[dict], threshold: float = 0.05) -> bool:
        """
        Check if the screen has changed significantly since last capture.
        Used for 'smart' capture mode.
        """
        if not self._previous_frames:
            return True

        for i, frame_data in enumerate(current):
            if i >= len(self._previous_frames) or self._previous_frames[i] is None:
                return True

            current_arr = np.array(frame_data["image"].resize((320, 180)))
            prev_arr = self._previous_frames[i]

            diff = np.mean(np.abs(current_arr.astype(float) - prev_arr.astype(float)))
            normalized_diff = diff / 255.0

            if normalized_diff > threshold:
                return True

        return False

    def _update_previous_frames(self, captures: list[dict]):
        self._previous_frames = [
            np.array(cap["image"].resize((320, 180))) for cap in captures
        ]

    @staticmethod
    def encode_for_vision(img: Image.Image, max_side: int = 768) -> tuple[str, tuple[int, int]]:
        """Compact JPEG for vision models. Returns (base64, (width, height))."""
        im = img.convert("RGB")
        if im.size[0] > max_side or im.size[1] > max_side:
            im = im.copy()
            im.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        im.save(buffer, format="JPEG", quality=72, optimize=True)
        return base64.b64encode(buffer.getvalue()).decode("utf-8"), im.size

    @staticmethod
    def _image_to_base64(img: Image.Image, max_size: tuple = (1920, 1080)) -> str:
        """Convert PIL Image to base64 string, resizing if needed to control API costs."""
        if img.size[0] > max_size[0] or img.size[1] > max_size[1]:
            img = img.copy()
            img.thumbnail(max_size, Image.Resampling.LANCZOS)

        buffer = io.BytesIO()
        img.save(buffer, format="PNG", optimize=True)
        return base64.b64encode(buffer.getvalue()).decode("utf-8")
