from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


class MediaError(RuntimeError):
    pass


def require_ffmpeg() -> str:
    path = shutil.which("ffmpeg")
    if not path:
        raise MediaError("ffmpeg not found on PATH")
    return path


def require_ffprobe() -> str:
    path = shutil.which("ffprobe")
    if not path:
        raise MediaError("ffprobe not found on PATH")
    return path


@dataclass(frozen=True)
class MediaInfo:
    path: Path
    duration_s: float
    width: int
    height: int
    has_audio: bool


def probe(path: str | Path) -> MediaInfo:
    ffprobe = require_ffprobe()
    source = Path(path)
    if not source.is_file():
        raise MediaError(f"media file not found: {source}")
    cmd = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format=duration:stream=width,height,codec_type",
        "-of",
        "json",
        str(source),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise MediaError(f"ffprobe failed: {result.stderr.strip()}")
    payload = json.loads(result.stdout or "{}")
    duration = float((payload.get("format") or {}).get("duration") or 0.0)
    width = 0
    height = 0
    has_audio = False
    for stream in payload.get("streams") or []:
        if stream.get("codec_type") == "video" and not width:
            width = int(stream.get("width") or 0)
            height = int(stream.get("height") or 0)
        if stream.get("codec_type") == "audio":
            has_audio = True
    return MediaInfo(
        path=source,
        duration_s=duration,
        width=width,
        height=height,
        has_audio=has_audio,
    )


def cut_clip(
    source: str | Path,
    dest: str | Path,
    *,
    start_s: float,
    duration_s: float,
    vertical: bool = False,
    overwrite: bool = True,
) -> Path:
    """Cut a segment with ffmpeg. Optional 9:16 center crop+scale for Content Rewards."""
    ffmpeg = require_ffmpeg()
    src = Path(source)
    out = Path(dest)
    out.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{max(0.0, start_s):.3f}",
        "-i",
        str(src),
        "-t",
        f"{max(0.1, duration_s):.3f}",
    ]
    if vertical:
        # Center-crop to 9:16 then scale to 1080x1920.
        cmd += [
            "-vf",
            (
                "scale=1080:1920:force_original_aspect_ratio=increase,"
                "crop=1080:1920"
            ),
        ]
    cmd += [
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-movflags",
        "+faststart",
    ]
    if overwrite:
        cmd.append("-y")
    cmd.append(str(out))

    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0 or not out.is_file():
        raise MediaError(f"ffmpeg cut failed: {result.stderr.strip() or result.stdout}")
    return out


def audio_volume_points(source: str | Path, *, interval_s: float = 1.0) -> list[tuple[float, float]]:
    """Return (time_s, mean_volume_db) samples using ffmpeg volumedetect in windows.

    Uses astats over the full file via silencedetect-style sampling is heavy;
    instead we use `ffmpeg -af astats=metadata=1:reset=1` with showinfo is complex.
    Simpler approach: extract mean volume per chunk via truncated decodes.
    """
    info = probe(source)
    if info.duration_s <= 0:
        return []
    ffmpeg = require_ffmpeg()
    points: list[tuple[float, float]] = []
    t = 0.0
    while t < info.duration_s:
        chunk = min(interval_s, info.duration_s - t)
        cmd = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "info",
            "-ss",
            f"{t:.3f}",
            "-t",
            f"{chunk:.3f}",
            "-i",
            str(source),
            "-af",
            "volumedetect",
            "-f",
            "null",
            "-",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        mean_db = -91.0
        for line in (result.stderr or "").splitlines():
            if "mean_volume:" in line:
                # e.g. [Parsed_volumedetect_0 @ ...] mean_volume: -23.5 dB
                try:
                    mean_db = float(line.split("mean_volume:")[1].split("dB")[0].strip())
                except (IndexError, ValueError):
                    pass
                break
        points.append((t, mean_db))
        t += interval_s
    return points
