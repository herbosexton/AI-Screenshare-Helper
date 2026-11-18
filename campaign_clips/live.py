from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from campaign_clips.clipper import CampaignClipper
from campaign_clips.models import Campaign, Partner, StreamStatus, utc_now_iso
from campaign_clips.pipeline import process_source
from campaign_clips.store import ClipStore
from campaign_clips.tracker import ContentRewardsTracker
from campaign_clips.triggers import (
    GameMatchTrigger,
    IntervalTrigger,
    ViewerSpikeTrigger,
    combine_all,
)
from campaign_clips.twitch_client import TwitchClient


class LiveClipError(RuntimeError):
    pass


@dataclass
class LiveClipEvent:
    campaign_id: str
    partner_login: str
    trigger: str
    source_path: str
    clip_ids: list[str]
    method: str  # "record" | "twitch_clip"
    created_at: str


def require_streamlink() -> str:
    path = shutil.which("streamlink")
    if not path:
        raise LiveClipError(
            "streamlink not found on PATH. Install with: pip install streamlink"
        )
    return path


def record_live_window(
    *,
    platform_url: str,
    dest: str | Path,
    duration_s: float,
    quality: str = "best",
    streamlink_bin: str | None = None,
    ffmpeg_bin: str = "ffmpeg",
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> Path:
    """Record the next N seconds of a live stream into an MP4 (contracted partners only)."""
    out = Path(dest)
    out.parent.mkdir(parents=True, exist_ok=True)
    sl = streamlink_bin or require_streamlink()
    # streamlink writes; ffmpeg trims/re-muxes to a stable mp4 with hard duration cap.
    # Using streamlink --hls-duration when available, plus ffmpeg -t as a backstop.
    cmd = [
        sl,
        "--loglevel",
        "error",
        "--hls-duration",
        str(max(1, int(duration_s))),
        "-o",
        str(out),
        platform_url,
        quality,
    ]
    result = run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0 or not out.is_file() or out.stat().st_size < 1000:
        # Fallback: pipe through ffmpeg for containers streamlink struggled to finalize.
        pipe_cmd = (
            f'"{sl}" --loglevel error --stdout "{platform_url}" {quality} '
            f'| "{ffmpeg_bin}" -hide_banner -loglevel error -i pipe:0 '
            f'-t {max(1.0, duration_s):.2f} -c copy -y "{out}"'
        )
        result2 = run(pipe_cmd, capture_output=True, text=True, check=False, shell=True)
        if result2.returncode != 0 or not out.is_file() or out.stat().st_size < 1000:
            err = (result.stderr or result.stdout or "") + "\n" + (result2.stderr or "")
            raise LiveClipError(f"live record failed for {platform_url}: {err.strip()}")
    return out


def twitch_channel_url(login: str) -> str:
    return f"https://www.twitch.tv/{login.strip().lstrip('@').lower()}"


@dataclass
class LiveWorkerStats:
    polls: int = 0
    live_sightings: int = 0
    clips_made: int = 0
    skipped: int = 0
    errors: int = 0


class LiveCampaignWorker:
    """Watch contracted partners while live, cut clips, enqueue rewards uploads."""

    def __init__(
        self,
        campaigns: list[Campaign],
        client: TwitchClient,
        *,
        tracker: ContentRewardsTracker,
        out_root: str | Path = "data/campaign_clips/live",
        export_root: str | Path | None = None,
        poll_interval_s: float = 45.0,
        record_duration_s: float | None = None,
        use_viewer_spike: bool = True,
        use_interval_fallback: bool = True,
        interval_every_n_polls: int = 8,
        prefer_twitch_clip_api: bool = True,
        clip_store: ClipStore | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        record_fn: Callable[..., Path] = record_live_window,
        on_event: Callable[[LiveClipEvent], None] | None = None,
        on_skip: Callable[[str], None] | None = None,
        on_error: Callable[[str], None] | None = None,
    ):
        self.campaigns = [c for c in campaigns if c.enabled]
        self.client = client
        self.tracker = tracker
        self.out_root = Path(out_root)
        self.export_root = Path(export_root) if export_root else None
        self.poll_interval_s = poll_interval_s
        self.record_duration_s = record_duration_s
        self.prefer_twitch_clip_api = prefer_twitch_clip_api
        self.clip_store = clip_store or ClipStore(
            self.out_root / "twitch_clips.jsonl"
        )
        self.clipper = CampaignClipper(
            client,
            self.clip_store,
            sleep_fn=sleep_fn,
            clip_ready_attempts=10,
            clip_ready_delay_s=1.0,
        )
        self._sleep = sleep_fn
        self._record = record_fn
        self.on_event = on_event
        self.on_skip = on_skip
        self.on_error = on_error
        self.stats = LiveWorkerStats()

        self._game = {c.id: GameMatchTrigger(c.game) for c in self.campaigns}
        self._spike = ViewerSpikeTrigger() if use_viewer_spike else None
        self._interval = (
            IntervalTrigger(interval_every_n_polls) if use_interval_fallback else None
        )

    def _pairs(self) -> list[tuple[Campaign, Partner]]:
        return [(c, p) for c in self.campaigns for p in c.partners]

    def poll_once(self) -> list[LiveClipEvent]:
        self.stats.polls += 1
        pairs = self._pairs()
        if not pairs:
            return []

        partners = [p for _, p in pairs]
        id_map = self.clipper.resolve_partner_ids(partners)
        live = self.client.get_streams_by_user_ids(list({*id_map.values()}))
        events: list[LiveClipEvent] = []

        for campaign, partner in pairs:
            login = partner.normalized_login()
            user_id = id_map[login]
            stream = live.get(user_id)
            if not stream:
                self.stats.skipped += 1
                if self.on_skip:
                    self.on_skip(f"{campaign.id}:{login}:offline")
                continue

            self.stats.live_sightings += 1
            decision = self._decide(campaign, stream)
            if not decision.should_clip:
                self.stats.skipped += 1
                if self.on_skip:
                    self.on_skip(f"{campaign.id}:{login}:{decision.reason}")
                continue

            try:
                event = self._clip_live(
                    campaign=campaign,
                    partner=partner,
                    stream=stream,
                    trigger=decision.reason,
                )
            except Exception as exc:
                self.stats.errors += 1
                if self.on_error:
                    self.on_error(f"{campaign.id}:{login}:{exc}")
                continue

            self.stats.clips_made += 1
            events.append(event)
            if self.on_event:
                self.on_event(event)
        return events

    def _decide(self, campaign: Campaign, stream: StreamStatus):
        game = self._game[campaign.id].evaluate(stream)
        moments = []
        if self._spike is not None:
            moments.append(self._spike.evaluate(stream))
        if self._interval is not None:
            moments.append(self._interval.evaluate(stream))
        if not moments:
            return game
        moment = next((m for m in moments if m.should_clip), None)
        if moment is None:
            return combine_all(game, moments[0])
        return combine_all(game, moment)

    def _clip_live(
        self,
        *,
        campaign: Campaign,
        partner: Partner,
        stream: StreamStatus,
        trigger: str,
    ) -> LiveClipEvent:
        duration = float(
            self.record_duration_s
            if self.record_duration_s is not None
            else campaign.default_clip_duration_s
        )
        stamp = utc_now_iso().replace(":", "").replace("+00:00", "Z")
        raw_dir = self.out_root / campaign.id / partner.normalized_login()
        raw_path = raw_dir / f"live_{stamp}.mp4"
        method = "record"

        # Preferred when the bot account is editor: official Twitch clip, then
        # still record a local file for Content Rewards upload packages.
        if self.prefer_twitch_clip_api:
            try:
                self.clipper.create_for_stream(
                    campaign, partner, stream, trigger=f"live:{trigger}"
                )
                method = "twitch_clip+record"
            except Exception:
                method = "record"

        self._record(
            platform_url=twitch_channel_url(partner.normalized_login()),
            dest=raw_path,
            duration_s=duration,
        )

        export_root = self.export_root or campaign.export_dir
        result = process_source(
            source=raw_path,
            campaign=campaign,
            partner=partner,
            out_root=export_root,
            tracker=self.tracker,
            max_clips=1,
            clip_duration_s=duration,
            make_vertical=True,
        )
        return LiveClipEvent(
            campaign_id=campaign.id,
            partner_login=partner.normalized_login(),
            trigger=trigger,
            source_path=str(raw_path),
            clip_ids=[c.clip_id for c in result.clips],
            method=method,
            created_at=utc_now_iso(),
        )

    def run(self, *, max_polls: int | None = None) -> LiveWorkerStats:
        polls = 0
        while max_polls is None or polls < max_polls:
            self.poll_once()
            polls += 1
            if max_polls is not None and polls >= max_polls:
                break
            self._sleep(self.poll_interval_s)
        return self.stats
