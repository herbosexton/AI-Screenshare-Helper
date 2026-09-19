"""Lightweight screen fingerprints and change detection. No extra packages."""

from __future__ import annotations

import hashlib
from typing import Any, Optional

import numpy as np
from PIL import Image


def average_hash_bits(image: Image.Image, size: int = 16) -> np.ndarray:
    small = image.convert("L").resize((size, size))
    arr = np.asarray(small, dtype=np.float32)
    mean = float(arr.mean())
    bits = (arr > mean).astype(np.uint8).flatten()
    brightness = np.unpackbits(np.array([int(max(0, min(255, round(mean))))], dtype=np.uint8))
    return np.concatenate([bits, brightness])


def perceptual_hash(image: Image.Image, size: int = 16) -> str:
    bits = average_hash_bits(image, size=size)
    return hashlib.sha256(bits.tobytes()).hexdigest()[:16]


def hash_distance(a: np.ndarray, b: np.ndarray) -> float:
    if a.shape != b.shape or a.size == 0:
        return 1.0
    if a.size >= 8:
        spat = float(np.mean(a[:-8] != b[:-8])) if a.size > 8 else 0.0
        br = float(np.mean(a[-8:] != b[-8:]))
        return max(spat, br)
    return float(np.mean(a != b))


def text_hash(parts: list[str]) -> str:
    blob = "\n".join((p or "").strip() for p in parts if p).lower()
    if not blob:
        return ""
    return hashlib.sha256(blob.encode("utf-8", errors="replace")).hexdigest()[:16]


def screen_fingerprint(
    *,
    active_window: str = "",
    active_application: str = "",
    image: Optional[Image.Image] = None,
    image_hash: str = "",
    visible_text: Optional[list[str]] = None,
) -> str:
    ih = image_hash or (perceptual_hash(image) if image is not None else "")
    th = text_hash(list(visible_text or [])[:12])
    raw = f"{active_application}|{active_window}|{ih}|{th}"
    return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:16]


class ScreenChangeDetector:
    def __init__(self, threshold: float = 0.04):
        self.threshold = threshold
        self._prev_bits: Optional[np.ndarray] = None
        self._prev_fp = ""

    def observe(self, image: Optional[Image.Image] = None, fingerprint: str = "") -> dict[str, Any]:
        bits = average_hash_bits(image) if image is not None else None
        mag = 1.0
        if bits is not None and self._prev_bits is not None:
            mag = hash_distance(self._prev_bits, bits)
        elif fingerprint and fingerprint == self._prev_fp:
            mag = 0.0
        changed = mag >= self.threshold
        kind = "same"
        if mag >= 0.45:
            kind = "major"
        elif mag >= self.threshold:
            kind = "small"
        if bits is not None:
            self._prev_bits = bits
        if fingerprint:
            self._prev_fp = fingerprint
        return {
            "hasScreenChanged": changed,
            "changeMagnitude": round(mag, 4),
            "kind": kind,
            "changedRegions": ["full"] if changed else [],
        }
