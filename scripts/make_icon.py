"""Generate assets/jarvis.ico — the HUD's arc reactor, for shortcuts and the tray.

Drawn rather than shipped as a binary so the mark can be adjusted in one place. Run after
changing it: python scripts/make_icon.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "assets" / "jarvis.ico"

RED = (255, 43, 43, 255)
DIM = (150, 20, 20, 255)
BACKDROP = (10, 4, 6, 255)

# Rendered large and downsampled: arcs this thin alias badly at 16 px drawn directly.
SIZE = 1024
SIZES = [(n, n) for n in (16, 24, 32, 48, 64, 128, 256)]


def draw() -> Image.Image:
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((0, 0, SIZE - 1, SIZE - 1), fill=BACKDROP)

    def ring(inset: int, width: int, color, gaps: list[tuple[int, int]]) -> None:
        box = (inset, inset, SIZE - inset, SIZE - inset)
        for start, end in gaps:
            d.arc(box, start, end, fill=color, width=width)

    # Outer ring, broken into segments the way the HUD draws it.
    ring(70, 34, RED, [(200, 340), (10, 150)])
    ring(150, 14, DIM, [(0, 360)])
    ring(215, 26, RED, [(120, 240), (300, 60)])

    # Core.
    d.ellipse((330, 330, SIZE - 330, SIZE - 330), outline=RED, width=22)
    d.ellipse((410, 410, SIZE - 410, SIZE - 410), fill=RED)
    return img


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    draw().save(OUT, format="ICO", sizes=SIZES)
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
