from __future__ import annotations

import time
from typing import Callable

from campaign_clips.models import Campaign, ClipRecord, Partner, StreamStatus, utc_now_iso
from campaign_clips.store import ClipStore
from campaign_clips.twitch_client import TwitchClient


class CampaignClipper:
    """Creates Twitch clips for contracted campaign partners."""

    def __init__(
        self,
        client: TwitchClient,
        store: ClipStore,
        *,
        sleep_fn: Callable[[float], None] = time.sleep,
        clip_ready_attempts: int = 8,
        clip_ready_delay_s: float = 1.0,
    ):
        self.client = client
        self.store = store
        self._sleep = sleep_fn
        self.clip_ready_attempts = clip_ready_attempts
        self.clip_ready_delay_s = clip_ready_delay_s
        self._user_ids: dict[str, str] = {}

    def resolve_partner_ids(self, partners: list[Partner] | tuple[Partner, ...]) -> dict[str, str]:
        logins = [p.normalized_login() for p in partners]
        missing = [login for login in logins if login not in self._user_ids]
        if missing:
            users = self.client.get_users_by_login(missing)
            for login in missing:
                user = users.get(login)
                if not user:
                    raise ValueError(f"Twitch user not found for partner login '{login}'")
                self._user_ids[login] = str(user["id"])
        return {login: self._user_ids[login] for login in logins}

    def build_title(self, campaign: Campaign, partner: Partner, stream: StreamStatus) -> str:
        bits = []
        if campaign.clip_title_prefix:
            bits.append(campaign.clip_title_prefix.strip())
        name = partner.display_name or partner.twitch_login
        bits.append(name)
        if stream.title:
            bits.append(stream.title.strip()[:80])
        if campaign.hashtags:
            bits.append(" ".join(f"#{h}" for h in campaign.hashtags))
        title = " | ".join(bits)
        return title[:140]

    def create_for_stream(
        self,
        campaign: Campaign,
        partner: Partner,
        stream: StreamStatus,
        *,
        trigger: str,
    ) -> ClipRecord | None:
        if not campaign.enabled:
            return None

        login = partner.normalized_login()
        recent = self.store.clips_in_last_hour(campaign.id, login)
        if recent >= campaign.max_clips_per_hour:
            return None

        created = self.client.create_clip(stream.user_id, has_delay=False)
        clip_id = str(created["id"])
        edit_url = str(created.get("edit_url") or "")

        # Twitch clips are eventually consistent; poll briefly for public URL.
        url = ""
        embed_url = ""
        for _ in range(self.clip_ready_attempts):
            detail = self.client.get_clip(clip_id)
            if detail:
                url = str(detail.get("url") or "")
                embed_url = str(detail.get("embed_url") or "")
                if url:
                    break
            self._sleep(self.clip_ready_delay_s)

        record = ClipRecord(
            campaign_id=campaign.id,
            partner_login=login,
            clip_id=clip_id,
            url=url or edit_url,
            embed_url=embed_url,
            title=self.build_title(campaign, partner, stream),
            created_at=utc_now_iso(),
            trigger=trigger,
            broadcaster_id=stream.user_id,
            game_id=stream.game_id,
            metadata={
                "contract_id": partner.contract_id,
                "stream_title": stream.title,
                "game_name": stream.game_name,
                "viewer_count": stream.viewer_count,
                "edit_url": edit_url,
            },
        )
        self.store.append(record)
        return record
