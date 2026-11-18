from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass(frozen=True)
class Partner:
    """A contracted streamer / creator in a paid campaign."""

    twitch_login: str
    display_name: str = ""
    contract_id: str = ""
    notes: str = ""

    def normalized_login(self) -> str:
        return self.twitch_login.strip().lstrip("@").lower()


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
