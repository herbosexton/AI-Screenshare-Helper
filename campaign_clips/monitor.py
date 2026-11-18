from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from campaign_clips.clipper import CampaignClipper
from campaign_clips.models import Campaign, ClipRecord, Partner
from campaign_clips.triggers import (
    GameMatchTrigger,
    IntervalTrigger,
    TriggerDecision,
    ViewerSpikeTrigger,
    combine_all,
)


@dataclass
class MonitorStats:
    polls: int = 0
    live_sightings: int = 0
    clips_created: int = 0
    skipped: int = 0


class CampaignMonitor:
    """Poll live partners and create clips when triggers fire."""

    def __init__(
        self,
        campaigns: list[Campaign],
        clipper: CampaignClipper,
        *,
        poll_interval_s: float = 45.0,
        use_viewer_spike: bool = True,
        use_interval_fallback: bool = False,
        interval_every_n_polls: int = 20,
        sleep_fn: Callable[[float], None] = time.sleep,
        on_clip: Callable[[ClipRecord], None] | None = None,
        on_skip: Callable[[str], None] | None = None,
    ):
        self.campaigns = [c for c in campaigns if c.enabled]
        self.clipper = clipper
        self.poll_interval_s = poll_interval_s
        self.use_viewer_spike = use_viewer_spike
        self.use_interval_fallback = use_interval_fallback
        self.interval_every_n_polls = interval_every_n_polls
        self._sleep = sleep_fn
        self.on_clip = on_clip
        self.on_skip = on_skip
        self.stats = MonitorStats()

        self._game_triggers = {
            c.id: GameMatchTrigger(c.game) for c in self.campaigns
        }
        self._spike = ViewerSpikeTrigger() if use_viewer_spike else None
        self._interval = (
            IntervalTrigger(interval_every_n_polls) if use_interval_fallback else None
        )

    def _partners(self) -> list[tuple[Campaign, Partner]]:
        pairs: list[tuple[Campaign, Partner]] = []
        for campaign in self.campaigns:
            for partner in campaign.partners:
                pairs.append((campaign, partner))
        return pairs

    def poll_once(self) -> list[ClipRecord]:
        self.stats.polls += 1
        pairs = self._partners()
        if not pairs:
            return []

        # Resolve all partner IDs up front.
        all_partners = [p for _, p in pairs]
        id_map = self.clipper.resolve_partner_ids(all_partners)
        user_ids = list({id_map[p.normalized_login()] for p in all_partners})
        live = self.clipper.client.get_streams_by_user_ids(user_ids)

        created: list[ClipRecord] = []
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
            decisions: list[TriggerDecision] = [
                self._game_triggers[campaign.id].evaluate(stream)
            ]
            moment_decisions: list[TriggerDecision] = []
            if self._spike is not None:
                moment_decisions.append(self._spike.evaluate(stream))
            if self._interval is not None:
                moment_decisions.append(self._interval.evaluate(stream))

            if moment_decisions:
                # Any moment trigger is enough if the game matches.
                moment = next((d for d in moment_decisions if d.should_clip), None)
                if moment is None:
                    decision = combine_all(decisions[0], moment_decisions[0])
                else:
                    decision = combine_all(decisions[0], moment)
            else:
                # No moment trigger configured → clip whenever game matches (manual-like).
                decision = decisions[0]

            if not decision.should_clip:
                self.stats.skipped += 1
                if self.on_skip:
                    self.on_skip(f"{campaign.id}:{login}:{decision.reason}")
                continue

            record = self.clipper.create_for_stream(
                campaign, partner, stream, trigger=decision.reason
            )
            if record is None:
                self.stats.skipped += 1
                if self.on_skip:
                    self.on_skip(f"{campaign.id}:{login}:rate_limited_or_disabled")
                continue

            self.stats.clips_created += 1
            created.append(record)
            if self.on_clip:
                self.on_clip(record)
        return created

    def run(self, *, max_polls: int | None = None) -> MonitorStats:
        polls = 0
        while max_polls is None or polls < max_polls:
            self.poll_once()
            polls += 1
            if max_polls is not None and polls >= max_polls:
                break
            self._sleep(self.poll_interval_s)
        return self.stats
