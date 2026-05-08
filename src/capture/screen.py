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
        """Return list of available monitors (excluding the virtual combined monitor at index 0)."""
        with mss.mss() as sct:
            return list(sct.monitors[1:])

    def capture_all(self) -> list[dict]:
        """
        Capture all configured monitors.
        Returns list of dicts with 'image' (PIL Image), 'base64' (str), and 'monitor_index' (int).
        """
        with mss.mss() as sct:
            monitors = sct.monitors[1:]

            if self.monitors_setting != "all":
                try:
                    idx = int(self.monitors_setting)
                    monitors = [monitors[idx]] if idx < len(monitors) else monitors
                except (ValueError, IndexError):
                    pass

            results = []
            for i, monitor in enumerate(monitors):
                screenshot = sct.grab(monitor)
                img = Image.frombytes("RGB", screenshot.size, screenshot.rgb)
                results.append({
                    "image": img,
                    "base64": self._image_to_base64(img),
                    "monitor_index": i,
                    "size": screenshot.size,
                })

            self._update_previous_frames(results)
            return results

    def capture_monitor(self, index: int) -> Optional[dict]:
        """Capture a specific monitor by index."""
        with mss.mss() as sct:
            monitors = sct.monitors[1:]
            if index >= len(monitors):
                return None

            monitor = monitors[index]
            screenshot = sct.grab(monitor)
            img = Image.frombytes("RGB", screenshot.size, screenshot.rgb)
            return {
                "image": img,
                "base64": self._image_to_base64(img),
                "monitor_index": index,
                "size": screenshot.size,
            }

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
    def _image_to_base64(img: Image.Image, max_size: tuple = (1920, 1080)) -> str:
        """Convert PIL Image to base64 string, resizing if needed to control API costs."""
        if img.size[0] > max_size[0] or img.size[1] > max_size[1]:
            img = img.copy()
            img.thumbnail(max_size, Image.Resampling.LANCZOS)

        buffer = io.BytesIO()
        img.save(buffer, format="PNG", optimize=True)
        return base64.b64encode(buffer.getvalue()).decode("utf-8")
