from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass(frozen=True)
class Partner:
    """A contracted streamer / creator in a paid campaign."""

    twitch_login: str = ""
    youtube_channel_id: str = ""
    youtube_handle: str = ""
    display_name: str = ""
    contract_id: str = ""
    notes: str = ""
    platforms: tuple[str, ...] = ()

    def normalized_login(self) -> str:
        """Stable filesystem / tracker key for this partner."""
        if self.twitch_login.strip():
            return self.twitch_login.strip().lstrip("@").lower()
        if self.youtube_handle.strip():
            return self.youtube_handle.strip().lstrip("@").lower()
        if self.youtube_channel_id.strip():
            return self.youtube_channel_id.strip()
        return "partner"

    def enabled_platforms(self) -> tuple[str, ...]:
        if self.platforms:
            return tuple(p.lower() for p in self.platforms)
        out: list[str] = []
        if self.twitch_login.strip():
            out.append("twitch")
        if self.youtube_channel_id.strip() or self.youtube_handle.strip():
            out.append("youtube")
        return tuple(out)


@dataclass(frozen=True)
class Campaign:
    """Paid campaign that authorizes clipping for listed partners."""

    id: str
    name: str
    game: str
    partners: tuple[Partner, ...]
    clip_title_prefix: str = ""
    hashtags: tuple[str, ...] = ()
    max_clips_per_hour: int = 6
    enabled: bool = True
    # Rewards portals (no public API) — browser bot uploads + local tracker.
    content_rewards_campaign: str = ""
    content_rewards_slug: str = ""
    reward_site_ids: tuple[str, ...] = ("content_rewards",)
    site_slugs: tuple[tuple[str, str], ...] = ()
    default_clip_duration_s: float = 25.0
    max_clips_per_source: int = 8
    inbox_dir: str = ""
    export_dir: str = "data/campaign_clips/export"

    def slug_for_site(self, site_id: str) -> str:
        for key, value in self.site_slugs:
            if key == site_id:
                return value
        if site_id == "content_rewards" and self.content_rewards_slug:
            return self.content_rewards_slug
        return self.content_rewards_slug or self.id


@dataclass
class ClipRecord:
    """One clip created under a campaign."""

    campaign_id: str
    partner_login: str
    clip_id: str
    url: str
    embed_url: str
    title: str
    created_at: str
    trigger: str
    broadcaster_id: str = ""
    game_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class StreamStatus:
    user_id: str
    user_login: str
    user_name: str
    game_id: str
    game_name: str
    title: str
    viewer_count: int
    started_at: str
    is_live: bool = True
    platform: str = "twitch"
    video_id: str = ""
    watch_url: str = ""
