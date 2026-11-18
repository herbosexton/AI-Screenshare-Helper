from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass

from campaign_clips.media import MediaError, MediaInfo, probe, require_ffmpeg


@dataclass(frozen=True)
class HighlightWindow:
    start_s: float
    duration_s: float
    score: float
    reason: str

    @property
    def end_s(self) -> float:
        return self.start_s + self.duration_s


_SCENE_RE = re.compile(r"pts_time:(?P<t>[0-9.]+)")


def detect_scene_times(source: str, *, threshold: float = 0.35) -> list[float]:
    """One-pass ffmpeg scene change timestamps."""
    ffmpeg = require_ffmpeg()
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-i",
        source,
        "-vf",
        f"select='gt(scene,{threshold})',showinfo",
        "-an",
        "-f",
        "null",
        "-",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    # showinfo writes to stderr even on success
    times: list[float] = []
    for line in (result.stderr or "").splitlines():
        if "pts_time:" not in line:
            continue
        match = _SCENE_RE.search(line)
        if match:
            times.append(float(match.group("t")))
    return times


def _clamp_window(
    center_s: float,
    *,
    duration_s: float,
    media_duration_s: float,
    pre_roll_s: float,
) -> tuple[float, float]:
    start = max(0.0, center_s - pre_roll_s)
    end = min(media_duration_s, start + duration_s)
    start = max(0.0, end - duration_s)
    return start, max(0.1, end - start)


def build_highlights(
    source: str,
    *,
    clip_duration_s: float = 25.0,
    pre_roll_s: float = 4.0,
    max_clips: int = 8,
    min_spacing_s: float = 20.0,
    scene_threshold: float = 0.35,
    info: MediaInfo | None = None,
) -> list[HighlightWindow]:
    """Propose highlight windows for Content Rewards export.

    Strategy:
    1. Prefer scene-change moments (action cuts).
    2. If too few scenes, pad with evenly spaced windows across the VOD.
    3. Deduplicate by spacing and rank by density of nearby scene cuts.
    """
    media = info or probe(source)
    if media.duration_s <= 1.0:
        raise MediaError("source too short to clip")

    scene_times = detect_scene_times(source, threshold=scene_threshold)
    candidates: list[HighlightWindow] = []

    for t in scene_times:
        nearby = sum(1 for s in scene_times if abs(s - t) <= clip_duration_s)
        start, dur = _clamp_window(
            t,
            duration_s=clip_duration_s,
            media_duration_s=media.duration_s,
            pre_roll_s=pre_roll_s,
        )
        candidates.append(
            HighlightWindow(
                start_s=round(start, 3),
                duration_s=round(dur, 3),
                score=float(nearby),
                reason=f"scene@{t:.2f}",
            )
        )

    if len(candidates) < max_clips:
        # Even coverage fallback for quiet / long takes.
        needed = max_clips - len(candidates)
        step = max(clip_duration_s, media.duration_s / max(needed + 1, 1))
        t = min(pre_roll_s, media.duration_s / 2)
        while len(candidates) < max_clips and t < media.duration_s:
            start, dur = _clamp_window(
                t,
                duration_s=clip_duration_s,
                media_duration_s=media.duration_s,
                pre_roll_s=pre_roll_s,
            )
            candidates.append(
                HighlightWindow(
                    start_s=round(start, 3),
                    duration_s=round(dur, 3),
                    score=0.5,
                    reason=f"interval@{t:.2f}",
                )
            )
            t += step

    candidates.sort(key=lambda w: (-w.score, w.start_s))
    selected: list[HighlightWindow] = []
    for window in candidates:
        if any(abs(window.start_s - s.start_s) < min_spacing_s for s in selected):
            continue
        selected.append(window)
        if len(selected) >= max_clips:
            break
    selected.sort(key=lambda w: w.start_s)
    return selected
